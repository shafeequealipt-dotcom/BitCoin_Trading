"""J2 (2026-05-14) — cross-direction pre-order guard tests.

Audit observation OBS-21 at 21:09:10 UTC: brain proposed Buy DYDXUSDT
while a stale local cache row showed Sell. APEX_DIR_LOCK_OVERRIDE
forced Buy back. The Buy was placed against what local state thought
was an existing Short. In one-way mode (_POSITION_IDX = 0) the result
on Bybit is a netted position whose state diverges from every local
consumer.

J1 removed the stale-cache trigger at the source. J2 adds a chokepoint
guard at BybitDemoOrderService.place_order: when a position on the
same symbol exists in TradeCoordinator._trades with the opposite side,
the order is rejected before any /v5/order/create is sent.

Pins:
  * Same-direction order is NOT blocked (legitimate add).
  * Opposite-direction order IS blocked with two structured log
    events (ORDER_CROSS_DIRECTION_BLOCKED + ORDER_BLOCKED).
  * Order returned has status=REJECTED; client.post never called.
  * force=True bypasses the guard (operator override).
  * No coordinator wired → no guard (legacy callers / tests).
  * Symbol absent from _trades → no guard (no conflict).
  * Side comparison is case-insensitive (TradeCoordinator stores the
    upstream side string which has historically varied between
    "Buy"/"BUY"/"buy").
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from loguru import logger as _loguru_logger

from src.bybit_demo.bybit_demo_adapter import BybitDemoOrderService
from src.core.types import OrderStatus, OrderType, Side


# --- Fakes ----------------------------------------------------------


class _FakeClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any], str]] = []
        self.create_response: dict[str, Any] = {
            "retCode": 0,
            "result": {"orderId": "filled_oid"},
        }
        # Realtime fill query (used by place_order to confirm fill)
        self.realtime_response: dict[str, Any] = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderStatus": "Filled",
                        "cumExecQty": "1.0",
                        "avgPrice": "100.0",
                    },
                ],
            },
        }

    async def get(self, path: str, params: dict[str, Any] | None = None, *, op: str = "") -> dict[str, Any]:
        if path == "/v5/order/realtime":
            return self.realtime_response
        return {"retCode": 0, "result": {"list": []}}

    async def post(self, path: str, body: dict[str, Any], *, op: str = "") -> dict[str, Any]:
        self.posts.append((path, body, op))
        return self.create_response


class _FakeCoordinator:
    """Minimal stand-in for TradeCoordinator. Tests populate _trades
    with namespaces that have ``.side`` attribute, matching the
    real TradeState shape."""

    def __init__(self) -> None:
        self._trades: dict[str, Any] = {}


# --- Fixtures -------------------------------------------------------


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(
            (msg.record["level"].name, msg.record["message"])
        ),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


async def _place_buy(svc: BybitDemoOrderService, **kwargs):
    return await svc.place_order(
        symbol=kwargs.pop("symbol", "BTCUSDT"),
        side=kwargs.pop("side", Side.BUY),
        order_type=OrderType.MARKET,
        qty=kwargs.pop("qty", 1.0),
        purpose=kwargs.pop("purpose", "test"),
        **kwargs,
    )


# --- Tests ----------------------------------------------------------


@pytest.mark.asyncio
async def test_same_direction_order_passes_through(loguru_sink) -> None:
    """Coordinator says Buy exists; new order is also Buy → not blocked."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    coord._trades["BTCUSDT"] = SimpleNamespace(side="Buy")
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(coord)

    order = await _place_buy(svc, symbol="BTCUSDT", side=Side.BUY)

    assert order.status == OrderStatus.FILLED
    # No block event
    assert _records_with_tag(loguru_sink, "ORDER_CROSS_DIRECTION_BLOCKED") == []
    # HTTP post happened
    assert any(p[0] == "/v5/order/create" for p in client.posts)


@pytest.mark.asyncio
async def test_opposite_direction_order_is_blocked(loguru_sink) -> None:
    """Coordinator has Sell; new Buy is blocked. No HTTP submit."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    coord._trades["DYDXUSDT"] = SimpleNamespace(side="Sell")
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(coord)

    order = await _place_buy(svc, symbol="DYDXUSDT", side=Side.BUY)

    assert order.status == OrderStatus.REJECTED
    assert order.symbol == "DYDXUSDT"
    # No HTTP submit
    assert all(p[0] != "/v5/order/create" for p in client.posts)
    # Two structured log events
    blocked = _records_with_tag(loguru_sink, "ORDER_CROSS_DIRECTION_BLOCKED")
    assert len(blocked) == 1
    kv = _parse_kv(blocked[0][1])
    assert kv["sym"] == "DYDXUSDT"
    assert kv["existing_side"] == "Sell"
    assert kv["new_side"] == "Buy"
    unified = [
        r for r in _records_with_tag(loguru_sink, "ORDER_BLOCKED")
        if "cross_direction_conflict" in r[1]
    ]
    assert len(unified) == 1


@pytest.mark.asyncio
async def test_force_true_bypasses_guard(loguru_sink) -> None:
    """force=True is the operator-override path; the guard must not
    fire even when a cross-direction conflict exists."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    coord._trades["DYDXUSDT"] = SimpleNamespace(side="Sell")
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(coord)

    order = await _place_buy(svc, symbol="DYDXUSDT", side=Side.BUY, force=True)

    assert order.status == OrderStatus.FILLED
    assert _records_with_tag(loguru_sink, "ORDER_CROSS_DIRECTION_BLOCKED") == []
    # HTTP submit DID happen
    assert any(p[0] == "/v5/order/create" for p in client.posts)


@pytest.mark.asyncio
async def test_no_coordinator_wired_falls_through(loguru_sink) -> None:
    """Legacy callers / tests do not attach a coordinator; the guard
    must silently skip rather than crash."""
    client = _FakeClient()
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    # No attach_coordinator call

    order = await _place_buy(svc, symbol="BTCUSDT", side=Side.BUY)

    assert order.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_symbol_absent_from_trades_passes(loguru_sink) -> None:
    """Empty coordinator state is the no-existing-position case;
    guard must not fire."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(coord)

    order = await _place_buy(svc, symbol="BTCUSDT", side=Side.BUY)

    assert order.status == OrderStatus.FILLED
    assert _records_with_tag(loguru_sink, "ORDER_CROSS_DIRECTION_BLOCKED") == []


@pytest.mark.asyncio
async def test_side_comparison_is_case_insensitive(loguru_sink) -> None:
    """TradeCoordinator stores the upstream side string which has
    varied between 'Buy', 'BUY', and 'buy'. Guard must normalize."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    coord._trades["BTCUSDT"] = SimpleNamespace(side="SELL")  # uppercase
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(coord)

    order = await _place_buy(svc, symbol="BTCUSDT", side=Side.BUY)

    assert order.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_legitimate_flip_after_close_works(loguru_sink) -> None:
    """The 'close then reopen opposite' pattern must still work.
    Simulate by registering a Sell, then removing it (close), then
    placing a Buy — guard must not fire."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    coord._trades["BTCUSDT"] = SimpleNamespace(side="Sell")
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(coord)

    # Operator/system closes the existing position — coordinator
    # pops the symbol.
    coord._trades.pop("BTCUSDT", None)

    order = await _place_buy(svc, symbol="BTCUSDT", side=Side.BUY)

    assert order.status == OrderStatus.FILLED
    assert _records_with_tag(loguru_sink, "ORDER_CROSS_DIRECTION_BLOCKED") == []


@pytest.mark.asyncio
async def test_coordinator_attribute_error_is_tolerated(loguru_sink) -> None:
    """A coordinator stub that lacks _trades or raises on access must
    not break order placement — the guard tolerantly skips."""
    client = _FakeClient()
    broken_coord = SimpleNamespace()  # no _trades
    svc = BybitDemoOrderService(client)  # type: ignore[arg-type]
    svc.attach_coordinator(broken_coord)

    order = await _place_buy(svc, symbol="BTCUSDT", side=Side.BUY)

    # Order succeeds; the bad coordinator does not propagate
    assert order.status == OrderStatus.FILLED
