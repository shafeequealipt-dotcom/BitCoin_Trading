"""Tests for SmartLeverage calculator."""

from src.core.types import Side
from src.strategies.models.regime_types import MarketRegime, RegimeState
from src.strategies.smart_leverage import SmartLeverage


def _regime(regime_type=MarketRegime.TRENDING_UP):
    return RegimeState(
        regime=regime_type, confidence=0.7,
        adx=30, atr_percentile=100, choppiness=35,
        volume_ratio=1.3, trend_direction=1,
    )


class TestSmartLeverage:
    def test_max_leverage_high_confidence_tier1(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        lev = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.9,
            regime=_regime(), coin_tier=1,
            volatility_percentile=100, ensemble_strength="STRONG",
        )
        assert lev == 5

    def test_low_confidence_caps_at_2(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        lev = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.5,
            regime=_regime(), coin_tier=1,
            volatility_percentile=100, ensemble_strength="WEAK",
        )
        assert lev == 2

    def test_tier3_caps_leverage(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        # Without STRONG boost, tier 3 caps at 3
        lev = sl.calculate(
            "DOGEUSDT", Side.BUY, confidence=0.95,
            regime=_regime(), coin_tier=3,
            volatility_percentile=100, ensemble_strength="GOOD",
        )
        assert lev <= 3

    def test_high_volatility_reduces(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        # With STRONG boost, vol cap of 2 gets boosted to 3
        lev = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.9,
            regime=_regime(), coin_tier=1,
            volatility_percentile=160, ensemble_strength="GOOD",
        )
        assert lev <= 2

    def test_volatile_regime_caps(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        lev = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.9,
            regime=_regime(MarketRegime.VOLATILE), coin_tier=1,
            volatility_percentile=100, ensemble_strength="GOOD",
        )
        assert lev <= 3

    def test_dead_regime_caps(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        lev = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.9,
            regime=_regime(MarketRegime.DEAD), coin_tier=1,
            volatility_percentile=100, ensemble_strength="GOOD",
        )
        assert lev <= 2

    def test_minimum_always_1(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        lev = sl.calculate(
            "DOGEUSDT", Side.BUY, confidence=0.3,
            regime=_regime(MarketRegime.DEAD), coin_tier=3,
            volatility_percentile=200, ensemble_strength="WEAK",
        )
        assert lev >= 1

    def test_strong_consensus_bonus(self, strategy_settings):
        sl = SmartLeverage(strategy_settings)
        lev_weak = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.9,
            regime=_regime(), coin_tier=1,
            volatility_percentile=100, ensemble_strength="WEAK",
        )
        lev_strong = sl.calculate(
            "BTCUSDT", Side.BUY, confidence=0.9,
            regime=_regime(), coin_tier=1,
            volatility_percentile=100, ensemble_strength="STRONG",
        )
        assert lev_strong >= lev_weak
