"""Tests for analysis tools (5 tools)."""

import pytest
from src.mcp.tools.analysis_tools import register_analysis_tools


@pytest.fixture
def analysis_tools(mock_services, test_db):
    return register_analysis_tools(mock_services, test_db)


class TestAnalysisTools:
    def test_registers_5_tools(self, analysis_tools):
        tools, _ = analysis_tools
        assert len(tools) == 5

    @pytest.mark.asyncio
    async def test_technical_analysis(self, analysis_tools):
        _, handlers = analysis_tools
        result = await handlers["get_technical_analysis"]({"symbol": "BTCUSDT"})
        assert "BUY" in result[0].text or "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_get_indicator(self, analysis_tools):
        _, handlers = analysis_tools
        result = await handlers["get_indicator"]({"symbol": "BTCUSDT", "indicator": "rsi"})
        # With mocked TA, should return something
        assert result[0].text

    @pytest.mark.asyncio
    async def test_get_signal(self, analysis_tools):
        _, handlers = analysis_tools
        result = await handlers["get_signal"]({"symbol": "BTCUSDT"})
        assert "BUY" in result[0].text.upper() or "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_trade_recommendation(self, analysis_tools):
        _, handlers = analysis_tools
        result = await handlers["get_trade_recommendation"]({"symbol": "BTCUSDT"})
        assert "BTCUSDT" in result[0].text
