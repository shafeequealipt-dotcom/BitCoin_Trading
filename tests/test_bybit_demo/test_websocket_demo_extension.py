"""P1 Phase 3a — BybitWebSocket demo + execution-stream extension tests.

Surgical tests for the two changes in src/trading/websocket.py:
1. connect_private(demo=True) routes to pybit's demo cluster using
   bybit_demo credentials (and never live bybit creds).
2. subscribe_executions wires through pybit's execution_stream.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import MarketDataError
from src.trading.websocket import BybitWebSocket


def _make_settings(*, bybit_testnet: bool = False, bd_present: bool = True):
    """Build a minimal settings stub covering bybit + bybit_demo paths."""
    bybit = SimpleNamespace(
        testnet=bybit_testnet,
        api_key="LIVE_KEY",
        api_secret="LIVE_SECRET",
        ws_reconnect_delay=5,
    )
    s = SimpleNamespace(bybit=bybit)
    if bd_present:
        s.bybit_demo = SimpleNamespace(
            api_key="DEMO_KEY",
            api_secret="DEMO_SECRET",
        )
    return s


@pytest.mark.asyncio
async def test_connect_private_demo_uses_bybit_demo_creds_and_demo_flag() -> None:
    settings = _make_settings()
    db = MagicMock()
    ws = BybitWebSocket(settings, db)

    fake_pybit_ws = MagicMock()
    with patch(
        "pybit.unified_trading.WebSocket", return_value=fake_pybit_ws,
    ) as patched_ctor:
        await ws.connect_private(demo=True)

    patched_ctor.assert_called_once()
    _, kwargs = patched_ctor.call_args
    assert kwargs["demo"] is True
    assert kwargs["testnet"] is False  # demo is the mainnet variant
    assert kwargs["channel_type"] == "private"
    assert kwargs["api_key"] == "DEMO_KEY"
    assert kwargs["api_secret"] == "DEMO_SECRET"
    assert ws._private_ws is fake_pybit_ws


@pytest.mark.asyncio
async def test_connect_private_default_uses_bybit_live_creds() -> None:
    settings = _make_settings(bybit_testnet=False)
    ws = BybitWebSocket(settings, MagicMock())

    with patch(
        "pybit.unified_trading.WebSocket", return_value=MagicMock(),
    ) as patched_ctor:
        await ws.connect_private()  # demo defaults False

    _, kwargs = patched_ctor.call_args
    assert kwargs["demo"] is False
    assert kwargs["testnet"] is False
    assert kwargs["api_key"] == "LIVE_KEY"
    assert kwargs["api_secret"] == "LIVE_SECRET"


@pytest.mark.asyncio
async def test_connect_private_demo_missing_settings_raises_loudly() -> None:
    settings = _make_settings(bd_present=False)
    ws = BybitWebSocket(settings, MagicMock())

    with pytest.raises(MarketDataError, match="settings.bybit_demo"):
        await ws.connect_private(demo=True)


@pytest.mark.asyncio
async def test_connect_private_demo_missing_creds_raises_loudly() -> None:
    settings = _make_settings()
    settings.bybit_demo.api_key = ""
    ws = BybitWebSocket(settings, MagicMock())

    with pytest.raises(MarketDataError, match="api_key and api_secret"):
        await ws.connect_private(demo=True)


def test_subscribe_executions_routes_to_pybit_execution_stream() -> None:
    settings = _make_settings()
    ws = BybitWebSocket(settings, MagicMock())

    fake_pybit_ws = MagicMock()
    ws._private_ws = fake_pybit_ws

    cb_called = []

    def my_callback(message):
        cb_called.append(message)

    ws.subscribe_executions(my_callback)

    fake_pybit_ws.execution_stream.assert_called_once()
    # Verify the callback is wrapped (not the raw user callback) — wrap
    # adds error handling so a user-callback exception cannot kill the
    # pybit thread.
    wrapped = fake_pybit_ws.execution_stream.call_args.kwargs["callback"]
    assert wrapped is not my_callback
    # Sanity: invoking the wrapper still calls the user callback.
    wrapped({"data": "test"})
    assert cb_called == [{"data": "test"}]


def test_subscribe_executions_raises_when_not_connected() -> None:
    settings = _make_settings()
    ws = BybitWebSocket(settings, MagicMock())
    # _private_ws is None by default

    with pytest.raises(MarketDataError, match="Private WebSocket not connected"):
        ws.subscribe_executions(lambda m: None)
