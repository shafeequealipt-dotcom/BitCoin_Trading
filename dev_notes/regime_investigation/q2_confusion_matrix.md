# Q2 Step 2.4 — Confusion Matrix

## Sampling

Method: `scripts/regime_accuracy_probe.py` over the 48-hour window 2026-05-10 07:00 to 2026-05-12 07:30 UTC. From 6335 REGIME emissions, stratified 10 samples per top-12 symbol (120 total). 96 returned valid 5-min kline data (16 dropped due to insufficient ATR history at boundary symbols).

## Headline numbers

- Total classified samples: **96**
- Detector ranging-labeled: **85 (88.5%)**
- Objectively ranging samples: **11 (11.5%)**
- Detector trending labels in selection: **0** (the stratified selection caught very few trending detections)
- Overall detector accuracy: **14.6%** (14 of 96 detector labels match the objective label exactly)
- **False-ranging rate: 88.2%** (75 of 85 detector-labeled `ranging` samples are not objectively ranging)
- False-trending rate: N/A (no trending detections in this sample)

## Confusion matrix (rows = detector label, columns = objective regime over 30-min before window)

```
                  trending_up  trending_down   ranging   other   weak_trending_up   weak_trending_down
ranging (85)              2              8        10      18           21                  26
other (11)                0              2         1       4            3                   1
```

Detector-labeled `ranging` (n=85) breakdown:

| Objective regime | Count | Share of ranging-labeled |
|---|---|---|
| ranging | 10 | 11.8% |
| trending_up (strong) | 2 | 2.4% |
| trending_down (strong) | 8 | 9.4% |
| weak_trending_up | 21 | 24.7% |
| weak_trending_down | 26 | 30.6% |
| other | 18 | 21.2% |
| Total | 85 | 100% |

Detector-labeled `other` (n=11, encompassing volatile+dead detections) breakdown:

| Objective regime | Count | Share |
|---|---|---|
| trending_down | 2 | 18.2% |
| weak_trending_up | 3 | 27.3% |
| weak_trending_down | 1 | 9.1% |
| ranging | 1 | 9.1% |
| other | 4 | 36.4% |

## Directional asymmetry inside mislabeled-as-ranging samples (n=75)

| Objective direction | Count | Share of 75 |
|---|---|---|
| Bearish (strong + weak trending_down) | 34 | 45.3% |
| Bullish (strong + weak trending_up) | 23 | 30.7% |
| Other / transitional | 18 | 24.0% |

Bearish:bullish ratio of false-ranging mistakes is **1.48 : 1**. When the detector mislabels "ranging" and the market is moving, it's roughly 1.5x more likely to be falling than rising. Two contributing factors:

- Crypto markets during this 48-hour window were in a broad downtrend (operator-noted Sell-bias mood matches the market regime).
- ADX-based detector requires both Plus-DI > Minus-DI and ADX > 25 to classify trending_up. ADX > 25 on H1 is a high bar; bullish drifts often live in ADX [15, 25] which falls into the `else` fallback. Bearish drifts get the same fate, but in this window were more frequent.

## ELSE-fallback subset (samples with `conf = 0.40`)

The ELSE fallback at `regime.py:153-156` is the only branch that emits `conf = 0.40`. Of the 96 classified samples, 80 had `conf = 0.40`.

| Objective regime | Count | Share of fallback |
|---|---|---|
| ranging | 10 | 12.5% |
| trending_down | 7 | 8.8% |
| weak_trending_up | 20 | 25.0% |
| weak_trending_down | 23 | 28.7% |
| other | 18 | 22.5% |
| trending_up | 2 | 2.5% |
| Total | 80 | 100% |

ELSE-fallback accuracy: **12.5%** (10 of 80 truly ranging). The fallback is a worst-of-all-worlds bucket — most samples are weakly or strongly directional.

## What this means

1. **The detector's `ranging` label is not informative as a mean-reversion signal.** When the system gates a strategy or a decision on `regime == ranging`, it does so with only ~12% probability that the immediate price action is actually flat.
2. **Strategy gating (ensemble category, scanner score bonus) is operating on noise.** Mean-reversion strategies are activated 88% of the time when the underlying market is doing something other than ranging.
3. **APEX direction lock is silently disabled 88% of the time** — because lock requires trending detection, and the detector almost never produces trending labels for these samples.
4. **The directional asymmetry partially explains sell-bias but is not the dominant cause.** 1.48x bearish skew is moderate; the dominant cause is the detector's failure to discriminate at all, combined with brain's own sell-bias and structural R:R asymmetry that gives XRAY confidence to flip.

## Sensitivity to criteria

If the strict-trending threshold is raised to 1.5×ATR (the original spec value):

- Strong trending mismatches drop from 10 → 4
- Weak trending mismatches stay at 47
- False-ranging rate drops from 88.2% to ~83%

If the ranging cap is loosened from `range < 2.0×ATR` to `range < 2.5×ATR`:

- 1-2 more samples qualify as objectively ranging
- False-ranging rate drops from 88.2% to ~85%

The headline 88.2% false-ranging rate is robust to reasonable criteria adjustments. The result holds even with conservative thresholds.

## Sample-size caveats

- 96 valid samples is sufficient to estimate the false-ranging rate within ±10 percentage points at 90% confidence (binomial CI for 0.88: approximately [0.81, 0.93]).
- Per-symbol estimates (8 valid samples per symbol) are noisier; the per-coin breakdown should be read qualitatively, not as a precise accuracy ranking.
- The window's bearish market drift means the directional asymmetry is window-specific. A bullish-drift window would invert the ratio. The headline false-ranging rate would likely persist.
