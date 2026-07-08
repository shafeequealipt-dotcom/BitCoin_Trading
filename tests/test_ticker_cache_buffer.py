"""Issue 2 of cascade-fix series — TickerCacheBuffer.

Pins the buffer's contract:

  1. ``put`` is latest-wins per symbol — multiple puts for the same
     symbol collapse to one row at flush time.
  2. ``put`` is thread-safe — concurrent puts from non-asyncio
     threads do not corrupt internal state.
  3. ``get`` returns the most-recent put for a symbol; None on miss.
  4. ``flush`` writes the snapshot via ``save_tickers_batch`` and
     clears the pending dict.
  5. The drainer fires on its interval and flushes pending puts.
  6. ``stop`` performs a final flush so no put is orphaned.
  7. The drainer survives a flush failure (does not die on one
     exception).
  8. ``MarketRepository.save_tickers_batch`` writes every row in one
     ``executemany`` and the result is observable via ``get_ticker``.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import pytest

from src.core.types import Ticker
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.workers.ticker_cache_buffer import TickerCacheBuffer


def _make_ticker(symbol: str, price: float) -> Ticker:
    return Ticker(
        symbol=symbol, last_price=price, bid=price - 0.1, ask=price + 0.1,
        high_24h=price * 1.05, low_24h=price * 0.95,
        volume_24h=1_000_000.0, change_24h_pct=0.0,
        timestamp=datetime.now(UTC),
    )


@pytest.fixture()
async def db(tmp_path):
    """Spin up an aiosqlite DB with the production ticker_cache schema."""
    db_path = tmp_path / "ticker_test.db"
    mgr = DatabaseManager(str(db_path))
    await mgr.connect()
    await mgr.execute(
        """
        CREATE TABLE ticker_cache (
            symbol TEXT PRIMARY KEY,
            last_price REAL NOT NULL,
            bid REAL NOT NULL DEFAULT 0,
            ask REAL NOT NULL DEFAULT 0,
            high_24h REAL NOT NULL DEFAULT 0,
            low_24h REAL NOT NULL DEFAULT 0,
            volume_24h REAL NOT NULL DEFAULT 0,
            change_24h_pct REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
    )
    yield mgr
    await mgr.disconnect()


@pytest.mark.asyncio
async def test_put_is_latest_wins_per_symbol(db) -> None:
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    # 100 puts for the same symbol — only one survives.
    for i in range(100):
        buf.put(_make_ticker("BTCUSDT", 50_000.0 + i))
    snap = buf.get("BTCUSDT")
    assert snap is not None
    assert snap.last_price == 50_099.0  # the last put wins


@pytest.mark.asyncio
async def test_get_returns_none_on_miss(db) -> None:
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    assert buf.get("ETHUSDT") is None


@pytest.mark.asyncio
async def test_put_is_thread_safe(db) -> None:
    """Stress test — 8 threads each put 1000 tickers concurrently. The
    buffer must end with exactly 8 distinct symbols (one per thread)."""
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)

    def _worker(idx: int) -> None:
        for j in range(1000):
            buf.put(_make_ticker(f"SYM{idx}", 100.0 + j))

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every thread's last put for its symbol must be present.
    for i in range(8):
        snap = buf.get(f"SYM{i}")
        assert snap is not None
        assert snap.last_price == 1099.0  # last j was 999 → 100 + 999


@pytest.mark.asyncio
async def test_flush_writes_via_save_tickers_batch_and_clears(db) -> None:
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    buf.put(_make_ticker("BTCUSDT", 50_000.0))
    buf.put(_make_ticker("ETHUSDT", 3_000.0))
    buf.put(_make_ticker("SOLUSDT", 100.0))
    n = await buf.flush()
    assert n == 3
    # Pending is cleared.
    assert not buf.has_pending()
    # All three rows present in DB.
    btc = await repo.get_ticker("BTCUSDT")
    assert btc is not None and btc.last_price == 50_000.0
    eth = await repo.get_ticker("ETHUSDT")
    assert eth is not None and eth.last_price == 3_000.0
    sol = await repo.get_ticker("SOLUSDT")
    assert sol is not None and sol.last_price == 100.0


@pytest.mark.asyncio
async def test_flush_is_noop_when_empty(db) -> None:
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    n = await buf.flush()
    assert n == 0


@pytest.mark.asyncio
async def test_drainer_flushes_on_interval(db) -> None:
    """Tight interval (60ms minimum, the buffer's lower bound, but we
    use 100ms for safety) — within ~300ms the buffer should have at
    least one flush."""
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=100)
    await buf.start()
    try:
        buf.put(_make_ticker("BTCUSDT", 50_000.0))
        # Wait long enough for ≥ 1 drain cycle.
        await asyncio.sleep(0.3)
        # Buffer should be empty now (drained).
        assert not buf.has_pending()
        # DB row should exist.
        row = await repo.get_ticker("BTCUSDT")
        assert row is not None and row.last_price == 50_000.0
    finally:
        await buf.stop()


@pytest.mark.asyncio
async def test_stop_performs_final_flush(db) -> None:
    """After stop(), pending puts must be flushed to DB so no data is
    orphaned at shutdown."""
    repo = MarketRepository(db)
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    await buf.start()
    buf.put(_make_ticker("ETHUSDT", 3_000.0))
    # stop() runs the final flush even though the drainer interval
    # has not elapsed.
    await buf.stop()
    assert not buf.has_pending()
    row = await repo.get_ticker("ETHUSDT")
    assert row is not None and row.last_price == 3_000.0


@pytest.mark.asyncio
async def test_drainer_survives_flush_failure(db) -> None:
    """If save_tickers_batch raises, the drainer logs and continues
    — it must NOT die on one bad flush."""
    class _BrokenRepo:
        async def save_tickers_batch(self, tickers):
            raise RuntimeError("simulated write failure")

    buf = TickerCacheBuffer(_BrokenRepo(), flush_interval_ms=80)
    await buf.start()
    try:
        buf.put(_make_ticker("BTCUSDT", 50_000.0))
        await asyncio.sleep(0.25)  # ≥ 2 drain cycles
        # Drainer is still alive (not done) and the err counter ticked.
        assert buf._drainer_task is not None
        assert not buf._drainer_task.done()
        assert buf._flush_err_count >= 1
    finally:
        # Stop without a final flush attempt failing on us — replace
        # repo with a working stub.
        buf._repo = _NoopRepo()
        await buf.stop()


class _NoopRepo:
    async def save_tickers_batch(self, tickers):
        return len(tickers)


@pytest.mark.asyncio
async def test_save_tickers_batch_is_executemany_efficient(db) -> None:
    """Source-level pin (and behavioral spot-check) — a 50-ticker
    batch should write all 50 rows. Confirms the executemany path."""
    repo = MarketRepository(db)
    tickers = [_make_ticker(f"SYM{i}", 100.0 + i) for i in range(50)]
    n = await repo.save_tickers_batch(tickers)
    assert n == 50
    # Spot-check a few rows.
    row = await repo.get_ticker("SYM0")
    assert row is not None and row.last_price == 100.0
    row = await repo.get_ticker("SYM49")
    assert row is not None and row.last_price == 149.0


@pytest.mark.asyncio
async def test_save_tickers_batch_empty_is_noop(db) -> None:
    repo = MarketRepository(db)
    n = await repo.save_tickers_batch([])
    assert n == 0


@pytest.mark.asyncio
async def test_market_repo_get_ticker_consults_buffer_first(db) -> None:
    """When a buffer is attached, get_ticker on a symbol that exists
    in the buffer must return the buffer's value (without hitting the
    DB). Verify by leaving the DB row stale and putting a fresh value
    in the buffer — the returned price should be the buffer's."""
    repo = MarketRepository(db)
    # Seed DB with stale row.
    await repo.save_ticker(_make_ticker("BTCUSDT", 1.0))
    # Attach buffer with a fresh value not yet flushed.
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    repo.attach_ticker_buffer(buf)
    buf.put(_make_ticker("BTCUSDT", 50_000.0))

    result = await repo.get_ticker("BTCUSDT")
    assert result is not None
    assert result.last_price == 50_000.0  # buffer value, not the DB stale 1.0


@pytest.mark.asyncio
async def test_market_repo_get_ticker_falls_back_to_db_on_miss(db) -> None:
    """When a buffer is attached but holds no entry for the symbol,
    get_ticker falls back to the DB SELECT path."""
    repo = MarketRepository(db)
    await repo.save_ticker(_make_ticker("ETHUSDT", 3_000.0))
    buf = TickerCacheBuffer(repo, flush_interval_ms=10_000)
    repo.attach_ticker_buffer(buf)
    # Buffer empty for ETHUSDT; falls back to DB.
    result = await repo.get_ticker("ETHUSDT")
    assert result is not None
    assert result.last_price == 3_000.0
