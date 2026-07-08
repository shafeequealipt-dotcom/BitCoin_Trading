# Adaptive Exit R-Calibration — Stage B Phase 1 Design (Trail and Rung Recalibration)

This is the design for the first phase of Stage B, following the approved Phase 0 diagnosis
(ADAPTIVE_EXIT_R_CALIBRATION_PHASE0_DIAGNOSIS.md) and the operator's two approval constraints.
It contains no production code change. It defines the candidate geometries, the faithful replay
that will choose between them, and the evidence that must be shown before anything goes live.

## The two constraints this design is built around

The operator approved Stage B with two binding instructions. First, the trail fix must not be a
flat global reduction of trail_r, because the flaw is structural: a fixed fraction of R is tight
on small-R coins (0.15 percent behind a peak when R is 0.3) and a chasm on high-R coins (0.9
percent behind a peak when R is 1.8). A flat smaller trail_r would tighten every coin and risk
regressing the small and typical movers that currently keep about seventy-one percent of their
peak. Second, the trail fix and the placeability fix are entangled and must be replayed together
on the real gateway, because a better-computed lock that the fresh-mark-degrade still cannot
place captures nothing; and the fresh-mark fix must not weaken the wire-fail safety it is part
of — if capturing the fast-move lock requires weakening that safety, that is an escalation, not
a fix.

## The lever being changed

The binding term in the profit lock is the trail, computed in profit_lock_pct in
src/analysis/vol_scale.py at line 176 as trail = peak minus trail_r times R, with trail_r equal
to 0.5. The lock is the maximum of the fee floor, the staged-rung value, and this trail; for most
of a move the trail dominates. Because the trail gap is a fixed fraction of R, it scales linearly
with R, which is precisely why it is too wide on the high-R movers. The recalibration changes how
the trail distance is derived from R. It only ever moves the lock closer to the peak, so it can
only tighten the protective stop, never loosen it; the catastrophic cap and the hard stop are not
touched.

## Candidate geometries to be swept on the replay

The replay sweeps the following candidates for the trail distance d, where the lock's trail term
becomes trail = peak minus d. The baseline is the live behaviour; the others are the tuning
options. Every coefficient named here is a centralized [adaptive_exit] config value, never
hardcoded; the chosen one ships as a bounded config addition with a boot-sentinel readout.

Candidate B0, the baseline: d equals trail_r times R with trail_r 0.5. This is the current live
geometry and the control against which every other candidate is measured.

Candidate B1, a flat smaller fraction: d equals k times R for k in 0.4, 0.35, and 0.3. This is
the naive fix. It is included specifically to test the operator's concern that a flat reduction
over-tightens the small movers. If the small-R cohort's capture falls under B1 relative to B0,
B1 is rejected on that evidence, not on assertion.

Candidate B2, an absolute cap on the trail distance: d equals the minimum of trail_r times R and
a cap, with trail_r held at 0.5 and the cap swept over 0.30, 0.40, and 0.50 percent. This is the
principal candidate. It implements the operator's "absolute cap on the trail distance regardless
of R" directly. It is mathematically identical to a trail fraction that shrinks as one over R
above a knee: below the knee, where trail_r times R is under the cap, the geometry is exactly the
current 0.5R and the small movers are untouched; above the knee, where trail_r times R exceeds
the cap, the gap is held flat at the cap and only the high-R movers are tightened. It introduces
one new bounded config key, trail_dist_cap_pct, defaulting to 0.0 which means no cap and
therefore the exact current behaviour, so the change is inert until a positive cap is set.

Candidate B3, an explicit R-dependent fraction: d equals trail_r_eff times R, where trail_r_eff
decreases from trail_r as R rises above a knee R0. This is included as a more gradual alternative
to B2's hard cap, in case the replay shows the hard knee is too abrupt. Because the constant-gap
form of B3 reduces algebraically to B2, B3 is only carried as a distinct candidate in its
soft-decay form; if it does not beat B2 on the replay it is dropped in favour of the simpler cap.

The rung and staged-secure terms (rung_r at 1.5R, 3R, 5R and secure_at_3r_r at 1.5R) are a
secondary lever. On the high-R movers the 3R secure rung is rarely reached — three times an R of
1.8 is 5.4 percent, above almost every realized peak — so the staged secure seldom engages for
exactly the coins that give back, and the trail term carries the capture. The replay will test
whether lowering the middle rung (for example to 2R) or adding a bounded absolute secure adds
capture on the big movers without regressing the small ones. A rung change is adopted only if it
helps on the evidence; otherwise Phase 1 ships the trail cap alone, as the single coherent change.

## The faithful replay (how the candidates are judged)

The existing simulate_adaptive_exit_replay.py is not adequate for this decision for two reasons:
it runs on the old pre-universe-fix one-hour window where the coins barely moved, and its capture
numbers are an idealized in-process walk that assumes every computed lock is placed perfectly. It
drives the real gateway only for one canonical trace, not for the capture figures. Per the
operator's second constraint, the capture numbers must come from the real gateway with the
placeability mechanism live. A new harness is therefore required.

The new harness reconstructs each trade in the captured 28-hour window from the logged truth:
entry, direction, and the initial stop from THESIS_OPEN; the per-second realized price path from
PRICE_PATH keyed by the trade's order id; the realized close and reason from THESIS_CLOSE; and
the movement unit R for the trade from its LADDER_ADAPTIVE lines. It then walks each trade tick by
tick and, at each tick, calls the real SLGateway.apply with the candidate geometry's lock as the
proposed stop, the resting placed stop as current_sl, the logged price as current_price, the
trade's R and class supplied through the volatility-profiler interface, and the source set to the
ladder source with the profit-lock and breakeven floors passed exactly as production passes them.
The gateway's real R2 minimum-distance clamp, the profit-lock exemption, the tighten-only
re-check, the fresh-mark-degrade, and the terminal wrong-side guard all run unmodified. When the
gateway accepts a stop it becomes the new resting placed stop; the trade exits when the price
crosses that resting placed stop, and the captured result is that stop converted to percent and
taken net of the round-trip fee. This makes the captured numbers reflect what the gateway would
actually have placed, not an idealized lock.

The fresh mark that the gateway validates placeability against is modelled by the next observed
price tick, the freshest available proxy for the mark the adapter would enforce against. This is
an honest approximation with a known direction of error: the per-second path cannot resolve the
roughly 150-millisecond live-mark latency, so using the next second overstates the gap and
therefore over-counts, not under-counts, the fresh-mark-degrade no-ops. That is the conservative
direction for a placeability test — it will not flatter a candidate's captures. The replay also
isolates the owner gate and the rate limit, as the existing harness does, to study the geometry
and placeability question cleanly; the owner hierarchy and rate limit are unchanged in production.

## What must be shown before going live

The replay must demonstrate, on these real trades through the real gateway, three things together,
or the candidate is not shipped. First, the median win rises toward the median peak relative to
the baseline. Second, the small and typical movers are not regressed: the small-R cohort's
capture under the chosen candidate is at least as good as under the baseline B0. Third, the
captures reflect real gateway placement, including the fresh-mark-degrade, so the reported gain is
what would actually be placed and not an idealized lock. The replay will report the candidates
side by side, split into a small-R or small-peak cohort and a high-R or high-peak cohort, with the
baseline shown for both, so any regression on the small cohort is immediately visible.

If the evidence shows the placeability leak alone caps the achievable capture on the fast movers —
that even the best-computed lock cannot be placed because price moves inside the minimum distance —
that finding is reported, and any change to the fresh-mark-degrade is designed separately and only
if it does not weaken the wire-fail safety; weakening that safety to capture the fast-move lock is
an escalation to the operator, not a fix made here.

## Shipping and safety

The chosen candidate ships as one atomic, revertible commit on the main branch: the bounded config
addition (the trail cap or the chosen form), the corresponding small change to profit_lock_pct in
vol_scale.py, the validator entry, and the boot-sentinel readout so the loaded value is visible.
A timestamped backup is taken before editing. The catastrophic cap, the hard stop, the owner
hierarchy, the universe system, and R are untouched. After the replay evidence is approved at the
gate, the change goes live and is watched on LADDER_ADAPTIVE and the win-versus-peak gap, with a
forced catastrophic-stop test confirming the cap still fires and the hard stop stays below it.
