# H2 — Claude CLI subprocess manager (`ClaudeCodeClient`)

Collected: 2026-05-02. Logs window: 2026-05-01 12:00 UTC → 2026-05-02 11:48 UTC.

## File overview

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/claude_code_client.py`
- Lines: 1465
- Last modified: 2026-04-27 20:44:41 UTC
- Classes: `ClaudeCodeClient` (line 73), `_NonRetryableError` (line 1440), `ClaudeCodeCostTracker` (line 1444).
- Public surface: `send_message(prompt, system_prompt="", max_tokens=4096) -> str` (line 187), `extract_json(response) -> dict` (line 505), `get_stats() -> dict` (line 558), `set_alert_callback(callback)` (line 175).
- Private: `_get_cred_mtime`, `_credentials_changed`, `_get_credential_expiry_seconds`, `_ensure_credentials_fresh`, `_try_token_refresh_with_retries`, `_try_token_refresh`, `_parse_usage_reset`, `_log_diagnostics`, `_validate_setup`, `_execute_cli`, `_subprocess_call`, `_stream_subprocess_io`, `_collect_stall_diagnostics`, `_capture_prekill_diagnostics`, `_kill_process_group`, `_cleanup_orphaned_processes`, `_find_claude`, `_build_env`.

## OAuth credentials

- Path constant: `_CREDENTIAL_PATH = Path(_HOME) / ".claude" / ".credentials.json"` (line 63). `_HOME = os.environ.get("HOME") or str(Path.home())` (line 62).
- Token URL constant: `_OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"` (line 66).
- Client ID constant: `_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"` (line 67).
- TTL read: `_get_credential_expiry_seconds` (line 590) reads `creds["claudeAiOauth"]["expiresAt"]` (ms) and returns `(expires_ms / 1000.0) - now_s`. Negative ⇒ already expired.
- Pre-flight refresh: `_ensure_credentials_fresh(min_remaining_seconds=None)` (line 611) called from `send_message` at line 277. Default margin = `credential_refresh_margin_seconds` (default 600 s, configurable from `BrainSettings`).
- Refresh path: `_try_token_refresh_with_retries` (line 683) — backoff ladder `[1.0, 3.0, 7.0]`. Underlying single attempt is `_try_token_refresh` (line 723).
- urllib request site: `urllib.request.urlopen(req, timeout=30)` at line 766; payload built at lines 749-753 with `Content-Type: application/json`, `User-Agent: claude-code/1.0.0 (python-client)`, `Accept: application/json`. Method POST.
- On success: writes new `accessToken`, `expiresAt` (ms = now + `expires_in` * 1000), and rotated `refreshToken` back to `_CREDENTIAL_PATH` (lines 781-786) and re-syncs `_cred_mtime`.
- "credential hang" failure mode origin: pre-Phase-3 single-attempt 30-s urllib call at 723-803 — when it raised, the caller logged then proceeded to spawn the CLI subprocess with an already-expired token, which would then hang for the full subprocess timeout (300 s) waiting on Anthropic's auth-error round trip via stdout. Phase 3 fix made `_ensure_credentials_fresh` raise `CredentialRefreshError` (line 673) inside the margin instead of silently proceeding, killing the call ~immediately and emitting `CRED_REFRESH_FAILED_BLOCKING | mins_left=... margin_min=... action=abort_call` (line 668-672). When refresh succeeds, log emits `CLAUDE_PREFLIGHT_REFRESH_OK | new_mins_left=...`. Real example from the window:
  ```
  2026-05-02 11:22:51.487 CLAUDE_PREFLIGHT_REFRESH | reason=expires_in mins_left=-82.7 threshold_min=10.0 attempts=3
  2026-05-02 11:22:51.487 CRED_REFRESH_ATTEMPT | attempt=1/3
  2026-05-02 11:22:51.840 CLAUDE_REFRESH_OK | new_token_expires_in=28800s | credentials updated
  2026-05-02 11:22:51.842 CLAUDE_PREFLIGHT_REFRESH_OK | new_mins_left=480.0
  ```

- 3-layer auth recovery in `send_message` exception block (lines 368-452):
  - Layer 1: `_try_token_refresh()` immediate retry (line 371-405).
  - Layer 2: `_credentials_changed()` hot-reload — operator ran `claude login` (line 409-417).
  - Layer 3: exponential backoff via `_AUTH_BACKOFF_SCHEDULE = [300, 600, 1200, 2400, 3600]` (line 70) plus optional Telegram alert via `_alert_callback` (line 430-443).

## Subprocess spawn

- Spawn site: `_subprocess_call` (line 937). `subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, text=False, cwd=_PROJECT, env=self._env, preexec_fn=os.setsid)` (lines 969-978). `text=False` so chunked-stdout streaming yields raw bytes, decoded once at end with `errors="replace"`.
- `_execute_cli` (line 923) runs the synchronous `_subprocess_call` via `loop.run_in_executor(None, ...)`. So although `send_message` is `async`, the actual CLI call runs in the default thread-pool executor, NOT `asyncio.create_subprocess_exec`.
- Command line: `cmd = [self._claude_path, "-p", "--output-format", "text"]` (line 958); `["--system-prompt", system_prompt]` appended if truthy (line 959-960).
- Stdin write: `proc.stdin.write(prompt.encode("utf-8"))` (line 989), then `proc.stdin.flush()` and `proc.stdin.close()` to deliver EOF (lines 990-991). Comment at 985-987: "if we don't close it the CLI will wait for more input and never write a response".
- Stdout/stderr read: `_stream_subprocess_io` (line 1066). Pipes set non-blocking with `fcntl(F_SETFL, ... | O_NONBLOCK)` (lines 1093-1098). Read loop polls every `_SUBPROC_POLL_INTERVAL_S = 0.05` (50 ms) at line 935, accumulating into `bytearray` buffers via `stream.read1(4096)` (line 1122).
- Timeout policy: deadline `self.timeout` (default `90`, but `WorkerManager` passes `300` from `BrainSettings`; CALL_START log at 11:22 confirms `timeout=300s`). Loop checks `if elapsed > self.timeout: self._capture_prekill_diagnostics(proc); raise subprocess.TimeoutExpired(...)` (lines 1131-1138).
- Kill mechanism: `_kill_process_group` (line 1313). On timeout the caller in `_subprocess_call` calls `_kill_process_group(proc)` (line 1037), which `os.killpg(pgid, SIGTERM)` then waits 5 s then `os.killpg(pgid, SIGKILL)` (lines 1322-1328). Process-group isolation comes from `preexec_fn=os.setsid` at spawn (line 977). After kill, emits `CLAUDE_PROC_KILLED | pid=...` (line 1329) and `BRAIN_FAILURE_CASCADE | call_id=... reason=credential_hang|network_or_cli ...` (lines 1048-1055). Pre-call orphan sweep: `_cleanup_orphaned_processes` (line 1340) runs `pgrep -f "claude.*-p"` and `os.kill(pid, SIGKILL)` for survivors.

## CLAUDE_PROC_STALL — where, threshold, levels

- Fires from `_stream_subprocess_io` (line 1179). Threshold buckets configurable via constructor kwarg `stall_warn_buckets_seconds`; default `(60.0, 120.0, 240.0)` (line 114). `WorkerManager` wires `settings.brain.stall_warn_buckets_seconds` into the kwarg (per docstring at lines 110-114).
- Each bucket fires exactly once per call (set `_stall_bucket_fired`, line 1116). Severity selected at lines 1195-1200:
  - `threshold ≤ 60.0` → `log.info` (informational — Claude CLI typically silent for ~60-90 s on the happy path).
  - `threshold ≤ 120.0` → `log.warning`.
  - `threshold > 120.0` → `log.error`.
- Tag pattern: `f"CLAUDE_PROC_STALL_{int(threshold)}S | pid={pid} elapsed={silence_s:.0f}s stdout_so_far={len(stdout_buf)} timeout_in_s={...}{extra}"` (lines 1201-1206). For 120 s and 240 s buckets, `extra` carries `state=R` and `wchan=...` from `_collect_stall_diagnostics` (line 1242).
- Legacy generic `CLAUDE_PROC_STALL` tag (line 1226) preserved for back-compat dashboards but demoted to DEBUG (Phase-7 post-Layer-1 fix per comment 1214-1221) and rate-limited to once per `_STALL_LOG_EVERY_S = 60.0` (line 932).

### 5 brain calls with time-to-first-stdout proxy

(The CLI does not emit a "first stdout byte" event; instead `CLAUDE_PROC_STALL_60S` confirms ≥60 s of stdout silence, and `CLAUDE_PROC_STALL_120S` confirms ≥120 s. Wallclock from `CLAUDE_PROC_SPAWNED` to `CLAUDE_CALL_OK` is the full call duration.)

```
pid=17370  spawn=06:16:58.576  STALL_60S(+60s)  STALL_120S(+120s)  OK=+133.3s   el_reported=133327ms
pid=17450  spawn=06:24:12.890  STALL_60S(+60s)  STALL_120S(+120s)  OK=+127.7s   el_reported=127756ms
pid=17968  spawn=06:28:50.658  STALL_60S(+60s)                     OK=+75.1s    el_reported=75140ms
pid=18045  spawn=06:32:35.942  (process restart cuts the trace before OK)
pid=932    spawn=11:22:51.870  STALL_60S(+60s)                     OK=+69.5s    el_reported=69537ms
```

Interpretation: every call breaches 60 s of stdout silence (so `STALL_60S` fires on every call); roughly half also breach 120 s. None reached 240 s in the window.

## Error handling and retry

- Retry loop: lines 281-498 in `send_message`. `for attempt in range(self.max_retries + 1):` — default `max_retries = 2` (line 84) so 3 attempts total. WorkerManager-passed value (per ctor): unchanged at 2 in production logs (`attempt=1/3` everywhere).
- `_NonRetryableError` (line 1440): raised from inside `_subprocess_call` at line 1010 when stderr/stdout text contains any pattern in `_NON_RETRYABLE` (line 48-58: `credit balance`, `authentication`, `unauthorized`, `api key`, `account suspended`, `quota exceeded`, `rate limit`, `out of extra usage`, `extra usage`). Caught at line 313 and routed to specific exception types: `ClaudeAPIError` for usage exhaustion (line 364-366) or generic non-retryable (line 453-455); `AuthenticationError` for auth-class messages (line 444-452).
- Generic timeout/transient error path (line 457-498): retries with backoff. Sleep between attempts at line 489-498:
  - `is_timeout = "timed out" in str(e).lower()` (line 488).
  - `sleep_s = (attempt+1) * self.retry_timeout_backoff_base` if timeout else `2 ** attempt` (lines 489-493). `retry_timeout_backoff_base` default 30 (line 86), but Phase-2 commentary says manager.py passes `BrainSettings.claude_cli_retry_timeout_backoff_base_seconds` (default 10 per docstring) for a 10/20/30 ladder.
- Fail-loud final emit: line 500 — `CLAUDE_CALL_FAIL | call_id=... err='...' attempts=...` then `raise BrainError(...)` at 501.
- `_consecutive_failures` increments on each non-OK path; `_adaptive_interval = min(min_interval * (2 ** _consecutive_failures), 30.0)` (lines 316-318, 459-461). On success: reset to `self.min_interval = 2.0` (line 298, default at line 85).
- Auth backoff state (lines 146-148): `_auth_failed`, `_auth_backoff_until`, `_auth_failure_count`, `_auth_alert_sent`. Schedule at line 70.
- Usage backoff state (lines 152-155): `_usage_exhausted`, `_usage_backoff_until`, `_usage_alert_sent`. `_parse_usage_reset` (line 805) parses `"resets 6pm (UTC)"`-style patterns from error text.
- Pre-call gates inside `send_message`: auth backoff (215-229), usage backoff (232-243), rate-limit (245-251 — `await asyncio.sleep` until `_adaptive_interval` elapsed since `_last_call_time`).

## Cost tracking

- Class: `ClaudeCodeCostTracker` (line 1444). `can_afford_call(self, estimated_cost=0.0) -> bool: return True` (line 1454-1455 — comment "always free"). `record_call(input_tokens=0, output_tokens=0) -> float: self._calls_today += 1; return 0.0` (line 1457-1459). `get_daily_spend() -> float: return 0.0` (line 1461). `get_remaining_budget() -> float: return self.daily_budget` (line 1464).
- Tokens / dollars: NOT tracked. Comment at lines 1444-1448: "Drop-in replacement for CostTracker. Always returns True (CLI is free)."
- 24h cost data: NOT FOUND — no `COST_TRACK` events in /tmp/h_collect/brain_24h.log. Searched: `COST_TRACK`, `cost_today`, `cost_usd=`. The CLI uses the operator's Max subscription (OAuth) and reports zero cost in the data lake (`claude_decisions.cost_usd` column not populated by this client). The legacy `src/brain/cost_tracker.py:CostTracker` class (line 11, used by Brain v2 / SDK path) does have full pricing logic ($3/M input, $15/M output) but is not invoked when `ClaudeCodeClient` is the strategist's claude client (which it is in production — `claude_code_client.py` is the active path).

## Last 50 CLAUDE_CALL_START → CLAUDE_CALL_DONE pairs

The "DONE" event is `CLAUDE_CALL_OK` (line 305). The 24h window contains 50+ matched pairs.

`elapsed_ms` distribution from `el=` field on `CLAUDE_CALL_OK`:

```
N=52
min  = 26,731 ms
max  = 169,393 ms
mean = 105,725 ms
p50  = 109,652 ms
p75  = 130,492 ms
p90  = 142,973 ms
p95  = 160,919 ms
p99  = 169,393 ms
```

Last 10 pairs (representative):

```
05:53:27.376 CALL_START call_id=42  in=17085 sys=8985 timeout=300s
05:55:24.104 CALL_OK    call_id=42  attempt=1/3 el=115887ms out=2492 calls=42
05:57:54.131 CALL_START call_id=43  in=...
05:55:24 / 05:57:54 — same did=d-1777701207375 / 1777701474112

call_id=43  el=  varies (CALL B small)
call_id=44  el=26731ms  out=1245    ← fastest CALL B in window
call_id=45  el=82496ms  out=2014
call_id=46  el=84792ms  out=665
call_id=47  el=118837ms out=1850
call_id=48  el=78839ms  out=578
call_id=49  el=133327ms out=2112
call_id=50  el=127756ms out=2128
call_id=51  el=75140ms  out=397
call_id=1   el=69537ms  out=2439    ← post-restart, _call_id was reset to 0
```

Failure rate (CALL_FAIL OR PARSE_FAIL OR STRAT_*_FAIL events / total brain calls): 1 / 54 = **1.9 %** in window. The single failure was the parse-failure on did=d-1777698545524 at 05:10:56 (Claude returned a non-JSON refusal). All 52 `CLAUDE_CALL_OK` events used `attempt=1/3` — no retries fired in the window.

CALL_START sample (full line, including `sys=` and `timeout=` fields):

```
2026-05-02 11:22:51.486 | INFO | src.brain.claude_code_client:send_message:262 | CLAUDE_CALL_START | call_id=1 in=4077 sys=8985 timeout=300s hash=e0558dedb7cd | did=d-1777720966952
```

The `sys=8985` matches the size of `TRADE_SYSTEM_PROMPT` (strategist.py:65) since `_has_urgent_concerns=False` and `surface_briefing_fields=False` paths apply. `timeout=300s` confirms the WorkerManager-passed value (overrides default 90 s in the constructor).
