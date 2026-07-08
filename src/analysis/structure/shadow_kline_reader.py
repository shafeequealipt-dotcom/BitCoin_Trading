"""Shadow Kline Reader — aggregates Shadow's 1-min candles into the requested timeframe.

Holds a single long-lived read-only ``aiosqlite.Connection`` to the Shadow DB,
opened by :meth:`ShadowKlineReader.connect` at process boot and closed by
:meth:`ShadowKlineReader.close` at shutdown. Queries are serialised through an
internal :class:`asyncio.Lock` so the single connection stays async-safe across
concurrent callers.

The shadow.db file is owned by the Shadow process; this reader is a guest
reader on a WAL database. Only read-side PRAGMAs are applied — writer-side
knobs (``journal_mode``, ``synchronous``, ``foreign_keys``,
``wal_autocheckpoint``, ``journal_size_limit``) are deliberately untouched.
The connection is opened with ``file:<path>?mode=ro`` and an additional
``PRAGMA query_only=ON`` for defence-in-depth.
"""

import asyncio
import time
from datetime import datetime, timezone

import aiosqlite

from src.core.exceptions import DatabaseError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import OHLCV, TimeFrame

log = get_logger("xray")

# Timeframe to milliseconds mapping
TF_MS: dict[str, int] = {
    "1": 60_000,
    "5": 300_000,
    "15": 900_000,
    "60": 3_600_000,
    "240": 14_400_000,
    "D": 86_400_000,
}


class ShadowKlineReader:
    """Reads and aggregates Shadow DB klines into OHLCV objects.

    Holds one long-lived read-only ``aiosqlite.Connection``. Lifecycle:

    - :meth:`connect` opens the connection (idempotent). Called once at
      bootstrap from ``WorkerManager.initialize`` immediately after
      construction.
    - :meth:`close` closes it (idempotent). Called from
      ``WorkerManager.stop_all`` immediately before ``db.disconnect()``.

    All queries serialise through ``self._lock`` so concurrent ``await``
    callers do not interleave on the single connection.

    Args:
        shadow_db_path: Path to Shadow's shadow.db file.
    """

    # Emit XRAY_SHADOW_STATS every N successful get_klines calls. With a
    # 25-symbol structure_worker batch and the trading.db fallback hitting
    # for ~5-7 of those, ~18-20 calls per tick reach this reader. 200
    # calls ≈ 10 ticks ≈ 10 minutes of operation — frequent enough for a
    # 30-min observation window, sparse enough to avoid log spam.
    _STATS_LOG_EVERY_N: int = 200

    def __init__(self, shadow_db_path: str) -> None:
        self._db_path: str = shadow_db_path
        self._db: aiosqlite.Connection | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        # Connection-reuse statistics (Phase 5/6 verification + health probes).
        self._total_calls: int = 0
        self._connection_opens: int = 0
        self._query_executes: int = 0
        self._total_query_ms: float = 0.0
        self._last_stats_emit_calls: int = 0

    async def connect(self) -> None:
        """Open the persistent read-only connection. Idempotent.

        On success, applies five read-side PRAGMAs (query_only, busy_timeout,
        cache_size, temp_store, mmap_size) and emits ``XRAY_SHADOW_CONN_OPEN``
        at INFO. On failure (e.g. shadow.db missing) the connection is left
        ``None`` and ``DatabaseError`` is raised — callers in
        ``WorkerManager.initialize`` treat this as a graceful-degradation
        signal (the reader is simply not registered in ``self._services``).

        Raises:
            DatabaseError: when ``aiosqlite.connect`` or any PRAGMA fails.
        """
        if self._db is not None:
            return
        try:
            uri = f"file:{self._db_path}?mode=ro"
            self._db = await aiosqlite.connect(uri, uri=True, timeout=5)
            # Read-side PRAGMAs only — Shadow owns writer-side knobs.
            # query_only is defence-in-depth on top of URI mode=ro.
            await self._db.execute("PRAGMA query_only=ON")
            # Absorb brief WAL-checkpoint contention from Shadow's writers.
            await self._db.execute("PRAGMA busy_timeout=10000")
            # 64 MiB page cache for the hot-symbol working set. Negative = KiB.
            await self._db.execute("PRAGMA cache_size=-65536")
            # Defensive: keep any temp sort/group state in RAM.
            await self._db.execute("PRAGMA temp_store=MEMORY")
            # Memory-map the first 256 MiB of the 817 MB DB to bypass pread().
            await self._db.execute("PRAGMA mmap_size=268435456")
            self._connection_opens += 1
            log.info(
                f"XRAY_SHADOW_CONN_OPEN | path={self._db_path} mode=ro "
                f"opens={self._connection_opens} | {ctx()}"
            )
        except Exception as e:
            self._db = None
            raise DatabaseError(
                f"Failed to open Shadow DB read-only connection: {e}",
                details={"path": self._db_path},
            )

    async def close(self) -> None:
        """Close the persistent connection. Idempotent.

        Safe to call multiple times and safe to call when ``connect`` was
        never invoked (no-op in both cases). Emits ``XRAY_SHADOW_CONN_CLOSE``
        at INFO with a final stats summary so the post-mortem log carries
        the connection's lifetime totals.
        """
        if self._db is None:
            return
        try:
            await self._db.close()
        finally:
            self._db = None
            log.info(
                f"XRAY_SHADOW_CONN_CLOSE | total_calls={self._total_calls} "
                f"opens={self._connection_opens} executes={self._query_executes} "
                f"total_query_ms={self._total_query_ms:.0f} | {ctx()}"
            )

    def get_stats(self) -> dict[str, int | float]:
        """Return runtime stats snapshot for verification and health probes."""
        return {
            "total_calls": self._total_calls,
            "connection_opens": self._connection_opens,
            "query_executes": self._query_executes,
            "total_query_ms": self._total_query_ms,
            "avg_query_ms": (
                self._total_query_ms / self._query_executes
                if self._query_executes
                else 0.0
            ),
        }

    async def get_klines(
        self,
        symbol: str,
        timeframe: str = "60",
        limit: int = 200,
    ) -> list[OHLCV]:
        """Fetch aggregated klines from Shadow DB.

        Reads raw 1-minute candles via a single async read-only SELECT
        through the persistent connection, then aggregates them in Python
        into the requested timeframe.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT").
            timeframe: Target timeframe value (e.g., "60" for H1).
            limit: Maximum number of aggregated candles to return.

        Returns:
            List of OHLCV objects sorted by timestamp ascending. Empty
            list on missing data, missing connection, or any DB error.
        """
        tf_ms = TF_MS.get(timeframe, 3_600_000)  # default to H1
        self._total_calls += 1
        return await self._aggregate_simple(symbol, timeframe, tf_ms, limit)

    async def _aggregate_simple(
        self,
        symbol: str,
        timeframe: str,
        tf_ms: int,
        limit: int,
    ) -> list[OHLCV]:
        """Async fetch raw 1-min candles + aggregate to the target tf in Python.

        Serialised by ``self._lock`` so the persistent connection is touched
        by exactly one coroutine at a time. Increments query stats and
        triggers periodic XRAY_SHADOW_STATS emission via
        :meth:`_maybe_emit_stats`.
        """
        if self._db is None:
            log.warning(f"XRAY_SHADOW_NOT_CONNECTED | sym={symbol} | {ctx()}")
            return []

        # Fetch enough 1-min candles to produce `limit` aggregated candles.
        # For H1 (60 min), need limit * 60 raw candles.
        minutes_per_bar = tf_ms // 60_000
        raw_limit = limit * minutes_per_bar + minutes_per_bar  # extra for partial last bar

        try:
            t0 = time.perf_counter()
            async with self._lock:
                cursor = await self._db.execute(
                    """
                    SELECT timestamp, open, high, low, close, volume, turnover
                    FROM klines
                    WHERE symbol = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (symbol, raw_limit),
                )
                rows = await cursor.fetchall()
                await cursor.close()
            self._query_executes += 1
            self._total_query_ms += (time.perf_counter() - t0) * 1000
            self._maybe_emit_stats()

            if not rows:
                return []

            # Reverse to chronological order
            rows = list(reversed(rows))

            # Aggregate into buckets. lo/tu spelled out to avoid the E741
            # ambiguous-single-letter-variable lint on the original `l`.
            buckets: dict[int, dict] = {}
            for ts_ms, o, h, lo, c, v, tu in rows:
                bucket = (ts_ms // tf_ms) * tf_ms
                if bucket not in buckets:
                    buckets[bucket] = {
                        "open": o, "high": h, "low": lo, "close": c,
                        "volume": v, "turnover": tu, "ts": bucket,
                    }
                else:
                    b = buckets[bucket]
                    b["high"] = max(b["high"], h)
                    b["low"] = min(b["low"], lo)
                    b["close"] = c  # last close wins
                    b["volume"] += v
                    b["turnover"] += tu

            # Convert to OHLCV objects, sorted chronologically, limited
            sorted_buckets = sorted(buckets.values(), key=lambda x: x["ts"])
            sorted_buckets = sorted_buckets[-limit:]

            tf_enum = TimeFrame.H1  # default
            for tf in TimeFrame:
                if tf.value == timeframe:
                    tf_enum = tf
                    break

            result: list[OHLCV] = []
            for b in sorted_buckets:
                ts_sec = b["ts"] / 1000.0
                dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
                result.append(OHLCV(
                    symbol=symbol,
                    timeframe=tf_enum,
                    timestamp=dt,
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b["volume"],
                    turnover=b["turnover"],
                ))

            return result

        except Exception as e:
            log.debug(
                f"XRAY_SHADOW_AGG_ERR | sym={symbol} err={str(e)[:80]} | {ctx()}"
            )
            return []

    def _maybe_emit_stats(self) -> None:
        """Emit XRAY_SHADOW_STATS once every ``_STATS_LOG_EVERY_N`` calls.

        Internal helper; called from the hot path of ``_aggregate_simple``.
        Cheap (one int comparison) on the no-emit path.
        """
        if self._total_calls - self._last_stats_emit_calls < self._STATS_LOG_EVERY_N:
            return
        self._last_stats_emit_calls = self._total_calls
        avg_ms = (
            self._total_query_ms / self._query_executes
            if self._query_executes
            else 0.0
        )
        log.info(
            f"XRAY_SHADOW_STATS | calls={self._total_calls} "
            f"opens={self._connection_opens} executes={self._query_executes} "
            f"avg_query_ms={avg_ms:.2f} total_query_ms={self._total_query_ms:.0f} "
            f"| {ctx()}"
        )
