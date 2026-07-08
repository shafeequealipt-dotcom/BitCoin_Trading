"""P3 — bounded retry on get_last_close + close_position fill resolution.

Two surgical tests:
1. get_last_close retries until indexer populates, then returns row.
2. close_position uses _resolve_close_fill avg_price (not pos.mark_price).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoPositionService,
)
from src.core.types import Side


@pytest.mark.asyncio
async def test_get_last_close_retries_until_indexer_populates() -> None:
    """First two polls return empty (indexer race), third returns the row.
    Verify the method polls 3 times then returns the populated data.
    """
    client = MagicMock()

    # Sequence: empty, empty, populated
    populated = {
        "result": {
            "list": [{
                "side": "Buy",
                "qty": "0.01",
                "avgEntryPrice": "80000",
                "avgExitPrice": "80500",
                "closedPnl": "5.00",
                "createdTime": "1714000000000",
                "updatedTime": "1714000300000",
            }]
        }
    }
    client.get = AsyncMock(side_effect=[
        {"result": {"list": []}},
        {"result": {"list": []}},
        populated,
    ])

    svc = BybitDemoPositionService(client)
    # Patch sleep to avoid 1s wait per attempt in the test.
    import src.bybit_demo.bybit_demo_adapter as adapter_mod
    original_sleep = adapter_mod.asyncio.sleep
    adapter_mod.asyncio.sleep = AsyncMock()
    try:
        result = await svc.get_last_close("BTCUSDT")
    finally:
        adapter_mod.asyncio.sleep = original_sleep

    assert result is not None
    assert result["exit_price"] == 80500.0
    assert result["net_pnl_usd"] == 5.00
    assert client.get.call_count == 3


@pytest.mark.asyncio
async def test_close_position_uses_resolved_fill_price_not_mark_price() -> None:
    """close_position should call _resolve_close_fill and use the returned
    avg_price as exit_price, NOT pos.mark_price (the audit-flagged stale value).
    """
    client = MagicMock()

    # get_position response — position with mark_price=80100 (stale)
    client.get = AsyncMock(side_effect=[
        # get_position call
        {
            "result": {
                "list": [{
                    "symbol": "BTCUSDT",
                    "size": "0.01",
                    "side": "Buy",
                    "avgPrice": "80000",
                    "markPrice": "80100",  # stale
                    "leverage": "5",
                    "positionValue": "800",
                    "unrealisedPnl": "1.0",
                    "createdTime": "1714000000000",
                    "updatedTime": "1714000100000",
                }]
            }
        },
        # _resolve_close_fill /v5/order/realtime call returns actual fill
        {
            "result": {
                "list": [{
                    "avgPrice": "80250.75",  # actual fill, not stale mark
                    "cumExecQty": "0.01",
                    "orderStatus": "Filled",
                }]
            }
        },
    ])

    # post returns orderId in result
    client.post = AsyncMock(return_value={
        "result": {"orderId": "ABC-CLOSE-123"},
    })

    svc = BybitDemoPositionService(client)
    order = await svc.close_position("BTCUSDT")

    # Verify exit price comes from the resolved fill (80250.75), NOT
    # the stale mark_price (80100).
    assert order.price == 80250.75
    assert order.side == Side.SELL  # opposite of Buy entry
    assert order.qty == 0.01
