#!/usr/bin/env python3
"""PF/LC Top-15 Problem 3.1 — win-probability over-cut smoothing (offline check).

Drives the real TimeDecaySLCalculator to prove the two smoothing levers (behind
smooth_p_win_enabled, default off) without removing the exit:

  (a) Edge-triggered regime penalty: with smoothing OFF a single regime mismatch
      halves p_win; with smoothing ON a single flicker does NOT (no penalty until
      the mismatch is sustained for N ticks).
  (b) Recovery guard: a near-breakeven trade making a new local high is HELD
      (not force-closed) when smoothing is ON; with smoothing OFF the same trade
      is force-closed — proving the guard is what saves a recovering trade.
      A trade that is not recovering is still cut.

Read-only. Dollar verdict provisional until 1.3 runs.
Run: python3 verify_pf_lc_top15_3_1.py
"""
import sys

from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator


def _cfg(smooth):
    return TimeDecayConfig(
        smooth_p_win_enabled=smooth,
        p_win_regime_edge_trigger_enabled=True,
        p_win_regime_penalty_sustained_ticks=3,
        p_win_recovery_guard_enabled=True,
        p_win_recovery_guard_be_band_pct=0.5,
        p_win_recovery_guard_n_ticks=3,
    )


def _fresh_state(calc):
    s = calc.create_state(
        symbol="ENAUSDT", direction="Buy", entry_price=100.0,
        original_sl_pct=2.0, max_hold_seconds=2700, atr_5m_pct=0.5,
        regime_confidence=0.6, tick_seconds=5.0,
        entry_xray_confidence=0.65, entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up", entry_regime_confidence=0.70,
    )
    s.p_win = 0.5
    s.prev_pnl_pct = 0.0
    s.mae_pct = 0.0
    return s


def _run():
    fails = []

    # ── (a) edge-triggered regime penalty (drive _update_p_win directly) ──
    # OFF: a single mismatch halves p_win (0.5 -> 0.30).
    calc_off = TimeDecaySLCalculator(_cfg(False))
    s = _fresh_state(calc_off)
    calc_off._update_p_win(s, current_pnl_pct=0.0, regime_still_supports=False)
    if abs(s.p_win - 0.30) > 1e-6:
        fails.append(f"off: single mismatch should give 0.30, got {s.p_win:.4f}")

    # ON: a single mismatch applies NO penalty (still inside the flicker window).
    calc_on = TimeDecaySLCalculator(_cfg(True))
    s = _fresh_state(calc_on)
    calc_on._update_p_win(s, current_pnl_pct=0.0, regime_still_supports=False)
    if abs(s.p_win - 0.50) > 1e-6:
        fails.append(f"on: a single flicker must NOT penalize, got {s.p_win:.4f}")
    # ON: sustained mismatch (3rd consecutive tick) DOES penalize.
    calc_on._update_p_win(s, current_pnl_pct=0.0, regime_still_supports=False)  # streak 2
    p_before = s.p_win
    calc_on._update_p_win(s, current_pnl_pct=0.0, regime_still_supports=False)  # streak 3
    if not (s.p_win < p_before - 1e-9):
        fails.append("on: a sustained 3-tick mismatch must apply the penalty")

    # ── (b) recovery guard (drive calculate to the force-close sentinel) ──
    def _decide(smooth, *, recovering):
        calc = TimeDecaySLCalculator(_cfg(smooth))
        s = _fresh_state(calc)
        s.p_win = 0.06               # near-certain-loser band (<=0.10 → yields)
        s.mae_pct = -1.1             # deep past excursion; passes Phase-2 gate
        s.prev_pnl_pct = -0.2
        s.recent_pnl = ([-0.6, -0.5, -0.4] if recovering
                        else [-0.1, -0.15, -0.2])  # rising vs falling
        return calc.calculate(
            s, current_pnl_pct=-0.2, position_age_seconds=400,
            regime_still_supports=True,          # avoid the regime penalty here
            velocity_pct_per_s=0.0, acceleration_pct_per_s2=0.0,
            structural_invalidation=False,       # no real failure signal
            invalidation_reason="stable",
        )

    if _decide(True, recovering=True) is not None:
        fails.append("on+recovering: the guard must HOLD (None), not cut")
    cut = _decide(True, recovering=False)
    if cut != -1.0:
        fails.append(f"on+not-recovering: should still CUT (-1.0), got {cut}")
    off_cut = _decide(False, recovering=True)
    if off_cut != -1.0:
        fails.append(f"off+recovering: without the guard it CUTS (-1.0), got {off_cut}")

    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 3.1 win-prob smoothing verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 3.1: a single regime flicker no longer halves p_win (penalty "
          "only on a sustained mismatch); a near-breakeven trade making a new high is "
          "held when smoothing is on (and cut when off), while a non-recovering trade "
          "is still cut. The exit is smoothed, not removed. Provisional until 1.3.")
