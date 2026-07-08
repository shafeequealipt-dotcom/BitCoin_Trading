"""Smoke test for BybitDemoOrderService — request body translation only.

Integration tests that hit api-demo.bybit.com are gated by the
``BYBIT_DEMO_INTEGRATION=1`` env var (Phase 2.F) so this file can run
in CI without credentials.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoOrderService,
    _build_order_from_v5,
    _parse_side,
    _rejected_order,
    _safe_float,
)
from src.core.exceptions import InsufficientBalanceError
from src.core.types import Order, OrderStatus, OrderType, Side


class _FakeClient:
    """Stand-in for BybitDemoClient — captures POST/GET calls for assertion."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any], str]] = []
        self.gets: list[tuple[str, dict[str, Any] | None, str]] = []
        self.post_response: dict[str, Any] = {"retCode": 0, "result": {"orderId": "abc123"}}
        self.get_response: dict[str, Any] = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "abc123",
                        "symbol": "BTCUSDT",
                        "avgPrice": "50000.5",
                        "cumExecQty": "0.001",
                        "orderStatus": "Filled",
                    }
                ]
            },
        }
        self.post_raises: Exception | None = None

    async def post(self, path: str, body: dict[str, Any], *, op: str = "") -> dict[str, Any]:
        self.posts.append((path, body, op))
        if self.post_raises is not None:
            raise self.post_raises
        return self.post_response

    async def get(self, path: str, params: dict[str, Any] | None = None, *, op: str = "") -> dict[str, Any]:
        self.gets.append((path, params, op))
        return self.get_response

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_place_order_builds_v5_body_and_returns_filled() -> None:
    client = _FakeClient()
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]

    order = await svc.place_order(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=0.001,
        stop_loss=49000.0,
        take_profit=51000.0,
        leverage=10,
    )

    # Two POSTs: set-leverage + order/create. One GET: order/realtime to resolve fill.
    paths = [p[0] for p in client.posts]
    assert "/v5/position/set-leverage" in paths
    assert "/v5/order/create" in paths

    # Inspect the order/create body for V5 correctness.
    create_body = next(b for p, b, _ in client.posts if p == "/v5/order/create")
    assert create_body["category"] == "linear"
    assert create_body["symbol"] == "BTCUSDT"
    assert create_body["side"] == "Buy"
    assert create_body["orderType"] == "Market"
    assert create_body["qty"] == "0.001"
    assert create_body["positionIdx"] == 0
    assert create_body["timeInForce"] == "IOC"
    assert create_body["stopLoss"] == "49000.0"
    assert create_body["takeProfit"] == "51000.0"

    # Returned Order is FILLED with resolved avg fill price.
    assert order.status == OrderStatus.FILLED
    assert order.symbol == "BTCUSDT"
    assert order.side == Side.BUY
    assert order.order_id == "abc123"
    assert order.avg_fill_price == 50000.5
    assert order.filled_qty == 0.001


@pytest.mark.asyncio
async def test_place_order_returns_rejected_on_insufficient_balance() -> None:
    """Bybit InsufficientBalanceError → REJECTED Order, not raised."""
    client = _FakeClient()
    client.post_raises = InsufficientBalanceError(
        "Bybit demo: insufficient balance",
        details={"ret_code": 110007, "ret_msg": "Insufficient", "op": "place_order"},
    )
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]

    order = await svc.place_order(
        symbol="BTCUSDT",
        side=Side.SELL,
        order_type=OrderType.MARKET,
        qty=0.001,
    )
    # Adapter never raises — returns sentinel.
    assert order.status == OrderStatus.REJECTED
    assert order.symbol == "BTCUSDT"
    assert order.side == Side.SELL
    assert order.qty == 0.0


def test_helper_parse_side() -> None:
    assert _parse_side("Buy") == Side.BUY
    assert _parse_side("Sell") == Side.SELL
    assert _parse_side("buy") == Side.BUY
    assert _parse_side("Long") == Side.BUY
    assert _parse_side("anything_else") == Side.SELL


def test_helper_safe_float() -> None:
    assert _safe_float("1.5") == 1.5
    assert _safe_float(None) == 0.0
    assert _safe_float("") == 0.0
    assert _safe_float("not-a-number") == 0.0
    assert _safe_float(None, default=42.0) == 42.0


def test_helper_rejected_order() -> None:
    o = _rejected_order(symbol="BTCUSDT", side=Side.BUY)
    assert isinstance(o, Order)
    assert o.status == OrderStatus.REJECTED
    assert o.symbol == "BTCUSDT"
    assert o.qty == 0.0


def test_helper_build_order_from_v5() -> None:
    o = _build_order_from_v5({
        "orderId": "x1",
        "symbol": "ETHUSDT",
        "side": "Sell",
        "orderType": "Market",
        "avgPrice": "2500.0",
        "cumExecQty": "0.5",
        "qty": "0.5",
        "orderStatus": "Filled",
    })
    assert o.order_id == "x1"
    assert o.symbol == "ETHUSDT"
    assert o.side == Side.SELL
    assert o.status == OrderStatus.FILLED
    assert o.avg_fill_price == 2500.0


@pytest.mark.asyncio
async def test_partially_filled_emits_partial_fill_tag() -> None:
    """PartiallyFilled status surfaces BYBIT_DEMO_PARTIAL_FILL with ratio.

    Mapping to OrderStatus.FILLED is intentional (matches contract) — the
    new tag is purely observability so the operator can see under-fills
    on IOC market orders without trawling the order history endpoint.
    """
    from loguru import logger

    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO")
    try:
        client = _FakeClient()
        client.get_response = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "abc123",
                        "symbol": "BTCUSDT",
                        "avgPrice": "50000.5",
                        "cumExecQty": "0.0006",  # 60% fill of 0.001 requested
                        "orderStatus": "PartiallyFilled",
                    }
                ]
            },
        }
        svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
        order = await svc.place_order(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=0.001,
        )
    finally:
        logger.remove(sink_id)

    # Status still maps to FILLED for downstream contract parity.
    assert order.status == OrderStatus.FILLED
    assert order.filled_qty == 0.0006

    partial_lines = [l for l in captured if "BYBIT_DEMO_PARTIAL_FILL" in l]
    assert len(partial_lines) == 1, (
        f"Expected exactly one BYBIT_DEMO_PARTIAL_FILL line; got {partial_lines}"
    )
    line = partial_lines[0]
    assert "sym=BTCUSDT" in line
    assert "oid=abc123" in line
    assert "filled=0.0006" in line
    assert "requested=0.001" in line
    assert "ratio=0.6" in line


@pytest.mark.asyncio
async def test_filled_does_not_emit_partial_fill_tag() -> None:
    """Fully filled orders must NOT emit BYBIT_DEMO_PARTIAL_FILL."""
    from loguru import logger

    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO")
    try:
        client = _FakeClient()
        # Default get_response uses orderStatus=Filled
        svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
        await svc.place_order(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=0.001,
        )
    finally:
        logger.remove(sink_id)

    assert not any("BYBIT_DEMO_PARTIAL_FILL" in l for l in captured)
