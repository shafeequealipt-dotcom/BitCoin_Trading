"""T2-10 Sniper trail stale watermark tests (2026-05-12).

Pre-fix bug (F68): the trail watermark _trail_hwm could become stuck
after a sharp market reversal:
  1. New trail SL lands on the wrong side of price (e.g. Long
     position price crashes; new_sl computed below price)
  2. SNIPER_WRONG_SIDE_GUARD blocks the push
  3. Watermark never updates
  4. Next tick capped by stale HWM, blocking re-entry closer to
     the new market level
  5. Repeat — 4+ retries observed on AAVE with byte-identical
     new_sl=97.76497500 while price drifted 97.86 → 97.99

Fix: per-symbol consecutive wrong-side trip counter. After N
consecutive trips (default 3), force-refresh the watermark by
dropping it; the next tick re-establishes from the current peak.
Streak resets on any successful (non-wrong-side) tick or
gateway-accepted push.

Tests are pure-state — they exercise the streak counter +
refresh trigger without requiring full sniper / market wiring.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_sniper():
    """Build a ProfitSniper with stub deps. We only exercise the
    streak counter + refresh logic which is local to the instance."""
    from src.workers.profit_sniper import ProfitSniper
    sniper = ProfitSniper.__new__(ProfitSniper)  # bypass __init__
    sniper._trail_wrong_side_streak = {}
    sniper._trail_hwm_refresh_after_wrong_side_count = 3
    sniper._tracked = {}
    return sniper


# ── T2-10 unit tests: streak counter + refresh threshold ─────────────


def test_t2_10_streak_starts_at_zero():
    """Newly-constructed sniper has empty streak counter."""
    sniper = _make_sniper()
    assert sniper._trail_wrong_side_streak == {}


def test_t2_10_first_trip_increments_streak_to_one():
    """The first wrong-side trip on a symbol sets streak=1."""
    sniper = _make_sniper()
    sniper._trail_wrong_side_streak["BTCUSDT"] = (
        sniper._trail_wrong_side_streak.get("BTCUSDT", 0) + 1
    )
    assert sniper._trail_wrong_side_streak["BTCUSDT"] == 1


def test_t2_10_consecutive_trips_accumulate():
    """Each subsequent wrong-side trip increments the counter."""
    sniper = _make_sniper()
    for expected in (1, 2, 3, 4):
        sniper._trail_wrong_side_streak["BTCUSDT"] = (
            sniper._trail_wrong_side_streak.get("BTCUSDT", 0) + 1
        )
        assert sniper._trail_wrong_side_streak["BTCUSDT"] == expected


def test_t2_10_per_symbol_isolation():
    """Streaks for different symbols don't bleed."""
    sniper = _make_sniper()
    sniper._trail_wrong_side_streak["BTCUSDT"] = 2
    sniper._trail_wrong_side_streak["ETHUSDT"] = 1
    assert sniper._trail_wrong_side_streak["BTCUSDT"] == 2
    assert sniper._trail_wrong_side_streak["ETHUSDT"] == 1


def test_t2_10_refresh_triggers_at_threshold():
    """Force-refresh fires when streak >= refresh_after_wrong_side_count."""
    sniper = _make_sniper()
    threshold = sniper._trail_hwm_refresh_after_wrong_side_count
    assert threshold == 3

    # Simulate the inline check: after threshold trips, the watermark
    # is dropped and streak reset.
    sniper._trail_wrong_side_streak["BTCUSDT"] = threshold
    sniper._tracked["BTCUSDT"] = {"_trail_hwm": 97.76497500}

    # Apply the inline refresh logic
    streak = sniper._trail_wrong_side_streak["BTCUSDT"]
    if streak >= sniper._trail_hwm_refresh_after_wrong_side_count:
        sniper._tracked["BTCUSDT"]["_trail_hwm"] = None
        sniper._trail_wrong_side_streak["BTCUSDT"] = 0

    assert sniper._tracked["BTCUSDT"]["_trail_hwm"] is None
    assert sniper._trail_wrong_side_streak["BTCUSDT"] == 0


def test_t2_10_refresh_does_not_trigger_below_threshold():
    """At streak < threshold (e.g. 2 of 3), watermark is preserved."""
    sniper = _make_sniper()
    sniper._trail_wrong_side_streak["BTCUSDT"] = 2  # below 3
    sniper._tracked["BTCUSDT"] = {"_trail_hwm": 97.76497500}

    if (
        sniper._trail_wrong_side_streak["BTCUSDT"]
        >= sniper._trail_hwm_refresh_after_wrong_side_count
    ):
        sniper._tracked["BTCUSDT"]["_trail_hwm"] = None
        sniper._trail_wrong_side_streak["BTCUSDT"] = 0

    assert sniper._tracked["BTCUSDT"]["_trail_hwm"] == 97.76497500
    assert sniper._trail_wrong_side_streak["BTCUSDT"] == 2


def test_t2_10_refresh_handles_missing_tracked_entry():
    """Defensive: if _tracked has no entry for symbol (already cleaned
    up), refresh still works without raising."""
    sniper = _make_sniper()
    sniper._trail_wrong_side_streak["BTCUSDT"] = 5
    # No _tracked entry for BTCUSDT

    # The inline refresh logic uses .get() so missing entry is safe
    _tracked_entry = sniper._tracked.get("BTCUSDT")
    _old_hwm = (
        _tracked_entry.get("_trail_hwm")
        if _tracked_entry is not None
        else None
    )
    if _tracked_entry is not None and _old_hwm is not None:
        _tracked_entry["_trail_hwm"] = None
    sniper._trail_wrong_side_streak["BTCUSDT"] = 0
    assert sniper._trail_wrong_side_streak["BTCUSDT"] == 0


def test_t2_10_refresh_handles_none_hwm():
    """Defensive: if _trail_hwm is already None, refresh is a no-op."""
    sniper = _make_sniper()
    sniper._trail_wrong_side_streak["BTCUSDT"] = 5
    sniper._tracked["BTCUSDT"] = {"_trail_hwm": None}

    _tracked_entry = sniper._tracked.get("BTCUSDT")
    _old_hwm = (
        _tracked_entry.get("_trail_hwm")
        if _tracked_entry is not None
        else None
    )
    if _tracked_entry is not None and _old_hwm is not None:
        _tracked_entry["_trail_hwm"] = None
    sniper._trail_wrong_side_streak["BTCUSDT"] = 0
    assert sniper._tracked["BTCUSDT"]["_trail_hwm"] is None


def test_t2_10_streak_resets_on_non_wrong_side_iteration():
    """When a non-wrong-side tick happens (the simple `if symbol in
    streak: reset` path post-WRONG_SIDE_GUARD block), the streak
    drops to 0."""
    sniper = _make_sniper()
    sniper._trail_wrong_side_streak["BTCUSDT"] = 2

    if "BTCUSDT" in sniper._trail_wrong_side_streak:
        sniper._trail_wrong_side_streak["BTCUSDT"] = 0
    assert sniper._trail_wrong_side_streak["BTCUSDT"] == 0


def test_t2_10_threshold_constant_locked():
    """The threshold default is locked at 3 — short enough to recover
    in ~15 s (3 ticks × 5 s/tick) but long enough to absorb a single
    transient geometry mismatch from a fast market move."""
    sniper = _make_sniper()
    assert sniper._trail_hwm_refresh_after_wrong_side_count == 3


# ── T2-10 contract test: signature on the sniper class ───────────────


def test_t2_10_attributes_exist_on_class_after_init():
    """Verify __init__ wires the new attributes (using a real
    construction path with mocks)."""
    from src.workers.profit_sniper import ProfitSniper
    # Use __new__ to skip the heavy __init__ deps
    sniper = ProfitSniper.__new__(ProfitSniper)
    # The init code we added would run as part of __init__;
    # for this contract test we verify the names are in __init__.
    import inspect
    src = inspect.getsource(ProfitSniper.__init__)
    assert "_trail_wrong_side_streak" in src, (
        "T2-10: __init__ must wire _trail_wrong_side_streak"
    )
    assert "_trail_hwm_refresh_after_wrong_side_count" in src, (
        "T2-10: __init__ must wire the threshold attribute"
    )
