"""Tests for Portfolio Optimizer components."""

import pytest

from src.portfolio.kelly import KellyCalculator
from src.portfolio.correlation import CorrelationTracker
from src.portfolio.risk_budget import RiskBudgetManager
from src.portfolio.stress_test import StressTester
from src.portfolio.models.portfolio_types import (
    StrategyAllocation, RiskBudget, StressTestResult, RebalanceAction,
)
from src.strategies.models.signal_types import StrategyPerformance
from src.strategies.registry import StrategyRegistry


# =============================================================================
# Kelly Tests
# =============================================================================

class TestKelly:
    def test_full_kelly_standard(self):
        k = KellyCalculator(None)
        # 60% WR, 2:1 R:R → f* = (0.6*2 - 0.4*1) / 2 = 0.4
        result = k.full_kelly(0.6, 2.0, 1.0)
        assert abs(result - 0.4) < 0.01

    def test_full_kelly_even_odds(self):
        k = KellyCalculator(None)
        # 55% WR, 1:1 → f* = (0.55*1 - 0.45*1) / 1 = 0.10
        result = k.full_kelly(0.55, 1.0, 1.0)
        assert abs(result - 0.10) < 0.01

    def test_full_kelly_negative_edge(self):
        k = KellyCalculator(None)
        # 40% WR, 1:1 → f* negative → clamped to 0
        result = k.full_kelly(0.4, 1.0, 1.0)
        assert result == 0.0

    def test_full_kelly_zero_win_rate(self):
        k = KellyCalculator(None)
        result = k.full_kelly(0.0, 2.0, 1.0)
        assert result == 0.0

    def test_full_kelly_perfect_win_rate(self):
        k = KellyCalculator(None)
        result = k.full_kelly(1.0, 2.0, 1.0)
        assert result == 1.0

    def test_fractional_kelly(self):
        k = KellyCalculator(None)
        full = k.full_kelly(0.6, 2.0, 1.0)
        frac = k.fractional_kelly(0.6, 2.0, 1.0)
        assert abs(frac - full * 0.25) < 0.001

    def test_dynamic_kelly_losing_streak(self):
        k = KellyCalculator(None)
        normal = k.fractional_kelly(0.6, 2.0, 1.0)
        reduced = k.dynamic_kelly(0.6, 2.0, 1.0, recent_streak=-5, drawdown_pct=0)
        assert reduced < normal

    def test_dynamic_kelly_in_drawdown(self):
        k = KellyCalculator(None)
        normal = k.fractional_kelly(0.6, 2.0, 1.0)
        reduced = k.dynamic_kelly(0.6, 2.0, 1.0, recent_streak=0, drawdown_pct=8.0)
        assert reduced < normal

    def test_calculate_for_strategy_insufficient_data(self):
        k = KellyCalculator(None)
        perf = StrategyPerformance(strategy_name="new", total_trades=5)
        result = k.calculate_for_strategy(perf)
        assert result["suggested_allocation_pct"] == 2.0
        assert "Insufficient" in result["reasoning"]

    def test_calculate_for_strategy_with_data(self):
        k = KellyCalculator(None)
        perf = StrategyPerformance(strategy_name="good")
        for _ in range(30):
            perf.update(2.0, True)
        for _ in range(12):
            perf.update(-1.0, False)
        result = k.calculate_for_strategy(perf)
        assert result["full_kelly_pct"] > 0
        assert result["fractional_kelly_pct"] > 0


# =============================================================================
# Correlation Tests
# =============================================================================

class TestCorrelation:
    def test_pearson_perfect_positive(self):
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        corr = CorrelationTracker._pearson(x, y)
        assert abs(corr - 1.0) < 0.01

    def test_pearson_perfect_negative(self):
        x = [1, 2, 3, 4, 5]
        y = [10, 8, 6, 4, 2]
        corr = CorrelationTracker._pearson(x, y)
        assert abs(corr - (-1.0)) < 0.01

    def test_pearson_no_correlation(self):
        x = [1, -1, 1, -1, 1]
        y = [1, 1, -1, -1, 0]
        corr = CorrelationTracker._pearson(x, y)
        assert abs(corr) < 0.5

    def test_pearson_empty(self):
        corr = CorrelationTracker._pearson([], [])
        assert corr == 0.0


# =============================================================================
# Risk Budget Tests
# =============================================================================

class TestRiskBudget:
    def test_calculate_budget(self, factory_settings):
        from unittest.mock import MagicMock
        rb = RiskBudgetManager(factory_settings, MagicMock())
        budget = rb.calculate_budget(10000)
        assert budget.total_daily_risk_pct == 5.0
        assert budget.proven_strategies_pct + budget.ai_strategies_pct + budget.trial_strategies_pct + budget.cash_reserve_pct == 100

    def test_can_trade_within_budget(self, factory_settings):
        from unittest.mock import MagicMock
        rb = RiskBudgetManager(factory_settings, MagicMock())
        rb.calculate_budget(10000)
        can, reason = rb.can_trade("test", 10)
        assert can is True

    def test_daily_reset(self, factory_settings):
        from unittest.mock import MagicMock
        rb = RiskBudgetManager(factory_settings, MagicMock())
        rb.calculate_budget(10000)
        rb.update_used_risk("test", 50)
        rb.reset_daily()
        util = rb.get_risk_utilization()
        assert util["total_used"] == 0


# =============================================================================
# Stress Test Tests
# =============================================================================

class TestStressTest:
    def test_all_scenarios_run(self):
        reg = StrategyRegistry()
        tester = StressTester(reg)
        results = tester.run_scenarios(10000)
        assert len(results) == 7  # 7 standard scenarios

    def test_all_scenarios_have_impact(self):
        reg = StrategyRegistry()
        tester = StressTester(reg)
        results = tester.run_scenarios(10000)
        for r in results:
            assert r.estimated_portfolio_impact_pct > 0
            assert r.estimated_loss_usd > 0

    def test_most_scenarios_survivable(self):
        reg = StrategyRegistry()
        tester = StressTester(reg)
        results = tester.run_scenarios(100000)
        survived = sum(1 for r in results if r.survival)
        assert survived >= 5  # Most should be survivable


# =============================================================================
# Model Tests
# =============================================================================

class TestModels:
    def test_allocation_to_dict(self):
        a = StrategyAllocation(
            strategy_name="test", category="scalping",
            allocated_pct=5.5, allocated_usd=550, max_leverage=3,
        )
        d = a.to_dict()
        assert d["strategy_name"] == "test"
        assert d["allocated_pct"] == 5.5

    def test_rebalance_action_to_dict(self):
        r = RebalanceAction(
            strategy_name="test", current_allocation_pct=3.0,
            proposed_allocation_pct=5.0, change_pct=2.0, reason="improved",
        )
        d = r.to_dict()
        assert d["change_pct"] == 2.0

    def test_stress_result_to_dict(self):
        s = StressTestResult(
            scenario_name="crash", estimated_portfolio_impact_pct=15.0,
            estimated_loss_usd=1500, survival=True,
        )
        d = s.to_dict()
        assert d["survival"] is True


# =============================================================================
# Config Tests
# =============================================================================

class TestConfig:
    def test_portfolio_settings_load(self):
        from src.config.settings import Settings
        Settings.reset()
        s = Settings._load_fresh()
        assert hasattr(s, 'portfolio')
        assert s.portfolio.kelly_fraction > 0
        assert s.portfolio.proven_strategies_budget_pct > 0
        assert s.portfolio.cash_reserve_pct > 0
        Settings.reset()
