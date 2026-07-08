"""Tests for trading tools (12 tools)."""

import pytest
from unittest.mock import AsyncMock

from src.mcp.tools.trading_tools import register_trading_tools


@pytest.fixture
def trading_tools(mock_services):
    tools, handlers = register_trading_tools(mock_services)
    return tools, handlers


class TestTradingTools:
    def test_registers_12_tools(self, trading_tools):
        tools, handlers = trading_tools
        assert len(tools) == 12
        assert len(handlers) == 12

    def test_all_tools_have_descriptions(self, trading_tools):
        tools, _ = trading_tools
        for t in tools:
            assert t.description
            assert len(t.description) > 10

    @pytest.mark.asyncio
    async def test_get_account_info(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["get_account_info"]({})
        assert "10,000" in result[0].text or "10000" in result[0].text

    @pytest.mark.asyncio
    async def test_get_ticker(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["get_ticker"]({"symbol": "BTCUSDT"})
        assert "BTCUSDT" in result[0].text
        assert "70,000" in result[0].text or "70000" in result[0].text

    @pytest.mark.asyncio
    async def test_get_tickers(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["get_tickers"]({})
        assert "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_place_order(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["place_order"]({
            "symbol": "BTCUSDT", "side": "Buy", "order_type": "Market",
            "qty": 0.01, "stop_loss": 68000,
        })
        assert "ord_123" in result[0].text

    @pytest.mark.asyncio
    async def test_place_order_error(self, mock_services):
        mock_services["order"].place_order = AsyncMock(side_effect=Exception("Insufficient balance"))
        tools, handlers = register_trading_tools(mock_services)
        result = await handlers["place_order"]({"symbol": "BTCUSDT", "side": "Buy", "order_type": "Market", "qty": 0.01})
        assert "failed" in result[0].text.lower() or "error" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_cancel_order(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["cancel_order"]({"symbol": "BTCUSDT", "order_id": "ord_123"})
        assert "cancel" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_get_positions(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["get_positions"]({})
        assert "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_close_position(self, trading_tools):
        _, handlers = trading_tools
        result = await handlers["close_position"]({"symbol": "BTCUSDT"})
        assert "close" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_service_not_available(self):
        tools, handlers = register_trading_tools({})
        result = await handlers["get_account_info"]({})
        assert "not available" in result[0].text.lower()
