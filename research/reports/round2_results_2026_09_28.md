# Round 2 results (approved focused round) — full 172-symbol / 2-year dataset

One principled change each to the two Phase B candidates that showed real
(not-yet-significant) separation from noise. Both fixed before running.

## B1v2: Donchian breakout + trailing stop (was: fixed target)

**PASSES the pre-registered bar. First strategy in this process to do so.**

| Metric | Value | Bar | Clears? |
|---|---|---|---|
| OOS trades | 51,965 | ≥ 100 | yes |
| OOS net expectancy (market cost) | **+0.1136%** | > 0 | yes |
| t-stat | **2.83** | ≥ 2.0 | yes |
| Edge ratio | **1.531** | ≥ 1.15 | yes |
| Folds net-positive | **3 / 3** | ≥ 2 / 3 | yes |
| Beats mirror (−0.22%) | yes | — | yes |
| Beats random (−0.05%) | yes | — | yes |

At maker/limit-order cost: +0.2436%, t=6.07 — stronger still, but that
number is conditional on Phase D (limit-order execution) actually working
as assumed; the market-cost result above is the one that already stands on
its own.

Every fold is individually positive, and monotonically shrinking
(+0.46% → +0.30% → +0.11%) rather than getting stronger — worth watching
in live paper trading (Phase F) rather than assuming the trend continues,
but three consecutive positive out-of-sample periods on a strategy this
simple is a real signal, not noise.

The one change from the failed original: exit style. Same entry (20-bar
Donchian breakout on 4h bars), same universe, same initial stop sizing
(1.5x ATR). Only the fixed 3x-ATR target was replaced with a standard
chandelier trailing stop (arms at 1x ATR favorable, trails 2x ATR behind
the running extreme). 60% of trades armed the trail at least once — the
other 40% stopped out on the initial stop before ever getting the chance,
consistent with breakout entries having a meaningfully-worse-than-even hit
rate (35.9%) that the survivors' larger average win offsets.

## B2v2: cross-sectional momentum, daily instead of weekly horizon

**FAILS — and worse than the original weekly version.**

OOS expectancy −0.264% (t=−4.55), all 3 folds negative, does not beat
either baseline. The hypothesis that a shorter horizon would surface more
independent samples and cleaner significance was wrong for this market and
this construction — daily crypto cross-sectional returns appear to be
noisier or more mean-reverting than the weekly signal, not more
persistent. B2 (the original, weekly, unproven-but-positive version) stays
exactly where Phase B left it: not promising enough to justify a further
attempt under the plan's one-change discipline. Closed.

## What this means

One approved, focused, honest round: one candidate now has a real result,
one doesn't. This is what the process is supposed to produce — not "more
things pass with enough tries," but a clear, defensible answer either way.
