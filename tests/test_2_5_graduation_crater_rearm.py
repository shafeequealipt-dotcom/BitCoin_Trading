"""Issue 2.5 (2026-06-07) — re-arm loss-cutting when a graduated trade craters.

Pure-logic mirror of the re-arm condition in profit_sniper (the one-shot gate
that re-opens the loss-cutting block for a graduated trade that has dropped to a
real loss). Confirms a climbing winner never re-arms and the latch is one-shot.
"""
from __future__ import annotations


def _rearm(*, enabled: bool, cur_pnl_pct: float, loss_thresh: float, already: bool) -> bool:
    """Mirror: rearm iff enabled AND not-already AND current pnl is a real loss."""
    return enabled and (not already) and cur_pnl_pct <= -loss_thresh


def test_disabled_never_rearms():
    assert _rearm(enabled=False, cur_pnl_pct=-5.0, loss_thresh=0.5, already=False) is False


def test_graduated_crater_rearms():
    assert _rearm(enabled=True, cur_pnl_pct=-0.6, loss_thresh=0.5, already=False) is True


def test_climbing_winner_never_rearms():
    # a graduated trade still in profit (or only mildly down) does NOT re-arm,
    # so a genuine winner is never cut.
    assert _rearm(enabled=True, cur_pnl_pct=+1.2, loss_thresh=0.5, already=False) is False
    assert _rearm(enabled=True, cur_pnl_pct=-0.3, loss_thresh=0.5, already=False) is False


def test_one_shot_no_whipsaw():
    # once armed, it does not re-fire (the tracked flag is sticky).
    assert _rearm(enabled=True, cur_pnl_pct=-0.6, loss_thresh=0.5, already=True) is False
