# PF/LC Top-15 Problem 3.3 — Chandelier full-band runner-capture tuning

## What this is

Problem 3.3 is the optimization payoff of the Problem 2.4 calibration. Problem
2.4 aligned the Chandelier trail's activation threshold with the ladder arm
(both 0.2 percent), so the Chandelier is now a candidate across the full
0.2-to-0.5 percent graduated band instead of sitting idle until 0.5 percent.
Problem 3.3 is the follow-up: after 2.4 has run live, confirm the Chandelier
adds genuine runner-capture across that band without prematurely winning the
spine on small moves, and fine-tune the trail width only if the measurement
shows it is needed.

This is a measure-then-tune item. There is no code change for it beyond the
already-centralized tunable. Doing it before 2.4 has produced live data would be
guessing, which the program forbids.

## Why there is no code change yet

The trail width is already centralized and tunable. The Chandelier distance is
base_atr_multiplier times the dialed ATR multiple times the regime factor. The
levers are, in order of preference:

- The base trail width: config.toml key base_atr_multiplier in the [mode4]
  section, currently 2.5. This is the most surgical lever — it widens or
  narrows the trail at all ages at once.
- The young end of the time-dial ATR multiple: config.toml key
  atr_multiple_young in the [profit_fetching] section, currently 3.0. Use this
  only if the base multiplier over-adjusts, because it also shifts the dial
  glide.
- The per-regime trail factors: these are NOT config keys for this trail. The
  profit-fetching Chandelier reads a hardcoded module constant,
  REGIME_TRAIL_FACTORS, in src/workers/profit_sniper.py (trending 1.3, ranging
  0.7, volatile 1.0, dead 0.6, balanced 0.85). The [mode4] regime_factor_*
  config keys are a legacy of the old trail and are not read by the
  profit-fetching trail, so changing them has no effect here. To re-balance a
  single regime, edit REGIME_TRAIL_FACTORS in code (a code change, not a config
  change). Only do this if one regime shows an outlier win rate in the band,
  and prefer the base multiplier above first.

The momentum and profit-decay parts of the trail must not be changed; they
drive the per-trade quality adaptation.

## The procedure (run after 2.4 has been live for at least a few days)

The blueprint's ladder-led character must hold: the Chandelier should win the
spine only on large, fast moves, while the ladder banks the ordinary climbs.
The measurement tells you whether the now-active trail respects that.

First, collect the spine-selection record. Grep the worker log for the
SNIPER_SPINE_SELECT lines over the measurement window. Each line names the
winner (ladder, chandelier, safety, or a loss candidate) and carries the trade
age and the age fraction.

Second, bucket the winners by the peak profit the trade had reached when the
line fired. The two buckets that matter are the 0.2-to-0.5 percent band (where
2.4 newly activated the Chandelier) and the above-0.5-percent band (the larger
moves the Chandelier is meant to catch).

Third, read the Chandelier win share in each bucket. The target shape is
ladder-led: the Chandelier should win only a small share inside the
0.2-to-0.5 percent band (roughly under one in ten), and a larger share on the
bigger moves above 0.5 percent. If the Chandelier is winning a large share
inside the 0.2-to-0.5 percent band, the trail is too tight and is stealing
ordinary climbs from the ladder — widen base_atr_multiplier a step (for example
from 2.5 toward 3.0). If the Chandelier almost never wins even on large fast
moves, the trail is too loose — narrow it slightly.

Fourth, change one lever by one step, then re-measure against the truthful
after-cost ruler over the next window, exactly as every other lever in this
program. Confirm that no winner that peaked in the 0.2-to-0.5 percent band has
its realized profit reduced versus the pre-tuning baseline. Revert the step if
it erodes capture.

## Safety

Any new trail candidate still passes through the stop-loss gateway, so it can
only tighten (rule R1) and can never place a stop inside the minimum distance.
The ladder floor already protects the 0.2-to-0.5 percent band, so there is no
naked exposure while this is being tuned. All profit-and-loss verdicts here are
provisional until the net-booking fix (Problem 1.3) has made the ruler truthful
and the three-to-five-day re-measurement confirms them.
