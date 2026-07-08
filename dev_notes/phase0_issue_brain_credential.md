# Phase 0 — Issue Investigation: Brain CLI Credential Lifecycle (Issues #3, #8, #9)

## Section A — The mechanism

### A.1 Subprocess spawn

**File:** `src/brain/claude_code_client.py:722-746` (`_subprocess_call`)

`subprocess.Popen` is invoked with:
- `stdin=PIPE`, `stdout=PIPE`, `stderr=PIPE` (text mode)
- `preexec_fn=os.setsid` for process-group isolation
- `cwd=_PROJECT`, explicit env dict
- Command: `/usr/bin/claude -p --output-format text [--system-prompt text]`
- Prompt delivered via stdin

**Timeout:** default 90s (per `__init__` at line 83). Spec's "300s timeout" is incorrect for the current code.

**Kill path:** `_kill_process_group` (lines 816-841) — SIGTERM → 5s wait → SIGKILL → `proc.wait(timeout=3)`.

### A.2 Credential expiry detection

**File:** `src/brain/claude_code_client.py:648-715` (`_validate_setup`)

At startup (and periodically):
- Reads `~/.claude/.credentials.json` `expiresAt` (line 677)
- Logs `Claude session EXPIRED at {exp}` if already expired (line 685)
- Logs `Claude credentials expire in {mins} minutes` if < 1 hour remaining (line 691)

**No automatic pre-flight refresh before each subprocess call.** Refresh is only attempted on call failure (Layer 1 strategy).

### A.3 OAuth refresh — already exists in 3 layers

**Layer 1 — On-demand refresh** (`claude_code_client.py:517-597`, `_try_token_refresh`):
- POST to `https://claude.ai/v1/oauth/token` with `grant_type=refresh_token`
- Hardcoded client_id `9d1c250a-e61b-44d9-88ed-5944d1962f5e` (line 62)
- 30s timeout (line 560)
- Writes updated `accessToken` / `expiresAt` / `refreshToken` back to credentials file (lines 575-579)
- Logs `CLAUDE_REFRESH_OK` on success (line 582)

**Layer 2 — Hot-reload** (`claude_code_client.py:505-515`, `_credentials_changed`):
- Detects mtime changes on credentials file (allows out-of-band `claude login` without restart)
- Resets `_auth_failed` flag (lines 174-178, 347-351)

**Layer 3 — Exponential backoff + Telegram alert** (`claude_code_client.py:358-390`):
- Backoff schedule `[300, 600, 1200, 2400, 3600]` seconds (line 65)
- Telegram alert sent once per cycle

**Phase 6 does NOT need to build refresh from scratch.** It needs to **invoke Layer 1 pre-emptively** (before the next subprocess spawn) when expiry is < 5 minutes away.

### A.4 LATENT BUG — `_last_response_time` doesn't exist

**Verified:** `claude_code_client.py` only sets `self._last_call_time` (lines 97, 236, 321). The string `_last_response_time` does NOT appear in the file at all.

But:
- `src/workers/position_watchdog.py:323` reads `getattr(self.claude_client, "_last_response_time", 0.0) or 0.0`
- The fallback to `0.0` silently masks the missing attribute. `max(call_t, 0.0) = call_t`, so the watchdog only ever sees call-start time.
- `src/strategies/performance_enforcer.py:380-386` (`_check_heartbeat`) reads only `_last_call_time` (line 384) — same issue: a slow but successful call updates `_last_call_time` at the *start*, so the elapsed-since-call check passes even when the call itself is silently hung.

**Consequence:** the watchdog/enforcer cannot detect "call started 30s ago, no response yet" — they only know "no new call started in N seconds." The 110s safety_net cascade described in Issue #8/#9 is the correct *visible* response, but the watchdog logic that would normally catch a hung-not-stopped call is broken.

### A.5 Why subprocess hangs silently

The Claude CLI is a Node.js process. When OAuth fails non-fatally (e.g., token refresh in progress, network hiccup), it can stall in any of:
- DNS resolution to `api.anthropic.com`
- HTTP TLS handshake
- Auth token verification round-trip
- Streaming response wait

All four can keep stdout silent for the full 90s timeout. There is **no stall heartbeat** — current code waits for the full timeout before declaring failure.

## Section B — The dependencies

| Component | File:Line | Read |
|---|---|---|
| `position_watchdog._determine_mode` | position_watchdog.py:286-334 | `_last_call_time`, `_last_response_time` (BROKEN), `_consecutive_failures` |
| `performance_enforcer._check_heartbeat` | performance_enforcer.py:380-386 | `_last_call_time` (only) |
| Telegram alerter | claude_code_client.py:368-381 | Triggered by Layer 3 backoff |

## Section C — The constraints

- **Do not touch the 3-layer OAuth refresh** — already works for the cases it's designed for.
- **Do not change the 90s timeout** without measurement — too short risks killing valid slow calls; too long widens the cascade window.
- **Do not change the credentials file format** — the refresh code writes specific fields; the CLI reads them.
- **Pre-flight refresh must be reentrant** — concurrent brain calls (rare but possible) shouldn't trigger duplicate refreshes. Use a lock or check `expiresAt` after acquiring.

## Section D — The fix candidates

### D.1 Add `_last_response_time` attribute (mandatory — fixes the latent bug)

In `claude_code_client.py:97`: `self._last_response_time = 0.0`. Set it at the success path of `_subprocess_call` (after stdout collection).

### D.2 Pre-flight credential check (Phase 6.2)

New method `_ensure_credentials_fresh(min_remaining_seconds=300)`:
- Read `~/.claude/.credentials.json` `expiresAt`
- If less than 5 minutes remaining: trigger `_try_token_refresh`
- Lock around it to prevent concurrent refreshes
- Logs `CLAUDE_PREFLIGHT_REFRESH_OK` on success, `CLAUDE_PREFLIGHT_FAIL` on failure
- Called from `_subprocess_call` before Popen

### D.3 Stall detection during subprocess (Phase 6.3)

Convert `proc.communicate(timeout=N)` to chunked stdout read with watchdog task:
- Reader task drains stdout line-by-line, updates `self._last_stdout_time`
- Main coroutine sleeps in 60s slices, checks elapsed-since-last-stdout
- 60s silence → `CLAUDE_PROC_STALL` WARNING
- 120s silence → second STALL log, escalate
- At full timeout: kill with pre-kill diagnostics (D.4)

### D.4 Pre-kill diagnostics (Phase 6.4)

Before SIGTERM:
- `/proc/{pid}/status` (Threads, State, RSS, VmSize)
- `/proc/{pid}/wchan` (kernel block point)
- Optional `py-spy dump --pid {pid}` if installed
- Log as `CLAUDE_PROC_PREKILL`

### D.5 Cascade correlation event (Phase 6.5)

When a brain call fails: emit `WORKER_DEGRADATION_CASCADE | trigger=brain_call_fail call_id=N expect=enforcer_stale,watchdog_safety_net duration_est=110s`.

### D.6 Watchdog/enforcer harmonization (Phase 6.6)

- Drop the `getattr` fallback once `_last_response_time` exists.
- Document: `_last_call_time` = request start; `_last_response_time` = last successful return. Use `max(...)` to detect "no activity at all" vs "call in flight."

## Verified citations

| Claim | File:Line |
|---|---|
| Subprocess spawn | `src/brain/claude_code_client.py:722-746` |
| Default timeout 90s | `src/brain/claude_code_client.py:83` (constructor default) |
| Kill path | `src/brain/claude_code_client.py:816-841` |
| Credential expiry warning | `src/brain/claude_code_client.py:691` |
| Token refresh | `src/brain/claude_code_client.py:517-597` |
| Hot-reload | `src/brain/claude_code_client.py:505-515` |
| Backoff schedule | `src/brain/claude_code_client.py:65, 358-390` |
| `_last_call_time` set at request start | `src/brain/claude_code_client.py:97, 236, 321` |
| `_last_response_time` does NOT exist | `src/brain/claude_code_client.py` (entire file — verified via grep) |
| Watchdog reads non-existent attr | `src/workers/position_watchdog.py:323` |
| Enforcer reads only call_time | `src/strategies/performance_enforcer.py:380-386` |
