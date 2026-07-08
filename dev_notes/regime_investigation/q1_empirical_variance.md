# Q1 Step 1.5 — Empirical Per-Coin Variance (48h Window)

## Method

Concatenated 7 workers log files spanning 2026-05-10 05:03 to 2026-05-12 07:47 (UTC). Parsed all `REGIME |` lines into `(timestamp, symbol, regime, conf, adx, choppiness)` tuples. Grouped by 5-minute timestamp bucket. For each bucket, counted distinct regime labels across the symbols present.

Files used:

```
data/logs/workers.log
data/logs/workers.2026-05-11_22-34-42_859953.log
data/logs/workers.2026-05-11_17-35-08_280673.log
data/logs/workers.2026-05-11_11-55-43_739853.log
data/logs/workers.2026-05-10_17-00-45_779645.log
data/logs/workers.2026-05-10_07-19-00_526602.log
data/logs/workers.2026-05-10_05-03-25_891314.log
```

## Headline numbers

- Total `REGIME |` emissions: **7508**
- Distinct 5-minute buckets: **159**
- Buckets with exactly **1** distinct regime across all symbols: **12 (7.5%)**
- Buckets with **>1** distinct regimes: **147 (92.5%)**

The 92.5% divergence rate is decisive evidence that the detector classifies per-coin, not as a single market-aggregate label pasted onto every symbol.

## Distribution of distinct-regime counts per bucket

| Distinct regimes in bucket | Bucket count | Share |
|---|---|---|
| 1 | 12 | 7.5% |
| 3 | 14 | 8.8% |
| 4 | 33 | 20.8% |
| 5 | 100 | 62.9% |

In 62.9% of buckets, all 5 possible regime labels (`ranging`, `trending_up`, `trending_down`, `volatile`, `dead`) appear simultaneously across the watch_list. This is the opposite of market-aggregate behavior.

## Sample buckets (multi-label)

```
2026-05-11 22:35  50 symbols  {ranging: 42, dead: 1, volatile: 3, trending_down: 3, trending_up: 1}
2026-05-11 22:40  50 symbols  {ranging: 42, dead: 1, volatile: 3, trending_down: 3, trending_up: 1}
2026-05-11 22:45  50 symbols  {ranging: 42, dead: 1, volatile: 3, trending_down: 3, trending_up: 1}
2026-05-11 22:50  50 symbols  {ranging: 42, dead: 1, volatile: 3, trending_down: 3, trending_up: 1}
```

Note the stability of the distribution across consecutive 5-minute buckets — typical for hourly-kline-based regime detection with 2-tick hysteresis. The same symbol mostly keeps the same label; different symbols carry different labels.

## ELSE-fallback signature (`conf=0.40`)

The classifier emits `confidence = 0.4` only for the `else` branch (lines 153-156 of `regime.py`). All other branches produce a confidence derived from the indicator value, so `conf=0.40` is a unique fingerprint of the fallback.

| Metric | Value |
|---|---|
| Total emissions | 7508 |
| Ranging-labeled | 5728 (76.3% of all) |
| Ranging with `conf=0.40` (ELSE fallback) | **5552 (96.9% of ranging, 73.9% of all emissions)** |
| Ranging from explicit strict branch (adx<20 AND chop>60) | 176 (3.1% of ranging) |

**Three-quarters of all regime emissions are produced by the `else = RANGING` fallback at lines 153-156.** Only 3.1% of ranging classifications pass the strict ranging criteria (ADX < 20 AND choppiness > 60).

## Confidence distribution by regime (top values)

| Regime | Top confidence | Count | Interpretation |
|---|---|---|---|
| RANGING | 0.40 | 5552 | ELSE fallback |
| RANGING | 0.82 | 42 | Strict branch with chop ≈ 66 |
| RANGING | 0.76 | 38 | Strict branch with chop ≈ 61 |
| TRENDING_UP | 0.51 | 37 | ADX ≈ 26 |
| TRENDING_UP | 0.63 | 33 | ADX ≈ 32 |
| TRENDING_DOWN | 0.58 | 66 | ADX ≈ 29 |
| TRENDING_DOWN | 0.57 | 55 | ADX ≈ 29 |
| VOLATILE | 1.00 | 96 | `atr_percentile/200 ≥ 1.0` — typically `volume_ratio > 2.0` AND high natr |
| VOLATILE | 0.25 | 37 | volume-ratio-only entry; low atr_percentile |
| DEAD | 0.80 | 362 | Hardcoded for dead branch |

Most trending classifications cluster around `conf ≈ 0.50-0.65`, reflecting ADX values just barely above the 25 threshold. Trending regimes are "weakly" labeled relative to ranging's hardcoded 0.40.

## Per-symbol ranging share (top 10 by sample count)

| Symbol | Samples | Ranging | Distribution |
|---|---|---|---|
| BTCUSDT | 305 | 78.4% | ranging 239, dead 57, volatile 6, trending_up 3 |
| ETHUSDT | 147 | 36.1% | dead 81, ranging 53, volatile 13 |
| SOLUSDT | 147 | 92.5% | ranging 136, trending_up 10, volatile 1 |
| BNBUSDT | 147 | 66.7% | ranging 98, dead 48, volatile 1 |
| XRPUSDT | 147 | 66.7% | ranging 98, dead 24, volatile 24, trending_up 1 |
| ADAUSDT | 147 | 92.5% | ranging 136, trending_up 10, volatile 1 |
| DOGEUSDT | 147 | 90.5% | ranging 133, volatile 13, dead 1 |
| AVAXUSDT | 147 | 84.4% | ranging 124, trending_up 10, volatile 13 |
| LINKUSDT | 147 | 90.5% | ranging 133, dead 12, trending_up 1, volatile 1 |
| ARBUSDT | 147 | **99.3%** | ranging 146, volatile 1 |

Range across the watch_list: 36.1% (ETHUSDT) to 99.3% (ARBUSDT). This per-symbol variance is further evidence of per-coin classification — symbols differ from each other systematically. ETHUSDT is the standout, which classifies as `dead` more than `ranging`.

## Definitive answers

1. **Is the detector per-coin or market-aggregate?** Per-coin. 92.5% of 5-min buckets show >1 distinct label across symbols. Per-symbol distributions vary from 36% to 99% ranging.

2. **Is the `ranging` label honest?** No. 96.9% of ranging classifications come from the `else` fallback (`conf=0.40`), not from the explicit ranging criteria. The strict-ranging criteria (ADX < 20 AND chop > 60) fire only 3.1% of the time.

3. **What does the ELSE fallback typically look like?** The classifier reaches the fallback when the coin doesn't satisfy any of the 5 explicit criteria — typically ADX in [15, 25] with choppiness in [45, 60]. These are transitional / weak-trend zones, which the detector lumps into `ranging`.

## Implication

If `ranging` is the default for "no clear pattern" rather than for "true mean-reversion", then `ranging` is not a meaningful gate. Consumers of regime that branch on `ranging`:

- APEX direction lock does NOT fire → XRAY can flip
- Stage 2 strategist prompt receives `[RANGING 40%]` tag → may steer brain toward mean-reversion/Sell
- Ensemble selects `mean_reversion` + `funding_arb` instead of momentum strategies
- Scanner withdraws the +10 trending bonus

The combined effect on a coin that is weakly trending but labeled `ranging`: brain is steered toward mean-reversion logic, structural R:R may favor the opposite direction of the weak trend, APEX doesn't lock, and XRAY flips. This is the mechanism for Q1b's flip-causation chain.
