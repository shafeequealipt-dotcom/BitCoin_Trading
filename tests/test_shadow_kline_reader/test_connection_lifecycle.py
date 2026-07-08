"""Connection-lifecycle and concurrency tests for ShadowKlineReader."""

import asyncio

import pytest

from src.analysis.structure.shadow_kline_reader import ShadowKlineReader
from src.core.exceptions import DatabaseError


class TestConnectClose:
    async def test_connect_then_close_opens_one_connection(self, temp_shadow_db):
        r = ShadowKlineReader(temp_shadow_db)
        await r.connect()
        assert r.get_stats()["connection_opens"] == 1
        await r.close()

    async def test_connect_is_idempotent(self, temp_shadow_db):
        r = ShadowKlineReader(temp_shadow_db)
        await r.connect()
        await r.connect()
        await r.connect()
        assert r.get_stats()["connection_opens"] == 1
        await r.close()

    async def test_close_is_idempotent(self, temp_shadow_db):
        r = ShadowKlineReader(temp_shadow_db)
        await r.connect()
        await r.close()
        await r.close()  # second close is a no-op, must not raise

    async def test_close_without_connect_is_noop(self, temp_shadow_db):
        r = ShadowKlineReader(temp_shadow_db)
        # No connect — close should be silent no-op.
        await r.close()
        assert r.get_stats()["connection_opens"] == 0


class TestPersistentReuse:
    async def test_n_calls_use_one_connection(self, temp_shadow_db):
        """100 sequential get_klines calls open exactly 1 connection."""
        r = ShadowKlineReader(temp_shadow_db)
        await r.connect()
        try:
            for _ in range(100):
                await r.get_klines("BTCUSDT", "60", 200)
            stats = r.get_stats()
            assert stats["connection_opens"] == 1
            assert stats["query_executes"] == 100
            assert stats["total_calls"] == 100
        finally:
            await r.close()

    async def test_get_klines_without_connect_returns_empty(self, temp_shadow_db):
        """get_klines without connect() warns and returns []; does not crash."""
        r = ShadowKlineReader(temp_shadow_db)
        candles = await r.get_klines("BTCUSDT", "60", 200)
        assert candles == []
        # _total_calls is incremented (call was made), but _query_executes stays 0
        # (early-returned before the SQL).
        stats = r.get_stats()
        assert stats["total_calls"] == 1
        assert stats["query_executes"] == 0


class TestConcurrency:
    async def test_concurrent_calls_serialise_and_complete(self, temp_shadow_db):
        """50 concurrent get_klines via asyncio.gather all complete; counter is exact."""
        r = ShadowKlineReader(temp_shadow_db)
        await r.connect()
        try:
            results = await asyncio.gather(
                *[r.get_klines("BTCUSDT", "60", 200) for _ in range(50)]
            )
            assert all(len(rows) == 6 for rows in results)
            stats = r.get_stats()
            assert stats["connection_opens"] == 1
            assert stats["query_executes"] == 50
            assert stats["total_calls"] == 50
        finally:
            await r.close()


class TestErrorPaths:
    async def test_missing_db_raises_databaseerror(self, tmp_path):
        bad_path = str(tmp_path / "does-not-exist.db")
        r = ShadowKlineReader(bad_path)
        with pytest.raises(DatabaseError) as excinfo:
            await r.connect()
        assert bad_path in str(excinfo.value) or bad_path in str(excinfo.value.details)
        # Failed connect leaves the reader in a clean state.
        assert r.get_stats()["connection_opens"] == 0

    async def test_failed_connect_leaves_reader_reusable(self, tmp_path, temp_shadow_db):
        """After a failed connect, a subsequent connect to a valid path succeeds."""
        bad_path = str(tmp_path / "missing.db")
        r = ShadowKlineReader(bad_path)
        with pytest.raises(DatabaseError):
            await r.connect()
        # Re-bind and retry by mutating db_path (not officially supported — but
        # we test that internal state didn't pin to the bad connection).
        r._db_path = temp_shadow_db
        await r.connect()
        try:
            assert r.get_stats()["connection_opens"] == 1
            candles = await r.get_klines("BTCUSDT", "60", 200)
            assert len(candles) == 6
        finally:
            await r.close()


class TestStatsCounter:
    async def test_get_stats_keys_and_types(self, temp_shadow_db):
        r = ShadowKlineReader(temp_shadow_db)
        await r.connect()
        await r.get_klines("BTCUSDT", "60", 200)
        stats = r.get_stats()
        assert set(stats) == {
            "total_calls",
            "connection_opens",
            "query_executes",
            "total_query_ms",
            "avg_query_ms",
        }
        assert isinstance(stats["total_calls"], int)
        assert isinstance(stats["connection_opens"], int)
        assert isinstance(stats["query_executes"], int)
        assert isinstance(stats["total_query_ms"], float)
        assert isinstance(stats["avg_query_ms"], float)
        assert stats["total_query_ms"] > 0.0  # at least one query ran
        await r.close()

    async def test_avg_query_ms_zero_when_no_queries(self, temp_shadow_db):
        r = ShadowKlineReader(temp_shadow_db)
        # No connect, no queries — avg should be 0.0 (no division by zero).
        stats = r.get_stats()
        assert stats["query_executes"] == 0
        assert stats["avg_query_ms"] == 0.0
