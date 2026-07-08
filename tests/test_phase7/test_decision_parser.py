"""Tests for DecisionParser: JSON extraction, validation, normalization."""

import json

import pytest

from src.core.exceptions import DecisionParseError
from src.core.types import BrainDecision
from src.brain.decision_parser import DecisionParser


@pytest.fixture
def parser():
    return DecisionParser()


class TestJSONExtraction:
    def test_clean_json(self, parser, mock_anthropic_response):
        decision = parser.parse(mock_anthropic_response)
        assert isinstance(decision, BrainDecision)
        assert decision.action == "buy"
        assert decision.symbol == "BTCUSDT"
        assert decision.confidence == 0.85

    def test_json_in_code_fences(self, parser, mock_anthropic_fenced_response):
        decision = parser.parse(mock_anthropic_fenced_response)
        assert decision.action == "buy"
        assert decision.symbol == "ETHUSDT"

    def test_json_with_surrounding_text(self, parser):
        text = 'Here is my analysis:\n{"action": "hold", "symbol": "BTCUSDT", "confidence": 0.5, "reasoning": "mixed signals"}\nThat is my recommendation.'
        decision = parser.parse(text)
        assert decision.action == "hold"

    def test_invalid_response_raises(self, parser):
        with pytest.raises(DecisionParseError):
            parser.parse("This is not JSON at all, just plain text analysis.")

    def test_hold_response(self, parser, mock_anthropic_hold_response):
        decision = parser.parse(mock_anthropic_hold_response)
        assert decision.action == "hold"
        assert decision.confidence == 0.4


class TestNormalization:
    def test_action_lowercased(self, parser):
        text = json.dumps({"action": "BUY", "symbol": "BTCUSDT", "confidence": 0.8})
        decision = parser.parse(text)
        assert decision.action == "buy"

    def test_confidence_clamped(self, parser):
        text = json.dumps({"action": "hold", "symbol": "BTCUSDT", "confidence": 1.5})
        decision = parser.parse(text)
        assert decision.confidence == 1.0

    def test_defaults_applied(self, parser):
        text = json.dumps({"action": "hold", "symbol": "BTCUSDT"})
        decision = parser.parse(text)
        assert decision.confidence == 0.0
        assert decision.reasoning == ""


class TestValidation:
    def test_valid_decision(self, parser, brain_settings, mock_anthropic_response):
        decision = parser.parse(mock_anthropic_response)
        issues = parser.validate_decision(decision, brain_settings)
        assert issues == []

    def test_invalid_action(self, parser, brain_settings):
        text = json.dumps({"action": "yolo", "symbol": "BTCUSDT", "confidence": 0.8})
        decision = parser.parse(text)
        issues = parser.validate_decision(decision, brain_settings)
        assert any("Invalid action" in i for i in issues)

    def test_unsupported_symbol(self, parser, brain_settings):
        text = json.dumps({"action": "buy", "symbol": "FAKECOIN", "confidence": 0.8, "qty_pct": 5, "stop_loss": 100})
        decision = parser.parse(text)
        issues = parser.validate_decision(decision, brain_settings)
        assert any("Unsupported symbol" in i for i in issues)

    def test_missing_stop_loss_warning(self, parser, brain_settings):
        text = json.dumps({"action": "buy", "symbol": "BTCUSDT", "confidence": 0.8, "qty_pct": 5})
        decision = parser.parse(text)
        issues = parser.validate_decision(decision, brain_settings)
        assert any("stop_loss" in i.lower() or "Stop-loss" in i for i in issues)

    def test_zero_qty_warning(self, parser, brain_settings):
        text = json.dumps({"action": "buy", "symbol": "BTCUSDT", "confidence": 0.8, "stop_loss": 68000})
        decision = parser.parse(text)
        issues = parser.validate_decision(decision, brain_settings)
        assert any("qty_pct" in i for i in issues)
