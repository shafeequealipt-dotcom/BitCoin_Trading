"""Observability G4 — BYBIT_DEMO_WS_POS_UPDATE for non-flat snapshots.

The audit (2026-05-13) noted BYBIT_DEMO_WS_POSITION fires zero times.
Investigation showed the position handler only emitted on size==0
(``BYBIT_DEMO_WS_POS_FLAT``). Non-flat state changes (size update, SL/TP
modification, status change, leverage update) were invisible.

G4 adds a new ``BYBIT_DEMO_WS_POS_UPDATE`` event with the full
position snapshot per state-change message, while preserving the
existing ``BYBIT_DEMO_WS_POS_FLAT`` lifecycle-end marker.

F-26 ground-truth divergence (system thinks 2 positions, exchange
has 5) would be detectable in real-time once this event flows.
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


def _open_position_event(*, sym: str = "BTCUSDT"):
    return {
        "topic": "position",
        "data": [{
            "symbol": sym,
            "side": "Buy",
            "size": "0.05",
            "entryPrice": "82000.5",
            "avgPrice": "82000.5",
            "markPrice": "82150.0",
            "unrealisedPnl": "7.475",
            "stopLoss": "80000",
            "takeProfit": "85000",
            "leverage": "5",
            "positionStatus": "Normal",
        }],
    }


def _flat_position_event(*, sym: str = "BTCUSDT"):
    return {
        "topic": "position",
        "data": [{
            "symbol": sym,
            "side": "",
            "size": "0",
        }],
    }


def _make_subscriber(coordinator: MagicMock | None = None) -> BybitDemoWebSocketSubscriber:
    """Build a subscriber bypassing the live WS (patched).

    The loop is a MagicMock because these tests drive
    ``_handle_position`` synchronously — no
    ``run_coroutine_threadsafe`` paths are exercised. Using MagicMock
    avoids the deprecated ``asyncio.get_event_loop()``, which fails
    unpredictably under full-suite test isolation when earlier tests
    close the global event loop.
    """
    if coordinator is None:
        coordinator = MagicMock()
    with patch("src.bybit_demo.bybit_demo_websocket_subscriber.BybitWebSocket"):
        return BybitDemoWebSocketSubscriber(
            settings=_make_settings(),
            db=MagicMock(),
            coordinator=coordinator,
            loop=MagicMock(),
        )


def test_non_flat_position_emits_pos_update_with_full_fields(loguru_sink) -> None:
    """Non-flat position state → BYBIT_DEMO_WS_POS_UPDATE with all fields."""
    sub = _make_subscriber()
    sub._handle_position(_open_position_event())

    updates = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_UPDATE")
    flats = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_FLAT")
    assert len(updates) == 1
    assert len(flats) == 0, "non-flat must not fire POS_FLAT"

    level, msg = updates[0]
    assert level == "INFO"
    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("side") == "Buy"
    assert float(kv.get("qty", "0")) == 0.05
    assert float(kv.get("entry_price", "0")) == 82000.5
    assert float(kv.get("mark_price", "0")) == 82150.0
    assert kv.get("sl_price") == "80000"
    assert kv.get("tp_price") == "85000"
    assert kv.get("lev") == "5"
    assert kv.get("status") == "Normal"


def test_flat_position_still_emits_pos_flat_only(loguru_sink) -> None:
    """size==0 → BYBIT_DEMO_WS_POS_FLAT (legacy behaviour preserved); no UPDATE."""
    sub = _make_subscriber()
    sub._handle_position(_flat_position_event())

    updates = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_UPDATE")
    flats = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_FLAT")
    assert len(updates) == 0, "flat must NOT fire POS_UPDATE"
    assert len(flats) == 1


def test_position_with_missing_optional_fields_still_emits(loguru_sink) -> None:
    """Best-effort field reads — missing optional fields default to empty."""
    sub = _make_subscriber()
    # Minimal payload: just symbol + size + side (no entry / SL / TP / leverage).
    msg = {
        "topic": "position",
        "data": [{"symbol": "ETHUSDT", "side": "Sell", "size": "0.1"}],
    }
    sub._handle_position(msg)

    updates = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_UPDATE")
    assert len(updates) == 1
    kv = _parse_kv(updates[0][1])
    assert kv.get("sym") == "ETHUSDT"
    assert kv.get("side") == "Sell"
    assert float(kv.get("qty", "0")) == 0.1


def test_multiple_positions_in_one_message_each_emit_update(loguru_sink) -> None:
    """A position WS message may carry multiple positions — emit one event per."""
    sub = _make_subscriber()
    msg = {
        "topic": "position",
        "data": [
            _open_position_event(sym="BTCUSDT")["data"][0],
            _open_position_event(sym="ETHUSDT")["data"][0],
        ],
    }
    sub._handle_position(msg)
    updates = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_UPDATE")
    assert len(updates) == 2
    syms = {_parse_kv(m)["sym"] for _, m in updates}
    assert syms == {"BTCUSDT", "ETHUSDT"}


def test_malformed_size_field_treated_as_flat(loguru_sink) -> None:
    """Non-numeric ``size`` should default to 0.0 → FLAT, not crash."""
    sub = _make_subscriber()
    msg = {
        "topic": "position",
        "data": [{"symbol": "XYZUSDT", "size": "not-a-number"}],
    }
    sub._handle_position(msg)
    flats = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_FLAT")
    updates = _records_with_tag(loguru_sink, "BYBIT_DEMO_WS_POS_UPDATE")
    assert len(flats) == 1
    assert len(updates) == 0
