# Stage B Phase 1 — Throttle-Modeled Cadence Replay: Faithfulness Failure and What It Means

This records the result of the throttle-modeled replay built to size the fast-move placeability
fix (the faster profit-lock cadence). It is an honest negative result: the replay failed its
faithfulness gate, which means it cannot size the fix, and — more importantly — it disproves the
hypothesis that the 30-second cadence is the dominant cause of the placeability loss. No code is
shipped. This is evidence for the gate.

## What was built and run

The faithful gateway-driven replay (simulate_trail_recalibration_replay.py) was extended to model
the production throttle: a per-symbol placement clock so the profit-lock ladder evaluates the
spine every five seconds and may place only when the configured cadence has elapsed since the last
accept (mirroring the per-symbol rate-limit clock plus the sniper's eligibility short-circuit), with
the lock computed from the monotonic running peak and the exit checked every tick. The cadence was
swept over the per-second ceiling, 30 seconds (today's value), 15, 10, and 5 seconds, on the
baseline geometry, across the 299 reconstructable trades.

## The faithfulness gate failed

The gate was: at a 30-second cadence the model must reproduce reality on these trades (about plus
27.47 percent net and 42 percent win, on the per-trade-percent basis). It did not. The 30-second
model produced plus 54.94 percent net at 55 percent win — about plus 27 percent net and 13 points of
win rate above reality. The residual is the same order as the entire placeability gap the fix was
meant to address. Because the 30-second model cannot reproduce the 30-second reality, the replay
cannot be trusted to size what a faster cadence would recover.

The full sweep (net / win, with cohort net means small / mid / big): per-second ceiling
plus 59.57 / 67 percent (-0.005 / +0.253 / +0.853); 30 seconds plus 54.94 / 55 percent
(-0.037 / +0.191 / +0.965); 15 seconds plus 59.63 / 61 percent; 10 seconds plus 59.94 / 61 percent;
5 seconds plus 62.41 / 64 percent. Reality was plus 27.47 / 42 percent (-0.117 / +0.084 / +0.855).

## What the failure reveals: cadence is not the dominant lever

The decisive observation is that the 30-second model (plus 54.94) is barely below the per-second
ceiling (plus 59.57) — a difference of under five percent. In the model, throttling all the way down
to 30 seconds costs almost nothing, because the big-mover peaks are sustained for tens of seconds to
minutes and are therefore catchable even at a 30-second cadence. So within any per-second model the
cadence is a minor factor.

Yet reality (plus 27.47) sits far below even the 30-second model. The gap between reality and the
30-second model is therefore not explained by cadence at all — it is placement failure that the
per-second replay does not reproduce. Reading the cohorts, the big cohort is roughly reproduced
(model plus 0.965 versus reality plus 0.855 — the runner wash-out), but the small and mid cohorts are
not (model minus 0.037 and plus 0.191 versus reality minus 0.117 and plus 0.084). The unreproduced
loss is concentrated in small and mid movers whose breakeven and small-profit locks, in reality,
never reached the exchange — and that failure is present at every cadence in the model, so a faster
cadence does not address it in the model.

This corrects the earlier working hypothesis. The placeability gap is real and large, but it is not
primarily a cadence problem. It is a placement-failure problem: locks that reality could not place at
all. The most likely mechanism, from the diagnosis, is the structural collision between the trail
distance (half of R) and the gateway minimum distance (also about half of R), so the lock sits right
at the placeability boundary and is refused on the live mark — compounded by the live exchange mark
differing from the per-second shadow price the replay drives the gateway with. The replay cannot
reproduce either effect, because the captured logs do not contain the live exchange mark at tick
granularity; they contain the shadow price.

## Why the replay cannot be fixed to size this

The replay drives the real gateway with the per-second shadow price as both the caller snapshot and
the next-tick fresh mark. Production judges placeability against the live exchange mark
(position mark price), which is a different series the logs do not record per tick. The replay is
therefore systematically more lenient about placement than reality, by an amount (about plus 27
percent net) that swamps the cadence sensitivity it can measure (about plus 7.5 percent net from 30
seconds to 5 seconds). Making the fresh-mark proxy artificially more adverse to force a match would be
fitting the model to the answer, not validating it. The honest conclusion is that this data cannot
size the fix.

## Honest recoverable estimate

There is no trustworthy replay-based recoverable number. The model's cadence sensitivity (a gain of
about plus 7.5 percent net and nine points of win rate from 30 to 5 seconds) is a loose upper bound
that is confounded — a faster cadence also increases the model's own over-placement optimism, so part
of that gain is bias, not benefit. The honest position is that a faster cadence is safe and cheap and
may recover a modest, single-digit amount, but its true benefit is uncertain and cannot be
established from this data; only a live measurement can. The dominant placement failure is largely
cadence-independent and may be partly structural — bounded by the minimum-distance safety floor, which
is off-limits to weaken — so part of the gap may not be safely recoverable at all.

## Recommendation

The replay has set direction (placeability is a real, large gap, concentrated in small and mid movers)
and reached its data-fidelity limit (it cannot size the fix, and it shows cadence is not the dominant
lever). The honest next step is to measure the placement-failure mechanism on live data the logs do
not currently capture — the live exchange mark versus the caller snapshot at each ladder placement
attempt, the effective minimum distance, and the placed-versus-degraded-versus-no-op outcome with the
foregone tightening. That small observability addition (in the spirit of the existing observability
loggers) would size the real opportunity and reveal whether more placement attempts (the cadence fix)
or the trail-versus-min-distance collision is the binding constraint. The cadence fix itself, being
safe and cheap, can ship default-off alongside that observability so its benefit can be A/B measured
live — but it should not be presented as the proven recovery of the placeability gap, because the
replay does not support that and in fact argues against cadence being the dominant lever.

The safety analysis is unchanged and still holds: a faster cadence cannot weaken the wire-fail guards
(they run before the rate-limit with no bypass), cannot whipsaw (tighten-only), and cannot touch the
catastrophic cap (a force-close outside the gateway). The only honest change is to expectations about
how much it recovers, and to the diagnosis of what the dominant lever actually is.
