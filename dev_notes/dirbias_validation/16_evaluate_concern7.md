# Phase 2.7 — Concern 7: ×0.7 counter multiplier should be REMOVED, not refactored

## Concern restated

The prior report's Issue 2 options preserve the ×0.7 suppression in different forms:
- Option 2.A: regime-adaptive multiplier (still hardcoded).
- Option 2.B: split direction-confidence from size-confidence (still cuts the size field).
- Option 2.D: data-calibrated multiplier (still has a multiplier).

A "remove entirely" option (set multiplier = 1.0) was NOT given equal consideration. The senior reviewer's concern: if counter trades are VALID (per directive), the multiplier may not belong at all.

## Evaluation

### Three concrete options for removal

**Option 7.1 — Config-only test** (lowest cost):
- Edit `config.toml:1724`: `counter_confidence_multiplier = 1.0`.
- Restart services.
- Producer at `structure_engine.py:1188, 1210` becomes effectively no-op (multiplies by 1.0).
- All 9 downstream consumers see un-cut confidence.
- Reversible in seconds: `git checkout config.toml && restart`.
- Cost: zero LOC. ~10 minutes for setup + 48h monitoring.

**Option 7.2 — Remove from code** (after config test confirms safety):
- Drop `* counter_mult` from `structure_engine.py:1188, 1210`.
- Remove unused `counter_mult` variable at line 1071.
- Mark `counter_confidence_multiplier` setting deprecated in `settings.py:2443`.
- Optionally remove the setting entirely (major-version bump).
- Cost: ~5 LOC edit. ~30 min for code + tests.

**Option 7.3 — Keep field, retire by default**:
- Change default in `settings.py:2443` from 0.7 to 1.0.
- Keep field for backward-compat / future re-introduction.
- Cost: 1 LOC. Minimal risk.

### Phase 1.2 finding — config test is feasible

The Phase 1.2 validation agent confirmed:
- `settings.py:__post_init__` validates `0 < counter_confidence_multiplier <= 1.0`. So 1.0 is within valid range.
- The producer at line 1188 becomes a no-op (`conf = base_conf * 1.0`).
- The 4 downstream floor-0.5 multipliers see the un-cut value:
  - `scorer.py:494`: counter conf jumps from 0.49 to 0.70 → factor = max(0.5, 0.70) = 0.70 (same as in-direction). Compounding eliminated.
  - `ensemble.py:158`: same. size_mult delta eliminated.
  - `scanner_worker.py:288`: same.
  - `apex/gate.py:218`: counter at 0.70 conf → no `*= 0.85` reduction (the elif branch fires only at `_xray_conf > 0` AND < 0.70, which 0.70 doesn't satisfy).
- Brain prompt at `strategist.py:1953` still appends "(COUNTER-TRADE — trade direction is OPPOSITE to market structure bias; lower conviction)" annotation. So Claude still sees the COUNTER context but at full confidence numerically.

### What changes immediately if multiplier = 1.0?

**At producer (structure_engine.py:1188)**:
- BULLISH_FVG_OB_COUNTER: confidence jumps from `min(mtf, smc) × 0.7 ≈ 0.21` (live mean) to `min(mtf, smc) ≈ 0.30`.
- BEARISH_FVG_OB_COUNTER: similar jump.

**At downstream consumers**:
- Scorer Quality factor: 0.50 (floored) → 0.50 (still floored, since 0.30 < 0.50). No change for very-low-conf counters. For counter conf ≥ 0.5, factor matches in-direction.
- Ensemble size_mult: same — floored at 0.5 if conf < 0.5; matches in-direction if conf ≥ 0.5.
- Scanner struct_norm: same.
- APEX gate conviction weight: counter at 0.30 → `weight *= 0.85` (in `_xray_conf > 0` branch). At 0.70 → `pass` (no weight cut). At 0.85+ → `weight *= 1.20`. So removing the multiplier ONLY helps counters whose underlying MTF/SMC is HIGH (rare).

**Net effect**: removing the multiplier helps counter trades MOST when their underlying MTF/SMC is strong. For counters with weak MTF/SMC (the typical case), the floor-0.5 clamp still floors them — so removing the multiplier doesn't make them more attractive.

This is an important nuance. **The multiplier mostly affects the HIGH-MTF/SMC counter setups** — the ones where structure genuinely supports the counter direction. Those are the cases where the multiplier is most clearly wrong (suppressing strong contrarian signal). Removing it lets those trades through.

### Risk assessment

**Risk if removed and counter trades have lower true WR**:
- More counter trades execute.
- Counter trade losses might worsen total PnL.
- Mitigation: 48h trial with revert threshold.

**Risk if removed and counter trades have similar/better WR**:
- More counter trades execute.
- More balanced direction distribution.
- Buy WR may improve (currently 41.8% over 14d — could be Buys are being selected from low-conviction pool because high-conviction Buys are counter-LONGs that get suppressed).

**Risk of NOT testing**:
- The 14d break-even WR persists.
- The asymmetric multiplier continues violating operator directive.
- The high-conviction counter setups continue to be wasted.

### Comparison to the operator directive

Operator directive: asymmetry from data, not numbers.
- Current `counter_confidence_multiplier = 0.7`: HARDCODED NUMBER. Violates directive.
- Removed (= 1.0): NO HARDCODED NUMBER. Honors directive.
- Option 2.B (split fields): hardcoded number still present in size-field path. Partially honors.
- Option 2.D (data-calibrated): hardcoded baseline but data-adjusted. Partially honors.
- **Option 7.1 (config = 1.0): cleanest fit to directive.**

### Empirical case for removal

Phase 1.8 finding: 14d Buy WR is 41.8%. If Buy trades are systematically routed through low-conviction high-noise channels (which the counter ×0.7 mechanism encourages), the low WR is a symptom of bad sample selection, not bad signal. Removing the multiplier might raise Buy WR by letting high-conviction counters through.

This is unprovable without the test. The test is cheap (TOML edit + restart).

### Caveats

1. **Counter trades have an annotation in the brain prompt** ("lower conviction"). Claude may still discount them subjectively. So the EFFECTIVE behavior change at brain may be smaller than the numeric change at the structure layer.

2. **MTF/SMC threshold filters** (`counter_mtf_threshold = 0.40`) still gate counter setups. Counters with very weak MTF will still be filtered out (good — that's the data-driven cut). Only counters with MTF ≥ 0.4 get classified at all.

3. **The 4 stacked floor-0.5 multipliers** still operate. For counter setups with raw MTF in (0.4, 0.5), the floor-0.5 clamp still applies — no change at sizing. Only counters with raw MTF ≥ 0.5 benefit from removing the multiplier.

In practice: removing the multiplier mostly helps the SUBSET of counter setups with strong underlying MTF/SMC (rare but high-quality). The bottom-tier counter setups (weak MTF) stay floored.

## Verdict

**VALID.** Removing the ×0.7 multiplier is the cleanest directive-aligned fix for Issue 2. Option 7.1 (config-only test) is the lowest-cost first move and instantly reversible.

## Recommendation

Ship Concern 7 in three sub-phases:
1. **Phase 7-1: config-only test** (zero LOC). Set `counter_confidence_multiplier = 1.0`. Run 48h with the metrics from Concern 6.
2. **Phase 7-2: ratify with code removal** (if 7-1 passes). Remove the multiplier from code. 1 commit.
3. **Phase 7-3: deprecate field** (cleanup). Mark setting deprecated. 1 commit.

If 7-1 fails (Buy WR < 35% or session PnL < 50% of baseline), revert and consider Option 2.B (split fields — preserves smaller suppression).

## Implications for fix path

- Pair Concern 7's Phase 7-1 (config-only test) with Issue 4 fix in Phase A of the recommended path.
- Both fixes can run in the same 48h trial — they don't conflict.
- If both pass at 48h, ratify both with code changes (Phase A2 commit for Issue 4 code + Phase 7-2 for Issue 2 removal).
