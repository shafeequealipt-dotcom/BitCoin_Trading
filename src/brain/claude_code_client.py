"""Claude Code CLI client — $0 cost, uses existing Max subscription.

Replaces:
    ClaudeClient -> calls api.anthropic.com -> $0.003-0.015 per call
With:
    ClaudeCodeClient -> calls `claude -p "..."` CLI -> $0 per call

The CLI is already installed and authenticated on the VM via OAuth.
Auth: ~/.claude/.credentials.json (subscriptionType: "max")
Binary: /usr/bin/claude -> /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js

Hardened for systemd: resolves full binary path at init, builds explicit
environment dict, validates credentials, and logs diagnostics.

Auth recovery (3-layer):
  1. OAuth token refresh — POST refreshToken to platform.claude.com (best-effort)
  2. Credential hot-reload — detects 'claude login' without service restart
  3. Telegram alert — notifies operator on persistent auth failure
"""

import asyncio
import glob
import hashlib
import json
import os
import re
import shutil
import signal as signal_mod
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from src.core.exceptions import (
    AuthenticationError,
    BrainError,
    ClaudeAPIError,
    CredentialRefreshError,
)
from src.core.log_context import ctx, get_did
from src.core.logging import get_logger

log = get_logger("claude_code")

# Errors that should NOT be retried (billing/auth — retrying won't help)
_NON_RETRYABLE = frozenset([
    "credit balance",
    "authentication",
    "unauthorized",
    "api key",
    "account suspended",
    "quota exceeded",
    "rate limit",
    "out of extra usage",   # Claude Max subscription daily usage cap
    "extra usage",          # shorter variant of the same error
])

# Derive paths dynamically instead of hardcoding
_PROJECT = str(Path(__file__).resolve().parents[2])
_HOME = os.environ.get("HOME") or str(Path.home())
_CREDENTIAL_PATH = Path(_HOME) / ".claude" / ".credentials.json"

# OAuth token refresh endpoint + client ID (from Claude CLI source)
_OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Backoff: 5 min → 10 min → 20 min → 40 min → 60 min (max)
_AUTH_BACKOFF_SCHEDULE = [300, 600, 1200, 2400, 3600]

# Stage 2 live-dump (System 1, observability): complete Call-A and Call-B
# prompt-and-response capture. Every brain call writes its full
# prompt/system_prompt/response plus call type, call id, decision id, and
# timestamp to one JSON file. The gate is centralized config — set once at
# boot via configure_brain_capture() from settings.observability — and the
# legacy data/stage2_dumps/.enabled sentinel file remains an optional live
# override (touch / rm data/stage2_dumps/.enabled, no restart needed). Capture
# is on when EITHER is set. Retention/rotation of this directory is handled by
# the hourly cleanup worker (_sweep_stage2_dumps). Fire-and-forget: a failure
# here never propagates into the brain or trade cycle.
_DUMP_DIR = Path(_PROJECT) / "data" / "stage2_dumps"
_DUMP_SENTINEL = _DUMP_DIR / ".enabled"
_CAPTURE_ENABLED = False


def configure_brain_capture(enabled: bool, dump_dir: str | None = None) -> None:
    """Set the centralized brain-capture gate from config at boot.

    Best-effort and idempotent. Config is the boot-time source of truth; the
    on-disk .enabled sentinel remains a live override (see _maybe_dump_call).
    """
    global _CAPTURE_ENABLED, _DUMP_DIR, _DUMP_SENTINEL
    try:
        _CAPTURE_ENABLED = bool(enabled)
        if dump_dir:
            d = Path(dump_dir)
            _DUMP_DIR = d if d.is_absolute() else Path(_PROJECT) / dump_dir
            _DUMP_SENTINEL = _DUMP_DIR / ".enabled"
        if _CAPTURE_ENABLED:
            _DUMP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # never let capture config break boot


def _sanitize_call_type(call_type: str) -> str:
    """Restrict the call type to a safe filename token (lowercase + underscore)."""
    import re
    ct = re.sub(r"[^a-z_]", "", (call_type or "other").lower()) or "other"
    return ct[:24]


def _maybe_dump_call(call_id: int, prompt: str, system_prompt: str, response: str,
                     elapsed_ms: float, prompt_hash: str,
                     call_type: str = "other") -> None:
    """Best-effort: dump a Claude call to disk when brain-capture is enabled.

    Gated by centralized config (_CAPTURE_ENABLED, set at boot) OR the legacy
    data/stage2_dumps/.enabled sentinel (live override). Fire-and-forget.
    """
    try:
        if not (_CAPTURE_ENABLED or _DUMP_SENTINEL.exists()):
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        did = get_did() or "no_ctx"
        ctype = _sanitize_call_type(call_type)
        path = _DUMP_DIR / f"{ts}_call{call_id:04d}_{ctype}_{did}.json"
        payload = {
            "call_id": call_id,
            "call_type": call_type,
            "did": did,
            "ts_utc": ts,
            "elapsed_ms": round(elapsed_ms, 1),
            "prompt_hash": prompt_hash,
            "prompt_chars": len(prompt),
            "system_prompt_chars": len(system_prompt),
            "response_chars": len(response),
            "system_prompt": system_prompt,
            "prompt": prompt,
            "response": response,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception:
        pass  # never let dump failure break the brain


# ─── T2-1 (2026-05-12): Claude CLI subprocess pre-spawn pool ───
#
# Pre-spawning a worker subprocess hides the per-call ``subprocess.Popen``
# + Claude CLI bootup latency (estimated 1-5 s per call) by overlapping
# it with the previous call's API wait time. The actual API latency
# (60-240 s for first stdout token, the dominant component per the
# F73/F48 evidence with ``wchan=ep_poll``) is unchanged — that's
# network + model inference time, not subprocess work.
#
# The Claude CLI in ``-p`` mode is single-shot (it processes one
# prompt then exits), so the "pool" is per-call: we pre-spawn ONE
# replacement worker after each call, keyed by system_prompt. The next
# call with a matching system_prompt picks it up; otherwise it spawns
# fresh.
#
# Workers older than ``max_age_seconds`` (default 60 s) are disposed
# without use — credentials may have rotated since spawn, so a stale
# worker risks an auth-failed call.
#
# A new ``CLAUDE_PROC_FIRST_TOKEN_MS`` log line in
# ``_stream_subprocess_io`` separates spawn+bootup time from API time
# so the operator can see post-deploy whether pre-spawning is enough
# or whether T2-1 follow-up (streaming-token mode) is needed.


# F28 (2026-06-05) — warm-pool CANARY. The prewarm pool was disabled
# (prewarm_max_age=0) after parked `claude -p` workers HUNG producing zero stdout
# on CLI 2.1.158/2.1.160 (a 75-min brain outage that recurred on each CLI auto-
# update). CLI 2.1.165 no longer hangs (verified by a parked-worker reproduction:
# parked responded in ~4 s, faster than cold). To re-enable the pool safely AND
# survive a FUTURE CLI auto-update that reintroduces the hang, reuse is gated on an
# out-of-band canary: a throwaway worker is keepalive-parked and must respond to a
# trivial prompt within a short deadline before reuse is trusted. A hung CLI is
# caught by the canary (off the hot path), the pool self-disables to cold-spawn,
# and a CALL_A is NEVER blocked by a hung parked worker again. The canary is
# version-agnostic (it tests the actual failure mode) and also records the CLI
# version for the operator. Re-enabling the pool removes only the ~50 ms cold-spawn
# cost; it does NOT reduce the API first-token latency, which is server-side.
_CANARY_SYS_PROMPT = "You are a warm-pool health probe. Answer in one word."
_CANARY_USER_PROMPT = "Respond with exactly: OK"
_CANARY_PARK_SECONDS = 3.0        # brief keepalive-park — the hang's trigger
_CANARY_DEADLINE_SECONDS = 45.0   # healthy parked first-byte ~4-20s; a hang is >300s


class _PrewarmSlot:
    """A single pre-spawned Claude CLI worker awaiting prompt delivery.

    The subprocess is spawned with ``-p --output-format text
    [--system-prompt X]`` and is currently in epoll_wait reading stdin.
    The caller writes the prompt to ``proc.stdin`` and closes it to
    let the worker proceed with the API call.
    """

    __slots__ = ("proc", "spawn_ts", "sys_prompt_hash")

    def __init__(
        self, proc: subprocess.Popen[bytes], sys_prompt_hash: str,
    ) -> None:
        self.proc = proc
        self.spawn_ts = time.time()
        self.sys_prompt_hash = sys_prompt_hash

    def age_seconds(self) -> float:
        return time.time() - self.spawn_ts

    def is_alive(self) -> bool:
        return self.proc.poll() is None


class _ClaudeWorkerPool:
    """Per-system-prompt pre-spawn pool for Claude CLI workers.

    Maintains at most ONE pre-spawned worker per system_prompt key.
    ``acquire`` returns a primed worker if available + fresh; otherwise
    ``(None, 0.0)`` so the caller falls back to a cold spawn. After
    each call, the caller schedules a background ``replenish_async``
    so the next call can pick up a primed worker.

    Thread-safety: a single ``threading.Lock`` guards the slot dict.
    Background replenishment runs in daemon threads (sniper / watchdog
    / strategy worker each call into the same client; the client's
    ``_subprocess_call`` runs in the asyncio default thread executor,
    so concurrent acquires from different worker tasks ARE possible).
    """

    def __init__(
        self,
        claude_path: str,
        env: dict[str, str],
        project_cwd: str,
        max_age_seconds: float = 900.0,
        stats_interval_seconds: float = 300.0,
        model: str = "",
        canary_ttl_seconds: float = 600.0,
        extra_flags: list[str] | None = None,
    ) -> None:
        self._claude_path = claude_path
        self._env = env
        self._project_cwd = project_cwd
        # Issue 1 (latency, 2026-06-06) — extra `claude -p` flags (e.g. --effort,
        # --bare, --exclude-dynamic-system-prompt-sections) appended at EVERY
        # spawn site in this pool so a prewarmed worker matches the decision
        # call's invocation exactly. Empty => byte-identical to the prior spawn.
        self._extra_flags: list[str] = list(extra_flags or [])
        # Brain-CLI model pin — prewarm workers must spawn with the SAME --model
        # the decision call expects, so an acquired worker is a valid match.
        self._model = (model or "").strip()
        # P2-1 (2026-05-13): default bumped from 60 s to 900 s. The
        # legacy 60 s freshness window was always shorter than the
        # 5-10 min CALL_A cadence, so every prewarmed worker was
        # disposed before it could be reused — pool hit rate was 0
        # in a 5-hour production sample. 900 s covers the worst-case
        # CALL_A cadence while staying well under the credential
        # refresh margin (default 600 s — overridden to 900 here
        # would be too close, so the caller is expected to keep the
        # credential margin >= max_age).
        self._max_age_seconds = max_age_seconds
        self._lock = threading.Lock()
        self._slots: dict[str, _PrewarmSlot] = {}
        # P2-1 (2026-05-13): cumulative effectiveness counters + a
        # periodic CLAUDE_POOL_STATS emit. Without these the operator
        # has no observable signal that the pool is doing useful work.
        self._hit_count: int = 0
        self._miss_count: int = 0
        self._stale_disposed_count: int = 0
        # J4 (2026-05-14) — split stale_disposed into the two causes so
        # operators can diagnose whether the 0% hit rate stems from
        # workers dying on their own (Claude CLI bailed out on its own
        # idle timeout) or simply ageing past max_age_seconds before the
        # next call arrives. Sum is identical to the legacy
        # stale_disposed counter; the breakdown is additive.
        self._age_disposed_count: int = 0
        self._dead_disposed_count: int = 0
        self._spawn_fail_count: int = 0
        self._stats_interval_s: float = float(stats_interval_seconds or 0.0)
        self._last_stats_emit_monotonic: float = time.monotonic()
        # F28 (2026-06-05) — canary-gated reuse. Reuse stays OFF until a canary
        # confirms the current CLI serves a parked worker without hanging, and is
        # re-confirmed every ``canary_ttl_seconds``. This is the safety net that
        # makes re-enabling the pool safe across CLI auto-updates.
        self._canary_ttl_s: float = float(canary_ttl_seconds or 0.0)
        self._reuse_healthy: bool = False
        self._canary_at_monotonic: float = 0.0
        self._canary_running: bool = False
        self._canary_lock = threading.Lock()
        self._cli_version: str = ""

    @staticmethod
    def _hash_sys_prompt(system_prompt: str) -> str:
        """16-char SHA-256 prefix is enough to disambiguate the 2-3
        distinct system prompts the project actually uses (STRATEGIST,
        POSITION, empty)."""
        return hashlib.sha256(
            (system_prompt or "").encode("utf-8")
        ).hexdigest()[:16]

    def known_pids(self) -> set[int]:
        """PIDs of currently-pre-spawned, still-alive workers.

        Used by ``ClaudeCodeClient._cleanup_orphaned_processes`` to
        skip our own pre-spawned workers (which match the
        ``claude.*-p`` pgrep pattern but must NOT be reaped before use).
        """
        with self._lock:
            return {
                slot.proc.pid
                for slot in self._slots.values()
                if slot.is_alive()
            }

    def acquire(
        self, system_prompt: str,
    ) -> tuple[subprocess.Popen[bytes] | None, float]:
        """Pop a primed worker for this system_prompt.

        Returns ``(proc, age_seconds)`` if a fresh-enough alive worker
        is available, else ``(None, 0.0)``. Stale or dead workers are
        disposed silently before this returns ``None`` so the next
        ``replenish_async`` call sees a clean slot.

        P2-1 (2026-05-13): increments hit/miss/stale_disposed counters
        and emits ``CLAUDE_POOL_STATS`` every ``stats_interval_s``
        seconds so the operator has periodic visibility into pool
        effectiveness.
        """
        sys_hash = self._hash_sys_prompt(system_prompt)
        with self._lock:
            slot = self._slots.pop(sys_hash, None)
        if slot is None:
            self._miss_count += 1
            self._maybe_emit_stats()
            return None, 0.0
        age = slot.age_seconds()
        _alive = slot.is_alive()
        if age > self._max_age_seconds or not _alive:
            # H1 (2026-05-16) — PREWARM_DEATH_CAUSE classification.
            # Capture returncode + a snippet of stderr so the operator
            # can diagnose what killed the subprocess (Claude CLI 3-s
            # stdin-silence error, auth failure, OOM, signal, etc.).
            # Best-effort: missing attributes on minimal test doubles
            # never raise from this path.
            _death_cause = "unknown"
            _death_rc: int | None = None
            _death_stderr = ""
            if not _alive:
                try:
                    _death_rc = getattr(slot.proc, "returncode", None)
                except Exception:
                    _death_rc = None
                _err_obj = getattr(slot.proc, "stderr", None)
                if _err_obj is not None:
                    try:
                        # Use a low-level fcntl-set-nonblocking approach via
                        # a small read attempt to avoid hanging.
                        import fcntl as _fcntl
                        try:
                            _fcntl.fcntl(
                                _err_obj,
                                _fcntl.F_SETFL,
                                _fcntl.fcntl(_err_obj, _fcntl.F_GETFL)
                                | os.O_NONBLOCK,
                            )
                        except Exception:
                            pass
                        _err_bytes = _err_obj.read() or b""
                        if isinstance(_err_bytes, (bytes, bytearray)):
                            _death_stderr = _err_bytes.decode(
                                "utf-8", errors="replace"
                            ).strip()[:200]
                    except Exception:
                        _death_stderr = ""
                # Classify based on signature
                if _death_rc == 1 and "no stdin data" in _death_stderr.lower():
                    _death_cause = "claude_cli_3s_stdin_timeout"
                elif _death_rc == 1 and "input must be provided" in _death_stderr.lower():
                    _death_cause = "claude_cli_no_input"
                elif _death_rc is not None and isinstance(_death_rc, int) and _death_rc < 0:
                    _death_cause = f"signal_{abs(_death_rc)}"
                elif _death_rc is not None:
                    _death_cause = f"exit_rc_{_death_rc}"
                else:
                    _death_cause = "no_returncode"
                log.info(
                    f"CLAUDE_PREWARM_DEATH_CAUSE | pid={slot.proc.pid} "
                    f"sys_hash={slot.sys_prompt_hash} age_s={age:.1f} "
                    f"rc={_death_rc} cause={_death_cause} "
                    f"stderr_tail='{_death_stderr[:120]}' | {ctx()}"
                )
            self._dispose(slot.proc)
            self._stale_disposed_count += 1
            # J4 (2026-05-14) — finer disposal attribution so the
            # operator can diagnose whether workers are ageing out
            # (call cadence is longer than max_age_seconds) or dying
            # on their own (Claude CLI idle timeout / signal). Both
            # increment the legacy stale_disposed counter; only one of
            # the new buckets fires per disposal.
            if not _alive:
                self._dead_disposed_count += 1
                _reason = "dead"
            else:
                self._age_disposed_count += 1
                _reason = "age_expired"
            log.info(
                f"CLAUDE_PREWARM_DISPOSED | pid={slot.proc.pid} "
                f"sys_hash={slot.sys_prompt_hash} age_s={age:.1f} "
                f"max_age_s={self._max_age_seconds:.0f} "
                f"reason={_reason} | {ctx()}"
            )
            self._miss_count += 1
            self._maybe_emit_stats()
            return None, 0.0
        self._hit_count += 1
        self._maybe_emit_stats()
        return slot.proc, age

    def _maybe_emit_stats(self) -> None:
        """Emit ``CLAUDE_POOL_STATS`` once per ``stats_interval_s``.

        Cheap — single monotonic compare on every acquire. Disabled
        when ``stats_interval_s <= 0``.
        """
        if self._stats_interval_s <= 0.0:
            return
        now_m = time.monotonic()
        if now_m - self._last_stats_emit_monotonic < self._stats_interval_s:
            return
        self._last_stats_emit_monotonic = now_m
        total = self._hit_count + self._miss_count
        hit_rate_pct = (
            (100.0 * self._hit_count / total) if total > 0 else 0.0
        )
        with self._lock:
            slot_count = len(self._slots)
        log.info(
            f"CLAUDE_POOL_STATS | hits={self._hit_count} "
            f"misses={self._miss_count} "
            f"stale_disposed={self._stale_disposed_count} "
            f"age_disposed={self._age_disposed_count} "
            f"dead_disposed={self._dead_disposed_count} "
            f"spawn_failed={self._spawn_fail_count} "
            f"hit_rate_pct={hit_rate_pct:.1f} "
            f"slots_currently_held={slot_count} "
            f"max_age_s={self._max_age_seconds:.0f} | {ctx()}"
        )

    def reuse_enabled(self) -> bool:
        """Reuse is allowed only when prewarm is configured ON (max_age > 0) AND
        the latest canary confirmed the current CLI serves a parked worker without
        hanging. Until the first canary passes, callers cold-spawn (safe)."""
        return self._max_age_seconds > 0 and self._reuse_healthy

    def maybe_run_canary_async(self) -> None:
        """F28: kick a background canary when prewarm is configured ON and the
        health check is unknown or stale. Non-blocking; at most one at a time. The
        hot-path CALL_A calls this each cycle so the canary stays fresh and a CLI
        that starts hanging is detected off the hot path within one TTL."""
        if self._max_age_seconds <= 0:
            return  # prewarm disabled by config — nothing to canary
        now = time.monotonic()
        with self._canary_lock:
            if self._canary_running:
                return
            if (
                self._reuse_healthy
                and self._canary_ttl_s > 0
                and (now - self._canary_at_monotonic) < self._canary_ttl_s
            ):
                return  # still fresh
            self._canary_running = True
        threading.Thread(
            target=self._canary_blocking, daemon=True, name="claude-pool-canary",
        ).start()

    def _canary_blocking(self) -> None:
        try:
            healthy, version, detail = self._run_canary()
            self._reuse_healthy = bool(healthy)
            self._cli_version = version
            self._canary_at_monotonic = time.monotonic()
            log.info(
                f"CLAUDE_POOL_CANARY | healthy={healthy} cli_version='{version}' "
                f"reuse_enabled={self.reuse_enabled()} detail='{detail}' | {ctx()}"
            )
        except Exception as e:
            # Any failure is treated as unhealthy — fail safe to cold-spawn.
            self._reuse_healthy = False
            log.warning(
                f"CLAUDE_POOL_CANARY_FAIL | err='{str(e)[:160]}' "
                f"reuse_enabled=False | {ctx()}"
            )
        finally:
            with self._canary_lock:
                self._canary_running = False

    def _run_canary(self) -> tuple[bool, str, str]:
        """Out-of-band probe of the documented failure mode: capture the CLI
        version, then spawn -> write the keepalive byte -> park briefly -> send a
        trivial prompt -> read with a SHORT deadline. A parked worker that produces
        zero stdout past the deadline is the hang (healthy parked first-byte is
        ~4-20 s; a hang is silent past the 300 s first-byte deadline). Returns
        ``(healthy, cli_version, detail)``. Always kills the throwaway worker."""
        import select as _select

        version = ""
        try:
            _v = subprocess.run(
                [self._claude_path, "--version"],
                capture_output=True, text=True, env=self._env, timeout=15,
            )
            version = (_v.stdout or "").strip()[:40] or "unknown"
        except Exception:
            version = "unknown"

        cmd = [self._claude_path, "-p", "--output-format", "text"]
        if self._model:
            cmd += ["--model", self._model]
        # Issue 1 — same latency flags as the real spawn, so the canary validates
        # the exact invocation the decision calls use.
        cmd += self._extra_flags
        cmd += ["--system-prompt", _CANARY_SYS_PROMPT]
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False, cwd=self._project_cwd,
                env=self._env, preexec_fn=os.setsid,
            )
            # Keepalive byte + brief park — the EXACT mechanism that hung on
            # CLI 2.1.158/2.1.160.
            proc.stdin.write(b"\n")
            proc.stdin.flush()
            _park0 = time.monotonic()
            while time.monotonic() - _park0 < _CANARY_PARK_SECONDS:
                if proc.poll() is not None:
                    return False, version, f"parked worker died rc={proc.returncode}"
                time.sleep(0.5)
            # Deliver the real prompt + EOF, then read with the short deadline.
            proc.stdin.write(_CANARY_USER_PROMPT.encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()
            t0 = time.monotonic()
            while True:
                if time.monotonic() - t0 > _CANARY_DEADLINE_SECONDS:
                    return (
                        False, version,
                        f"no stdout within {_CANARY_DEADLINE_SECONDS:.0f}s (hang)",
                    )
                r, _, _ = _select.select([proc.stdout], [], [], 1.0)
                if r:
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if chunk:
                        return True, version, f"responded in {time.monotonic()-t0:.1f}s"
                    return False, version, "EOF before any stdout"
                if proc.poll() is not None:
                    rest = proc.stdout.read() or b""
                    return (
                        bool(rest), version,
                        "exited with output" if rest else "exited with no output",
                    )
        finally:
            if proc is not None:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def replenish_async(self, system_prompt: str) -> None:
        """Schedule a daemon-thread spawn of a replacement worker.

        Returns immediately. The spawn happens in the background so
        the calling thread does not pay subprocess.Popen latency on
        the hot path.
        """
        # Prewarm spawning is gated on reuse_enabled() — prewarm configured ON
        # (max_age > 0) AND the canary has confirmed the current CLI does not hang
        # a keepalive-parked worker. The original incident (CLI 2.1.158/2.1.160)
        # was parked workers hanging with ZERO stdout, piling up holding stdin open
        # and starving the brain — every CALL_A timed out. The blanket disable
        # (max_age=0) prevented that but also gave up all reuse. F28 (2026-06-05)
        # replaces it: spawn prewarm workers ONLY when the canary is healthy, so a
        # CLI that starts hanging self-disables the pool (acquire() misses, the
        # caller cold-spawns) instead of starving the brain — restoring reuse on
        # healthy CLIs while keeping the failure mode harmless.
        if not self.reuse_enabled():
            return
        threading.Thread(
            target=self._replenish_blocking,
            args=(system_prompt,),
            daemon=True,
            name=f"claude-prewarm-{self._hash_sys_prompt(system_prompt)[:8]}",
        ).start()

    def _replenish_blocking(self, system_prompt: str) -> None:
        """Spawn a fresh worker for ``system_prompt`` and install it
        in the pool. No-op if a fresh slot already exists."""
        sys_hash = self._hash_sys_prompt(system_prompt)
        with self._lock:
            existing = self._slots.get(sys_hash)
            if (
                existing is not None
                and existing.is_alive()
                and existing.age_seconds() < self._max_age_seconds
            ):
                # Another thread already replenished this slot. No-op.
                return

        cmd = [self._claude_path, "-p", "--output-format", "text"]
        # Brain-CLI model pin — keep the latency-critical trade call off the slow
        # CLI default (claude-opus-4-8[1m]); empty self._model => CLI default.
        if self._model:
            cmd += ["--model", self._model]
        # Issue 1 (latency) — same flags as the cold-spawn/decision call so the
        # prewarmed worker is a valid match.
        cmd += self._extra_flags
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                cwd=self._project_cwd,
                env=self._env,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            self._spawn_fail_count += 1
            log.warning(
                f"CLAUDE_PROC_PREWARM_SPAWN_FAIL | sys_hash={sys_hash} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return

        # H1 (2026-05-16) — keepalive byte. Empirical Step B1 (see
        # dev_notes/four_high_fixes/h1_phase1_death_diagnosis.md):
        # ``claude -p`` exits with rc=1 after 3 seconds of stdin
        # silence ("Warning: no stdin data received in 3s, ... Error:
        # Input must be provided either through stdin or as a prompt
        # argument when using --print"). Pre-H1 the prewarm pool spawned
        # subprocesses and immediately returned without touching stdin
        # → every prewarmed subprocess died within 3 s, producing 0 %
        # pool hit rate in production. Writing a single newline byte at
        # spawn time resets the 3 s stdin-silence guard inside the CLI;
        # Step B2 confirmed the subprocess survives ≥ 60 s with stdin
        # held open. Step B5 confirmed the leading whitespace is a
        # no-op for response correctness — when the real prompt arrives
        # in ``_subprocess_call`` and stdin is closed, the CLI sees
        # ``b"\n" + prompt_bytes + EOF`` and processes it identically
        # to a leading-whitespace-free prompt.
        try:
            proc.stdin.write(b"\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            # Process died between Popen() and the keepalive write —
            # rare but possible (immediate auth fail, OOM, etc.).
            # Dispose and bail; this counts as a spawn failure.
            self._spawn_fail_count += 1
            log.warning(
                f"CLAUDE_PROC_PREWARM_KEEPALIVE_FAIL | "
                f"pid={getattr(proc, 'pid', '?')} sys_hash={sys_hash} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            self._dispose(proc)
            return

        new_slot = _PrewarmSlot(proc, sys_hash)
        with self._lock:
            existing = self._slots.get(sys_hash)
            if (
                existing is not None
                and existing.is_alive()
                and existing.age_seconds() < self._max_age_seconds
            ):
                # Lost the race — dispose our duplicate.
                self._dispose(proc)
                return
            if existing is not None:
                # Replace stale/dead existing slot
                self._dispose(existing.proc)
            self._slots[sys_hash] = new_slot

        log.info(
            f"CLAUDE_PROC_PREWARM_OK | pid={proc.pid} "
            f"sys_hash={sys_hash} sys_prompt_chars={len(system_prompt or '')} "
            f"keepalive=newline | {ctx()}"
        )

    def shutdown(self) -> None:
        """Dispose all pre-spawned workers. Called on client teardown."""
        with self._lock:
            slots = list(self._slots.values())
            self._slots.clear()
        for slot in slots:
            self._dispose(slot.proc)

    @staticmethod
    def _dispose(proc: subprocess.Popen[bytes]) -> None:
        """Best-effort kill + reap of a pre-spawned worker."""
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal_mod.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal_mod.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
        except Exception:
            # Pool disposal is best-effort; never raise.
            pass


class ClaudeCodeClient:
    """Drop-in replacement for ClaudeClient. Uses CLI instead of API.

    Resolves the full binary path at init time and builds an explicit
    environment dict so it works identically from a terminal, a Python
    script, or a systemd service.
    """

    def __init__(
        self,
        timeout_seconds: int = 90,
        max_retries: int = 2,
        min_interval: float = 2.0,
        retry_timeout_backoff_base_seconds: int = 30,
        credential_refresh_margin_seconds: int = 600,
        credential_refresh_max_attempts: int = 3,
        stall_warn_buckets_seconds: tuple[float, ...] | None = None,
        first_byte_timeout_seconds: float = 90.0,
        prewarm_max_age_seconds: float = 900.0,
        prewarm_stats_interval_seconds: float = 300.0,
        prewarm_canary_ttl_seconds: float = 600.0,
        model: str = "claude-opus-4-7",
        effort: str = "",
        bare: bool = False,
        exclude_dynamic_system_prompt: bool = False,
    ) -> None:
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.min_interval = min_interval
        # P2-1 (2026-05-13): first-byte deadline (separate from total
        # timeout). When > 0, _stream_subprocess_io kills the subprocess
        # and raises a TimeoutExpired carrying ``timeout=first_byte_timeout``
        # if no stdout byte arrives within this window. <= 0 disables.
        # See BrainSettings.claude_cli_first_byte_timeout_seconds for the
        # rationale (defaults to 90 s).
        self._first_byte_timeout = float(first_byte_timeout_seconds or 0.0)
        # Backoff base for timeout-path retries (Phase 2 session-stability fix).
        # sleep = (attempt+1) * base. Default 30 preserves legacy behaviour for
        # callers that don't pass the new argument; ``workers/manager.py``
        # passes the config-backed value ``BrainSettings.claude_cli_retry_timeout_backoff_base_seconds``.
        self.retry_timeout_backoff_base = retry_timeout_backoff_base_seconds
        # Phase 3 (Brain credentials): pre-flight refresh margin and retry
        # budget. Default 600 s (10 min) margin + 3 attempts with
        # exponential backoff (1 s / 3 s / 7 s) eliminates the silent
        # in-call hang surface that the legacy hardcoded 300 s + single
        # urllib attempt left open.
        self._credential_refresh_margin_seconds = float(credential_refresh_margin_seconds)
        self._credential_refresh_max_attempts = int(credential_refresh_max_attempts)
        # Phase 7 (post-Layer-1 fix): graduated stall-warning buckets.
        # WorkerManager wires settings.brain.stall_warn_buckets_seconds
        # into this kwarg so operators can tune thresholds without a
        # code deploy. The 60/120/240 defaults match the original
        # production constants. Each bucket fires at a different log
        # level (60 → INFO, 120 → WARNING, 240 → ERROR) — see
        # _subprocess_call's stall-detection block.
        if stall_warn_buckets_seconds is None:
            stall_warn_buckets_seconds = (60.0, 120.0, 240.0)
        try:
            self._stall_warn_buckets = tuple(
                float(b) for b in stall_warn_buckets_seconds
            )
        except (TypeError, ValueError):
            self._stall_warn_buckets = (60.0, 120.0, 240.0)
        if not self._stall_warn_buckets:
            self._stall_warn_buckets = (60.0, 120.0, 240.0)
        self._call_count = 0
        self._total_calls_today = 0
        # Monotonically-increasing correlation id stamped on every send_message
        # invocation (incremented before any retry loop), threaded into logs so
        # subprocess-spawn / attempt / outcome lines can be linked.
        self._call_id: int = 0
        self._last_call_time = time.time()  # Assume healthy at startup
        # Phase 6 (post-Layer-1 fix): the watchdog at
        # ``src/workers/position_watchdog.py:323`` reads
        # ``_last_response_time`` via ``getattr(..., 0.0) or 0.0`` and
        # falls back to 0 silently when the attribute is missing — which
        # it was. Adding the attribute here unmasks that read; the
        # semantics are: ``_last_call_attempt_time`` updates BEFORE the
        # subprocess spawn, ``_last_response_time`` updates ONLY on a
        # successful return. Together they let the watchdog detect
        # "call started 30 s ago but never completed" — which the prior
        # code could not see.
        self._last_call_attempt_time = time.time()
        self._last_response_time = time.time()
        self._consecutive_failures = 0
        self._adaptive_interval = min_interval

        # Auth backoff state — exponential schedule, not fixed 600s
        self._auth_failed = False
        self._auth_backoff_until = 0.0
        self._auth_failure_count = 0        # consecutive auth failures (for backoff tier)
        self._auth_alert_sent = False       # suppress duplicate Telegram alerts

        # Usage-quota backoff state — distinct from auth failures.
        # "out of extra usage" errors: back off until reset time (not operator-fixable).
        self._usage_exhausted: bool = False
        self._usage_backoff_until: float = 0.0
        self._usage_alert_sent: bool = False

        # Credential hot-reload: detect 'claude login' without service restart
        self._cred_mtime: float = self._get_cred_mtime()

        # Optional Telegram alert callback — injected by WorkerManager
        self._alert_callback: Optional[Callable[[str], Awaitable[None]]] = None

        # Resolve binary path once at startup
        self._claude_path = self._find_claude()

        # Brain-CLI model pin (2026-05-30). Threaded to `claude -p --model <id>`
        # at BOTH spawn sites (prewarm pool + cold spawn) so the latency-critical
        # trade calls use a fast-enough, JSON-reliable tier instead of the CLI
        # default — which is now the slow claude-opus-4-8[1m] (~240s of deep
        # reasoning on the full 24.7K trade prompt, breaching the 300s deadline ->
        # total_timeout). Opus 4.7 returns clean JSON in ~78s (measured). Empty
        # string => do not pass --model, fall back to the CLI default (revert).
        self._model = (model or "").strip()

        # Issue 1 (latency, 2026-06-06) — extra `claude -p` flags that cut the
        # dominant CALL_A cost: the model THINKING before the first token (proven
        # over 1200 live samples — first_token_ms tracks prompt size r=0.80, not
        # pool-hit r=0.01; the warm pool is already enabled and does not reduce
        # the 60-163 s wait). --effort caps Opus 4.7 adaptive-thinking depth (the
        # one live lever). Built ONCE and appended at EVERY spawn site (canary,
        # prewarm, cold spawn) so a prewarmed worker is a valid match for the
        # decision call. All-off (effort="" + both bools False) yields an empty
        # list => byte-identical to the pre-Issue-1 invocation. Fully reversible
        # from config.toml [brain].
        #
        # NOTE on the two bool flags: --bare skips hook/LSP/plugin discovery but on
        # CLI 2.1.167 ALSO skips OAuth login (breaks the call), so it is kept off.
        # --exclude-dynamic-system-prompt-sections is a NO-OP on the brain path:
        # the CLI applies it only with the DEFAULT system prompt and IGNORES it when
        # --system-prompt is supplied, which the brain ALWAYS does. The plumbing is
        # retained (harmless, off) but neither bool changes the live invocation; the
        # active lever is --effort alone.
        _VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
        _extra_flags: list[str] = []
        _eff = (effort or "").strip().lower()
        if _eff and _eff not in _VALID_EFFORTS:
            # Guard a config typo from emitting an invalid --effort that would break
            # every brain call. Fall back to the CLI default (no flag) and warn.
            log.warning(
                f"CLAUDE_CLI_EFFORT_INVALID | value='{_eff}' not in "
                f"{sorted(_VALID_EFFORTS)} — dropping --effort (CLI default) | {ctx()}"
            )
            _eff = ""
        if _eff:
            _extra_flags += ["--effort", _eff]
        if bare:
            _extra_flags.append("--bare")
        if exclude_dynamic_system_prompt:
            _extra_flags.append("--exclude-dynamic-system-prompt-sections")
        # Hermetic brain calls (2026-06-12 root-cause fix). Without this, every
        # spawn loads the MCP servers configured for this cwd — including the
        # project's own trading-intelligence stdio server, i.e. a SECOND full
        # app instance booted inside each brain call. When that instance boots
        # far enough to handshake (it does under the service env), its
        # tools/list times out at 30s and the CLI enters a ~32s
        # connect→timeout→reconnect cycle that starves the turn pipeline:
        # zero stdout for the full 300s deadline (the 2026-06-12 outage; the
        # canary's 45s deadline dies the same way at 2.5s connect + 30s
        # timeout). The brain call is pure text→JSON and must never load MCP
        # servers. --strict-mcp-config with no --mcp-config = load none.
        # This list feeds all three spawn sites: cold, prewarm, and canary.
        _extra_flags.append("--strict-mcp-config")
        self._extra_cli_flags: list[str] = _extra_flags

        # Build the subprocess environment once (defense-in-depth vs systemd)
        self._env = self._build_env()

        # T2-1 (2026-05-12): per-system-prompt pre-spawn pool. Hides
        # ~1-5 s of subprocess.Popen + Claude CLI bootup latency by
        # overlapping it with the previous call's API wait time.
        # P2-1 (2026-05-13): the freshness window is now configurable
        # and defaults to 900 s (15 min) so the worker survives across
        # 5-10-min CALL_A cadences. Operator-tunable via
        # ``BrainSettings.claude_cli_prewarm_max_age_seconds``. The
        # pool also emits a CLAUDE_POOL_STATS line every
        # ``prewarm_stats_interval_seconds`` so the operator can
        # verify the pool is actually delivering hits.
        self._proc_pool = _ClaudeWorkerPool(
            claude_path=self._claude_path,
            env=self._env,
            project_cwd=_PROJECT,
            max_age_seconds=float(prewarm_max_age_seconds),
            stats_interval_seconds=float(prewarm_stats_interval_seconds),
            model=self._model,
            canary_ttl_seconds=float(prewarm_canary_ttl_seconds),
            extra_flags=self._extra_cli_flags,
        )
        # Issue 1 (2026-06-06) boot sentinel — confirm the new CALL_A latency
        # flags loaded. Empty `flags` means no extra flags (CLI default thinking).
        log.info(
            f"CLAUDE_CLI_FLAGS_CONFIG | "
            f"effort={_eff or '(cli-default)'} "
            f"bare={bool(bare)} "
            f"exclude_dynamic_system_prompt={bool(exclude_dynamic_system_prompt)} "
            f"flags='{' '.join(self._extra_cli_flags) or '(none)'}' | {ctx()}"
        )
        # F28 (2026-06-05) boot sentinel — confirm the prewarm re-enable + canary
        # gate loaded. max_age>0 with reuse_gated_on_canary=True means the pool is
        # re-enabled but a parked-worker hang on a future CLI cannot block CALL_A.
        log.info(
            f"CLAUDE_POOL_PREWARM_CONFIG | "
            f"max_age_s={float(prewarm_max_age_seconds):.0f} "
            f"canary_ttl_s={float(prewarm_canary_ttl_seconds):.0f} "
            f"reuse_gated_on_canary=True | {ctx()}"
        )

        # Startup health checks + diagnostics
        self._log_diagnostics()
        self._validate_setup()

    # ─── Public API (same interface as old ClaudeClient) ───

    def set_alert_callback(
        self, callback: Callable[[str], Awaitable[None]]
    ) -> None:
        """Inject Telegram alert callback (called on persistent auth failure).

        Args:
            callback: async function accepting a message string. Injected by
                      WorkerManager after AlertManager is ready.
        """
        self._alert_callback = callback
        # Phase 12.2 (lifecycle-logging-audit Gap 2.3-G1): structured tag.
        log.info(f"CLAUDE_ALERT_CALLBACK_OK | {ctx()}")

    async def send_message(
        self, prompt: str, system_prompt: str = "", max_tokens: int = 4096,
        call_type: str = "other",
    ) -> str:
        """Send prompt to Claude via CLI.

        Args:
            prompt: the user message
            system_prompt: system context (prepended with separator)
            max_tokens: ignored (CLI manages internally)

        Returns:
            Claude's response as a string

        Raises:
            AuthenticationError: For credential/auth failures.
            ClaudeAPIError: For billing, rate limit, or API errors.
            BrainError: For general brain failures after retries exhausted.
        """
        if not self._claude_path:
            raise BrainError(
                "claude CLI binary not found — cannot send message",
                details={"searched_paths": [
                    "/usr/bin/claude", "/usr/local/bin/claude",
                    f"{_HOME}/.local/bin/claude",
                ]},
            )

        # Auth backoff: skip calls when auth is known-expired
        if self._auth_failed and time.time() < self._auth_backoff_until:
            # Hot-reload: if 'claude login' was run, credentials file changes.
            # Re-read and reset auth state so we recover without a restart.
            if self._credentials_changed():
                log.info("CLAUDE_CRED_RELOAD | credentials file updated — clearing auth backoff")
                self._auth_failed = False
                self._auth_failure_count = 0
                self._auth_alert_sent = False
            else:
                remaining = int(self._auth_backoff_until - time.time())
                raise AuthenticationError(
                    f"Claude auth expired — skipping call (backoff {remaining}s). "
                    "Run 'claude login' on the server to re-authenticate.",
                    details={"backoff_remaining_s": remaining},
                )

        # Usage-quota backoff: skip calls when daily quota is known-exhausted
        if self._usage_exhausted:
            if time.time() < self._usage_backoff_until:
                remaining = int(self._usage_backoff_until - time.time())
                raise ClaudeAPIError(
                    f"Claude usage quota exhausted — skipping call (backoff {remaining}s remaining). "
                    "Quota resets daily.",
                )
            else:
                # Backoff window expired — reset and allow the call through
                self._usage_exhausted = False
                self._usage_alert_sent = False
                log.info(f"CLAUDE_USAGE_RECOVERED | quota backoff expired — resuming calls | {ctx()}")

        # Rate limiting
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < self._adaptive_interval:
            wait = self._adaptive_interval - elapsed
            log.debug(f"CLAUDE_RATE | sleep={wait:.1f}s interval={self._adaptive_interval:.1f}s | {ctx()}")
            await asyncio.sleep(wait)

        # Prompt is delivered via stdin; system_prompt via --system-prompt flag.
        # Bundling them into a single string added ~4K chars to the user message
        # and placed system instructions in the wrong conversation slot.
        full_prompt = prompt

        import hashlib
        prompt_hash = hashlib.sha256(full_prompt.encode()).hexdigest()[:12]
        self._call_id += 1
        _cid = self._call_id
        log.info(
            f"CLAUDE_CALL_START | call_id={_cid} in={len(full_prompt)} sys={len(system_prompt)} "
            f"timeout={self.timeout}s hash={prompt_hash} | {ctx()}"
        )
        log.debug(f"CLAUDE_PROMPT | call_id={_cid} chars={len(full_prompt)} has_system={'Y' if system_prompt else 'N'} | {ctx()}")

        # Phase 6 (post-Layer-1 fix) introduced this pre-flight; Phase 3
        # (Brain credentials) raised the default margin from 300 s to
        # ``credential_refresh_margin_seconds`` (default 600 s) and
        # converted refresh failure inside the margin from "log + proceed"
        # to "raise CredentialRefreshError". The rationale: spawning a
        # subprocess that is going to inherit an about-to-expire token
        # produces the silent 300-s hang we are trying to prevent.
        # CredentialRefreshError propagates out of call() so the caller
        # sees a fast failure with clear attribution.
        self._ensure_credentials_fresh()

        # Retry loop
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                # Pre-call cleanup: kill orphaned claude processes
                self._cleanup_orphaned_processes()

                # Phase 6: stamp attempt time BEFORE the subprocess so
                # the watchdog can detect "call started but never
                # finished" (the previous code only updated on success).
                self._last_call_attempt_time = time.time()
                _t0 = self._last_call_attempt_time
                response = await self._execute_cli(full_prompt, system_prompt)
                _elapsed_ms = (time.time() - _t0) * 1000
                self._call_count += 1
                self._total_calls_today += 1
                self._last_call_time = time.time()
                self._last_response_time = self._last_call_time
                self._consecutive_failures = 0
                self._adaptive_interval = self.min_interval
                self._auth_failed = False         # Reset auth flag on success
                self._auth_failure_count = 0
                self._auth_alert_sent = False
                self._usage_exhausted = False    # Reset quota flag on success
                self._usage_alert_sent = False

                log.info(f"CLAUDE_CALL_OK | call_id={_cid} attempt={attempt + 1}/{self.max_retries + 1} el={_elapsed_ms:.0f}ms out={len(response)} calls={self._total_calls_today} | {ctx()}")
                # H1 (2026-05-16) — CALL_A_PHASE_TIMING. Spec Rule 6
                # mandated event surfacing the latency breakdown so the
                # operator can attribute total CALL_A elapsed to
                # specific phases (pool acquire vs cold spawn vs prompt
                # write vs first-token wait vs inference vs response
                # consumption). Components are stashed in
                # _subprocess_call and _stream_subprocess_io via
                # self._last_* fields; values are best-effort and
                # default to 0.0 when the call took a path that didn't
                # populate a particular field (e.g. cold-spawn miss
                # leaves pool_acquire_ms=0).
                _pool_acq = float(getattr(self, "_last_pool_acquire_ms", 0.0) or 0.0)
                _cold_spawn = float(getattr(self, "_last_cold_spawn_ms", 0.0) or 0.0)
                _prompt_write = float(getattr(self, "_last_prompt_write_ms", 0.0) or 0.0)
                _first_token = float(getattr(self, "_last_first_token_ms", 0.0) or 0.0)
                _was_hit = bool(getattr(self, "_last_was_pool_hit", False))
                # inference_ms is the post-first-token portion (model
                # is generating). full_response_ms is the total elapsed.
                _inference = max(0.0, _elapsed_ms - _first_token) if _first_token > 0 else 0.0
                # H1/H2/H3 (IMPLEMENT_FIVE_ISSUES_FIX.md Rule 7,
                # 2026-05-20) — API-side breakdown augmentation. Lets
                # the operator plot first_token_ms vs prompt size,
                # observe API throughput post-first-token, correlate
                # pool age with latency, and see credential TTL at
                # call entry without grep-joining other events.
                _prompt_full_chars = int(getattr(self, "_last_prompt_chars", 0) or 0)
                _sys_full_chars = int(getattr(self, "_last_sys_prompt_chars", 0) or 0)
                _prompt_input_tokens_est = int((_prompt_full_chars + _sys_full_chars) / 3.5)
                _output_chars = len(response) if isinstance(response, str) else 0
                _output_tokens_est = int(_output_chars / 3.5)
                _tokens_per_sec_post_first_token = (
                    (_output_tokens_est / (_inference / 1000.0))
                    if _inference > 0 else 0.0
                )
                _pool_age = float(getattr(self, "_last_pool_age_s", 0.0) or 0.0) if _was_hit else 0.0
                _cred_ttl_at_start = self._get_credential_expiry_seconds()
                _cred_ttl_int = int(_cred_ttl_at_start) if _cred_ttl_at_start is not None else -1
                log.info(
                    f"CALL_A_PHASE_TIMING | call_id={_cid} "
                    f"pool_hit={_was_hit} "
                    f"pool_acquire_ms={_pool_acq:.0f} "
                    f"cold_spawn_ms={_cold_spawn:.0f} "
                    f"prompt_write_ms={_prompt_write:.0f} "
                    f"first_token_ms={_first_token:.0f} "
                    f"inference_ms={_inference:.0f} "
                    f"full_response_ms={_elapsed_ms:.0f} "
                    f"prompt_input_tokens_est={_prompt_input_tokens_est} "
                    f"output_chars={_output_chars} "
                    f"output_tokens_est={_output_tokens_est} "
                    f"tokens_per_sec_post_first_token={_tokens_per_sec_post_first_token:.1f} "
                    f"pool_age_s_on_hit={_pool_age:.1f} "
                    f"cred_ttl_s_at_call_start={_cred_ttl_int} "
                    f"| {ctx()}"
                )
                log.debug(
                    "Claude Code call #{n} OK ({chars} chars)",
                    n=self._total_calls_today,
                    chars=len(response),
                )
                _maybe_dump_call(_cid, full_prompt, system_prompt, response, _elapsed_ms, prompt_hash, call_type)
                return response

            except _NonRetryableError as e:
                # Billing/auth/rate-limit errors — don't waste retries
                self._consecutive_failures += 1
                self._adaptive_interval = min(
                    self.min_interval * (2 ** self._consecutive_failures), 30.0
                )
                error_str = str(e)
                log.warning(f"CLAUDE_NONRETRY | err='{error_str[:150]}' | {ctx()}")
                log.warning(
                    "Claude Code non-retryable error: {e}",
                    e=error_str,
                )

                # Classify into specific exception types for callers
                error_lower = error_str.lower()

                # ── Usage quota exhaustion ──
                # "You're out of extra usage · resets 6pm (UTC)" — Claude Max daily cap.
                # Back off until reset time. This is NOT an auth failure — retrying or
                # re-logging will not help; only the quota reset matters.
                if any(p in error_lower for p in ("out of extra usage", "extra usage")):
                    reset_ts = self._parse_usage_reset(error_str)
                    if reset_ts:
                        backoff_s = max(int(reset_ts - time.time()), 300)
                    else:
                        backoff_s = 3600  # default 1h if reset time unparseable
                    self._usage_exhausted = True
                    self._usage_backoff_until = time.time() + backoff_s

                    reset_str = (
                        datetime.fromtimestamp(reset_ts, tz=timezone.utc).strftime("%H:%M UTC")
                        if reset_ts else "unknown"
                    )
                    log.warning(
                        f"CLAUDE_QUOTA_EXHAUSTED | backoff={backoff_s}s reset={reset_str} "
                        f"err='{error_str[:100]}' | {ctx()}"
                    )

                    if not self._usage_alert_sent and self._alert_callback is not None:
                        self._usage_alert_sent = True
                        alert_msg = (
                            "⚠️ <b>Claude Daily Usage Cap Reached</b>\n\n"
                            f"Quota exhausted — brain paused ~{backoff_s // 3600:.0f}h "
                            f"(resets {reset_str}).\n\n"
                            f"<code>{error_str[:200]}</code>"
                        )
                        try:
                            await self._alert_callback(alert_msg)
                        except Exception as alert_err:
                            log.warning(f"CLAUDE_ALERT_FAIL | err='{str(alert_err)[:80]}'")

                    raise ClaudeAPIError(
                        f"Claude usage quota exhausted: {error_str}",
                    ) from e

                if any(p in error_lower for p in ("authentication", "unauthorized", "api key")):
                    # ── Auth recovery: 3-layer strategy ──
                    # Layer 1: attempt OAuth token refresh
                    refresh_ok = self._try_token_refresh()
                    if refresh_ok:
                        # Token refreshed — retry the call immediately (don't count as failure)
                        log.info(f"CLAUDE_AUTH_RECOVERED | method=token_refresh | retrying | {ctx()}")
                        try:
                            self._last_call_attempt_time = time.time()
                            _t0 = self._last_call_attempt_time
                            response = await self._execute_cli(full_prompt, system_prompt)
                            _elapsed_ms = (time.time() - _t0) * 1000
                            self._call_count += 1
                            self._total_calls_today += 1
                            self._last_call_time = time.time()
                            self._last_response_time = self._last_call_time
                            self._consecutive_failures = 0
                            self._adaptive_interval = self.min_interval
                            self._auth_failed = False
                            self._auth_failure_count = 0
                            self._auth_alert_sent = False
                            self._usage_exhausted = False
                            self._usage_alert_sent = False
                            log.info(f"CLAUDE_CALL_OK | call_id={_cid} attempt=auth_refresh el={_elapsed_ms:.0f}ms out={len(response)} (post-refresh) | {ctx()}")
                            _maybe_dump_call(_cid, full_prompt, system_prompt, response, _elapsed_ms, prompt_hash, call_type)
                            return response
                        except Exception as retry_err:
                            retry_err_str = str(retry_err).lower()
                            if "timed out" in retry_err_str:
                                # Post-refresh timeout is a transient failure, not an auth issue.
                                # Skip Layer 3 auth backoff and let the outer retry loop handle it.
                                log.warning(
                                    f"CLAUDE_POST_REFRESH_TIMEOUT | timed out — retrying without "
                                    f"auth backoff (attempt {attempt + 1}/{self.max_retries + 1}) | {ctx()}"
                                )
                                last_error = retry_err
                                if attempt < self.max_retries:
                                    await asyncio.sleep(30)
                                continue  # outer for attempt loop — skip Layer 2/3 auth path
                            log.warning(f"CLAUDE_POST_REFRESH_FAIL | err='{str(retry_err)[:100]}' | {ctx()}")

                    # Layer 2: credential hot-reload (someone may have run 'claude login')
                    if self._credentials_changed():
                        log.info(f"CLAUDE_CRED_RELOAD | credentials updated — will retry next call | {ctx()}")
                        self._auth_failed = False
                        self._auth_failure_count = 0
                        self._auth_alert_sent = False
                        raise AuthenticationError(
                            "Claude credentials updated — retry next cycle.",
                            details={"credential_path": str(_CREDENTIAL_PATH)},
                        ) from e

                    # Layer 3: exponential backoff + Telegram alert
                    self._auth_failure_count += 1
                    tier = min(self._auth_failure_count - 1, len(_AUTH_BACKOFF_SCHEDULE) - 1)
                    backoff_s = _AUTH_BACKOFF_SCHEDULE[tier]
                    self._auth_failed = True
                    self._auth_backoff_until = time.time() + backoff_s

                    log.error(f"CLAUDE_AUTH | status=expired failures={self._auth_failure_count} backoff={backoff_s}s | {ctx()}")
                    # Phase 12.2 (lifecycle-logging-audit Gap 2.3-G2):
                    # deleted prose duplicate of CLAUDE_AUTH above.

                    # Send Telegram alert once per auth-fail cycle (not on every retry)
                    if not self._auth_alert_sent and self._alert_callback is not None:
                        self._auth_alert_sent = True
                        alert_msg = (
                            "⚠️ <b>Claude Auth Expired</b>\n\n"
                            "The Claude OAuth token has expired. The trading brain is "
                            "temporarily disabled.\n\n"
                            "<b>Fix:</b> SSH into the server and run:\n"
                            "<code>claude login</code>\n\n"
                            "The service will recover automatically once logged in."
                        )
                        try:
                            await self._alert_callback(alert_msg)
                        except Exception as alert_err:
                            log.warning(f"CLAUDE_ALERT_FAIL | err='{str(alert_err)[:80]}'")

                    raise AuthenticationError(
                        f"Claude CLI authentication failed: {error_str}",
                        details={
                            "credential_path": str(_CREDENTIAL_PATH),
                            "failure_count": self._auth_failure_count,
                            "backoff_s": backoff_s,
                        },
                    ) from e
                raise ClaudeAPIError(
                    f"Claude CLI non-retryable error: {error_str}",
                ) from e

            except Exception as e:
                last_error = e
                self._consecutive_failures += 1
                self._adaptive_interval = min(
                    self.min_interval * (2 ** self._consecutive_failures), 30.0
                )

                # Tag distinct CLAUDE_CALL_TIMEOUT events so dashboards/alerts can
                # separate timeouts (Claude-side slowness) from generic failures.
                # P2-1 (2026-05-13): the first-byte deadline path produces a
                # distinct RuntimeError message — surface it as a separate
                # tag so operators can monitor the new path independently
                # from the total-timeout path. Both paths still trigger the
                # generic CLAUDE_RETRY emit below.
                _err_lower = str(e).lower()
                if "first-byte deadline" in _err_lower:
                    log.warning(
                        f"CLAUDE_RETRY_ON_FIRST_BYTE_TIMEOUT | call_id={_cid} "
                        f"attempt={attempt + 1}/{self.max_retries + 1} "
                        f"first_byte_timeout_s={self._first_byte_timeout:.0f} "
                        f"err='{str(e)[:120]}' | {ctx()}"
                    )
                elif "timed out" in _err_lower:
                    log.warning(
                        f"CLAUDE_CALL_TIMEOUT | call_id={_cid} attempt={attempt + 1}/{self.max_retries + 1} "
                        f"timeout={self.timeout}s err='{str(e)[:120]}' | {ctx()}"
                    )
                log.warning(f"CLAUDE_RETRY | call_id={_cid} attempt={attempt + 1}/{self.max_retries + 1} err='{str(e)[:150]}' interval={self._adaptive_interval:.1f}s | {ctx()}")
                log.warning(
                    "Claude Code attempt {a}/{t} failed: {e}. Interval={iv:.1f}s",
                    a=attempt + 1,
                    t=self.max_retries + 1,
                    e=str(e),
                    iv=self._adaptive_interval,
                )
                if attempt < self.max_retries:
                    # After a timeout the API was slow — wait longer before retry.
                    # After other errors (non-retryable excluded above) a quick
                    # exponential backoff is sufficient.
                    # Phase 2 (Y-22 companion): timeout-backoff base is now
                    # configurable (was hardcoded 30). Lowering the base to
                    # 10 gives a 10/20/30 ladder instead of 30/60/90, which
                    # halves the brain-outage window after a single timeout
                    # while still spacing retries exponentially.
                    is_timeout = "timed out" in str(e).lower()
                    sleep_s = (
                        (attempt + 1) * self.retry_timeout_backoff_base
                        if is_timeout
                        else 2 ** attempt
                    )
                    log.debug(
                        f"CLAUDE_RETRY_SLEEP | sleep={sleep_s}s "
                        f"reason={'timeout_backoff' if is_timeout else 'error_backoff'} | {ctx()}"
                    )
                    await asyncio.sleep(sleep_s)

        log.error(f"CLAUDE_CALL_FAIL | call_id={_cid} err='{str(last_error)[:150]}' attempts={self.max_retries + 1} | {ctx()}")
        raise BrainError(
            f"Claude Code failed after {self.max_retries + 1} attempts: {last_error}",
        )

    def extract_json(self, response: str) -> dict:
        """Extract JSON from Claude Code response.

        Handles: ```json blocks, bare JSON objects, bare arrays, raw JSON.
        """
        text = response.strip()

        # Strategy 1: ```json ... ``` block
        match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Strategy 2: first { to last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        # Strategy 3: first [ to last ]
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, list):
                    return {"decisions": result}
                return result
            except json.JSONDecodeError:
                pass

        # Strategy 4: raw parse
        try:
            return json.loads(text)
        except json.JSONDecodeError as _e:
            # Phase 11 Gap F1 (output-quality obs): emit a distinct
            # CLAUDE_PARSE_FAIL tag so operators can grep for parse vs
            # API failures separately. The existing CLAUDE_CALL_FAIL
            # covers subprocess/API errors; CLAUDE_PARSE_FAIL covers
            # cases where the API succeeded but produced un-parseable
            # JSON. Both lead to errors but require different fixes
            # (API config vs prompt engineering).
            log.warning(
                f"CLAUDE_PARSE_FAIL | reason=json_decode "
                f"err='{str(_e)[:80]}' raw_response='{text[:100]}...' | {ctx()}"
            )
            raise ValueError(f"Cannot extract JSON from response:\n{text[:300]}...")

    def get_stats(self) -> dict:
        return {
            "calls_today": self._total_calls_today,
            "cost_today": 0.0,
            "adaptive_interval": round(self._adaptive_interval, 1),
            "consecutive_failures": self._consecutive_failures,
            "binary_path": self._claude_path or "NOT FOUND",
        }

    def shutdown(self) -> None:
        """T2-1 (2026-05-12): dispose any pre-spawned pool workers.

        Called during ``WorkerManager`` graceful shutdown so primed
        workers do not leak as orphan claude CLI processes.
        Idempotent.
        """
        if hasattr(self, "_proc_pool"):
            self._proc_pool.shutdown()

    # ─── Private ───

    # ── Auth recovery helpers ──

    def _get_cred_mtime(self) -> float:
        """Return credentials file modification time (0.0 if missing)."""
        try:
            return _CREDENTIAL_PATH.stat().st_mtime if _CREDENTIAL_PATH.exists() else 0.0
        except OSError:
            return 0.0

    def _credentials_changed(self) -> bool:
        """Return True if credentials file was updated since last check.

        Detects when the operator ran 'claude login' while the service was
        running, allowing auth recovery without a service restart.
        """
        current_mtime = self._get_cred_mtime()
        if current_mtime != self._cred_mtime:
            self._cred_mtime = current_mtime
            return True
        return False

    def _get_credential_expiry_seconds(self) -> float | None:
        """Return seconds until ``expiresAt`` in the credentials file, or None.

        Returns:
            ``None`` if the credentials file is missing/unreadable, or
            ``expiresAt`` is absent. A negative value means the credentials
            have already expired. A non-negative value is the time-to-live
            in seconds.
        """
        if not _CREDENTIAL_PATH.is_file():
            return None
        try:
            creds = json.loads(_CREDENTIAL_PATH.read_text())
            expires_ms = creds.get("claudeAiOauth", {}).get("expiresAt", 0)
            if not expires_ms:
                return None
            now_s = time.time()
            return (expires_ms / 1000.0) - now_s
        except (json.JSONDecodeError, OSError):
            return None

    def _ensure_credentials_fresh(
        self, min_remaining_seconds: float | None = None,
    ) -> bool:
        """Pro-actively refresh the OAuth token if it expires soon.

        Phase 6 (post-Layer-1 fix) introduced the pre-flight check.
        Phase 3 (Brain credentials) extended it with:

        - Configurable margin via ``credential_refresh_margin_seconds``
          (default 600 s / 10 min), passed at construction. Pass an
          explicit override here only for tests.
        - Multi-attempt refresh with exponential backoff (1 s / 3 s / 7 s)
          inside ``_try_token_refresh``. The legacy single 30-s urllib
          call gave up at the first transient blip.
        - On final failure when the call is INSIDE the margin: raise
          ``CredentialRefreshError`` instead of returning False. The
          caller catches this at the call() entry and aborts the call
          rather than spawning a doomed subprocess.

        Returns:
            ``True`` if credentials are usable for the upcoming call
            (already fresh, or refreshed successfully).
            ``False`` if the credentials file is unreadable (we let the
            live path surface the issue) — preserves legacy behaviour
            so a corrupted credentials file does not become a hard fail.

        Raises:
            CredentialRefreshError: If refresh failed inside the margin.
        """
        margin = (
            min_remaining_seconds
            if min_remaining_seconds is not None
            else self._credential_refresh_margin_seconds
        )
        ttl = self._get_credential_expiry_seconds()
        if ttl is None:
            # Cannot read — let the live path surface the issue.
            return True
        if ttl > margin:
            return True

        log.info(
            f"CLAUDE_PREFLIGHT_REFRESH | reason=expires_in mins_left={ttl/60:.1f} "
            f"threshold_min={margin/60:.1f} attempts={self._credential_refresh_max_attempts} "
            f"| {ctx()}"
        )
        ok = self._try_token_refresh_with_retries()
        if ok:
            new_ttl = self._get_credential_expiry_seconds()
            log.info(
                f"CLAUDE_PREFLIGHT_REFRESH_OK | "
                f"new_mins_left={(new_ttl or 0)/60:.1f} | {ctx()}"
            )
            return True

        # Inside the margin AND refresh failed → fail loudly so the
        # caller aborts before spawning the doomed subprocess.
        log.error(
            f"CRED_REFRESH_FAILED_BLOCKING | mins_left={ttl/60:.1f} "
            f"margin_min={margin/60:.1f} attempts={self._credential_refresh_max_attempts} "
            f"action=abort_call | {ctx()}"
        )
        raise CredentialRefreshError(
            "OAuth refresh failed inside pre-flight margin; "
            f"credentials expire in {ttl:.0f}s, margin is {margin:.0f}s.",
            details={
                "remaining_seconds": ttl,
                "margin_seconds": margin,
                "attempts": self._credential_refresh_max_attempts,
            },
        )

    def _try_token_refresh_with_retries(self) -> bool:
        """Wrap ``_try_token_refresh`` with exponential-backoff retries.

        Phase 3 (Brain credentials). The single-attempt urllib call in
        ``_try_token_refresh`` is fragile under transient network blips.
        Backoff ladder is fixed at (1 s, 3 s, 7 s) for the first three
        attempts; additional attempts (if configured) add 7 s each.

        Returns:
            ``True`` on first successful refresh; ``False`` when the
            full attempt budget is exhausted.
        """
        backoff_ladder = [1.0, 3.0, 7.0]
        max_attempts = max(1, self._credential_refresh_max_attempts)
        for attempt in range(1, max_attempts + 1):
            log.info(
                f"CRED_REFRESH_ATTEMPT | attempt={attempt}/{max_attempts} | {ctx()}"
            )
            ok = self._try_token_refresh()
            if ok:
                if attempt > 1:
                    log.info(
                        f"CRED_REFRESH_ATTEMPT_OK | attempt={attempt} | {ctx()}"
                    )
                return True
            # Last attempt — no point sleeping.
            if attempt >= max_attempts:
                break
            sleep_s = (
                backoff_ladder[attempt - 1]
                if attempt - 1 < len(backoff_ladder)
                else backoff_ladder[-1]
            )
            log.warning(
                f"CRED_REFRESH_RETRY | attempt={attempt}/{max_attempts} "
                f"sleep_s={sleep_s:.1f} | {ctx()}"
            )
            time.sleep(sleep_s)
        return False

    def _try_token_refresh(self) -> bool:
        """Attempt OAuth token refresh using the stored refreshToken.

        Posts to Claude's OAuth token endpoint to exchange the refresh token
        for a new access token, then writes the updated credentials back to
        the credentials file. This is a best-effort operation — if the
        endpoint is unreachable or the refresh token has also expired, it
        returns False and the caller falls through to backoff + alert.

        Returns:
            True if a new accessToken was obtained and credentials updated.
            False if refresh failed for any reason.
        """
        try:
            if not _CREDENTIAL_PATH.is_file():
                return False

            creds = json.loads(_CREDENTIAL_PATH.read_text())
            oauth = creds.get("claudeAiOauth", {})
            refresh_token = oauth.get("refreshToken", "")
            if not refresh_token:
                log.debug("CLAUDE_REFRESH_SKIP | reason=no_refresh_token_in_credentials")
                return False

            log.info("CLAUDE_REFRESH_ATTEMPT | posting to OAuth token endpoint")

            payload = json.dumps({
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _OAUTH_CLIENT_ID,
            }).encode()

            req = urllib.request.Request(
                _OAUTH_TOKEN_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "claude-code/1.0.0 (python-client)",
                    "Accept": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            new_access_token = data.get("access_token")
            if not new_access_token:
                log.warning("CLAUDE_REFRESH_FAIL | reason=no_access_token_in_response")
                return False

            # Write updated token back to credentials file
            expires_in = int(data.get("expires_in", 3600))
            new_refresh = data.get("refresh_token") or refresh_token  # may rotate
            new_expiry_ms = int(
                (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp() * 1000
            )

            oauth["accessToken"] = new_access_token
            oauth["expiresAt"] = new_expiry_ms
            oauth["refreshToken"] = new_refresh
            creds["claudeAiOauth"] = oauth
            _CREDENTIAL_PATH.write_text(json.dumps(creds, indent=2))
            self._cred_mtime = self._get_cred_mtime()  # sync mtime after write

            log.info(
                f"CLAUDE_REFRESH_OK | new_token_expires_in={expires_in}s | credentials updated | {ctx()}"
            )
            return True

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(200).decode(errors="replace")
            except Exception:
                pass
            log.warning(f"CLAUDE_REFRESH_FAIL | http={e.code} body='{body[:100]}'")
            return False
        except Exception as e:
            log.warning(f"CLAUDE_REFRESH_FAIL | err='{str(e)[:100]}'")
            return False

    @staticmethod
    def _parse_usage_reset(error_str: str) -> Optional[float]:
        """Parse the daily quota reset time from a Claude usage-cap error message.

        Handles patterns like:
          "You're out of extra usage · resets 6pm (UTC)"
          "out of extra usage · resets 18:00 (UTC)"

        Returns:
            UTC Unix timestamp of the next reset, or None if unparseable.
        """
        try:
            # Match "resets Hpm", "resets H:MMam/pm", "resets HH:MM" — all UTC
            match = re.search(
                r'resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:\(UTC\))?',
                error_str,
                re.IGNORECASE,
            )
            if not match:
                return None

            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            ampm = (match.group(3) or "").lower()

            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0

            now = datetime.now(timezone.utc)
            reset_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset_dt <= now:
                reset_dt += timedelta(days=1)  # reset is tomorrow
            return reset_dt.timestamp()

        except Exception:
            return None

    def _log_diagnostics(self) -> None:
        """Log resolved environment configuration for debugging."""
        log.info("Claude Code Client diagnostics:")
        log.info("  Binary: {path}", path=self._claude_path or "NOT FOUND")
        log.info("  HOME: {home}", home=self._env.get("HOME", "UNSET"))
        log.info("  Project dir: {proj}", proj=_PROJECT)
        log.info("  Credential file: {path} (exists={ok})",
                 path=str(_CREDENTIAL_PATH), ok=_CREDENTIAL_PATH.is_file())
        log.info("  PATH: {path}", path=self._env.get("PATH", "UNSET"))

    def _validate_setup(self) -> None:
        """Validate binary, credentials, and environment at startup.

        Note on expiresAt: The value in the credentials file reflects the
        OAuth session expiry (can be hours away) but the underlying accessToken
        may already be rejected by the server. Full validation only happens on
        the first actual API call. This method only logs warnings — it does NOT
        set _auth_failed, since a proactive token refresh attempt is made on
        first call failure rather than pre-emptively blocking all calls.
        """
        if not self._claude_path:
            log.error(
                "claude CLI binary not found in PATH or common locations. "
                "Calls to send_message() will fail."
            )
            return

        if not _CREDENTIAL_PATH.is_file():
            log.error(
                "Credential file not found: {path}. "
                "Claude CLI will fail to authenticate.",
                path=str(_CREDENTIAL_PATH),
            )
            return

        try:
            creds = json.loads(_CREDENTIAL_PATH.read_text())
            oauth = creds.get("claudeAiOauth", {})

            expires_ms = oauth.get("expiresAt", 0)
            if expires_ms:
                expires_dt = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                remaining = expires_dt - now

                if remaining.total_seconds() <= 0:
                    # Session expired — attempt token refresh before first call
                    log.warning(
                        "Claude session EXPIRED at {exp} — will attempt token refresh on first call.",
                        exp=expires_dt.isoformat(),
                    )
                elif remaining.total_seconds() < 3600:
                    log.warning(
                        "Claude credentials expire in {mins:.0f} minutes ({exp})",
                        mins=remaining.total_seconds() / 60,
                        exp=expires_dt.isoformat(),
                    )
                else:
                    log.info(
                        "  Credentials valid until {exp} ({hours:.1f}h remaining)",
                        exp=expires_dt.isoformat(),
                        hours=remaining.total_seconds() / 3600,
                    )

            has_refresh = bool(oauth.get("refreshToken"))
            sub_type = oauth.get("subscriptionType", "unknown")
            log.info(
                "  Subscription: {sub} | has_refresh_token={has_rt}",
                sub=sub_type,
                has_rt=has_refresh,
            )

        except (json.JSONDecodeError, OSError) as e:
            log.error(
                "Cannot read credential file {path}: {err}",
                path=str(_CREDENTIAL_PATH),
                err=str(e),
            )

    async def _execute_cli(self, prompt: str, system_prompt: str = "") -> str:
        """Run the claude CLI as a subprocess in a thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._subprocess_call, prompt, system_prompt)

    # Phase 6 follow-up (post-Layer-1 fix): stall-detection thresholds.
    # Emit a CLAUDE_PROC_STALL warning at every multiple of this interval
    # of stdout silence, so operators get a "still alive but quiet at
    # 60s" warning before the full timeout triggers.
    _STALL_LOG_EVERY_S = 60.0
    # Polling cadence for the chunked stdout reader. Coarser than 50ms
    # would risk under-counting stall durations; finer would CPU-spin.
    _SUBPROC_POLL_INTERVAL_S = 0.05

    def _subprocess_call(self, prompt: str, system_prompt: str = "") -> str:
        """Spawn the claude CLI subprocess with chunked-stdout streaming
        and a stall watchdog.

        Phase 6 follow-up of the post-Layer-1 fix work. The previous
        implementation used ``proc.communicate(timeout=...)`` which is
        all-or-nothing: stdout/stderr were not visible to the operator
        until either complete success or full timeout. A 90 s silent
        hang at credential boundaries presented as "no logs for 90 s →
        timeout" with no advance warning.

        This implementation streams stdout in 4 KiB chunks, tracks the
        time of the last byte received, and emits ``CLAUDE_PROC_STALL``
        warnings at every ``_STALL_LOG_EVERY_S`` of silence. On full
        timeout, ``CLAUDE_PROC_PREKILL`` captures ``/proc/<pid>/status``
        and ``/proc/<pid>/wchan`` so operators have root-cause data
        instead of just "it hung".

        Prompt is delivered via stdin (writes are buffered, then stdin
        is closed so the CLI sees EOF). System prompt via flag.
        """
        cmd = [self._claude_path, "-p", "--output-format", "text"]
        # Brain-CLI model pin — keep the latency-critical trade call off the slow
        # CLI default (claude-opus-4-8[1m]); empty self._model => CLI default.
        if self._model:
            cmd += ["--model", self._model]
        # Issue 1 (latency, 2026-06-06) — effort/bare/exclude-dynamic flags that
        # cut the model's pre-first-token thinking. Same list the prewarm pool
        # spawns with, so an acquired warm worker matches this invocation.
        cmd += self._extra_cli_flags
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]

        # HIGH-4 fix (2026-05-09): record prompt sizes so the spawn log
        # and subsequent stall logs carry them. Audit observed 87% of
        # brain calls stall 60s+; correlating stalls with prompt
        # complexity needs the prompt size at log time.
        _prompt_chars = len(prompt) if prompt else 0
        _sys_prompt_chars = len(system_prompt) if system_prompt else 0
        # Stash for the stall watcher (same call's _stream_subprocess_io)
        self._last_prompt_chars = _prompt_chars
        self._last_sys_prompt_chars = _sys_prompt_chars

        proc = None
        _spawn_t0 = time.time()
        try:
            # F28: keep the warm-pool health canary fresh (non-blocking). On the
            # first calls — and whenever the canary is stale or the CLI starts
            # hanging — reuse_enabled() is False so acquire() misses and we
            # cold-spawn (safe); once the canary passes, reuse turns on.
            self._proc_pool.maybe_run_canary_async()
            # T2-1 (2026-05-12): try to acquire a pre-spawned worker
            # primed with this exact system_prompt. Hides spawn + CLI
            # bootup latency (~1-5 s) by overlapping with the previous
            # call's API wait time. Falls back to cold spawn if pool
            # empty / stale / process died.
            # F28: only REUSE when the canary has confirmed the current CLI does
            # not hang a parked worker (reuse_enabled()); otherwise skip acquire and
            # cold-spawn. The gate lives here (not inside acquire) so acquire stays
            # a pure slot-pop. A hung parked worker can never reach a CALL_A.
            if self._proc_pool.reuse_enabled():
                prewarm_proc, prewarm_age_s = self._proc_pool.acquire(
                    system_prompt
                )
            else:
                prewarm_proc, prewarm_age_s = None, 0.0
            if prewarm_proc is not None:
                proc = prewarm_proc
                _spawn_ms = (time.time() - _spawn_t0) * 1000
                log.info(
                    f"CLAUDE_PROC_POOL_ACQUIRE | pid={proc.pid} "
                    f"prewarm_age_s={prewarm_age_s:.1f} "
                    f"acquire_ms={_spawn_ms:.0f} "
                    f"prompt_chars={_prompt_chars} "
                    f"sys_prompt_chars={_sys_prompt_chars} | {ctx()}"
                )
                # J4 (2026-05-14) — master-prompt-mandated alias event.
                # CLAUDE_PROC_POOL_ACQUIRE remains the canonical
                # diagnostic emission (richer context); CLAUDE_PREWARM_HIT
                # is the operator-facing grep-target documented in the
                # J4 spec. Both fire on the same condition.
                log.info(
                    f"CLAUDE_PREWARM_HIT | pid={proc.pid} "
                    f"prewarm_age_s={prewarm_age_s:.1f} | {ctx()}"
                )
                # H1 (2026-05-16) — CALL_A_REUSED_WORKER. Explicit
                # operator-facing event that a prewarmed worker was
                # successfully reused. CLAUDE_PREWARM_HIT remains the
                # canonical fire-on-hit log; this one is the spec
                # Rule 6 mandated grep target.
                log.info(
                    f"CALL_A_REUSED_WORKER | pid={proc.pid} "
                    f"prewarm_age_s={prewarm_age_s:.1f} "
                    f"acquire_ms={_spawn_ms:.0f} "
                    f"sys_prompt_chars={_sys_prompt_chars} | {ctx()}"
                )
                # Stash for CALL_A_PHASE_TIMING (emitted in send_message).
                self._last_pool_acquire_ms = float(_spawn_ms)
                self._last_cold_spawn_ms = 0.0
                self._last_was_pool_hit = True
                # H1/H2/H3 (2026-05-20) — stash pool age for the
                # augmented CALL_A_PHASE_TIMING. Lets the operator
                # plot first_token_ms vs pool age to see whether
                # ageing slots have any latency impact.
                self._last_pool_age_s = float(prewarm_age_s)
            else:
                # Cold spawn (legacy path, also the failure-safe fallback).
                # Binary mode (text=False) — we accumulate raw bytes and
                # decode once at the end with errors="replace" so a
                # partial multi-byte UTF-8 boundary at chunk EOF doesn't
                # crash the decode.
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False,
                    cwd=_PROJECT,
                    env=self._env,
                    preexec_fn=os.setsid,  # process group isolation
                )
                _spawn_ms = (time.time() - _spawn_t0) * 1000
                log.info(
                    f"CLAUDE_PROC_SPAWNED | pid={proc.pid} "
                    f"spawn_ms={_spawn_ms:.0f} "
                    f"prompt_chars={_prompt_chars} "
                    f"sys_prompt_chars={_sys_prompt_chars} "
                    f"cmd_argc={len(cmd)} pool_miss=true | {ctx()}"
                )
                # H1: stash for CALL_A_PHASE_TIMING.
                self._last_pool_acquire_ms = 0.0
                self._last_cold_spawn_ms = float(_spawn_ms)
                self._last_was_pool_hit = False
                # H1/H2/H3 (2026-05-20) — no pool hit means no age.
                self._last_pool_age_s = 0.0

            # Send prompt via stdin. Closing stdin signals EOF to the
            # CLI; if we don't close it the CLI will wait for more input
            # and never write a response.
            _prompt_write_t0 = time.time()
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.flush()
                proc.stdin.close()
            except (BrokenPipeError, OSError) as e:
                # Process likely already died (auth fail, etc.); fall
                # through to the read-and-drain path which will surface
                # the actual error from stderr.
                log.debug(
                    f"CLAUDE_STDIN_WRITE_FAIL | pid={proc.pid} "
                    f"err={str(e)[:120]} | {ctx()}"
                )
            # H1: stash prompt-write timing for CALL_A_PHASE_TIMING.
            self._last_prompt_write_ms = (time.time() - _prompt_write_t0) * 1000

            # T2-1: schedule background pre-spawn of the next worker
            # for this system_prompt. The replenishment runs in a daemon
            # thread, so subprocess.Popen overhead overlaps with this
            # call's API wait time (60-240 s) — completely hidden.
            self._proc_pool.replenish_async(system_prompt)
            # J4 (2026-05-14) — master-prompt-mandated event so the
            # operator sees that the pipeline is preparing the next
            # call's worker BEFORE the current call returns. Mirrors
            # the existing CLAUDE_PROC_PREWARM_OK that fires when the
            # spawn completes (background thread); CLAUDE_PIPELINE_NEXT
            # fires synchronously on the dispatch decision so the
            # log timeline reads pipeline-decision then spawn-result.
            log.info(
                f"CLAUDE_PIPELINE_NEXT | sys_prompt_chars={_sys_prompt_chars} "
                f"current_pid={proc.pid} | {ctx()}"
            )

            stdout, stderr = self._stream_subprocess_io(proc)

            if proc.returncode != 0:
                error_msg = (stderr.strip() or stdout.strip())[:200]

                # Classify: non-retryable errors (billing, auth, rate limit)
                error_lower = error_msg.lower()
                for pattern in _NON_RETRYABLE:
                    if pattern in error_lower:
                        raise _NonRetryableError(
                            f"claude CLI: {error_msg}"
                        )

                raise RuntimeError(
                    f"claude CLI exit code {proc.returncode}: {error_msg}"
                )

            response = stdout.strip()
            if not response:
                raise RuntimeError("claude CLI returned empty response")

            return response

        except subprocess.TimeoutExpired as _te:
            # The streaming reader raises this when EITHER the total
            # timeout (e.timeout == self.timeout) OR the first-byte
            # deadline (e.timeout == self._first_byte_timeout) elapses.
            # Both paths have already captured pre-kill diagnostics in
            # _stream_subprocess_io. Kill the process group to clean up.
            #
            # Phase 3 (Brain credentials): emit BRAIN_FAILURE_CASCADE
            # so the downstream STALE/safety_net signals from the
            # enforcer/watchdog have an attribution head. The reason
            # is heuristically classified from the most recent
            # credential probe — if the access token was inside the
            # margin at call entry, this hang is almost certainly a
            # credential boundary race; otherwise it is network/CLI.
            if proc is not None:
                self._kill_process_group(proc)
            ttl = self._get_credential_expiry_seconds()
            margin = self._credential_refresh_margin_seconds
            if ttl is not None and ttl < margin:
                cascade_reason = "credential_hang"
            else:
                cascade_reason = "network_or_cli"
            # P2-1 (2026-05-13): distinguish first-byte vs total timeout
            # in the cascade trace and in the RuntimeError message that
            # bubbles up to send_message's retry loop. The retry handler
            # detects "first-byte" in the message to emit
            # CLAUDE_RETRY_ON_FIRST_BYTE_TIMEOUT. We compare e.timeout to
            # self._first_byte_timeout (not self.timeout) because the
            # first-byte path passes a smaller window than the total
            # timeout; under == comparison this is unambiguous.
            _is_first_byte = (
                self._first_byte_timeout > 0.0
                and float(_te.timeout) == self._first_byte_timeout
                and self._first_byte_timeout < self.timeout
            )
            _cascade_kind = (
                "first_byte_deadline" if _is_first_byte else "total_timeout"
            )
            _duration_ms = int(float(_te.timeout) * 1000)
            # Phase 3 (Brain credentials) — include call_id so the operator
            # can correlate this cascade with the matching CLAUDE_CALL_START
            # log line and any downstream STALE/safety_net events that key
            # on the same call_id.
            log.error(
                f"BRAIN_FAILURE_CASCADE | call_id={self._call_id} "
                f"reason={cascade_reason} kind={_cascade_kind} "
                f"duration_ms={_duration_ms} "
                f"cred_ttl_s={int(ttl) if ttl is not None else 'unknown'} "
                f"cred_margin_s={int(margin)} "
                f"expected_cascade=enforcer_stale,watchdog_safety_net | {ctx()}"
            )
            # H1/H2/H3 (IMPLEMENT_FIVE_ISSUES_FIX.md Rule 7, 2026-05-20) —
            # BRAIN_CASCADE_ROOT_CAUSE consolidates everything the
            # operator needs to attribute the cascade to a specific
            # cause without manually grep-joining multiple log lines:
            #   - subprocess state + wchan (from _collect_stall_diagnostics)
            #   - TCP socket state (from _collect_tcp_state)
            #   - last successful first_token_ms (from prior CALL_A)
            #   - pool hit/miss counters (proves prewarm working or not)
            #   - prompt size at call entry
            #   - cred TTL vs margin (re-stated for self-contained event)
            # Best-effort: a failure here MUST NOT shadow the cascade
            # itself, so we swallow exceptions at DEBUG.
            try:
                _proc_diag = self._collect_stall_diagnostics(proc.pid) if proc is not None else ""
                _tcp_diag = self._collect_tcp_state(proc.pid) if proc is not None else {}
                _pool_obj = getattr(self, "_proc_pool", None)
                _pool_hits = int(getattr(_pool_obj, "_hit_count", 0)) if _pool_obj is not None else 0
                _pool_misses = int(getattr(_pool_obj, "_miss_count", 0)) if _pool_obj is not None else 0
                _pool_stats = {"hits": _pool_hits, "misses": _pool_misses}
                _last_ft = float(getattr(self, "_last_first_token_ms", 0.0) or 0.0)
                _last_pc = int(getattr(self, "_last_prompt_chars", 0) or 0)
                log.error(
                    f"BRAIN_CASCADE_ROOT_CAUSE | call_id={self._call_id} "
                    f"reason={cascade_reason} kind={_cascade_kind} "
                    f"proc_diag='{(_proc_diag or '').strip()[:80]}' "
                    f"tcp_established={_tcp_diag.get('established_count', 0)} "
                    f"tcp_api_socket='{_tcp_diag.get('api_socket', 'unknown')}' "
                    f"tcp_fd_count={_tcp_diag.get('fd_count', 0)} "
                    f"pool_hits={_pool_stats.get('hits', 0)} "
                    f"pool_misses={_pool_stats.get('misses', 0)} "
                    f"last_successful_first_token_ms={_last_ft:.0f} "
                    f"prompt_chars={_last_pc} "
                    f"cred_ttl_s={int(ttl) if ttl is not None else 'unknown'} "
                    f"cred_margin_s={int(margin)} | {ctx()}"
                )
            except Exception as _bcrc_e:
                log.debug(
                    f"BRAIN_CASCADE_ROOT_CAUSE_FAIL | "
                    f"call_id={self._call_id} err='{str(_bcrc_e)[:80]}' "
                    f"| {ctx()}"
                )
            if _is_first_byte:
                raise RuntimeError(
                    f"claude CLI first-byte deadline missed "
                    f"(no stdout in {self._first_byte_timeout:.0f}s)"
                )
            raise RuntimeError(
                f"claude CLI timed out after {self.timeout}s"
            )
        except (RuntimeError, _NonRetryableError):
            raise
        except Exception as e:
            if proc is not None and proc.poll() is None:
                self._kill_process_group(proc)
            raise RuntimeError(f"claude CLI subprocess error: {e}")

    def _stream_subprocess_io(
        self, proc: subprocess.Popen,
    ) -> tuple[str, str]:
        """Stream stdout+stderr from a running subprocess with stall watchdog.

        Polls in 50 ms intervals. On every iteration:

        1. If ``proc.poll()`` returns non-None, drain remaining bytes
           from both pipes and return.
        2. Otherwise read up to 4 KiB from each pipe non-blockingly,
           accumulating into bytearray buffers.
        3. Track ``last_stdout_time``. If silence exceeds
           ``_STALL_LOG_EVERY_S``, emit ``CLAUDE_PROC_STALL`` warning
           (rate-limited to one log per ``_STALL_LOG_EVERY_S`` window).
        4. If wall-clock exceeds ``self.timeout``, capture pre-kill
           diagnostics and raise ``subprocess.TimeoutExpired``. The
           caller owns the actual SIGTERM/SIGKILL.

        Returns:
            ``(stdout_text, stderr_text)`` decoded as UTF-8 with
            ``errors="replace"`` so a partial multi-byte boundary at the
            end of the stream cannot raise.
        """
        import fcntl  # platform: Linux only — same as os.setsid above

        # Set both read pipes non-blocking so read() returns
        # immediately if no data is buffered.
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            fd = stream.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        start = time.time()
        last_stdout_time = start
        last_stall_log = 0.0
        # T2-1 (2026-05-12): time-to-first-byte telemetry — separates
        # spawn + CLI bootup time (now masked by the prewarm pool) from
        # the actual API + inference time (the dominant 60-240 s
        # component). Operators read this to decide whether T2-1's
        # subprocess pool is sufficient or whether streaming-token mode
        # (Option D in the original spec) is needed next. Emitted
        # exactly once per call, on the first stdout byte.
        _first_token_logged = False
        # Phase 3 (Brain credentials) — progressive stall buckets. Each
        # threshold (60/120/240 s of silence) emits exactly ONE named
        # warning so operators see escalation, not a flat 60-s repeat.
        # The 120 s and 240 s buckets also capture /proc diagnostics
        # so root-cause data is on hand BEFORE the 300-s SIGKILL fires.
        # Phase 7 (post-Layer-1 fix) — buckets read from the constructor-
        # injected ``_stall_warn_buckets`` field. WorkerManager wires
        # settings.brain.stall_warn_buckets_seconds into the kwarg so
        # operators can tune without a code change. See
        # dev_notes/phase0_post_layer1_fixes/issue_7_stall_threshold.md.
        _stall_buckets = self._stall_warn_buckets
        _stall_bucket_fired: set[float] = set()

        def _try_read(stream) -> bytes:
            if stream is None:
                return b""
            try:
                chunk = stream.read1(4096)
            except (BlockingIOError, OSError):
                return b""
            return chunk or b""

        while True:
            now = time.time()
            elapsed = now - start

            # P2-1 (2026-05-13): first-byte deadline. If we have not
            # yet observed a single stdout byte AND we've been waiting
            # longer than the configured first-byte budget, abort. The
            # caller's retry loop spawns a fresh subprocess, which on
            # an unhealthy API path usually picks up a different
            # streaming session. Disabled when _first_byte_timeout <= 0.
            # NOTE: the check is keyed on ``_first_token_logged`` (set
            # to True on the first non-empty chunk_out below), not on
            # stdout_buf length — stderr-only output should NOT defeat
            # the deadline because we're explicitly measuring stdout
            # silence as the operator-visible "no progress" signal.
            if (
                self._first_byte_timeout > 0.0
                and not _first_token_logged
                and elapsed > self._first_byte_timeout
            ):
                self._capture_prekill_diagnostics(proc)
                _pc = getattr(self, "_last_prompt_chars", 0)
                _spc = getattr(self, "_last_sys_prompt_chars", 0)
                log.warning(
                    f"CLAUDE_PROC_FIRST_BYTE_DEADLINE | pid={proc.pid} "
                    f"elapsed_s={elapsed:.0f} "
                    f"deadline_s={self._first_byte_timeout:.0f} "
                    f"stdout_so_far={len(stdout_buf)} "
                    f"stderr_so_far={len(stderr_buf)} "
                    f"prompt_chars={_pc} sys_prompt_chars={_spc} | {ctx()}"
                )
                # Use ``timeout=_first_byte_timeout`` so the caller's
                # except-block can distinguish this from a full-timeout
                # expiry by comparing e.timeout to self.timeout.
                raise subprocess.TimeoutExpired(
                    cmd=str(proc.args),
                    timeout=self._first_byte_timeout,
                )

            if elapsed > self.timeout:
                # Capture diagnostics before raising; the caller will
                # send SIGTERM/SIGKILL via _kill_process_group.
                self._capture_prekill_diagnostics(proc)
                raise subprocess.TimeoutExpired(
                    cmd=str(proc.args),
                    timeout=self.timeout,
                )

            poll = proc.poll()

            # Read everything currently buffered from both pipes.
            chunk_out = _try_read(proc.stdout)
            if chunk_out:
                # T2-1: emit time-to-first-byte exactly once.
                if not _first_token_logged:
                    _first_token_logged = True
                    _first_token_ms = (now - start) * 1000
                    # H1 (2026-05-16) — stash for CALL_A_PHASE_TIMING.
                    self._last_first_token_ms = float(_first_token_ms)
                    log.info(
                        f"CLAUDE_PROC_FIRST_TOKEN_MS | pid={proc.pid} "
                        f"ms={_first_token_ms:.0f} "
                        f"first_chunk_bytes={len(chunk_out)} | {ctx()}"
                    )
                stdout_buf.extend(chunk_out)
                last_stdout_time = now
            chunk_err = _try_read(proc.stderr)
            if chunk_err:
                stderr_buf.extend(chunk_err)
                # stderr activity is also a sign of life — refresh the
                # silence clock so a noisy-stderr-but-silent-stdout call
                # doesn't false-trip the stall log.
                last_stdout_time = now

            if poll is not None:
                # Process has exited. Drain any remaining bytes (the
                # non-blocking read above may have left a few hundred
                # bytes if EOF coincided with the read).
                while True:
                    extra = _try_read(proc.stdout)
                    if not extra:
                        break
                    stdout_buf.extend(extra)
                while True:
                    extra = _try_read(proc.stderr)
                    if not extra:
                        break
                    stderr_buf.extend(extra)
                break

            # Stall detection. Phase 3 (Brain credentials) replaces the
            # legacy generic 60 s rate-limited warn with a progressive
            # ladder: each of the 60/120/240-second buckets fires
            # exactly once, escalating in severity, and the 120/240
            # buckets capture lightweight /proc diagnostics so the
            # operator sees what the subprocess is doing well before
            # SIGKILL.
            silence_s = now - last_stdout_time
            for threshold in _stall_buckets:
                if (
                    silence_s >= threshold
                    and threshold not in _stall_bucket_fired
                ):
                    _stall_bucket_fired.add(threshold)
                    extra = ""
                    if threshold >= 120.0:
                        extra = self._collect_stall_diagnostics(proc.pid)
                    # Phase 7 (post-Layer-1 fix). Graduated severity:
                    # the first bucket (typically 60 s) is informational
                    # — Claude subprocess startup latency is observed at
                    # 60-90 s in production, so this bucket fires on
                    # every successful call and should not alarm. The
                    # second bucket (120 s) means something is wrong;
                    # third (240 s+) is approaching SIGKILL territory.
                    if threshold <= 60.0:
                        log_fn = log.info
                    elif threshold <= 120.0:
                        log_fn = log.warning
                    else:
                        log_fn = log.error
                    # HIGH-4 fix (2026-05-09): include prompt sizes in
                    # the stall log so a single grep for stalls reveals
                    # the prompt complexity that triggered each one.
                    # Pre-fix the stall logs gave only pid/elapsed/buf
                    # — operators couldn't correlate stalls with prompt
                    # complexity without manually joining the SPAWNED
                    # log via tid + timestamp.
                    _pc = getattr(self, "_last_prompt_chars", 0)
                    _spc = getattr(self, "_last_sys_prompt_chars", 0)
                    log_fn(
                        f"CLAUDE_PROC_STALL_{int(threshold)}S | pid={proc.pid} "
                        f"elapsed={silence_s:.0f}s "
                        f"stdout_so_far={len(stdout_buf)} "
                        f"timeout_in_s={self.timeout - elapsed:.0f} "
                        f"prompt_chars={_pc} sys_prompt_chars={_spc}{extra} | {ctx()}"
                    )
                    # H1/H2/H3 (2026-05-20) IMPLEMENT_FIVE_ISSUES_FIX
                    # Rule 7 — CLAUDE_STALL_DIAGNOSTIC with TCP socket
                    # state attribution at 120 s+ buckets. Confirms
                    # whether the ``wchan=ep_poll`` signature is on
                    # the Anthropic API socket (state=01 ESTABLISHED,
                    # remote :443) vs something else. Best-effort —
                    # _collect_tcp_state never raises.
                    if threshold >= 120.0:
                        try:
                            _tcp = self._collect_tcp_state(proc.pid)
                            log_fn(
                                f"CLAUDE_STALL_DIAGNOSTIC | "
                                f"pid={proc.pid} "
                                f"threshold_s={int(threshold)} "
                                f"silence_s={silence_s:.0f} "
                                f"established_count={_tcp.get('established_count', 0)} "
                                f"fd_count={_tcp.get('fd_count', 0)} "
                                f"api_socket='{_tcp.get('api_socket', 'unknown')}' "
                                f"prompt_chars={_pc} | {ctx()}"
                            )
                        except Exception as _e:
                            log.debug(
                                f"CLAUDE_STALL_DIAGNOSTIC_FAIL | "
                                f"pid={proc.pid} err='{str(_e)[:80]}' "
                                f"| {ctx()}"
                            )

            # Legacy 60 s rate-limited generic warn — preserved for
            # backwards compatibility with operator dashboards/grep
            # filters that already key on CLAUDE_PROC_STALL. Fires
            # AFTER the named buckets so the named events appear first
            # in the log stream.
            #
            # Phase 7 (post-Layer-1 fix) — demoted from WARNING to DEBUG.
            # The named bucket events at 60/120/240 s carry the same
            # information with proper graduation; the legacy generic
            # tag was effectively dead-code at WARNING level (operators
            # learned to ignore "still stalled at 180 s" lines that
            # fired on every successful call). DEBUG-level keeps the
            # tag available for forensic deep-dives without polluting
            # the steady-state log stream.
            if (
                silence_s >= self._STALL_LOG_EVERY_S
                and now - last_stall_log >= self._STALL_LOG_EVERY_S
            ):
                log.debug(
                    f"CLAUDE_PROC_STALL | pid={proc.pid} "
                    f"silence_s={silence_s:.0f} "
                    f"stdout_so_far={len(stdout_buf)} "
                    f"timeout_in_s={self.timeout - elapsed:.0f} | {ctx()}"
                )
                last_stall_log = now

            time.sleep(self._SUBPROC_POLL_INTERVAL_S)

        return (
            stdout_buf.decode("utf-8", errors="replace"),
            stderr_buf.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _collect_stall_diagnostics(pid: int) -> str:
        """Best-effort ``/proc/{pid}`` snapshot for a stalled subprocess.

        Phase 3 (Brain credentials). Called from the stall watcher at
        120 s+ silence buckets and again from ``_capture_prekill_
        diagnostics``. Always returns a string suffix; never raises.

        Returns:
            ``" state=R wchan=do_select"``-style suffix safe to append
            to a log line. Empty string if every probe failed.
        """
        bits: list[str] = []
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("State:"):
                        bits.append(f"state={line.split(':', 1)[1].strip().split()[0]}")
                        break
        except Exception:
            pass
        try:
            with open(f"/proc/{pid}/wchan", "r", encoding="utf-8") as fh:
                wchan = fh.read().strip()
                if wchan:
                    bits.append(f"wchan={wchan[:32]}")
        except Exception:
            pass
        return (" " + " ".join(bits)) if bits else ""

    @staticmethod
    def _collect_tcp_state(pid: int) -> dict[str, str | int]:
        """Best-effort decode of ``/proc/{pid}/net/tcp`` and
        ``/proc/{pid}/net/tcp6`` for stall-time socket inspection.

        H1/H2/H3 (2026-05-20) — when CLAUDE_PROC_STALL fires at 120s+
        with ``wchan=ep_poll`` (subprocess sleeping in epoll_wait), the
        operator wants to know whether the subprocess is genuinely
        waiting on its Anthropic API socket vs spinning on something
        else. This helper returns a dict with the count of established
        sockets, a sample remote-IP:port for the most-likely API
        socket (heuristic: state=01 ESTABLISHED + remote port 443),
        and the open file descriptor count.

        TCP state codes (Linux kernel):
            01=ESTABLISHED 02=SYN_SENT 03=SYN_RECV 04=FIN_WAIT1
            05=FIN_WAIT2  06=TIME_WAIT 07=CLOSE   08=CLOSE_WAIT
            09=LAST_ACK   0A=LISTEN    0B=CLOSING

        Returns:
            Dict with keys: established_count (int), api_socket
            (string like "1.2.3.4:443 state=01" or "none"), fd_count
            (int). Missing or unreadable probes yield 0 / "unknown".
        """
        out: dict[str, str | int] = {
            "established_count": 0,
            "api_socket": "none",
            "fd_count": 0,
        }
        # Count open file descriptors — useful for confirming the
        # subprocess hasn't leaked or exhausted FDs.
        try:
            import os
            out["fd_count"] = len(os.listdir(f"/proc/{pid}/fd"))
        except Exception:
            pass

        def _decode_hex_addr(hex_addr: str) -> str:
            """``1234567A:01BB`` (little-endian hex IP : hex port) ->
            ``122.86.52.18:443``. Returns "?" on any decode failure."""
            try:
                ip_hex, port_hex = hex_addr.split(":")
                # IPv4: 8 hex chars, little-endian byte order.
                if len(ip_hex) == 8:
                    ip_bytes = bytes.fromhex(ip_hex)
                    ip = ".".join(str(b) for b in reversed(ip_bytes))
                else:
                    # IPv6 — keep as opaque hex prefix
                    ip = f"ipv6:{ip_hex[:16]}"
                port = int(port_hex, 16)
                return f"{ip}:{port}"
            except Exception:
                return "?"

        for proc_file in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"):
            try:
                with open(proc_file, "r", encoding="utf-8") as fh:
                    next(fh, None)  # skip header
                    for line in fh:
                        parts = line.split()
                        if len(parts) < 4:
                            continue
                        local = parts[1]
                        remote = parts[2]
                        state = parts[3]
                        if state == "01":  # ESTABLISHED
                            out["established_count"] = int(out["established_count"]) + 1
                            if out["api_socket"] == "none":
                                # Heuristic: prefer remote port 443 (HTTPS / API)
                                if remote.endswith(":01BB"):  # 0x01BB = 443
                                    out["api_socket"] = (
                                        f"{_decode_hex_addr(remote)} "
                                        f"local={_decode_hex_addr(local)} state=01"
                                    )
                                else:
                                    out["api_socket"] = (
                                        f"{_decode_hex_addr(remote)} "
                                        f"local={_decode_hex_addr(local)} "
                                        f"state={state}"
                                    )
            except Exception:
                continue

        return out

        return (
            stdout_buf.decode("utf-8", errors="replace"),
            stderr_buf.decode("utf-8", errors="replace"),
        )

    def _capture_prekill_diagnostics(
        self, proc: subprocess.Popen,
    ) -> None:
        """Capture /proc/<pid>/{status,wchan} before the kill.

        Best-effort. Any failure is swallowed (logged at DEBUG) — the
        timeout path still proceeds to kill regardless.
        """
        pid = proc.pid
        try:
            with open(f"/proc/{pid}/status", "r") as fh:
                # Pull just the lines that name the state and resources;
                # the full file is ~50 lines and noisy.
                wanted_keys = (
                    "Name:", "State:", "Threads:", "VmRSS:",
                    "VmSize:", "voluntary_ctxt_switches:",
                )
                lines = []
                for ln in fh:
                    if ln.startswith(wanted_keys):
                        lines.append(ln.strip())
                status_summary = "; ".join(lines)
        except OSError:
            status_summary = "<unreadable>"

        try:
            with open(f"/proc/{pid}/wchan", "r") as fh:
                wchan = fh.read().strip()[:80] or "<idle>"
        except OSError:
            wchan = "<unreadable>"

        log.warning(
            f"CLAUDE_PROC_PREKILL | pid={pid} wchan={wchan} "
            f"status='{status_summary[:300]}' | {ctx()}"
        )

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen) -> None:
        """Kill entire process group: SIGTERM -> wait 5s -> SIGKILL.

        Ensures no orphaned child processes from the Claude CLI subprocess.
        """
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            return  # already dead
        try:
            os.killpg(pgid, signal_mod.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal_mod.SIGKILL)
                proc.wait(timeout=3)
            log.warning(f"CLAUDE_PROC_KILLED | pid={proc.pid}")
        except (ProcessLookupError, OSError):
            pass  # race: process died between getpgid and kill
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass

    def _cleanup_orphaned_processes(self) -> None:
        """Kill orphaned claude CLI processes before starting a new call.

        Safety net: catches edge cases where kill failed or a process
        leaked from a crash. Under normal operation, _kill_process_group
        handles cleanup on timeout.

        T2-1 (2026-05-12): preserves PIDs registered in
        ``self._proc_pool`` so pre-spawned workers awaiting a prompt are
        not reaped before use. Pool workers match the same
        ``claude.*-p`` pgrep pattern (intentionally — they ARE Claude
        CLI processes), so without this exclusion they would be killed
        by the very next call's pre-flight cleanup.
        """
        try:
            result = subprocess.run(
                ["pgrep", "-f", "claude.*-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return
            my_pid = os.getpid()
            # T2-1: pool's pre-spawned workers must not be reaped.
            pool_pids = (
                self._proc_pool.known_pids()
                if hasattr(self, "_proc_pool")
                else set()
            )
            killed = 0
            for pid_str in result.stdout.strip().split("\n"):
                try:
                    pid = int(pid_str.strip())
                    if pid == my_pid or pid in pool_pids:
                        continue
                    os.kill(pid, signal_mod.SIGKILL)
                    killed += 1
                except (ValueError, ProcessLookupError, PermissionError):
                    continue
            if killed:
                log.warning(
                    f"CLAUDE_ORPHAN_CLEANUP | killed={killed} "
                    f"preserved_pool_pids={len(pool_pids)}"
                )
        except Exception:
            pass

    @staticmethod
    def _find_claude() -> str:
        """Find the claude binary, preferring the native installer.

        Resolution order puts the native install (``~/.local/bin/claude``,
        which Anthropic's auto-updater keeps current) FIRST, then a PATH
        lookup, then the legacy npm-global / system locations. Every
        candidate is resolved with ``os.path.realpath`` and the *resolved
        target* is verified to be an existing, executable file before the
        candidate is accepted — a candidate whose symlink dangles is
        skipped, not returned.

        We return the candidate path itself (not the realpath) when it is a
        symlink, so each subprocess exec re-follows the link to whatever
        version is live — surviving CLI version bumps that swap the
        underlying binary.

        Incident 2026-05-29: Claude Code migrated from the npm-global
        install (``/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js``)
        to the native installer. The old ``cli.js`` was deleted underneath a
        long-running worker that had cached it via
        ``shutil.which("claude") -> realpath`` at startup, so every brain
        call failed with ``[Errno 2] No such file or directory``. Preferring
        the native install and validating the resolved target prevents a
        dead path from being cached again.
        """

        def _usable(candidate: str) -> str:
            """Return ``candidate`` if it resolves to a runnable file, else ''."""
            if not candidate:
                return ""
            real = os.path.realpath(candidate)
            if os.path.isfile(real) and os.access(real, os.X_OK):
                return candidate
            return ""

        candidates = [
            # Native installer first — the supported, auto-updated install.
            f"{_HOME}/.local/bin/claude",
            # Whatever is on PATH (systemd PATH may not include ~/.local/bin).
            shutil.which("claude") or "",
            # Legacy npm-global / system locations.
            "/usr/local/bin/claude",
            "/usr/bin/claude",
            f"{_HOME}/.npm-global/bin/claude",
        ]

        # nvm installations (any node version)
        candidates.extend(glob.glob(f"{_HOME}/.nvm/versions/node/*/bin/claude"))

        for candidate in candidates:
            resolved = _usable(candidate)
            if resolved:
                return resolved

        return ""

    @staticmethod
    def _build_env() -> dict:
        """Build explicit environment for the subprocess.

        Guarantees HOME, PATH, LANG, and LC_ALL are set correctly
        regardless of whether we're running from a terminal or a
        systemd service (which has a minimal environment).

        This is defense-in-depth: the systemd service file ALSO sets
        these, but we don't rely on that being correct.
        """
        env = os.environ.copy()

        # CRITICAL: Remove ANTHROPIC_API_KEY so the CLI uses OAuth
        # credentials (~/.claude/.credentials.json) instead of API billing.
        # The .env file sets this key for the old ClaudeClient (SDK), but
        # when present, the CLI prefers it over OAuth — and if the key has
        # no credit balance, every call fails with "Credit balance too low".
        env.pop("ANTHROPIC_API_KEY", None)

        # HOME: Required by Claude CLI to find ~/.claude/.credentials.json
        env["HOME"] = _HOME

        # PATH: Must include directories for claude binary, node, and python venv.
        # ~/.local/bin first so the native Claude installer is reachable even
        # when the systemd unit's PATH omits it (incident 2026-05-29).
        required_dirs = [
            f"{_HOME}/.local/bin",
            f"{_PROJECT}/.venv/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        current_path = env.get("PATH", "")
        for d in required_dirs:
            if d not in current_path:
                current_path = f"{d}:{current_path}" if current_path else d
        env["PATH"] = current_path

        # LANG/LC_ALL: Ensure UTF-8 encoding for subprocess I/O
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")

        return env


class _NonRetryableError(Exception):
    """Raised for errors where retrying is pointless (billing, auth, rate limit)."""


class ClaudeCodeCostTracker:
    """Drop-in replacement for CostTracker. Always returns True (CLI is free).

    Maintains interface compatibility so no other code needs changes.
    """

    def __init__(self, daily_budget: float = 999.0) -> None:
        self.daily_budget = daily_budget
        self._calls_today = 0

    def can_afford_call(self, estimated_cost: float = 0.0) -> bool:
        return True  # always free

    def record_call(self, input_tokens: int = 0, output_tokens: int = 0) -> float:
        self._calls_today += 1
        return 0.0

    def get_daily_spend(self) -> float:
        return 0.0

    def get_remaining_budget(self) -> float:
        return self.daily_budget
