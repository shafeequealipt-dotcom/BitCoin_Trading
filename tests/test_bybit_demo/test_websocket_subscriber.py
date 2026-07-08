"""P1 Phase 3b — BybitDemoWebSocketSubscriber integration test.

One surgical integration test covering parse + dedup + dispatch end-to-end.
Mocks pybit's WebSocket; feeds synthetic execution events; verifies
coordinator.on_trade_closed receives correct args exactly once even
when the same event is re-emitted (dedup gate).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bybit_demo.bybit_demo_websocket_subscriber import (
    BybitDemoWebSocketSubscriber,
)


def _make_settings():
    return SimpleNamespace(
        bybit=SimpleNamespace(
            testnet=False, api_key="LK", api_secret="LS", ws_reconnect_delay=5,
        ),
        bybit_demo=SimpleNamespace(api_key="DK", api_secret="DS"),
    )


def _exec_event(
    *,
    symbol="BTCUSDT",
    order_id="OID-12345",
    closed_size="0.01",
    leaves_qty="0",
    exec_price="80000.50",
    exec_qty="0.01",
    exec_fee="0.0048",
    side="Sell",
    stop_order_type="StopLoss",
):
    return {
        "topic": "execution",
        "data": [{
            "symbol": symbol,
            "orderId": order_id,
            "closedSize": closed_size,
            "leavesQty": leaves_qty,
            "execPrice": exec_price,
            "execQty": exec_qty,
            "execFee": exec_fee,
            "side": side,
            "stopOrderType": stop_order_type,
        }],
    }


@pytest.mark.asyncio
async def test_subscriber_dispatches_close_then_dedups_replay() -> None:
    """End-to-end: SL execution event arrives, coordinator.on_trade_closed
    fires with bybit_ws_authoritative price_source. Re-emit suppressed by
    L1 TTL dedup; coordinator only called once.
    """
    coordinator = MagicMock()
    coordinator.pop_close_reason = MagicMock(return_value="")
    # A bare MagicMock makes pop_partial_close_pending return a truthy mock,
    # which wrongly routes to the partial-close branch; pin it to None so
    # this exercises the FULL-close path.
    coordinator.pop_partial_close_pending = MagicMock(return_value=None)
    # PnL-truth fix (2026-05-26): the WS full-close path now routes through
    # coordinator.close_with_authoritative_pnl (async) instead of calling
    # on_trade_closed directly.
    coordinator.close_with_authoritative_pnl = AsyncMock()

    loop = asyncio.get_running_loop()
    settings = _make_settings()
    db = MagicMock()

    # Construct subscriber. The internal BybitWebSocket is created in
    # __init__ — we patch it to a MagicMock so connect() can be called
    # safely without a live network.
    with patch(
        "src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket",
    ) as patched_ws_cls:
        fake_ws = MagicMock()
        fake_ws.connect_private = AsyncMock()
        fake_ws.disconnect = AsyncMock()
        fake_ws._private_ws = MagicMock()
        patched_ws_cls.return_value = fake_ws

        sub = BybitDemoWebSocketSubscriber(
            settings=settings,
            db=db,
            coordinator=coordinator,
            loop=loop,
        )

        # Feed the execution event directly into the handler. This
        # bypasses connect() (no need for a live WS) and exercises the
        # parse → dedup → dispatch path.
        sub._handle_execution(_exec_event())
        # Allow the run_coroutine_threadsafe-scheduled coroutine to run.
        await asyncio.sleep(0.05)

        # The close is dispatched once, via close_with_authoritative_pnl
        # (which resolves the real net closedPnl internally, then calls
        # on_trade_closed). price_source is resolved inside, not passed here.
        coordinator.close_with_authoritative_pnl.assert_awaited_once()
        kwargs = coordinator.close_with_authoritative_pnl.call_args.kwargs
        assert kwargs["symbol"] == "BTCUSDT"
        assert kwargs["exit_price"] == 80000.50
        assert kwargs["closed_by"] == "bybit_sl_hit"

        # Dedup test: re-emit identical event within 5s window.
        sub._handle_execution(_exec_event())
        await asyncio.sleep(0.05)

        # Close STILL dispatched only once (L1 dedup suppressed the replay).
        assert coordinator.close_with_authoritative_pnl.await_count == 1
        # And dedup counter incremented.
        snap = sub.get_health_snapshot()
        assert snap["dedup_count"] == 1


@pytest.mark.asyncio
async def test_subscriber_skips_partial_fills() -> None:
    """Execution event with leavesQty>0 must NOT trigger on_trade_closed
    — the position is still open after the partial fill.
    """
    coordinator = MagicMock()
    coordinator.on_trade_closed = MagicMock()
    loop = asyncio.get_running_loop()

    with patch("src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket"):
        sub = BybitDemoWebSocketSubscriber(
            settings=_make_settings(),
            db=MagicMock(),
            coordinator=coordinator,
            loop=loop,
        )
        # Partial fill: closed 0.005 but 0.005 still leaves
        sub._handle_execution(_exec_event(closed_size="0.005", leaves_qty="0.005"))
        await asyncio.sleep(0.05)

        coordinator.on_trade_closed.assert_not_called()


@pytest.mark.asyncio
async def test_subscriber_uses_pop_close_reason_when_no_stop_order_type() -> None:
    """When stopOrderType is empty (system-initiated close), closed_by
    comes from coordinator.pop_close_reason; falls back to
    'bybit_external' when coordinator has no reason set.
    """
    coordinator = MagicMock()
    coordinator.pop_close_reason = MagicMock(return_value="strategic_review")
    coordinator.pop_partial_close_pending = MagicMock(return_value=None)  # full close
    coordinator.close_with_authoritative_pnl = AsyncMock()
    loop = asyncio.get_running_loop()

    with patch("src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket"):
        sub = BybitDemoWebSocketSubscriber(
            settings=_make_settings(),
            db=MagicMock(),
            coordinator=coordinator,
            loop=loop,
        )
        sub._handle_execution(_exec_event(stop_order_type=""))
        await asyncio.sleep(0.05)

        coordinator.close_with_authoritative_pnl.assert_awaited_once()
        assert coordinator.close_with_authoritative_pnl.call_args.kwargs["closed_by"] == "strategic_review"
