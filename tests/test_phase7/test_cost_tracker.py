"""Tests for CostTracker."""

import pytest
from src.brain.cost_tracker import CostTracker


class TestCostTracker:
    def test_calculate_cost(self, cost_tracker):
        # 2000 input + 500 output at Sonnet prices
        cost = cost_tracker.calculate_cost(2000, 500)
        expected = (2000 / 1e6) * 3.0 + (500 / 1e6) * 15.0
        assert cost == pytest.approx(expected, abs=1e-6)

    def test_record_call(self, cost_tracker):
        cost = cost_tracker.record_call(1000, 200)
        assert cost > 0
        assert cost_tracker.today_calls == 1
        assert cost_tracker.today_cost == cost

    def test_daily_budget_enforcement(self):
        tracker = CostTracker(daily_budget_usd=0.001)
        tracker.record_call(100000, 50000)  # Expensive call
        assert tracker.can_afford_call() is False

    def test_can_afford_with_budget(self):
        tracker = CostTracker(daily_budget_usd=10.00)
        assert tracker.can_afford_call() is True

    def test_daily_reset(self):
        tracker = CostTracker(daily_budget_usd=1.00)
        tracker.today_date = "2024-01-01"
        tracker.today_cost = 0.99
        tracker.today_calls = 50
        # Force reset by checking
        tracker._reset_if_new_day()
        # If today is not 2024-01-01, counters should reset
        from src.core.utils import now_utc
        if now_utc().strftime("%Y-%m-%d") != "2024-01-01":
            assert tracker.today_cost == 0.0
            assert tracker.today_calls == 0

    def test_daily_stats(self, cost_tracker):
        cost_tracker.record_call(1000, 200)
        stats = cost_tracker.get_daily_stats()
        assert stats["calls_today"] == 1
        assert stats["cost_today_usd"] > 0
        assert stats["budget_remaining_usd"] > 0

    def test_monthly_estimate(self, cost_tracker):
        cost_tracker.record_call(5000, 1000)
        est = cost_tracker.get_monthly_estimate()
        assert est["monthly_estimate_usd"] > 0
        assert est["monthly_estimate_inr"] > 0
