"""Tests for PromptBuilder."""

import pytest

from src.brain.prompt_builder import PromptBuilder


class TestPromptBuilder:
    @pytest.mark.asyncio
    async def test_build_context(self, test_db, brain_settings, mock_services):
        builder = PromptBuilder(test_db, brain_settings, mock_services)
        context = await builder.build_market_context()

        assert "prices" in context
        assert "technical_analysis" in context
        assert "sentiment" in context
        assert "fear_greed" in context
        assert "positions" in context
        assert "account" in context
        assert "risk_params" in context
        assert "recent_performance" in context

    @pytest.mark.asyncio
    async def test_build_prompt(self, test_db, brain_settings, mock_services):
        builder = PromptBuilder(test_db, brain_settings, mock_services)
        context = await builder.build_market_context()
        prompt = builder.build_prompt(context)

        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "BTCUSDT" in prompt or "Prices" in prompt

    @pytest.mark.asyncio
    async def test_prompt_under_token_limit(self, test_db, brain_settings, mock_services):
        """Prompt should be under ~12000 chars (~3000 tokens)."""
        builder = PromptBuilder(test_db, brain_settings, mock_services)
        context = await builder.build_market_context()
        prompt = builder.build_prompt(context)
        assert len(prompt) < 15000

    def test_prompt_hash_deterministic(self, test_db, brain_settings, mock_services):
        builder = PromptBuilder(test_db, brain_settings, mock_services)
        h1 = builder.compute_prompt_hash("same prompt")
        h2 = builder.compute_prompt_hash("same prompt")
        assert h1 == h2

    def test_prompt_hash_differs(self, test_db, brain_settings, mock_services):
        builder = PromptBuilder(test_db, brain_settings, mock_services)
        h1 = builder.compute_prompt_hash("prompt A")
        h2 = builder.compute_prompt_hash("prompt B")
        assert h1 != h2

    @pytest.mark.asyncio
    async def test_graceful_service_failure(self, test_db, brain_settings):
        """Missing services should not crash context building."""
        builder = PromptBuilder(test_db, brain_settings, {})
        context = await builder.build_market_context()
        assert context is not None
        assert context["prices"] == {}
