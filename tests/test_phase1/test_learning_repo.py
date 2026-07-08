"""Tests for LearningRepository."""

import pytest
from src.database.repositories.learning_repo import LearningRepository


class TestStrategyPerformance:
    @pytest.mark.asyncio
    async def test_save_and_get(self, test_db):
        repo = LearningRepository(test_db)
        await repo.save_strategy_performance("momentum", "BTCUSDT", {
            "total_trades": 10, "winning_trades": 7, "losing_trades": 3, "win_rate": 0.7,
        })
        results = await repo.get_strategy_performance("momentum")
        assert len(results) == 1
        assert results[0]["win_rate"] == 0.7

    @pytest.mark.asyncio
    async def test_update_stats(self, test_db):
        repo = LearningRepository(test_db)
        await repo.update_strategy_stats("scalp", "ETHUSDT", 50.0, True)
        await repo.update_strategy_stats("scalp", "ETHUSDT", -20.0, False)

        results = await repo.get_strategy_performance("scalp")
        assert len(results) == 1
        assert results[0]["total_trades"] == 2
        assert results[0]["winning_trades"] == 1
        assert results[0]["losing_trades"] == 1
        assert results[0]["win_rate"] == 0.5


class TestSignalAccuracy:
    @pytest.mark.asyncio
    async def test_save_and_update(self, test_db):
        repo = LearningRepository(test_db)
        sig_id = await repo.save_signal_accuracy("strong_buy", "BTCUSDT", "up", 0.85, 70000)
        assert sig_id > 0

        await repo.update_signal_outcome(sig_id, "up", {"1h": 70500, "4h": 71000})
        stats = await repo.get_signal_accuracy_stats("strong_buy")
        assert stats["total_signals"] == 1
        assert stats["correct_count"] == 1
        assert stats["accuracy_pct"] == 100.0


class TestPatternLog:
    @pytest.mark.asyncio
    async def test_save_and_outcome(self, test_db):
        repo = LearningRepository(test_db)
        pid = await repo.save_pattern("bullish_engulfing", "BTCUSDT", {"rsi": 28}, 0.8)
        assert pid > 0

        await repo.update_pattern_outcome(pid, {"result": "price_up_2pct"})
        outcomes = await repo.get_pattern_outcomes("bullish_engulfing")
        assert len(outcomes) == 1


class TestBrainDecisions:
    @pytest.mark.asyncio
    async def test_save_and_get(self, test_db):
        repo = LearningRepository(test_db)
        did = await repo.save_brain_decision(
            "hash123", {"prices": {}}, "Claude says buy",
            {"action": "buy"}, tokens_used=2000, cost_usd=0.012,
        )
        assert did > 0

        decisions = await repo.get_brain_decisions(limit=5)
        assert len(decisions) == 1
        assert decisions[0]["cost_usd"] == 0.012

    @pytest.mark.asyncio
    async def test_brain_cost_today(self, test_db):
        repo = LearningRepository(test_db)
        await repo.save_brain_decision("h1", {}, "", {}, cost_usd=0.01)
        await repo.save_brain_decision("h2", {}, "", {}, cost_usd=0.02)

        cost = await repo.get_brain_cost_today()
        assert cost == pytest.approx(0.03, abs=0.001)
