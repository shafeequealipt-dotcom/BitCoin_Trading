"""T1-4 / F4 incremental_vacuum migration smoke tests (six-tier-fixes 2026-05-11).

Validates the migration sequence and the cleanup_worker constants. Uses
sync stdlib sqlite3 against an on-disk temp file (incremental_vacuum is
not supported on :memory: databases per SQLite docs — a temp file is
the closest equivalent).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_incremental_vacuum_pages_constant():
    """cleanup_worker exposes the per-tick page count as a module constant."""
    from src.workers import cleanup_worker

    assert hasattr(cleanup_worker, "_INCREMENTAL_VACUUM_PAGES")
    assert cleanup_worker._INCREMENTAL_VACUUM_PAGES == 1000


def test_pragma_auto_vacuum_incremental_persists_after_vacuum():
    """Setting auto_vacuum=INCREMENTAL + VACUUM persists the mode."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        conn = sqlite3.connect(path)
        try:
            # Fresh DB starts at auto_vacuum=0 (NONE).
            mode_before = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            assert mode_before == 0
            # Migration sequence: set mode, then VACUUM once.
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("VACUUM")
            mode_after = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            assert mode_after == 2
        finally:
            conn.close()
        # Re-open to confirm persistence.
        conn2 = sqlite3.connect(path)
        try:
            mode_reopen = conn2.execute("PRAGMA auto_vacuum").fetchone()[0]
            assert mode_reopen == 2
        finally:
            conn2.close()
    finally:
        os.unlink(path)


def test_pragma_incremental_vacuum_executes_without_error_on_migrated_db():
    """PRAGMA incremental_vacuum(N) runs on a migrated DB even when no freelist."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("VACUUM")
            # Should run without error even with no pages to reclaim.
            cur = conn.execute("PRAGMA incremental_vacuum(1000)")
            cur.fetchall()  # consume result set
            # Confirm freelist count is 0 (nothing to reclaim).
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            assert free == 0
        finally:
            conn.close()
    finally:
        os.unlink(path)


def test_incremental_vacuum_reclaims_pages_after_drop():
    """PRAGMA incremental_vacuum(N) reclaims pages when freelist is non-empty.

    Creates a table, fills with data, drops it; freelist grows; the
    incremental call shrinks the DB.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("VACUUM")
            conn.execute("CREATE TABLE filler(id INTEGER PRIMARY KEY, blob TEXT)")
            payload = "x" * 1000
            for i in range(5000):
                conn.execute("INSERT INTO filler (blob) VALUES (?)", (payload,))
            conn.commit()
            size_with_data = os.path.getsize(path)
            conn.execute("DROP TABLE filler")
            conn.commit()
            # Freelist should now be populated.
            free_before = conn.execute("PRAGMA freelist_count").fetchone()[0]
            assert free_before > 0
            cur = conn.execute("PRAGMA incremental_vacuum(1000)")
            cur.fetchall()
            free_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
            assert free_after < free_before
            size_after = os.path.getsize(path)
            assert size_after < size_with_data
        finally:
            conn.close()
    finally:
        os.unlink(path)
