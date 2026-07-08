"""Phase 5c — EnsembleVoter scales size_multiplier by structural confidence.

Counter setups (Phase 4) carry confidence ≈ 0.35 (vs in-direction ≈
0.55-0.85). At the same consensus level they should size SMALLER than
in-direction setups. ``EnsembleVoter.vote`` reads
``setup.scoring_details["setup_type_confidence"]`` and applies a
``max(0.5, min(1.0, conf))`` factor to the CONSENSUS_SIZE-derived
size_mult.

Floor 0.5 mirrors scorer.py:5a and scanner_worker.py:5b — never
zero out legitimate consensus output.
"""

from __future__ import annotations

import pytest

from src.core.types import Side, TimeFrame
from src.strategies.base_strategy import BaseStrategy
from src.strategies.ensemble import EnsembleVoter
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal, ScoredSetup
from src.strategies.registry import StrategyRegistry


class _BullishStrategy(BaseStrategy):
    def __init__(self, n="bull"):
        self._name = n

    @property
    def name(self):
        return self._name

    @property
    def category(self):
        return "momentum"

    @property
    def applicable_regimes(self):
        return [MarketRegime.TRENDING_UP]

    @property
    def timeframe(self):
        return TimeFrame.M5

    async def scan(self, *a, **kw):
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        return ("BUY", 0.8, "bullish indicators")


def _make_scored_setup(setup_type_confidence: float | None = None,
                       trade_direction: str = "long") -> ScoredSetup:
    signal = RawSignal(
        strategy_name="A1_test", strategy_category="scalping",
        symbol="BTCUSDT", direction=Side.BUY,
        entry_price=70000, suggested_stop_loss=69000,
        suggested_take_profit=72000, timeframe="5",
    )
    details: dict = {
        "base": 30.0, "confluence": 15.0, "context": 12.0, "quality": 10.0,
    }
    if setup_type_confidence is not None:
        details["setup_type_confidence"] = setup_type_confidence
        details["trade_direction"] = trade_direction
    return ScoredSetup(
        raw_signal=signal, base_score=30, confluence_score=15,
        context_score=12, quality_score=10, total_score=67, grade="B",
        scoring_details=details,
    )


def _strong_consensus_voter(strategy_settings) -> EnsembleVoter:
    reg = StrategyRegistry()
    for i in range(7):
        reg.register(_BullishStrategy(f"bull_{i}"))
    return EnsembleVoter(reg, strategy_settings)


class TestPhase5cSizeMultConfidenceScaling:
    def test_full_confidence_size_unchanged(self, strategy_settings, sample_regime):
        voter = _strong_consensus_voter(strategy_settings)
        setup = _make_scored_setup(setup_type_confidence=1.0)
        result = voter.vote(
            setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        # STRONG → CONSENSUS_SIZE=1.0; conf=1.0 → factor=1.0; final=1.0.
        assert result.consensus_strength == "STRONG"
        assert result.size_multiplier == pytest.approx(1.0, abs=1e-3)

    def test_counter_confidence_reduces_size(self, strategy_settings, sample_regime):
        voter = _strong_consensus_voter(strategy_settings)
        setup = _make_scored_setup(setup_type_confidence=0.35)
        result = voter.vote(
            setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        # STRONG → 1.0; conf=0.35 → factor floors at 0.5; final=0.5.
        assert result.consensus_strength == "STRONG"
        assert result.size_multiplier == pytest.approx(0.5, abs=1e-3)

    def test_in_direction_high_conf_outsizes_counter(self, strategy_settings, sample_regime):
        voter = _strong_consensus_voter(strategy_settings)
        setup_in = _make_scored_setup(setup_type_confidence=0.85)
        setup_counter = _make_scored_setup(setup_type_confidence=0.35)
        r_in = voter.vote(
            setup_in, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        r_counter = voter.vote(
            setup_counter, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        # Both STRONG, but in-direction sizes 0.85 (above floor) vs counter floored at 0.5.
        assert r_in.consensus_strength == r_counter.consensus_strength == "STRONG"
        assert r_in.size_multiplier > r_counter.size_multiplier
        assert r_in.size_multiplier == pytest.approx(0.85, abs=1e-3)

    def test_legacy_no_confidence_uses_default_0_85(self, strategy_settings, sample_regime):
        voter = _strong_consensus_voter(strategy_settings)
        setup = _make_scored_setup(setup_type_confidence=None)
        result = voter.vote(
            setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        # No setup_type_confidence in scoring_details → default 0.85 → factor 0.85.
        assert result.size_multiplier == pytest.approx(0.85, abs=1e-3)

    def test_floor_at_0_5(self, strategy_settings, sample_regime):
        voter = _strong_consensus_voter(strategy_settings)
        setup = _make_scored_setup(setup_type_confidence=0.0)
        result = voter.vote(
            setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        assert result.size_multiplier == pytest.approx(0.5, abs=1e-3)

    def test_ceiling_at_1_0(self, strategy_settings, sample_regime):
        voter = _strong_consensus_voter(strategy_settings)
        setup = _make_scored_setup(setup_type_confidence=1.5)
        result = voter.vote(
            setup, {"BTCUSDT": []}, {"BTCUSDT": {}}, None, None, sample_regime,
        )
        assert result.size_multiplier == pytest.approx(1.0, abs=1e-3)
