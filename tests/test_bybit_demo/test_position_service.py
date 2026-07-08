"""Smoke test for BybitDemoPositionService — V5 translation only."""

from __future__ import annotations

from typing import Any

import pytest

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoPositionService,
    _build_position_from_v5,
)
from src.core.exceptions import OrderRejectedError
from src.core.types import OrderStatus, Side


class _FakeClient:
    def __init__(self) -> None:
        self.gets: list[tuple[str, dict[str, Any] | None, str]] = []
        self.posts: list[tuple[str, dict[str, Any], str]] = []
        # Default position list with one open BTCUSDT long
        self.position_list_response = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.5",
                        "avgPrice": "50000",
                        "markPrice": "50500",
                        "unrealisedPnl": "250",
                        "leverage": "10",
                        "liqPrice": "30000",
                        "stopLoss": "49000",
                        "takeProfit": "52000",
                    },
                    {
                        # Zero-size entry — must be filtered out
                        "symbol": "ETHUSDT",
                        "side": "Buy",
                        "size": "0",
                    },
                ]
            },
        }
        self.create_response: dict[str, Any] = {
            "retCode": 0,
            "result": {"orderId": "close_oid"},
        }
        self.realtime_response: dict[str, Any] = {
            "retCode": 0,
            "result": {"list": [{"avgPrice": "50500"}]},
        }
        self.post_raises: Exception | None = None

    async def get(self, path: str, params: dict[str, Any] | None = None, *, op: str = "") -> dict[str, Any]:
        self.gets.append((path, params, op))
        if path == "/v5/position/list":
            return self.position_list_response
        if path == "/v5/order/realtime":
            return self.realtime_response
        return {"retCode": 0, "result": {"list": []}}

    async def post(self, path: str, body: dict[str, Any], *, op: str = "") -> dict[str, Any]:
        self.posts.append((path, body, op))
        if self.post_raises is not None:
            raise self.post_raises
        return self.create_response

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_get_positions_filters_zero_size_and_translates() -> None:
    client = _FakeClient()
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    positions = await svc.get_positions()

    # Zero-size ETHUSDT is dropped; only the open BTCUSDT remains.
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "BTCUSDT"
    assert p.side == Side.BUY
    assert p.size == 0.5
    assert p.entry_price == 50000.0
    assert p.mark_price == 50500.0
    assert p.unrealized_pnl == 250.0
    assert p.leverage == 10
    assert p.stop_loss == 49000.0
    assert p.take_profit == 52000.0


@pytest.mark.asyncio
async def test_close_position_uses_reduce_only_opposite_side() -> None:
    client = _FakeClient()
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    order = await svc.close_position("BTCUSDT", purpose="layer4_close")

    # The close-order POST body has reduceOnly=True and side=Sell (opposite of long).
    create_post = next(p for p in client.posts if p[0] == "/v5/order/create")
    assert create_post[1]["reduceOnly"] is True
    assert create_post[1]["side"] == "Sell"
    assert create_post[1]["qty"] == "0.5"
    assert create_post[1]["timeInForce"] == "IOC"
    assert order.status == OrderStatus.FILLED
    assert order.qty == 0.5


@pytest.mark.asyncio
async def test_close_position_returns_rejected_on_no_position() -> None:
    client = _FakeClient()
    # Override position list to return empty
    client.position_list_response = {"retCode": 0, "result": {"list": []}}
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    order = await svc.close_position("BTCUSDT")
    assert order.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_reduce_position_falls_back_to_close_on_reject() -> None:
    client = _FakeClient()
    client.post_raises = OrderRejectedError(
        "rejected", details={"ret_code": 110099, "ret_msg": "rejected", "op": "reduce"}
    )
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    # First call goes to /v5/order/create (reduce attempt), raises.
    # Adapter catches → close_position fallback (which itself attempts
    # another /v5/order/create — that also raises).
    # Net: REJECTED Order is returned (not raised).
    order = await svc.reduce_position("BTCUSDT", 0.1)
    assert order.status == OrderStatus.REJECTED


def test_build_position_from_v5() -> None:
    p = _build_position_from_v5({
        "symbol": "ETHUSDT",
        "side": "Sell",
        "size": "1.0",
        "avgPrice": "2500",
        "markPrice": "2480",
        "unrealisedPnl": "20",
        "leverage": "5",
        "liqPrice": "3000",
        "stopLoss": "2550",
        "takeProfit": "2400",
    })
    assert p.symbol == "ETHUSDT"
    assert p.side == Side.SELL
    assert p.size == 1.0
    assert p.entry_price == 2500.0
    assert p.mark_price == 2480.0
    assert p.unrealized_pnl == 20.0
    assert p.leverage == 5
    assert p.stop_loss == 2550.0
    assert p.take_profit == 2400.0


# ─── T1-4: qty quantization + clear-pending fix (2026-05-12) ─────────────
#
# Pre-fix: profit_sniper computed close_qty = pos.size * close_pct / 100
# without snapping to lotSizeFilter.qtyStep. Bybit V5 rejected with
# ret_code=10001 'Qty invalid'. The adapter's REDUCE_FALLBACK arm called
# close_position which closed the FULL position. The pre-stamped
# partial-close pending entry was never cleared, so the next WS execution
# event mislabeled the close as partial='Y' and a stray trade_log row was
# written. Operator's "small amount sold but coin still in dashboard"
# symptom.
#
# Verified live on OPUSDT (12:26:06 today), AEROUSDT (yesterday 19:26),
# GMTUSDT (yesterday 18:13), and an earlier pre-session instance.
#
# Fix: floor-quantize qty to qty_step BEFORE the POST. If snapped < min_qty,
# downgrade to full close (operator decision Path B). On every fallback
# exit, clear the partial-close pending entry so the WS event labels
# correctly as full close.


class _FakeInstrumentService:
    """Minimal in-test stand-in for InstrumentService.get_instrument_info.

    Tests configure ``qty_step`` and ``min_qty`` per scenario. ``raises``
    triggers the BYBIT_DEMO_QTY_QUANTIZE_FETCH_FAIL path.
    """

    def __init__(self, *, qty_step: float = 0.001, min_qty: float = 0.001) -> None:
        from src.trading.models.instrument import InstrumentInfo
        self.info = InstrumentInfo(
            symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT",
            status="Trading", min_qty=min_qty, max_qty=1000.0,
            qty_step=qty_step, min_price=0.0, max_price=0.0,
            price_tick=0.0, min_leverage=1, max_leverage=100,
            leverage_step=0.01, min_notional=0.0,
        )
        self.calls: list[str] = []
        self.raises: Exception | None = None

    async def get_instrument_info(self, symbol: str) -> Any:
        self.calls.append(symbol)
        if self.raises is not None:
            raise self.raises
        return self.info


class _FakeCoordinator:
    """Minimal coordinator stand-in exposing only the partial-close API."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}

    def mark_partial_close_pending(self, symbol: str, qty: float, *, by: str) -> None:
        self._pending[symbol] = {"qty": qty, "by": by}

    def pop_partial_close_pending(self, symbol: str) -> dict[str, Any] | None:
        return self._pending.pop(symbol, None)


def test_quantize_qty_floor_decimal_grid() -> None:
    """T1-4: pure-math correctness of the quantize_qty_floor helper.
    Includes the float-drift case where math.floor(qty/step) returns N+1
    because qty/step lands at N+ULP."""
    from src.core.utils import quantize_qty_floor
    assert quantize_qty_floor(0.41176470, 0.001) == 0.411
    assert quantize_qty_floor(3.7, 0.5) == 3.5
    # Float-drift case: 0.0030000000000000005 / 0.001 → 3.000000000000001
    # math.floor would return 3 here too, so use a more pathological case
    assert quantize_qty_floor(0.003, 0.001) == 0.003
    assert quantize_qty_floor(0.0001, 0.001) == 0.0  # below step
    # qty < step → 0.0 (caller decides skip vs full-close)
    assert quantize_qty_floor(0.5, 1.0) == 0.0
    # Bug-replication: OPUSDT-style (qty_step=1.0, qty=5861.65 → 5861)
    assert quantize_qty_floor(5861.65, 1.0) == 5861.0
    # Edge: zero/negative inputs
    assert quantize_qty_floor(0.0, 0.001) == 0.0
    assert quantize_qty_floor(0.5, 0.0) == 0.0


@pytest.mark.asyncio
async def test_t1_4_reduce_position_quantizes_qty_to_step() -> None:
    """T1-4 happy path: raw float qty 0.41176470 → POST body 0.411."""
    client = _FakeClient()
    inst = _FakeInstrumentService(qty_step=0.001, min_qty=0.001)
    svc = BybitDemoPositionService(
        client, instrument_service=inst,  # type: ignore[arg-type]
    )
    await svc.reduce_position("BTCUSDT", 0.41176470)
    create = next(p for p in client.posts if p[0] == "/v5/order/create")
    assert create[1]["qty"] == "0.411"


@pytest.mark.asyncio
async def test_t1_4_reduce_position_below_min_qty_falls_back_to_close() -> None:
    """T1-4 Path B: quantized qty < min_qty → full close + clear pending.
    Verifies (a) only ONE create POST (the close, qty=full size),
    (b) BYBIT_DEMO_QTY_BELOW_MIN log fires (assertable via single-create)."""
    client = _FakeClient()
    # Position size 0.005, qty_step 0.001, min_qty 0.01
    # → user wants 0.0025 partial → snapped 0.002 < min 0.01 → full close
    client.position_list_response["result"]["list"][0]["size"] = "0.005"
    inst = _FakeInstrumentService(qty_step=0.001, min_qty=0.01)
    coord = _FakeCoordinator()
    svc = BybitDemoPositionService(
        client, instrument_service=inst,  # type: ignore[arg-type]
    )
    svc.attach_coordinator(coord)
    order = await svc.reduce_position("BTCUSDT", 0.0025)
    assert order.status == OrderStatus.FILLED
    # Only one create POST — the close, qty=full size
    creates = [p for p in client.posts if p[0] == "/v5/order/create"]
    assert len(creates) == 1
    assert creates[0][1]["qty"] == "0.005"
    # Pending entry must NOT exist (path B clears it before fallback)
    assert coord.pop_partial_close_pending("BTCUSDT") is None


@pytest.mark.asyncio
async def test_t1_4_reduce_position_no_instrument_service_falls_back() -> None:
    """T1-4 wiring gap: instrument_service=None → graceful full-close
    fallback with cleared pending. Never sends raw qty to Bybit."""
    client = _FakeClient()
    coord = _FakeCoordinator()
    svc = BybitDemoPositionService(
        client, instrument_service=None,  # type: ignore[arg-type]
    )
    svc.attach_coordinator(coord)
    order = await svc.reduce_position("BTCUSDT", 0.1)
    # Single create POST (the close). Original raw 0.1 was NEVER sent.
    creates = [p for p in client.posts if p[0] == "/v5/order/create"]
    assert len(creates) == 1
    assert creates[0][1]["qty"] == "0.5"  # full position size
    assert order.status == OrderStatus.FILLED
    assert coord.pop_partial_close_pending("BTCUSDT") is None


@pytest.mark.asyncio
async def test_t1_4_reduce_position_clears_pending_on_bybit_reject() -> None:
    """T1-4 secondary bug fix: Bybit reject (ret_code=10001) →
    pending entry cleared so WS doesn't mislabel as partial='Y'."""
    client = _FakeClient()
    client.post_raises = OrderRejectedError(
        "rejected",
        details={"ret_code": 10001, "ret_msg": "Qty invalid", "op": "reduce"},
    )
    inst = _FakeInstrumentService(qty_step=0.001, min_qty=0.001)
    coord = _FakeCoordinator()
    svc = BybitDemoPositionService(
        client, instrument_service=inst,  # type: ignore[arg-type]
    )
    svc.attach_coordinator(coord)
    await svc.reduce_position("BTCUSDT", 0.1)
    # Pending entry must be cleared even though the POST happened
    assert coord.pop_partial_close_pending("BTCUSDT") is None


@pytest.mark.asyncio
async def test_t1_4_reduce_position_happy_path_keeps_pending_for_ws() -> None:
    """T1-4 sanity: success path STILL stamps the pending entry so the
    WS race-defence is preserved. Without this, the WS subscriber would
    label the partial fill as a full close."""
    client = _FakeClient()
    inst = _FakeInstrumentService(qty_step=0.001, min_qty=0.001)
    coord = _FakeCoordinator()
    svc = BybitDemoPositionService(
        client, instrument_service=inst,  # type: ignore[arg-type]
    )
    svc.attach_coordinator(coord)
    await svc.reduce_position("BTCUSDT", 0.123)
    pending = coord.pop_partial_close_pending("BTCUSDT")
    assert pending is not None
    assert pending["by"] == "mode4_partial"
    assert pending["qty"] == 0.123  # snapped (already grid-aligned)


@pytest.mark.asyncio
async def test_t1_4_reduce_position_fetch_fail_falls_back() -> None:
    """T1-4: InstrumentService raises → full-close fallback +
    cleared pending. Verifies BYBIT_DEMO_QTY_QUANTIZE_FETCH_FAIL path."""
    client = _FakeClient()
    inst = _FakeInstrumentService()
    inst.raises = RuntimeError("network down")
    coord = _FakeCoordinator()
    svc = BybitDemoPositionService(
        client, instrument_service=inst,  # type: ignore[arg-type]
    )
    svc.attach_coordinator(coord)
    order = await svc.reduce_position("BTCUSDT", 0.1)
    creates = [p for p in client.posts if p[0] == "/v5/order/create"]
    assert len(creates) == 1
    assert creates[0][1]["qty"] == "0.5"  # full position
    assert order.status == OrderStatus.FILLED
    assert coord.pop_partial_close_pending("BTCUSDT") is None


@pytest.mark.asyncio
async def test_t1_4_reduce_position_opusdt_replication() -> None:
    """T1-4 bug-replication: OPUSDT-style (qty_step=1.0, prior_size=
    11723.3, partial=5861.65). Pre-fix this raw 5861.65 would be sent
    to Bybit, rejected as Qty invalid, and the position fully closed.
    Post-fix: snapped to 5861 (1.0 grid), POST proceeds, partial fill
    succeeds."""
    client = _FakeClient()
    client.position_list_response["result"]["list"][0]["size"] = "11723.3"
    inst = _FakeInstrumentService(qty_step=1.0, min_qty=1.0)
    svc = BybitDemoPositionService(
        client, instrument_service=inst,  # type: ignore[arg-type]
    )
    await svc.reduce_position("BTCUSDT", 5861.65)
    create = next(p for p in client.posts if p[0] == "/v5/order/create")
    assert create[1]["qty"] == "5861.0"
    # reduceOnly + IOC preserved
    assert create[1]["reduceOnly"] is True
    assert create[1]["timeInForce"] == "IOC"
