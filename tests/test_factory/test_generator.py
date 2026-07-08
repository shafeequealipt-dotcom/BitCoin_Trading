"""Tests for StrategyGenerator."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.factory.generator import StrategyGenerator
from src.factory.models.factory_types import DiscoveredPattern


class TestCodeExtraction:
    def test_extract_from_markdown_fence(self):
        text = '```python\nclass Foo:\n    pass\n```'
        code = StrategyGenerator._extract_code(text)
        assert "class Foo:" in code

    def test_extract_raw_code(self):
        text = 'class Foo:\n    def bar(self):\n        pass'
        code = StrategyGenerator._extract_code(text)
        assert "class Foo:" in code

    def test_extract_empty_response(self):
        code = StrategyGenerator._extract_code("No code here")
        assert code == ""


class TestSyntaxCheck:
    def test_valid_python(self):
        assert StrategyGenerator._check_syntax("x = 1") is True

    def test_invalid_python(self):
        assert StrategyGenerator._check_syntax("def broken(:\n  pass") is False


class TestGeneration:
    @pytest.mark.asyncio
    async def test_generate_with_mock_claude(self, factory_settings, sample_pattern, valid_strategy_code):
        mock_claude = MagicMock()
        mock_claude.send_message = AsyncMock(return_value={
            "text": f"```python\n{valid_strategy_code}\n```",
            "cost_usd": 0.005,
            "model": "test-model",
        })
        mock_tracker = MagicMock()
        mock_tracker.can_afford_call.return_value = True

        gen = StrategyGenerator(factory_settings, mock_claude, mock_tracker)
        result = await gen.generate(sample_pattern)

        assert result.code != ""
        assert result.syntax_valid is True
        assert result.generation_cost_usd > 0
        mock_claude.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_no_client(self, factory_settings, sample_pattern):
        gen = StrategyGenerator(factory_settings, None, None)
        result = await gen.generate(sample_pattern)
        assert "not available" in result.validation_errors[0]

    @pytest.mark.asyncio
    async def test_generate_budget_exceeded(self, factory_settings, sample_pattern):
        mock_claude = MagicMock()
        mock_tracker = MagicMock()
        mock_tracker.can_afford_call.return_value = False

        gen = StrategyGenerator(factory_settings, mock_claude, mock_tracker)
        result = await gen.generate(sample_pattern)
        assert "budget" in result.validation_errors[0].lower()
