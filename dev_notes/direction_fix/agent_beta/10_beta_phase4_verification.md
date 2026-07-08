# BETA Phase 4 — Verification Report

## Scope

Verification of R2 (composite-score lock) + R3 (WR-aware XRAY override
threshold) on branch `fix/r2-r3-apex-direction-lock`. The branch is
stacked on ALPHA's tip (R1 plumbing); BETA's consumer code reads the
`trade_direction` field ALPHA added.

## Commits on the BETA branch (after ALPHA tip)

- `828d159` beta/phase3-1 — APEXSettings composite-score weights + R3 WR-aware fields under APEXSettings (relocated next)
- `4a05acf` beta/phase3-1b — relocate R3 WR-aware settings to RiskSettings (next to legacy threshold)
- `1d66f5c` beta/phase3-2 — composite-score `_check_direction_lock` + `APEX_LOCK_DECISION_EXPLAINED`
- `dc4db0a` beta/phase3-3 — WR-aware override threshold + `XRAY_OVERRIDE_RATIO_DETAIL`
- `1e50e2c` beta/phase3-4a — update legacy lock tests for composite-scoring semantics
- `3238635` beta/phase3-4b — 13 fresh tests for composite scoring + WR-aware override

ALPHA commits beneath (inherited):
- `595e9c7` alpha/phase4 verification
- `465eed9` alpha/phase3-3 tests
- `478dd2f` alpha/phase3-2 observability
- `712ccb8` alpha/phase3-1 plumb trade_direction

## Test results

```
tests/test_alpha_r1_trade_direction.py            6 passed
tests/test_beta_r2_r3_composite_lock.py          13 passed
tests/test_apex_direction_lock.py                28 passed, 1 pre-existing fail
tests/test_apex_lock_propagation.py              13 passed
tests/test_apex_flip_decision_log.py             16 passed
tests/test_apex_flip_discipline.py                8 passed
tests/test_apex_flip_rr_boost.py                  7 passed
tests/test_apex_sell_bias_gates.py               18 passed
tests/test_xray_dir_flip.py                       3 passed
tests/test_j3_xray_lock_override.py              15 passed
tests/test_apex_pipeline_integration.py          (passed in integration set)
tests/test_xray_counter_property.py              74 passed
tests/test_setup_classifier_counter.py           26 passed
tests/test_corrected_layer1_integration.py       25 passed
tests/test_corrected_layer1_pipeline_e2e.py      21 passed
                                                ----
total                                           273 passed, 1 pre-existing fail
```

The single failure (`test_system_prompt_still_has_rsi_caution`) is the
same pre-existing assertion ALPHA Phase 4 verified is unrelated to the
direction-fix work.

## Verification metrics (DELTA 04 criteria)

| Metric | Status |
|---|---|
| `APEX_DIR_LOCK` event count drops from 80 to 50-65 | DEFERRED to live trial (composite-scoring locks fewer events by design — aligned-brain cases no longer lock) |
| `APEX_LOCK_DECISION_EXPLAINED` events emit with verdict={fired, bailed} | VERIFIED — caller emits every call; tests confirm format |
| `XRAY_FLIP_SUPPRESSED_BY_LOCK` count drops to 0-2 (was 8) | DEFERRED to live trial; mechanism confirmed (WR-aware threshold 5.0 at neutral 50/50 WR is below the legacy 10.0 dead zone) |
| `XRAY_OVERRIDE_LOCK` count rises to 10-14 (was 6) | DEFERRED to live trial; mechanism confirmed |
| `XRAY_OVERRIDE_RATIO_DETAIL` emits with `derived_threshold` in [2.0, 15.0] | VERIFIED — test_r3_high_buy_wr_lowers + test_r3_high_sell_wr_lowers exercise the bounds |
| Sell/Buy directive ratio improves to 60-75% Sell from 89% | DEFERRED to live trial |
| BSBUSDT-class trades clear | VERIFIED in unit tests (test_signal_isolation_structural_dominates_regime exercises exact 7.3x scenario) |
| ALPHA cross-agent: lock consumes trade_direction | VERIFIED via test_composite_all_signals_align_against_brain + test_signal_isolation_structural_dominates_regime |

## How the operator's directive is satisfied

> "sell and buy should be both work according to the best scenarios,
> not hard coded saying if sell this much then buy this much not like that"

- R2 composite scoring evaluates the SAME 5 signals for both Buy and Sell at decision time. No `if direction == "Buy" then X` branches.
- The asymmetry between Buy and Sell EMERGES from the WR signal. On the current 2026-05-16 data (Buy WR 55.6, Sell WR 41.8) the asymmetry pulls toward Buy automatically; if Sells start winning more it reverses.
- R3 override threshold derivation uses one formula `wr_base * (1 - dir_wr_fraction)` applied to whichever direction the override targets. Same code path, asymmetry emerges from the data only.
- All weights and thresholds are config-tunable, default neutral (1.0 each weight, 0.0 threshold). Operator may tune any of them without re-deploying.

## Cross-cutting safety

- Shadow unaffected — zero changes to `src/shadow/`.
- DB cascade absence — only one new read query in `_derive_wr_aware_override_threshold` (SELECT on indexed trade_log, LIMIT 200). No writes; no cascade risk.
- Brain prompt unchanged.
- Backward compat:
  - Legacy `xray_lock_override_ratio_threshold` retained as cold-start fallback. Existing operator config keys still load.
  - New settings have neutral defaults; existing pipelines behave identically to pre-fix when no per-direction WR signal is present (with the intentional exception of dropped aligned-brain locks, which were no-ops anyway).
  - `_check_direction_lock` signature unchanged; reason text format changed but only the optimizer log consumes it (line 290).
- Type hints + structured logging per project pattern.
- Import smoke clean.

## Files modified

- `src/config/settings.py` — +51 lines (R2 APEXSettings fields + R3 RiskSettings fields)
- `src/apex/optimizer.py` — +191 lines, -41 lines (composite-score `_check_direction_lock`, `APEX_LOCK_DECISION_EXPLAINED` emission, `__init__` stamp init)
- `src/workers/strategy_worker.py` — +151 lines, -5 lines (helper `_derive_wr_aware_override_threshold`, replaced static-threshold read, `XRAY_OVERRIDE_RATIO_DETAIL` emission)
- `tests/test_apex_direction_lock.py` — +71 / -23 lines (semantics-updated assertions)
- `tests/test_apex_flip_decision_log.py` — +23 / -0 lines (regime/WR fixture adjustments)
- `tests/test_beta_r2_r3_composite_lock.py` — +372 lines (13 new tests)

Total: +859 / -69 across 6 files, including ~640 lines of tests.

## GO criterion for next phase

Per DELTA 02 implementation sequence:

BETA Phase 4 GO requires:
- All R2 + R3 unit tests pass (PASS — 19 BETA tests)
- ALPHA tests continue to pass on the stacked branch (PASS — 6 R1 tests)
- Neighbor regression suite passes minus pre-existing RSI fail (PASS — 273 of 274)
- Shadow unaffected (PASS)
- DB cascade absence holds (PASS)
- Import smoke (PASS)

**GO recorded.** GAMMA Phase 3 may begin on `fix/r4-portfolio-direction-cap`.

## Operator notes

- The composite-score framework is operator-tunable. If post-trial data shows the lock fires too often, the operator can:
  - Raise `apex_lock_score_threshold` above 0.0 (e.g., -0.3) to require a stronger negative consensus before locking.
  - Lower individual weights (e.g., `apex_lock_regime_weight = 0.7`) to de-emphasize regime versus structure.
- The WR-aware override is similarly tunable via `xray_lock_override_wr_base` (default 10.0), `wr_floor` (2.0), `wr_ceiling` (15.0), `wr_window_trades` (200). The cold-start fallback `xray_lock_override_wr_window_min` (30 trades) prevents low-sample noise from driving extreme thresholds early in deployment.
- `APEX_LOCK_DECISION_EXPLAINED` fires every optimization call (verdict=fired or bailed). The operator can grep `verdict=bailed_structural` to find cases where structural evidence overrode the regime-only lock that would have fired pre-fix.
- `XRAY_OVERRIDE_RATIO_DETAIL` fires every XRAY override decision with `source=cold_start` (legacy fallback) or `source=wr` (live derivation). The operator can grep `source=wr derived_threshold=2` to find cases at the floor (high-WR direction overrides cheaply) and `source=wr derived_threshold=15` for the ceiling.
