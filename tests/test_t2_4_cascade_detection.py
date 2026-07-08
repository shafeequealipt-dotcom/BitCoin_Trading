"""T2-4 System-wide cascade pattern detection tests (2026-05-12).

Pre-fix bug (F40): a DB lock (12.6 s observed) caused sniper to go
OVERDUE, which caused watchdog poll-lag to spike 2.2x, which caused
more delays. The cascade was thought resolved by earlier fixes but
instances were still occurring. The cascade was only visible by
correlating DB_LOCK_WAIT timestamps in workers.log with downstream
sniper-overdue / poll-lag symptoms in separate log files.

Fix: when a DB lock wait crosses the cascade threshold (default
5 s — much larger than the 1 s WARN threshold), emit
CASCADE_DETECTED so operators see the trigger BEFORE the
downstream symptoms appear. Single `grep CASCADE_DETECTED` flags
the operationally significant events directly.

The fix does NOT prevent the cascade — root-cause elimination of
the slow DB write is OUT of T2-4 scope (would require moving
specific writes off the hot tick path, separate investigation).
T2-4 makes the cascade VISIBLE so operators can correlate trigger
with downstream effects in real-time and prioritize the next fix.

Tests are pure-state — they exercise the threshold logic without
requiring a real SQLite database.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _cascade_should_fire(wait_ms: float, threshold_ms: float = 5000.0) -> bool:
    """Mirror of the inline T2-4 check in
    DatabaseManager._lock_acquire (after the existing DB_LOCK_WAIT
    warn block)."""
    return wait_ms >= threshold_ms


# ── T2-4 unit tests: threshold semantics ─────────────────────────────


def test_t2_4_cascade_fires_at_exact_threshold():
    """Lock wait at exactly threshold_ms fires CASCADE_DETECTED."""
    assert _cascade_should_fire(5000.0) is True


def test_t2_4_cascade_fires_above_threshold():
    """Lock wait above threshold fires."""
    assert _cascade_should_fire(5001.0) is True
    assert _cascade_should_fire(12600.0) is True  # F40 case


def test_t2_4_cascade_does_not_fire_below_threshold():
    """Lock wait below threshold does NOT fire (DB_LOCK_WAIT warn
    still fires for the 1-5s tail; CASCADE_DETECTED is reserved for
    the operationally significant 5s+ trigger)."""
    assert _cascade_should_fire(4999.0) is False
    assert _cascade_should_fire(1000.0) is False  # at WARN threshold
    assert _cascade_should_fire(0.0) is False


def test_t2_4_threshold_separates_warn_from_cascade():
    """The cascade threshold must be strictly LARGER than the warn
    threshold so cascade events are a strict subset of warn events.
    A cascade fires DB_LOCK_WAIT first (since it crosses the warn
    threshold), then CASCADE_DETECTED."""
    from src.database.connection import (
        DB_CASCADE_THRESHOLD_MS,
        DB_LOCK_WAIT_WARN_MS,
    )
    assert DB_CASCADE_THRESHOLD_MS > DB_LOCK_WAIT_WARN_MS
    assert DB_CASCADE_THRESHOLD_MS == 5000.0
    assert DB_LOCK_WAIT_WARN_MS == 1000.0


def test_t2_4_f40_replication():
    """F40 case: 12.6 s lock wait → cascade fires."""
    # The original spec captured 12.6 s observed (12600 ms)
    assert _cascade_should_fire(12600.0) is True


def test_t2_4_threshold_constants_are_module_level():
    """Both thresholds must be module-level constants so they're
    importable for telemetry tools without instantiating a
    DatabaseManager."""
    import src.database.connection as conn
    assert hasattr(conn, "DB_LOCK_WAIT_WARN_MS")
    assert hasattr(conn, "DB_CASCADE_THRESHOLD_MS")


def test_t2_4_thresholds_are_floats():
    """Thresholds compared via >= must be floats (or ints) so the
    comparison is well-defined across Python versions."""
    from src.database.connection import (
        DB_CASCADE_THRESHOLD_MS,
        DB_LOCK_WAIT_WARN_MS,
    )
    assert isinstance(DB_LOCK_WAIT_WARN_MS, (int, float))
    assert isinstance(DB_CASCADE_THRESHOLD_MS, (int, float))


# ── T2-4 contract test: emit ordering ───────────────────────────────


def test_t2_4_cascade_implies_warn():
    """Any wait that triggers cascade ALSO triggers warn (cascade
    is a strict-subset relationship). This is the operational
    invariant: DB_LOCK_WAIT count >= CASCADE_DETECTED count."""
    from src.database.connection import (
        DB_CASCADE_THRESHOLD_MS,
        DB_LOCK_WAIT_WARN_MS,
    )
    # If cascade fires at threshold T, then warn fires at threshold W
    # where W < T. So any wait_ms that triggers cascade also
    # triggers warn — the cascade event is a strict subset of warn
    # events.
    test_waits = [5000.0, 5001.0, 12600.0, 30000.0]
    for w in test_waits:
        cascade_fires = w >= DB_CASCADE_THRESHOLD_MS
        warn_fires = w >= DB_LOCK_WAIT_WARN_MS
        if cascade_fires:
            assert warn_fires, (
                f"Invariant violated at wait_ms={w}: cascade fires but warn does not"
            )
