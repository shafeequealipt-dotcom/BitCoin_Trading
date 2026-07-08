"""Market data repository: save and query klines, tickers, and orderbook snapshots."""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import OHLCV, Ticker, TimeFrame
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")

# Phase 1 (D-3 fix) — default chunk size when no override is supplied at
# construction. Mirrors ``DatabaseSettings.kline_save_chunk_size`` so legacy
# callers that instantiate ``MarketRepository(db)`` without settings still
# benefit from chunking. Override via the constructor when settings are
# available (workers/manager.py wiring passes the configured value).
_DEFAULT_KLINE_SAVE_CHUNK_SIZE = 500


class MarketRepository:
    """Repository for market data persistence.

    Args:
        db: Active DatabaseManager instance.
        kline_save_chunk_size: Per-chunk row count for
            :meth:`save_klines`. The default mirrors
            ``DatabaseSettings.kline_save_chunk_size`` so legacy callers
            that have no ``Settings`` reference still get chunked writes.
            Must be a positive integer; values <= 0 fall back to the
            module default.
    """

    def __init__(
        self,
        db: DatabaseManager,
        kline_save_chunk_size: int = _DEFAULT_KLINE_SAVE_CHUNK_SIZE,
    ) -> None:
        self._db = db
        if not isinstance(kline_save_chunk_size, int) or kline_save_chunk_size < 1:
            kline_save_chunk_size = _DEFAULT_KLINE_SAVE_CHUNK_SIZE
        self._kline_save_chunk_size = kline_save_chunk_size

    # Retention: keep newest N rows per (symbol, timeframe). Enforced
    # exclusively by ``cleanup_worker._sweep_klines_retention`` (hourly).
    # 300 = 200 used in queries + 50% buffer.
    #
    # Phase 4 of the post-Layer-1 fix work removed the historical
    # in-line DELETE that fired here every 50 saves per (symbol,
    # timeframe). That DELETE used to be the "belt and suspenders"
    # backstop alongside the hourly sweep, but its lock acquisition
    # contended directly with kline_worker's write loop and was a
    # measurable contributor to D-3 lock contention. The hourly sweep
    # alone is sufficient — at kline_worker's 60 s tick the table
    # grows by at most ~60 rows per (symbol, timeframe) per hour,
    # then the sweep prunes back to 300. Worst case during a missed
    # sweep: a few hundred extra rows per pair, well inside the
    # page-cache budget.
    _KLINES_RETENTION_PER_SYMTF = 300

    async def save_klines(self, klines: list[OHLCV]) -> int:
        """Save a batch of klines, ignoring duplicates.

        Per-(symbol, timeframe) retention is enforced by the hourly
        ``cleanup_worker._sweep_klines_retention`` — this method does
        only the INSERT OR IGNORE, so it holds the DatabaseManager
        lock for as little time as possible. See class docstring above
        for the Phase-4 rationale.

        Phase 1 (D-3 fix). The historical implementation issued a single
        ``executemany`` for the full payload under DatabaseManager's
        global ``asyncio.Lock``; with kline_worker pushing ~9k rows per
        tick across ~45 (symbol, timeframe) pairs that single call was
        observed holding the lock for 12-20 s, queueing every other
        worker behind it. We now split the params list into chunks of
        ``self._kline_save_chunk_size`` rows and yield the event loop
        between chunks via ``asyncio.sleep(0)``. Each chunk acquires
        the DB lock briefly and releases it; other workers can interleave
        their own ops without queueing for the duration of the full
        save. Total wall-clock work is unchanged because the sum of
        per-chunk executemany times equals the historical single-call
        time; what changes is who waits and for how long.

        ``KLINE_SAVE_CHUNKED`` is emitted at INFO level only when the
        payload exceeds one chunk, so single-chunk saves stay quiet
        and the log volume is bounded by the number of multi-chunk
        ticks per hour (typically a few).

        Args:
            klines: List of OHLCV dataclasses.

        Returns:
            Number of rows inserted.
        """
        if not klines:
            return 0

        sql = """
            INSERT OR IGNORE INTO klines
            (symbol, timeframe, timestamp, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (
                k.symbol,
                k.timeframe.value if isinstance(k.timeframe, TimeFrame) else k.timeframe,
                k.timestamp.isoformat(),
                k.open,
                k.high,
                k.low,
                k.close,
                k.volume,
                k.turnover,
            )
            for k in klines
        ]

        chunk_size = self._kline_save_chunk_size
        total = len(params)
        chunks = (total + chunk_size - 1) // chunk_size
        t0 = time.monotonic()
        for i in range(0, total, chunk_size):
            await self._db.executemany(sql, params[i : i + chunk_size])
            # Yield the event loop between chunks so other workers (and
            # other coroutines on this worker) can acquire the DB lock
            # without queueing behind the entire save. ``sleep(0)`` is
            # the minimal yield point — it does not idle the loop.
            if chunks > 1:
                await asyncio.sleep(0)
        el_ms = (time.monotonic() - t0) * 1000.0

        sym = klines[0].symbol
        tf = (
            klines[0].timeframe.value
            if isinstance(klines[0].timeframe, TimeFrame)
            else klines[0].timeframe
        )

        if chunks > 1:
            avg_chunk_ms = el_ms / chunks
            log.info(
                f"KLINE_SAVE_CHUNKED | sym={sym} tf={tf} rows={total} "
                f"chunks={chunks} chunk_size={chunk_size} "
                f"avg_chunk_ms={avg_chunk_ms:.1f} el_ms={el_ms:.1f} | {ctx()}"
            )
        else:
            log.debug(
                "Saved {n} klines for {s}/{tf} "
                "(single chunk, el_ms={el_ms:.1f}, retention via cleanup_worker keep {k})",
                n=total, s=sym, tf=tf, el_ms=el_ms, k=self._KLINES_RETENTION_PER_SYMTF,
            )
        return total

    async def get_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> list[OHLCV]:
        """Fetch recent klines from the database.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            timeframe: Bybit interval string (e.g. "15", "60", "D").
            limit: Maximum rows to return.

        Returns:
            List of OHLCV ordered by timestamp ascending.
        """
        rows = await self._db.fetch_all(
            """
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume, turnover
            FROM klines
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (symbol, timeframe, limit),
        )
        result = []
        for r in reversed(rows):
            result.append(OHLCV(
                symbol=r["symbol"],
                timeframe=TimeFrame(r["timeframe"]),
                timestamp=datetime.fromisoformat(r["timestamp"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                turnover=r["turnover"],
            ))
        return result

    async def get_klines_batch(
        self,
        symbols: list[str],
        timeframe: str,
        limit: int = 200,
    ) -> dict[str, list[OHLCV]]:
        """Fetch newest ``limit`` klines for each symbol in a SINGLE query.

        Replaces N serial ``get_klines`` calls with one ``ROW_NUMBER()``
        partitioned query. Returns the SAME data shape as ``get_klines``:
        timestamps ascending, OHLCV dataclass instances.

        Args:
            symbols: Trading pairs. Empty list returns an empty dict.
            timeframe: Bybit interval string (e.g. "5", "60", "D").
            limit: Max rows per symbol.

        Returns:
            ``{symbol: [OHLCV]}``. Every requested symbol is a key (value may
            be an empty list if the table has no rows for it).
        """
        if not symbols:
            return {}

        placeholders = ",".join("?" for _ in symbols)
        rows = await self._db.fetch_all(
            f"""
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume, turnover
            FROM (
              SELECT symbol, timeframe, timestamp, open, high, low, close, volume, turnover,
                     ROW_NUMBER() OVER (
                       PARTITION BY symbol
                       ORDER BY timestamp DESC
                     ) AS rn
              FROM klines
              WHERE symbol IN ({placeholders}) AND timeframe = ?
            ) WHERE rn <= ?
            ORDER BY symbol, timestamp DESC
            """,
            (*symbols, timeframe, limit),
        )

        grouped: dict[str, list[OHLCV]] = {s: [] for s in symbols}
        for r in rows:
            grouped.setdefault(r["symbol"], []).append(OHLCV(
                symbol=r["symbol"],
                timeframe=TimeFrame(r["timeframe"]),
                timestamp=datetime.fromisoformat(r["timestamp"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                turnover=r["turnover"],
            ))
        # Query yields DESC per symbol; callers (strategies, TAEngine) expect
        # ASC — same contract as ``get_klines`` which does ``reversed(rows)``.
        for s in grouped:
            grouped[s].reverse()
        return grouped

    async def save_ticker(self, ticker: Ticker) -> None:
        """Upsert a ticker into the cache.

        Single-row write. Issue 2 of cascade-fix series (2026-05-10):
        the high-frequency WS callback path now batches via
        :class:`TickerCacheBuffer` + :meth:`save_tickers_batch`. This
        single-row variant is retained for low-frequency callers (test
        fixtures, ad-hoc scripts) that don't warrant a buffer.

        Args:
            ticker: Ticker dataclass.
        """
        await self._db.execute(
            """
            INSERT OR REPLACE INTO ticker_cache
            (symbol, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker.symbol,
                ticker.last_price,
                ticker.bid,
                ticker.ask,
                ticker.high_24h,
                ticker.low_24h,
                ticker.volume_24h,
                ticker.change_24h_pct,
                ticker.timestamp.isoformat(),
            ),
        )

    async def save_tickers_batch(self, tickers: list[Ticker]) -> int:
        """Batch upsert tickers into the cache via a single
        ``executemany`` call.

        Issue 2 of cascade-fix series (2026-05-10). Phase 0 baseline
        observed 100-200 ``INSERT OR REPLACE INTO ticker_cache`` calls
        per second from the WebSocket path, with each call acquiring
        the global ``DatabaseManager.asyncio.Lock`` and queueing the
        next call behind itself. Cumulative wait of 34 hours per day
        and max wait of 63.6 seconds (Phase 0 sample).

        :class:`TickerCacheBuffer` collapses multiple per-symbol puts
        into a single latest-wins snapshot (typical batch ≤ 50 rows for
        a 50-symbol universe), and this method writes that snapshot in
        one ``executemany`` lock acquisition. Result: DB write rate
        drops from 100-200/sec to ~2/sec at the default 500 ms flush
        cadence.

        Mirrors the chunking pattern of :meth:`save_klines` so single
        large batches yield the loop between chunks. For typical
        ticker batch sizes (≤ 50 rows) the chunk size of
        ``_DEFAULT_KLINE_SAVE_CHUNK_SIZE`` means no inter-chunk yield
        is needed; the chunked structure exists for safety if a
        pathological backlog ever occurs.

        Args:
            tickers: List of Ticker dataclasses. Empty list is a no-op.

        Returns:
            Number of rows written (== ``len(tickers)``).
        """
        if not tickers:
            return 0

        sql = """
            INSERT OR REPLACE INTO ticker_cache
            (symbol, last_price, bid, ask, high_24h, low_24h,
             volume_24h, change_24h_pct, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (
                t.symbol,
                t.last_price,
                t.bid,
                t.ask,
                t.high_24h,
                t.low_24h,
                t.volume_24h,
                t.change_24h_pct,
                t.timestamp.isoformat(),
            )
            for t in tickers
        ]

        chunk_size = self._kline_save_chunk_size
        total = len(params)
        chunks = (total + chunk_size - 1) // chunk_size
        for i in range(0, total, chunk_size):
            await self._db.executemany(sql, params[i : i + chunk_size])
            if chunks > 1:
                await asyncio.sleep(0)
        return total

    def attach_ticker_buffer(self, ticker_buffer: Any) -> None:
        """Wire the TickerCacheBuffer for in-memory ticker reads.

        Issue 2 of cascade-fix series (2026-05-10). When attached,
        ``get_ticker`` consults the buffer before falling back to the
        DB ``SELECT``. The buffer holds tickers from the WS stream
        that have not yet been flushed to disk — strictly fresher
        than the DB. Idempotent — safe to call multiple times.
        """
        self._ticker_buffer = ticker_buffer

    async def get_ticker(self, symbol: str) -> Ticker | None:
        """Fetch a cached ticker.

        Issue 2 of cascade-fix series (2026-05-10): consults the
        :class:`TickerCacheBuffer` first when attached via
        ``attach_ticker_buffer``. The buffer holds tickers up to one
        flush_interval old (default 500 ms) from the WS stream — so on
        hit the caller avoids one ``DB_LOCK_WAIT`` event. On miss
        (symbol not yet seen since last flush), falls back to the DB
        SELECT path.

        Args:
            symbol: Trading pair.

        Returns:
            Ticker dataclass or None.
        """
        buf = getattr(self, "_ticker_buffer", None)
        if buf is not None:
            cached = buf.get(symbol)
            if cached is not None:
                return cached

        row = await self._db.fetch_one(
            "SELECT * FROM ticker_cache WHERE symbol = ?", (symbol,)
        )
        if row is None:
            return None
        return Ticker(
            symbol=row["symbol"],
            last_price=row["last_price"],
            bid=row["bid"],
            ask=row["ask"],
            high_24h=row["high_24h"],
            low_24h=row["low_24h"],
            volume_24h=row["volume_24h"],
            change_24h_pct=row["change_24h_pct"],
            timestamp=datetime.fromisoformat(row["updated_at"]),
        )

    async def save_orderbook(self, symbol: str, bids: list, asks: list) -> None:
        """Save an orderbook snapshot.

        Args:
            symbol: Trading pair.
            bids: List of [price, qty] pairs.
            asks: List of [price, qty] pairs.
        """
        await self._db.execute(
            """
            INSERT INTO orderbook_snapshots (symbol, bids, asks, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, json.dumps(bids), json.dumps(asks), now_utc().isoformat()),
        )
