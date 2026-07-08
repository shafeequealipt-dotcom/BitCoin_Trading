"""Tests for the PROTECTED-table runtime guard (Phase 0a).

These tests verify that destructive SQL targeting any PROTECTED table is
refused at the DatabaseManager boundary, while non-destructive SQL
(SELECT/INSERT/UPDATE) and DELETE on non-protected tables pass through.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.database.connection import DatabaseManager
from src.database.protected_tables import (
    PROTECTED_TABLES,
    ProtectedTableViolation,
    _classify,
    assert_not_protected_destructive,
    is_protected,
)


# ─── Pure-function tests (no DB needed) ──────────────────────────────


def test_protected_set_contains_critical_tables() -> None:
    # Phase 3 (post-Layer-1 fix). ``trade_thesis`` was REMOVED from this
    # set so the hourly CleanupWorker tick can prune theses past their
    # 60-day TTL. Safety preserved by the per-row ``status='closed'``
    # filter at the cleanup query site (see
    # ``src/workers/cleanup_worker.py`` ``_RETENTION_EXTRA_FILTERS``)
    # and the ``test_cleanup_trade_thesis.py`` regression suite. The
    # other 8 critical tables MUST remain protected.
    expected = {
        "tias_results", "tias_analyses", "trade_intelligence",
        "trade_log", "trade_history",
        "thesis_store",
        "virtual_positions", "sniper_log",
    }
    assert expected.issubset(PROTECTED_TABLES)
    # Negative assertion — ensure trade_thesis stays OUT of the set.
    # Removing this assertion would silently allow a future regression
    # to re-protect the table and break the cleanup loop again.
    assert "trade_thesis" not in PROTECTED_TABLES


def test_is_protected_case_insensitive() -> None:
    assert is_protected("trade_log") is True
    assert is_protected("TRADE_LOG") is True
    assert is_protected("Trade_Log") is True
    assert is_protected("klines") is False


@pytest.mark.parametrize("sql,expected_kind,expected_table", [
    ("DELETE FROM trade_log WHERE id = 1", "DELETE", "trade_log"),
    ("delete from tias_results", "DELETE", "tias_results"),
    ("  DELETE  FROM   thesis_store  ", "DELETE", "thesis_store"),
    ("DELETE FROM main.virtual_positions", "DELETE", "virtual_positions"),
    ("DELETE FROM `sniper_log` WHERE x=1", "DELETE", "sniper_log"),
    ("TRUNCATE TABLE trade_history", "TRUNCATE", "trade_history"),
    ("TRUNCATE trade_intelligence", "TRUNCATE", "trade_intelligence"),
    ("DROP TABLE trade_thesis", "DROP", "trade_thesis"),
    ("DROP TABLE IF EXISTS tias_analyses", "DROP", "tias_analyses"),
])
def test_classify_destructive_extracts_table(sql, expected_kind, expected_table) -> None:
    kind, table = _classify(sql)
    assert kind == expected_kind
    assert table == expected_table


@pytest.mark.parametrize("sql", [
    "SELECT * FROM trade_log",
    "INSERT INTO trade_log (a) VALUES (1)",
    "UPDATE trade_log SET a = 1 WHERE id = 1",
    "PRAGMA table_info(trade_log)",
    "",
    "  ",
])
def test_classify_returns_none_for_safe_sql(sql) -> None:
    assert _classify(sql) is None


@pytest.mark.parametrize("sql", [
    "DELETE FROM trade_log WHERE created_at < '2020-01-01'",
    "DELETE FROM tias_results",
    "TRUNCATE TABLE thesis_store",
    "DROP TABLE IF EXISTS virtual_positions",
])
def test_assert_blocks_destructive_on_protected(sql) -> None:
    with pytest.raises(ProtectedTableViolation) as ei:
        assert_not_protected_destructive(sql)
    assert "PROTECTED" in str(ei.value.message)


@pytest.mark.parametrize("sql", [
    "DELETE FROM klines WHERE created_at < '2020-01-01'",
    "DELETE FROM ticker_cache",
    "TRUNCATE TABLE orderbook_snapshots",
    "DROP TABLE IF EXISTS news_articles",
    "SELECT * FROM trade_log",
])
def test_assert_passes_safe_sql(sql) -> None:
    # Should not raise.
    assert_not_protected_destructive(sql)


def test_force_overrides_block() -> None:
    # `force=True` is the documented escape hatch; logged as DB_PROTECT_FORCE
    # but does not raise.
    assert_not_protected_destructive("DELETE FROM trade_log", force=True)


# ─── Integration tests via DatabaseManager ────────────────────────────


@pytest.fixture
async def db():
    """Provide an isolated in-memory DatabaseManager with the protected tables created."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "test.db")
        mgr = DatabaseManager(path)
        await mgr.connect()
        # Create one protected and one safe table for the round-trip tests.
        await mgr.execute("CREATE TABLE trade_log (id INTEGER PRIMARY KEY, payload TEXT)")
        await mgr.execute("CREATE TABLE klines (id INTEGER PRIMARY KEY, ts TEXT)")
        await mgr.execute("INSERT INTO trade_log (payload) VALUES ('important')")
        await mgr.execute("INSERT INTO klines (ts) VALUES ('2026-01-01')")
        try:
            yield mgr
        finally:
            await mgr.disconnect()


@pytest.mark.asyncio
async def test_execute_blocks_delete_on_protected(db) -> None:
    with pytest.raises(ProtectedTableViolation):
        await db.execute("DELETE FROM trade_log WHERE id = 1")
    # Row must still be present — guard fired BEFORE the lock.
    rows = await db.fetch_all("SELECT * FROM trade_log")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_execute_allows_safe_delete(db) -> None:
    await db.execute("DELETE FROM klines WHERE id = 1")
    rows = await db.fetch_all("SELECT * FROM klines")
    assert rows == []


@pytest.mark.asyncio
async def test_execute_allows_select_on_protected(db) -> None:
    rows = await db.fetch_all("SELECT * FROM trade_log")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_execute_allows_insert_on_protected(db) -> None:
    await db.execute("INSERT INTO trade_log (payload) VALUES ('another')")
    rows = await db.fetch_all("SELECT * FROM trade_log")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_execute_force_protected_allows_destructive(db) -> None:
    await db.execute("DELETE FROM trade_log WHERE id = 1", force_protected=True)
    rows = await db.fetch_all("SELECT * FROM trade_log")
    assert rows == []


@pytest.mark.asyncio
async def test_executemany_blocks_delete_on_protected(db) -> None:
    with pytest.raises(ProtectedTableViolation):
        await db.executemany(
            "DELETE FROM trade_log WHERE id = ?",
            [(1,), (2,)],
        )


# ─── Cleanup module integration ───────────────────────────────────────


def test_retention_policies_excludes_protected() -> None:
    """If anyone adds a protected table to RETENTION_POLICIES, the import-time
    assertion in cleanup.py will fail. This test asserts the current state is clean."""
    from src.database.cleanup import RETENTION_POLICIES
    bad = [t for (t, _col, _days) in RETENTION_POLICIES if t.lower() in PROTECTED_TABLES]
    assert bad == [], f"PROTECTED tables found in RETENTION_POLICIES: {bad}"
