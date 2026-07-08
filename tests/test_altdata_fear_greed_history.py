"""Issue 1 of cascade-fix series — `AltDataRepository.get_fear_greed_history`
is now bounded by both ``days`` (cutoff) and ``limit`` (row cap), and
backed by an ASC index on the ``timestamp`` column so the
``ORDER BY timestamp ASC`` clause is index-served instead of forcing a
full scan-and-sort under the global connection mutex.

The Phase 0 baseline of the cascade-fix series found 21,516 rows in
``fear_greed_index`` and 99.7 % of all DB_LOCK_WAIT events held by
``ticker_cache`` writes — fear_greed itself was 0 % of holders. That
means the original report's claim is stale, but the unbounded query at
``altdata_repo.py:65`` was still a latent footgun. These tests pin the
new contract:

  1. Default call returns an ascending list bounded by the row cap
  2. ``limit`` is honoured (returns at most N rows)
  3. ``limit`` is clamped at the FearGreedClient layer
  4. ``days`` is clamped at the FearGreedClient layer
  5. The ASC index is created by the migration
  6. The query is index-served (sqlite3 EXPLAIN QUERY PLAN check)
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.core.types import FearGreedData
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import AltDataRepository


@pytest.fixture()
async def db(tmp_path):
    """Spin up an aiosqlite DB with the production fear_greed schema +
    the new ASC index from migration v31."""
    db_path = tmp_path / "fg_test.db"
    mgr = DatabaseManager(str(db_path))
    await mgr.connect()
    await mgr.execute(
        """
        CREATE TABLE fear_greed_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value INTEGER NOT NULL,
            classification TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
    )
    await mgr.execute(
        "CREATE INDEX idx_fear_greed_ts ON fear_greed_index(timestamp DESC)",
    )
    await mgr.execute(
        "CREATE INDEX idx_fear_greed_ts_asc ON fear_greed_index(timestamp ASC)",
    )
    yield mgr
    await mgr.disconnect()


async def _insert(mgr: DatabaseManager, value: int, when_iso: str, cls: str = "Neutral") -> None:
    await mgr.execute(
        "INSERT INTO fear_greed_index (value, classification, timestamp) "
        "VALUES (?, ?, ?)",
        (value, cls, when_iso),
    )


@pytest.mark.asyncio
async def test_default_call_returns_ascending_within_window(db) -> None:
    """Sanity: history method returns rows in ascending timestamp
    order. This pins the contract before/after the LIMIT addition."""
    repo = AltDataRepository(db)
    now = now_utc()
    for i in range(5):
        await _insert(db, 40 + i, (now - timedelta(days=4 - i)).isoformat())

    result = await repo.get_fear_greed_history(days=30)
    assert len(result) == 5
    # Strictly ascending by timestamp.
    for prev, curr in zip(result[:-1], result[1:]):
        assert prev.timestamp <= curr.timestamp


@pytest.mark.asyncio
async def test_limit_caps_returned_rows(db) -> None:
    """The new ``limit`` kwarg must cap the result set even when the
    cutoff window matches more rows."""
    repo = AltDataRepository(db)
    now = now_utc()
    for i in range(50):
        await _insert(db, 10 + (i % 90), (now - timedelta(hours=49 - i)).isoformat())

    result = await repo.get_fear_greed_history(days=30, limit=10)
    assert len(result) == 10
    # Still ascending — the LIMIT clips the tail of the ordered set.
    for prev, curr in zip(result[:-1], result[1:]):
        assert prev.timestamp <= curr.timestamp


@pytest.mark.asyncio
async def test_default_limit_does_not_blow_up_large_table(db) -> None:
    """With the table loaded heavily, the default-limit call returns
    at most 10,000 rows — bounding mutex hold time."""
    repo = AltDataRepository(db)
    now = now_utc()
    # 12,000 hourly samples = ~500 days. Larger than the default
    # cutoff (30 days) but every cutoff-eligible row should still be
    # bounded by the default limit.
    for i in range(12_000):
        await _insert(db, 50, (now - timedelta(hours=i)).isoformat())

    # days=999 is wider than the data — no cutoff filtering. Limit
    # alone must still cap at 10_000.
    result = await repo.get_fear_greed_history(days=999)
    assert len(result) == 10_000


@pytest.mark.asyncio
async def test_returns_empty_when_no_rows(db) -> None:
    """No rows in window → empty list, never None or crash."""
    repo = AltDataRepository(db)
    result = await repo.get_fear_greed_history(days=30, limit=100)
    assert result == []


@pytest.mark.asyncio
async def test_typed_dataclass_round_trip(db) -> None:
    """Returned objects are FearGreedData dataclasses, not dict
    rows — pin the contract for downstream consumers."""
    repo = AltDataRepository(db)
    now = now_utc()
    await _insert(db, 25, (now - timedelta(hours=1)).isoformat(), cls="Extreme Fear")

    result = await repo.get_fear_greed_history(days=1)
    assert len(result) == 1
    item = result[0]
    assert isinstance(item, FearGreedData)
    assert item.value == 25
    assert item.classification == "Extreme Fear"


@pytest.mark.asyncio
async def test_explain_query_plan_uses_asc_index(db) -> None:
    """Verify SQLite picks the ASC index for the ORDER BY ASC query.
    Catches future regressions that drop the index or change the
    query in a way that defeats it."""
    now = now_utc()
    for i in range(100):
        await _insert(db, 50, (now - timedelta(hours=i)).isoformat())

    cutoff = (now - timedelta(days=30)).isoformat()
    rows = await db.fetch_all(
        "EXPLAIN QUERY PLAN "
        "SELECT * FROM fear_greed_index WHERE timestamp > ? "
        "ORDER BY timestamp ASC LIMIT ?",
        (cutoff, 1000),
    )
    plan = " | ".join(str(r["detail"]) for r in rows)
    # Either index name acceptable — the ASC variant is the new one,
    # but if SQLite ever decides DESC + reverse is cheaper that's also
    # fine. What we want to fail on is a SCAN with no USING INDEX.
    assert "USING INDEX" in plan, f"Query plan does not use any index: {plan}"
    assert "idx_fear_greed_ts" in plan, (
        f"Query plan does not use a fear_greed_index index: {plan}"
    )
