"""Tests for BrainExecutor: safety checks, trade execution."""

import json

import pytest

from src.brain.decision_parser import DecisionParser
from src.brain.executor import BrainExecutor


@pytest.fixture
def parser():
    return DecisionParser()


@pytest.fixture
def executor(brain_settings, mock_services):
    return BrainExecutor(brain_settings, mock_services)


class TestExecutorSafetyChecks:
    @pytest.mark.asyncio
    async def test_blocked_when_disabled(self, brain_settings, mock_services, parser):
        brain_settings.brain.enabled = False
        exec_ = BrainExecutor(brain_settings, mock_services)
        decision = parser.parse(json.dumps({
            "action": "buy", "symbol": "BTCUSDT", "confidence": 0.9,
            "qty_pct": 5, "stop_loss": 68000,
        }))
        result = await exec_.execute(decision)
        assert result["executed"] is False
        assert "disabled" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_blocked_low_confidence(self, executor, parser):
        decision = parser.parse(json.dumps({
            "action": "buy", "symbol": "BTCUSDT", "confidence": 0.3,
            "qty_pct": 5, "stop_loss": 68000,
        }))
        result = await executor.execute(decision)
        assert result["executed"] is False
        assert "confidence" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_blocked_missing_stop_loss(self, executor, parser):
        decision = parser.parse(json.dumps({
            "action": "buy", "symbol": "BTCUSDT", "confidence": 0.9,
            "qty_pct": 5,
        }))
        result = await executor.execute(decision)
        assert result["executed"] is False
        assert "stop" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_hold_not_executed(self, executor, parser):
        decision = parser.parse(json.dumps({
            "action": "hold", "symbol": "BTCUSDT", "confidence": 0.9,
        }))
        result = await executor.execute(decision)
        assert result["executed"] is False
        assert result["action"] == "hold"
        assert result["error"] is None


class TestExecutorExecution:
    @pytest.mark.asyncio
    async def test_buy_calls_place_order(self, executor, parser, mock_services):
        decision = parser.parse(json.dumps({
            "action": "buy", "symbol": "BTCUSDT", "confidence": 0.9,
            "qty_pct": 5, "stop_loss": 68000, "take_profit": 73000, "leverage": 2,
        }))
        result = await executor.execute(decision)
        assert result["executed"] is True
        mock_services["order"].place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_sell_calls_place_order(self, executor, parser, mock_services):
        decision = parser.parse(json.dumps({
            "action": "sell", "symbol": "BTCUSDT", "confidence": 0.85,
            "qty_pct": 3, "stop_loss": 72000,
        }))
        result = await executor.execute(decision)
        assert result["executed"] is True

    @pytest.mark.asyncio
    async def test_close_calls_close_position(self, executor, parser, mock_services):
        decision = parser.parse(json.dumps({
            "action": "close", "symbol": "BTCUSDT", "confidence": 0.9,
        }))
        result = await executor.execute(decision)
        assert result["executed"] is True
        mock_services["position"].close_position.assert_called_once()
