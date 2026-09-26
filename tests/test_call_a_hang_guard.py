"""Call-A hang guard (2026-09-26).

``strategist.create_trade_plan()`` had no timeout while the execution step
right after it does (``wait_for(_execute_new_trades, timeout=300)``). A
single stuck await inside prompt-build froze new-trade-finding for 22h
(2026-08-05) and 71.6h (2026-09-23->26) with the process otherwise healthy —
systemd's watchdog only sees whether the event loop ticks at all, never
whether one coroutine is stuck, so it never fired either time. Neither
freeze left any trace of WHERE it hung.

These tests pin ``LayerManager._call_a_bounded``: the happy path is
unaffected, a hang past the configured bound raises (so the caller's
existing except-Exception handling applies unchanged), the stuck task's
stack is captured and logged BEFORE cancellation (the one thing a bare
``asyncio.wait_for`` cannot give you — it cancels before you can look), the
inner coroutine's own finally still runs (STRAT_CALL_A_END stays paired),
and the settings knob including its <=0 kill switch.
"""

from __future__ import annotations

import asyncio

import pytest
from loguru import logger as _loguru_logger

from src.config.settings import BrainSettings
from src.core.layer_manager import LayerManager


@pytest.fixture
def loguru_sink():
    records: list[str] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(msg.record["message"]),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _lm(timeout_seconds: float = 900.0) -> LayerManager:
    lm = LayerManager.__new__(LayerManager)
    lm.services = {}
    lm.settings = type("S", (), {})()
    lm.settings.brain = BrainSettings(call_a_timeout_seconds=timeout_seconds)
    return lm


class _FakeStrategist:
    """Stands in for ClaudeStrategist with a controllable create_trade_plan."""

    def __init__(self, *, plan=None, raises: BaseException | None = None,
                 hang_forever: bool = False, hang_seconds: float | None = None,
                 mark_started: asyncio.Event | None = None):
        self._plan = plan
        self._raises = raises
        self._hang_forever = hang_forever
        self._hang_seconds = hang_seconds
        self._mark_started = mark_started
        self.finally_ran = False

    async def create_trade_plan(self):
        try:
            if self._mark_started is not None:
                self._mark_started.set()
            if self._hang_forever:
                await asyncio.Event().wait()  # never completes
            if self._hang_seconds is not None:
                await asyncio.sleep(self._hang_seconds)
            if self._raises is not None:
                raise self._raises
            return self._plan
        finally:
            # Mirrors create_trade_plan's own try/finally (STRAT_CALL_A_END) —
            # must still fire when this coroutine is cancelled from outside.
            self.finally_ran = True


# ── Happy path: unaffected by the guard ──────────────────────────────────

@pytest.mark.asyncio
async def test_fast_call_returns_normally() -> None:
    lm = _lm(timeout_seconds=5.0)
    plan = object()
    result = await lm._call_a_bounded(_FakeStrategist(plan=plan))
    assert result is plan


@pytest.mark.asyncio
async def test_exception_from_create_trade_plan_propagates_unchanged() -> None:
    """The existing except-Exception handling in _run_brain_cycle must see
    the SAME exception it always did — the guard adds a bound, not a new
    failure path."""
    lm = _lm(timeout_seconds=5.0)
    with pytest.raises(ValueError, match="boom"):
        await lm._call_a_bounded(_FakeStrategist(raises=ValueError("boom")))


@pytest.mark.asyncio
async def test_slow_but_within_bound_still_returns(loguru_sink) -> None:
    lm = _lm(timeout_seconds=1.0)
    plan = object()
    result = await lm._call_a_bounded(_FakeStrategist(plan=plan, hang_seconds=0.05))
    assert result is plan
    assert not any("BRAIN_CALL_A_HUNG" in m for m in loguru_sink)


# ── The hang path: this is the whole point ───────────────────────────────

@pytest.mark.asyncio
async def test_hang_past_bound_raises_timeout_error() -> None:
    lm = _lm(timeout_seconds=0.05)
    with pytest.raises(TimeoutError, match="exceeded"):
        await lm._call_a_bounded(_FakeStrategist(hang_forever=True))


@pytest.mark.asyncio
async def test_hang_logs_stack_before_cancelling(loguru_sink) -> None:
    """The stuck coroutine's frame must be visible in the log line — this is
    the exact information neither prior 22h/71.6h freeze left behind."""
    lm = _lm(timeout_seconds=0.05)
    strat = _FakeStrategist(hang_forever=True)
    with pytest.raises(TimeoutError):
        await lm._call_a_bounded(strat)
    hung_lines = [m for m in loguru_sink if "BRAIN_CALL_A_HUNG" in m]
    assert len(hung_lines) == 1
    assert "create_trade_plan" in hung_lines[0]
    # The captured frame shows create_trade_plan suspended in this test's own
    # fake await, not an empty/failed capture.
    assert "asyncio.Event().wait()" in hung_lines[0] or "await" in hung_lines[0]


@pytest.mark.asyncio
async def test_hung_coroutines_own_finally_still_runs() -> None:
    """create_trade_plan's finally block (STRAT_CALL_A_END) must still fire
    on cancellation — the G1 try/finally-pairing contract that section
    documents must hold even when the hang guard is what triggers it."""
    lm = _lm(timeout_seconds=0.05)
    strat = _FakeStrategist(hang_forever=True)
    with pytest.raises(TimeoutError):
        await lm._call_a_bounded(strat)
    assert strat.finally_ran is True


@pytest.mark.asyncio
async def test_task_is_actually_cancelled_not_leaked() -> None:
    lm = _lm(timeout_seconds=0.05)
    started = asyncio.Event()
    strat = _FakeStrategist(hang_forever=True, mark_started=started)
    with pytest.raises(TimeoutError):
        await lm._call_a_bounded(strat)
    await started.wait()  # sanity: the fake coroutine did actually start
    await asyncio.sleep(0)  # let any leaked task surface
    pending = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
    assert pending == []


@pytest.mark.asyncio
async def test_a_second_error_while_draining_is_logged_not_raised(loguru_sink) -> None:
    """If awaiting the cancelled task raises something OTHER than
    CancelledError (a bug in the strategist's own cleanup), that must not
    mask the TimeoutError the caller is expecting — it's logged instead."""

    class _BadCleanupStrategist:
        async def create_trade_plan(self):
            try:
                await asyncio.Event().wait()
            finally:
                raise RuntimeError("cleanup exploded")

    lm = _lm(timeout_seconds=0.05)
    with pytest.raises(TimeoutError):
        await lm._call_a_bounded(_BadCleanupStrategist())
    assert any("BRAIN_CALL_A_HUNG_DRAIN_ERR" in m for m in loguru_sink)


# ── Settings: default, config-driven, and the kill switch ────────────────

def test_default_is_900_seconds() -> None:
    assert BrainSettings().call_a_timeout_seconds == 900.0


def test_loader_reads_configured_value() -> None:
    from src.config.settings import _build_brain

    s = _build_brain({"call_a_timeout_seconds": 120})
    assert s.call_a_timeout_seconds == 120.0


@pytest.mark.asyncio
async def test_zero_or_negative_disables_the_guard() -> None:
    """<= 0 must restore the exact prior behaviour: an unbounded await."""
    lm = _lm(timeout_seconds=0.0)
    plan = object()
    # hang_seconds longer than any timeout we test elsewhere would still
    # return normally, proving no bound is applied.
    result = await lm._call_a_bounded(_FakeStrategist(plan=plan, hang_seconds=0.1))
    assert result is plan


@pytest.mark.asyncio
async def test_negative_also_disables_the_guard() -> None:
    lm = _lm(timeout_seconds=-1.0)
    plan = object()
    result = await lm._call_a_bounded(_FakeStrategist(plan=plan))
    assert result is plan
