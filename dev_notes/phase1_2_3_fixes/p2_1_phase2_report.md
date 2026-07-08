# P2-1 Phase 2 — Claude CLI Stall: Operator Decision Report

## Diagnosis Summary

Phase 1 confirmed P2-1 is **alive and active today**:

- One `CLAUDE_PROC_STALL_240S` at 09:32:49 today (post-T2-1).
- Today's CALL_A elapsed P90 = 82 min, P99 = 111 min.
- The 7-hour CALL_A gap from 10:27 → 17:31 was caused by **one hung CALL_B that took 112 minutes to fire its 300 s timeout** — the executor thread was starved.
- T2-1's prewarm pool: 0 hits, 1424 misses today. The 60-second freshness window is mismatched to the 5–10-min CALL_A cadence. Pool is dead weight.
- Real bottleneck is API streaming latency on large CALL_A prompts (18K+ chars). First-byte time: P90 ≈ 247 s, max 300+ s.

## What the Fix Has to Achieve

- Bound the blast radius of any single slow CALL_A or CALL_B so it can never hang the system for 30+ minutes.
- Eliminate the 7-hour gap pattern by making the timeout enforcement reliable.
- Preserve aggressive opportunity exploitation — no reduction in trade frequency, no defensive bias.
- Keep Telegram responsive even when the brain itself is slow.
- Shadow mode must continue working.

## Recommended Approach (Three Stacked Decisions)

### Decision A — Add a first-byte deadline (independent of total timeout)

| Option | Behavior | Effort | Risk |
|--------|----------|--------|------|
| A1 (Recommended) | Add `first_byte_timeout_seconds` setting (default 90 s). If the subprocess produces zero stdout bytes in N s, kill, capture diagnostics, retry on fresh process. Total timeout stays 300 s. | 60–120 LOC, 5–8 tests | LOW |
| A2 | Same but no separate setting — hard-code 90 s. | 40 LOC | LOW |
| A3 | Do nothing on first-byte; keep 300 s only. | 0 LOC | - |

Recommendation: A1. Configurable so the operator can tune later. New observability tags: `CLAUDE_PROC_FIRST_BYTE_DEADLINE`, `CLAUDE_RETRY_ON_FIRST_BYTE_TIMEOUT`.

### Decision B — Pool handling

| Option | Behavior | Effort | Risk |
|--------|----------|--------|------|
| B1 (Recommended) | Bump `_max_age_seconds` from 60 → 900 s, and prewarm both CALL_A and CALL_B sys-prompts at boot. Keeps the pool, raises hit rate from 0% to most calls. | 30–50 LOC, 2 tests | LOW |
| B2 | Gate the pool behind a settings flag `use_subprocess_pool` (default off) since today's hit rate is 0. Removes wasted background spawns. | 20 LOC | LOW |
| B3 | Leave pool alone — Decision A alone is sufficient. | 0 LOC | - |

Recommendation: B1. Even at 5-10 min cadence, a 900-s freshness window will capture most reuse opportunities. Background spawns then have a chance to be useful. Adds `CLAUDE_POOL_STATS` periodic emit for observability.

### Decision C — Brain isolation from worker event loop

| Option | Behavior | Effort | Risk |
|--------|----------|--------|------|
| C1 (Deferred-recommended) | Keep brain in the default ThreadPoolExecutor for now. Re-evaluate after Decision A + B verification. Most likely Decision A alone closes the 30-min stuck window. | 0 LOC | - |
| C2 | Move brain calls to a dedicated `ProcessPoolExecutor` so OS scheduling can't starve them. | 300–500 LOC, complex re-wiring | MEDIUM |
| C3 | Sidecar brain in a separate Python process via IPC. | 800+ LOC | MEDIUM-HIGH |

Recommendation: C1 (defer). Re-evaluate if 24-h soak still shows the multi-hour gap pattern. Most evidence points to first-byte stalls amplified by retries — Decision A alone should resolve the operator-visible symptom.

## Combined Recommended Sequence

1. Single branch `fix/p2-1-claude-cli-stall`.
2. Two atomic commits on the branch:
   - Commit 1: First-byte deadline (Decision A1). New settings field. Code in `claude_code_client.py`. Unit tests.
   - Commit 2: Pool freshness tuning + prewarm-both at boot + periodic stats (Decision B1). Unit tests for new pool behavior.
3. Merge into `audit/all-tier2-combined`. Restart services.
4. Verification window: 30-min minimum (≥ 6 CALL_A cycles). Pull metrics. If `CLAUDE_PROC_STALL_240S = 0` and CALL_A cadence stays 5-10 min, P2-1 ships clean. If not, escalate to C2.

## Risks Mitigated

- A1 caps the worst-case stall at 90 s × `max_retries` = 270 s. Today's 111-min cycles cannot reproduce.
- B1 actually delivers the spawn-time savings T2-1 was supposed to provide.
- C1 keeps the change surface small for the first ship.
- Aim preserved: trade frequency unchanged, retries unchanged, no defensive bias.
- Shadow unaffected: brain.py is the same code; Shadow uses the same client and benefits identically.

## Risks NOT Mitigated by This Fix

- If the API itself sustains 300+ s first-byte times for long stretches (network issue, account-level rate limit), the system still slows. We will see this via the new `CLAUDE_RETRY_ON_FIRST_BYTE_TIMEOUT` count. If that count climbs, escalate to Decision D (Anthropic SDK direct).
- The 7-hour gap symptom requires verification. If a 24-h soak shows another > 15-min gap after A+B, the executor thread is being starved by something else (e.g., GIL contention from other workers). Then Decision C2 becomes the next step.

## What I Will NOT Do Without Operator Approval

- Switch to the Anthropic SDK direct (Decision D).
- Add a ProcessPoolExecutor (Decision C2).
- Remove the existing T2-1 pool entirely (B2).
- Change `self.timeout`, `_SUBPROC_POLL_INTERVAL_S`, or any retry count.
- Reduce stall observability.
- Touch Stage 2 prompt construction.

Awaiting three decisions: A (first-byte deadline), B (pool), C (isolation).
