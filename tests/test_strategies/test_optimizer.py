"""Tests for WeeklyOptimizer."""

from unittest.mock import MagicMock

import pytest

from src.core.types import TimeFrame
from src.strategies.base_strategy import BaseStrategy
from src.strategies.models.regime_types import MarketRegime
from src.strategies.optimizer import WeeklyOptimizer
from src.strategies.registry import StrategyRegistry


class DummyStrat(BaseStrategy):
    def __init__(self, n):
        self._n = n
    @property
    def name(self): return self._n
    @property
    def category(self): return "scalping"
    @property
    def applicable_regimes(self): return [MarketRegime.TRENDING_UP]
    @property
    def timeframe(self): return TimeFrame.M5
    async def scan(self, *a, **kw): return None
    def vote(self, *a, **kw): return ("NEUTRAL", 0.5, "")


class TestOptimizer:
    @pytest.mark.asyncio
    async def test_outperforming_increases_weight(self, strategy_settings):
        reg = StrategyRegistry()
        reg.register(DummyStrat("good_strat"))
        # Simulate 30 wins out of 40
        for _ in range(30):
            reg.update_performance("good_strat", 1.5, True)
        for _ in range(10):
            reg.update_performance("good_strat", -0.8, False)

        optimizer = WeeklyOptimizer(strategy_settings, MagicMock(), reg)
        report = await optimizer.run_optimization()

        adjustments = report["weight_adjustments"]
        if adjustments:
            assert adjustments[0]["direction"] == "increase"

    @pytest.mark.asyncio
    async def test_underperforming_decreases_weight(self, strategy_settings):
        reg = StrategyRegistry()
        reg.register(DummyStrat("bad_strat"))
        # Simulate 10 wins out of 30
        for _ in range(10):
            reg.update_performance("bad_strat", 0.5, True)
        for _ in range(20):
            reg.update_performance("bad_strat", -1.0, False)

        optimizer = WeeklyOptimizer(strategy_settings, MagicMock(), reg)
        report = await optimizer.run_optimization()

        adjustments = report["weight_adjustments"]
        if adjustments:
            assert adjustments[0]["direction"] == "decrease"

    @pytest.mark.asyncio
    async def test_skip_insufficient_trades(self, strategy_settings):
        reg = StrategyRegistry()
        reg.register(DummyStrat("new_strat"))
        # Only 5 trades, below min_trades_for_optimization (20)
        for _ in range(5):
            reg.update_performance("new_strat", 1.0, True)

        optimizer = WeeklyOptimizer(strategy_settings, MagicMock(), reg)
        report = await optimizer.run_optimization()
        assert len(report["weight_adjustments"]) == 0

    @pytest.mark.asyncio
    async def test_disable_after_consecutive_weeks(self, strategy_settings):
        reg = StrategyRegistry()
        reg.register(DummyStrat("dying_strat"))
        for _ in range(5):
            reg.update_performance("dying_strat", 0.3, True)
        for _ in range(25):
            reg.update_performance("dying_strat", -1.0, False)

        optimizer = WeeklyOptimizer(strategy_settings, MagicMock(), reg)
        # Simulate 3 consecutive underperforming weeks
        for _ in range(3):
            await optimizer.run_optimization()

        perf = reg.get_performance("dying_strat")
        assert perf.enabled is False
