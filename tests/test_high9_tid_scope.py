"""Unit tests for HIGH-9 (cross-symbol tid bleed) fix.

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md HIGH-9.

Pre-fix: workers iterating multiple symbols within one tick (sniper M5/M7
loops, watchdog data_lake/emergency/dup loops) called set_tid in some
loops but not others. The ContextVar held the LAST symbol's tid into
the next loop, producing the audit's RENDERUSDT events tagged
tid=t-ATOMUSDT-sniper.

Fix: new tid_scope context manager in log_context.py provides token-
restore semantics so each iteration's tid is automatically restored on
exit. Applied to sniper M5/M7 loops + watchdog data_lake/emergency/dup
loops + watchdog main monitoring loop top-of-body.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.log_context import (
    get_tid,
    set_tid,
    tid_scope,
)


@pytest.fixture(autouse=True)
def _reset_tid():
    """Ensure each test starts with a clean tid context."""
    set_tid("")
    yield
    set_tid("")


# ──────────────────────────────────────────────────────────────────────
# Group 1 — basic scope semantics
# ──────────────────────────────────────────────────────────────────────


def test_tid_scope_sets_inside_block() -> None:
    """Inside the with block, get_tid returns the scoped value."""
    with tid_scope("BTCUSDT", "sniper"):
        assert get_tid() == "t-BTCUSDT-sniper"


def test_tid_scope_restores_on_exit() -> None:
    """After the with block exits, tid reverts to whatever was set
    BEFORE entering. Empty default in this case."""
    assert get_tid() == ""
    with tid_scope("BTCUSDT", "sniper"):
        assert get_tid() == "t-BTCUSDT-sniper"
    assert get_tid() == ""


def test_tid_scope_restores_on_exception() -> None:
    """Even if an exception is raised inside the block, tid is restored
    on exit (token-restore semantics via try/finally)."""
    set_tid("t-PRIOR-mon")
    with pytest.raises(ValueError):
        with tid_scope("BTCUSDT", "sniper"):
            assert get_tid() == "t-BTCUSDT-sniper"
            raise ValueError("simulated failure")
    # Restored to the PRIOR value, not empty
    assert get_tid() == "t-PRIOR-mon"


def test_tid_scope_without_role() -> None:
    """Empty role produces tid='t-{symbol}' with no suffix."""
    with tid_scope("ETHUSDT"):
        assert get_tid() == "t-ETHUSDT"


# ──────────────────────────────────────────────────────────────────────
# Group 2 — the audit's failure pattern
# ──────────────────────────────────────────────────────────────────────


def test_iteration_pattern_no_bleed_between_iterations() -> None:
    """Simulate the sniper M5/M7 loop pattern: iterate symbols, each
    iteration sets its own tid via tid_scope. After the loop completes,
    the tid is restored to whatever was set before the loop (empty here).

    Pre-fix the LAST iteration's tid would persist into the next loop's
    body — the audit's RENDERUSDT/ATOMUSDT bleed."""
    symbols = ["KATUSDT", "INJUSDT", "MANAUSDT", "RENDERUSDT", "ATOMUSDT"]
    captured_tids = []

    for sym in symbols:
        with tid_scope(sym, "sniper"):
            # Inside the iteration body, the tid is THIS symbol's
            captured_tids.append(get_tid())
        # After the with block, tid is restored to "" (the value set at
        # the top of the simulated tick)

    # Each iteration captured its own tid — no bleed
    assert captured_tids == [
        "t-KATUSDT-sniper",
        "t-INJUSDT-sniper",
        "t-MANAUSDT-sniper",
        "t-RENDERUSDT-sniper",
        "t-ATOMUSDT-sniper",
    ]
    # After the loop, no leftover tid
    assert get_tid() == ""


def test_two_consecutive_loops_no_bleed() -> None:
    """Simulate the actual sniper anti-pattern: Loop 1 (M3/M4) sets tid
    per iter; Loop 2 (M5) iterates SAME symbols but does NOT set tid.
    Pre-fix Loop 2 inherited Loop 1's last tid for ALL its iterations."""
    # Loop 1 — sets tid per iter via tid_scope
    last_loop1_tid = ""
    for sym in ["KATUSDT", "INJUSDT", "MANAUSDT"]:
        with tid_scope(sym, "sniper"):
            last_loop1_tid = get_tid()
    # After Loop 1 exits, tid restored to "" (no bleed)
    assert get_tid() == ""

    # Loop 2 — also wraps in tid_scope (post-HIGH-9 fix)
    captured = []
    for sym in ["KATUSDT", "INJUSDT", "MANAUSDT"]:
        with tid_scope(sym, "sniper"):
            captured.append(get_tid())

    # Each iter captured its OWN tid; no inheritance from Loop 1's last
    assert captured[0] == "t-KATUSDT-sniper"
    assert captured[1] == "t-INJUSDT-sniper"
    assert captured[2] == "t-MANAUSDT-sniper"


# ──────────────────────────────────────────────────────────────────────
# Group 3 — async safety
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tid_scope_propagates_across_await() -> None:
    """ContextVar values propagate through await — confirm tid_scope
    holds across an async boundary inside the block."""
    async def _async_inner() -> str:
        # Yield control to the event loop briefly
        await asyncio.sleep(0)
        return get_tid()

    with tid_scope("BTCUSDT", "sniper"):
        result = await _async_inner()
        assert result == "t-BTCUSDT-sniper"
    # Restored after exit
    assert get_tid() == ""


@pytest.mark.asyncio
async def test_concurrent_tid_scopes_are_isolated() -> None:
    """Two concurrent coroutines each in their own tid_scope must see
    only their OWN tid (ContextVar per-coroutine isolation)."""
    captured: dict[str, str] = {}

    async def _worker(symbol: str, role: str) -> None:
        with tid_scope(symbol, role):
            await asyncio.sleep(0.01)  # let other coroutine interleave
            captured[symbol] = get_tid()

    await asyncio.gather(
        _worker("BTCUSDT", "sniper"),
        _worker("ETHUSDT", "wd"),
        _worker("SOLUSDT", "ext"),
    )

    assert captured["BTCUSDT"] == "t-BTCUSDT-sniper"
    assert captured["ETHUSDT"] == "t-ETHUSDT-wd"
    assert captured["SOLUSDT"] == "t-SOLUSDT-ext"


# ──────────────────────────────────────────────────────────────────────
# Group 4 — yield value
# ──────────────────────────────────────────────────────────────────────


def test_tid_scope_yields_the_new_tid() -> None:
    """The context manager yields the new tid string for callers that
    want to capture it (rare but useful for log lines that include the
    tid explicitly)."""
    with tid_scope("BTCUSDT", "wd_emergency") as tid:
        assert tid == "t-BTCUSDT-wd_emergency"
        assert get_tid() == tid
