# Phase B results — all six candidates, complete 172-symbol / 2-year dataset

Run 2026-09-27, backfill fully complete (172/172 symbols). Every number below
is the out-of-sample (last of 3 time folds) result at real market-order cost
(0.17% round trip) unless marked otherwise. Reproduce with
`research/.venv/bin/python -m research.run_strategy <name>` on the server.

## Summary

| # | Strategy | OOS expectancy | t-stat | Beats mirror? | Beats random? | Verdict |
|---|---|---|---|---|---|---|
| B1 | Trend/breakout (Donchian, 4h) | −0.062% | −1.61 | **yes** | **yes** | FAIL |
| B2 | Cross-sectional momentum (daily) | **+0.042%** | 0.15 | **yes** | **yes** | FAIL (not significant) |
| B3 | Funding-rate fade | −0.120% | −8.03 | yes | no | FAIL |
| B4 | Open-interest shock fade | −0.343% | −16.78 | no | no | FAIL |
| B5 | Fade the volume/volatility surge | −0.298% | −15.80 | no | no | FAIL |
| B6 | Current LLM brain (replayed) | −0.496% | −2.05 | no | no | FAIL |

**All six fail the pre-registered bar.** Per the plan, none gets a pass.

## What's worth noticing anyway

- **B1 and B2 are qualitatively different from B3–B6.** Both show real
  separation from randomness — B1 beats mirror (−0.33%) and random (−0.26%)
  by a wide margin despite still being net-negative itself; B2 is net
  *positive* (+0.042%, and +0.17% at maker cost) and beats both baselines by
  0.7–1.0 percentage points. Neither clears the significance bar (t-stat,
  edge ratio), but neither looks like noise either — they look like a real,
  small effect that needs a better exit or more data to prove out, not a
  demonstrably absent one.
- **B4 and B5 fail in the wrong direction relative to their own hypothesis.**
  For both, MIRRORING the signal (i.e., doing the opposite — following the
  move instead of fading it) scores better than the tested version. That's a
  hint the underlying phenomenon may be momentum continuation, not
  reversal — a distinct, new hypothesis, not a re-tuning of B4/B5 as tested
  (which stay FAIL, final, per the plan's no-re-tuning rule).
- **B3's earlier "promising at maker cost" preview does not hold on the full
  data** (t=0.66 on the complete set vs. the t=4.07 seen on ~1/3 of the
  universe). Correcting that here for the record.
- **B6 is the clearest result of the six**: the current brain loses to
  both a coin flip and its own mirror image. This is the strongest single
  confirmation of the plan's root-cause finding (no-entry-edge-2026-09-27).

## Decision point

Per the plan's own stop rule: if nothing passes, the honest recommendation
is to stop trading this system as currently built, rather than continue
live paper-trading it. That recommendation stands for the system as tested.

B1 and B2 are a genuine, narrower question: is it worth ONE more
principled round on those two specifically — e.g., B1 with a trailing exit
instead of a fixed target (a different, standard trend-following exit
style, not a threshold re-tune), and B2 with a refined ranking or longer
sample? This is the operator's call, not something to proceed on
unilaterally — continuing to iterate without an explicit go-ahead is
exactly the pattern the promotion bar exists to prevent.
