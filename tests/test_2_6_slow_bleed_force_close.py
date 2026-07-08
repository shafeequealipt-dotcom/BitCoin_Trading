"""Issue 2.6 (2026-06-07) — slow-bleed cumulative-drawdown force-close carve-out.

Pure-logic mirror of the carve-out added to TimeDecaySLCalculator.calculate: the
structural-invalidation guard YIELDS (so the force-close fires) only when the
trade is statistically dead (p_win < p_win_force_close) AND has bled past the
cumulative-loss threshold. A recovering winner carries a higher p_win, so the
low-p_win AND large-loss conjunction is a clear loser, never a winner.
"""
from __future__ import annotations


def _guard_action(*, enabled, struct_required, structural_invalidation,
                  p_win, p_win_force_close, near_certain_loser, cur_pnl_pct,
                  slow_bleed_loss_pct):
    """Returns 'cut'(force-close fires) / 'block'(held) / 'normal'(guard inactive)."""
    if not (struct_required and p_win < p_win_force_close and not structural_invalidation):
        return "normal"
    if p_win <= near_certain_loser:
        return "cut"                      # near-certain-loser carve-out (existing)
    if enabled and cur_pnl_pct <= -slow_bleed_loss_pct:
        return "cut"                      # 2.6 slow-bleed carve-out
    return "block"


BASE = dict(struct_required=True, structural_invalidation=False,
            p_win_force_close=0.15, near_certain_loser=0.10, slow_bleed_loss_pct=2.5)


def test_slow_grind_loser_is_cut_when_enabled():
    # p_win in the (0.10, 0.15] dead-but-not-near-certain band, stable structure,
    # bled to -3% -> previously BLOCKED, now CUT.
    assert _guard_action(enabled=True, p_win=0.12, cur_pnl_pct=-3.0, **BASE) == "cut"


def test_slow_grind_blocked_when_disabled():
    assert _guard_action(enabled=False, p_win=0.12, cur_pnl_pct=-3.0, **BASE) == "block"


def test_small_loss_not_cut_even_when_enabled():
    # only mildly down -> the carve-out does NOT fire; the guard still holds.
    assert _guard_action(enabled=True, p_win=0.12, cur_pnl_pct=-0.8, **BASE) == "block"


def test_healthy_pwin_never_reaches_guard():
    # a recovering / healthy trade (p_win above force-close) never enters the
    # guard at all, so it is never cut regardless of drawdown.
    assert _guard_action(enabled=True, p_win=0.40, cur_pnl_pct=-3.0, **BASE) == "normal"
