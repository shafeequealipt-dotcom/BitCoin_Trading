# Phase 2.7-bis — Plan Revision Per Operator Directive

## Operator directive

> "sell and buy should be both work according to the best scenarios, not hard coded saying if sell this much then buy this much not like that"

Translation: no direction-specific hard-coded thresholds. Every direction decision must be scenario-driven — composed from CURRENT evidence (regime, structural signal, `trade_direction`, recent per-direction WR) at the moment of decision. The asymmetry between Buy and Sell, if any, must EMERGE from the data, not be encoded as fixed numbers per direction.

## What changes versus DELTA 04 master recommendation

### R1 (ALPHA Option E + D) — unchanged

ALPHA's fix is pure data plumbing. No thresholds, no asymmetry. The fix lets APEX SEE the structural signal that XRAY already computes; there is nothing direction-specific to revise.

### R2 (BETA APEX_DIR_LOCK) — REVISED

DELTA 04 proposed static asymmetric thresholds: 10x for Buy→Sell, 3x for Sell→Buy. This is exactly the "hard-coded direction-specific" pattern the operator rejected.

Revised mechanism: `_check_direction_lock()` computes a composite SUPPORT SCORE for the brain's proposed direction from four signals. The lock fires only when the composite score is below a single threshold (default 0.0 — neutral). No per-direction thresholds.

Composite score components (each contributes to the same scalar):

1. **Regime alignment** — does the regime label support brain_dir?
   - Score: +regime_weight if aligned, -regime_weight if opposed, 0 if regime is ranging/dead/unknown
2. **Structural R:R alignment** — does structural data favor brain_dir?
   - Read `package.structural_data.rr_long`, `rr_short`
   - Compute log-scale ratio: `log(rr_dir / rr_opposite)` where rr_dir matches brain_dir
   - Score: contributes `log_ratio * structural_weight`
   - Log-scale dampens extreme ratios while preserving sign
3. **XRAY trade_direction alignment** — does ALPHA's plumbed `trade_direction` match brain_dir?
   - Score: +trade_dir_weight if match, -trade_dir_weight if opposite, 0 if unset
4. **Per-direction WR alignment** — does recent trade history support brain_dir?
   - Read trade_log over window (default 200 trades)
   - Compute `dir_wr = buy_wr if brain_dir == "long" else sell_wr`
   - Score: `(dir_wr - 0.50) * wr_weight` — positive when direction WR > 50%

Lock decision: `lock_fires = composite_score < lock_threshold`. With default weights = 1.0 each and threshold = 0.0, the lock fires when total evidence is net-negative for the brain's direction.

Why this is scenario-driven:

- Every signal is evaluated AT DECISION TIME from current package state
- No "if direction == X then Y" branches
- The asymmetry between Buy and Sell EMERGES from the WR signal — if Buy WR is 55% and Sell WR is 41%, the WR component pulls scores in opposite ways automatically
- All weights are config-tunable; defaults are NEUTRAL (1.0 each)
- The threshold itself is direction-agnostic — the SAME number gates Buy and Sell

New settings on `APEXSettings`:

- `apex_lock_score_threshold: float = 0.0`
- `apex_lock_regime_weight: float = 1.0`
- `apex_lock_structural_weight: float = 1.0`
- `apex_lock_trade_dir_weight: float = 1.0`
- `apex_lock_wr_weight: float = 1.0`
- `apex_lock_wr_window_trades: int = 200`

New event: `APEX_LOCK_DECISION_EXPLAINED` carrying every component value, the composite score, and the verdict so the operator can audit each lock decision.

### R3 (BETA XRAY override threshold) — unchanged

R3 Option E (per-direction WR auto-tuning) is ALREADY fully scenario-driven. The override threshold derives dynamically from current per-direction WR. There is no "10x Buy→Sell vs 3x Sell→Buy" — the threshold for each direction is computed from `buy_wr` and `sell_wr` measured each cycle. The asymmetry emerges from the data.

If post-fix Sell starts winning more than Buy, R3's threshold AUTO-INVERTS toward favoring Sell. That is the design intent and is preserved.

### R4 (GAMMA Design C portfolio cap) — unchanged

Design C is aim-conditional: the cap fires ONLY when XRAY shows the opposite direction is viable (via `trade_direction` or `rr_opposite/rr_chosen` ratio). The 70% threshold is a single direction-agnostic value — the same percent applies whether the over-represented direction is Buy or Sell.

The decision to fire/not is scenario-aware (Design C). The 70% threshold itself is a config default, not direction-specific; the operator may tune in one place if observed behavior warrants.

If future data shows one direction systematically performs better, the operator can EITHER:
- Tune the threshold (simple)
- OR add a future enhancement where the threshold itself derives from per-direction WR — same pattern as R3 (deferred to Phase 5; out of scope for first iteration)

## What stays from DELTA 04

- Sequencing: R1 (ALPHA) → R2+R3 (BETA) → R4 (GAMMA)
- Branch strategy: three branches off HEAD 7320266
- Risk register (12 items) — still applies, with R-3 and R-4 (WR window pollution and double-feedback loop) now even more relevant because R2 also reads WR
- Verification criteria — adjusted: R2's verification now confirms `APEX_LOCK_DECISION_EXPLAINED` shows balanced score components rather than direction-specific bail logic

## What was rejected and why

- "MODIFY - reduce R3 to static asymmetric" — REJECTED. That direction is what the operator just told us NOT to do.
- "MODIFY - lower GAMMA cap from 70% to 60%" — DEFERRED. Threshold tuning can happen post-trial; the design is already scenario-aware.
- "FURTHER INVESTIGATION - replay 2026-05-16" — REJECTED. Predicted behavior is already specified in DELTA 03; live trial after each Phase 3 will validate. No marginal value from replay.

## Operator-facing summary

Each lock / override / cap decision now asks the SAME question regardless of direction: "given the CURRENT evidence — regime, structure, counter signal, recent win rate — is the proposed direction supported?" If yes, the decision permits. If no, the decision denies. The asymmetry between Buy and Sell, when it exists, comes from the win-rate signal — it is data-driven, not hard-coded.

This is the design the operator approved by the directive in this Phase 2.7 redirection.

## Implementation effort delta versus DELTA 04

- R1: unchanged. 2-4 hours.
- R2: slightly higher complexity (composite score function with four components). 3-5 hours (was 2-3 hours).
- R3: unchanged. 3-5 hours.
- R4: unchanged. 4-6 hours.

Total: 12-20 hours, same band as DELTA 04.

## Proceeding

Orchestrator proceeds to Phase 3 implementation on this revised plan. ALPHA Phase 3 begins immediately on `fix/r1-xray-counter-inversion`.
