"""Phase 3 (post-Layer-1 fix) — trade_thesis cleanup safety filters.

The cleanup loop applies a per-table extra filter for ``trade_thesis``
that restricts deletion to ``status='closed'`` rows. This prevents a
long-running open position's journal from being pruned out from under
the active position even if its ``opened_at`` is past the 60-day
retention window.

These tests exercise the SQL contract directly rather than the full
``CleanupWorker.tick()`` (which calls VACUUM and a WAL checkpoint that
make the test wall-clock dependent for negligible coverage gain).

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_3_db_protect_blocked.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.database.connection import DatabaseManager
from src.workers.cleanup_worker import (
    _RETENTION_EXTRA_FILTERS,
    RETENTION_POLICIES,
)


def test_extra_filter_includes_trade_thesis_status_closed() -> None:
    """trade_thesis cleanup must filter on status='closed'.

    Pure metadata test — no DB needed. Verifies the safety contract is
    encoded so a future regression is caught at unit-test time.
    """
    f = _RETENTION_EXTRA_FILTERS.get("trade_thesis", "")
    assert "status" in f.lower()
    assert "closed" in f.lower()


def test_trade_thesis_in_retention_policies_with_60d() -> None:
    """trade_thesis stays in RETENTION_POLICIES with 60-day retention on opened_at."""
    matches = [p for p in RETENTION_POLICIES if p[0] == "trade_thesis"]
    assert len(matches) == 1
    table, days, ts_col = matches[0]
    assert days == 60
    assert ts_col == "opened_at"


@pytest.mark.asyncio
async def test_cleanup_query_preserves_open_theses(tmp_path) -> None:
    """The combined WHERE clause must preserve open theses past retention.

    Builds the same SQL the cleanup loop emits and runs it on a
    purpose-built test DB. Asserts row preservation — the contract that
    matters to operators.
    """
    db_path = tmp_path / "test.db"
    db = DatabaseManager(str(db_path))
    await db.connect()
    try:
        await db.execute(
            """
            CREATE TABLE trade_thesis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
            """
        )
        old_ts = (datetime.now(timezone.utc) - timedelta(days=70)).isoformat()
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        # 4 rows covering the matrix:
        # 1. CLOSED + 70d → should DELETE (eligible)
        # 2. OPEN + 70d   → should KEEP (status filter)
        # 3. CLOSED + 10d → should KEEP (TTL filter)
        # 4. OPEN + 10d   → should KEEP (both filters)
        await db.execute(
            "INSERT INTO trade_thesis (symbol, status, opened_at, closed_at) "
            "VALUES ('A', 'closed', ?, ?)",
            (old_ts, old_ts),
        )
        await db.execute(
            "INSERT INTO trade_thesis (symbol, status, opened_at, closed_at) "
            "VALUES ('B', 'open', ?, NULL)",
            (old_ts,),
        )
        await db.execute(
            "INSERT INTO trade_thesis (symbol, status, opened_at, closed_at) "
            "VALUES ('C', 'closed', ?, ?)",
            (recent_ts, recent_ts),
        )
        await db.execute(
            "INSERT INTO trade_thesis (symbol, status, opened_at, closed_at) "
            "VALUES ('D', 'open', ?, NULL)",
            (recent_ts,),
        )

        # Reproduce the cleanup loop's exact SQL.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        extra = _RETENTION_EXTRA_FILTERS["trade_thesis"]
        sql = f"DELETE FROM trade_thesis WHERE opened_at < ? AND ({extra})"
        await db.execute(sql, (cutoff,))

        rows = await db.fetch_all(
            "SELECT symbol, status FROM trade_thesis ORDER BY symbol"
        )
        kept = sorted([r["symbol"] for r in rows])
        # A is gone (closed+old). B (open+old), C (closed+recent), D (open+recent) preserved.
        assert kept == ["B", "C", "D"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_cleanup_query_no_extra_filter_for_other_tables(tmp_path) -> None:
    """Only trade_thesis has an extra filter; other tables use bare TTL."""
    # Sanity: signals doesn't get a status filter inadvertently.
    assert _RETENTION_EXTRA_FILTERS.get("signals", "") == ""
    assert _RETENTION_EXTRA_FILTERS.get("klines", "") == ""
    assert _RETENTION_EXTRA_FILTERS.get("brain_decisions", "") == ""
