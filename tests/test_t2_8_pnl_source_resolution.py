"""T2-8 Two-source PnL contradiction resolution tests (2026-05-12).

Pre-fix bug (F65): a single COORD_CLOSE event reported
    pnl=+0.0958%  pnl$=-0.6174  win=N
Three sources disagreed: percentage POSITIVE (suggests win), dollar
amount NEGATIVE (loss after fees), win flag N (declared loss).
Coordinator preserved caller-supplied values without cross-check;
downstream consumers (TIAS, performance_enforcer, dashboard) might
pick the wrong source.

Fix: when signs of pnl_pct and pnl_usd disagree, treat pnl_usd as
authoritative (Bybit's realized PnL with fees). Back-derive pnl_pct
from pnl_usd / notional. `was_win` derives from pnl_usd. Always
emit PNL_SOURCE_RESOLVED so the resolution is greppable.

Tests are pure-logic — they exercise the resolution math without
requiring the full coordinator wiring.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _resolution(
    pnl_pct: float, pnl_usd: float, notional: float,
) -> tuple[float, float, bool, bool]:
    """Mirror of the inline T2-8 resolution block in
    TradeCoordinator.on_trade_closed.

    Returns ``(final_pnl_pct, final_pnl_usd, was_win, sign_mismatch)``.
    """
    was_win = pnl_pct > 0
    sign_mismatch = False
    if pnl_pct != 0 and pnl_usd != 0 and ((pnl_pct > 0) != (pnl_usd > 0)):
        sign_mismatch = True
        if notional > 0:
            pnl_pct = pnl_usd / notional * 100
            was_win = pnl_usd > 0
        else:
            was_win = pnl_usd > 0
    return pnl_pct, pnl_usd, was_win, sign_mismatch


def test_t2_8_f65_replication_resolves_to_loss():
    """F65 replication: pnl_pct=+0.0958, pnl_usd=-0.6174,
    notional ~$650 (typical mode4-partial-fallback-full notional).
    Expected: was_win=False, pnl_pct back-derived to negative."""
    pnl_pct, pnl_usd, was_win, mismatch = _resolution(
        pnl_pct=0.0958, pnl_usd=-0.6174, notional=650.0,
    )
    assert mismatch is True
    assert was_win is False
    assert pnl_pct < 0
    # back-derived: -0.6174 / 650 * 100 ≈ -0.095%
    assert abs(pnl_pct - (-0.6174 / 650.0 * 100)) < 1e-9


def test_t2_8_consistent_signs_no_resolution():
    """Sign-consistent inputs (both positive or both negative) → no
    mismatch fires, values preserved."""
    pnl_pct, pnl_usd, was_win, mismatch = _resolution(
        pnl_pct=2.5, pnl_usd=15.0, notional=600.0,
    )
    assert mismatch is False
    assert pnl_pct == 2.5
    assert was_win is True

    pnl_pct, pnl_usd, was_win, mismatch = _resolution(
        pnl_pct=-1.2, pnl_usd=-7.2, notional=600.0,
    )
    assert mismatch is False
    assert pnl_pct == -1.2
    assert was_win is False


def test_t2_8_zero_notional_falls_back_to_was_win_only():
    """When notional is 0 (no size + no _trade_info), pnl_pct cannot
    be back-derived. The mismatch is still flagged + was_win uses
    pnl_usd as authoritative — consumers see the right loss flag
    even when the numerical pct is unrecoverable."""
    pnl_pct, pnl_usd, was_win, mismatch = _resolution(
        pnl_pct=0.5, pnl_usd=-2.0, notional=0.0,
    )
    assert mismatch is True
    assert was_win is False  # uses pnl_usd
    # pnl_pct preserved (no back-derive possible)
    assert pnl_pct == 0.5


def test_t2_8_zero_pnl_pct_does_not_trigger_mismatch():
    """When pnl_pct is exactly 0 (the back-derive sentinel from earlier
    in the function), the T2-8 mismatch check skips. The earlier
    COORD_PNL_BACK_DERIVED block already resolved this case."""
    pnl_pct, pnl_usd, was_win, mismatch = _resolution(
        pnl_pct=0.0, pnl_usd=-0.6174, notional=650.0,
    )
    assert mismatch is False
    assert was_win is False  # was_win = pnl_pct > 0 = False
    assert pnl_pct == 0.0


def test_t2_8_zero_pnl_usd_does_not_trigger_mismatch():
    """When pnl_usd is exactly 0, no mismatch — preserves the legacy
    pnl_pct-derived was_win."""
    pnl_pct, pnl_usd, was_win, mismatch = _resolution(
        pnl_pct=2.5, pnl_usd=0.0, notional=600.0,
    )
    assert mismatch is False
    assert was_win is True
    assert pnl_pct == 2.5


def test_t2_8_back_derived_pnl_pct_matches_pnl_usd_sign():
    """Resolution invariant: post-fix, sign(pnl_pct) == sign(pnl_usd)
    whenever notional > 0. This is the exact contract downstream
    consumers (TIAS, performance_enforcer) rely on."""
    cases = [
        (-1.5, 5.0, 600.0),    # caller: pnl_pct=-1.5, pnl_usd=+5
        (1.5, -5.0, 600.0),    # caller: pnl_pct=+1.5, pnl_usd=-5
        (0.05, -2.0, 100.0),   # F65-shape
    ]
    for pre_pct, usd, notional in cases:
        pnl_pct, _, was_win, mismatch = _resolution(pre_pct, usd, notional)
        assert mismatch is True
        assert (pnl_pct > 0) == (usd > 0), (
            f"sign-consistency violated for ({pre_pct}, {usd}, {notional}): "
            f"got pnl_pct={pnl_pct} usd={usd}"
        )
        assert was_win == (usd > 0)


def test_t2_8_back_derived_pnl_pct_magnitude_matches_notional():
    """The back-derived pnl_pct equals exactly pnl_usd / notional * 100
    so dashboard / TIAS formatting matches the dollar outcome to the bp."""
    pnl_pct, _, _, _ = _resolution(
        pnl_pct=2.0, pnl_usd=-12.0, notional=1000.0,
    )
    assert pnl_pct == -1.2  # -12 / 1000 * 100
