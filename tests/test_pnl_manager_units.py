"""PnL-truth fix completion (2026-05-26): DailyPnLManager.on_trade_closed must
keep realized PnL in DOLLARS (drives the dashboard $ and the halt %) while the
best/worst/average-trade stats stay in PERCENT (rendered as % on the dashboard).

Pairs with IMPLEMENT_PNL_TRUTH_AND_DISABLE_OVERTIGHTENING.md (Issue 1).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.strategies.pnl_manager import DailyPnLManager


def _mgr() -> DailyPnLManager:
    settings = SimpleNamespace(
        pnl_targets=SimpleNamespace(
            daily_target_pct=5.0,
            protect_threshold_pct=3.0,
            caution_threshold_pct=-1.0,
            survival_threshold_pct=-3.0,
            halt_threshold_pct=-5.0,
        )
    )
    # db=None -> _persist_daily_pnl returns early; account_service=None -> no wallet fetch.
    return DailyPnLManager(settings=settings, account_service=None, position_service=None, db=None)


@pytest.mark.asyncio
async def test_realized_is_dollars_stats_are_percent() -> None:
    m = _mgr()
    # A win: +$10.09 net, +0.617% on its notional.
    await m.on_trade_closed(10.09, symbol="BTCUSDT", pnl_pct=0.617)
    # A loss: -$8.89 net, -0.631%.
    await m.on_trade_closed(-8.89, symbol="ETHUSDT", pnl_pct=-0.631)

    # realized_pnl is DOLLARS (10.09 - 8.89 = 1.20), not a percent-sum.
    assert m.realized_pnl == pytest.approx(1.20)
    # best/worst/avg are PERCENT (what the dashboard renders via _format_pct).
    assert m._best_trade_pct == pytest.approx(0.617)
    assert m._worst_trade_pct == pytest.approx(-0.631)
    assert m._avg_win_pct == pytest.approx(0.617)
    assert m._avg_loss_pct == pytest.approx(0.631)
    # Win/loss decided by the DOLLAR (post-fee) outcome.
    assert m._wins_today == 1
    assert m._losses_today == 1
    # Per-coin pnl is in DOLLARS.
    assert m._per_coin_stats["BTCUSDT"]["pnl"] == pytest.approx(10.09)
    assert m._per_coin_stats["ETHUSDT"]["pnl"] == pytest.approx(-8.89)


@pytest.mark.asyncio
async def test_win_loss_follows_dollars_not_percent() -> None:
    """A trade green on price but net-negative after fees counts as a LOSS
    (the dollar outcome is authoritative)."""
    m = _mgr()
    await m.on_trade_closed(-0.20, symbol="ARBUSDT", pnl_pct=0.05)  # +0.05% gross, -$0.20 net
    assert m._losses_today == 1
    assert m._wins_today == 0
    assert m.realized_pnl == pytest.approx(-0.20)


@pytest.mark.asyncio
async def test_legacy_call_without_pct_keeps_working() -> None:
    """Legacy callers passing only a dollar figure must not break; the
    percent-stats just stay flat (default 0.0)."""
    m = _mgr()
    await m.on_trade_closed(5.0, symbol="SOLUSDT")
    assert m.realized_pnl == pytest.approx(5.0)
    assert m._best_trade_pct == 0.0  # no pct supplied
