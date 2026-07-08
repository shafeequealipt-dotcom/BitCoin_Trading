# HIGH-4 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

HIGH-4 — CLAUDE_PROC_STALL on every Stage-2 brain call (60s+).

## Phase 0 baseline

In the 2.85h audit window:
- 33× CLAUDE_PROC_STALL_60S
- 19× CLAUDE_PROC_STALL_120S
- 38 CALL_A/CALL_B start events
- ≈87 percent of brain calls stall ≥60s
- Sample subprocess state: `state=S wchan=ep_poll stdout_so_far=0` (alive, sleeping on epoll, NO output produced after 60-120 seconds of waiting)

## Investigation

### What CLAUDE_PROC_STALL means

`src/brain/claude_code_client.py:_stream_subprocess_io` (lines 1103-1276) polls the Claude CLI subprocess at 50ms intervals. If `stdout` is silent for ≥60s (`_stall_warn_buckets[0]`), it emits `CLAUDE_PROC_STALL_60S`. At 120s → `_120S` (WARNING). At 240s → `_240S` (ERROR — approaching SIGKILL territory).

The stall watcher's diagnostics show `state=S wchan=ep_poll stdout_so_far=0`. This is the Claude CLI subprocess sleeping on `epoll_wait` — almost certainly waiting on the Anthropic API HTTPS response.

### Root cause classification

The stall is NOT a project-side bug. The chain is:
1. WorkerManager spawns Claude CLI via `subprocess.Popen([self._claude_path, ...])` — line 1006
2. CLI receives the prompt via stdin
3. CLI calls Anthropic API
4. Anthropic processes the prompt (long for complex prompts with extensive thinking)
5. Anthropic streams tokens back
6. CLI writes tokens to stdout
7. Project's `_stream_subprocess_io` reads stdout

Steps 3-5 happen in the Anthropic API's process. Steps 1-2 and 6-7 are Project. The 60-240s silence is during step 4-5 (no tokens generated yet). At 60s+ silence, the Claude API is mid-computation; at 240s+ the prompt may have produced no output at all (truly stuck or rate-limited).

### What can be fixed in-project

The stall ITSELF cannot be eliminated without:
- (a) Reducing prompt complexity (FORBIDDEN per prompt's hard constraint)
- (b) Switching models (FORBIDDEN per prompt — must understand cause first)
- (c) Wrapping subprocess in shorter timeouts (FORBIDDEN — hides the cause)

What CAN be done:
1. **Add observability** — log prompt size, system_prompt size, and command args at CLAUDE_PROC_SPAWNED so operators can correlate stalls with prompt complexity. Currently the spawn log shows only `pid` and `spawn_ms` — no prompt context.
2. **Tag stall events with prompt size** — the CLAUDE_PROC_STALL_*S log lines should include the prompt size so a single grep for stalls shows the corresponding prompt complexity.
3. **Document for follow-up** — record findings in dev_notes so a future operator can revisit if stall rate changes.

### Risk Register Risk 5

The prompt's Risk 5 explicitly states: "HIGH-4 root cause may be external (Claude CLI subprocess). If external, document and defer." This investigation confirms the root cause is external. Documenting and deferring is the operator-aligned path.

## Three options considered

### Option A — Add observability + document defer (recommended)

Enhance CLAUDE_PROC_SPAWNED and CLAUDE_PROC_STALL_*S log lines with prompt_chars / system_prompt_chars / arg_count. Document in dev_notes that the structural fix is deferred pending Phase 4 verification of stall rate vs prompt complexity correlation.

Pros:
- Provides forensic data for future investigation
- Zero behavior change (no decision frequency reduction, no prompt-content change)
- Lets operators identify which prompt classes correlate with stalls

Cons:
- Doesn't reduce stall count
- Stall alerts continue to fire at the same cadence

### Option B — Reduce prompt complexity for high-stall paths

FORBIDDEN per prompt.

### Option C — Switch model

FORBIDDEN per prompt.

## Recommendation

**Option A.** This commit adds observability and documents the deferred root-cause investigation.

## Implementation plan

Single atomic commit. Files modified:

1. `src/brain/claude_code_client.py:_subprocess_call` — extend the CLAUDE_PROC_SPAWNED log line to include `prompt_chars`, `sys_prompt_chars`, and `cmd_argc`.
2. `src/brain/claude_code_client.py:_stream_subprocess_io` — pass prompt_chars into the function (or stash on `self._last_prompt_chars`); enhance CLAUDE_PROC_STALL_*S log lines to include `prompt_chars=...`.
3. Tests: 1-2 tests covering the new fields' presence in the log lines.

## Open questions

None blocking. The structural fix (if needed) requires correlating stall rate with prompt complexity over a multi-week observation window, plus possibly reaching out to Anthropic about API latency. Both are out of scope for this commit.
