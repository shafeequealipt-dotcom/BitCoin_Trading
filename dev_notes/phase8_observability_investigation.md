# Phase 8 — Module 2 Observability Gap Investigation

**Date:** 2026-04-27
**Purpose:** Map every observability gap from the prompt + the prior catalog (`dev_notes/phase0_observability_gaps_catalog.md`) to its execution phase, with implementation cost + log-volume estimate.

## Mapping table

### Phase 9 — Workers subsystem (Gaps A1-A10)

| ID | File | Tag | Severity | Volume |
|---|---|---|---|---|
| A1 | scanner_worker.py:85-107 | SERVICE_ACCESSOR_FAIL | high | DEBUG; sparse |
| A2 | scanner_worker.py:277-440 | SCANNER_BLOCKAGE_CHECK_FAIL | high | DEBUG; sparse |
| A3 | scanner_worker.py:593-725 | SCANNER_TICK_START/DONE | med | INFO; 12/hr |
| A4 | price_worker.py:217-219 | PRICE_WS_TICK_FAIL | med | DEBUG; rare |
| A5 | price_worker.py:186-187 | PRICE_SKIP_INVALID | med | DEBUG; rare |
| A6 | altdata_worker.py:123-176 | ALTDATA_FEED_FAIL | high | WARNING; rare |
| A7 | strategy_worker.py:212-240 | STRAT_SKIP_STALE_AGG | med | INFO; per-cycle |
| A8 | strategy_worker.py:274-280 | STRAT_TA_DONE aggregate | med | INFO; per-cycle |
| A9 | regime_worker.py:125-131 | REGIME_RESTORE_FAIL | high | WARNING; once at boot |
| A10 | regime_worker.py:200-223 | REGIME_PERCOIN_DIVERGENT | med | INFO; per-cycle |

### Phase 10 — Trading + Layer manager (B1-B3, C1-C4)

| ID | File | Tag | Severity | Volume |
|---|---|---|---|---|
| B1 | order_service.py:150-186 | ORDER_BLOCKED standardisation | high | rare (per blocked order) |
| B2 | order_service.py:388-500 | ORDER_START at function top | med | per-order |
| B3 | order_service.py:214-222 | STRAT_PREFETCH_H1_EMPTY at WARNING | med | rare |
| C1 | layer_manager.py:188-200 | LAYER_STATE_SYNC_HEARTBEAT every 10 iter | high | once per 10 min |
| C2 | layer_manager.py:202-210 | LAYER_STATE_PERSIST_FAIL → ERROR + telegram | med | already at WARNING in dead-workers fix; verify ERROR + alert |
| C3 | layer_manager.py:285-339 | persist-after-toggle | CRITICAL | ✓ already fixed in dead-workers Phase 2 |
| C4 | layer_manager.py:617 | BRAIN_TRADES_DROPPED + telegram | high | rare |

### Phase 11 — DB + Strategies + Brain (D1-D3, E1-E3, F1-F2)

| ID | File | Tag | Severity | Volume |
|---|---|---|---|---|
| D1 | trading_repo.py | ORDER_COLLISION | high | rare |
| D2 | database/connection.py | QUERY_SLOW | med | sampled (>threshold) |
| D3 | repos generally | QUERY_FAIL | med | rare |
| E1 | performance_enforcer.py:198-287 | ENFORCER_STATS_COLLECT | high | per-tick |
| E2 | performance_enforcer.py:317-359 | ENFORCER_DB_STATS | med | per-tick |
| E3 | pnl_manager.py | PNL_GATE_EVAL | med | per-call |
| F1 | claude_code_client.py | CLAUDE_PARSE_FAIL distinct from CLAUDE_CALL_FAIL | low | rare |
| F2 | strategist.py:174-200 | POSITION_INVALIDATED | med | rare |

### Phase 12 — Intelligence + Worker manager (G1-G3, H1-H2)

| ID | File | Tag | Severity | Volume |
|---|---|---|---|---|
| G1 | altdata/fear_greed.py:57-91 | FEAR_GREED_FETCH_FAIL with URL/status | high | rare |
| G2 | altdata/funding_rates.py:69-75 | FUNDING_FETCH_FAIL with category | high | per-failed-symbol |
| G3 | altdata/fear_greed.py:100-111 | FEAR_GREED_FALLBACK with age | med | rare |
| H1 | workers/manager.py | WORKER_START/STOP/CRASH structured | med | once per worker per restart |
| H2 | workers/base_worker.py | SWEET_SPOT_SKIP with next_in_s | med | already exists as LAYER1*_TICK_SKIP at INFO from dead-workers Phase 4 — verify content |

### Phase 13 — Pipeline blind spots (I1-I4)

| ID | Path | Tag | Volume |
|---|---|---|---|
| I1 | scanner→strategist | SCANNER_PACKAGES_WRITTEN, STRATEGIST_PACKAGES_READ | per-cycle |
| I2 | strategist→order | STRATEGY_DIRECTIVE_ATTEMPT, ORDER_RESULT(chain_id) | per-directive |
| I3 | position_watchdog→close | POSITION_CLOSE_REASON | per-close |
| I4 | rule_engine | RULE_ENGINE_EVAL | per-evaluation |

### Phase 14 — Context-ID hygiene (J1-J4)

| ID | Where | Implementation |
|---|---|---|
| J1 | BaseWorker.__init__ | self.wid = uuid.uuid4().hex[:8]; include in worker logs |
| J2 | StrategyWorker→Scanner→Stage2→Order | propagate did= via context vars (already in log_context.py) |
| J3 | PriceWorker WS | self.sid = uuid.uuid4().hex[:8] on connect; new sid on reconnect |
| J4 | order_service ORDER_BLOCKED | actor=<layer3_auto|operator_manual|system_auto|enforcer|gate> |

### Phase 15 — /health + cycle_metrics

| Target | Change |
|---|---|
| `/health` Telegram | Multi-section format per the prompt: Layer 1A/1B/1C/1D detail + Brain Reliability + (Data Freshness already added Phase 6) |
| `cycle_metrics` table | New columns: signal_buy_pct, signal_sell_pct, signal_neutral_pct, xray_setup_type_count, regime_distribution_json, l1_strategies_fired_avg, l2_score_p50, l3_consensus_dist_json, package_completeness_avg, freshness_klines_to_xray_p50 |
| `cycle_tracker` populator | Read the new tags emitted in Phases 9-14, populate hourly aggregates |

## Already-shipped or already-correct (no work)

- **C3** (layer_manager persist ordering) — shipped in dead-workers Phase 2 (commit `bff3f16`).
- **WORKER_FIRST_TICK** — shipped in dead-workers Phase 3 (commit `0af5204` hooked it).
- **WORKER_NEVER_TICKED / WORKER_TICK_OVERDUE** — shipped in dead-workers Phase 3.
- **LAYER_STATE_PERSIST_OK / FAIL / DRIFT_RECOVERED** — shipped in dead-workers Phase 2.
- **LAYER1{B,C,D}_TICK_SKIP at INFO with rate-limit** — shipped in dead-workers Phase 4 (commit `a173e56`).
- **Per-worker TICK_SLOW thresholds** — shipped pre-this-task (`_TICK_SLOW_PER_WORKER` at base_worker.py:37).
- **SIG_GEN_INPUT / SIG_CLASSIFY** — Phase 1 of THIS module.
- **XRAY_NONE_REASON / conf percentiles** — Phase 2 of THIS module.
- **REGIME_PERCOIN_SUMMARY / REGIME_PENDING /N** — Phase 3 of THIS module.
- **STRAT_L1/L2/L3/L4_DONE distribution** — Phase 4 of THIS module.
- **PACKAGE_VALIDATE / SUMMARY / QUARANTINED** — Phase 5 of THIS module.
- **CYCLE_FRESHNESS** — Phase 6 of THIS module.
- **SENT_DEGRADED_MODE / SENT_NO_DATA** — Phase 7 of THIS module.

## Verification gates per phase

Each phase's report will include:
1. Per-gap "tag fires under expected condition" verification.
2. Log volume delta < 25% per the prompt's rule.
3. No regression in existing tags.
4. Smoke + regression tests pass.

## Known caveats from the prior catalog

- **Loguru rotation tail-F issue** — spec-assumed; verify before fixing. Catalog flags this as needing verification.
- **Per-coin REGIME_PERCOIN_FAIL** — already structured in regime_worker.py (verified during Phase 3 read). May be a no-op for Phase 11.
- **Sentiment SENT_UNKNOWN** — addressed in Phase 7 (categorical refinement). Phase 12's G* items are altdata-specific, not sentiment.

## Estimated total commit count for Module 2

- Phase 8: 1 (this report)
- Phase 9: 5 (one per modified file: scanner, price, altdata, strategy, regime)
- Phase 10: 2 (order_service, layer_manager)
- Phase 11: 3 (database, strategies, brain)
- Phase 12: 2 (intelligence, manager+base_worker)
- Phase 13: 4 (one per chain)
- Phase 14: 4 (one per ID)
- Phase 15: 3 (health, migrations, cycle_tracker)
- Per-phase reports: 7

**Total ≈ 31 commits.**
