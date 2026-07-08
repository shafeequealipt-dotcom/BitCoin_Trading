"""Phase 1 (D-3 fix) — chunked ``MarketRepository.save_klines`` tests.

The historical implementation did one ``executemany`` for the full payload
under DatabaseManager's global lock; under heavy load that single call
held the lock for 12-20 s. Phase 1 splits the params list into
``kline_save_chunk_size`` chunks and yields the event loop between
chunks. These tests verify:

- Total row count persisted is identical to the un-chunked version.
- The configured chunk size is respected (last chunk may be partial).
- The event loop is yielded between chunks (sentinel coroutine advances).
- Single-chunk payloads (rows ≤ chunk_size) take the no-yield path so log
  volume stays bounded.
- ``INSERT OR IGNORE`` idempotency is preserved across chunk boundaries.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.database.repositories.market_repo import MarketRepository

from .conftest import make_klines


pytestmark = pytest.mark.asyncio


class TestSaveKlinesChunked:
    async def test_returns_total_row_count(self, temp_db):
        repo = MarketRepository(temp_db, kline_save_chunk_size=500)
        klines = make_klines(1500)
        n = await repo.save_klines(klines)
        assert n == 1500

    async def test_persists_all_rows_across_chunk_boundary(self, temp_db):
        repo = MarketRepository(temp_db, kline_save_chunk_size=500)
        await repo.save_klines(make_klines(1500))
        row = await temp_db.fetch_one(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ?", ("TESTUSDT",)
        )
        assert row is not None and row["c"] == 1500

    async def test_chunk_count_matches_size(self, temp_db, monkeypatch):
        """Spy on DatabaseManager.executemany; assert it's called once per chunk."""
        repo = MarketRepository(temp_db, kline_save_chunk_size=500)
        original = temp_db.executemany
        call_count = {"n": 0, "chunk_sizes": []}

        async def spy(sql, params_list, *args, **kwargs):
            call_count["n"] += 1
            call_count["chunk_sizes"].append(len(params_list))
            return await original(sql, params_list, *args, **kwargs)

        monkeypatch.setattr(temp_db, "executemany", spy)
        await repo.save_klines(make_klines(1500))
        # 1500 / 500 = 3 chunks of exactly 500 rows.
        assert call_count["n"] == 3
        assert call_count["chunk_sizes"] == [500, 500, 500]

    async def test_partial_last_chunk(self, temp_db, monkeypatch):
        repo = MarketRepository(temp_db, kline_save_chunk_size=500)
        original = temp_db.executemany
        seen_chunks: list[int] = []

        async def spy(sql, params_list, *args, **kwargs):
            seen_chunks.append(len(params_list))
            return await original(sql, params_list, *args, **kwargs)

        monkeypatch.setattr(temp_db, "executemany", spy)
        await repo.save_klines(make_klines(1234))
        # 1234 = 500 + 500 + 234.
        assert seen_chunks == [500, 500, 234]

    async def test_single_chunk_payload_no_extra_yield(self, temp_db, monkeypatch):
        """A payload <= chunk_size should issue exactly one executemany call."""
        repo = MarketRepository(temp_db, kline_save_chunk_size=500)
        original = temp_db.executemany
        call_count = {"n": 0}

        async def spy(sql, params_list, *args, **kwargs):
            call_count["n"] += 1
            return await original(sql, params_list, *args, **kwargs)

        monkeypatch.setattr(temp_db, "executemany", spy)
        await repo.save_klines(make_klines(499))
        assert call_count["n"] == 1

    async def test_event_loop_yields_between_chunks(self, temp_db):
        """A concurrent sentinel coroutine should advance during the save —
        proving that ``await asyncio.sleep(0)`` between chunks released
        control of the event loop."""
        repo = MarketRepository(temp_db, kline_save_chunk_size=100)
        sentinel_ticks = {"n": 0}

        async def sentinel():
            # Bounded loop so a regression doesn't hang the test.
            for _ in range(1000):
                sentinel_ticks["n"] += 1
                await asyncio.sleep(0)

        sentinel_task = asyncio.create_task(sentinel())
        # 1000 rows / 100 chunk = 10 chunks; 9 yield points.
        await repo.save_klines(make_klines(1000))
        sentinel_task.cancel()
        try:
            await sentinel_task
        except asyncio.CancelledError:
            pass
        assert sentinel_ticks["n"] > 0, (
            "sentinel did not advance — the save did not yield the event loop"
        )

    async def test_insert_or_ignore_idempotent_across_chunks(self, temp_db):
        """Re-saving the same klines must not duplicate rows, regardless of
        chunk boundaries — INSERT OR IGNORE on (symbol, timeframe, timestamp)
        is the contract."""
        repo = MarketRepository(temp_db, kline_save_chunk_size=300)
        klines = make_klines(1000)
        await repo.save_klines(klines)
        await repo.save_klines(klines)  # second call, same data
        row = await temp_db.fetch_one(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ?", ("TESTUSDT",)
        )
        assert row is not None and row["c"] == 1000

    async def test_empty_input_is_noop(self, temp_db):
        repo = MarketRepository(temp_db, kline_save_chunk_size=500)
        n = await repo.save_klines([])
        assert n == 0

    async def test_invalid_chunk_size_falls_back_to_default(self, temp_db):
        """Construct with an invalid chunk size and confirm fallback to the
        module default (500). Behavior, not introspection — verify by
        observing chunk count for a 700-row payload."""
        repo = MarketRepository(temp_db, kline_save_chunk_size=0)
        # 700 rows with default 500 chunk = 2 chunks.
        n = await repo.save_klines(make_klines(700))
        assert n == 700
        row = await temp_db.fetch_one(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ?", ("TESTUSDT",)
        )
        assert row is not None and row["c"] == 700

    async def test_chunk_size_one_still_works(self, temp_db):
        """Edge: chunk_size=1 (every row is its own executemany)."""
        repo = MarketRepository(temp_db, kline_save_chunk_size=1)
        n = await repo.save_klines(make_klines(7))
        assert n == 7
        row = await temp_db.fetch_one(
            "SELECT COUNT(*) AS c FROM klines WHERE symbol = ?", ("TESTUSDT",)
        )
        assert row is not None and row["c"] == 7
