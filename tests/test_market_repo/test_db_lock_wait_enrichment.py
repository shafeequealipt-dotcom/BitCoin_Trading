"""Phase 1 (D-3 fix) — minimal smoke test for DB_LOCK_WAIT enrichment.

Verifies the configurable threshold + per-caller histogram attribution.
We don't try to drive a contention scenario (timing-flaky); we just
exercise the code paths that contribute counters.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from src.database.connection import DatabaseManager


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db(tmp_path):
    path = os.path.join(tmp_path, "trading.db")
    d = DatabaseManager(path, wal_mode=True, lock_wait_warn_ms=500)
    await d.connect()
    await d.execute(
        "CREATE TABLE IF NOT EXISTS smoke (id INTEGER PRIMARY KEY, v INTEGER)"
    )
    yield d
    await d.disconnect()


async def test_threshold_overridable(db):
    """Constructor stores the override; module default is the fallback."""
    assert db._lock_wait_warn_ms == 500.0


async def test_per_caller_counters_populate_and_reset(db):
    # Drive a few operations to populate per-caller counters.
    for i in range(5):
        await db.execute("INSERT INTO smoke (v) VALUES (?)", (i,))
    await db.fetch_all("SELECT * FROM smoke")
    # Counters should now have at least one entry per op-tag prefix.
    assert sum(db._caller_wait_counts.values()) >= 6
    # log_lock_histogram emits the summary AND clears the per-caller maps.
    db.log_lock_histogram()
    assert sum(db._caller_wait_counts.values()) == 0
    assert sum(db._caller_wait_total_ms.values()) == 0
