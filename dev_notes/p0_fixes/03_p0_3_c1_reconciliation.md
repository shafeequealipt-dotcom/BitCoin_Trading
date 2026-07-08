# P0-3 ⇄ C1 Reconciliation

Date: 2026-05-22. Required by spec Rule 7 and section A.7.

## H1 — Setup

C1 is the `IMPLEMENT_C1_ENFORCE_ACTIVATION` work that activated `wd_brain_scoring_enforce = true`. The activation landed on 2026-05-21 as commit `3bfb5e4 c1: activate wd_brain_scoring_enforce (operator-approved 2026-05-21)`. The 2026-05-22 session that produced the P0 defects is the first production day with this mode live.

C1's goal: when the brain panic-closes a losing position, the watchdog rejects it and tightens SL toward break-even instead — so passive paths (deadline, trailing SL) close the position more profitably. The evidence backing C1 came from the 2026-05-20 9-hour session where 27 of 28 brain panic-closes lost money and the scoring (in log-only mode) recommended rejecting all 28.

P0-3's complaint: the same scoring on 2026-05-22 rejected 12 brain close votes that — in hindsight — appear to have been correct (INJ rode to stop, ICP rode to timeout). The composite ceiling never reached 6.0.

The reconciliation question per section A.7: are these two findings contradictory?

## H1 — Why They Are Not Contradictory

The two findings differ in one decisive variable: **entry quality**.

In the C1 session the entries were structurally sound. The brain panicked when positions showed temporary drawdown; rejecting the panic-close and holding allowed the passive paths to close profitably. The scoring correctly rejected the brain's "this is losing, I should cut it" instinct.

In the 2026-05-22 session the entries were structurally inverted by P0-2. The system shorted coins the brain itself wanted to buy. When a wrong-direction trade started losing, the brain's close vote was *correct* — the position was never going to recover because it was a wrong-direction trade. Rejecting the brain's correct close compounded the damage.

The close-veto's value is conditional on entry quality. When entries are sound, vetoing panic-closes helps. When entries are inverted, vetoing the brain's correct close compounds the damage.

This conditional logic is exactly the spec's A.7 reconciliation: **fix P0-2 first** (so entries are no longer silently inverted), then the C1 enforce-mode work on the actual brain panic-closes (which is its proper domain).

## H1 — Did the Composite Scoring Distinguish Sound vs Unsound in 2026-05-22?

The composite scoring has two factors specifically designed to make this distinction:

- **xray_factor** (factor 6). Reads the XRAY structural verdict and compares to the position's side. When XRAY says "the trade direction is wrong" (broken), factor = +2.0. When XRAY says "the trade direction is right" (supports), factor = -2.0.
- **velocity_factor** (factor 4). When the position is moving toward TP (positive velocity), factor pushes against closing. When the position is accelerating into SL (negative velocity), factor pushes for closing.

On the 2026-05-22 INJ at 16:05:19 (the case the spec highlights): `xray_bucket=broken xray_factor=2.0 velocity_bucket=stationary velocity_factor=0.0`. XRAY correctly identified the trade direction as wrong (broken) and contributed +2.0. Velocity was stationary (price one tick from SL, not accelerating), so velocity contributed 0.

The scoring's `xray_factor=broken` is the structural-validity signal that should distinguish the unsound 2026-05-22 cases from the sound C1 cases. It did its job correctly for the 12 P0-3 rejections — they all carried `xray_bucket=broken`. But its +2.0 contribution was not enough to overcome:

- `pnl_factor` heavily negative on shallow_loser (-3.0).
- `time_factor` heavily negative on positions with >20m remaining (-2.0).
- `sl_factor` zero on positions in the 60-80% SL band (most P0-3 cases).

The scoring *correctly identified the broken structure*; the threshold was simply too high to act on the brain's vote even when structure was broken.

## H1 — Should the Composite Be Recalibrated, or Should the Brain Vote Get Real Weight?

Two non-band-aid options:

### Option A: Recalibrate the composite

Raise `xray_factor` for the broken bucket from +2.0 to +3.0 or +4.0. Rationale: the structural-validity signal is the most decisive evidence the scoring has access to; weight it accordingly.

Pro: minimal mechanical change. Same scoring shape.
Con: still doesn't give the brain's *vote* any decisive authority; the brain remains just a trigger. Doesn't address the spec's A.7 "the brain's explicit close decision carried no authority" framing.

### Option B: Give the brain's explicit vote a bounded, decisive factor

Add a new `brain_vote` factor: +2.0 to +3.0 baseline when the brain explicitly votes close (vs the close path firing for automated reasons). Combined with the existing reasoning_factor (max +2.0 for structural), a brain-with-evidence close gets +4.0 to +5.0 in addition to whatever the structural factors contribute. This is the spec's "brain's explicit close decision with real authority" framing.

Pro: directly addresses the A.7 critique. The brain remains the close-proposer; the scoring weighs the brain's vote alongside independent factors.
Con: more mechanical change.

### Recommended: B with a hard risk floor

Combine option B (brain_vote factor) with a hard risk floor: when `sl_consumption_pct >= 85%` (operator-tunable), force-close regardless of composite. The floor catches the edge cases where structural evidence is mixed or stale (e.g., XRAY stale or unavailable) but the position is already running out of risk budget.

This is the spec's section A.8 framing: "give the brain's explicit, evidenced close decision real weight and add a hard risk floor that closes regardless of composite beyond a stated stop-consumption level, while preserving the watchdog's legitimate anti-churn role when the brain is silent."

## H1 — Does the P0-2 Fix Need to Land First?

Yes. Per Rule 6 and A.7:

- If P0-2 is not fixed, entries continue to be silently inverted. The brain's close votes on those inverted entries are correct (close the wrong-direction trade), and the P0-3 fix correctly enables them.
- But that means we are using the P0-3 fix to compensate for the P0-2 defect — close more aggressively to bail out wrong-direction trades. That violates the spec's anti-pattern 4 (using close-policy changes to mask wrong outputs).
- With P0-2 fixed first, the entries flow as the brain intended. Then the P0-3 fix's domain is the actual panic-close case (the C1 domain): brain's close vote on a structurally sound position. The brain_vote_weight + reasoning_quality combine to allow brain-with-evidence closes to execute even when other factors are mixed; the hard risk floor catches the residual.

## H1 — C1 Sequencing Decision (Operator Choice at P0-3 Gate)

After P0-2 lands and is verified, and P0-3's brain_vote_weight + hard_risk_floor proposal is approved, the operator decides:

- **Option 1 (recommended): Keep enforce mode ON with the new scoring.** The new factors (brain_vote_weight + hard_risk_floor) push correct closes through while preserving rejection of vague-reasoning panic-closes on structurally-sound positions. C1's intent is preserved and strengthened.
- **Option 2: Pause enforce mode while the new scoring is observed in log-only.** Run for a session in log-only with the new factors, confirm the composite distribution makes sense, then re-flip enforce to true.
- **Option 3: Roll back C1 entirely.** Set `wd_brain_scoring_enforce = false`, revert to brain panic-closes always firing. Not recommended; this loses the C1 wins on sound entries and is a step back.

I will present these three options at the P0-3 decision gate after the P0-2 fix is approved and verified.

## H1 — Verification That C1 Wins Are Preserved After P0-3 Fix

The P0-3 fix's no-churn regression check: run a controlled scenario reproducing the C1-target case — brain panic-closes a position with vague reasoning, structurally-supportive XRAY, stationary velocity, mature age, comfortable SL consumption. The composite should still reject (now with the additional brain_vote_factor but still below threshold because all other factors push against closing on a sound position).

Concrete worked example for the no-churn regression: vague reasoning brain panic-close at -0.5% on a position with XRAY supports, mature age (~15 min), 35% SL consumption, mild_negative velocity:
- pnl_factor = -3.0 (shallow_loser)
- time_factor = -2.0 (deep, >20m remaining)
- age_factor = 0.0 (mature)
- velocity_factor = +1.0 (mild_negative)
- sl_factor = -1.0 (comfortable)
- xray_factor = -2.0 (supports)
- reasoning_factor = +0.5 (vague)
- **NEW brain_vote_factor = +2.0** (brain explicitly voted close)
- composite = -3 + (-2) + 0 + 1 + (-1) + (-2) + 0.5 + 2 = **-4.5** → reject_and_tighten

Even with the brain_vote_factor, the composite is -4.5 (well below threshold). The C1 protection holds.

Now the P0-3-target case (brain correct close on wrong-direction position): broken XRAY, strong_negative velocity, imminent SL, structural reasoning:
- pnl_factor = -1.0 (moderate_loser)
- time_factor = -2.0 (deep)
- age_factor = 0.0 (mature)
- velocity_factor = +2.0 (strong_negative)
- sl_factor = +1.0 (imminent)
- xray_factor = +2.0 (broken)
- reasoning_factor = +2.0 (structural)
- **NEW brain_vote_factor = +2.0**
- composite = -1 + (-2) + 0 + 2 + 1 + 2 + 2 + 2 = **+6.0** → execute (just at threshold)

The brain-with-evidence close now executes. Both authorities are preserved.

## H1 — Settled Position

P0-2 fix lands and is verified first. Then P0-3 fix adds brain_vote_weight + hard_risk_floor, presented at gate with the three C1 sequencing options. C1 enforce mode stays on through P0-1, P0-2, and the P0-3 investigation; the operator decides sequencing at the P0-3 gate.
