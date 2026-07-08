"""Tests for StrategyRegistry."""

import pytest

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.strategies.base_strategy import BaseStrategy
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal, StrategyPerformance
from src.strategies.registry import StrategyRegistry


class DummyStrategy(BaseStrategy):
    """Concrete strategy for testing."""

    @property
    def name(self) -> str:
        return "test_strategy"

    @property
    def category(self) -> str:
        return "scalping"

    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.RANGING]

    @property
    def timeframe(self) -> TimeFrame:
        return TimeFrame.M5

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata):
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        return ("BUY", 0.7, "test vote")


class MomentumStrategy(BaseStrategy):
    @property
    def name(self): return "B1_momentum"
    @property
    def category(self): return "momentum"
    @property
    def applicable_regimes(self): return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self): return TimeFrame.M15

    async def scan(self, *args, **kwargs): return None
    def vote(self, *args, **kwargs): return ("NEUTRAL", 0.5, "")


class TestRegistration:
    def test_register_and_get(self):
        reg = StrategyRegistry()
        s = DummyStrategy()
        reg.register(s)
        assert reg.get("test_strategy") is s
        assert reg.count == 1

    def test_get_nonexistent(self):
        reg = StrategyRegistry()
        assert reg.get("nonexistent") is None

    def test_get_all(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        reg.register(MomentumStrategy())
        assert len(reg.get_all()) == 2


class TestFiltering:
    def test_get_active_for_regime_filtered_by_default(self):
        """Layer 1 Defect 1 (2026-05-21) — the function now HONORS its
        regime argument by default. The category-to-regime mapping at
        regime_types.REGIME_ACTIVE_CATEGORIES gates activation:

        - TRENDING_UP active categories include both ``scalping`` and
          ``momentum`` → both fixtures fire.
        - RANGING active categories include ``scalping`` but NOT
          ``momentum`` → only DummyStrategy fires.

        The operator chose flag-default-ON per IMPLEMENT_LAYER1_REPAIR
        Defect 1 Option A. The flag is in
        StrategyEngineSettings.strategy_regime_filter_enabled and
        flows to the registry via constructor. Setting it False
        restores the pre-Defect-1 uniform-strategy behavior (every
        enabled strategy regardless of regime) — exercised by the
        next test.
        """
        reg = StrategyRegistry()  # default flag True
        reg.register(DummyStrategy())     # category="scalping"
        reg.register(MomentumStrategy())  # category="momentum"

        # TRENDING_UP includes scalping AND momentum → both fire.
        trending = reg.get_active_for_regime(MarketRegime.TRENDING_UP)
        assert {s.name for s in trending} == {"test_strategy", "B1_momentum"}

        # RANGING includes scalping but NOT momentum → only scalping.
        ranging = reg.get_active_for_regime(MarketRegime.RANGING)
        assert {s.name for s in ranging} == {"test_strategy"}

    def test_get_active_for_regime_legacy_uniform_with_flag_false(self):
        """When the operator flips ``regime_filter_enabled`` to False
        (emergency rollback), every enabled strategy is returned
        regardless of regime — exact pre-Defect-1 behavior."""
        reg = StrategyRegistry(regime_filter_enabled=False)
        reg.register(DummyStrategy())     # category="scalping"
        reg.register(MomentumStrategy())  # category="momentum"

        # Both regimes return both strategies in legacy mode, even
        # though RANGING's REGIME_ACTIVE_CATEGORIES excludes momentum.
        trending = reg.get_active_for_regime(MarketRegime.TRENDING_UP)
        assert len(trending) == 2

        ranging = reg.get_active_for_regime(MarketRegime.RANGING)
        assert len(ranging) == 2

    def test_set_regime_filter_enabled_toggles_at_runtime(self):
        """``set_regime_filter_enabled`` flips the contract live without
        re-constructing the registry — useful for emergency rollback
        via Telegram or REPL when a regression appears."""
        reg = StrategyRegistry(regime_filter_enabled=True)
        reg.register(DummyStrategy())
        reg.register(MomentumStrategy())

        ranging_filtered = reg.get_active_for_regime(MarketRegime.RANGING)
        assert len(ranging_filtered) == 1   # scalping only

        reg.set_regime_filter_enabled(False)
        ranging_legacy = reg.get_active_for_regime(MarketRegime.RANGING)
        assert len(ranging_legacy) == 2     # both, legacy uniform

        reg.set_regime_filter_enabled(True)
        ranging_back = reg.get_active_for_regime(MarketRegime.RANGING)
        assert len(ranging_back) == 1       # filtered again

    def test_get_by_category(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        reg.register(MomentumStrategy())
        assert len(reg.get_by_category("scalping")) == 1
        assert len(reg.get_by_category("momentum")) == 1
        assert len(reg.get_by_category("nonexistent")) == 0

    def test_get_enabled_excludes_disabled(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        reg.register(MomentumStrategy())
        reg.set_enabled("test_strategy", False)
        enabled = reg.get_enabled()
        assert len(enabled) == 1
        assert enabled[0].name == "B1_momentum"


class TestPerformance:
    def test_update_performance_win(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        reg.update_performance("test_strategy", 2.5, True)
        perf = reg.get_performance("test_strategy")
        assert perf.wins == 1
        assert perf.total_trades == 1
        assert perf.win_rate == 1.0

    def test_update_performance_loss(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        reg.update_performance("test_strategy", -1.5, False)
        perf = reg.get_performance("test_strategy")
        assert perf.losses == 1
        assert perf.win_rate == 0.0
        assert perf.current_streak == -1

    def test_ensemble_weight_clamped(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        reg.set_ensemble_weight("test_strategy", 5.0)
        assert reg.get_performance("test_strategy").ensemble_weight == 3.0
        reg.set_ensemble_weight("test_strategy", 0.01)
        assert reg.get_performance("test_strategy").ensemble_weight == 0.1

    def test_registry_summary(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy())
        summary = reg.get_registry_summary()
        assert summary["total_strategies"] == 1
        assert summary["enabled"] == 1
        assert len(summary["strategies"]) == 1
