# P2-1 Phase 1 — Claude CLI Subprocess Stall Investigation

## TL;DR

The 30-minute Telegram-stuck pattern is dominated by **Claude API streaming latency on large CALL_A prompts**, not subprocess spawn time. T2-1's prewarm pool is shipped but its `max_age_seconds=60s` is mismatched to the 5–10 min CALL_A cadence, so the pool hit rate is **0 of 1424 calls today**. The 240 s stall path remains active and is compounded by a second, more serious symptom: **the 300 s asyncio timeout fires up to 112 minutes late** under load, indicating executor-thread starvation that turns a single slow API call into a multi-hour worker hang. This is the actual root cause of the operator's "30-minute stuck" observation.

## 1. Confirmed Today's Evidence (post-T2-1, post-restart)

Source: `data/logs/brain.log` (current, post-T2-1 merge at 2026-05-12 17:24 UTC).

### Today's CALL_A elapsed distribution

```
count=19
min=  0 ms
p50= 165 380 ms  (2.75 min)
p90= 4 937 326 ms (82 min!)
p99= 6 691 557 ms (111 min!)
max= 6 691 557 ms (111 min)
```

### CLAUDE_PROC_STALL events today

```
24 × CLAUDE_PROC_STALL_60S
15 × CLAUDE_PROC_STALL_120S
 1 × CLAUDE_PROC_STALL_240S  (at 09:32:49)
```

### Pool effectiveness today

```
CLAUDE_PROC_PREWARM_OK    : 39   (replenish thread fires)
CLAUDE_PROC_POOL_ACQUIRE  :  0   ZERO pool hits
CLAUDE_PROC_SPAWNED       : 1424 all calls cold-spawn (pool_miss=true)
```

### The 240 s stall at 09:32:49 — full trace

```
09:28:49.408  STRAT_CALL_A         chars=18042
09:28:49.408  CLAUDE_CALL_START    call_id=17 in=18042 sys=7244 timeout=300s
09:28:49.445  CLAUDE_PROC_SPAWNED  pid=17701 spawn_ms=23 pool_miss=true
09:28:49.476  CLAUDE_PROC_PREWARM_OK pid=17703   (replenish — but already cold-spawned)
09:30:09.534  CLAUDE_PROC_STALL_60S  elapsed=80s stdout_so_far=0
09:30:49.463  CLAUDE_PROC_STALL_120S elapsed=120s stdout_so_far=0 state=S wchan=ep_poll
09:32:49.456  CLAUDE_PROC_STALL_240S elapsed=240s stdout_so_far=0 state=S wchan=ep_poll
09:32:56.388  CLAUDE_PROC_FIRST_TOKEN_MS  ms=246941 first_chunk_bytes=3147
09:32:56.891  CLAUDE_CALL_OK       call_id=17 attempt=1/3 el=247469ms
```

Key takeaways:

- `pool_miss=true` — the pool had no fresh worker for this CALL_A sys_prompt.
- spawn_ms=23 — cold spawn was fast.
- `state=S wchan=ep_poll` — the subprocess is alive, the parent has nothing to read.
- **The first byte from the Claude API arrived 246.9 seconds after spawn.** This is not a subprocess problem — it is an API streaming-latency problem.
- The 60-s bucket fired at elapsed=80 s — the polling loop ran 20 s late.

### The 112-minute "300s timeout" — the operator's actual stuck symptom

```
10:24:13.143  CLAUDE_CALL_START   call_id=28 in=3884 sys=1783 timeout=300s   (small CALL_B)
10:24:48.542  CLAUDE_CALL_OK      el=35386ms                              ← normal CALL_B
10:30:53.654  CLAUDE_CALL_START   call_id=30 in=3844 sys=1783 timeout=300s   (CALL_B again)
12:22:59.791  CLAUDE_CALL_TIMEOUT call_id=30 attempt=1/3 timeout=300s
                          err='claude CLI timed out after 300s'
17:31:13.376  STRAT_CALL_A_START   ← strategist resumes after 7-hour hiatus
```

- Wall-clock time from CALL_START to TIMEOUT: **112 minutes** for a "300s timeout".
- Total wait for the next CALL_A start: **7 hours** (10:27 → 17:31).
- During this window, the executor thread was blocked / descheduled so the `elapsed > self.timeout` check in `_stream_subprocess_io` could not fire on schedule.

### The 82-min and 111-min CALL_A elapsed values are multi-retry chains

The 4 937 326 ms (82 min) STRAT_CALL_A_END at 09:13:38 is the strategist's view of total wall time. The underlying CLAUDE_CALL retry log shows one or more 112-min-style false-timeouts feeding the retry loop, with the eventual successful retry capping the chain.

## 2. Code-Path Verification (read end-to-end)

`src/brain/claude_code_client.py` reviewed in the relevant ranges. Key facts:

### a. async invocation chain

- `send_message(prompt, system_prompt) -> str`  (line 460, async)
- → `await self._execute_cli(prompt, system_prompt)` (line 564)
- → `await loop.run_in_executor(None, self._subprocess_call, prompt, system_prompt)` (line 1212)

So `_subprocess_call` runs in the default `ThreadPoolExecutor` (typically `min(32, os.cpu_count() + 4)` threads). Heavy CPU on the event loop is therefore NOT the direct cause; **executor-thread starvation is.** Possible upstream causes: GIL contention from other threads, kernel descheduling under memory/CPU pressure, or another sync call holding the GIL.

### b. _subprocess_call → _stream_subprocess_io (the polling loop)

`_stream_subprocess_io` (line 1391, sync) runs the loop at line 1460:

```
while True:
    now = time.time()
    elapsed = now - start
    if elapsed > self.timeout:      # 300 s default
        self._capture_prekill_diagnostics(proc)
        raise subprocess.TimeoutExpired(...)
    poll = proc.poll()
    chunk_out = _try_read(proc.stdout)   # non-blocking
    if chunk_out:
        if not _first_token_logged:
            log.info(f"CLAUDE_PROC_FIRST_TOKEN_MS | ...")
        stdout_buf.extend(chunk_out)
        last_stdout_time = now
    chunk_err = _try_read(proc.stderr)
    ...
    silence_s = now - last_stdout_time
    for threshold in _stall_buckets:
        if silence_s >= threshold and threshold not in _stall_bucket_fired:
            ...
            log_fn(f"CLAUDE_PROC_STALL_{int(threshold)}S | ...")
    ...
    time.sleep(self._SUBPROC_POLL_INTERVAL_S)   # 0.05 s
```

`time.sleep(0.05)` is fine — it doesn't block the event loop because it's in an executor thread. **But the cadence of this loop drifts wildly under load:** STALL_60S firing at elapsed=80 s means the loop took 20 s longer than expected to reach that iteration. Under extreme contention the timeout check can be delayed by hours.

### c. Prewarm pool (`_ClaudeWorkerPool`)

- `acquire(sys_prompt)` (line 211): hash sys_prompt → look up in `self._slots` dict.
- Freshness check: `age > self._max_age_seconds` → dispose, return `None`.
- Default `_max_age_seconds = 60.0` (T2-1 design).

CALL_A schedules every 5-10 min. By the next CALL_A, the prewarmed worker is 5-10 min old → always disposed before reuse → `pool_miss=true`. CALL_B can theoretically hit, but CALL_A and CALL_B keep their own pool slots via `_hash_sys_prompt` (different sys_prompts: 7244 vs 1783 chars).

The pool is effectively dead-weight at the current cadence. 0/1424 hit rate today confirms this.

### d. Retry loop

`send_message` line 549 has `for attempt in range(self.max_retries + 1)`. Each timeout triggers a retry with a fresh subprocess. The 4 937 326 ms (82 min) CALL_A elapsed = 1+ stalled timeouts × (112-min each, in the worst case) + eventual success. With `max_retries=2` default, one bad call can hang for hours.

### e. _capture_prekill_diagnostics

Line 1627. Reads `/proc/<pid>/status` and `/proc/<pid>/wchan`. Best-effort, never raises. Captures `state=S wchan=ep_poll` showing the subprocess is alive and blocked on epoll (waiting for API tokens).

## 3. Root-Cause Hypotheses (Evidence-Ranked)

### H1 — Claude API inference latency dominates on large CALL_A prompts (VERY HIGH confidence)

Evidence:
- `FIRST_TOKEN_MS = 246941` (4.1 min) on 18042-char CALL_A.
- `FIRST_TOKEN_MS = 76496` (1.3 min) on 2443-char CALL_B same day.
- All stalled procs in `state=S wchan=ep_poll` — alive, waiting for I/O.
- spawn_ms = 23 across all calls — spawn is never the bottleneck.

Implication: **The fix cannot be CLI-side alone.** Either the prompt must shrink (out of scope per the operator's prompt) or the streaming must be parallelized / aborted earlier.

### H2 — Executor-thread starvation under load (VERY HIGH confidence)

Evidence:
- 300 s timeout fired at 112 min wall time (call_id=30 at 10:30→12:22).
- 60 s stall bucket fired at elapsed=80 s — loop 20 s late.
- The 7-hour CALL_A gap from 10:27 → 17:31 directly correlates with this single hung CALL_B.

Implication: Even if API streaming is slow, the parent process should NOT have its enforcement deadline drift this far. Possible amplifiers:
- GIL contention from 20+ workers doing CPU/IO at the same time.
- Memory pressure causing kernel-level descheduling.
- Long `time.sleep(0.05)` drift under thread saturation.

This is the root cause of the operator-visible Telegram stuck — once one CALL_B hangs for 112 min instead of 300 s, the strategist halts everything downstream of that loop.

### H3 — Prewarm pool is mismatched to call cadence (HIGH confidence)

Evidence:
- `max_age_seconds = 60.0` (T2-1 default).
- CALL_A cadence 5–10 min.
- 0 pool hits over 1424 calls today.

Implication: The pool incurs cost (background spawn ×39 today) without delivering benefit. Should be either reconfigured or removed.

### H4 — Streaming-first-byte is unbounded — no separate budget (MEDIUM confidence as a fix, HIGH as a defect)

Evidence:
- Only the total timeout is enforced (300 s).
- There's no "abort if no byte arrived in X s" check.
- An API call that's going to take 4+ min to first byte will burn the entire 300 s waiting.

Implication: A separate `first_byte_timeout` (e.g., 90 s) could abort and retry on a fresh subprocess before the full 300 s budget is spent.

## 4. The Pool's Visible Misbehavior

- Pool init at line 434-438; 1 worker per sys_prompt hash.
- Replenish (line 297-300) emits `CLAUDE_PROC_PREWARM_OK` 39× today.
- Acquire (line 211) NEVER returns a worker today (POOL_ACQUIRE = 0).
- Therefore every replenish is wasted spawn work.

The replenish thread also might be contributing to the executor-thread starvation observed in H2 — it spawns a real subprocess in the background, which competes for the same GIL and OS resources.

## 5. Aim Preservation Check

The prompt's Hard Rule 8: aggressive opportunity exploitation, not capital preservation. Implications for fix candidates:

- Adding a streaming-first-byte timeout makes the system *more responsive*. Preserves aim.
- Pipelining CALL_B in parallel with CALL_A's slow path increases throughput. Preserves aim.
- Reducing the retry count or backoff aggressiveness would slow recovery — would *reduce* aggressive exploitation. Reject.
- Switching to Anthropic SDK with extended-thinking models could increase prompt latency further. Caution required.

## 6. Operator Options (for Phase 2 report)

### Option A — Pool tuning (LOW effort, LOW benefit)

- Raise `_max_age_seconds` from 60 → 900 s.
- Pre-warm both CALL_A and CALL_B sys_prompts at boot.
- Effort: 20-40 LOC. Risk: LOW. Benefit: saves ~50 ms spawn time per call. Does NOT address API latency or thread starvation.

### Option B — First-byte timeout (MEDIUM effort, HIGH benefit) — RECOMMENDED FOUNDATION

- Add a separate "no first byte in N seconds" deadline (e.g., 90 s), independent of the total timeout.
- On first-byte deadline miss, capture diagnostics, kill subprocess, retry on a fresh process.
- Effort: 50-100 LOC, 5-10 unit tests. Risk: LOW. Bounds the per-call stall blast radius from 300 s → 90 s, with up to 3 retries = 270 s worst case. Caps the visible stuck window cleanly.

### Option C — Decouple brain from worker event loop (HIGH effort, VERY HIGH benefit)

- Run the brain subprocess and its polling loop in a dedicated `ProcessPoolExecutor` or sidecar process, NOT the default ThreadPoolExecutor that contends with all other workers.
- Effort: 300-500 LOC, possibly architectural. Risk: MEDIUM (changes shared-state semantics). Benefit: Telegram + watchdog + sniper all stay responsive even when brain hangs for 30 min.

### Option D — Switch to Anthropic SDK direct (HIGH effort, MEDIUM benefit)

- Replace the CLI subprocess invocation with `anthropic.Anthropic().messages.stream(...)`.
- Effort: 500-800 LOC, replace `_subprocess_call` + `_stream_subprocess_io`. Risk: MEDIUM-HIGH (different auth model, may not support OAuth CLI credentials).
- Benefit: native streaming abort, per-token timeouts, observability into API-side latency.

### Option E — Remove the pool (LOW effort, NEGATIVE benefit)

- Pool delivers 0 benefit today and consumes background CPU spawning workers.
- Could remove or gate it behind a config flag.
- Effort: 30 LOC. Risk: LOW. Benefit: cleaner observability; small CPU saving.

### Combined recommendation (will propose in Phase 2 report)

**Option B (first-byte deadline) as the foundation**, optionally combined with **Option E (pool gated off)** and the door left open for **Option C (process-pool decoupling)** if Option B alone does not eliminate the 7-hour-gap pattern in the 24-hour soak.

- Option B has the best ROI: small surface, large blast-radius reduction, easy to verify.
- Option E removes wasted background work and noise.
- Option C is bigger but addresses H2 (thread starvation) head-on; deferred to a follow-up if Option B doesn't suffice.
- Option D is the heavy nuclear option; defer indefinitely unless investigation surfaces a reason the CLI path itself is broken.

## 7. Hard Constraints for the Fix

- After the fix, `CLAUDE_PROC_STALL_240S` count over 24 h: 0.
- After the fix, no CALL_A or CALL_B may exceed `(first_byte_timeout × max_retries + safety_margin)` wall-clock time.
- After the fix, the strategist loop must continue scheduling new CALL_A cycles even if one call hangs.
- After the fix, the operator's Telegram dashboard must remain responsive during a brain hang.
- No reduction in trade frequency or aggressive-exploitation behavior.
- Shadow mode must continue to work (Shadow uses the same brain code).

## 8. FORBIDDEN Band-Aid Choices

- Raising `self.timeout` from 300 s. Doesn't fix the underlying drift / API latency.
- Catching `subprocess.TimeoutExpired` and returning empty. Loses trading signal.
- Setting `_SUBPROC_POLL_INTERVAL_S` to a smaller value. CPU-spin, no help.
- Removing the stall buckets. Loses observability, no help.

## 9. Observability the Fix Must Add (per prompt Rule 6)

- `CLAUDE_PROC_FIRST_BYTE_DEADLINE deadline_ms=N` when the new timeout fires.
- `CLAUDE_PROC_FIRST_BYTE_OK ms=N first_chunk_bytes=N` already exists (`CLAUDE_PROC_FIRST_TOKEN_MS`) — confirm parity.
- `CLAUDE_RETRY_ON_FIRST_BYTE_TIMEOUT call_id=N attempt=N` for retries triggered by the new deadline.
- Optionally pool stats: `CLAUDE_POOL_STATS hits=N misses=N stale_disposed=N` at periodic intervals to monitor effectiveness if Option A is taken.

## 10. NOT FOUND

- No existing first-byte deadline.
- No existing CALL_A pipelining.
- No existing process-pool isolation.
- No external profiling data (`py-spy` etc.) — would be the next step if Option C is chosen.

## 11. Verification Plan (Phase 4)

After whichever fix lands:

- Run 6+ CALL_A cycles (~30 min minimum).
- Count CLAUDE_PROC_STALL_240S — must be 0.
- P90 CALL_A elapsed must be ≤ 90 s × max_retries (Option B) or ≤ 240 s (Option A only).
- CALL_A cadence must remain 5–10 min consistently.
- Telegram dashboard must remain responsive (operator-confirmed).
- Shadow mode regression test passes.

## 12. Next Step

Write Phase 2 report with the three recommended options (B + E + C-as-followup) and present to operator for the implementation choice.
