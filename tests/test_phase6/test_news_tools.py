"""Tests for news tools (4 tools)."""

import pytest
from src.mcp.tools.news_tools import register_news_tools


@pytest.fixture
def news_tools(mock_services):
    return register_news_tools(mock_services)


class TestNewsTools:
    def test_registers_4_tools(self, news_tools):
        tools, handlers = news_tools
        assert len(tools) == 4
        assert len(handlers) == 4

    @pytest.mark.asyncio
    async def test_get_latest_news(self, news_tools):
        _, handlers = news_tools
        result = await handlers["get_latest_news"]({})
        assert result[0].text  # Returns something

    @pytest.mark.asyncio
    async def test_search_news(self, news_tools):
        _, handlers = news_tools
        result = await handlers["search_news"]({"keyword": "bitcoin"})
        assert result[0].text

    @pytest.mark.asyncio
    async def test_economic_calendar(self, news_tools):
        _, handlers = news_tools
        result = await handlers["get_economic_calendar"]({})
        assert result[0].text

    @pytest.mark.asyncio
    async def test_not_available(self):
        tools, handlers = register_news_tools({})
        result = await handlers["get_latest_news"]({})
        assert "not available" in result[0].text.lower()
