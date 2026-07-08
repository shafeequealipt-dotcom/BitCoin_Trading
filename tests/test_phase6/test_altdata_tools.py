"""Tests for alt data tools (5 tools)."""

import pytest
from src.mcp.tools.altdata_tools import register_altdata_tools


@pytest.fixture
def alt_tools(mock_services):
    return register_altdata_tools(mock_services)


class TestAltdataTools:
    def test_registers_5_tools(self, alt_tools):
        tools, _ = alt_tools
        assert len(tools) == 5

    @pytest.mark.asyncio
    async def test_fear_greed(self, alt_tools):
        _, handlers = alt_tools
        result = await handlers["get_fear_greed_index"]({})
        assert "45" in result[0].text

    @pytest.mark.asyncio
    async def test_funding_rates(self, alt_tools):
        _, handlers = alt_tools
        result = await handlers["get_funding_rates"]({})
        assert "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_open_interest(self, alt_tools):
        _, handlers = alt_tools
        result = await handlers["get_open_interest"]({})
        assert "15,000" in result[0].text or "15000" in result[0].text

    @pytest.mark.asyncio
    async def test_market_overview(self, alt_tools):
        _, handlers = alt_tools
        result = await handlers["get_market_overview"]({})
        assert "overview" in result[0].text.lower() or "market" in result[0].text.lower()
