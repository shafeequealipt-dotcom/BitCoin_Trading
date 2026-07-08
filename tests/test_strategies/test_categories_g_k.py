"""Tests for strategy implementations G1 through K4."""

import pytest

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.models.regime_types import MarketRegime
from src.strategies.registry import StrategyRegistry
from src.strategies.register_all import register_strategies_g_to_k, register_all_strategies


@pytest.fixture
def registry_gk():
    r = StrategyRegistry()
    register_strategies_g_to_k(r)
    return r


@pytest.fixture
def full_registry():
    r = StrategyRegistry()
    register_all_strategies(r)
    return r


@pytest.fixture
def sample_candles():
    candles = []
    for i in range(50):
        base = 70000 + (i - 25) * 10
        candles.append(OHLCV(
            symbol="BTCUSDT", timeframe=TimeFrame.M5,
            timestamp=now_utc(), open=base - 5, high=base + 20,
            low=base - 20, close=base + 5, volume=100 + i * 2,
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
    def test_20_gk_strategies_registered(self, registry_gk):
        assert registry_gk.count == 20  # G1-G4 + H1-H4 + I1-I4 + J1-J4 + K1-K4

    def test_all_strategies_registered(self, full_registry):
        assert full_registry.count >= 39  # 39 base + X1 on testnet

    def test_all_names_unique(self, full_registry):
        names = [s.name for s in full_registry.get_all()]
        assert len(names) == len(set(names))

    def test_gk_categories_present(self, registry_gk):
        cats = {s.category for s in registry_gk.get_all()}
        assert "predatory" in cats
        assert "microstructure" in cats
        assert "time_based" in cats
        assert "cross_market" in cats
        assert "ai_enhanced" in cats

    def test_full_registry_all_categories(self, full_registry):
        cats = {s.category for s in full_registry.get_all()}
        expected = {"scalping", "momentum", "mean_reversion", "funding_arb",
                    "sentiment", "advanced", "predatory", "microstructure",
                    "time_based", "cross_market", "ai_enhanced"}
        assert expected.issubset(cats)


# =============================================================================
# Properties Tests
# =============================================================================

class TestProperties:
    def test_g1_properties(self, registry_gk):
        s = registry_gk.get("G1_stop_hunt")
        assert s is not None
        assert s.category == "predatory"
        assert MarketRegime.VOLATILE in s.applicable_regimes

    def test_h1_properties(self, registry_gk):
        s = registry_gk.get("H1_funding_predict")
        assert s is not None
        assert s.category == "microstructure"
        assert len(s.applicable_regimes) == len(MarketRegime)  # All regimes (incl. UNKNOWN)

    def test_i1_properties(self, registry_gk):
        s = registry_gk.get("I1_kill_zone")
        assert s is not None
        assert s.category == "time_based"

    def test_j1_properties(self, registry_gk):
        s = registry_gk.get("J1_btc_dominance")
        assert s is not None
        assert s.category == "cross_market"
        assert s.timeframe == TimeFrame.D1

    def test_k3_placeholder(self, registry_gk):
        s = registry_gk.get("K3_ensemble")
        assert s is not None
        assert s.risk_level == "low"

    def test_k4_placeholder(self, registry_gk):
        s = registry_gk.get("K4_optimizer")
        assert s is not None
        assert s.timeframe == TimeFrame.W1


# =============================================================================
# Scan Tests — Placeholders Return None
# =============================================================================

class TestPlaceholderScans:
    @pytest.mark.asyncio
    async def test_k3_always_none(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K3_ensemble")
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, {}, None, None)
        assert signal is None

    @pytest.mark.asyncio
    async def test_k4_always_none(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K4_optimizer")
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, {}, None, None)
        assert signal is None

    @pytest.mark.asyncio
    async def test_k1_no_trigger_returns_none(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K1_claude_conviction")
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, {}, None, None)
        assert signal is None

    @pytest.mark.asyncio
    async def test_k2_no_matches_returns_none(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K2_pattern_memory")
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, {}, None, {})
        assert signal is None


# =============================================================================
# Vote Tests
# =============================================================================

class TestVotes:
    def test_g1_vote_neutral_no_pattern(self, registry_gk, sample_candles):
        s = registry_gk.get("G1_stop_hunt")
        ta = {"support_resistance": {"support_levels": [69000], "resistance_levels": [71000]}}
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, ta, None, None)
        assert vote == "NEUTRAL"

    def test_g2_vote_extreme_greed(self, registry_gk, sample_candles):
        s = registry_gk.get("G2_retail_fade")
        vote, conf, reason = s.vote("BTCUSDT", Side.SELL, sample_candles, {},
                                     None, {"fear_greed": 90})
        assert vote == "SELL"
        assert conf > 0.5

    def test_h4_vote_positive_flow(self, registry_gk, sample_candles):
        s = registry_gk.get("H4_order_flow")
        ta = {"volume": {"chaikin_money_flow": 0.2, "force_index": 1000}}
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, ta, None, None)
        assert vote == "BUY"

    def test_i4_vote_top_close(self, registry_gk):
        candles = [OHLCV(
            symbol="BTCUSDT", timeframe=TimeFrame.H1,
            timestamp=now_utc(), open=70000, high=70100,
            low=69900, close=70090, volume=100,
        )]
        s = registry_gk.get("I4_hourly_close")
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, candles, {}, None, None)
        assert vote == "BUY"

    def test_k3_never_votes(self, registry_gk, sample_candles):
        s = registry_gk.get("K3_ensemble")
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, {}, None, None)
        assert vote == "NEUTRAL"
        assert conf == 0.0

    def test_k4_never_votes(self, registry_gk, sample_candles):
        s = registry_gk.get("K4_optimizer")
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, {}, None, None)
        assert vote == "NEUTRAL"
        assert conf == 0.0

    def test_j3_arb_neutral(self, registry_gk, sample_candles):
        s = registry_gk.get("J3_price_lag")
        vote, conf, reason = s.vote("BTCUSDT", Side.BUY, sample_candles, {}, None, None)
        assert vote == "NEUTRAL"


# =============================================================================
# Scan Tests — Insufficient Data
# =============================================================================

class TestInsufficientData:
    @pytest.mark.asyncio
    async def test_empty_candles(self, registry_gk, sample_ticker):
        for strategy in registry_gk.get_all():
            signal = await strategy.scan("BTCUSDT", [], sample_ticker, {}, None, None)
            assert signal is None, f"{strategy.name} should return None with empty candles"

    @pytest.mark.asyncio
    async def test_few_candles(self, registry_gk, sample_ticker):
        few = [OHLCV(symbol="BTCUSDT", timeframe=TimeFrame.M5,
                      timestamp=now_utc(), open=70000, high=70100,
                      low=69900, close=70050, volume=100)
               for _ in range(3)]
        for strategy in registry_gk.get_all():
            signal = await strategy.scan("BTCUSDT", few, sample_ticker, {}, None, None)
            assert signal is None, f"{strategy.name} should return None with too few candles"


# =============================================================================
# Special Strategy Tests
# =============================================================================

class TestK1ClaudeConviction:
    @pytest.mark.asyncio
    async def test_triggered_with_valid_altdata(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K1_claude_conviction")
        ta = {"momentum": {"rsi_14": 55}, "trend": {"trend_summary": "BULLISH"}}
        altdata = {"k1_trigger": {"symbol": "BTCUSDT", "direction": "Buy", "score": 85, "consensus": "STRONG"}}
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, ta, None, altdata)
        assert signal is not None
        assert signal.direction == Side.BUY
        assert signal.conditions_met["claude_triggered"] is True

    @pytest.mark.asyncio
    async def test_not_triggered_low_score(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K1_claude_conviction")
        ta = {"momentum": {"rsi_14": 55}, "trend": {"trend_summary": "NEUTRAL"}}
        altdata = {"k1_trigger": {"symbol": "BTCUSDT", "direction": "Buy", "score": 60, "consensus": "WEAK"}}
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, ta, None, altdata)
        assert signal is None


class TestK2PatternMemory:
    @pytest.mark.asyncio
    async def test_bullish_pattern_matches(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K2_pattern_memory")
        ta = {"momentum": {"rsi_14": 50}}
        matches = [{"outcome": "up", "pnl_pct": 1.5}] * 8 + [{"outcome": "down", "pnl_pct": -0.5}] * 2
        altdata = {"pattern_matches": matches}
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, ta, None, altdata)
        assert signal is not None
        assert signal.direction == Side.BUY

    @pytest.mark.asyncio
    async def test_mixed_patterns_no_signal(self, registry_gk, sample_candles, sample_ticker):
        s = registry_gk.get("K2_pattern_memory")
        ta = {"momentum": {"rsi_14": 50}}
        matches = [{"outcome": "up"}] * 5 + [{"outcome": "down"}] * 5
        altdata = {"pattern_matches": matches}
        signal = await s.scan("BTCUSDT", sample_candles, sample_ticker, ta, None, altdata)
        assert signal is None
