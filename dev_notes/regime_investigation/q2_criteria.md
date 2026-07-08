# Q2 Step 2.1 — Objective Trending vs Ranging Criteria

These are the criteria used by `scripts/regime_accuracy_probe.py` to classify the objective regime over a 30-minute window of 5-minute candles. Defined to be testable, deterministic, and standard for crypto-norm price-action analysis.

## Inputs

For a candidate timestamp T (rounded down to the nearest 5-minute boundary), the criteria consume:

- **Before window**: 6 5-min candles ending immediately before T. Reflects what was happening immediately prior to the regime classification.
- **After window**: 6 5-min candles starting at T. Reflects what actually happened after the classification (validation only — not used for confusion matrix).
- **ATR(14)**: mean true range over the 14 5-min candles immediately preceding the before window. Provides a per-symbol volatility scale.

## Classification rules

Applied to the **before window**. Symbols:

- `total_change = closes[-1] - closes[0]` (net 30-min close change)
- `abs_change = abs(total_change)`
- `rng = max(highs) - min(lows)` (full bar range across window)
- `up_steps` = number of candles where close > previous close
- `down_steps` = number where close < previous close
- `max_dd` = maximum drawdown from running peak across the window
- `max_rally` = maximum rally from running trough

Rules (first match wins):

1. **TRENDING_UP**: `total_change >= 1.0 * ATR` AND `up_steps >= 4` AND `max_dd < 0.7 * ATR`
2. **TRENDING_DOWN**: `total_change <= -1.0 * ATR` AND `down_steps >= 4` AND `max_rally < 0.7 * ATR`
3. **RANGING**: `abs_change <= 0.8 * ATR` AND `rng <= 2.0 * ATR`
4. **WEAK_TRENDING_UP**: `total_change > 0.5 * ATR` AND `up_steps >= 3` (and didn't qualify for strict trending_up)
5. **WEAK_TRENDING_DOWN**: `total_change < -0.5 * ATR` AND `down_steps >= 3`
6. **OTHER**: anything else (transitional, spike-and-revert, mixed)

## Rationale

The strict-trending criteria require a 1-ATR net move with directional consistency and limited pullback. This matches what a trader would describe as a "real trend" on a 30-min scale.

The ranging criteria require both the net move AND the full bar range to be small relative to ATR. A random walk on 6 5-min candles produces an expected range of approximately `sqrt(6) * ATR = 2.45 * ATR`. The 2.0×ATR range cap is therefore tighter than a typical random walk, ensuring "ranging" means meaningfully flat.

The `weak_trending` bucket captures directionally consistent moves that aren't strong enough for the strict category. These are partially-correct labels — the detector might say "ranging" and the market is weakly trending in one direction.

The `other` bucket holds everything that doesn't fit cleanly. These are typically choppy / transitional / spike-then-revert patterns. Neither clearly trending nor clearly ranging.

## Mapping to detector labels for confusion matrix

Detector labels are normalized to a comparison space:

- `trending_up` (detector) → compared against objective `trending_up`
- `trending_down` (detector) → compared against objective `trending_down`
- `ranging` (detector) → compared against objective `ranging`
- `volatile` and `dead` (detector) → bucketed as `other` for the comparison

Per the spec, `volatile` and `dead` are not the central question. They are reported separately where relevant.

## Refinements considered and rejected

- **Even stricter ranging** (range < 1.2 × ATR): tested initially, produced 100% false-ranging because no real markets stay that flat over 30 min. Rejected as unrealistic.
- **ATR(7) instead of ATR(14)**: tested for robustness; produced similar results (within 5 percentage points on the false-ranging rate). Stayed with ATR(14) for parity with the detector's choppiness/ATR usage.
- **15-minute window instead of 30-minute**: too short to distinguish trend from noise. 30 minutes is the smallest window where 1-ATR moves are detectable from noise.
- **Volume-weighted criteria**: deferred; the volume signal is already used by `volatile` and `dead` detector branches and would complicate the apples-to-apples comparison. Could be revisited in a follow-up.

## Limitations

- The objective regime reflects only immediate (30-min) price action. The detector uses 200 H1 candles (~8 days). The two are intentionally measuring different time horizons. The mismatch tells us whether the detector's H1-based label matches the immediate trade-execution horizon — which is the relevant question for XRAY flip decisions made on 5-min trades.
- Sample size: 96 valid samples after stratification. Sufficient to characterize the headline false-ranging rate but not to make per-symbol claims with tight confidence intervals.
- The 48-hour window included a period of overall bearish drift. The asymmetry in the trending_up vs trending_down counts (23 vs 34 in mislabeled samples) reflects the market regime during the window, not necessarily a permanent characteristic.
