"""Phase 11 (dead-workers fix) — WorkerLivenessWatchdog tests.

Verifies the watchdog correctly classifies registered workers, emits
WORKER_NEVER_TICKED / WORKER_TICK_OVERDUE warnings, and rate-limits
Telegram alerts so a stuck worker doesn't flood the operator. Cycle-
gate handling is the most important property — false positives during
normal L3=OFF operation must not fire.

The watchdog is a BaseWorker subclass; we exercise its ``tick()``
method directly without booting the full asyncio run loop. BaseWorker
plumbing (settings.workers.max_consecutive_failures etc.) is stubbed
via ``__new__`` + manual attribute seeding.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core import worker_liveness as wl_mod
from src.core.worker_liveness import WorkerLivenessTracker
from src.workers.worker_liveness_watchdog import WorkerLivenessWatchdog

pytestmark = pytest.mark.asyncio


def _make_watchdog(
    tracker: WorkerLivenessTracker,
    *,
    grace_s: float = 90.0,
    overdue_mult: float = 2.0,
    rate_limit_s: float = 3600.0,
    alert_manager: Any = None,
    layer_manager: Any = None,
) -> WorkerLivenessWatchdog:
    """Build a watchdog without invoking BaseWorker.__init__ machinery.

    BaseWorker.__init__ calls into Settings for max_consecutive_failures
    and similar; bypassing it lets us exercise tick() without a full
    Settings instance.
    """
    w = WorkerLivenessWatchdog.__new__(WorkerLivenessWatchdog)
    w.name = "worker_liveness_watchdog"
    w.interval = 30.0
    w.settings = MagicMock()
    w.db = MagicMock()
    w.status = MagicMock()
    w.running = False
    w.restart_count = 0
    w.max_restarts = 5
    w.restart_delay = 10.0
    w.last_tick_time = None
    w.last_error = None
    w.total_ticks = 0
    w.error_count = 0
    w._layer_manager = layer_manager
    w._cycle_tracker = None
    w._heartbeat_interval = 300
    w._last_heartbeat = 0.0
    w._start_time = None
    w._first_tick_logged = False
    w._tick_slow_threshold_s = 60.0

    # Watchdog-specific attributes.
    w._tracker = tracker
    w._grace_s = grace_s
    w._overdue_mult = overdue_mult
    w._alert_rate_limit_s = rate_limit_s
    w._alert_manager = alert_manager
    w._last_alert_ts = {}
    return w


@pytest.fixture(autouse=True)
def _reset_tracker_singleton() -> None:
    wl_mod.set_default_tracker(None)
    yield
    wl_mod.set_default_tracker(None)


async def test_heartbeat_fires_with_zero_workers(monkeypatch) -> None:
    """Empty tracker → heartbeat with all zero counts."""
    tracker = WorkerLivenessTracker()
    w = _make_watchdog(tracker)

    captured: list[str] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    info_msgs = [m for level, m in captured if level == "info"]
    assert any("WORKER_LIVENESS_HEARTBEAT" in m for m in info_msgs)
    assert any("total=0" in m for m in info_msgs)
    # No warnings on an empty tracker.
    warnings = [m for level, m in captured if level == "warning"]
    assert warnings == []


async def test_never_ticked_emits_warning_when_cycle_active(
    monkeypatch,
) -> None:
    """Genuine hang: cycle ON, worker never ticked beyond effective grace → warning."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    # Backdate beyond the effective grace (300 + 90 = 390 s). Use 500 s.
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 500.0

    lm = MagicMock()
    lm.is_cycle_active = MagicMock(return_value=True)
    w = _make_watchdog(tracker, layer_manager=lm)

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    warnings = [m for level, m in captured if level == "warning"]
    assert any(
        "WORKER_NEVER_TICKED" in m and "structure_worker" in m
        for m in warnings
    ), f"expected NEVER_TICKED warning, got: {warnings}"
    info = [m for level, m in captured if level == "info"]
    assert any("never_ticked=1" in m for m in info)


async def test_cycle_gated_silent_when_cycle_off(monkeypatch) -> None:
    """Same backdated worker but cycle=OFF → no warning, classified idle."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "signal_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    tracker._workers["signal_worker"]["wm_start_ts"] = time.time() - 7 * 3600.0

    lm = MagicMock()
    lm.is_cycle_active = MagicMock(return_value=False)
    w = _make_watchdog(tracker, layer_manager=lm)

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    warnings = [m for level, m in captured if level == "warning"]
    assert warnings == [], (
        "Cycle-gated worker with cycle OFF must NOT trigger a warning; "
        f"got: {warnings}"
    )
    info = [m for level, m in captured if level == "info"]
    assert any("idle_cycle_gate=1" in m for m in info)


async def test_overdue_emits_warning(monkeypatch) -> None:
    """Worker that ticked once then went silent past 2x interval."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "altdata_worker",
        expected_interval_s=300.0,
        cycle_gated=False,
        tier="LAYER1A",
    )
    tracker.record_tick("altdata_worker")
    tracker._workers["altdata_worker"]["last_tick_ts"] = time.time() - 700.0

    w = _make_watchdog(tracker)

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    warnings = [m for level, m in captured if level == "warning"]
    assert any(
        "WORKER_TICK_OVERDUE" in m and "altdata_worker" in m
        for m in warnings
    ), f"expected OVERDUE warning, got: {warnings}"


async def test_alert_rate_limit_suppresses_repeats(monkeypatch) -> None:
    """Two ticks back-to-back must produce only ONE Telegram alert."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 500.0

    lm = MagicMock()
    lm.is_cycle_active = MagicMock(return_value=True)

    alert_mgr = MagicMock()
    alert_mgr.send_error_alert = AsyncMock()

    w = _make_watchdog(
        tracker,
        rate_limit_s=3600.0,
        alert_manager=alert_mgr,
        layer_manager=lm,
    )

    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(info=MagicMock(), warning=MagicMock()),
    )

    await w.tick()
    await w.tick()
    await w.tick()

    # Allow scheduled alert tasks to run to completion.
    import asyncio
    pending = [
        t for t in asyncio.all_tasks()
        if t.get_name().startswith("worker_liveness_alert_")
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # send_error_alert must have been called exactly once despite 3 ticks.
    assert alert_mgr.send_error_alert.await_count == 1, (
        f"expected exactly 1 alert in rate-limit window, "
        f"got {alert_mgr.send_error_alert.await_count}"
    )


async def test_alert_rate_limit_allows_after_window(monkeypatch) -> None:
    """After rate_limit_s elapses, the next alert fires."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "regime_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    tracker._workers["regime_worker"]["wm_start_ts"] = time.time() - 500.0

    lm = MagicMock()
    lm.is_cycle_active = MagicMock(return_value=True)

    alert_mgr = MagicMock()
    alert_mgr.send_error_alert = AsyncMock()

    w = _make_watchdog(
        tracker,
        rate_limit_s=0.001,  # near-zero so the second alert always fires
        alert_manager=alert_mgr,
        layer_manager=lm,
    )

    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(info=MagicMock(), warning=MagicMock()),
    )

    await w.tick()
    time.sleep(0.005)
    await w.tick()

    import asyncio
    pending = [
        t for t in asyncio.all_tasks()
        if t.get_name().startswith("worker_liveness_alert_")
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    assert alert_mgr.send_error_alert.await_count == 2


async def test_no_alert_manager_log_only(monkeypatch) -> None:
    """When alert_manager is None, watchdog still logs but does not crash."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "stuck",
        expected_interval_s=60.0,
        cycle_gated=False,
        tier="LAYER1A",
    )
    tracker._workers["stuck"]["wm_start_ts"] = time.time() - 200.0

    w = _make_watchdog(tracker, alert_manager=None)
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    warnings = [m for level, m in captured if level == "warning"]
    assert any("WORKER_NEVER_TICKED" in m for m in warnings)


async def test_no_layer_manager_defaults_cycle_active_true(
    monkeypatch,
) -> None:
    """LM not yet wired → watchdog defaults cycle_active=True (alarms fire).

    Conservative default — a wiring oversight is itself a real bug,
    so we want loud alarms, not silent muting.
    """
    tracker = WorkerLivenessTracker()
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 500.0

    w = _make_watchdog(tracker, layer_manager=None)
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    warnings = [m for level, m in captured if level == "warning"]
    assert any(
        "WORKER_NEVER_TICKED" in m and "cycle_active=True" in m
        for m in warnings
    )


async def test_lm_probe_exception_defaults_cycle_active_true(
    monkeypatch,
) -> None:
    """If is_cycle_active() raises, watchdog still operates with default."""
    tracker = WorkerLivenessTracker()
    tracker.register(
        "structure_worker",
        expected_interval_s=300.0,
        cycle_gated=True,
        tier="LAYER1B",
    )
    tracker._workers["structure_worker"]["wm_start_ts"] = time.time() - 500.0

    lm = MagicMock()
    lm.is_cycle_active = MagicMock(side_effect=RuntimeError("oops"))

    w = _make_watchdog(tracker, layer_manager=lm)
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.workers.worker_liveness_watchdog.log",
        MagicMock(
            info=lambda m, *a, **k: captured.append(("info", str(m))),
            warning=lambda m, *a, **k: captured.append(("warning", str(m))),
        ),
    )

    await w.tick()

    # Conservative default → cycle_active=True → alarm fires for the
    # cycle-gated worker that hasn't ticked.
    warnings = [m for level, m in captured if level == "warning"]
    assert any("WORKER_NEVER_TICKED" in m for m in warnings)
