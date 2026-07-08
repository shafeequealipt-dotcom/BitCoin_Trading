"""P1-1 (2026-05-13) — cleanup_worker auto_vacuum probe + observability.

Three focused tests that lock in the P1-1 fix:

1. ``test_pragma_auto_vacuum_keys_by_column_name`` — proves that
   ``DatabaseManager.fetch_one('PRAGMA auto_vacuum')`` returns a dict
   keyed by the column name ``'auto_vacuum'`` (not by integer position).
   Pre-P1-1 the cleanup_worker indexed by ``row[0]``, which raised
   KeyError on the dict and was silently swallowed by the outer
   except — so even a migrated DB was reported as mode=0 and
   incremental_vacuum was never reached.

2. ``test_tick_emits_db_incremental_vacuum_ok_with_pages_freed`` — full
   integration: a temp DB in mode=2 with a populated freelist; one
   ``CleanupWorker.tick()`` call must emit
   ``DB_INCREMENTAL_VACUUM_OK pages_freed=N elapsed_ms=N`` with
   ``pages_freed > 0``.

3. ``test_tick_in_mode0_emits_migration_required_warning`` — converse:
   a default-mode-0 temp DB must emit ``DB_VACUUM_MIGRATION_REQUIRED``
   on first daily tick and skip the incremental_vacuum path entirely.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock

import pytest
from loguru import logger as loguru_logger

from src.database.connection import DatabaseManager
from src.workers.cleanup_worker import CleanupWorker


def _make_mode2_db(path: str, freelist_pages: int = 50) -> None:
    """Create a temp DB with auto_vacuum=INCREMENTAL and a populated freelist.

    Populates by INSERTing into a filler table then DROPping it, which
    SQLite tracks in the freelist under incremental_vacuum mode.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("VACUUM")
        conn.execute("CREATE TABLE filler(id INTEGER PRIMARY KEY, blob TEXT)")
        payload = "x" * 1000
        # 5000 inserts at 1KB each yields well over 50 freelist pages
        # after the DROP under the default 4KB page_size.
        for _ in range(5000):
            conn.execute("INSERT INTO filler (blob) VALUES (?)", (payload,))
        conn.commit()
        conn.execute("DROP TABLE filler")
        conn.commit()
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        assert free >= freelist_pages, f"setup produced only {free} freelist pages"
    finally:
        conn.close()


def _make_mode0_db(path: str) -> None:
    """Create a temp DB at default auto_vacuum=NONE (mode=0)."""
    conn = sqlite3.connect(path)
    try:
        # Explicit no-op write so the file exists and PRAGMAs return values.
        conn.execute("CREATE TABLE IF NOT EXISTS marker(id INTEGER PRIMARY KEY)")
        conn.commit()
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        assert mode == 0, f"expected mode=0 default, got {mode}"
    finally:
        conn.close()


def _fake_settings() -> MagicMock:
    """Minimal Settings stand-in — CleanupWorker only reads BaseWorker fields."""
    s = MagicMock()
    s.workers.max_consecutive_failures = 5
    s.workers.restart_delay = 10
    return s


@pytest.mark.asyncio
async def test_pragma_auto_vacuum_keys_by_column_name() -> None:
    """Probe by column name returns the actual mode; row[0] (the prior bug) would raise."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        _make_mode2_db(path)
        db = DatabaseManager(path)
        await db.connect()
        try:
            row = await db.fetch_one("PRAGMA auto_vacuum")
            assert row is not None
            # The correct key (post-fix). Pre-P1-1, the cleanup_worker
            # used ``row[0]`` here which raises KeyError on the dict.
            assert "auto_vacuum" in row
            assert int(row["auto_vacuum"]) == 2
            with pytest.raises(KeyError):
                _ = row[0]  # documents the bug the fix corrects.
        finally:
            await db.disconnect()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_tick_emits_db_incremental_vacuum_ok_with_pages_freed() -> None:
    """In mode=2 with a non-empty freelist, one tick reclaims pages and emits the new tag."""
    captured: list[str] = []
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        _make_mode2_db(path, freelist_pages=50)
        sink_id = loguru_logger.add(
            lambda msg: captured.append(str(msg)),
            level="DEBUG",
            format="{message}",
        )
        try:
            db = DatabaseManager(path)
            await db.connect()
            try:
                worker = CleanupWorker(_fake_settings(), db)
                await worker.tick()
            finally:
                await db.disconnect()
        finally:
            loguru_logger.remove(sink_id)

        ok_lines = [m for m in captured if "DB_INCREMENTAL_VACUUM_OK" in m]
        assert ok_lines, (
            "no DB_INCREMENTAL_VACUUM_OK emitted — check that fetch_one "
            "is returning the correct dict shape for PRAGMA auto_vacuum"
        )
        line = ok_lines[-1]
        assert "pages_freed=" in line, line
        assert "elapsed_ms=" in line, line
        assert "freelist_before=" in line, line
        assert "freelist_after=" in line, line
        # pages_freed should be a non-negative integer; with a 50+ freelist
        # and a 1000-page cap, the entire freelist should be reclaimed.
        import re
        m = re.search(r"pages_freed=(\d+)", line)
        assert m and int(m.group(1)) > 0, (
            f"expected pages_freed > 0, got line: {line}"
        )

        # Negative check: the legacy tag must not appear alongside the new one.
        legacy = [m for m in captured if "VACUUM | mode=incremental" in m]
        assert not legacy, "legacy VACUUM tag should be replaced, not duplicated"
    finally:
        if os.path.exists(path):
            os.unlink(path)


@pytest.mark.asyncio
async def test_tick_in_mode0_emits_migration_required_warning() -> None:
    """Mode=0 DBs fire DB_VACUUM_MIGRATION_REQUIRED and skip incremental_vacuum."""
    captured: list[str] = []
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        _make_mode0_db(path)
        sink_id = loguru_logger.add(
            lambda msg: captured.append(str(msg)),
            level="DEBUG",
            format="{message}",
        )
        try:
            db = DatabaseManager(path)
            await db.connect()
            try:
                worker = CleanupWorker(_fake_settings(), db)
                await worker.tick()
            finally:
                await db.disconnect()
        finally:
            loguru_logger.remove(sink_id)

        warn_lines = [
            m for m in captured if "DB_VACUUM_MIGRATION_REQUIRED" in m
        ]
        assert warn_lines, "expected DB_VACUUM_MIGRATION_REQUIRED in mode=0"
        assert "current_auto_vacuum=0" in warn_lines[-1]
        # And the new mode=2 tag must NOT fire in mode=0.
        new_tag = [m for m in captured if "DB_INCREMENTAL_VACUUM_OK" in m]
        assert not new_tag, (
            "DB_INCREMENTAL_VACUUM_OK fired in mode=0 — probe path is wrong"
        )
    finally:
        if os.path.exists(path):
            os.unlink(path)
