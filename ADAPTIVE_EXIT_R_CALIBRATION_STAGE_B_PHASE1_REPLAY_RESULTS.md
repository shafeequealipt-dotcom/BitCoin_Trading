# Adaptive Exit R-Calibration — Stage B Phase 1 Replay Results

This records the result of the faithful, gateway-driven replay built for Stage B Phase 1
(simulate_trail_recalibration_replay.py), run on the captured 28-hour window. It is evidence to
be read at the gate before any code goes live. It contains no production code change. The
headline is that the replay redirects the priority of Phase 1: the trail recalibration the
operator approved is real but marginal, and the placeability leak is the dominant lever by close
to two orders of magnitude. The recommendation is to reorder Phase 1 so the placeability fix
leads and the trail cap follows as a small, safe refinement.

## How the replay works and how far it can be trusted

The harness reconstructs every trade in the window from the logged truth — entry, direction and
the initial stop from THESIS_OPEN, the per-second realized price path from PRICE_PATH keyed by
order id, the realized close from THESIS_CLOSE, and the movement unit R from the trade's
LADDER_ADAPTIVE lines. It walks each trade tick by tick and, at each new running-peak high, calls
the real SLGateway.apply with the candidate geometry's lock as the proposed stop, the resting
placed stop as the current stop, the logged price as the current price, the trade's R and class
through the volatility-profiler interface, and the source set to the ladder source with the
profit-lock and breakeven floors passed exactly as production passes them. The gateway's real R2
clamp, profit-lock exemption, tighten-only re-check, fresh-mark degrade and terminal guard all
run unmodified; the accepted stop is what would be placed, and the trade exits when the price
crosses that resting placed stop. Of 397 closed trades, 299 reconstructed cleanly; 96 were
dropped for having no LADDER_ADAPTIVE R in their window and two for no close, reported honestly.

Two self-checks support trust in the harness. First, the baseline candidate's lock formula
reproduces the production vol_scale.profit_lock_pct to the digit (for example BEAT at peak 1.902
and R 1.842 returns a lock of 0.981, matching the live log line). Second, the baseline replayed
against the actual realized closes has a mean absolute error of 0.179 percent overall, and the
high-peak cohort's mean matches reality almost exactly (gateway 0.853 percent versus actual 0.855
percent). The known fidelity limit is stated plainly: the per-second price path cannot resolve the
roughly 150-millisecond live-mark latency or the sniper's roughly five-second call cadence, so the
fresh mark is modelled by the next observed tick. This makes the replay OPTIMISTIC about placement
on fast moves — it places some locks reality could not — so its capture figures are an upper bound,
not a promise. That direction of error is the safe one for this analysis and, as shown below, does
not change the conclusion.

## Result one — the trail recalibration is real but marginal

All figures are sums of per-trade pnl percentages net of the round-trip fee across the 299 trades.
They are not dollar profit and loss; trade sizes differ and 100 trades were dropped, so this is an
apples-to-apples per-trade-percent basis, not the window's dollar bottom line.

Through the real gateway, the candidates land within about one percent of each other. The baseline
trail of half R nets 59.57 percent at a 67 percent win rate. A flat smaller trail fraction of 0.4
nets 59.36 and of 0.3 nets 59.82. An absolute cap on the trail distance of 0.50 percent nets 60.19,
of 0.40 percent nets 60.24, and of 0.30 percent nets 59.47. The best candidate, the absolute cap at
0.40 percent, beats the baseline by 0.67 percent over 299 trades. The small cohort is not regressed
by any candidate: trades peaking under 0.5 percent keep 31 percent of their peak under both the
baseline and the tighter candidates, confirming the operator's concern about over-tightening the
small movers does not materialize for the cap, which by construction leaves them untouched.

A second, instructive detail: the very tight flat trail of 0.3 computes locks so close to the peak
that the gateway's own minimum-distance rule clamps many of them away — its idealized perfect-
placement capture is only 54.0 percent but its gateway capture is 59.8 percent, because the gateway
refuses to place the over-tight locks and the trades ride. In other words the gateway already guards
against an over-tight trail, which is another reason a flat reduction of trail_r is the wrong lever.
The absolute cap is the clean form: it tightens only the high-R movers and leaves the rest exactly
as they are.

The BEAT worked example carries the same message. The baseline captures a net 1.190 percent and the
tightened candidates capture 1.268 percent — an improvement of under 0.08 percent on the trade that
motivated the whole investigation. The trail geometry is simply not where the give-back lives.

## Result two — placeability is the dominant lever, by close to two orders of magnitude

The same 299 trades in reality netted 27.47 percent at a 42 percent win rate. The gateway replay,
which places the computed locks far more successfully than reality did, netted 59.57 percent at a 67
percent win rate on the identical geometry. The difference — about 32 percent of net and 25 points
of win rate — is the placeability gap: locks that were computed correctly but, in reality, failed to
reach the exchange because price moved inside the minimum distance on a fast move and the fresh-mark
degrade held the looser stop. This gap is concentrated in the small and mid movers reversing into
losses because their breakeven and small-profit locks never placed, and in individual big movers
such as BEAT, whose computed 0.98 percent lock became a 0.53 percent realized close.

Set the two levers side by side. The trail recalibration moves net by about plus 0.67 percent and
the win rate not at all. Closing the placeability gap moves net by up to about plus 32 percent and
the win rate by up to 25 points. Even discounting heavily for the replay's per-second optimism — the
true recoverable amount lies somewhere between today's 27.47 percent and the replay's 59.57 percent,
not at the top — the placeability lever is larger than the trail lever by a wide margin. The exit's
remaining cost is overwhelmingly that good locks are not being placed, not that the locks are
computed too wide.

This is exactly the entanglement the operator named at approval: a better-computed lock that cannot
be placed captures nothing. The replay quantifies it: the better-computed trail buys almost nothing
on its own precisely because placement, not computation, is the binding constraint.

## Recommendation — reorder Phase 1

The evidence says to lead Phase 1 with the placeability fix and follow with the trail cap, rather
than the reverse. Concretely:

First, the primary work of Phase 1 should be the fast-move placeability of the ladder lock: the
2,370 fresh-mark-degrade no-ops identified in the diagnosis, where a placeable, profitable lock is
foregone because price moved inside the minimum distance against the fresh mark. The design of that
fix is the next deliverable and will be brought to the gate before any live change. It must not
weaken the wire-fail safety the fresh-mark degrade exists to provide; if recovering the fast-move
lock is found to require weakening that safety, that is an escalation to the operator, not a fix
made here. Because the per-second replay cannot fully resolve the sub-second placeability behaviour,
that fix will also need a verification that does not depend on per-second fidelity — for example a
targeted unit-level test of the degrade path on constructed fast-mark sequences, alongside live
observation after it ships.

Second, the trail cap remains worth shipping as a small, safe, non-regressing refinement — the
absolute cap on the trail distance near 0.40 percent, introduced as one bounded config key
defaulting to off — but it should be recognized as a refinement worth well under one percent, not
the main event, and it should ship after or alongside the placeability fix, not instead of it.

The rung and staged-secure lever was examined and is not pursued in Phase 1: on the high-R movers
the 3R secure rung is rarely reached, so it does not engage for the coins that give back, and no
rung change improved the replay without touching the small movers.

## What stays fixed regardless

R is unchanged. The catastrophic cap and the hard stop are untouched. The owner hierarchy, the
universe system, and the behavioral gates are unchanged. Any change ships as one atomic, revertible
commit on main with a timestamped backup, a boot-sentinel readout of the loaded value, and a forced
catastrophic-stop test confirming the cap still fires. Nothing goes live before its own gate.
