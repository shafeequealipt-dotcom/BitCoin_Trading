"""Issue 3 of cascade-fix series — profit_sniper iteration race.

The sniper's main tick loop iterates ``self._tracked.items()`` and
contains multiple awaits in the body. Before the fix this was a
``dict.items()`` view, so any mutation of ``_tracked`` between awaits
would raise ``RuntimeError: dictionary changed size during iteration``.

After the fix, the loop reads ``list(self._tracked.items())`` so the
mutation can land safely without breaking iteration. Same pattern is
already used at lines ~649 and ~689 of profit_sniper.py — these tests
pin the new third site at line 327 and the defensive site in
TradeCoordinator.get_status.

The test does NOT exercise the full sniper tick (3,700 lines, many
service dependencies). Instead it simulates the exact iteration
pattern that crashed in production (a `for` loop over `items()` whose
body yields control to a coroutine that mutates the same dict) — and
asserts the snapshot pattern survives where the live view does not.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_live_view_iteration_raises_on_concurrent_mutation() -> None:
    """Reproduce the failure mode — pin the diagnosis. Without
    snapshotting, a mutation during an await raises RuntimeError."""
    tracked: dict[str, int] = {f"SYM{i}": i for i in range(20)}

    async def mutator() -> None:
        # Single-event mutation; runs once when the iterator yields.
        tracked.pop("SYM5", None)

    raised = False
    try:
        for sym, val in tracked.items():
            if sym == "SYM3":
                # Schedule the mutation; await yields to it.
                await mutator()
    except RuntimeError as e:
        assert "changed size" in str(e)
        raised = True
    assert raised, (
        "Reproducer should crash: items() view + mutation during await "
        "must raise RuntimeError"
    )


@pytest.mark.asyncio
async def test_snapshot_iteration_survives_concurrent_mutation() -> None:
    """The fix — wrap items() in list(). Mutation lands safely; the
    snapshot still iterates over the original keys."""
    tracked: dict[str, int] = {f"SYM{i}": i for i in range(20)}

    async def mutator() -> None:
        tracked.pop("SYM5", None)
        tracked.pop("SYM7", None)
        tracked["SYM_NEW"] = 999

    visited: list[str] = []
    for sym, val in list(tracked.items()):
        visited.append(sym)
        if sym == "SYM3":
            await mutator()

    # The snapshot still has all 20 original keys; visited count == 20.
    assert len(visited) == 20
    # The mutation happened; SYM5 and SYM7 are gone, SYM_NEW is added.
    assert "SYM5" not in tracked
    assert "SYM7" not in tracked
    assert tracked["SYM_NEW"] == 999


def test_profit_sniper_main_iteration_is_snapshot() -> None:
    """Source-level pin: profit_sniper.py line 327 must use list()
    around the items() call. If a refactor regresses this, the test
    fails before the bug ships."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/workers/profit_sniper.py", encoding="utf-8",
    ).read()
    # Find the "M3: Run mathematical models" comment block; the line
    # immediately after must be the snapshot iteration.
    idx = src.find("M3: Run mathematical models on each tracked position")
    assert idx >= 0, "M3 marker comment not found — file structure changed"
    snippet = src[idx:idx + 2000]
    assert "for symbol, tracked in list(self._tracked.items())" in snippet, (
        "profit_sniper.py main M3 loop is no longer snapshot-iterated. "
        "Issue 3 cascade-fix regressed; restore list() wrapper."
    )


def test_trade_coordinator_get_status_is_snapshot() -> None:
    """Source-level pin: TradeCoordinator.get_status iterates a list()
    snapshot of self._trades.items(). Defensive — protects against
    future refactors that introduce awaits inside get_status."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/core/trade_coordinator.py", encoding="utf-8",
    ).read()
    idx = src.find("def get_status(self)")
    assert idx >= 0, "get_status not found — file structure changed"
    snippet = src[idx:idx + 1500]
    assert "for symbol, state in list(self._trades.items())" in snippet, (
        "TradeCoordinator.get_status no longer snapshot-iterates. "
        "Issue 3 cascade-fix regressed; restore list() wrapper."
    )
