# H1/H2/H6 Phase 1 — Synthesis

## Root cause (confirmed empirically)

`claude -p` exits with rc=1 after **3 seconds** of stdin silence. Error text:

```
Warning: no stdin data received in 3s, proceeding without it. If piping from a
slow command, redirect stdin explicitly: < /dev/null to skip, or wait longer.
Error: Input must be provided either through stdin or as a prompt argument when
using --print
```

The pool in `src/brain/claude_code_client.py::_ClaudeWorkerPool._replenish_blocking` spawns a `claude -p` subprocess and does NOT write to stdin. Every subprocess dies within 3 s — well before the configured `max_age_seconds=900` ever fires. Pool hit rate is 0 % because acquire() always finds a dead subprocess.

Full evidence in `h1_phase1_death_diagnosis.md`. Mitigation (single newline byte at spawn) verified to keep the subprocess alive for ≥ 60 s without corrupting response correctness.

## Aim impact

H1 fix raises pool hit rate from 0 % to > 80 % (forecast). Each successful reuse saves ~3-7 s of cold-spawn overhead. CALL_A median latency drops from 102 s to ~95-99 s — modest improvement.

**H2 (CALL_A latency) is mostly API-bound, not subprocess-bound.** The dominant cost is the first-token wait inside the model API, NOT subprocess spawn. Fixing H1 will not bring CALL_A below 60 s (the spec target). That target would require an architectural change (streaming, caching, parallel calls) which is out of scope per Part C Rule 3 (forbidden band-aids include prompt compression, CALL_A splitting, cross-cycle caching).

**H6 (stall events) is partly addressed.** A 60 s stall mostly reflects API delay, so the count will not drop drastically from H1 alone. But avoiding the cold spawn shaves the early portion of the timer, so some 60 s stalls may slide into the "no stall" region.

**Honest expectation:** H1 fix succeeds completely (hit rate 0 % → > 50 %); H2/H6 improve modestly (5-10 % reduction in CALL_A median; minor stall-count drop). The spec's targets of < 60 s median and ≥ 50 % stall-drop are not achievable by H1 alone. Operator should know this before sign-off.

## Fix options evaluated

| Option | Description | Status |
|---|---|---|
| **A. Keepalive byte at spawn** | Write `b"\n"` immediately after `subprocess.Popen` returns; do not close stdin | **RECOMMENDED — verified working** |
| B. Reduce max_age_s | Irrelevant — subprocess dies at 3 s regardless of max_age | Reject |
| C. Pre-warm prompt prefix | Send partial prompt at spawn — fragile, semantic risk | Reject |
| D. Replace CLI with direct API | Major architectural change; out of scope | Reject |
| E. Multi-slot pool | All slots still die at 3 s; doesn't help | Reject (orthogonal) |
| F. Stronger health check | Subprocess IS dead — health check is correct | Reject (not the root) |

## Recommendation

**Option A (keepalive byte at spawn).** Single-line change in `_replenish_blocking`. Zero protocol risk (verified the leading newline does not corrupt response). High-confidence fix.

Pair with two observability additions per spec Rule 6:
- `PREWARM_DEATH_CAUSE` — when a subprocess IS found dead, classify (returncode value or exception message in stderr buffer).
- `CALL_A_REUSED_WORKER` — new event at successful acquire, plus existing `CLAUDE_POOL_STATS` hit rate continues.
- `CALL_A_PHASE_TIMING` — new event at CALL_A completion with `pool_acquire_ms` / `cold_spawn_ms` / `prompt_write_ms` / `first_token_ms` / `inference_ms` / `full_response_ms` broken out. This is the spec-mandated H2 observability.

## Aim-bias verdict (4 questions)

| Question | Verdict |
|---|---|
| 1. Trade frequency preserved? | YES (slight rise — faster cycle close means brain ticks slightly faster) |
| 2. Aggression preserved? | YES (no behavior change; just less spawn overhead) |
| 3. Decision speed or quality? | YES (speed — saves ~3-7 s of cold spawn per CALL_A) |
| 4. Passive-close advantage preserved? | YES (no impact — close path doesn't use prewarm pool) |

All four YES. Aim is fully preserved.

## Verification metrics (24h soak after deploy)

| Metric | Baseline (today) | Target after H1 fix |
|---|---|---|
| Prewarm hit rate | 0.0 % | ≥ 50 % (forecast 80 %+) |
| `CLAUDE_PREWARM_DISPOSED reason=dead` | 43/day | < 5/day |
| `CLAUDE_POOL_STATS dead_disposed` | growing | flat |
| `CALL_A_REUSED_WORKER` | n/a (new event) | ≥ 30 over 24 h |
| `CALL_A_PHASE_TIMING pool_acquire_ms` (new event) | n/a | < 100 ms p95 on hit; > 3000 ms on miss |
| CALL_A median latency | 102 s | < 100 s (modest improvement; NOT the spec's < 60 s) |
| CLAUDE_PROC_STALL_60S count | 42/5h | < 30/5h |
| CLAUDE_PROC_STALL_240S count | 1/5h | 0/5h |
| Trade frequency | 4.5/hr | HOLD or RISE |
| Win rate | 78.6 % session | HOLD |
| DB cascade | 0 | 0 |
| Shadow path | working | working |

## Operator-facing honesty

Per spec Rule 1 ("be honest about what you don't know"): the spec's H2 target (CALL_A median < 60 s) is **not achievable by H1 alone**. The dominant cost is API latency, which lives in the Anthropic backend and the cross-VM network path. Achieving < 60 s requires either:
- Streaming response consumption (already partially in place via `CLAUDE_PROC_FIRST_TOKEN_MS`)
- A smaller / cached / parallelized prompt
- A faster model
None are in scope for this cluster. H1 will move H2 modestly; the bulk of the H2 win is a separate optimization to be discussed in a follow-on.

## Trial behaviour after fix

After deploy:
- Pool spawns first subprocess after first CALL_A completes.
- Spawn includes the new keepalive byte write.
- Next CALL_A's acquire() finds the subprocess alive → HIT.
- `CLAUDE_PROC_PREWARM_OK` event emits (existing) + `CALL_A_REUSED_WORKER` event emits (new).
- `CLAUDE_POOL_STATS` line shows `hit_rate_pct` rising (was 0.0 → expect > 50 %).
- `CALL_A_PHASE_TIMING` event emits with `pool_acquire_ms ~ 5 ms` and no `cold_spawn_ms` cost.
- Per-cycle latency drops by ~3-7 s.

## Hard constraints honored

- The CLI command line + stdin protocol (`-p` + close-stdin-to-fire) NOT changed (the keepalive is INPUT, not a new flag).
- No timeout reductions.
- No feature flag bypass.
- No silent death suppression.
- Test coverage: 2-3 surgical tests (per the test-velocity rule).

## Branch: `fix/h1-prewarm-pool-revive`

Commit shape (Phase 3):
- h1/phase3-1: keepalive byte at spawn + observability additions (PREWARM_DEATH_CAUSE, CALL_A_REUSED_WORKER, CALL_A_PHASE_TIMING)
- h1/phase3-2: surgical tests on the new keepalive behavior
- h1/phase3-3: docs (Phase 1 deliverables)
