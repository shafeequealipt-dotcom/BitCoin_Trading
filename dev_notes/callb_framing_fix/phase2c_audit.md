# Phase 2C — Audit other enforcer modes

**Date:** 2026-05-06
**Outcome:** **NO-OP**. Phase 4 of dir-block-fix (commit `2cb3dc4`) and the new Phase 2A/2B already cover the philosophy. No further code changes needed.

## Mode-by-mode review

### Level 0 (NORMAL) — PnL >= 0%
- `should_allow_trade` returns (True, "ok").
- `clamp_leverage` no-op (within cap).
- `get_max_positions_override` returns None (no override).
- `get_min_score_override` returns None.
- `qualify_survival_trade` short-circuits to (True, "not_in_survival").
- `get_size_multiplier` 1.0.

**Verdict:** Pass-through. No restrictions.

### Level 1 (CAPITAL_PRESERVATION) — PnL < -3.0%
- `should_allow_trade` returns (True, "ok"). (Phase 4 dir-block-fix.)
- `clamp_leverage` clamps requested leverage to `level_1_max_leverage` (3) via `PRESERVATION_CLAMP`. Not a block. (Phase 4.)
- `get_max_positions_override` returns 3. **This IS a block** — caller (likely `layer_manager` or `strategy_worker`) refuses to open new trades when the open-position count is at 3.
- `get_min_score_override` returns 75 — **block via quality gate** at the score level.
- `get_size_multiplier` 0.50.

**Audit verdict:** The position-count cap (3) is genuine recovery safety — preventing the system from opening too many positions while drawdown recovery is happening. The score floor (75) is the same kind of quality safety. Both are bounded (not infinite blocks), config-driven, and align with the operator's aim: scale UP quality during recovery without halting. Conversion to "adjustment" is not meaningful for these (you can't adjust a position count to be lower than what it already is, and you can't make a low-score setup into a high-score setup).

**No code change.**

### Level 2 (SURVIVAL) — PnL < -12.0% (raised from -7.0% in Phase 2A)
- `should_allow_trade` returns (True, "ok").
- `clamp_leverage` clamps to `level_2_max_leverage` (3) via `SURVIVAL_CLAMP`.
- `get_max_positions_override` returns 2.
- `get_min_score_override` returns 80.
- `qualify_survival_trade`: structure-data quality gate. **Phase 2B (commit `118adcf`)** converted the RR floor from BLOCK to ADJUSTMENT. The remaining gates (setup_quality A+/A, confluence >= 7) stay as BLOCKS — they're quality-floor safety.
- `get_size_multiplier` 0.25 / 0.40 / 0.50 depending on recovery stage.

**Audit verdict:** Phase 2B addressed the RR floor. The remaining gates are intentional quality safety: refuse to open low-quality setups while in deep drawdown. Conversion to adjustment isn't meaningful (you can't make a C-quality setup into an A-quality one).

**No code change.**

### Level 3 (HALTED) — PnL < -15.0% (NEW in Phase 2A)
- `qualify_survival_trade` returns (False, "halted") regardless of structure data.
- `clamp_leverage` clamps to 1 (defense in depth via `HALTED_CLAMP`).
- `get_max_positions_override` returns 0.
- `get_min_score_override` returns 100 (no setup will pass).
- `try_adjust_for_survival_rr` returns (None, "halted") — adjustment refused.
- `get_size_multiplier` 0.0.

**Audit verdict:** Genuine emergency stop. Operator-driven recovery only. ENFORCER_HALTED entry/exit logged once per transition.

**No code change.**

## Streak path (secondary signal)

`streak_boost_threshold = -8` AND `streak_boost_pnl_floor_pct = -1.0` together mean: 8+ loss streak only elevates level when PnL is also below -1%. Phase 4 dir-block-fix raised both knobs (was -5 + no PnL floor). No Phase 2C change.

## Soft throttle: size_multiplier

The size_multiplier path is config-driven and bounded across all four levels. No change needed.

## Conclusion

Phase 2A + 2B cover the philosophical realignment (raise SURVIVAL trigger, convert RR floor to adjustment, add HALTED). The remaining "block" gates in Levels 1 and 2 are bounded quality/safety mechanisms — converting them to adjustments would not be meaningful (you can't adjust a setup-quality letter grade or confluence score). Per the operator's plan-time guidance, Phase 2C is a no-op.

No commit beyond this audit document.
