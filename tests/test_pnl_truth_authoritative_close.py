"""Focused tests for the PnL-truth fix (2026-05-26, operator "truth everywhere").

``TradeCoordinator.close_with_authoritative_pnl`` must book the exchange's
real net ``closedPnl`` — overriding the gross price back-derive that made
the dashboard report +$65 for a window in which the wallet lost ~$2,274 —
and must fall back cleanly to the prior gross back-derive when no
authoritative data is available, so a close is never lost.

Pairs with IMPLEMENT_PNL_TRUTH_AND_DISABLE_OVERTIGHTENING.md, Issue 1.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trade_coordinator import TradeCoordinator


@pytest.fixture
def coordinator() -> TradeCoordinator:
    return TradeCoordinator()


def _register(coord: TradeCoordinator, symbol: str, side: str, entry: float, size: float = 100.0) -> None:
    coord.register_trade(
        symbol=symbol,
        strategy_category="default",
        strategy_name="test",
        entry_price=entry,
        side=side,
        size=size,
    )


@pytest.mark.asyncio
async def test_books_exchange_net_overriding_gross(coordinator: TradeCoordinator) -> None:
    """Exchange reports authoritative net → the close books NET, not gross.

    A Buy 100 -> 101 with size 100 looks like +$100 gross from prices, but
    the exchange reports -$3.50 net (fees + slippage turned a marginal move
    into a real loss). The booked record must carry -$3.50 and was_win
    False — the exact failure mode that hid ~$1,800 of real loss.
    """
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))

    tf = MagicMock()
    tf.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": -3.50,
        "net_pnl_pct": -0.35,
        "exit_price": 101.0,
    })
    coordinator._transformer = tf

    _register(coordinator, symbol="BTCUSDT", side="Buy", entry=100.0, size=100.0)
    await coordinator.close_with_authoritative_pnl(
        symbol="BTCUSDT", exit_price=101.0, closed_by="bybit_sl_hit",
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec["pnl_usd"] == pytest.approx(-3.50)   # exchange NET, not +100 gross
    assert rec["was_win"] is False
    tf.get_last_close.assert_awaited_once_with("BTCUSDT")


@pytest.mark.asyncio
async def test_falls_back_to_gross_when_no_transformer(coordinator: TradeCoordinator) -> None:
    """No transformer wired → degrade to the prior sentinel/back-derive so
    the close is never lost. Buy 100 -> 101 size 100 back-derives +1% / +$100."""
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))
    coordinator._transformer = None

    _register(coordinator, symbol="ETHUSDT", side="Buy", entry=100.0, size=100.0)
    await coordinator.close_with_authoritative_pnl(
        symbol="ETHUSDT", exit_price=101.0, closed_by="bybit_sl_hit",
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec["pnl_pct"] == pytest.approx(1.0)
    assert rec["pnl_usd"] == pytest.approx(100.0)
    assert rec["was_win"] is True


@pytest.mark.asyncio
async def test_identity_confirmed_books_exchange_loss_despite_exit_divergence(
    coordinator: TradeCoordinator,
) -> None:
    """PnL-truth fix (2026-06-07): the DOGE sign-flip case.

    In ws_exec mode with a close order_id, the exchange row IS this trade.
    Its authoritative post-slippage exit (101.0) legitimately differs from the
    locally observed WS exit (100.0) and its NET is a loss (-4.36) while the
    local gross looks like a win. The booked record MUST be the exchange loss,
    NOT demoted to the local gross by the exit-divergence gate.
    """
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))

    tf = MagicMock()
    tf.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": -4.36,
        "net_pnl_pct": -0.0616,
        "exit_price": 101.0,   # exchange fill differs from ws exit 100.0
    })
    coordinator._transformer = tf

    _register(coordinator, symbol="DOGEUSDT", side="Buy", entry=99.0, size=100.0)
    await coordinator.close_with_authoritative_pnl(
        symbol="DOGEUSDT", exit_price=100.0, closed_by="bybit_sl_hit",
        ws_order_id="oid-doge-1", ws_exec_qty=100.0, close_pnl_source="ws_exec",
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec["pnl_usd"] == pytest.approx(-4.36)        # exchange NET loss booked
    assert rec["was_win"] is False
    assert rec["price_source"] == "exchange_authoritative"  # NOT demoted


@pytest.mark.asyncio
async def test_legacy_still_demotes_on_exit_divergence(
    coordinator: TradeCoordinator,
) -> None:
    """The stale-row protection is preserved for non-identity-confirmed callers.

    Same divergent exit, but in legacy mode (no order_id) the row cannot be
    proven to be this trade, so the exit-divergence gate still demotes to the
    local reference (the existing phantom-loss protection must not regress).
    """
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))

    tf = MagicMock()
    tf.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": -4.36,
        "net_pnl_pct": -0.0616,
        "exit_price": 101.0,
    })
    coordinator._transformer = tf

    _register(coordinator, symbol="DOGEUSDT", side="Buy", entry=99.0, size=100.0)
    await coordinator.close_with_authoritative_pnl(
        symbol="DOGEUSDT", exit_price=100.0, closed_by="bybit_sl_hit",
        close_pnl_source="legacy",
    )

    assert len(captured) == 1
    rec = captured[0]
    # demoted to the local reference (not the exchange row)
    assert rec["price_source"] == "local_fallback_stale"


@pytest.mark.asyncio
async def test_falls_back_when_exchange_returns_none(coordinator: TradeCoordinator) -> None:
    """Transformer present but exchange has no closed row yet (indexer lag)
    → fall back to gross back-derive rather than losing the close.
    Sell 100 -> 99 = +1% win, back-derived."""
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))
    tf = MagicMock()
    tf.get_last_close = AsyncMock(return_value=None)
    coordinator._transformer = tf

    _register(coordinator, symbol="SOLUSDT", side="Sell", entry=100.0, size=100.0)
    await coordinator.close_with_authoritative_pnl(
        symbol="SOLUSDT", exit_price=99.0, closed_by="wd_timeout",
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec["pnl_pct"] == pytest.approx(1.0)
    assert rec["was_win"] is True
