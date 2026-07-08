"""Phase 1 audit fix: BaseWorker → CycleTracker integration.

Regression guard for the gap caught during the post-Phase-9 audit:
Layer 1B/1C workers must record their tick latency in the CycleTracker
so the CYCLE_COMPLETE rollup shows real numbers instead of zeros.
"""

from unittest.mock import MagicMock

from src.core.types import WorkerTier
from src.workers.base_worker import BaseWorker


class _StubWorker(BaseWorker):
    """Minimal BaseWorker subclass for unit-testing the cycle helpers.

    Bypasses ``BaseWorker.__init__`` so we don't need DI for the unit
    tests. Each test sets ``worker_tier`` (the canonical class-level
    enum) on the stub directly and exercises the helper methods.
    """

    def __init__(self) -> None:  # type: ignore[override]
        self.name = "stub_worker"
        self._layer_manager = None
        self._cycle_tracker = None
        self.worker_tier = None  # overridden per test

    async def tick(self) -> None:  # pragma: no cover — abstract impl
        return None


class TestMaybeStartCycle:
    def test_no_tracker_returns_none(self) -> None:
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1B
        assert w._maybe_start_cycle(0.0) is None

    def test_layer1a_skipped(self) -> None:
        """1A has no cycle semantics; never starts."""
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1A
        ct = MagicMock()
        w._cycle_tracker = ct
        assert w._maybe_start_cycle(0.0) is None
        ct.start_cycle.assert_not_called()

    def test_layer1d_skipped_in_base_loop(self) -> None:
        """1D drives its own start/end inside ScannerWorker.tick()."""
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1D
        ct = MagicMock()
        w._cycle_tracker = ct
        assert w._maybe_start_cycle(0.0) is None
        ct.start_cycle.assert_not_called()

    def test_layer1b_starts(self) -> None:
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1B
        ct = MagicMock()
        ct.start_cycle.return_value = "c-2026-04-27-21:30"
        w._cycle_tracker = ct
        cid = w._maybe_start_cycle(0.0)
        assert cid == "c-2026-04-27-21:30"
        ct.start_cycle.assert_called_once_with("layer1b")

    def test_layer1c_starts(self) -> None:
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1C
        ct = MagicMock()
        ct.start_cycle.return_value = "c-2026-04-27-21:30"
        w._cycle_tracker = ct
        cid = w._maybe_start_cycle(0.0)
        assert cid == "c-2026-04-27-21:30"
        ct.start_cycle.assert_called_once_with("layer1c")

    def test_tracker_exception_falls_through(self) -> None:
        """A buggy tracker must NOT break the production tick path."""
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1B
        ct = MagicMock()
        ct.start_cycle.side_effect = RuntimeError("boom")
        w._cycle_tracker = ct
        assert w._maybe_start_cycle(0.0) is None  # swallowed


class TestMaybeEndCycle:
    def test_none_cycle_id_skipped(self) -> None:
        w = _StubWorker()
        w._cycle_tracker = MagicMock()
        w.worker_tier = WorkerTier.LAYER1B
        w._maybe_end_cycle(None)
        w._cycle_tracker.end_cycle.assert_not_called()

    def test_no_tracker_skipped(self) -> None:
        w = _StubWorker()
        w._cycle_tracker = None
        w._maybe_end_cycle("c-x")  # should not crash

    def test_normal_path(self) -> None:
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1C
        w._cycle_tracker = MagicMock()
        w._maybe_end_cycle("c-x")
        w._cycle_tracker.end_cycle.assert_called_once_with("layer1c", "c-x")

    def test_end_cycle_exception_swallowed(self) -> None:
        w = _StubWorker()
        w.worker_tier = WorkerTier.LAYER1B
        w._cycle_tracker = MagicMock()
        w._cycle_tracker.end_cycle.side_effect = RuntimeError("boom")
        # Must not raise — recorder bug ≠ production failure.
        w._maybe_end_cycle("c-x")
