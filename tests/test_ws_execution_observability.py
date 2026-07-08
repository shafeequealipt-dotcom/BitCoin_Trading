"""Observability G3 — BYBIT_DEMO_WS_EXEC_NON_CLOSE promoted to INFO.

The audit (2026-05-13) noted BYBIT_DEMO_WS_EXECUTION fires zero times.
Investigation showed the literal tag did not exist, but the cluster
already covers two of three execution outcomes at INFO:

  - BYBIT_DEMO_WS_CLOSE_EVENT   (full close — INFO)
  - BYBIT_DEMO_WS_EXEC_PARTIAL  (partial fill — INFO)
  - BYBIT_DEMO_WS_EXEC_NON_CLOSE (opening fill / reduction — DEBUG)  ← G3 gap

G3 promotes BYBIT_DEMO_WS_EXEC_NON_CLOSE from DEBUG to INFO and adds
the same fields the CLOSE_EVENT carries (side, exec_price, exec_qty,
exec_fee, exec_type) so log consumers can correlate opening vs closing
fills.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger as _loguru_logger

from src.bybit_demo.bybit_demo_websocket_subscriber import (
    BybitDemoWebSocketSubscriber,
)


@pytest.fixture
def loguru_sink():
    """Capture loguru records into a list for assertion."""
    records: list[tuple[str, str]] = []  # (level, message)
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


def _non_close_exec_event(*, exec_type: str = "Trade"):
    """A fill where closedSize=0 — opening fill, reduction, or modification."""
    return {
        "topic": "execution",
        "data": [{
            "symbol": "BTCUSDT",
            "orderId": "OID-NC-9876543210",
            "closedSize": "0",           # the key signal: not a close
            "leavesQty": "0",
            "execPrice": "82000.50",
            "execQty": "0.05",
            "execFee": "0.0012",
            "side": "Buy",
            "stopOrderType": "",
            "execType": exec_type,
        }],
    }


@pytest.mark.asyncio
async def test_non_close_execution_emits_at_info_level(loguru_sink) -> None:
    """Opening fill (closedSize=0) emits at INFO with the full field set."""
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
        sub._handle_execution(_non_close_exec_event())
        await asyncio.sleep(0.05)

    events = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_EXEC_NON_CLOSE")
    assert len(events) == 1, "non-close fill must emit exactly one event"
    level, msg = events[0]
    assert level == "INFO", f"non-close fill must emit at INFO, got {level}"

    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("oid", "").startswith("OID-NC-9876")  # truncated to 12 chars
    assert kv.get("side") == "Buy"
    assert float(kv.get("exec_price", "0")) == 82000.50
    assert float(kv.get("exec_qty", "0")) == 0.05
    assert float(kv.get("exec_fee", "0")) == 0.0012
    assert kv.get("exec_type") == "Trade"
    # closed_size present
    assert "closed_size" in kv
    # partial=N mirrors CLOSE_EVENT schema shape for parser consistency
    assert kv.get("partial") == "N"

    # Coordinator MUST NOT be called for non-close fills.
    coordinator.on_trade_closed.assert_not_called()


@pytest.mark.asyncio
async def test_non_close_with_exec_type_label_propagates(loguru_sink) -> None:
    """exec_type field passes through (Funding / Settle / Trade etc.)."""
    coordinator = MagicMock()
    loop = asyncio.get_running_loop()

    with patch("src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket"):
        sub = BybitDemoWebSocketSubscriber(
            settings=_make_settings(),
            db=MagicMock(),
            coordinator=coordinator,
            loop=loop,
        )
        sub._handle_execution(_non_close_exec_event(exec_type="Funding"))
        await asyncio.sleep(0.05)

    events = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_EXEC_NON_CLOSE")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv.get("exec_type") == "Funding"
