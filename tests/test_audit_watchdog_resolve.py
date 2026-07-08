"""Audit-gap coverage for the watchdog/sniper self-close PnL path (Phase 1C).

``tests/test_pnl_truth_authoritative_close.py`` exercises the WS-driven
``close_with_authoritative_pnl`` wrapper (which threads ``ref_*`` + identity
hints into ``on_trade_closed``). It does NOT cover the OTHER live close path:
``position_watchdog`` / ``profit_sniper`` self-closes call
``coordinator.resolve_authoritative_pnl(..., qty=pos.size)`` directly and then
hand the resolved tuple straight into ``coordinator.on_trade_closed(...)``
WITHOUT any ``ref_*`` kwargs (see ``position_watchdog.py:3382-3411``,
``profit_sniper`` self-close sites).

Two distinct staleness protections live on this path:

  - The qty-primary gate INSIDE ``resolve_authoritative_pnl``
    (``trade_coordinator.py:1041-1063``): the coordinator holds the live
    trade size, so a closed-pnl row whose ``qty`` does not match THIS trade's
    size belongs to a different (earlier) trade — the Bybit indexer returning
    a stale row — and the resolver demotes to the caller's local fallback
    tagged ``"local_fallback_stale"``.
  - The on_trade_closed gate (``trade_coordinator.py:1346``) is GUARDED by
    ``ref_pnl_usd is not None`` and so is INERT on this path (the watchdog
    passes no ``ref_*``). The qty gate above is therefore the sole protection
    for poll-detected self-closes that carry no WS exit to compare.

These tests pin that wiring against a REAL ``TradeCoordinator``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trade_coordinator import TradeCoordinator


@pytest.fixture
def coordinator() -> TradeCoordinator:
    return TradeCoordinator()


def _register(
    coord: TradeCoordinator, symbol: str, side: str, entry: float, size: float = 100.0
) -> None:
    """Mirror tests/test_pnl_truth_authoritative_close.py::_register."""
    coord.register_trade(
        symbol=symbol,
        strategy_category="default",
        strategy_name="test",
        entry_price=entry,
        side=side,
        size=size,
    )


@pytest.mark.asyncio
async def test_resolve_qty_staleness_gate_demotes_to_local_fallback(
    coordinator: TradeCoordinator,
) -> None:
    """(a) Stale closed-pnl row (qty 999 != this trade's qty 100) is demoted.

    Mirrors the watchdog self-close: ``resolve_authoritative_pnl`` is invoked
    with ``qty=pos.size`` (the trade's real size, 100). The exchange returns a
    row for an EARLIER trade (qty 999) — the indexer-lag phantom-loss root
    cause. The qty gate at trade_coordinator.py:1050-1063 must fire and book
    the caller's local fallback, NOT the stale row's net.
    """
    # position_service mock: a stale row whose qty (999) does not match the
    # trade's size hint (100). get_last_close receives the qty hint exactly as
    # the shadow_adapter / bybit_demo signature declares it (keyword `qty=`).
    position_service = MagicMock()
    position_service.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": -250.0,    # the WRONG (stale, earlier) trade's net
        "net_pnl_pct": -25.0,
        "exit_price": 88.0,
        "qty": 999.0,             # != 100 → stale wrong-trade row
    })

    _register(coordinator, symbol="BTCUSDT", side="Buy", entry=100.0, size=100.0)

    auth_usd, auth_pct, src, auth_exit = await coordinator.resolve_authoritative_pnl(
        symbol="BTCUSDT",
        position_service=position_service,
        fallback_pnl_usd=12.5,     # the caller's trusted local mark
        fallback_pnl_pct=1.25,
        fallback_exit_price=101.0,
        qty=100.0,                 # watchdog passes pos.size
    )

    # Gate fired: booked = local fallback, NOT the stale -250 row.
    assert src == "local_fallback_stale"
    assert auth_usd == pytest.approx(12.5)
    assert auth_pct == pytest.approx(1.25)
    assert auth_exit == pytest.approx(101.0)
    # The qty hint reached the position service the way the real callers send it.
    _, kwargs = position_service.get_last_close.await_args
    assert kwargs.get("qty") == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_resolve_matching_qty_books_exchange_authoritative(
    coordinator: TradeCoordinator,
) -> None:
    """(b) Matching-qty row (qty == 100) passes the gate → exchange net flows.

    Same path, but the indexer returns THIS trade's row (qty 100). The gate
    does not fire and the exchange's authoritative net (-3.50 after fees, a
    loss even though the local mark looked like a small win) is returned with
    ``src=="exchange_authoritative"``.
    """
    position_service = MagicMock()
    position_service.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": -3.50,     # exchange NET (post-fee) — a real loss
        "net_pnl_pct": -0.35,
        "exit_price": 100.6,
        "qty": 100.0,             # == this trade's size → fresh, real row
    })

    _register(coordinator, symbol="ETHUSDT", side="Buy", entry=100.0, size=100.0)

    auth_usd, auth_pct, src, auth_exit = await coordinator.resolve_authoritative_pnl(
        symbol="ETHUSDT",
        position_service=position_service,
        fallback_pnl_usd=2.0,      # local mark (would have looked like a win)
        fallback_pnl_pct=0.2,
        fallback_exit_price=100.2,
        qty=100.0,
    )

    assert src == "exchange_authoritative"
    assert auth_usd == pytest.approx(-3.50)   # exchange NET, not the +2.0 local mark
    assert auth_pct == pytest.approx(-0.35)
    assert auth_exit == pytest.approx(100.6)  # exchange's authoritative fill price


@pytest.mark.asyncio
async def test_self_close_threading_books_resolved_net_without_ref(
    coordinator: TradeCoordinator,
) -> None:
    """(c) resolve → on_trade_closed WITHOUT ref_* mirrors the watchdog/sniper.

    This is the exact two-step the self-close sites perform
    (position_watchdog.py:3382-3411): resolve with ``qty=pos.size``, then feed
    the resolved tuple into ``on_trade_closed`` with NO ``ref_*`` kwargs. The
    booked close-callback record must carry the resolved exchange net and the
    resolved ``price_source`` straight through — the ref-guarded on_trade_closed
    staleness gate (line 1346) is correctly inert here because no ref was passed.
    """
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))

    position_service = MagicMock()
    position_service.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": -3.50,
        "net_pnl_pct": -0.35,
        "exit_price": 100.6,
        "qty": 100.0,
    })

    _register(coordinator, symbol="SOLUSDT", side="Buy", entry=100.0, size=100.0)

    # Step 1 — exactly as the watchdog self-close calls it.
    auth_usd, auth_pct, price_src, auth_exit = await coordinator.resolve_authoritative_pnl(
        symbol="SOLUSDT",
        position_service=position_service,
        fallback_pnl_usd=2.0,
        fallback_pnl_pct=0.2,
        qty=100.0,   # watchdog: qty=pos.size
    )
    assert price_src == "exchange_authoritative"

    # Step 2 — the self-close booking, with NO ref_* kwargs (the gap under test).
    coordinator.on_trade_closed(
        symbol="SOLUSDT",
        pnl_pct=auth_pct,
        pnl_usd=auth_usd,
        was_win=auth_usd > 0,
        closed_by="watchdog",
        exit_price=auth_exit,
        price_source=price_src,
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec["pnl_usd"] == pytest.approx(-3.50)         # resolved exchange net booked
    assert rec["was_win"] is False                        # net loss, not the local +win
    assert rec["price_source"] == "exchange_authoritative"
    assert rec["close_price"] == pytest.approx(100.6)     # resolved exit, not back-derived
