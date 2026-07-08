# Issue 7 — CLAUDE_PROC_STALL fires WARNING on every brain call

**Status:** PARTIAL — graduated 60/120/240s buckets exist; legacy 60s WARN still fires deterministically.
**Tier:** 3 (log noise).
**Source observation:** `data/logs/brain.log` 06:33:36, 06:40:28, 06:47:20 — `CLAUDE_PROC_STALL_60S` and `CLAUDE_PROC_STALL` both at WARNING with `silence_s=60 stdout_so_far=0`.

## A. Mechanism

`src/brain/claude_code_client.py` runs Claude as a subprocess and tails stdout. There are two independent stall-detection paths active today:

1. **Bucket-based escalation (lines 1076-1159).** `_stall_buckets = (60.0, 120.0, 240.0)`. On each pass, if elapsed since last stdout exceeds the next un-fired bucket, fire `CLAUDE_PROC_STALL_{60|120|240}S` with optional `/proc/{pid}` diagnostics for 120s+. Each tag fires at most once per call.
2. **Legacy rate-limited emission (line 903 + 1167-1175).** `_STALL_LOG_EVERY_S = 60.0`. On each pass past 60s of silence, fire `CLAUDE_PROC_STALL | pid=... silence_s=60 stdout_so_far=0 timeout_in_s=240` at WARNING, throttled to one per 60s window.

When a brain call has the typical ~70s startup latency before first stdout (Claude subprocess loads tools + model), BOTH paths fire WARNINGs near the 60s mark. Result: every brain call emits at least 2 WARNING-level stall events even though the call ultimately completes successfully.

Live evidence (3 calls in 14 min):
- 06:33:36.169 `CLAUDE_PROC_STALL pid=4479 silence_s=60` (legacy)
- 06:40:28.040 `CLAUDE_PROC_STALL_60S pid=5477 elapsed=60s` (bucket) + 06:40:28.041 `CLAUDE_PROC_STALL pid=5477` (legacy)
- 06:47:20.069 same dual emission

These calls did not actually stall — they reached completion. The 60s WARNINGs train operators to ignore stall warnings, defeating the purpose of having one.

The hard timeout is separate (line 1097: `if elapsed > self.timeout`, default 240s in observed logs but documented as 300s in the prompt) — orthogonal to the warning.

## B. Dependencies

- `src/brain/strategist.py` does not gate on stall events.
- `src/workers/watchdog_worker.py` to be checked — if it reacts to `CLAUDE_PROC_STALL` WARNING level, downgrading would change its behavior. Pre-check: `grep -rn 'CLAUDE_PROC_STALL' src/`.
- Operator Telegram alerts likely grep WARNING+ events in brain.log.

## C. Constraints

- Must preserve a meaningful "really stalled" alert path (operators need to know if Claude wedged for real).
- The 240s `kill` (or 300s in prompt) is the hard timeout; warning levels below should be informational, not alarming.
- Cannot change the public `CLAUDE_PROC_STALL_{60|120|240}S` tag name — too many ad-hoc dashboards may grep them.
- The legacy `CLAUDE_PROC_STALL` (no suffix) tag is also widely greppable — preserve emission but consider level.

## D. Fix candidates

1. **Tunable thresholds + downgrade legacy 60s WARN to INFO (chosen).**
   - Move `(60.0, 120.0, 240.0)` to `[brain.stall_detection]` config: `warn_threshold_sec`, `escalate_threshold_sec`, `kill_threshold_sec`. Defaults preserved.
   - 60s bucket emits at INFO ("informational; first stall window"). 120s bucket at WARNING. 240s bucket at ERROR.
   - Drop the legacy `CLAUDE_PROC_STALL` (no-suffix) emission entirely OR keep it at INFO (matching the bucket) — investigation will resolve based on whether anything downstream greps the un-suffixed tag.
2. Raise the first bucket to 120s. Rejected — 60s is still useful as an "informational watermark"; the issue is level, not threshold.
3. Suppress entirely. Rejected — losses early-warning value when subprocess actually stalls.

## E. Observability gap

- No tunability today; constants are hardcoded.
- No level discrimination: operators see "WARNING ... stall" and don't know severity.

## F. Verification approach

- Synthetic harness: spawn a fake subprocess that emits stdout after 70s. Assert `CLAUDE_PROC_STALL_60S` at INFO; no WARNING+ events.
- Synthetic harness: 130s silence then stdout. Assert `CLAUDE_PROC_STALL_120S` at WARNING with `/proc` diagnostics.
- Synthetic harness: full 300s silence. Assert `CLAUDE_PROC_STALL_240S` at ERROR + kill.
- Live trial: 10 successful brain calls → zero `CLAUDE_PROC_STALL*` at WARNING+.
- Real stall (DNS block during call): bucket progression visible in correct levels.

## G. Rollback path

Single-commit revert of `src/brain/claude_code_client.py` and `config.toml`. No state migration. Rollback restores the dual-WARNING noise but otherwise equivalent.
