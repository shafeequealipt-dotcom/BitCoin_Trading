# Phase 0 — Issue 4: Brain CLI Credential Hangs Investigation

**Date:** 2026-04-27
**Brief reference:** `IMPLEMENT_FIVE_CRITICAL_FIXES_PROFESSIONAL.md` § Issue 4, Phase 3

## A — The mechanism

`src/brain/claude_code_client.py` is currently 1233 LOC. Verified line numbers against the live file:

| Element | Line | Notes |
|---|---|---|
| `call()` invokes pre-flight | 243 | `self._ensure_credentials_fresh(min_remaining_seconds=300.0)` — 300 s margin hardcoded at the call site |
| `_ensure_credentials_fresh` defined | 566-611 | Default arg also `300.0`; signature accepts override |
| In-method refresh attempt | 598 | `ok = self._try_token_refresh()` |
| `_try_token_refresh` defined | 612-693 | Synchronous urllib (`urlopen(req, timeout=30)`); writes credentials file via `_CREDENTIAL_PATH.write_text(...)`; no retry |
| Subprocess wrapper enters thread | 815 | `await loop.run_in_executor(None, self._subprocess_call, ...)` — runs on the default thread pool, not the event loop |
| Subprocess spawn | 858-867 | `subprocess.Popen` with `preexec_fn=os.setsid` for process-group isolation |
| Stall constant | 821 | `_STALL_LOG_EVERY_S = 60.0` |
| Polling interval | 824 | `_SUBPROC_POLL_INTERVAL_S = 0.05` |
| Stall detector loop | 1023-1037 | Emits a single rate-limited `CLAUDE_PROC_STALL` warning every 60 s of silence |
| Pre-kill diagnostics path | exists (referenced) | `_capture_prekill_diagnostics(proc)` called before SIGKILL on timeout |
| Process group kill | 1081 | `_kill_process_group(proc)` |
| Retry classification | 337 | `refresh_ok = self._try_token_refresh()` (in-call recovery) |

Failure mode chain when credentials approach expiry:
1. Pre-flight fires `_ensure_credentials_fresh(300.0)`. If remaining > 300 s, no action taken.
2. Subprocess spawns. The Claude CLI inside it discovers credentials are about to expire mid-call, attempts its own refresh, hangs (network slow, OAuth slow, or DNS issues). It writes nothing to stdout/stderr.
3. Stall watcher emits one `CLAUDE_PROC_STALL` after 60 s, then again at 120 s, etc., but the log message is the same on each emission — operators can't tell at a glance that 60/120/240 s have elapsed.
4. `claude_cli_timeout_seconds = 300` reached. `_capture_prekill_diagnostics` runs. SIGKILL.
5. `RuntimeError("claude CLI timed out after 300s")` raised.
6. `_consecutive_failures` increments at `:337` site / equivalents.
7. Strategist heartbeat goes STALE.
8. Enforcer reads `claude_client._last_call_attempt_time` / `_last_call_response_time` (`src/strategies/performance_enforcer.py:399-409`). `max(attempt, response)` becomes the alive_at timestamp; if `time.time() - alive_at > 600`, returns False.
9. PositionWatchdog (`src/workers/position_watchdog.py:336-345`) reads the same timestamps and `_consecutive_failures`; flips `_watchdog_mode = "safety_net"` if `_consecutive_failures >= 3` OR elapsed > 600.
10. Watchdog stays in safety_net for ~110 s of degraded mode.

The **root defect** is that the pre-flight margin (300 s) is shorter than the credential window during which a refresh from inside the subprocess could itself stall, AND `_try_token_refresh` is single-attempt synchronous urllib with no retry — so a transient OAuth blip leaves the system in the same expiry-boundary window the next call hits.

## B — The dependencies

- **Strategist** (`src/brain/strategist.py`) calls `claude_code_client.call(...)` every `strategic_interval = 150 s` (config). Failure attributes are `_consecutive_failures`, `_last_call_attempt_time`, `_last_call_response_time` — all read by other components.
- **Performance enforcer** (`src/strategies/performance_enforcer.py:380-409`) consumes `_last_call_attempt_time` / `_last_call_response_time` at `:399-400` and decides STALE/healthy at `:409`.
- **Position watchdog** (`src/workers/position_watchdog.py:293-349`) consumes the same heartbeat plus `_consecutive_failures`. Mode flip at `:341, :345`.
- **Telegram dashboard** displays heartbeat status to the operator; sees STALE when the flip propagates.
- **Credentials file** (`~/.claude/.credentials.json`) is the OAuth token store — read/write inside `_try_token_refresh`. Schema: top-level keys depend on the OAuth provider; we will not echo their contents.

## C — The constraints

- The subprocess is invoked via Anthropic's Claude CLI; its internals are out of our control.
- Subprocess is launched in a thread pool executor (line 815), not asyncio. File I/O inside `_try_token_refresh` blocks that thread; if the pool saturates, other subprocess calls queue.
- `~/.claude/.credentials.json` writes must remain atomic-enough — current implementation writes via `Path.write_text` (not atomic). Any change must preserve compatibility with the Claude CLI's reader.
- The 300 s timeout matches `[brain] claude_cli_timeout_seconds`. Increasing it would mask the problem; decreasing it would amplify failures. Stay at 300.
- Cascade plumbing (enforcer + watchdog) consumes `_last_call_attempt_time/_last_call_response_time` directly. Our changes must keep those attributes honest.

## D — The fix candidates (per brief Phase 3)

All four sub-fixes from the brief apply:

1. **Configurable refresh margin (10 min default)** — replace hardcoded 300 s with `[brain] credential_refresh_margin_seconds = 600`. Default 600 captures the long tail of refresh latency.
2. **Multi-attempt refresh with backoff** — 3 attempts (1 s / 3 s / 7 s) inside `_try_token_refresh`. On final failure when remaining_seconds < margin: emit `CRED_REFRESH_FAILED_BLOCKING` AND raise `CredentialRefreshError`, aborting the doomed subprocess spawn at the boundary.
3. **Progressive stall detection** — replace single 60 s rate-limited warning with three named events at 60 / 120 / 240 s. At 120 s capture `/proc/{pid}/status`. At 240 s capture wchan + `py-spy dump` (if available, best-effort).
4. **Pre-kill diagnostics enrichment** — single `CLAUDE_PROC_PREKILL` log with `pid`, `state`, `wchan`, `py_spy`, `elapsed`, last 1 KB of stdout/stderr. Best-effort; never raise from inside the diagnostic path.
5. **Cascade attribution** — on RuntimeError exit, emit `BRAIN_FAILURE_CASCADE | reason=credential_hang|network|other duration_ms=... call_id=...`. Determine reason from pre-kill artefacts. Enforcer/watchdog correlate via the call_id.

User implicitly accepted all five (default Phase 3 plan).

## E — The observability gap

Currently emitted:
- `CLAUDE_PREFLIGHT_REFRESH` at `:595` (best-effort outcome)
- `CLAUDE_PROC_STALL` rate-limited every 60 s of silence (line ~1026-1027) — generic, no progressive labels
- `_capture_prekill_diagnostics` referenced — log exists but is generic
- Various `BRAIN_TIMEOUT`-class messages on RuntimeError

Missing per brief:
- `CRED_REFRESH_PREFLIGHT_OK | margin=Xs remaining=Ys`
- `CRED_REFRESH_ATTEMPT | attempt=N status=ok|fail err=...`
- `CRED_REFRESH_FAILED_BLOCKING | remaining=Ys`
- `CLAUDE_PROC_STALL_60S | pid=... elapsed=60s`
- `CLAUDE_PROC_STALL_120S | pid=... elapsed=120s status=... `
- `CLAUDE_PROC_STALL_240S | pid=... elapsed=240s wchan=... py_spy=...`
- `CLAUDE_PROC_PREKILL | pid=... state=... wchan=... py_spy=... elapsed=... stdout_tail=... stderr_tail=...`
- `BRAIN_FAILURE_CASCADE | reason=... duration_ms=... call_id=...`

Phase 3 adds all of these.

## F — The verification approach

| Trial | Procedure | Pass criterion |
|---|---|---|
| 3.1 | Backdate `~/.claude/.credentials.json` `expires_at` to within 600 s; trigger a brain call (in a controlled environment) | Pre-flight fires; refresh succeeds; call proceeds; no cascade |
| 3.2 | Block Anthropic API DNS for 5 min | `CLAUDE_PROC_STALL_60S/120S/240S` fire in order; `CLAUDE_PROC_PREKILL` fires at 300 s; `BRAIN_FAILURE_CASCADE` fires |
| 3.3 | After 3.2, inspect enforcer / watchdog | They emit STALE / safety_net carrying the cascade `call_id` for correlation |
| 3.4 | 24-hour reliability run | Brain success rate > 95 %; 0 cascades attributed to `credential_hang`; any cascade is `network` or `other` |

Edge cases:
- Pre-flight refresh succeeds but the new token expires within 60 s (server returned a short-lived token) — cascade may still fire; treat as `network/other`, not credential_hang. We do NOT escalate on `expires_in` < 60 s in this Phase; document as a follow-up.
- Slow disk write of `_CREDENTIAL_PATH.write_text` — out of scope unless it manifests in observability.

## G — The rollback path

Each of the four commits reverts independently. Specifically:
1. Configurable refresh margin: revert restores the 300 s hardcoded behaviour. The new escalation (raise `CredentialRefreshError` on blocking failure) is in the same commit; revert restores the existing behaviour where the call proceeds even after a failed refresh.
2. Progressive stall detection: revert restores the single rate-limited warning.
3. Pre-kill diagnostics: revert removes the enriched log; pre-existing `_capture_prekill_diagnostics` behaviour remains.
4. Cascade attribution: revert removes the cascade event and call_id correlation.

Recovery time: seconds (`git revert` + worker restart).
