"""Verify each Layer 1 worker class advertises the right WorkerTier.

After the Phase 4 audit, ``WorkerTier`` is the single source of truth
and ``layer_tier_tag`` is a derived property on BaseWorker. These tests
assert both the class-level enum assignment AND that the derived
log-emission tag matches the documented uppercase form.
"""

from src.core.types import WorkerTier
from src.workers.altdata_worker import AltDataWorker
from src.workers.base_worker import BaseWorker
from src.workers.kline_worker import KlineWorker
from src.workers.news_worker import NewsWorker
from src.workers.price_worker import PriceWorker
from src.workers.regime_worker import RegimeWorker
from src.workers.scanner_worker import ScannerWorker
from src.workers.signal_worker import SignalWorker
from src.workers.strategy_worker import StrategyWorker
from src.workers.structure_worker import StructureWorker


def _derived_tag(cls: type) -> str | None:
    """Compute the layer_tier_tag the way a worker instance would.

    Avoids constructing a real instance (which needs full DI) by
    invoking the property's getter against a stub that exposes
    only ``worker_tier``.
    """
    stub = type("Stub", (), {"worker_tier": cls.worker_tier})()
    return BaseWorker.layer_tier_tag.fget(stub)


class TestWorkerTierClassAssignments:
    def test_layer1a(self) -> None:
        assert KlineWorker.worker_tier is WorkerTier.LAYER1A
        assert PriceWorker.worker_tier is WorkerTier.LAYER1A
        assert AltDataWorker.worker_tier is WorkerTier.LAYER1A
        assert NewsWorker.worker_tier is WorkerTier.LAYER1A

    def test_layer1b(self) -> None:
        assert StructureWorker.worker_tier is WorkerTier.LAYER1B
        assert SignalWorker.worker_tier is WorkerTier.LAYER1B
        assert RegimeWorker.worker_tier is WorkerTier.LAYER1B

    def test_layer1c(self) -> None:
        assert StrategyWorker.worker_tier is WorkerTier.LAYER1C

    def test_layer1d(self) -> None:
        assert ScannerWorker.worker_tier is WorkerTier.LAYER1D


class TestDerivedLogTag:
    """``layer_tier_tag`` derives uppercase log tag from the enum."""

    def test_layer1a_tag(self) -> None:
        assert _derived_tag(KlineWorker) == "LAYER1A"
        assert _derived_tag(PriceWorker) == "LAYER1A"
        assert _derived_tag(AltDataWorker) == "LAYER1A"
        assert _derived_tag(NewsWorker) == "LAYER1A"

    def test_layer1b_tag(self) -> None:
        assert _derived_tag(StructureWorker) == "LAYER1B"
        assert _derived_tag(SignalWorker) == "LAYER1B"
        assert _derived_tag(RegimeWorker) == "LAYER1B"

    def test_layer1c_tag(self) -> None:
        assert _derived_tag(StrategyWorker) == "LAYER1C"

    def test_layer1d_tag(self) -> None:
        assert _derived_tag(ScannerWorker) == "LAYER1D"

    def test_unset_returns_none(self) -> None:
        # Utility worker with no tier yields None — base loop won't emit
        # any tier-tagged markers.
        stub = type("Stub", (), {"worker_tier": None})()
        assert BaseWorker.layer_tier_tag.fget(stub) is None
