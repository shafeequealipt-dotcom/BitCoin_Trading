# Adaptive Exit R-Calibration — Final Verdict (live-measured, adversarially verified)

This closes the adaptive-exit calibration program. It is grounded in live forensic data recorded
after the 2026-06-23 deploy and adversarially verified by an independent workflow that tried to
break each conclusion. It contains no new code change; its purpose is the proven cause and the
fix decision. Bottom line: the exit machinery works, the residual losses are entry-driven, the
placeability lever is already correct and should not be changed, and the one honest open item is
a re-measurement on a volatile window — not a code change.

## The arc that led here

Phase 0 proved R is measured correctly. The faithful gateway-driven replay proved the trail
recalibration is marginal (about plus 0.67 percent) and that the placeability leak was the
dominant lever in the replay. The throttle-modeled replay then failed its own faithfulness gate,
disproving cadence as the dominant cause and showing the real loss was placement failure the
per-second shadow-price logs could not reproduce — because production judges placeability against
the live exchange mark, which the logs did not carry. So we shipped, behaviour-neutral and
default-inert, a placement-forensics logger (which records the live mark versus the caller
snapshot, the min-distance, the outcome, and the foregone tightening) and an inert cadence key,
restarted the stack, and measured the mechanism live for about three hours.

## What the live data proves

Proven cause of the residual losses: they are entry-driven and market-driven, not placeability.
On a correct per-trade join, of the realized losers in the window the four largest all carry
entry or market reason codes and had zero refused tightening on the actual losing trade — RESOLV
minus 1.73 percent (catastrophic cap force-close), REUSDT minus 0.91 percent, ICP minus 0.75
percent, TAO minus 0.46 percent. For these the ladder never armed because price went straight
against entry; there was no profit lock to forgo, and the per-trade max forgone tightening was
between 0.10 and 0.22 percent, four to eight times smaller than the loss. The only two losers
touched by a refused tightening were both BELUSDT, and on a per-trade trace those exits are
instantaneous price gaps — the fill prints far above the in-profit marks with no intermediate
ticks — so a tighter trigger would have filled at the same gapped price. No placeability-caused
loss exists in the data.

The fix decision on the placeability and cadence lever: do not ship a change; the lever is
already correct. The fresh-mark degrade is doing the right thing. The snap-versus-mark divergence
it guards against was small in this window — median 0.017 percent, p99 0.198 percent, max 1.049
percent — and only 8 of 379 near-money attempts (2.1 percent) flipped wrong-side, every one of
which was clamped to a valid min-distance boundary rather than wired wrong-side. Outcomes were 373
clamp-noop and 125 placed, with zero wrong-side, zero wire-fail, and zero rate-limited across all
498 attempts. The largest-divergence symbol, DEXE, forwent 1.05 percent on one tick and recovered
to close plus 1.144 percent. There is no defect to fix, and loosening the min-distance floor would
only have allowed wrong-side wires the clamp currently absorbs — it would not have saved a single
loss. Keep the degrade and the clamp exactly as they are.

Where the remaining cost lives: entry-side and structural, out of scope for this exit program.
The exit is behaving. The remaining cost is in entry selection and in market gap and slippage risk
(the BEL vertical spikes, the cap force-close on RESOLV), both upstream of the profit-lock ladder
and below the min-distance safety floor we deliberately will not weaken. It is not recoverable by
tightening locks faster or relaxing placement guards. The honest read: leave the exit alone — it
works, and the losses are entries.

## Safety and neutrality — confirmed

The deployed code introduced no behaviour change and no safety regression. Post-deploy there were
zero ladder wire-fails, zero wrong-side wires, zero rate-limit rejects on any source (the
shared-clock caveat stayed dormant because the cadence is inert), and zero forensic-logger
failures or gateway exceptions. The two ladder wire-fails found in the logs predate the deploy.
The "wrong_side" string matches are all the name of a placeability check that passed, not a
wrong-side wire. The forensic try/finally piggyback altered no gateway decision; every forensic
outcome is backed by a gateway accept, reject, or fresh-degrade event. The verification ran live
on the real system, not a replay.

## Caveats, stated plainly

Sampling bias is real and material. This is a single calm window of about three hours, not a
volatile burst. Movers — symbols whose ladder peaked at or above 1 percent — are roughly seven to
eight times under-represented here versus the system's own history (about 13 percent of all
retained ladder rows historically, max peak 3.46 percent, versus under 2 percent here, max 2.30
percent). Divergence demonstrably scales with mover size: the at-or-above-1-percent bucket has a
median divergence about nine times the tiny-attempt median and a 33 percent flip rate. So the
headline "rare and tiny, about 2 percent flip" is a blended average dominated by quiet attempts
and understates what a volatile, mover-heavy window would show. The conclusion "placeability is
immaterial" is therefore established for calm regimes and must not be generalized to volatile
ones without the re-measurement below.

A correction to an earlier method, on the record: the prior per-symbol forgone attribution was
misleading because it borrowed forgone tightening from a symbol's winning trades, which made some
losers look placeability-touched when a correct per-trade join shows they were not. The per-trade
evidence is cleaner than the earlier framing, not worse.

## Recommendation

Ship nothing new on the exit. The trail cap (the marginal plus 0.67 percent refinement) is not
worth shipping now either — the live evidence says the exit geometry is not where the losses are;
hold it unless the volatile-window re-measurement changes the picture. Leave the
profit_lock_rate_limit_seconds key in place at 30, confirmed inert and harmless; removing it would
be churn. Keep the PLACEMENT_FORENSIC logging on permanently — it is behaviour-neutral, it failed
zero times, and it is the only instrument that can re-run this exact analysis over a genuinely
volatile, mover-heavy window.

The single highest-value next step is a re-measurement, not a code change: collect a forensic
window whose ladder peak density matches the historical roughly 13 percent of movers at or above
1 percent, and re-check the flip rate and the forgone-versus-realized-loss there. If that window
also shows the degrade clamping cleanly with no placeability-caused losses, the exit-side is
closed for good. If it shows movers stopping out at degraded levels, that is the data that would
justify revisiting the lever — and only then. For now: the exit works, the losses are the
entries, and the entries are a different program.
