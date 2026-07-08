# Adaptive Exit R-Calibration — Phase 0 Diagnosis

This document is the Phase 0 diagnosis for the task brief in
TUNE_ADAPTIVE_EXIT_R_CALIBRATION.md. It establishes, from the code and the captured log
together, why the adaptive exit still gives profit back on the largest movers. It contains no
code change. It exists to be read and approved at the decision gate before any tuning is
designed. All evidence is drawn from the live adaptive-exit code and from the captured window
log_bundle_2026-06-17T0130_to_2026-06-18T0530_UTC.log, which covers 2026-06-17 01:30 to
2026-06-18 05:30 UTC, twenty-eight hours of real bybit_demo paper trading on the main branch.

## Summary of the finding

The brief's central premise is that the movement unit R reads too small, around 0.3 percent,
on coins that now move plus 1 to plus 3 percent, and that the geometry built from that too-small
R is therefore too tight. The captured log does not support that premise. R is measured
correctly and tracks each coin's volatility across a wide range. The give-back that remains is
not caused by R being too small. It is caused by two separate things, both downstream of R:
the trail distance behind the peak is a fixed half of R, which becomes a wide absolute gap on
the high-volatility coins that make the biggest moves; and on those same fast moves the climbing
profit lock is frequently unplaceable against the live market price, so it is held back rather
than written, and the trade rides a looser stop. R must not be changed. The work should be
aimed at the trail and staged-rung geometry and at the placeability of the lock on fast moves.

## Part 1 — R is measured correctly and is not the cause

R is the coin's Average True Range expressed as a percent of price. In the live code it is the
NATR-14 value computed with Wilder smoothing over fourteen five-minute candles, surfaced as
atr_pct_5m from the volatility profiler in src/analysis/volatility_profile.py and fed into the
ladder in src/workers/profit_sniper.py at line 2065, where it is smoothed per symbol by a 0.3
exponential moving average before the geometry uses it.

The log shows R is not stuck near 0.3 percent. Across the 1,042 LADDER_ADAPTIVE events in the
window the measured R ranges from 0.096 percent to 2.378 percent. The tenth percentile is 0.167
percent, the median is 0.377 percent, the mean is 0.452 percent, and the ninetieth percentile is
0.811 percent. The figure of 0.3 percent quoted in the brief is only the median; it is the
middle of a wide distribution, not a ceiling. R is small on quiet coins and large on volatile
coins, which is exactly what a correct volatility measurement should do.

The two coins the brief names as proof that R reads too small do not support the claim when
their actual behaviour in the window is checked. The brief states that ZRO reads R of 0.332
percent and BCH reads R of 0.301 percent and that these same coins are "peaking far above that."
In the captured window ZRO's highest peak on any adaptive-ladder tick was plus 0.279 percent and
BCH's was plus 0.210 percent. Both coins peaked at or below their own R. They did not make plus 1
to plus 3 percent moves. They are quiet coins with correctly small R and correspondingly small
peaks, not big movers being clipped by a too-small R.

The coins that genuinely peaked plus 1 to plus 3 percent are different coins, and on those coins
R was correspondingly large. BSB peaked as high as plus 3.08 percent and its R on those ticks
read between roughly 0.8 and 1.8 percent. LAB peaked near plus 2.9 percent, JUP near plus 2.7
percent, ASTER near plus 2.6 percent, and BEAT near plus 2.3 percent, each with R in the 0.7 to
1.9 percent range rather than 0.3 percent. Measured across every adaptive tick, the realized
peak is about one R in size: the median ratio of the tick's running peak to its R is about 0.95.
The premise that the realized move is three to ten times R is not present in this window.

The conclusion of Part 1 is that R reflects the real movement and is not the cause of the
give-back. Widening the R measurement is rejected, because it would distort a working
measurement to chase a cause the data does not support, and because a larger R would widen the
trail gap described in Part 2 and make the very problem worse. R is left exactly as it is.

## Part 2 — The give-back is in the trail and staged-rung geometry on the big movers

The profit lock is computed by profit_lock_pct in src/analysis/vol_scale.py at lines 156 to 183.
With the running peak above the arm, the lock is the larger of three quantities: the fee floor,
a staged-capture value, and a trail that sits a fixed fraction of R behind the running peak. The
trail term is exactly peak minus trail_r times R, where trail_r is 0.5 in the live configuration
(config.toml line 2286). The staged-capture value becomes the fee floor once the peak clears the
first rung at 1.5 times R, and becomes secure_at_3r_r times R, currently 1.5 times R, once the
peak clears the middle rung at 3 times R (rung_r is the list 1.5, 3.0, 5.0 at config.toml line
2280). For most of a move the trail term dominates, so the lock effectively trails one half of R
behind the peak.

Because the trail gap is a fixed half of R, it grows in absolute percent as R grows. On a quiet
coin with R of 0.3 percent the lock sits only 0.15 percent behind the peak, which is tight and
captures well. On a volatile coin with R of 1.8 percent the lock sits 0.9 percent behind the
peak, which is a wide absolute gap. The biggest movers are also the highest-R coins, so they are
exactly the coins where the trail gives back the most. This is the first proven contributor.

The aggregate retention in the window shows the pattern. Across all 402 closed trades, retention
of peak is strong on the typical small trade and weaker on the big movers. The median realized
peak across all trades is 0.324 percent and the median winning close is 0.229 percent, so the
median trade keeps about seventy-one percent of its peak; the geometry works well on the typical
move. The fifty-one trades that peaked at or above plus 1.0 percent kept on average only
fifty-three percent of their peak, and the thirteen that peaked at or above plus 2.0 percent kept
on average sixty-eight percent. The shortfall is concentrated in the large, high-R moves, not in
the median trade.

A single traced trade shows both contributors precisely. BEAT opened and ran to a realized peak
of plus 2.29 percent in the price path, with R measured near 1.84 percent. On its last logged
adaptive tick the running peak was plus 1.902 percent, R was 1.842 percent, and the computed
lock was 0.981 percent. That lock equals the peak minus one half of R, 1.902 minus 0.921, to the
digit, confirming the trail term is the binding component and that it sat 0.92 percent behind the
peak. Even if that lock had been placed and held, a 0.92 percent trail gap on a coin that peaked
at plus 2.29 percent would surrender a large fraction of the move. This is the geometry lever:
the half-of-R trail is calibrated for moves about one R in size and is too wide, in absolute
terms, on the highest-R coins.

It is important to record, honestly, that the system also captures well on many big movers, so
this is a calibration of a working mechanism and not a rebuild. In the same window BSB rode to a
realized peak of plus 3.71 percent and closed at plus 3.56 percent on a take-profit hit, keeping
ninety-six percent; USELESS kept ninety-one percent of a plus 2.91 percent peak on a take-profit
hit; ASTER kept eighty-three percent of a plus 2.34 percent peak; and JUP kept seventy-eight
percent of a plus 2.75 percent peak. Take-profit exits now occur where the pre-adaptive system
had none. The redirect should tighten the give-back on the high-R reversers without breaking
these good captures, which the replay is there to verify.

## Part 3 — The fast-move placeability leak (the clamp-noop breakdown)

The BEAT trade also exposes the second contributor. Its computed lock on the last logged tick
was 0.981 percent, but the trade closed at plus 0.53 percent, below its own computed lock. A lock
that had been written and held should have produced a close at or above it. The close below the
lock means the climbing lock was not placed during the fast part of the move. This is the
placeability leak, and the gateway logs quantify how often it happens.

The brief asks specifically why the clamp-noops persist when a profit-lock exemption was shipped
to stop them. The answer is that the exemption is working and is not the leak. The exemption,
gated by r2_profit_lock_floor_enabled in src/core/sl_gateway.py at lines 813 to 830, holds the
R-derived lock through the R2 minimum-distance clamp so the lock writes instead of being dropped.
In this window it did so 3,352 times, each logged as SL_GATEWAY_R2_PROFIT_LOCK_HELD. That path is
healthy.

The 3,128 clamp-noops recorded on the ladder source are a different mechanism that the exemption
was never meant to cover, and they divide cleanly into two kinds. The first kind, 758 events, is
the final tighten-only re-check at lines 957 to 969: the ladder recomputes the same already
captured lock on a later tick, the proposed stop does not improve on the stop already placed, and
the gateway correctly holds the existing stop. These are benign. They are the ladder restating a
level it already protected, and they cost nothing.

The second kind, 2,370 events, is the fresh-mark-degrade no-op at lines 1052 to 1062, and this is
the lossy path. On a fast move the price has already advanced inside the minimum distance of the
lock the ladder wants to place, so the lock is unplaceable against the fresher live mark price.
The gateway degrades it to the closest placeable boundary, and when even that boundary cannot
improve on the stop already in place it holds the existing, looser stop and records a clamp-noop.
The tighter lock the ladder computed is foregone for that tick. The two kinds sum to exactly the
3,128 total with nothing unaccounted.

The precise effect of a single fresh-mark-degrade no-op is a foregone tighten, not an immediate
realized loss: the existing stop stays in place, so the trade only gives profit back if price
later reverses past that looser stop. But on a fast, high-R mover these no-ops recur tick after
tick during exactly the steep part of the move, so the lock chronically lags the peak, and when
the reversal comes the trade exits far below where the computed lock sat. BEAT is the worked
example: lock computed near 0.98 percent, trade closed at 0.53 percent. This is a placeability
problem created by the lock being too close to the live price on a fast move, which ties it back
to the geometry rather than to a broken exemption.

The mechanism that produces these no-ops, the fresh-mark degrade, is itself a safety feature: it
exists to stop unplaceable stops being spammed at the exchange and causing wire failures. It
therefore cannot simply be switched off. The fix must make the lock both capture the move and
remain placeable on a fast mark, which is again a geometry and bounds question, addressed in the
redirect and proven on the replay, not a removal of a safety guard.

## Part 4 — Redirect recommendation

R is correct and is out of scope. The proven causes of the residual give-back are the wide
half-of-R trail on the highest-R movers and the fast-move placeability leak that prevents the
climbing lock from being written. The recommended redirect, to be designed and replayed against
the real logged trades before any live change, addresses exactly these and nothing else.

The first lever is the trail and staged-rung geometry in src/analysis/vol_scale.py, configured
entirely in the [adaptive_exit] section: the trail fraction trail_r, the staged rung positions
rung_r and the secure level secure_at_3r_r, and their bounds. The aim is that a coin peaking plus
2 percent locks nearer that peak rather than roughly 0.9 percent behind it, while the tight,
well-capturing behaviour on the typical small move is preserved. The exact values must be chosen
by replaying candidate settings against the real logged trades with the existing
simulate_adaptive_exit_replay.py harness and selecting what would have captured the real moves
net of fees, not by guessing.

The second lever is the fast-move placeability of the lock at the gateway boundary, the
fresh-mark-degrade no-op subset. The aim is that the placed lock both captures the move and stays
placeable against the live mark, so the climbing lock reaches the exchange during the fast part
of a big move. Whether this is best done by shaping the geometry so the lock respects the minimum
distance against the fresh mark, or by a centralized gateway parameter, will be decided from this
quantification and presented at the gate before any change. It must not weaken the wire-fail
safety, the catastrophic cap, or the tighten-only discipline.

Both levers are calibrations of the existing adaptive exit. They add no new exit mechanism, no
new trading gate, and no new filter. The catastrophic cap, the owner hierarchy, and the universe
system are untouched. R is untouched. Each change will ship as one atomic, revertible commit on
the main branch, proven on the replay first and then watched live with a forced
catastrophic-stop test confirming the cap still fires and the hard stop stays below it.

## Honest limits of this diagnosis

This diagnosis is drawn from one twenty-eight-hour window. It proves that, in this window, R is
correct and the give-back is in the trail geometry and the fast-move placeability. It does not by
itself prove what the retuned values should be; that is the replay's job, phase by phase. It also
does not address how often the entries are right, which sets the ceiling on the win rate and is a
separate question this exit work informs but does not change. The seventy-percent target named in
the brief is not claimed from this tuning alone; the replay will show what win rate and net the
retuned exit would produce on these real trades, and the remaining distance, if any, is the
entries' to close.
