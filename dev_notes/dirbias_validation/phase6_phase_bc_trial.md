# Phase 6 — Phase B + Phase C Combined Trial Specification

Per spec Rule 13. Successor to `phase6_phase_a_trial.md`. Shipped 2026-05-19 at 10:03 UTC.

## Shipped commits

- **Phase B (Issue 3)**: branch `fix/dirbias-labeller-soft-haircut` commit `1ebae0d` (merged at `161fae2`). Converts 8 per-trigger regime hard-kills in `src/workers/scanner/state_labeler.py` to soft confidence-haircut multipliers. New `LabellerSettings.counter_regime_confidence_haircut = 0.5` (active).
- **Phase C (Issue 1)**: branch `fix/dirbias-xray-rr-collapse` commit `99b3420` (merged at `2864216`). Adds min-edge floor on `structural_tp` (`tp_min_distance_pct=0.5`) + symmetric resistance touch filter (`min_touches_resistance=2`). New `is_structurally_invalid` flag on `StructuralPlacement`.

Both merged off `main` HEAD `e250ec4` (post-Phase-A state). Final main HEAD: `2864216`.

## Boot sentinels verified at restart 2026-05-19 10:03:33-35 UTC

| Phase | Sentinel | Value |
|---|---|---|
| C | `XRAY_FLIP_CONFIG` | `tp_min_distance_pct=0.50 min_touches_support=2 min_touches_resistance=2 min_touches_symmetric=True` |
| B | `STATE_LABELLER_REGIME_HAIRCUT_INIT` | `version=2 haircut=0.50 mode=soft_haircut` |
| A1 | `STRAT_REGIME_INSTR_REFRAMED` | `block_version=2 mode=symmetric_scenario` (still firing) |
| pre-existing | `STRAT_CALL_B_REFRAMED` | `system_prompt_version=2 close_rules_removed=2 contract=aggressive_management` (still firing) |

No boot errors. 21 workers heartbeating. Services active.

## Layer state at restart

```
{"layer_active": {"1": True, "2": False, "3": False}, "user_stopped": True}
```

Layer 2 (brain) and Layer 3 (execution) are OFF from the operator's prior emergency_close at 09:35:52. **No new CALL_A or trades will fire until the operator re-enables Layer 2 and Layer 3 via the telegram dashboard.** Layer 1 (data ingestion) continues — kline_worker, structure_worker, regime_worker, altdata_worker all running so when Layer 2/3 re-enable the first CALL_A will have fresh data.

## Pre-ship baseline (Phase 0 carryover + Phase A interim)

From the Phase A 50-min trial (08:44–09:35):
- Brain decisions: 7 Buy / 8 Sell = 47% Buy / 53% Sell (vs 92.3% Sell baseline before Phase A).
- BYBIT entries: 3 Buy / 9 Sell = 25% Buy / 75% Sell (vs 89.3% baseline).
- XRAY flips Buy→Sell: 5 events (all rr_original ≤ 0.3 — collapse signature).
- Closed trades during emergency_close: 3 Buy at -$16.73 / 9 Sell at +$23.21.
- Session PnL: +$6.48.

These are the M1–M5 baselines for the combined Phase B+C trial.

## Combined Phase B+C success criteria

Trial window: 48 hours after operator re-enables Layer 2/3.

| Metric | Pass | Hard-revert trigger | Query |
|---|---|---|---|
| M1 brain Sell% | 40–80% (drop from Phase A's 47% Buy / 53% Sell or further toward balance) | <20% (over-correction) OR ≥90% (Phase B+C inert) | `grep STRAT_DIRECTIVE data/logs/brain.log` |
| M1b brain LONG label count | ≥ 1.5× Phase A baseline (Phase B haircut should admit more LONG labels in trending_down) | <0.8× Phase A baseline | `grep SCANNER_LABELED data/logs/workers.log` |
| M2 BYBIT entry Sell% | 40–80% (Phase C should reduce XRAY flip rate substantially) | <20% OR ≥85% | `grep BYBIT_DEMO_ORDER_RECEIVED data/logs/workers.log` |
| M2b XRAY_DIR_FLIP rate | drops by ≥50% vs Phase A's 5-flips-in-50-min rate (~0.1/min → ≤0.05/min) | rises above Phase A rate | `grep XRAY_DIR_FLIP data/logs/workers.log` |
| M2c XRAY is_structurally_invalid flag fires | YES on at least 1 placement per ~10 min where price near resistance | always False (clamp inert) OR always True (clamp over-firing) | `grep is_structurally_invalid data/logs/workers.log` (XRAY_LEVELS DEBUG) |
| M3a Buy WR (DB trade_log) | ≥ 40% | <35% over 48h | `sqlite3 data/trading.db "SELECT direction, ...WR FROM trade_log WHERE created_at >= '<T0>'..."` |
| M3b Sell WR | ≥ 40% | <35% over 48h | same |
| M3c Counter-LONG WR (if identifiable) | ≥ 30% | <25% over 48h | join trade_log with thesis or filter by close_reason patterns |
| M4 Trades per hour | ≥ 0.8× Phase A baseline | <0.5× baseline | `wc -l` on BYBIT entries |
| M5 Session PnL | ≥ 0.8× Phase A baseline | <0.5× baseline | `sqlite3 SUM(pnl_usd)` |
| M6 All shipped fix sentinels | All 4 firing on each restart | Any missing | per-sentinel grep |
| M7 Shadow E2E | passes (if exists) | fails | shadow test script |
| M8 DB cascades | 0 new `DB_LOCK_WAIT` | any cascade | `grep -c DB_LOCK_WAIT data/logs/general.log` |

## Decision matrix at T0+48h

| Outcome | Verdict | Action |
|---|---|---|
| M1 ∈ [40, 80] AND M2 ∈ [40, 80] AND M3a, M3b ≥ 40% | **CLEAN PASS** | Phase B+C complete. Hold defaults. Optionally tune haircut from 0.5 to 0.7 for more permissive labels. |
| M2 still ≥ 85% Sell despite M1 balanced | **PARTIAL — XRAY flips still binding** | Investigate: are XRAY flips still firing at high rate? If yes, lower `tp_min_distance_pct` to 0.3 OR raise `xray_dir_flip_threshold_ratio` from 3.0 to 5.0 (operator decision). |
| M1 ≥ 90% Sell still | **PHASE B INERT** | Raise haircut from 0.5 to 0.7 or 1.0 in `[scanner.labeller]`. |
| M3a Buy WR < 35% | **HARD REVERT** | Revert Phase B+C (git revert merge commits + restart). Investigate which fix caused Buy WR collapse. |
| M3c counter-LONG WR < 25% (sample ≥ 20) | **HARD REVERT Phase A2** | Revert Issue 2 counter mult to 0.7 (`git checkout main~5 -- config.toml` for the counter_confidence_multiplier line). |
| M5 < 50% baseline | **HARD REVERT BOTH** | Revert Phase B + Phase C. Reassess. |
| Any M6 sentinel missing | **REGRESSION** | Revert and diagnose. |

## Per-fix aim-bias evaluation (cumulative)

All four fixes pass the five-question aim-bias test:

| Fix | Q1 freq | Q2 aggression | Q3 quality | Q4 passive-close | Q5 separation |
|---|---|---|---|---|---|
| Issue 4 (Phase A1) | YES | YES | YES | N/A | YES (L2) |
| Issue 2 Concern 7 (Phase A2) | YES | YES | YES | YES | YES (L1B) |
| Issue 3 (Phase B) | YES | YES | YES | N/A | YES (L1D) |
| Issue 1 (Phase C) | YES | YES | YES | YES | YES (L1B) |

## Rollback procedures (per phase)

| Phase | Revert command |
|---|---|
| Phase A1 (Issue 4) | `git revert 2016528` |
| Phase A2 (Issue 2 Concern 7) | `git checkout e250ec4~1 -- config.toml` (restore counter_confidence_multiplier=0.7) |
| Phase B (Issue 3) | `git revert 161fae2` |
| Phase C (Issue 1) | `git revert 2864216` |
| All four | `git reset --hard 5b69233` (back to pre-Phase-A main) — destructive, use only if all-up revert needed |

All require `sudo systemctl restart trading-workers trading-mcp-sse` after the change.

## Operator next steps

1. Re-enable Layer 2 + Layer 3 via the telegram dashboard when ready.
2. Phase B + C trial begins on first CALL_A after layers re-enable.
3. Measure 48h per the metric table above.
4. Apply decision matrix.
5. If CLEAN PASS, the project's direction-bias series is complete.

## Status of Phase 7 (final integration verification)

Phase 7 begins after the 48h Phase B+C trial. Verifies:
- All 4 fixes still firing at boot
- Direction distribution responds to market regime proportionally (no hardcoded amplification)
- Both Buy and Sell WR ≥ 45% over the trial window OR honestly tracks regime (one ≥ 55%, the other allowed lower)
- Total PnL not degraded vs pre-fix baseline
- All shipped sentinels intact
- No DB cascades
- Shadow still works

Output: `dev_notes/dirbias_validation/phase7_final_verification.md` (to be written after Phase B+C trial completes).
