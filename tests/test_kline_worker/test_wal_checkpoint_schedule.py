"""Phase 1 (D-3 fix) — WAL checkpoint scheduler tests for KlineWorker.

We don't drive a full kline_worker.tick() in these tests (Bybit + market_service
make that an integration concern). Instead we exercise the
``_maybe_run_wal_checkpoint`` helper directly with a mocked DatabaseManager
to verify cadence, mode escalation, and observability semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


pytestmark = pytest.mark.asyncio


# ----- minimal stand-ins so the test stays surgical and fast ---------------


@dataclass
class _FakeDatabaseSettings:
    path: str = "data/trading.db"
    wal_checkpoint_every_n_kline_ticks: int = 5
    wal_checkpoint_truncate_after_busy_count: int = 3


@dataclass
class _FakeSettings:
    database: _FakeDatabaseSettings = field(default_factory=_FakeDatabaseSettings)


class _FakeDB:
    """Captures every checkpoint() call so the test asserts on them."""

    def __init__(self, busy_results: list[int] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        # busy_results queue lets the test script multi-tick scenarios
        # (e.g. four consecutive busy=1 results to trigger escalation).
        self._busy_results = list(busy_results or [])

    async def checkpoint(self, mode: str = "PASSIVE") -> dict[str, int]:
        busy = self._busy_results.pop(0) if self._busy_results else 0
        result = {
            "busy": busy,
            "log_pages": 1234,
            "ckpt_pages": 1230,
            "mode": mode,
        }
        self.calls.append({"mode": mode, "result": result})
        return result


class _CheckpointHarness:
    """Strips KlineWorker down to the slice _maybe_run_wal_checkpoint touches.

    The full KlineWorker requires a Bybit client + a real DatabaseManager
    + the SweetSpotWorker scheduler to instantiate; none of those are
    relevant to the unit under test. This harness mirrors the attributes
    the helper reads (settings, db, _tick_count, _consecutive_busy_
    checkpoints) and binds the helper as an instance method so the
    tests exercise the production code path verbatim.
    """

    def __init__(
        self,
        cadence: int = 5,
        truncate_after: int = 3,
        busy_results: list[int] | None = None,
    ) -> None:
        self.settings = _FakeSettings(
            database=_FakeDatabaseSettings(
                wal_checkpoint_every_n_kline_ticks=cadence,
                wal_checkpoint_truncate_after_busy_count=truncate_after,
            ),
        )
        self.db = _FakeDB(busy_results=busy_results)
        self._tick_count = 0
        self._consecutive_busy_checkpoints = 0

    async def simulate_tick(self) -> None:
        from src.workers.kline_worker import KlineWorker

        self._tick_count += 1
        await KlineWorker._maybe_run_wal_checkpoint(self)


# ----- tests ----------------------------------------------------------------


class TestWalCheckpointCadence:
    async def test_no_checkpoint_until_cadence_reached(self):
        h = _CheckpointHarness(cadence=5)
        for _ in range(4):
            await h.simulate_tick()
        assert h.db.calls == []

    async def test_checkpoint_fires_on_cadence(self):
        h = _CheckpointHarness(cadence=5)
        for _ in range(5):
            await h.simulate_tick()
        assert len(h.db.calls) == 1
        assert h.db.calls[0]["mode"] == "PASSIVE"

    async def test_checkpoint_repeats_at_each_cadence_multiple(self):
        h = _CheckpointHarness(cadence=3)
        for _ in range(9):  # ticks 3, 6, 9 fire
            await h.simulate_tick()
        assert len(h.db.calls) == 3
        assert all(c["mode"] == "PASSIVE" for c in h.db.calls)


class TestWalCheckpointEscalation:
    async def test_busy_count_resets_on_clean_checkpoint(self):
        # Two busy results then one clean — counter must reset.
        h = _CheckpointHarness(
            cadence=1, truncate_after=3, busy_results=[1, 1, 0],
        )
        for _ in range(3):
            await h.simulate_tick()
        # All three ticks PASSIVE; no escalation; counter back to zero.
        assert [c["mode"] for c in h.db.calls] == ["PASSIVE", "PASSIVE", "PASSIVE"]
        assert h._consecutive_busy_checkpoints == 0

    async def test_escalates_to_truncate_after_threshold_busy(self):
        # 3 busy in a row → 4th call is TRUNCATE.
        h = _CheckpointHarness(
            cadence=1, truncate_after=3, busy_results=[1, 1, 1, 0],
        )
        for _ in range(4):
            await h.simulate_tick()
        modes = [c["mode"] for c in h.db.calls]
        assert modes == ["PASSIVE", "PASSIVE", "PASSIVE", "TRUNCATE"]

    async def test_truncate_clears_consecutive_busy_when_clean(self):
        # 3 busy → escalate to TRUNCATE; the TRUNCATE returns clean → counter resets.
        h = _CheckpointHarness(
            cadence=1, truncate_after=3, busy_results=[1, 1, 1, 0, 0],
        )
        for _ in range(5):
            await h.simulate_tick()
        modes = [c["mode"] for c in h.db.calls]
        assert modes == ["PASSIVE", "PASSIVE", "PASSIVE", "TRUNCATE", "PASSIVE"]
        assert h._consecutive_busy_checkpoints == 0


class TestWalCheckpointSafety:
    async def test_checkpoint_failure_does_not_raise(self):
        h = _CheckpointHarness(cadence=1)

        async def boom(mode="PASSIVE"):
            raise RuntimeError("simulated checkpoint failure")

        h.db.checkpoint = boom  # type: ignore[assignment]
        # Must not raise — kline_worker tick must survive checkpoint errors.
        await h.simulate_tick()
        # No state mutation either.
        assert h._consecutive_busy_checkpoints == 0

    async def test_invalid_cadence_zero_skips_checkpoint(self):
        h = _CheckpointHarness(cadence=0)
        for _ in range(20):
            await h.simulate_tick()
        assert h.db.calls == []
