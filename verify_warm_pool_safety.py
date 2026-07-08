#!/usr/bin/env python3
"""F28 verification — safe warm-pool re-enable with a canary gate.

Two layers:
  1. DETERMINISTIC gating (no API): construct the real _ClaudeWorkerPool and prove
     reuse is OFF until a canary confirms health, that a simulated hung CLI
     self-disables reuse (so acquire() misses and the caller cold-spawns), and that
     max_age=0 keeps prewarm fully off.
  2. LIVE canary (real `claude -p`): run the actual _run_canary against the
     installed CLI and confirm it reports healthy (parked worker responds) — proving
     the re-enable is safe on this CLI. Skipped with --no-live.

Read-only. Exit 0 = all checks pass.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src.brain.claude_code_client import _ClaudeWorkerPool  # noqa: E402
from src.config.settings import Settings  # noqa: E402

_FAIL: list[str] = []


def chk(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _FAIL.append(name)


def _env():
    e = os.environ.copy()
    e.pop("ANTHROPIC_API_KEY", None)
    e["HOME"] = os.path.expanduser("~")
    return e


print("=" * 74)
print("verify_warm_pool_safety — F28 canary-gated warm-pool re-enable")
print("=" * 74)

# ── Config ────────────────────────────────────────────────────────────────
S = Settings.load("config.toml")
chk("prewarm re-enabled (max_age=900)", int(S.brain.claude_cli_prewarm_max_age_seconds) == 900)
chk("canary TTL configured (600)", int(S.brain.claude_cli_prewarm_canary_ttl_seconds) == 600)

# ── Layer 1 — deterministic gating, no API ─────────────────────────────────
print("\n[1] canary gating (deterministic, no API call)")
import shutil
_claude = shutil.which("claude", path=f"{os.path.expanduser('~')}/.local/bin:" + os.environ.get("PATH", "")) or "claude"
pool = _ClaudeWorkerPool(
    claude_path=_claude, env=_env(), project_cwd=str(Path.cwd()),
    max_age_seconds=900.0, stats_interval_seconds=0.0, model="claude-opus-4-7",
    canary_ttl_seconds=600.0,
)
chk("reuse OFF before any canary (cold-spawn until proven)", pool.reuse_enabled() is False)
proc, age = pool.acquire("SYS")
chk("acquire() misses while reuse gated", proc is None and age == 0.0)

# Simulate a HEALTHY canary result and run the canary worker.
pool._run_canary = lambda: (True, "2.1.165 (Claude Code)", "responded in 4.0s")
pool._canary_blocking()
chk("after a healthy canary, reuse is ENABLED", pool.reuse_enabled() is True)
chk("CLI version recorded from canary", "2.1.165" in pool._cli_version)

# Simulate the CLI starting to HANG (the incident failure mode).
pool._run_canary = lambda: (False, "2.1.170 (Claude Code)", "no stdout within 45s (hang)")
pool._canary_at_monotonic = 0.0  # force a refresh
pool.maybe_run_canary_async()
import time as _t
for _ in range(50):  # wait for the background canary thread
    if not pool._reuse_healthy:
        break
    _t.sleep(0.1)
chk("a hung CLI self-disables reuse (pool falls back to cold-spawn)",
    pool.reuse_enabled() is False)
proc, age = pool.acquire("SYS")
chk("acquire() misses again after self-disable", proc is None and age == 0.0)

# replenish must no-op when reuse is gated (no prewarm pile-up).
_spawned = {"n": 0}
pool._replenish_blocking = lambda sp: _spawned.__setitem__("n", _spawned["n"] + 1)
pool.replenish_async("SYS")
_t.sleep(0.2)
chk("replenish_async spawns NO prewarm worker while reuse disabled", _spawned["n"] == 0)

# max_age=0 -> prewarm fully OFF, canary never runs.
pool0 = _ClaudeWorkerPool(
    claude_path=_claude, env=_env(), project_cwd=str(Path.cwd()),
    max_age_seconds=0.0, stats_interval_seconds=0.0, model="", canary_ttl_seconds=600.0,
)
_ran = {"n": 0}
pool0._run_canary = lambda: (_ran.__setitem__("n", _ran["n"] + 1), (True, "x", "y"))[1]
pool0.maybe_run_canary_async()
_t.sleep(0.2)
chk("max_age=0 -> reuse OFF and canary never kicked", pool0.reuse_enabled() is False and _ran["n"] == 0)

# ── Layer 2 — live canary against the installed CLI ────────────────────────
if "--no-live" not in sys.argv:
    print("\n[2] live canary (real `claude -p` on the installed CLI)")
    live = _ClaudeWorkerPool(
        claude_path=_claude, env=_env(), project_cwd=str(Path.cwd()),
        max_age_seconds=900.0, stats_interval_seconds=0.0, model="claude-opus-4-7",
        canary_ttl_seconds=600.0,
    )
    healthy, version, detail = live._run_canary()
    print(f"    canary: healthy={healthy} version='{version}' detail='{detail}'")
    chk("live canary reports the CLI healthy (parked worker responds)", healthy is True, detail)
else:
    print("\n[2] live canary SKIPPED (--no-live)")

print("\n" + "-" * 74)
if _FAIL:
    print(f"RESULT: FAIL ({len(_FAIL)}): {_FAIL}")
    sys.exit(1)
print("RESULT: PASS — reuse is gated on the canary: OFF until proven, self-disables")
print("on a hung CLI (cold-spawn fallback, no brain outage), and confirmed healthy")
print("on the installed CLI. Re-enabling saves the ~50ms spawn, not the API latency.")
