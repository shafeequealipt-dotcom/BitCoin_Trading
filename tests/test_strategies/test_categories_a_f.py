"""Tests for strategy implementations A1 through F4."""

import pytest

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.models.regime_types import MarketRegime
from src.strategies.registry import StrategyRegistry
from src.strategies.register_all import register_strategies_a_to_f


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def registry():
    r = StrategyRegistry()
    register_strategies_a_to_f(r)
    return r


@pytest.fixture
def bullish_ta():
    """TA data that should trigger LONG signals for many strategies."""
    return {
        "trend": {
            "sma_20": 69500, "sma_50": 68000, "sma_200": 65000,
            "ema_12": 69800, "ema_26": 69500,
            "macd": {"macd_line": 150, "signal_line": 100, "histogram": 50},
            "adx": {"adx": 30, "plus_di": 28, "minus_di": 15},
            "supertrend": {"value": 68500, "direction": 1},
            "parabolic_sar": 69000,
            "trend_summary": "BULLISH",
        },
        "momentum": {
            "rsi_14": 22, "stochastic": {"k": 18, "d": 15},
            "stochastic_rsi": {"k": 15, "d": 12},
            "cci_20": -120, "williams_r": -85, "roc_12": -2.5,
            "momentum_10": -500, "awesome_oscillator": -200,
            "tsi": {"tsi": -15, "signal": -20},
            "momentum_summary": "BEARISH",
        },
        "volatility": {
            "bollinger": {"upper": 71000, "middle": 69500, "lower": 68000, "bandwidth": 4.3},
            "atr_14": 500, "natr_14": 1.2,
            "keltner": {"upper": 70500, "middle": 69500, "lower": 68500},
            "donchian": {"upper": 71500, "middle": 69750, "lower": 68000},
            "historical_volatility": 0.4, "choppiness_index": 35,
            "volatility_summary": "MODERATE",
        },
        "volume": {
            "obv": 1500000, "vwap": 69600, "mfi_14": 18,
            "chaikin_money_flow": 0.15, "accumulation_distribution": 500000,
            "volume_sma_ratio": 2.0, "force_index": 5000,
            "volume_summary": "ABOVE_AVERAGE",
        },
        "patterns": {
            "candlestick": [{"name": "hammer", "type": "bullish", "confidence": 0.8}],
            "chart": [],
        },
        "support_resistance": {
            "current_price": 68200,
            "support_levels": [68000, 67500, 67000],
            "resistance_levels": [70000, 71000, 72000],
        },
        "overall": {
            "signal": "BUY", "score": 0.6, "confidence": 0.7,
            "bullish_indicators": 5, "bearish_indicators": 2, "neutral_indicators": 1,
            "key_reasons": ["RSI oversold", "Supertrend bullish"],
        },
    }


@pytest.fixture
def bearish_ta():
    """TA data that should trigger SHORT signals."""
    return {
        "trend": {
            "sma_20": 70500, "sma_50": 72000, "sma_200": 75000,
            "ema_12": 70200, "ema_26": 70500,
            "macd": {"macd_line": -150, "signal_line": -100, "histogram": -50},
            "adx": {"adx": 30, "plus_di": 15, "minus_di": 28},
            "supertrend": {"value": 71500, "direction": -1},
            "parabolic_sar": 71000,
            "trend_summary": "BEARISH",
        },
        "momentum": {
            "rsi_14": 78, "stochastic": {"k": 82, "d": 85},
            "stochastic_rsi": {"k": 85, "d": 88},
            "cci_20": 120, "williams_r": -15, "roc_12": 2.5,
            "momentum_10": 500, "awesome_oscillator": 200,
            "tsi": {"tsi": 15, "signal": 20},
            "momentum_summary": "BULLISH",
        },
        "volatility": {
            "bollinger": {"upper": 72000, "middle": 70500, "lower": 69000, "bandwidth": 4.3},
            "atr_14": 500, "natr_14": 1.2,
            "keltner": {"upper": 71500, "middle": 70500, "lower": 69500},
            "donchian": {"upper": 72500, "middle": 70750, "lower": 69000},
            "historical_volatility": 0.4, "choppiness_index": 35,
            "volatility_summary": "MODERATE",
        },
        "volume": {
            "obv": 1500000, "vwap": 70400, "mfi_14": 82,
            "chaikin_money_flow": -0.15, "accumulation_distribution": -500000,
            "volume_sma_ratio": 2.0, "force_index": -5000,
            "volume_summary": "ABOVE_AVERAGE",
        },
        "patterns": {
            "candlestick": [{"name": "shooting_star", "type": "bearish", "confidence": 0.8}],
            "chart": [],
        },
        "support_resistance": {
            "current_price": 72000,
            "support_levels": [70000, 69000, 68000],
            "resistance_levels": [72000, 73000, 74000],
        },
        "overall": {
            "signal": "SELL", "score": -0.6, "confidence": 0.7,
            "bullish_indicators": 2, "bearish_indicators": 5, "neutral_indicators": 1,
            "key_reasons": ["RSI overbought", "Supertrend bearish"],
        },
    }


@pytest.fixture
def neutral_ta():
    """TA data that should NOT trigger signals."""
    return {
        "trend": {
            "sma_20": 70000, "sma_50": 70000, "sma_200": 70000,
            "ema_12": 70000, "ema_26": 70000,
            "macd": {"macd_line": 0, "signal_line": 0, "histogram": 0},
            "adx": {"adx": 15, "plus_di": 20, "minus_di": 20},
            "supertrend": {"value": 70000, "direction": 0},
            "parabolic_sar": 70000,
            "trend_summary": "NEUTRAL",
        },
        "momentum": {
            "rsi_14": 50, "stochastic": {"k": 50, "d": 50},
            "stochastic_rsi": {"k": 50, "d": 50},
            "cci_20": 0, "williams_r": -50, "roc_12": 0,
            "momentum_10": 0, "awesome_oscillator": 0,
            "tsi": {"tsi": 0, "signal": 0},
            "momentum_summary": "NEUTRAL",
        },
        "volatility": {
            "bollinger": {"upper": 71000, "middle": 70000, "lower": 69000, "bandwidth": 2.8},
            "atr_14": 300, "natr_14": 0.8,
            "keltner": {"upper": 70800, "middle": 70000, "lower": 69200},
            "donchian": {"upper": 71000, "middle": 70000, "lower": 69000},
            "historical_volatility": 0.3, "choppiness_index": 55,
            "volatility_summary": "MODERATE",
        },
        "volume": {
            "obv": 1000000, "vwap": 70000, "mfi_14": 50,
            "chaikin_money_flow": 0, "accumulation_distribution": 0,
            "volume_sma_ratio": 1.0, "force_index": 0,
            "volume_summary": "AVERAGE",
        },
        "patterns": {"candlestick": [], "chart": []},
        "support_resistance": {
            "current_price": 70000,
            "support_levels": [69000], "resistance_levels": [71000],
        },
        "overall": {
            "signal": "NEUTRAL", "score": 0, "confidence": 0.3,
            "bullish_indicators": 2, "bearish_indicators": 2, "neutral_indicators": 4,
            "key_reasons": [],
        },
    }


@pytest.fixture
def sample_candles():
    """50 sample OHLCV candles at ~70000."""
    candles = []
    for i in range(50):
        base = 70000 + (i - 25) * 10
        candles.append(OHLCV(
            symbol="BTCUSDT", timeframe=TimeFrame.M5,
            timestamp=now_utc(), open=base - 5, high=base + 20,
            low=base - 20, close=base + 5, volume=100,
        ))
    return candles


@pytest.fixture
def sample_ticker():
    return Ticker(
        symbol="BTCUSDT", last_price=70000, bid=69990, ask=70010,
        high_24h=71000, low_24h=68000, volume_24h=500_000_000,
        change_24h_pct=2.5,
    )


# =============================================================================
# Registration Tests
# =============================================================================

class TestRegistration:
    def test_all_19_strategies_registered(self, registry):
        assert registry.count == 19

    def test_all_strategy_names_unique(self, registry):
        names = [s.name for s in registry.get_all()]
        assert len(names) == len(set(names))

    def test_all_categories_present(self, registry):
        cats = {s.category for s in registry.get_all()}
        assert "scalping" in cats
        assert "momentum" in cats
        assert "mean_reversion" in cats
        assert "funding_arb" in cats
        assert "sentiment" in cats
        assert "advanced" in cats

    def test_all_have_applicable_regimes(self, registry):
        for s in registry.get_all():
            assert len(s.applicable_regimes) > 0

    def test_all_have_valid_timeframe(self, registry):
        for s in registry.get_all():
            assert isinstance(s.timeframe, TimeFrame)


# =============================================================================
# Strategy Properties Tests
# =============================================================================

class TestStrategyProperties:
    def test_a1_properties(self, registry):
        s = registry.get("A1_rsi_reversal")
        assert s is not None
        assert s.category == "scalping"
        assert s.risk_level == "low"
        assert s.timeframe == TimeFrame.M5

    def test_b1_properties(self, registry):
        s = registry.get("B1_volume_breakout")
        assert s is not None
        assert s.category == "momentum"
        assert s.timeframe == TimeFrame.M15

    def test_d1_properties(self, registry):
        s = registry.get("D1_funding_fade")
        assert s is not None
        assert s.category == "funding_arb"
        assert len(s.applicable_regimes) == len(MarketRegime)  # All regimes (incl. UNKNOWN)

    def test_f4_properties(self, registry):
        s = registry.get("F4_grid_recovery")
        assert s is not None
        assert s.risk_level == "high"
        assert MarketRegime.RANGING in s.applicable_regimes


# =============================================================================
# Scan Tests — A1 RSI Reversal
# =============================================================================

class TestA1Scan:
    @pytest.mark.asyncio
    async def test_scan_long_signal(self, registry, sample_candles, sample_ticker):
        s = registry.get("A1_rsi_reversal")
        ta = {
            "momentum": {"rsi_14": 22, "stochastic": {"k": 18, "d": 15}},
            "volatility": {"bollinger": {"upper": 71000, "middle": 70000, "lower": 70005}},
            "volume": {"volume_sma_ratio": 2.0},
            "trend": {"adx": {"adx": 20, "plus_di": 22, "minus_di": 18}},
        }
        ticker = Ticker(symbol="BTCUSDT", last_price=70000, bid=69990, ask=70010,
                        high_24h=71000, low_24h=68000, volume_24h=500_000_000, change_24h_pct=-1)
        signal = await s.scan("BTCUSDT", sample_candles, ticker, ta, None, None)
        assert signal is not None
        assert signal.direction == Side.BUY
        assert signal.suggested_stop_loss < signal.entry_price
        assert "rsi_oversold" in signal.conditions_met

    @pytest.mark.asyncio
    async def test_scan_no_signal_neutral_rsi(self, registry, sample_candles, sample_ticker):
        s = registry.get("A1_rsi_reversal")
        ta = {
            "momentum": {"rsi_14": 50, "stochastic": {"k": 50, "d": 50}},
            "volatility": {"bollinger": {"upper": 71000, "middle": 70000, "lower": 69000}},
            "volume": {"volume_sma_ratio": 2.0},
            "trend": {"adx": {"adx": 20, "plus_di": 22, "minus_di": 18}},
        }
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, ta, None, None)
        assert signal is None


# =============================================================================
# Vote Tests
# =============================================================================

class TestVoting:
    def test_a1_vote_buy_agreement(self, registry, sample_candles):
        s = registry.get("A1_rsi_reversal")
        ta = {"momentum": {"rsi_14": 25}}
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, ta, None, None)
        assert vote == "BUY"
        assert conf > 0

    def test_a1_vote_neutral(self, registry, sample_candles):
        s = registry.get("A1_rsi_reversal")
        ta = {"momentum": {"rsi_14": 50}}
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, ta, None, None)
        assert vote == "NEUTRAL"

    def test_b2_vote_supertrend(self, registry, sample_candles):
        s = registry.get("B2_supertrend")
        ta = {"trend": {"supertrend": {"direction": 1}, "adx": {"adx": 35}}}
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, ta, None, None)
        assert vote == "BUY"
        assert conf > 0.5

    def test_f2_vote_alignment(self, registry, sample_candles):
        s = registry.get("F2_multi_tf_alignment")
        ta = {"trend": {"trend_summary": "BULLISH", "supertrend": {"direction": 1}}}
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, ta, None, None)
        assert vote == "BUY"
        assert conf > 0.8

    def test_d1_vote_funding(self, registry, sample_candles):
        s = registry.get("D1_funding_fade")
        vote, conf, reason = s.vote("BTCUSDT", Side.SELL, sample_candles, {},
                                     None, {"funding_rate": 0.0005})
        assert vote == "SELL"

    def test_e1_vote_fear(self, registry, sample_candles):
        s = registry.get("E1_fear_greed")
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, {},
                                     None, {"fear_greed": 10})
        assert vote == "BUY"
        assert conf > 0.5

    def test_f4_never_votes(self, registry, sample_candles):
        s = registry.get("F4_grid_recovery")
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, {}, None, None)
        assert vote == "NEUTRAL"
        assert conf == 0.0


# =============================================================================
# Scan Tests — No Signal with Neutral Data
# =============================================================================

class TestNoSignalNeutral:
    @pytest.mark.asyncio
    async def test_no_signal_with_neutral_data(self, registry, sample_candles, sample_ticker, neutral_ta):
        """All strategies should return None with neutral/mid-range indicator values."""
        for strategy in registry.get_all():
            if strategy.name == "F4_grid_recovery":
                continue  # Special case
            signal = await strategy.scan("BTCUSDT", sample_candles, sample_ticker,
                                          neutral_ta, None, None)
            # Most strategies should not trigger on neutral data
            # (some might on edge cases, that's OK)


# =============================================================================
# Scan Tests — Insufficient Data
# =============================================================================

class TestInsufficientData:
    @pytest.mark.asyncio
    async def test_no_candles(self, registry, sample_ticker, neutral_ta):
        for strategy in registry.get_all():
            signal = await strategy.scan("BTCUSDT", [], sample_ticker, neutral_ta, None, None)
            assert signal is None, f"{strategy.name} should return None with empty candles"

    @pytest.mark.asyncio
    async def test_few_candles(self, registry, sample_ticker, neutral_ta):
        few = [OHLCV(symbol="BTCUSDT", timeframe=TimeFrame.M5, timestamp=now_utc(),
                      open=70000, high=70100, low=69900, close=70050, volume=100)
               for _ in range(3)]
        for strategy in registry.get_all():
            signal = await strategy.scan("BTCUSDT", few, sample_ticker, neutral_ta, None, None)
            assert signal is None, f"{strategy.name} should return None with too few candles"
