"""Definitive-fix Phase 10 — M4 lifetime partial cap.

Smoke test for the per-position partial-emit budget. Once a position
has emitted ``max_partials_per_position`` partial_close actions, the
next stall escape becomes ``full_close`` instead of another partial.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config.settings import Mode4Settings
from src.workers.profit_sniper import ProfitSniper


def _make_sniper(max_partials: int = 1) -> ProfitSniper:
    sw = ProfitSniper.__new__(ProfitSniper)
    cfg = Mode4Settings()
    cfg.max_partials_per_position = max_partials
    cfg.stall_escape_partial_after_ticks = 1  # fire fast in tests
    cfg.stall_escape_full_after_ticks = 9999  # don't take the forced-full path
    cfg.stall_escape_cooldown_seconds = 0
    cfg.stall_tighten_max_applications = 9999
    # Sniper-Latency-Size Fix Phase 1 (2026-05-07) — disable the
    # tick-based grace gate so this rapid-fire test that emits multiple
    # escapes within one loop iteration continues to test the partial-
    # cap behaviour without being intercepted by the new spacing
    # requirement. The grace gate has its own tests in
    # tests/test_layer4_sniper/test_grace_gap.py.
    cfg.partial_to_partial_grace_ticks = 0
    cfg.partial_to_full_grace_ticks = 0
    sw.settings = MagicMock()
    sw.settings.mode4 = cfg
    return sw


def test_phase10_first_partial_then_full_close() -> None:
    """Capture every emission across many ticks; expect [partial, full, ...]."""
    sw = _make_sniper(max_partials=1)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    actions: list[str] = []
    for _ in range(8):
        a = sw._stall_escape_action("ETHUSDT", tracked, True, "hold")
        if a is not None:
            actions.append(a)
    # First non-None emission is partial_close (budget=1), every
    # subsequent emission is full_close.
    assert actions, "expected at least one emission"
    assert actions[0] == "partial_close"
    assert tracked["_partials_emitted"] == 1
    assert all(a == "full_close" for a in actions[1:])


def test_phase10_higher_cap_allows_more_partials() -> None:
    """When max_partials=3, three partials can fire before full_close."""
    sw = _make_sniper(max_partials=3)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.3}}

    actions = []
    for _ in range(12):
        a = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
        if a is not None:
            actions.append(a)
    # Three partials, then full_close.
    assert actions[:3] == ["partial_close", "partial_close", "partial_close"]
    assert actions[3] == "full_close"
