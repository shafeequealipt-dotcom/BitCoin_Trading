# Phase 2.3 — Concern 3: Does Issue 2 Option B (split direction/size confidence) preserve suppression via renamed field?

## Concern restated

Issue 2 Option B proposes: split `setup_type_confidence` into two fields:
- `setup_type_confidence_direction` (un-cut, no ×0.7 multiplier applied) — read by direction-lock, brain prompt, post-entry invalidation.
- `setup_type_confidence_size` (cut by ×0.7 for counters) — read by sizing, ranking, ensemble.

This addresses the compounding (4 stacked floor-0.5 multipliers all see the un-cut value) and the Layer 4 force-close asymmetry.

The senior reviewer's concern: the proposal **preserves the underlying suppression**, just refactored into a renamed field. The ×0.7 multiplier still exists, still cuts counter confidence, still feeds the sizing path. If the underlying assumption (counter trades should be size-suppressed) is wrong, Option B doesn't fix it — it just hides it better.

## Evaluation

### Is the underlying assumption (counter trades = lower conviction = smaller size) valid?

This is the load-bearing question. Three sub-questions:

#### a) Does the system have data on counter vs in-direction trade WR?

The `trade_log` table has a `setup_type` column but does not stratify by counter-vs-in-direction directly. We'd need to join `trade_log.thesis` or related fields. Per the spec (and to avoid touching DB), I'm using direct grep on the audit log.

In the audit window (5.5h, 91 brain decisions, 84 final orders):
- Counter setups exist in 14.5% of XRAY_CLASSIFY rows (441 / 3033).
- Of the 84 final orders, how many came from counter setups?

The audit log shows BYBIT_DEMO_ORD_SEND entries are tagged with `link_id` and `purpose=layer3_entry`, but the link to setup_type would require correlating with strategy_worker emissions. From `XRAY_FLIP` events alone, 11 of 91 brain decisions were flips (some Sell→Buy, some Buy→Sell), and flips often involve counter setups.

Insufficient direct data in the audit log alone to give a definitive counter-vs-in-direction WR. Need DB query.

#### b) What's the original design intent of the multiplier?

From commit `3a59637` (2026-04-30) commit message (per Phase 1.2 finding): "counter is already lower-conviction; don't double-penalize on MTF." The author CHOSE 0.7 heuristically to make counter setups visibly lower-confidence than in-direction setups. No backtest. No empirical calibration.

So the underlying assumption is: **counter trades intrinsically have lower conviction → should be sized smaller**. This is a heuristic, not a measured truth.

#### c) Is the heuristic plausibly correct?

Theoretical case for counter trade lower conviction:
- Counter trades go against structural bias. The bias has more evidence behind it (FVG + OB + trend) than the counter (FVG + OB only, opposite-direction).
- Lower evidence base → lower confidence.

Theoretical case against:
- The R1 fix (2026-05-17) recognized that counter trades have their own justification — the `trade_direction` field separates "where structure points" from "where the trade should go."
- If R1 is correct, counter trades aren't INFERIOR — they're just SEPARATELY-justified. They deserve the SAME confidence calibration as in-direction.

Empirical case (from audit window):
- 1,140 counter-LONG opportunities in 5.5h. If counter trades were genuinely lower conviction, the system should be ignoring most of them — which it is (only 9 final Buy orders out of 84 total).
- But that suppression is the BIAS we're investigating. So "the system is currently suppressing counter LONGs and it's profitable" is circular reasoning.

### Does Option B preserve suppression?

YES — by design. The `_size` field carries the cut. Sizing of counter trades remains discounted.

### Is Option B better than the current state?

YES, despite preserving suppression. Specifically:
- It fixes the COMPOUNDING (4 stacked floor-0.5 multipliers operating on the same cut input → ~3.88× total → reduced to single 1.4× cut).
- It fixes the Layer 4 asymmetric force-close (post-entry invalidation reads un-cut value).
- It fixes the brain prompt (Claude reads un-cut confidence → direction signal isn't pre-distorted).

So Option B is an honest improvement even if you accept the underlying suppression assumption.

### Is Option B better than Option E (full removal)?

This is the key question. Option E (Concern 7) removes the multiplier entirely. Compared to Option B:

| Aspect | Option B (split) | Option E (remove) |
|---|---|---|
| Preserves suppression at sizing? | YES | NO |
| Reversible? | YES (split is config-toggleable) | YES (set config = 1.0 = no-op, or git revert) |
| Surface size | 6+ consumer changes | Single producer change |
| Risk of regression | MEDIUM | HIGH (no safety net for genuine low-conviction counters) |
| Honors operator directive? | Partially (still has hardcoded 0.7) | YES (no number at all) |

Option E is bolder. Option B is safer. Operator decision.

### Senior reviewer's specific point

"If the underlying assumption is wrong, Option B doesn't fix it — it just hides it better."

True. Option B refactors the suppression so it's only at sizing, but doesn't question whether sizing suppression is valid in the first place. If counter trades have equal performance to in-direction trades (data-confirmed via a longer trial), the suppression at sizing is itself a bug — and Option B preserves it.

## Verdict

**PARTIALLY VALID.** Option B IS a refactor that preserves the underlying suppression. But it's NOT a band-aid in the same sense as Option A — it's an architectural improvement that improves observability and reduces compounding. The honest critique is: Option B is a halfway house. It's better than the status quo, but not as bold as Option E.

## Recommendation

The operator's directive (asymmetry from data, not numbers) is BETTER served by Option E (remove ×0.7 entirely). Option B is acceptable as a code-hygiene improvement but doesn't fully resolve the directive concern.

Sequenced approach:
1. Run Concern 7's config-only test first (set `counter_confidence_multiplier = 1.0` in TOML for 48h trial).
2. If WR doesn't degrade, ratify with code removal (Option E).
3. If WR degrades materially, fall back to Option B (split fields — preserves a smaller version of the suppression).
4. If even Option B degrades, fall back to Option D (data-calibrated multiplier).

## Implications for fix path

- Option B should NOT be the first-choice Issue 2 fix.
- The first move should be Concern 7's config test (Option E light).
- If the test passes, ship Option E (remove from code).
- If the test fails, ship Option B (split fields).
- Either way, Option A (regime-concentration adaptive) is out (per Concern 2).
