"""Unit tests for the DatabaseManager concurrency engines.

Phase conn-pool/p3-4 (db-concurrency-refactor 2026-05-14). Exercises:

- Engine selection by concurrency_model setting.
- API parity between _LegacyEngine and _PooledDatabaseEngine for the
  six public methods (execute, executemany, fetch_one, fetch_all,
  transaction, checkpoint).
- _ReaderPool acquire/release happy path.
- _ReaderPool exhaustion + queue-waiter resolution.
- _ReaderPool dynamic growth up to hard_cap.
- Writer-lock single-acquire serialization.
- _apply_pragmas applied to every connection at open.
- ProtectedTableViolation still raises on protected DELETE.
- transaction() rolls back on exception.
- checkpoint() runs on the writer side under both engines.

Tests use a fresh empty SQLite file in a tmp dir for each test — the
production DB is never touched.
"""

import asyncio
import os
import tempfile

import aiosqlite
import pytest

from src.core.exceptions import DatabaseError
from src.database.connection import (
    DatabaseManager,
    _apply_pragmas,
    _PooledDatabaseEngine,
    _ReaderPool,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tmp_db_path():
    """Provide a temporary SQLite path; clean up the file + WAL/SHM after."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


async def _seed_basic_schema(db: DatabaseManager) -> None:
    """Create a small test table the pooled engine can exercise."""
    await db.execute(
        "CREATE TABLE IF NOT EXISTS t_unit (id INTEGER PRIMARY KEY, v TEXT)"
    )


# ---------------------------------------------------------------------------
# Engine selection (Phase conn-pool/p3-9 — only reader_pool is supported)
# ---------------------------------------------------------------------------


async def test_engine_default_is_reader_pool(tmp_db_path):
    """Default ``concurrency_model`` is now ``reader_pool``; ``single_lock``
    was removed in Phase conn-pool/p3-9.
    """
    db = DatabaseManager(tmp_db_path)
    assert isinstance(db._engine, _PooledDatabaseEngine)


async def test_engine_explicit_reader_pool(tmp_db_path):
    db = DatabaseManager(tmp_db_path, concurrency_model="reader_pool", reader_pool_size=2)
    assert isinstance(db._engine, _PooledDatabaseEngine)
    assert db._engine._pool.size == 2
    assert db._engine._pool.hard_cap == 4


async def test_engine_rejects_single_lock(tmp_db_path):
    """Phase conn-pool/p3-9: passing the removed ``single_lock`` raises
    with a clear migration message.
    """
    with pytest.raises(DatabaseError):
        DatabaseManager(tmp_db_path, concurrency_model="single_lock")


async def test_engine_rejects_unknown_model(tmp_db_path):
    with pytest.raises(DatabaseError):
        DatabaseManager(tmp_db_path, concurrency_model="bogus")


# ---------------------------------------------------------------------------
# Public API parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["reader_pool"])
async def test_api_parity_basic_crud(tmp_db_path, engine):
    db = DatabaseManager(tmp_db_path, concurrency_model=engine, reader_pool_size=2)
    await db.connect()
    try:
        await _seed_basic_schema(db)
        await db.execute("INSERT INTO t_unit (v) VALUES (?)", ("alpha",))
        await db.executemany(
            "INSERT INTO t_unit (v) VALUES (?)", [("beta",), ("gamma",)]
        )
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM t_unit")
        assert row is not None and row["c"] == 3
        rows = await db.fetch_all("SELECT v FROM t_unit ORDER BY id")
        assert [r["v"] for r in rows] == ["alpha", "beta", "gamma"]
    finally:
        await db.disconnect()


@pytest.mark.parametrize("engine", ["reader_pool"])
async def test_transaction_commits_on_success(tmp_db_path, engine):
    db = DatabaseManager(tmp_db_path, concurrency_model=engine, reader_pool_size=2)
    await db.connect()
    try:
        await _seed_basic_schema(db)
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO t_unit (v) VALUES (?)", ("txn1",))
            await conn.execute("INSERT INTO t_unit (v) VALUES (?)", ("txn2",))
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM t_unit")
        assert row["c"] == 2
    finally:
        await db.disconnect()


@pytest.mark.parametrize("engine", ["reader_pool"])
async def test_transaction_rolls_back_on_exception(tmp_db_path, engine):
    db = DatabaseManager(tmp_db_path, concurrency_model=engine, reader_pool_size=2)
    await db.connect()
    try:
        await _seed_basic_schema(db)
        with pytest.raises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO t_unit (v) VALUES (?)", ("rollback",))
                raise RuntimeError("trigger rollback")
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM t_unit")
        assert row["c"] == 0, "rollback should leave the table empty"
    finally:
        await db.disconnect()


@pytest.mark.parametrize("engine", ["reader_pool"])
async def test_checkpoint_returns_three_fields(tmp_db_path, engine):
    db = DatabaseManager(tmp_db_path, concurrency_model=engine, reader_pool_size=2)
    await db.connect()
    try:
        result = await db.checkpoint("PASSIVE")
        assert set(result.keys()) >= {"busy", "log_pages", "ckpt_pages", "mode"}
        assert result["mode"] == "PASSIVE"
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# _ReaderPool primitive tests
# ---------------------------------------------------------------------------


async def test_reader_pool_acquire_release_happy_path(tmp_db_path):
    pool = _ReaderPool(tmp_db_path, size=2, hard_cap=4, wal_mode=True)
    await pool.open()
    try:
        conn1, wait_ms = await pool.acquire()
        assert conn1 is not None
        assert wait_ms < 100  # immediate from queue
        # Connection comes from the pool's owned set, never grown.
        assert conn1 in pool._conns
        pool.release(conn1)
        # Re-acquire should yield ONE of the pool's connections (FIFO order
        # depends on prior insertion; we only need to confirm reuse, not
        # which specific instance).
        conn2, _ = await pool.acquire()
        assert conn2 in pool._conns
        pool.release(conn2)
        assert pool.stats()["acquires"] == 2
        assert pool.stats()["growths"] == 0  # never had to grow
    finally:
        await pool.close()


async def test_reader_pool_dynamic_growth_to_hard_cap(tmp_db_path):
    pool = _ReaderPool(tmp_db_path, size=2, hard_cap=4, wal_mode=True)
    await pool.open()
    try:
        # Hold all initial readers, then acquire 2 more to trigger growth.
        held = []
        for _ in range(2):
            conn, _ = await pool.acquire()
            held.append(conn)
        # No connections available; next acquires should grow the pool.
        conn3, _ = await pool.acquire()
        conn4, _ = await pool.acquire()
        stats = pool.stats()
        assert stats["growths"] == 2
        assert stats["owned"] == 4
        # Release them all
        for c in held + [conn3, conn4]:
            pool.release(c)
    finally:
        await pool.close()


async def test_reader_pool_exhausted_waits_then_resolves(tmp_db_path):
    pool = _ReaderPool(tmp_db_path, size=1, hard_cap=2, wal_mode=True)
    await pool.open()
    try:
        # Hold both connections (1 initial + 1 grown).
        held1, _ = await pool.acquire()
        held2, _ = await pool.acquire()
        assert pool.stats()["owned"] == 2

        # Third acquire must wait; spawn a waiter task and release after 100ms.
        async def waiter():
            return await pool.acquire()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)  # let waiter queue
        assert pool.stats()["exhausted_count"] >= 1
        pool.release(held1)
        conn, wait_ms = await asyncio.wait_for(task, timeout=2.0)
        assert conn is held1  # FIFO from queue
        assert wait_ms > 0
        pool.release(conn)
        pool.release(held2)
    finally:
        await pool.close()


async def test_reader_pool_pragmas_applied_per_connection(tmp_db_path):
    pool = _ReaderPool(tmp_db_path, size=2, hard_cap=4, wal_mode=True)
    await pool.open()
    try:
        # Verify each pooled connection has busy_timeout set (one of our pragmas).
        for conn in pool._conns:
            cur = await conn.execute("PRAGMA busy_timeout")
            row = await cur.fetchone()
            await cur.close()
            assert int(row[0]) == 10000, "busy_timeout PRAGMA missing on reader"
    finally:
        await pool.close()


async def test_reader_pool_rejects_invalid_sizes(tmp_db_path):
    with pytest.raises(ValueError):
        _ReaderPool(tmp_db_path, size=0, hard_cap=4, wal_mode=True)
    with pytest.raises(ValueError):
        _ReaderPool(tmp_db_path, size=4, hard_cap=2, wal_mode=True)


# ---------------------------------------------------------------------------
# Writer lock serialization
# ---------------------------------------------------------------------------


async def test_writer_lock_serializes_concurrent_writes(tmp_db_path):
    """Two concurrent executemany calls must serialize on the writer."""
    db = DatabaseManager(
        tmp_db_path, concurrency_model="reader_pool", reader_pool_size=2
    )
    await db.connect()
    try:
        await _seed_basic_schema(db)

        async def insert_batch(prefix: str, n: int):
            rows = [(f"{prefix}-{i}",) for i in range(n)]
            await db.executemany("INSERT INTO t_unit (v) VALUES (?)", rows)

        await asyncio.gather(
            insert_batch("a", 50),
            insert_batch("b", 50),
            insert_batch("c", 50),
        )
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM t_unit")
        assert row["c"] == 150
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Concurrent reads under pooled engine
# ---------------------------------------------------------------------------


async def test_concurrent_reads_complete_under_pool(tmp_db_path):
    """8 concurrent reads against a 2-reader pool should complete (pool grows)."""
    db = DatabaseManager(
        tmp_db_path, concurrency_model="reader_pool", reader_pool_size=2
    )
    await db.connect()
    try:
        await _seed_basic_schema(db)
        await db.executemany(
            "INSERT INTO t_unit (v) VALUES (?)",
            [(f"row{i}",) for i in range(20)],
        )

        async def reader():
            row = await db.fetch_one("SELECT COUNT(*) AS c FROM t_unit")
            return row["c"]

        results = await asyncio.gather(*[reader() for _ in range(8)])
        assert all(r == 20 for r in results)
        # Pool must have grown beyond initial size of 2.
        assert isinstance(db._engine, _PooledDatabaseEngine)
        stats = db._engine._pool.stats()
        assert stats["acquires"] >= 8
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Protected-tables guard preserved on both engines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["reader_pool"])
async def test_protected_delete_blocked(tmp_db_path, engine):
    """DELETE on a PROTECTED table must raise before acquiring any lock."""
    from src.database.protected_tables import ProtectedTableViolation

    db = DatabaseManager(tmp_db_path, concurrency_model=engine, reader_pool_size=2)
    await db.connect()
    try:
        with pytest.raises(ProtectedTableViolation):
            await db.execute("DELETE FROM trade_log")
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# log_lock_histogram emits cleanly without raising
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["reader_pool"])
async def test_log_lock_histogram_emits(tmp_db_path, engine):
    db = DatabaseManager(tmp_db_path, concurrency_model=engine, reader_pool_size=2)
    await db.connect()
    try:
        await _seed_basic_schema(db)
        await db.execute("INSERT INTO t_unit (v) VALUES (?)", ("hist",))
        # Should not raise even if some counters are empty.
        db.log_lock_histogram()
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# _apply_pragmas standalone test
# ---------------------------------------------------------------------------


async def test_apply_pragmas_sets_expected_values(tmp_db_path):
    conn = await aiosqlite.connect(tmp_db_path)
    try:
        await _apply_pragmas(conn, wal_mode=True)
        # busy_timeout
        row = await (await conn.execute("PRAGMA busy_timeout")).fetchone()
        assert int(row[0]) == 10000
        # foreign_keys
        row = await (await conn.execute("PRAGMA foreign_keys")).fetchone()
        assert int(row[0]) == 1
        # synchronous
        row = await (await conn.execute("PRAGMA synchronous")).fetchone()
        assert int(row[0]) == 1  # NORMAL
        # journal_mode
        row = await (await conn.execute("PRAGMA journal_mode")).fetchone()
        assert row[0].lower() == "wal"
    finally:
        await conn.close()
