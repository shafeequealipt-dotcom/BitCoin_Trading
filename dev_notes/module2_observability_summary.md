# Module 2 — Complete Layer 1 Observability Summary

**Date:** 2026-04-27
**Phases:** 8-15 (Module 2 of `IMPLEMENT_LAYER1_OUTPUT_QUALITY_AND_OBSERVABILITY_PROFESSIONAL.md`)

## Commits (8 phases, 8 commits + 1 investigation commit)

```
f89351b phase15(obs): cycle_metrics extension — schema migration only
26c2455 phase14(obs): context-ID hygiene (Gaps J1, J4)
b58b0f4 phase13(obs): pipeline blind spots — Scanner→Strategist + close reasons
b3afcb2 phase12(obs): intelligence + worker-manager observability (Gaps G1-G3)
52d2aa3 phase11(obs): brain instrumentation (Gaps F1, F2)
6c689a1 phase10(obs): trading + layer manager observability (Gaps B2, C4)
f77a5b6 phase9(obs):  workers subsystem instrumentation (Gaps A1, A4, A5, A7, A8, A9)
8439904 phase8(obs):  investigation pass over all observability gaps
```

## Gap closure summary

| Gap | Source prompt | Status |
|---|---|---|
| A1 | scanner accessor exception silence | ✓ shipped (Phase 9) |
| A2 | scanner _check_blockers exception handlers | ⏸ deferred (large diff; trial-driven) |
| A3 | SCANNER_TICK_START/DONE | ✓ already covered by SCANNER_PACKAGE_BUILD_* + Phase 6 CYCLE_FRESHNESS |
| A4 | price_worker WS callback context | ✓ shipped (Phase 9) |
| A5 | price_worker last_price <=0 silent skip | ✓ shipped (Phase 9) |
| A6 | altdata _timed feed URL/status/retry | ✓ moved to Phase 12 (G1, G2 — fetch sites) |
| A7 | strategy_worker stale aggregate | ✓ shipped (Phase 9) |
| A8 | strategy_worker TA aggregate | ✓ shipped (Phase 9) |
| A9 | regime first-tick restore failure context | ✓ shipped (Phase 9) |
| A10 | regime per-coin divergent symbols | ✓ already structured at regime_worker.py:200-211 |
| B1 | order_service ORDER_BLOCKED standardisation | ✓ already structured |
| B2 | order_service ORDER_START at function top | ✓ shipped as ORDER_ATTEMPT (Phase 10) |
| B3 | order_service prefetch H1 fail at WARNING | ✓ already at appropriate level |
| C1 | layer_manager state_sync heartbeat | ✓ already covered by per-tick LAYER_STATE_SYNC |
| C2 | layer_manager persist fail | ✓ already at WARNING (dead-workers Phase 2) |
| C3 | layer_manager persist-after-toggle | ✓ shipped in dead-workers Phase 2 |
| C4 | layer_manager BRAIN_TRADES_DROPPED | ✓ shipped (Phase 10) |
| D1 | repo INSERT OR REPLACE collisions | ⏸ deferred (own work stream — DB lock instrumentation) |
| D2 | DB slow-query logging | ⏸ deferred (DatabaseManager refactor) |
| D3 | DB query failure context | ⏸ deferred |
| E1 | performance_enforcer stat collection | ⏸ deferred (separate module) |
| E2 | performance_enforcer DB elapsed | ⏸ deferred |
| E3 | pnl_manager PNL_GATE_EVAL | ⏸ deferred |
| F1 | claude_code_client parse fail tag | ✓ shipped (Phase 11) |
| F2 | strategist invalidate_position INFO | ✓ shipped (Phase 11) |
| G1 | fear_greed fetch fail URL/status | ✓ shipped (Phase 12) |
| G2 | funding_rates per-symbol categorisation | ✓ shipped (Phase 12) |
| G3 | fear_greed fallback freshness log | ✓ shipped (Phase 12) |
| H1 | worker manager lifecycle structuring | ✓ already structured (WM_START/STOP/CRASH) |
| H2 | sweet-spot skip silent | ✓ already covered by dead-workers Phase 4 LAYER1*_TICK_SKIP at INFO |
| I1 | Scanner→Strategist handoff | ✓ shipped (Phase 13) |
| I2 | Strategist→Order chain_id | ⏸ deferred (existing did= covers correlation) |
| I3 | PositionWatchdog close reasons | ✓ shipped (Phase 13) |
| I4 | Rule engine evaluation | ⏸ deferred |
| J1 | wid in BaseWorker | ✓ shipped (Phase 14) |
| J2 | did Strategy→Order propagation | ✓ already covered by log_context.py ContextVar |
| J3 | sid PriceWorker WS | ⏸ deferred (Bybit WS lib lifecycle) |
| J4 | actor in ORDER_BLOCKED | ✓ shipped (Phase 14) |

## Closure tally

- **24 gaps closed** in this module (shipped or already correct)
- **9 gaps deferred** (D1-D3, E1-E3, I2, I4, J3 — each warranting its own focused workstream)
- **Phase 15** schema migration shipped; populator + /health extension wired in follow-up

## New observability tags introduced (this module + Module 1 combined)

| Tag | Phase | Level |
|---|---|---|
| SIG_GEN_INPUT | 1 | INFO |
| SIG_CLASSIFY | 1 | INFO |
| XRAY_NONE_REASON | 2 | INFO |
| XRAY_CLASSIFY_SUMMARY conf_p50/p95 | 2 | INFO (extension) |
| REGIME_PERCOIN_SUMMARY | 3 | INFO |
| REGIME_PENDING /N | 3 | INFO (extension) |
| STRAT_L1_DONE distribution | 4 | INFO (extension) |
| STRAT_L2_DONE percentiles | 4 | INFO (extension) |
| STRAT_L3_DONE consensus_dist | 4 | INFO (extension) |
| STRAT_L4_HANDOFF cache sizes | 4 | INFO (extension) |
| PACKAGE_VALIDATE | 5 | INFO |
| PACKAGE_VALIDATE_SUMMARY | 5 | INFO |
| PACKAGE_QUARANTINED | 5 | WARNING |
| CACHE_WRITE / CACHE_READ | 6 | DEBUG |
| CYCLE_FRESHNESS | 6 | INFO |
| SENT_DEGRADED_MODE | 7 | INFO |
| SENT_NO_DATA | 7 | INFO |
| SERVICE_ACCESSOR_FAIL | 9 | DEBUG |
| PRICE_SKIP_INVALID | 9 | DEBUG |
| PRICE_WS_TICK_FAIL | 9 | WARNING |
| STRAT_SKIP_STALE_AGG | 9 | INFO |
| STRAT_TA_DONE | 9 | INFO |
| ORDER_ATTEMPT | 10 | INFO |
| BRAIN_TRADES_DROPPED | 10 | WARNING |
| CLAUDE_PARSE_FAIL | 11 | WARNING |
| POSITION_INVALIDATED | 11 | INFO (promoted from DEBUG) |
| FEAR_GREED_FETCH_FAIL | 12 | WARNING |
| FUNDING_FETCH_FAIL | 12 | WARNING |
| FEAR_GREED_FALLBACK | 12 | WARNING |
| STRATEGIST_PACKAGES_READ | 13 | INFO |
| POSITION_CLOSE_REASON | 13 | INFO |
| ORDER_BLOCKED actor= | 14 | (extension) |
| WM_START/STOP/CRASH wid= | 14 | (extension) |
| LAYER_STATE_PERSIST_OK/FAIL/DRIFT_RECOVERED | dead-workers | INFO/WARNING |
| WORKER_NEVER_TICKED / OVERDUE / LIVENESS_HEARTBEAT | dead-workers | WARNING/INFO |
| WORKER_TICK_START / FAIL | dead-workers | INFO/WARNING |
| LAYER1{B,C,D}_TICK_SKIP rate-limited | dead-workers | INFO |

## Verification — automated

```
Module 1 + Module 2 focused: 140 passed
Full project: pending — running below
```

## Operator deployment

The plan calls for restart-and-observe verification. Operator sequence:

1. Backup + git tag (already in place)
2. `pm2 restart workers` (deploys dead-workers + Module 1 + Module 2 — 21+9 = 30+ commits since pre-output-quality-fix tag)
3. Wait ~30 min — confirm WORKER_LIVENESS_HEARTBEAT healthy
4. /start trading via Telegram — confirm cycle_gated workers tick
5. Module 2 verification: grep workers.log for the new tags above; verify each fires under expected conditions

## Out of scope (deferred items list)

These warrant follow-up commits if they surface as priorities during operator trial:

- **D1 / D2 / D3** — DB observability is its own work stream (the catalog flagged this as "Phase 9 DB lock instrumentation" — separate from this output-quality module).
- **E1 / E2 / E3** — performance_enforcer + pnl_manager visibility. Not blocking.
- **I2** — Strategist→Order chain_id. The existing did= covers the bulk of correlation needs.
- **I4** — Rule engine evaluation. Module-internal; needs deeper introspection.
- **J3** — PriceWorker WS sid. Bybit WS lib owns the connect/reconnect lifecycle.
- **A2** — scanner_worker._check_blockers exception handlers. Many small except-blocks; trial-driven decision on which deserve INFO/WARNING.
- **Phase 15 populator + /health Layer 1A/1B/1C/1D sections** — schema is ready; populator wires the new cycle_metrics columns from the new tag emissions.
