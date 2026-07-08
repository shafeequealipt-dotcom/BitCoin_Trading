"""Cycle-gate + cold-start boundary wait — Layer 1 restructure Phase 4."""

from unittest.mock import MagicMock

from src.core.types import WorkerTier


def test_worker_tier_enum_values() -> None:
    assert WorkerTier.LAYER1A.value == "layer1a"
    assert WorkerTier.LAYER1B.value == "layer1b"
    assert WorkerTier.LAYER1C.value == "layer1c"
    assert WorkerTier.LAYER1D.value == "layer1d"
    assert WorkerTier.LAYER4.value == "layer4"
    assert WorkerTier.LAYER5.value == "layer5"
    assert WorkerTier.UTILITY.value == "utility"


def test_cycle_gated_class_attrs() -> None:
    """Each Layer 1 worker class advertises the right cycle_gated value.

    1A workers must NOT be gated (always run); 1B/1C/1D must be gated.
    """
    from src.workers.altdata_worker import AltDataWorker
    from src.workers.kline_worker import KlineWorker
    from src.workers.news_worker import NewsWorker
    from src.workers.price_worker import PriceWorker
    from src.workers.regime_worker import RegimeWorker
    from src.workers.scanner_worker import ScannerWorker
    from src.workers.signal_worker import SignalWorker
    from src.workers.strategy_worker import StrategyWorker
    from src.workers.structure_worker import StructureWorker

    # 1A — never gated
    assert KlineWorker.cycle_gated is False
    assert PriceWorker.cycle_gated is False
    assert AltDataWorker.cycle_gated is False
    assert NewsWorker.cycle_gated is False

    # 1B/1C/1D — always gated
    assert StructureWorker.cycle_gated is True
    assert SignalWorker.cycle_gated is True
    assert RegimeWorker.cycle_gated is True
    assert StrategyWorker.cycle_gated is True
    assert ScannerWorker.cycle_gated is True


class TestSecondsToBoundary:
    def test_zero_at_boundary(self) -> None:
        from src.core.layer_manager import LayerManager
        # 1500 epoch seconds: 1500 % 300 == 0 → exactly on a 5-min boundary.
        assert LayerManager._seconds_to_next_window_boundary(now=1500) == 0.0

    def test_returns_seconds_until_next(self) -> None:
        from src.core.layer_manager import LayerManager
        # 30s past a boundary → 270s until the next one.
        assert LayerManager._seconds_to_next_window_boundary(now=1500.0 + 30) == 270.0

    def test_custom_window_minutes(self) -> None:
        from src.core.layer_manager import LayerManager
        # 1m window: 1500 + 30 = 1530; 1530 % 60 = 30; 60-30 = 30s remaining.
        assert LayerManager._seconds_to_next_window_boundary(
            window_minutes=1, now=1500.0 + 30,
        ) == 30.0


class TestIsCycleActive:
    def test_both_required(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._layer_active = {1: True, 2: True, 3: True}
        assert lm.is_cycle_active() is True

        lm._layer_active = {1: True, 2: True, 3: False}
        assert lm.is_cycle_active() is False

        lm._layer_active = {1: True, 2: False, 3: True}
        assert lm.is_cycle_active() is False
