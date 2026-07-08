"""Tests for src/core/cycle_tracker.py — Layer 1 restructure Phase 1."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.cycle_tracker import CycleSummary, CycleTracker


class TestCycleId:
    def test_make_cycle_id_minute_aligned(self) -> None:
        # 21:33 → floor to 21:30 (5-min boundary)
        t = datetime(2026, 4, 27, 21, 33, 12, tzinfo=timezone.utc)
        assert CycleTracker.make_cycle_id(t) == "c-2026-04-27-21:30"

    def test_make_cycle_id_exact_boundary(self) -> None:
        t = datetime(2026, 4, 27, 21, 30, 0, tzinfo=timezone.utc)
        assert CycleTracker.make_cycle_id(t) == "c-2026-04-27-21:30"


class TestStartEnd:
    @pytest.fixture
    def tracker(self) -> CycleTracker:
        return CycleTracker(db=AsyncMock(), max_history=10)

    def test_start_cycle_unknown_layer_raises(self, tracker: CycleTracker) -> None:
        with pytest.raises(ValueError, match="unknown layer"):
            tracker.start_cycle("not_a_layer")

    def test_end_without_start_returns_zero(self, tracker: CycleTracker) -> None:
        elapsed = tracker.end_cycle("layer1b", "c-2026-04-27-21:30")
        assert elapsed == 0  # missing start — emits NO_START debug, returns 0

    def test_full_cycle_completes_with_history(self, tracker: CycleTracker) -> None:
        cid = tracker.start_cycle("layer1b")
        assert cid.startswith("c-")
        tracker.end_cycle("layer1b", cid)
        tracker.start_cycle("layer1c", cycle_id=cid)
        tracker.end_cycle("layer1c", cid)
        tracker.start_cycle("layer1d", cycle_id=cid)
        tracker.end_cycle("layer1d", cid)
        # 1D end is the cycle terminator — _emit_complete fires.
        recent = tracker.get_recent(5)
        assert len(recent) == 1
        assert recent[0].cycle_id == cid
        assert recent[0].status == "ok"

    def test_record_qualified_stamps_summary(self, tracker: CycleTracker) -> None:
        cid = "c-2026-04-27-21:30"
        tracker.record_qualified(cid, qualified=14, selected=12, packages=12)
        tracker.start_cycle("layer1d", cycle_id=cid)
        tracker.end_cycle("layer1d", cid)
        recent = tracker.get_recent(5)
        assert recent[0].packages_ready == 12

    def test_max_history_bounded(self) -> None:
        t = CycleTracker(db=AsyncMock(), max_history=3)
        for i in range(5):
            cid = f"c-2026-04-27-{i:02d}:30"
            t.start_cycle("layer1d", cycle_id=cid)
            t.end_cycle("layer1d", cid)
        recent = t.get_recent(10)
        assert len(recent) == 3  # deque cap respected
        assert recent[-1].cycle_id == "c-2026-04-27-04:30"


class TestSummary:
    def test_total_ms_sums_recorded(self) -> None:
        s = CycleSummary(cycle_id="c", layer1a_ms=100, layer1b_ms=200, layer1c_ms=300, layer1d_ms=50)
        assert s.total_ms == 650

    def test_total_ms_skips_none(self) -> None:
        s = CycleSummary(cycle_id="c", layer1b_ms=200, layer1d_ms=50)
        assert s.total_ms == 250
