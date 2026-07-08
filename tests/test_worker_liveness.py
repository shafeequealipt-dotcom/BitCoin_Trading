"""Phase 11 (dead-workers fix) — WorkerLivenessTracker tests.

The tracker is the lower half of the dead-worker observability fix; it
records registration, ticks, and sweet-spot fires per worker, and
classifies each worker's status with cycle-gate awareness so that the
upcoming WorkerLivenessWatchdog and the /health Telegram command can
distinguish "really hung" from "intentionally idle because L3 is OFF".

Cycle-gate awareness is the most important property — false positives
during normal L3=OFF operation (every time the operator stops trading
and leaves data running) would cause alarm fatigue. The pre-existing
``LAYER1B_TICK_SKIP`` log at DEBUG level was specifically chosen to
avoid this kind of noise; the watchdog inherits that constraint and
the tracker enforces it via ``is_alive(cycle_active=...)``.
"""

from __future__ import annotations

import time

import pytest

from src.core import worker_liveness as wl_mod
from src.core.worker_liveness import (
    STATUS_HEALTHY,
    STATUS_IDLE_CYCLE_GATE,
    STATUS_NEVER_TICKED,
    STATUS_NO_DATA,
    STATUS_OVERDUE,
    WorkerLivenessTracker,
)


@pytest.fixture
def tracker() -> WorkerLivenessTracker:
    return WorkerLivenessTracker()


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Clear the module-level singleton between tests."""
    wl_mod.set_default_tracker(None)
    yield
    wl_mod.set_default_tracker(None)


def test_register_creates_record(tracker: WorkerLivenessTracker) -> None:
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    snap = tracker.snapshot()
    assert len(snap) == 1
    h = snap[0]
    assert h.name == "structure_worker"
    assert h.cycle_gated is True
    assert h.tier == "LAYER1B"
    assert h.expected_interval_s == 300.0
    assert h.first_tick_ts is None
    assert h.last_tick_ts is None
    assert h.tick_count == 0
    assert h.sweet_spot_fires == 0


def test_record_tick_sets_first_and_last(tracker: WorkerLivenessTracker) -> None:
    tracker.register(
        "price_worker",
        expected_interval_s=45.0,
        cycle_gated=False,
        tier="LAYER1A",
    )
    tracker.record_tick("price_worker")
    snap = tracker.snapshot()[0]
    assert snap.first_tick_ts is not None
    assert snap.last_tick_ts is not None
    # First and last are equal on the first tick.
    assert snap.first_tick_ts == snap.last_tick_ts
    assert snap.tick_count == 1

    # Second tick advances last but not first.
    first_ts = snap.first_tick_ts
    time.sleep(0.005)
    tracker.record_tick("price_worker")
    snap2 = tracker.snapshot()[0]
    assert snap2.first_tick_ts == first_ts
    assert snap2.last_tick_ts > first_ts
    assert snap2.tick_count == 2


def test_record_tick_unknown_worker_is_noop(tracker: WorkerLivenessTracker) -> None:
    """Unregistered worker should not raise; just ignored."""
    tracker.record_tick("ghost_worker")  # must not raise
    assert tracker.snapshot() == []


def test_record_sweet_spot_increments_counter(tracker: WorkerLivenessTracker) -> None:
    tracker.register(
        "kline_worker",
        expected_interval_s=300.0,
        cycle_gated=False,
        tier="LAYER1A",
    )
    tracker.record_sweet_spot("kline_worker")
    tracker.record_sweet_spot("kline_worker")
    tracker.record_sweet_spot("kline_worker")
    assert tracker.snapshot()[0].sweet_spot_fires == 3


def test_is_alive_within_grace_no_tick_yet(tracker: WorkerLivenessTracker) -> None:
    """Just-registered worker hasn't ticked but is within the grace window."""
    tracker.register(
        "fresh_worker",
        expected_interval_s=60.0,
        cycle_gated=False,
        tier=None,
    )
    alive, status = tracker.is_alive(
        "fresh_worker", cycle_active=True, grace_s=90.0,
    )
    assert alive is True
    assert status == STATUS_HEALTHY


def test_is_alive_never_ticked_beyond_grace(tracker: WorkerLivenessTracker) -> None:
    """Force the wm_start_ts back so elapsed > grace, no tick recorded."""
    tracker.register(
        "stuck_worker",
        expected_interval_s=60.0,
        cycle_gated=False,
        tier="LAYER1A",
    )
    # Backdate the start so the elapsed_since_start_s > grace_s.
    tracker._workers["stuck_worker"]["wm_start_ts"] = time.time() - 200.0
    alive, status = tracker.is_alive(
        "stuck_worker", cycle_active=True, grace_s=90.0,
    )
    assert alive is False
    assert status == STATUS_NEVER_TICKED


def test_cycle_gated_worker_idle_when_cycle_inactive(
    tracker: WorkerLivenessTracker,
) -> None:
    """A cycle_gated worker that never ticked is INTENTIONALLY idle when L3=OFF.

    This is the false-positive prevention check — the 7-hour silent
    skip observed on 2026-04-27 must not produce a NEVER_TICKED alarm.
    """
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 7 * 3600.0
    alive, status = tracker.is_alive(
        "structure_worker", cycle_active=False, grace_s=90.0,
    )
    # Alive (intentionally silent), status surfaces the gate reason.
    assert alive is True
    assert status == STATUS_IDLE_CYCLE_GATE


def test_cycle_gated_worker_never_ticked_when_cycle_active(
    tracker: WorkerLivenessTracker,
) -> None:
    """When cycle IS active and a cycle_gated worker still hasn't ticked
    beyond the EFFECTIVE grace window (expected_interval_s + grace_s
    for sweet-spot workers), that's a real hang — alarm.
    """
    tracker.register(
        "regime_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    # Backdate beyond the effective grace (300 + 90 = 390 s). 500 s is
    # comfortably past so the alarm definitely fires.
    tracker._workers["regime_worker"]["wm_start_ts"] = time.time() - 500.0
    alive, status = tracker.is_alive(
        "regime_worker", cycle_active=True, grace_s=90.0,
    )
    assert alive is False
    assert status == STATUS_NEVER_TICKED


def test_sweet_spot_worker_in_boot_window_is_healthy(
    tracker: WorkerLivenessTracker,
) -> None:
    """SweetSpotWorker (expected=300) waiting for its first sweet-spot
    must NOT be flagged as never_ticked at elapsed=200 s.

    Without the effective-grace adjustment, the watchdog would alarm
    on every healthy SweetSpotWorker between 90 s and ~300 s post-boot
    while they wait for their first sweet-spot fire. The 06:18 reference
    run had structure first-tick at 158 s, kline at 163 s, scanner at
    352 s — all healthy but vulnerable to the false positive.
    """
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=False,  # cycle_gated tested separately above
        tier="LAYER1B",
    )
    # 200s post-boot — past the user grace (90) but inside the effective
    # grace (300 + 90 = 390).
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 200.0
    alive, status = tracker.is_alive(
        "structure_worker", cycle_active=True, grace_s=90.0,
    )
    assert alive is True
    assert status == STATUS_HEALTHY


def test_sweet_spot_worker_alarms_after_effective_grace(
    tracker: WorkerLivenessTracker,
) -> None:
    """Once elapsed > expected + grace and still no first tick, alarm.

    Confirms the effective-grace widening doesn't prevent the alarm
    indefinitely — it just shifts it past the boot warmup.
    """
    tracker.register(
        "scanner_worker",
        expected_interval_s=300.0,
        cycle_gated=False,
        tier="LAYER1D",
    )
    tracker._workers["scanner_worker"]["wm_start_ts"] = time.time() - 500.0
    alive, status = tracker.is_alive(
        "scanner_worker", cycle_active=True, grace_s=90.0,
    )
    assert alive is False
    assert status == STATUS_NEVER_TICKED


def test_overdue_after_first_tick_for_non_gated(
    tracker: WorkerLivenessTracker,
) -> None:
    """Healthy non-gated worker that stopped ticking → OVERDUE."""
    tracker.register(
        "altdata_worker",
        expected_interval_s=300.0,
        cycle_gated=False,
        tier="LAYER1A",
    )
    tracker.record_tick("altdata_worker")
    # Stale by 700s — > 2 × 300s threshold.
    tracker._workers["altdata_worker"]["last_tick_ts"] = time.time() - 700.0
    alive, status = tracker.is_alive(
        "altdata_worker",
        cycle_active=True,
        grace_s=90.0,
        overdue_mult=2.0,
    )
    assert alive is False
    assert status == STATUS_OVERDUE


def test_overdue_suppressed_for_cycle_gated_when_cycle_off(
    tracker: WorkerLivenessTracker,
) -> None:
    """Even after a successful tick, a cycle_gated worker that goes
    quiet because L3 was toggled OFF must not report OVERDUE.
    """
    tracker.register(
        "strategy_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1C",
    )
    tracker.record_tick("strategy_worker")
    tracker._workers["strategy_worker"]["last_tick_ts"] = time.time() - 700.0
    alive, status = tracker.is_alive(
        "strategy_worker",
        cycle_active=False,
        grace_s=90.0,
        overdue_mult=2.0,
    )
    assert alive is True
    assert status == STATUS_IDLE_CYCLE_GATE


def test_no_data_for_unregistered(tracker: WorkerLivenessTracker) -> None:
    alive, status = tracker.is_alive("ghost", cycle_active=True)
    assert alive is False
    assert status == STATUS_NO_DATA


def test_snapshot_with_cycle_classifies_consistently(
    tracker: WorkerLivenessTracker,
) -> None:
    """snapshot_with_cycle should produce status fields matching is_alive
    for the same cycle_active argument.
    """
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    # Backdate beyond effective grace (300 + 90 = 390 s).
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 500.0

    snap_off = tracker.snapshot_with_cycle(cycle_active=False)
    assert snap_off[0].status == STATUS_IDLE_CYCLE_GATE

    snap_on = tracker.snapshot_with_cycle(cycle_active=True)
    assert snap_on[0].status == STATUS_NEVER_TICKED


def test_default_tracker_singleton() -> None:
    a = wl_mod.get_default_tracker()
    b = wl_mod.get_default_tracker()
    assert a is b
    # set_default_tracker(None) replaces (covered by fixture cleanup).
    wl_mod.set_default_tracker(None)
    c = wl_mod.get_default_tracker()
    assert c is not a


def test_deregister_removes_record(tracker: WorkerLivenessTracker) -> None:
    tracker.register(
        "worker_a",
        expected_interval_s=60.0,
        cycle_gated=False,
        tier=None,
    )
    assert len(tracker.snapshot()) == 1
    tracker.deregister("worker_a")
    assert tracker.snapshot() == []
    # Idempotent.
    tracker.deregister("worker_a")
