# Phase 0.G — Test Inventory

## Existing tests touching scanner / brain gate / packages

These tests must STAY GREEN throughout the rollout (until Phase 10 explicit deletion).

### Layer 1D scanner

| Test file | What it covers | Phase impact |
|---|---|---|
| `tests/test_scanner_filter.py` | `_qualifies()` 5-criterion gate | Stays green Phases 0-9; Phase 10 deletion candidate |
| `tests/test_scanner_filter_aggregate.py` | 9-bucket SCANNER_FILTER_AGGREGATE counter | Phase 5 extends with mode parametrize |
| `tests/test_scanner_rr_direction.py` | rr_long/rr_short direction-aware (Phase 4 fix) | Stays green; Phase 4 verifies `structural_quality` reads same |
| `tests/test_scanner_opportunity_score_confidence.py` | Phase 5b struct_norm × setup_type_confidence | Phase 4 verifies same in interestingness `structural_quality` |
| `tests/test_scanner_async_prefetch.py` | F&G + open-position prefetch async correctness | Stays green; both modes use prefetch helpers |
| `tests/test_scanner_active_universe_enrichment.py` | Phase 8 active_universe table fields | Stays green throughout |

### CoinPackage + brain

| Test file | What it covers | Phase impact |
|---|---|---|
| `tests/test_coin_package_validator.py` | Validator: complete=1.0, missing fields, staleness | Phase 6 adds new optional blocks (must not regress completeness) |
| `tests/test_layer_manager_cold_start.py` | Cold-start gate avg_completeness path | Phase 7 fixture updated for `min_qualified=1` |
| `tests/test_corrected_layer1_integration.py` | Pipeline integration: scanner → packages → brain | Stays green throughout (canary) |
| `tests/test_corrected_layer1_pipeline_e2e.py` | Full E2E: scanner → packages → stage 2 brain | Stays green throughout (canary) |
| `tests/stage1_2_pipeline_test.py` | Strategist prompt building with mock packages | Phase 6 may extend |
| `tests/test_brain_subprocess_streaming.py` | Claude CLI subprocess + parse | Stays green |
| `tests/test_strategy_worker_consensus.py` | Consensus cache shape | Phase 2 extends with `vote_distribution` key |

### Other

| Test file | Phase impact |
|---|---|
| `tests/test_phase{1..8}_layer1_restructure/` | Stays green (prior restructure tests) |
| `tests/test_strategies/test_scanner.py` | MarketScanner active_universe getter/setter — stays green |
| `tests/test_end_to_end_pipeline/test_layer1_pipeline.py` | Layer 1 full flow — stays green |

## New tests per phase (≤3 per phase, ≤10 min effort)

| Phase | New test files |
|---|---|
| 1 | `test_phase1_1d_briefing/test_log_tags_registered.py`, `test_cycle_metrics_columns_present.py` |
| 2 | `test_phase2_1d_briefing/test_vote_distribution_shape.py` |
| 3 | `test_phase3_1d_briefing/test_state_labeler_pure.py` |
| 4 | `test_phase4_1d_briefing/test_interestingness_pure.py`, `test_interestingness_no_nan.py` |
| 5 | `test_phase5_1d_briefing/test_briefing_mode_path.py`, `test_mode_flag_default_off.py` |
| 6 | `test_phase6_1d_briefing/test_prompt_extension_flag_off.py`, `test_prompt_extension_flag_on.py` |
| 7 | `test_phase7_1d_briefing/test_gate_passes_with_one_qualified.py` |
| 8 | `test_phase8_1d_briefing/test_ab_alternation_deterministic.py` |
| 9 | `test_phase9_1d_briefing/test_default_mode_briefing.py` |
| 10 | (none — removal) |
| 11 | (none — observation) |

## Tests that become obsolete in Phase 10 (case-by-case audit)

- `tests/test_scanner_filter.py` — exercises only the legacy 5-gate path. Likely DELETE.
- `tests/test_scanner_filter_aggregate.py` — if briefing-mode aggregate test in `test_scanner_filter_aggregate_briefing.py` covers all buckets, DELETE.
- `tests/test_scanner_rr_direction.py` — RR is a structural input, still relevant. KEEP, possibly UPDATE.
- `tests/test_scanner_opportunity_score_confidence.py` — opportunity_score path retired in briefing mode. Most assertions migrate to `test_interestingness_*`. EVALUATE.

## Cross-phase invariant tests (always green)

```
tests/test_corrected_layer1_pipeline_e2e.py
tests/test_corrected_layer1_integration.py
tests/test_layer_manager_cold_start.py  (except Phase 7, where fixture updates alongside config)
tests/test_coin_package_validator.py
tests/test_scanner_async_prefetch.py
tests/stage1_2_pipeline_test.py
```

## Test velocity discipline (per memory `feedback_test_velocity`)

- ≤10 min on tests per phase
- 1-3 new tests per phase, each answers ONE question
- No mock-heavy unit tests for integration seam — use existing E2E harnesses
- No exhaustive coverage of legacy path — existing tests are the regression net
