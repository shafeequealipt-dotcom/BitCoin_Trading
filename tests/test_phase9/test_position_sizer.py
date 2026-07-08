"""Tests for PositionSizer."""

import pytest
from src.core.types import Side
from src.risk.position_sizer import PositionSizer


class TestFixedPercentage:
    def test_basic_calculation(self, risk_settings):
        """$10K equity, 2% risk, BTC $70K, SL $68K.
        Position = $200 / (2000/70000) = $7000, exceeds 10% cap → capped to $1000."""
        ps = PositionSizer(risk_settings)
        result = ps.fixed_percentage(10000, 2.0, 70000, 68000, 0.001)
        assert result["qty"] > 0
        assert result["risk_amount_usd"] == 200.0
        assert result["capped"] is True  # $7000 > $1000 (10% of $10K)
        assert result["qty_usd"] <= 1000 + 1  # Capped at max_position_size_pct

    def test_position_capped(self, risk_settings):
        """Position capped at max_position_size_pct."""
        ps = PositionSizer(risk_settings)
        result = ps.fixed_percentage(10000, 10.0, 100, 50, 0.001)
        # Risk $1000, 50% stop -> huge position, should be capped at 10%
        assert result["capped"] is True
        assert result["qty_usd"] <= 10000 * 0.10 + 1

    def test_zero_stop_distance(self, risk_settings):
        ps = PositionSizer(risk_settings)
        result = ps.fixed_percentage(10000, 2.0, 70000, 70000, 0.001)
        assert result["qty"] == 0

    def test_rounding_to_step(self, risk_settings):
        ps = PositionSizer(risk_settings)
        result = ps.fixed_percentage(10000, 2.0, 70000, 68000, 0.001)
        # Qty should be a multiple of 0.001
        assert round(result["qty"] / 0.001) == result["qty"] / 0.001


class TestATRBased:
    def test_atr_sizing(self, risk_settings):
        ps = PositionSizer(risk_settings)
        result = ps.atr_based(10000, 2.0, 70000, 500, 2.0, Side.BUY, 0.001)
        assert result["method"] == "atr_based"
        assert result["calculated_stop_loss"] == 69000  # 70000 - 500*2
        assert result["qty"] > 0


class TestKellyCriterion:
    def test_positive_edge(self, risk_settings):
        ps = PositionSizer(risk_settings)
        result = ps.kelly_criterion(10000, 0.6, 200, 100)
        assert result["full_kelly_pct"] > 0
        assert result["position_size_usd"] > 0

    def test_negative_edge(self, risk_settings):
        """40% win rate with 1:1 ratio -> negative Kelly."""
        ps = PositionSizer(risk_settings)
        result = ps.kelly_criterion(10000, 0.4, 100, 100)
        assert result["position_size_usd"] == 0
        assert "negative" in result["reason"].lower() or "insufficient" in result["reason"].lower()

    def test_insufficient_data(self, risk_settings):
        ps = PositionSizer(risk_settings)
        result = ps.kelly_criterion(10000, 0, 0, 0)
        assert result["position_size_usd"] == 0
        assert "insufficient" in result["reason"].lower()


class TestRecommend:
    def test_picks_smallest(self, risk_settings):
        ps = PositionSizer(risk_settings)
        result = ps.recommend(10000, 2.0, 70000, stop_loss_price=68000, atr_value=500)
        assert result["recommended_qty"] > 0
        assert len(result["all_methods"]) >= 2
