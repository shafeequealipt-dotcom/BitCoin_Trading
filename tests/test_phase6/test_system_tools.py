"""Tests for system + memory tools (7 tools)."""

import pytest
from src.mcp.tools.system_tools import register_system_tools
from src.mcp.tools.memory_tools import register_memory_tools


@pytest.fixture
def sys_tools(mock_services, test_db):
    return register_system_tools(mock_services, test_db)


@pytest.fixture
def mem_tools(mock_services, test_db):
    return register_memory_tools(mock_services, test_db)


class TestSystemTools:
    def test_registers_3_tools(self, sys_tools):
        tools, _ = sys_tools
        assert len(tools) == 3

    @pytest.mark.asyncio
    async def test_system_status(self, sys_tools):
        _, handlers = sys_tools
        result = await handlers["get_system_status"]({})
        assert "System Status" in result[0].text

    @pytest.mark.asyncio
    async def test_update_preference(self, sys_tools):
        _, handlers = sys_tools
        result = await handlers["update_preference"]({"key": "theme", "value": "dark"})
        assert "theme" in result[0].text


class TestMemoryTools:
    def test_registers_4_tools(self, mem_tools):
        tools, _ = mem_tools
        assert len(tools) == 4

    @pytest.mark.asyncio
    async def test_trade_history(self, mem_tools):
        _, handlers = mem_tools
        result = await handlers["get_trade_history"]({})
        assert result[0].text

    @pytest.mark.asyncio
    async def test_strategy_performance(self, mem_tools):
        _, handlers = mem_tools
        result = await handlers["get_strategy_performance"]({})
        assert result[0].text

    @pytest.mark.asyncio
    async def test_brain_decisions(self, mem_tools):
        _, handlers = mem_tools
        result = await handlers["get_brain_decisions"]({})
        assert result[0].text


class TestToolCount:
    """Verify total tool count across all modules."""
    def test_total_43_tools(self, mock_services, mock_settings, test_db):
        from src.mcp.tools.trading_tools import register_trading_tools
        from src.mcp.tools.news_tools import register_news_tools
        from src.mcp.tools.sentiment_tools import register_sentiment_tools
        from src.mcp.tools.altdata_tools import register_altdata_tools
        from src.mcp.tools.analysis_tools import register_analysis_tools
        from src.mcp.tools.risk_tools import register_risk_tools
        from src.mcp.tools.memory_tools import register_memory_tools
        from src.mcp.tools.system_tools import register_system_tools

        total = 0
        for reg_fn, args in [
            (register_trading_tools, [mock_services]),
            (register_news_tools, [mock_services]),
            (register_sentiment_tools, [mock_services]),
            (register_altdata_tools, [mock_services]),
            (register_analysis_tools, [mock_services, test_db]),
            (register_risk_tools, [mock_services, mock_settings]),
            (register_memory_tools, [mock_services, test_db]),
            (register_system_tools, [mock_services, test_db]),
        ]:
            tools, _ = reg_fn(*args)
            total += len(tools)

        assert total == 43
