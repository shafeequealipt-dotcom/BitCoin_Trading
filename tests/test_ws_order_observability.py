"""Observability G5 — BYBIT_DEMO_WS_ORDER promoted to INFO + full fields.

The audit (2026-05-13) noted BYBIT_DEMO_WS_ORDER fires zero times.
Investigation confirmed the order handler at
``bybit_demo_websocket_subscriber.py:284-304`` emitted at DEBUG only
AND filtered to the three terminal states (Filled / Cancelled /
Rejected). Intermediate transitions (New, PartiallyFilled, Triggered,
etc.) were silent.

G5 removes the terminal-state filter, promotes to INFO, and adds the
full Bybit V5 field set (side, qty, price, SL/TP, orderType, linkId)
so operators can trace order lifecycle end-to-end and correlate
ORD_SEND latency with eventual fill timing.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger as _loguru_logger

from src.bybit_demo.bybit_demo_websocket_subscriber import (
    BybitDemoWebSocketSubscriber,
)


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records: list[tuple[str, str]], tag: str) -> list[tuple[str, str]]:
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _make_settings():
    return SimpleNamespace(
        bybit=SimpleNamespace(
            testnet=False, api_key="LK", api_secret="LS", ws_reconnect_delay=5,
        ),
        bybit_demo=SimpleNamespace(api_key="DK", api_secret="DS"),
    )


def _order_event(*, status: str = "Filled", **overrides):
    base = {
        "symbol": "BTCUSDT",
        "orderId": "ORD-1234567890AB",
        "orderStatus": status,
        "side": "Buy",
        "qty": "0.05",
        "price": "82000",
        "avgPrice": "82001.5",
        "stopLoss": "80000",
        "takeProfit": "85000",
        "orderType": "Market",
        "orderLinkId": "tplan-abc-9999-link",
    }
    base.update(overrides)
    return {"topic": "order", "data": [base]}


def _make_subscriber() -> BybitDemoWebSocketSubscriber:
    """Construct a subscriber with a MagicMock event loop.

    The order handler is synchronous, so no run_coroutine_threadsafe
    paths are exercised. Using MagicMock avoids the deprecated
    asyncio.get_event_loop() which fails under full-suite test
    isolation when an earlier test closes the global loop.
    """
    with patch("src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket"):
        return BybitDemoWebSocketSubscriber(
            settings=_make_settings(),
            db=MagicMock(),
            coordinator=MagicMock(),
            loop=MagicMock(),
        )


@pytest.mark.parametrize(
    "status", ["New", "PartiallyFilled", "Filled", "Cancelled", "Rejected", "Triggered"]
)
def test_order_event_emits_at_info_for_all_status(loguru_sink, status) -> None:
    """All observable order states emit at INFO (not DEBUG)."""
    sub = _make_subscriber()
    sub._handle_order(_order_event(status=status))

    events = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_ORDER")
    assert len(events) == 1
    level, msg = events[0]
    assert level == "INFO", f"status={status} must emit at INFO, got {level}"
    kv = _parse_kv(msg)
    assert kv.get("status") == status
    assert kv.get("sym") == "BTCUSDT"


def test_order_event_carries_full_field_set(loguru_sink) -> None:
    """All required fields present per the audit schema."""
    sub = _make_subscriber()
    sub._handle_order(_order_event(status="Filled"))

    events = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_ORDER")
    assert len(events) == 1
    msg = events[0][1]
    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("oid", "").startswith("ORD-12345678")  # truncated to 12 chars
    assert kv.get("status") == "Filled"
    assert kv.get("side") == "Buy"
    assert kv.get("qty") == "0.05"
    assert kv.get("price") == "82000"
    assert kv.get("sl_price") == "80000"
    assert kv.get("tp_price") == "85000"
    assert kv.get("order_type") == "Market"
    assert kv.get("link_id", "").startswith("tplan-abc")


def test_order_event_with_missing_status_is_skipped(loguru_sink) -> None:
    """An order message without orderStatus is silently skipped (defensive)."""
    sub = _make_subscriber()
    msg = {"topic": "order", "data": [{"symbol": "BTCUSDT", "orderId": "ID"}]}
    sub._handle_order(msg)
    assert _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_ORDER") == []


def test_order_event_price_falls_back_to_avgprice(loguru_sink) -> None:
    """``price`` empty → falls back to ``avgPrice`` (Bybit uses avgPrice for fills)."""
    sub = _make_subscriber()
    sub._handle_order(_order_event(status="Filled", price=""))
    events = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_ORDER")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert float(kv.get("price", "0")) == 82001.5


def test_order_event_multiple_orders_in_one_message(loguru_sink) -> None:
    """Multi-order messages → one event per order."""
    sub = _make_subscriber()
    msg = {
        "topic": "order",
        "data": [
            _order_event(status="New")["data"][0],
            _order_event(status="Filled")["data"][0],
        ],
    }
    sub._handle_order(msg)
    events = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_ORDER")
    assert len(events) == 2
    statuses = [_parse_kv(m)["status"] for _, m in events]
    assert "New" in statuses
    assert "Filled" in statuses
