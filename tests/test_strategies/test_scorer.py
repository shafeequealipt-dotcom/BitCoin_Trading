"""Tests for TradeScorer."""

import pytest

from src.core.types import Side
from src.strategies.models.signal_types import RawSignal
from src.strategies.scorer import TradeScorer


class TestScoring:
    def test_base_score_minimum(self, strategy_settings, sample_regime):
        scorer = TradeScorer(strategy_settings)
        signal = RawSignal(
            strategy_name="test", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
            conditions_met={}, conditions_strength={},
        )
        setup = scorer.score(signal, [], {}, None, None, sample_regime)
        assert setup.base_score == 30  # Aggressive tuning: base starts at 30
        assert setup.total_score > 0

    def test_base_score_with_strong_conditions(self, strategy_settings, sample_raw_signal, sample_ta_data, sample_regime):
        scorer = TradeScorer(strategy_settings)
        setup = scorer.score(sample_raw_signal, [], sample_ta_data, None, None, sample_regime)
        assert setup.base_score >= 25
        assert setup.base_score <= 40

    def test_total_score_bounded(self, strategy_settings, sample_raw_signal, sample_ta_data, sample_regime):
        scorer = TradeScorer(strategy_settings)
        setup = scorer.score(sample_raw_signal, [], sample_ta_data, None, None, sample_regime)
        assert 0 <= setup.total_score <= 105  # max: base(40) + conf(25) + ctx(20) + qual(20)

    def test_grade_assignment(self, strategy_settings, sample_raw_signal, sample_ta_data, sample_regime):
        scorer = TradeScorer(strategy_settings)
        setup = scorer.score(sample_raw_signal, [], sample_ta_data, None, None, sample_regime)
        assert setup.grade in ("A+", "A", "B", "C", "D")

    def test_confluence_bullish_agreement(self, strategy_settings, sample_ta_data, sample_regime):
        scorer = TradeScorer(strategy_settings)
        signal = RawSignal(
            strategy_name="test", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
            conditions_met={}, conditions_strength={},
        )
        setup = scorer.score(signal, [], sample_ta_data, None, None, sample_regime)
        # Bullish TA + BUY signal should give positive confluence
        assert setup.confluence_score > 0

    def test_confluence_bearish_disagreement(self, strategy_settings, sample_regime):
        scorer = TradeScorer(strategy_settings)
        bearish_ta = {
            "trend": {"trend_summary": "BEARISH"},
            "momentum": {"momentum_summary": "BEARISH"},
            "volatility": {"volatility_summary": "MODERATE"},
            "volume": {"volume_summary": "AVERAGE"},
            "overall": {"signal": "SELL", "confidence": 0.7},
            "support_resistance": {"current_price": 70000, "support_levels": [], "resistance_levels": []},
        }
        signal = RawSignal(
            strategy_name="test", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,  # BUY against bearish TA
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
            conditions_met={}, conditions_strength={},
        )
        setup = scorer.score(signal, [], bearish_ta, None, None, sample_regime)
        # BUY signal with bearish TA should have low/zero confluence
        assert setup.confluence_score <= 5

    def test_score_batch_filters_by_threshold(self, strategy_settings, sample_ta_data, sample_regime):
        scorer = TradeScorer(strategy_settings)
        signals = [
            RawSignal(
                strategy_name=f"test_{i}", strategy_category="scalping",
                symbol="BTCUSDT", direction=Side.BUY,
                entry_price=70000, suggested_stop_loss=69000,
                suggested_take_profit=72000, timeframe="5",
                conditions_met={}, conditions_strength={},
            )
            for i in range(3)
        ]
        scored = scorer.score_batch(
            signals, {"BTCUSDT": []}, {"BTCUSDT": sample_ta_data},
            None, None, sample_regime,
        )
        # All should pass since threshold is 60 and our signals should score ~60+
        for s in scored:
            assert s.total_score >= strategy_settings.strategy_engine.min_score_threshold

    def test_issue5_quality_cap_lowers_grade_when_enabled(
        self, strategy_settings, sample_raw_signal, sample_ta_data, sample_regime,
    ):
        """Issue 5 (2026-06-09): with the gated cap ON and a floor above the max
        quality (20), every setup is treated as low-quality, so the canonical
        grade is lowered to the configured ceiling ("B") and never raised. With
        the cap OFF (default) the grade is unchanged."""
        order = ["D", "C", "B", "A", "A+"]
        se = strategy_settings.strategy_engine

        # Cap OFF (default): record the uncapped grade.
        se.grade_quality_cap_enabled = False
        g_off = TradeScorer(strategy_settings).score(
            sample_raw_signal, [], sample_ta_data, None, None, sample_regime,
        )

        # Cap ON, floor above max quality (20) -> any setup is "low quality".
        se.grade_quality_cap_enabled = True
        se.grade_quality_floor = 21.0
        se.grade_quality_cap_max_grade = "B"
        g_on = TradeScorer(strategy_settings).score(
            sample_raw_signal, [], sample_ta_data, None, None, sample_regime,
        )

        # Never above the ceiling, never raised by the cap.
        assert order.index(g_on.grade) <= order.index("B")
        assert order.index(g_on.grade) <= order.index(g_off.grade)
        # quality_capped flag is True exactly when the uncapped grade was above B.
        assert g_on.scoring_details["quality_capped"] is (
            order.index(g_off.grade) > order.index("B")
        )

    def test_issue5_cap_max_case_insensitive(
        self, strategy_settings, sample_raw_signal, sample_ta_data, sample_regime,
    ):
        """Issue 5 follow-up: a lowercase/whitespace cap ceiling ("b ") is
        normalized so a config typo cannot silently disable the cap."""
        order = ["D", "C", "B", "A", "A+"]
        se = strategy_settings.strategy_engine
        se.grade_quality_cap_enabled = True
        se.grade_quality_floor = 21.0
        se.grade_quality_cap_max_grade = "b "  # lowercase + trailing space
        g = TradeScorer(strategy_settings).score(
            sample_raw_signal, [], sample_ta_data, None, None, sample_regime,
        )
        assert order.index(g.grade) <= order.index("B")

    def test_issue5_cap_off_by_default_preserves_grade(
        self, strategy_settings, sample_raw_signal, sample_ta_data, sample_regime,
    ):
        """Default (cap disabled): the grade is the pure-threshold grade and the
        quality_capped flag is False — no behaviour change ships by default."""
        setup = TradeScorer(strategy_settings).score(
            sample_raw_signal, [], sample_ta_data, None, None, sample_regime,
        )
        assert setup.scoring_details["quality_capped"] is False

    def test_score_batch_sorted_descending(self, strategy_settings, sample_ta_data, sample_regime):
        scorer = TradeScorer(strategy_settings)
        sig1 = RawSignal(
            strategy_name="weak", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
            conditions_met={}, conditions_strength={},
        )
        sig2 = RawSignal(
            strategy_name="strong", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
            conditions_met={"a": True, "b": True, "c": True},
            conditions_strength={"a": 0.9, "b": 0.85, "c": 0.95},
        )
        scored = scorer.score_batch(
            [sig1, sig2], {"BTCUSDT": []}, {"BTCUSDT": sample_ta_data},
            None, None, sample_regime,
        )
        if len(scored) >= 2:
            assert scored[0].total_score >= scored[1].total_score
