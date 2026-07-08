"""Tests for sentiment tools (5 tools)."""

import pytest
from src.mcp.tools.sentiment_tools import register_sentiment_tools


@pytest.fixture
def sent_tools(mock_services):
    return register_sentiment_tools(mock_services)


class TestSentimentTools:
    def test_registers_5_tools(self, sent_tools):
        tools, _ = sent_tools
        assert len(tools) == 5

    @pytest.mark.asyncio
    async def test_reddit_sentiment(self, sent_tools):
        _, handlers = sent_tools
        result = await handlers["get_reddit_sentiment"]({"symbol": "BTCUSDT"})
        assert "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_social_buzz(self, sent_tools):
        _, handlers = sent_tools
        result = await handlers["get_social_buzz"]({})
        assert "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_aggregated_sentiment(self, sent_tools):
        _, handlers = sent_tools
        result = await handlers["get_aggregated_sentiment"]({"symbol": "BTCUSDT"})
        assert "bullish" in result[0].text.lower() or "BTCUSDT" in result[0].text

    @pytest.mark.asyncio
    async def test_sentiment_history(self, sent_tools):
        _, handlers = sent_tools
        result = await handlers["get_sentiment_history"]({"symbol": "BTCUSDT"})
        assert "improving" in result[0].text.lower() or "BTCUSDT" in result[0].text
