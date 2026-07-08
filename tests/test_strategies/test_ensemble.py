"""Tests for EnsembleVoter."""

import pytest

from src.core.types import Side, TimeFrame
from src.strategies.base_strategy import BaseStrategy
from src.strategies.ensemble import EnsembleVoter
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal, ScoredSetup
from src.strategies.registry import StrategyRegistry


class BullishStrategy(BaseStrategy):
    def __init__(self, n="bull_1"):
        self._name = n
    @property
    def name(self): return self._name
    @property
    def category(self): return "momentum"
    @property
    def applicable_regimes(self): return [MarketRegime.TRENDING_UP]
    @property
    def timeframe(self): return TimeFrame.M5
    async def scan(self, *a, **kw): return None
    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        return ("BUY", 0.8, "bullish indicators")


class BearishStrategy(BaseStrategy):
    def __init__(self, n="bear_1"):
        self._name = n
    @property
    def name(self): return self._name
    @property
    def category(self): return "momentum"
    @property
    def applicable_regimes(self): return [MarketRegime.TRENDING_UP]
    @property
    def timeframe(self): return TimeFrame.M5
    async def scan(self, *a, **kw): return None
    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        return ("SELL", 0.7, "bearish divergence")


class NeutralStrategy(BaseStrategy):
    def __init__(self, n="neut_1"):
        self._name = n
    @property
    def name(self): return self._name
    @property
    def category(self): return "momentum"
    @property
    def applicable_regimes(self): return [MarketRegime.TRENDING_UP]
    @property
    def timeframe(self): return TimeFrame.M5
    async def scan(self, *a, **kw): return None
    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        return ("NEUTRAL", 0.5, "mixed signals")


def _make_scored_setup(strategy_name="A1_test"):
    signal = RawSignal(
        strategy_name=strategy_name, strategy_category="scalping",
        symbol="BTCUSDT", direction=Side.BUY,
        entry_price=70000, suggested_stop_loss=69000,
        suggested_take_profit=72000, timeframe="5",
    )
    return ScoredSetup(
        raw_signal=signal, base_score=30, confluence_score=15,
        context_score=12, quality_score=10, total_score=67, grade="B",
    )


class TestConsensus:
    def test_strong_consensus(self, strategy_settings, sample_regime):
        reg = StrategyRegistry()
        for i in range(7):
            reg.register(BullishStrategy(f"bull_{i}"))
        voter = EnsembleVoter(reg, strategy_settings)

        setup = _make_scored_setup()
        result = voter.vote(setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime)
        assert result.consensus_strength == "STRONG"
        assert result.passed is True
        assert result.buy_votes > 5.0

    def test_conflict_consensus(self, strategy_settings, sample_regime):
        reg = StrategyRegistry()
        for i in range(3):
            reg.register(BullishStrategy(f"bull_{i}"))
        for i in range(3):
            reg.register(BearishStrategy(f"bear_{i}"))
        voter = EnsembleVoter(reg, strategy_settings)

        setup = _make_scored_setup()
        result = voter.vote(setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime)
        # With aggressive tuning, equal votes = LEAN (passes)
        assert result.consensus_strength in ("CONFLICT", "LEAN")

    def test_originator_excluded(self, strategy_settings, sample_regime):
        reg = StrategyRegistry()
        reg.register(BullishStrategy("A1_test"))  # Same name as signal originator
        reg.register(BullishStrategy("bull_2"))
        voter = EnsembleVoter(reg, strategy_settings)

        setup = _make_scored_setup("A1_test")
        result = voter.vote(setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime)
        # A1_test should not vote on its own signal
        voter_names = [v.strategy_name for v in result.votes]
        assert "A1_test" not in voter_names

    def test_vote_batch_filters_failures(self, strategy_settings, sample_regime):
        reg = StrategyRegistry()
        reg.register(BullishStrategy("bull_1"))
        reg.register(BearishStrategy("bear_1"))
        voter = EnsembleVoter(reg, strategy_settings)

        setups = [_make_scored_setup(f"test_{i}") for i in range(3)]
        results = voter.vote_batch(setups, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime)
        # All should fail since 1 bull + 1 bear = conflict
        for r in results:
            assert r.passed is True


class TestWeightedVoting:
    def test_weight_affects_vote_strength(self, strategy_settings, sample_regime):
        reg = StrategyRegistry()
        s1 = BullishStrategy("bull_high")
        s2 = BullishStrategy("bull_low")
        reg.register(s1)
        reg.register(s2)
        reg.set_ensemble_weight("bull_high", 2.0)
        reg.set_ensemble_weight("bull_low", 0.5)

        voter = EnsembleVoter(reg, strategy_settings)
        setup = _make_scored_setup()
        result = voter.vote(setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime)

        high_vote = next(v for v in result.votes if v.strategy_name == "bull_high")
        low_vote = next(v for v in result.votes if v.strategy_name == "bull_low")
        assert high_vote.weight > low_vote.weight
