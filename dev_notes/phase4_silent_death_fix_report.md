# Phase 4 — Cycle-Gate Observability Upgrade (revised from silent-death fix)

**Date:** 2026-04-27
**Commits:** `a173e56`

## Re-scope rationale

Phase 4 was originally a hang-fix with four candidate sub-paths:
- **A** — lock-init in shared services (TACache / ShadowKlineReader)
- **B** — cross-coroutine deadlock
- **C** — DB-lock contention
- **D** — inter-worker cache wait

Phase 0 evidence proved none apply. The 5 "dead" workers are correctly skipping per `SweetSpotWorker.start():475-486` because `is_cycle_active() = L2 AND L3 = True AND False`. The skip emits at DEBUG, invisible at the configured `log_level=INFO`. **There is no hang.**

Per the approved plan ("contingency tree only — fix chosen after Phase 1 evidence"), Phase 4 is re-scoped to an **observability upgrade** that closes the visibility gap that hid the silent-skip pattern for 7 hours on 2026-04-27.

## Changes

| Tag | Where | Cadence | Rationale |
|---|---|---|---|
| `WORKER_TICK_START` | `BaseWorker.start` line ~225 / `SweetSpotWorker.start` line ~582 | One-shot per worker | Diagnostic: WM_START + WORKER_TICK_START + no WORKER_FIRST_TICK = real tick-body hang. WM_START + no WORKER_TICK_START = upstream issue (cycle gate, scheduler, run-loop wedge). |
| `WORKER_TICK_FAIL` | BaseWorker.start / SweetSpotWorker.start exception handler | Per occurrence | Structured grep target for tick failures across all workers; complements the existing free-text "Worker '%s' tick failed" error. |
| `LAYER1{B,C,D}_TICK_SKIP` | Cycle-gate skip branch (was DEBUG, now INFO) | Per worker per 10 min (rate-limited) | Operator visibility into "5 workers silent because L3 is OFF". Intermediate skips still emit at DEBUG so a DEBUG-level run captures every fire-and-skip pair. |

## Configuration

Hardcoded module-level constant `_SKIP_INFO_RATE_LIMIT_S = 600` in `base_worker.py`. With 5 cycle_gated workers on 5-min cadence, that's at most 30 INFO `*_TICK_SKIP` events per hour total — bounded noise, unbounded visibility. Not exposed via config (a tunable for a single-purpose noise threshold isn't worth the surface area; if operators ever want a different rate, change the constant).

## Tags NOT added

`WORKER_INIT_START` / `WORKER_INIT_DONE` / `WORKER_INIT_FAIL` were in the original prompt's Phase 4 spec for the lock-init sub-path A. Since no service init is hanging in production, there's no ticked init step to surface; adding empty stubs would be observability inflation. The constants are reserved in `log_tags.py` for any future work that adds explicit async service init.

## Verification — automated

```
pytest tests/test_worker_liveness.py
       tests/test_worker_liveness_watchdog.py
       tests/test_layer_state_sync.py
       tests/test_layer_manager_persistence.py
       tests/test_corrected_layer1_integration.py
       tests/test_logging_routing.py
       tests/test_telegram/test_telegram.py
       95 passed
```

## Verification — operator-driven (post-deploy)

| # | Trial | Pass criterion |
|---|---|---|
| 4.1 | Restart workers; tail workers.log for 2 min | `WORKER_TICK_START` fires once per worker (~18 events) within 60 s of the worker reaching its tick body. With L3=OFF, the 5 cycle_gated workers do NOT emit `WORKER_TICK_START` — confirming they never reach tick(). |
| 4.2 | With L3=OFF for 30 min | Each cycle_gated worker emits ONE `LAYER1{B,C,D}_TICK_SKIP \| rate_limited=true` INFO log per 10 min, not per fire. Total: ~5 × 3 = 15 INFO skips in 30 min. |
| 4.3 | `/start trading` (toggles L2 + L3) | Within 5 min, all 5 cycle_gated workers emit their first `WORKER_TICK_START` followed by `WORKER_FIRST_TICK`. The operator can see the gate-to-tick transition explicitly. |
| 4.4 | Inject a tick failure (e.g. raise inside structure_worker.tick) | `WORKER_TICK_FAIL \| name=structure_worker err_type=RuntimeError ...` fires at WARNING. The existing "Worker '%s' tick failed" error log still fires alongside (back-compat). |

## Out of scope for this phase

- Phase 5 re-verification of the 10 prior post-Layer-1 fixes.
- Phase 6 / 7 live trial + 24-hour observation (executed separately by the operator).

## Cross-phase summary so far

Phases 0-4 collectively deliver:

| Property | Before fixes | After fixes |
|---|---|---|
| Layer 3 toggle stability | reverts in 6-33 s after cascaded /start trading | persists indefinitely (Phase 2) |
| Per-worker liveness visibility | invisible (DEBUG-only skip log filtered at INFO) | INFO heartbeat every 30 s + rate-limited skip events + /health section (Phases 3 + 4) |
| Silent failure detection | absent | `WORKER_NEVER_TICKED` within 90 s for 1A workers / 390 s for SweetSpotWorker (Phase 3) |
| Cradle-to-grave tick events | partial (WM_START, WORKER_FIRST_TICK, LAYER1*_TICK_DONE) | complete (+ WORKER_TICK_START one-shot, + WORKER_TICK_FAIL on exception) (Phase 4) |
| False-positive alarms on cycle_gated workers when L3=OFF | n/a | suppressed — STATUS_IDLE_CYCLE_GATE classification (Phases 3 + 4) |

## Rollback

Phase 4 changes are additive observability with no behavioural impact on the tick path (the existing logic is unchanged; only new log lines are added). Rollback is `git revert a173e56` if any of the new logs prove problematic.
