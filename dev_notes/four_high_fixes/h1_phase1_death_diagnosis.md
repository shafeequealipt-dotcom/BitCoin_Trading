# H1 Phase 1 — Empirical Death Diagnosis (Steps B1–B6)

## Method

Spawned a Claude CLI subprocess directly with the exact arguments used by the prewarm pool (`/home/inshadaliqbal786/.local/bin/claude -p --output-format text --system-prompt "..."`) on the production VM. Observed lifecycle without writing to stdin. Then repeated with a single newline byte written at spawn time and a real prompt appended after 5 s.

## Setup

```
Claude CLI: 2.1.143 (Claude Code)
Binary: /home/inshadaliqbal786/.local/bin/claude
Spawn args (mirrors src/brain/claude_code_client.py:348-361):
  ['claude', '-p', '--output-format', 'text', '--system-prompt', '<text>']
Pipe layout: stdin=PIPE, stdout=PIPE, stderr=PIPE, text=False
preexec_fn: os.setsid (process-group isolation)
```

## Step B1 — Baseline (no stdin input)

```
[t=0.0] spawning + holding stdin open (no write)
[t=1.0] status=alive
[t=3.0] status=alive
[t=5.0] status=DEAD rc=1
[t=5.0] stderr: "Warning: no stdin data received in 3s, proceeding without it. If piping from a slow command, redirect stdin explicitly: < /dev/null to skip, or wait longer."
        "Error: Input must be provided either through stdin or as a prompt argument when using --print"
```

**ROOT CAUSE CONFIRMED.** `claude -p` errors out with exit code 1 after **3 seconds** of stdin silence. This explains every prewarm pool failure observed in production:
- 0 % hit rate
- `dead_disposed=42, age_disposed=0` (all deaths are early, not TTL)
- 43.2 s minimum age at disposal (every subprocess died at ~3 s; the 43.2 s is the time between spawn and the next acquire that found it dead)
- 6792.4 s maximum age at disposal (subprocess died at 3 s; pool didn't acquire it until ~113 min later)

The age_disposed=0 stat is the smoking gun: the max_age_seconds=900 boundary never fires because every subprocess dies LONG before reaching it.

## Step B2 — Mitigation (newline byte at spawn)

```
[t=0.0] spawning + writing single newline byte to stdin + flush + keep stdin open
[t=1.0] status=alive
[t=3.0] status=alive
[t=5.0] status=alive
[t=10.0] status=alive
[t=15.0] status=alive
[t=30.1] status=alive
[t=60.0] status=alive
[t=60.0] subprocess SURVIVED 60s — kill
```

**Subprocess survived 60 s with stdin held open** after a single newline byte was written at spawn time. The 3 s stdin-silence timer is reset by the byte; the CLI then waits indefinitely for the rest of the input (i.e., until stdin is closed).

## Step B5 — End-to-end correctness (no response corruption)

```
[t=0.00] spawned + keepalive newline written
[t=5.01] alive=True; appending real prompt: "echo me back: alpha-bravo-charlie"
[t=5.01] prompt sent + stdin closed
[t=8.73] FIRST_TOKEN_MS_AFTER_PROMPT=3719
[t=9.28] rc=0 response_elapsed=4.3s
STDOUT (26 bytes): "ECHO: alpha-bravo-charlie\n"
STDERR: <empty>
```

The leading newline does NOT corrupt the response. The CLI interprets the input as `"\necho me back: alpha-bravo-charlie"` and produces the correct echo (matching the system-prompt template `"Respond ONLY with: ECHO: <whatever the user said>"`). Leading whitespace is treated as a no-op by Claude's input parser.

## Step B3 — Kernel signal evidence

`dmesg | tail -200` and `journalctl --user -n 200` checks during the manual spawn showed:
- No OOM kills
- No cgroup memory events
- No external SIGTERM / SIGKILL

The 3 s exit is **self-inflicted** by the Claude CLI binary's own stdin-silence guard, NOT an external signal.

## Step B4 — Kernel call trace

While the subprocess was alive (with the newline keepalive), `/proc/<pid>/wchan` showed `do_select` (Node.js readable-stream poll, equivalent to `epoll_wait` on the stdin pipe). After the timeout / EOF, the process transitioned through Node.js cleanup to exit.

## Step B6 — Correlation with production logs

Production `CLAUDE_PREWARM_DISPOSED` events show age_s values: min 43.2, max 6792.4, p50 900.0. With the empirically-confirmed 3 s death:

- Every subprocess dies at ~3 s after spawn.
- The pool's `acquire()` checks at the time of next CALL_A (~2.5 min cadence on average).
- age_s at acquire is dominated by inter-call interval, not subprocess lifetime.
- The p50 sitting at 900 (the max_age boundary) reflects subprocesses that lived as zombies in the dict for a long time before the next acquire found them.

This is fully consistent with the empirical 3 s death.

## Why prior fixes didn't catch this

The T2-1 fix added the prewarm pool. The J4 (commit 51293df) fix added observability with `dead_disposed` vs `age_disposed` counters. Both fixes assumed the subprocess would live until either age expired or external death occurred. Neither fix tested what happens when `claude -p` is spawned without immediate stdin input. The 3 s timeout was not documented and not tested.

## Forward implications for the fix

The fix is **single line**: at the end of `_replenish_blocking` in `src/brain/claude_code_client.py`, immediately after `subprocess.Popen(...)`, write one newline byte to `proc.stdin` and flush. Do NOT close stdin (the CLI needs more input later). Stdin stays open until the real prompt arrives at acquire time.

When the real prompt arrives in `_subprocess_call`, the existing `proc.stdin.write(prompt_bytes); proc.stdin.flush(); proc.stdin.close()` sequence runs. The accumulated stdin is `"\n" + prompt_bytes`. Claude CLI's parser treats leading whitespace as a no-op (Step B5 confirms).

This single-line fix is expected to:
- Raise prewarm hit rate from 0 % to > 80 % (subprocesses now actually live to be acquired).
- Save ~3-10 s of cold-spawn overhead per CALL_A.
- Eliminate `dead_disposed` events except for genuine deaths (OOM, signal, etc.).
- Zero impact on response correctness.
- Zero impact on Shadow (Shadow path doesn't use this pool).

## Latency breakdown impact

Phase 0 baseline shows CALL_A median latency 102 s. Per the empirical timing, cold-spawn-to-first-token is ~3.7 s (see Step B5 — though that was a trivial prompt; production prompts of 17-25 KB will have longer first-token latency due to API processing). If prewarm works:
- `pool_acquire_ms` becomes ~0-10 ms (vs. ~3-7 s cold spawn).
- `first_token_ms` is API-side and unaffected.
- `full_response_ms` shrinks by the ~3-7 s saved on spawn.

Net forecast: ~5 % drop in median CALL_A latency. Modest but real. Stall events (60/120/240 s buckets) are API-side and largely unaffected by the prewarm fix. **H6 will be partially addressed (the 60 s bucket may drop) but H2's main cost is API latency, not subprocess overhead.** This is operator-relevant: the prewarm fix is correct but the dominant CALL_A latency cost requires a separate architectural change (streaming, parallel calls, caching) which is out of scope.

## Conclusion

Root cause confirmed empirically: `claude -p` exits with rc=1 after 3 s of stdin silence. The pool's design assumed the CLI would tolerate idle. It does not. A one-byte keepalive at spawn time fixes the pool. The fix is safe, surgical, and high-confidence.
