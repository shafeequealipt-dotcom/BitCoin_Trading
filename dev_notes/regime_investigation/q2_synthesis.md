# Q2 Synthesis — Is The Regime Detector Accurate?

## Headline numbers

- **Overall detector accuracy**: 14.6% (14 of 96 samples have detector_label == objective_label)
- **False-ranging rate**: 88.2% (75 of 85 `ranging` labels disagree with the objective 30-min regime)
- **ELSE-fallback accuracy** (samples with conf=0.40): 12.5% (10 of 80 are objectively ranging)
- **Best per-coin accuracy**: 62% (ETHUSDT, which uses `dead` branch frequently)
- **Worst per-coin accuracy**: 0% (BTCUSDT, ADAUSDT, NEARUSDT, XRPUSDT — all 100% ranging-labeled, 0% objectively ranging)
- **Directional asymmetry of mislabeled-as-ranging samples**: 1.48x more bearish than bullish (34 vs 23 in 75 mislabels)

## What's broken vs what's working

Working:

- Detector is unambiguously per-coin (Q1 confirms 92.5% bucket divergence).
- All inputs are per-symbol.
- Hysteresis prevents noise-driven flips.
- The `dead` and `volatile` branches do fire and do produce non-`ranging` labels when their criteria are met.
- When the detector correctly labels trending, the downstream protection chain (APEX direction lock → XRAY suppression) works correctly (INJUSDT at 23:17 case study).

Broken:

- The ELSE-fallback at `regime.py:153-156` is producing 73.9% of all regime emissions (5552 of 7508 in 48h window).
- Of those fallback emissions, only ~12% match objective ranging behavior.
- The strict-ranging branch (`adx < 20 AND chop > 60`) is so narrow that real ranging crypto markets rarely satisfy it.
- The strict-trending branch (`adx > 25 AND chop < 45`) is so narrow that weakly-trending markets fall through to the fallback.
- The trending_up label fires 2.2% of the time across the 50-coin universe. Trending_down fires 6.6% (3x more). The detector doesn't differentiate between "I see a trend" and "I really see a trend" — it only emits the label when the indicators have already moved far past the threshold, which is rare.

## Is the detector "good enough" or "fundamentally broken"?

**Narrowly broken: one branch (the ELSE fallback) dominates the output and produces uninformative labels.**

The detector is not fundamentally broken — its inputs are correct, its per-coin logic is correct, its hysteresis is correct, the four explicit branches are correct in concept. The issue is the gap in the criteria: a wide region of the (ADX, choppiness, ATR percentile) space has no explicit classification and falls into the `else` clause that says "call it ranging with low confidence."

A surgical fix to close that gap (Path B candidate B1) would convert most fallback samples into either:

- A new label like `transitional` or `unknown` (informative because consumers can branch on it differently)
- A re-derived `weak_trending_up` / `weak_trending_down` label using widened ADX/choppiness criteria
- A `ranging` label with criteria that match crypto-norm flat-market behavior (rather than the current strict definition that only applies to extreme low-ADX high-choppiness simultaneity)

## Why this matters for the Sell-bias

The Sell-bias mechanism, traced via Q1b:

```
Detector mislabels coin as ranging (88.2% rate)
                |
                v
APEX direction lock NOT applied (lock requires trending)
                |
                v
Brain decides Buy via its own signal mix
                |
                v
APEX preserves Buy (PRIMARY fix working — confidence gate)
                |
                v
XRAY sees strong structural R:R asymmetry (rr_flipped/rr_chosen > 3.0)
                |
                v
XRAY flips Buy → Sell
                |
                v
Trade places as Sell
```

The detector's high false-ranging rate is what removes the APEX direction lock from 88% of trade decisions. Without the lock, XRAY's threshold is the only gate against Buy → Sell flips. With the threshold at 3.0 and observed XRAY ratios ranging 5.7x to 668x, the gate is permissive.

Two corollary observations:

1. Even with a perfect detector, XRAY's structural R:R might still be a strong Sell signal in genuinely-trending-down markets. Path A (tune threshold) addresses this independently.
2. Even with a higher XRAY threshold, brain's own Sell-bias (61.6% Sell pre-flip) would remain. Path A and Path B together do not resolve brain's bias — that would require addressing the Stage 2 prompt construction, which is OUT OF SCOPE per the spec.

## Recommendation framework for Phase 3

Given the Q1 + Q2 + Q1b findings, the three paths have these expected impacts:

- **Path A (XRAY threshold tune to a higher value like 8-12x)**: would eliminate marginal flips (the 5.7x and 6.4x events) but allow the 24x and higher events through. Estimated Buy-share recovery: from 5-10% to 12-18%. Quick fix. Doesn't address the regime detector's failure.
- **Path B1 (eliminate ELSE fallback / widen detector criteria)**: most direct fix. If the detector starts emitting `weak_trending` labels for the band [ADX 20-25], APEX direction lock can fire on weakly-trending coins. Brain's Stage 2 prompt would receive a more informative tag. Scanner score bonus would apply correctly. Cascading positive effect. Larger code surface to change but each surface is small. Estimated Buy-share recovery: 20-35% based on the false-ranging breakdown.
- **Path C (Path B1 then re-evaluate Path A)**: most rigorous. After Path B1 deploys, re-measure: false-ranging rate, trending label share, XRAY flip count, Buy-share. If XRAY flips are still excessive, then add Path A. If Path B1 sufficient, skip Path A.

## What Phase 3's discussion report will say

The report will recommend Path C with B1 as the first action. Reasoning:

- Path A alone treats the symptom; the detector remains broken.
- Path B1 alone may not be enough if structural R:R asymmetries are independently driving XRAY flips even on correctly-labeled coins.
- Path C does the upstream fix first, measures, and then makes an informed Path A decision.

The operator may choose differently. The recommendation is documented but the operator's choice prevails.

## Limitations to disclose to the operator

- 96 samples is sufficient for headline numbers but not for per-symbol confidence intervals.
- The 48h window was bearishly-skewed; the directional asymmetry result is window-specific.
- Trade-outcome correlation is weak because few regime samples coincided with trades.
- The objective 30-min-window comparison is one of many possible measures; the result is robust to reasonable criteria adjustments (±5pp) but a different ground-truth definition could produce somewhat different numbers.
