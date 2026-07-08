"""Tests for ClaudeClient: API calls, cost tracking, error handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.brain.claude_client import ClaudeClient
from src.brain.cost_tracker import CostTracker
from src.core.exceptions import BrainError, ClaudeAPIError


@pytest.fixture
def client(brain_settings, cost_tracker):
    return ClaudeClient(brain_settings, cost_tracker)


class TestClaudeClient:
    @pytest.mark.asyncio
    async def test_send_message_success(self, client, mock_anthropic_response):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=mock_anthropic_response)]
        mock_resp.usage = MagicMock(input_tokens=1500, output_tokens=300)
        mock_resp.model = "claude-sonnet-4-20250514"
        mock_resp.id = "msg_test123"

        client.client.messages.create = AsyncMock(return_value=mock_resp)

        result = await client.send_message("test prompt")
        assert result["text"] == mock_anthropic_response
        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 300
        assert result["cost_usd"] > 0
        assert client.total_calls == 1

    @pytest.mark.asyncio
    async def test_budget_exceeded_blocks_call(self, brain_settings):
        tracker = CostTracker(daily_budget_usd=0.0001)
        tracker.record_call(100000, 50000)
        client = ClaudeClient(brain_settings, tracker)

        with pytest.raises(BrainError, match="budget"):
            await client.send_message("test")

    @pytest.mark.asyncio
    async def test_api_error_wrapped(self, client):
        import anthropic
        client.client.messages.create = AsyncMock(
            side_effect=anthropic.APIError("Server error", request=MagicMock(), body=None)
        )
        with pytest.raises(ClaudeAPIError):
            await client.send_message("test")

    def test_usage_stats(self, client):
        stats = client.get_usage_stats()
        assert stats["total_calls"] == 0
        assert stats["total_cost_usd"] == 0


class TestAnalyzeMarket:
    @pytest.mark.asyncio
    async def test_analyze_calls_send(self, client, mock_anthropic_response):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=mock_anthropic_response)]
        mock_resp.usage = MagicMock(input_tokens=2000, output_tokens=400)
        mock_resp.model = "claude-sonnet-4-20250514"
        mock_resp.id = "msg_test456"
        client.client.messages.create = AsyncMock(return_value=mock_resp)

        result = await client.analyze_market("market data here", "system prompt")
        assert result["text"] == mock_anthropic_response
