"""Tests for StopLossCalculator."""

import pytest
from src.core.types import Side
from src.risk.stop_loss import StopLossCalculator


class TestFixedPercentage:
    def test_buy_sl(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.fixed_percentage(70000, Side.BUY, 2.0, 4.0)
        assert result["stop_loss"] == 68600  # 70000 * 0.98
        assert result["take_profit"] == 72800  # 70000 * 1.04
        assert result["risk_reward_ratio"] == 2.0

    def test_sell_sl(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.fixed_percentage(70000, Side.SELL, 2.0, 4.0)
        assert result["stop_loss"] == 71400  # 70000 * 1.02
        assert result["take_profit"] == 67200  # 70000 * 0.96


class TestATRBased:
    def test_atr_buy(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.atr_based(70000, Side.BUY, 500, 2.0, 3.0)
        assert result["stop_loss"] == 69000  # 70000 - 500*2
        assert result["take_profit"] == 71500  # 70000 + 500*3


class TestSupportResistance:
    def test_buy_with_levels(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.support_resistance(70000, Side.BUY, [69000, 68000], [72000, 74000])
        assert result["stop_loss"] < 70000
        assert result["take_profit"] > 70000

    def test_fallback_when_no_levels(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.support_resistance(70000, Side.BUY, [], [])
        # Should fall back to fixed percentage
        assert result["stop_loss"] < 70000


class TestRecommend:
    def test_recommend_with_atr(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.recommend(70000, Side.BUY, atr_value=500)
        assert result["recommended_stop_loss"] < 70000
        assert result["recommended_take_profit"] > 70000
        assert result["risk_reward_ratio"] >= 1.5

    def test_rr_enforced(self, risk_settings):
        calc = StopLossCalculator(risk_settings)
        result = calc.recommend(70000, Side.BUY)
        assert result["risk_reward_ratio"] >= 1.5
