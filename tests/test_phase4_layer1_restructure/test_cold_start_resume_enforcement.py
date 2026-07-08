"""Layer 1 restructure Phase 4 (gap fix) — cold-start boundary enforcement.

Blueprint HR-8 mandates: "Cycle waits for next 5-min boundary on cold
start." The original Phase-4 implementation only ANNOUNCED the wait via
CYCLE_RESUME_WAIT/CYCLE_RESUME log markers. Live observation on
2026-04-29 caught the consequence: at boot t=02:51:56 (window-position
116s within the 02:50–02:55 window), ``scanner_worker`` (sweet-spot
4:00=240s) was the only worker whose offset still lay ahead of ``now``,
so it fired alone at 02:54:00 with an empty XRAY cache and produced
``fail_no_xray=50`` — restricting Stage 2 selection to forced
(protected) positions for one full cycle.

These tests exercise the enforcement state added on top of the
announcement: ``LayerManager._cold_start_resume_done`` flips False the
moment a boundary wait is scheduled and back to True the moment the
wait completes (CYCLE_RESUME log line). The gate in
``BaseWorker.start`` / ``SweetSpotWorker.start`` skips every
``cycle_gated`` tick while the flag is False.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.layer_manager import LayerManager


# ──────────────────────────────────────────────────────────────────────
# LayerManager state contract
# ──────────────────────────────────────────────────────────────────────


def _build_lm() -> LayerManager:
    """Build a real LayerManager with mock settings/services.

    The constructor needs ``settings`` and ``services`` but does no IO
    that we cannot mock here — disk persistence is best-effort with
    a try/except in ``_persist_state``.
    """
    settings = MagicMock()
    settings.brain.cold_start_protection.enabled = True
    return LayerManager(settings, services={})


def test_default_flag_is_true_after_construct() -> None:
    """A freshly-constructed LM does not gate workers (fail-open)."""
    lm = _build_lm()
    assert lm._cold_start_resume_done is True
    assert lm._cold_start_resume_task is None


def test_constructed_via_new_has_no_attribute() -> None:
    """``__new__``-built LM (test fixture pattern) has no flag.

    Worker-side gates use ``getattr(..., True)`` so this case must
    fail-open. Verified separately in
    ``test_sweet_spot_gate_open_when_attribute_missing``.
    """
    lm = LayerManager.__new__(LayerManager)
    assert not hasattr(lm, "_cold_start_resume_done")


# ──────────────────────────────────────────────────────────────────────
# start_layer cold-start path
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_layer_3_schedules_wait_and_clears_flag(monkeypatch) -> None:
    """When start_layer(3) flips is_cycle_active() True, the flag goes
    False and a task is stored."""
    lm = _build_lm()
    # Layer 1 must be active for layer 3 to start; layer 2 too.
    lm._layer_active[1] = True
    lm._layer_active[2] = True

    # Force a wait (otherwise on_boundary path emits CYCLE_RESUME and
    # the flag stays True). Pin "seconds to next boundary" to a small
    # but >0 value so the test is fast.
    monkeypatch.setattr(
        LayerManager,
        "_seconds_to_next_window_boundary",
        staticmethod(lambda *, window_minutes=5, now=None: 0.05),
    )

    ok, _msg = await lm.start_layer(3)
    assert ok is True

    # After the toggle, flag must be False — workers should now skip.
    assert lm._cold_start_resume_done is False
    assert lm._cold_start_resume_task is not None
    # The task should be live (not yet awaited).
    assert not lm._cold_start_resume_task.done()

    # Now drive the wait to completion and verify the flag flips back.
    await lm._cold_start_resume_task
    assert lm._cold_start_resume_done is True
    assert lm._cold_start_resume_task is None


@pytest.mark.asyncio
async def test_re_toggle_cancels_stale_task(monkeypatch) -> None:
    """Operator rapid-fire toggle should not leak overlapping waits.

    Without the cancellation step, a re-entry into start_layer(3) would
    create a second task; both would try to flip the flag back to True
    at different boundaries, producing a non-deterministic gate.
    """
    lm = _build_lm()
    lm._layer_active[1] = True
    lm._layer_active[2] = True
    # Pick a long wait so the first task does not complete before the
    # second start_layer call cancels it.
    monkeypatch.setattr(
        LayerManager,
        "_seconds_to_next_window_boundary",
        staticmethod(lambda *, window_minutes=5, now=None: 30.0),
    )

    ok1, _ = await lm.start_layer(3)
    assert ok1
    first_task = lm._cold_start_resume_task
    assert first_task is not None and not first_task.done()

    # Simulate a second toggle path (e.g. user toggled off then on).
    # We re-fire start_layer(3) — but it requires layers 1&2 already
    # on, which they are; the test scenario is operator hitting "Start
    # Trading" twice in rapid succession.
    ok2, _ = await lm.start_layer(3)
    assert ok2
    second_task = lm._cold_start_resume_task
    assert second_task is not None
    assert second_task is not first_task
    # First task must have been cancelled.
    # Yield the loop so the cancellation propagates.
    await asyncio.sleep(0)
    assert first_task.cancelled() or first_task.done()

    # Tear down the second task cleanly.
    second_task.cancel()
    try:
        await second_task
    except (asyncio.CancelledError, BaseException):
        pass


@pytest.mark.asyncio
async def test_cancellation_does_not_restore_flag() -> None:
    """If the wait task is cancelled, the flag stays False so a new
    wait can own the lifecycle without a stale tick squeezing through.

    The contract is documented in ``_await_resume_boundary``'s
    docstring: only successful completion flips the flag back to True.
    """
    lm = _build_lm()
    lm._cold_start_resume_done = False
    task = asyncio.create_task(lm._await_resume_boundary(30.0))
    await asyncio.sleep(0)  # let the task enter the sleep
    task.cancel()
    with pytest.raises((asyncio.CancelledError, BaseException)):
        await task
    # Flag remains False per the cancellation contract.
    assert lm._cold_start_resume_done is False


# ──────────────────────────────────────────────────────────────────────
# Worker-side gate behavior
# ──────────────────────────────────────────────────────────────────────


def test_sweet_spot_gate_blocks_when_flag_false() -> None:
    """The skip predicate evaluates True (skip) when cycle_gated and
    the LM flag is False.

    Mirrors the inline check inside ``SweetSpotWorker.start``; we
    extract it into a callable here for test isolation.
    """

    def should_skip(worker, lm) -> bool:
        return bool(
            getattr(worker, "cycle_gated", False)
            and lm
            and not getattr(lm, "_cold_start_resume_done", True)
        )

    lm = MagicMock()
    lm._cold_start_resume_done = False

    worker = MagicMock()
    worker.cycle_gated = True
    assert should_skip(worker, lm) is True

    worker.cycle_gated = False
    assert should_skip(worker, lm) is False


def test_sweet_spot_gate_open_when_flag_true() -> None:
    """When the flag is True (steady state), the predicate does not
    skip — workers tick normally."""

    def should_skip(worker, lm) -> bool:
        return bool(
            getattr(worker, "cycle_gated", False)
            and lm
            and not getattr(lm, "_cold_start_resume_done", True)
        )

    lm = MagicMock()
    lm._cold_start_resume_done = True
    worker = MagicMock()
    worker.cycle_gated = True
    assert should_skip(worker, lm) is False


def test_sweet_spot_gate_open_when_attribute_missing() -> None:
    """Forward-compatibility: an LM lacking ``_cold_start_resume_done``
    must fail-open so test fixtures using ``__new__`` still tick."""

    def should_skip(worker, lm) -> bool:
        return bool(
            getattr(worker, "cycle_gated", False)
            and lm
            and not getattr(lm, "_cold_start_resume_done", True)
        )

    lm = LayerManager.__new__(LayerManager)
    assert not hasattr(lm, "_cold_start_resume_done")
    worker = MagicMock()
    worker.cycle_gated = True
    assert should_skip(worker, lm) is False
