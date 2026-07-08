"""Tests for risk tools (5 tools)."""

import pytest
from src.mcp.tools.risk_tools import register_risk_tools


@pytest.fixture
def risk_tools(mock_services, mock_settings):
    return register_risk_tools(mock_services, mock_settings)


class TestRiskTools:
    def test_registers_5_tools(self, risk_tools):
        tools, _ = risk_tools
        assert len(tools) == 5

    @pytest.mark.asyncio
    async def test_position_size(self, risk_tools):
        _, handlers = risk_tools
        result = await handlers["calculate_position_size"]({
            "entry_price": 70000, "stop_loss_price": 68000, "risk_pct": 2.0,
        })
        assert "Position Size" in result[0].text

    @pytest.mark.asyncio
    async def test_risk_exposure(self, risk_tools):
        _, handlers = risk_tools
        result = await handlers["get_risk_exposure"]({})
        assert "Exposure" in result[0].text

    @pytest.mark.asyncio
    async def test_calculate_stop_loss(self, risk_tools):
        _, handlers = risk_tools
        result = await handlers["calculate_stop_loss"]({
            "symbol": "BTCUSDT", "side": "Buy", "entry_price": 70000,
        })
        assert "Stop-Loss" in result[0].text

    @pytest.mark.asyncio
    async def test_daily_pnl(self, risk_tools):
        _, handlers = risk_tools
        result = await handlers["get_daily_pnl"]({})
        assert "PnL" in result[0].text

    @pytest.mark.asyncio
    async def test_risk_status(self, risk_tools):
        _, handlers = risk_tools
        result = await handlers["get_risk_status"]({})
        assert "Risk Status" in result[0].text
