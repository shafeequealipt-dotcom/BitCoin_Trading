"""Tests for BybitWebSocket: connection, subscription, disconnect."""

import pytest
from unittest.mock import MagicMock, patch

from src.core.exceptions import MarketDataError
from src.trading.websocket import BybitWebSocket

WS_PATCH = "pybit.unified_trading.WebSocket"


class TestWebSocketConnection:
    @pytest.mark.asyncio
    async def test_connect_public(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws:
            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_public()
            assert ws.is_running is True
            mock_ws.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_private(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws:
            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_private()
            # P3a of P1-P10 added the `demo` kwarg (default False).
            # The default-False path preserves the legacy live/testnet
            # behaviour; the test now asserts the explicit default
            # appears in the pybit constructor call.
            mock_ws.assert_called_once_with(
                testnet=True,
                channel_type="private",
                api_key="test_api_key_123",
                api_secret="test_api_secret_456",
                demo=False,
            )

    @pytest.mark.asyncio
    async def test_disconnect(self, test_settings, test_db):
        with patch(WS_PATCH):
            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_public()
            await ws.disconnect()
            assert ws.is_running is False


class TestWebSocketSubscriptions:
    @pytest.mark.asyncio
    async def test_subscribe_ticker(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws

            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_public()

            callback = MagicMock()
            ws.subscribe_ticker(["BTCUSDT"], callback)
            mock_ws.ticker_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_kline(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws

            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_public()

            callback = MagicMock()
            ws.subscribe_kline("BTCUSDT", 15, callback)
            mock_ws.kline_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_orderbook(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws

            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_public()

            callback = MagicMock()
            ws.subscribe_orderbook("BTCUSDT", 50, callback)
            mock_ws.orderbook_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_without_connection_raises(self, test_settings, test_db):
        ws = BybitWebSocket(test_settings, test_db)
        with pytest.raises(MarketDataError, match="not connected"):
            ws.subscribe_ticker(["BTCUSDT"], lambda x: None)

    @pytest.mark.asyncio
    async def test_subscribe_orders_private(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws

            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_private()

            callback = MagicMock()
            ws.subscribe_orders(callback)
            mock_ws.order_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_positions_private(self, test_settings, test_db):
        with patch(WS_PATCH) as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws

            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_private()

            callback = MagicMock()
            ws.subscribe_positions(callback)
            mock_ws.position_stream.assert_called_once()


class TestCallbackWrapping:
    @pytest.mark.asyncio
    async def test_callback_error_does_not_propagate(self, test_settings, test_db):
        """Errors in user callbacks should be caught and logged."""
        with patch(WS_PATCH) as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws

            ws = BybitWebSocket(test_settings, test_db)
            await ws.connect_public()

            def bad_callback(msg):
                raise RuntimeError("user error")

            ws.subscribe_ticker(["BTCUSDT"], bad_callback)

            # Get the wrapped callback that was passed to pybit
            wrapped = mock_ws.ticker_stream.call_args[1]["callback"]
            # Should not raise
            wrapped({"data": "test"})
