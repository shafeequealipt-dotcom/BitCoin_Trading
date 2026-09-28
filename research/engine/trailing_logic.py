"""The trailing-stop ratchet math, extracted to ONE place (2026-09-28) so
research/engine/backtest_trailing.py (historical, bar-by-bar over a whole
DataFrame) and research/paper_trade/engine.py (live, one new bar at a time)
can never drift apart into two subtly-different implementations of "the
same" exit rule. Every function here is pure and was covered by
research/tests/test_backtest_trailing.py before this extraction; that suite
re-passing after the extraction is the check that nothing changed.
"""
from __future__ import annotations


def initial_stop(entry_price: float, direction: int, stop_pct: float) -> float:
    return entry_price * (1 - direction * stop_pct / 100)


def update_extreme(extreme: float, direction: int, bar_high: float, bar_low: float) -> float:
    bar_extreme = bar_high if direction > 0 else bar_low
    return max(extreme, bar_extreme) if direction > 0 else min(extreme, bar_extreme)


def ratchet_stop(
    current_stop: float, extreme: float, entry_price: float, direction: int,
    trail_activate_pct: float, trail_pct: float,
) -> tuple[float, bool]:
    """Returns (new_stop, armed). The stop only ever moves toward the
    favorable side once armed -- it never loosens back toward entry."""
    favorable_pct = (extreme - entry_price) / entry_price * 100 * direction
    if favorable_pct < trail_activate_pct:
        return current_stop, False
    candidate = extreme * (1 - direction * trail_pct / 100)
    new_stop = max(current_stop, candidate) if direction > 0 else min(current_stop, candidate)
    return new_stop, True


def check_stop_hit(stop_level: float, direction: int, bar_high: float, bar_low: float) -> bool:
    return (bar_low <= stop_level) if direction > 0 else (bar_high >= stop_level)
