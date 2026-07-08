"""P7 — BybitDemoOrderService + BybitDemoPositionService persist via trading_repo.

Surgical tests: place_order calls save_order; close_position calls
save_order + save_trade + save_position.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoOrderService,
    BybitDemoPositionService,
)
from src.core.types import OrderType, Side


@pytest.mark.asyncio
async def test_place_order_persists_via_trading_repo() -> None:
    client = MagicMock()
    # set_leverage post + main place_order post + _resolve_order_fill GET.
    client.post = AsyncMock(side_effect=[
        {"result": {}},  # set_leverage (idempotent)
        {"result": {"orderId": "OID-PLC-1"}},  # /v5/order/create
    ])
    client.get = AsyncMock(return_value={
        "result": {"list": [{"avgPrice": "80000", "cumExecQty": "0.01", "orderStatus": "Filled"}]},
    })

    repo = MagicMock()
    repo.save_order = AsyncMock()

    svc = BybitDemoOrderService(client, trading_repo=repo)
    order = await svc.place_order(
        symbol="BTCUSDT", side=Side.BUY, order_type=OrderType.MARKET,
        qty=0.01, leverage=5, purpose="layer3_entry",
    )

    repo.save_order.assert_called_once()
    saved_order = repo.save_order.call_args.args[0]
    assert saved_order.order_id == "OID-PLC-1"
    assert order.order_id == "OID-PLC-1"


@pytest.mark.asyncio
async def test_close_position_persists_order_trade_and_position() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        # get_position
        {"result": {"list": [{
            "symbol": "BTCUSDT", "size": "0.01", "side": "Buy",
            "avgPrice": "80000", "markPrice": "80100", "leverage": "5",
            "positionValue": "800", "unrealisedPnl": "1.0",
            "createdTime": "1714000000000", "updatedTime": "1714000100000",
        }]}},
        # _resolve_close_fill GET
        {"result": {"list": [{
            "avgPrice": "80250.75", "cumExecQty": "0.01", "orderStatus": "Filled",
        }]}},
    ])
    client.post = AsyncMock(return_value={"result": {"orderId": "OID-CLS-1"}})

    repo = MagicMock()
    repo.save_order = AsyncMock()
    repo.save_trade = AsyncMock()
    repo.save_position = AsyncMock()

    svc = BybitDemoPositionService(client, trading_repo=repo)
    await svc.close_position("BTCUSDT")

    # CRITICAL-3 fix (2026-05-09): trade_history persistence MOVED out of
    # the adapter into a coordinator-level _trade_history_close_callback
    # registered in workers/manager.py. The callback fires for ALL
    # coordinator close paths (WS event, watchdog poll, sniper, time-decay)
    # — same fan-out pattern as trade_log/intelligence/thesis. The
    # adapter still persists `orders` and `positions` directly because
    # those tables don't have a coordinator close-callback path.
    #
    # I4 of cascade-fix series (2026-05-10): BybitDemoPositionService.
    # get_positions now also persists open positions (parity with live
    # PositionService:54-80). close_position internally calls
    # get_position → get_positions(symbol=...), which fires save_position
    # ONCE for the open state before close_position itself fires
    # save_position with size==0. So the total save_position call count
    # is now 2 (was 1 pre-I4): one open-state INSERT, one
    # delete-on-zero. The final-state assertion below remains correct
    # — the LAST call must be the zero-size delete.
    repo.save_order.assert_called_once()
    repo.save_trade.assert_not_called()  # CRITICAL-3: now via coord callback
    assert repo.save_position.await_count == 2, (
        "Expected exactly 2 save_position calls: "
        "one from get_positions (open state, I4) and one from "
        "close_position (size=0 delete). Got "
        f"{repo.save_position.await_count}."
    )

    # The last save_position call must be the zero-size delete (close
    # path runs after get_position returns). Pin the final-state
    # contract.
    last_pos = repo.save_position.await_args_list[-1].args[0]
    assert last_pos.size == 0
    # And both calls must tag exchange_mode='bybit_demo' (I4 contract).
    for call in repo.save_position.await_args_list:
        assert call.kwargs.get("exchange_mode") == "bybit_demo"


@pytest.mark.asyncio
async def test_place_order_no_repo_skips_persistence() -> None:
    """trading_repo=None preserves backward-compat (legacy callers)."""
    client = MagicMock()
    client.post = AsyncMock(return_value={"result": {"orderId": "OID-X"}})
    client.get = AsyncMock(return_value={"result": {"list": []}})

    svc = BybitDemoOrderService(client, trading_repo=None)
    order = await svc.place_order(
        symbol="BTCUSDT", side=Side.BUY, order_type=OrderType.MARKET, qty=0.01,
    )

    assert order.order_id == "OID-X"
    # No exception raised.
