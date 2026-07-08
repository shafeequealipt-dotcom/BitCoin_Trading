"""Kline collector — stores confirmed 1-minute candles and backfills gaps.

Receives kline messages from the WebSocket manager. Only saves candles
where confirm=true (closed candles). Buffers writes and flushes in batches.
On startup, backfills any gaps from Bybit REST API.
"""

import asyncio
import time
from typing import Any

from pybit.unified_trading import HTTP

from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("collector.kline")

FLUSH_INTERVAL = 5  # seconds between buffer flushes
FLUSH_THRESHOLD = 50  # flush if buffer reaches this size
BACKFILL_MAX_CANDLES = 1000  # Bybit max per request


class KlineCollector:
    """Collects and stores confirmed 1-minute klines.

    Args:
        db: Connected DatabaseManager instance.
        config: Shadow configuration.
    """

    def __init__(self, db: DatabaseManager, config: ShadowConfig) -> None:
        self._db = db
        self._config = config
        self._buffer: list[tuple[str, int, float, float, float, float, float, float]] = []
        self._lock = asyncio.Lock()
        self._total_saved = 0
        self._pybit_client = HTTP(testnet=False)

    def on_kline(self, symbol: str, kline: dict[str, Any]) -> None:
        """Callback for kline messages from WebSocket manager.

        Only buffers candles where confirm=true (closed candles).
        """
        if not kline.get("confirm", False):
            return

        try:
            row = (
                symbol,
                int(kline["start"]),
                float(kline["open"]),
                float(kline["high"]),
                float(kline["low"]),
                float(kline["close"]),
                float(kline["volume"]),
                float(kline.get("turnover", 0)),
            )
            # Thread-safe append (GIL protects list.append)
            self._buffer.append(row)
        except (KeyError, ValueError) as e:
            log.warning("Bad kline data for {sym}: {err}", sym=symbol, err=e)

    async def run(self) -> None:
        """Flush buffer to database periodically."""
        log.info("Kline collector started")
        while True:
            try:
                await asyncio.sleep(FLUSH_INTERVAL)
                await self._flush()
            except asyncio.CancelledError:
                await self._flush()  # Final flush on shutdown
                log.info(
                    "Kline collector stopped. Total saved: {n:,}",
                    n=self._total_saved,
                )
                return
            except Exception as e:
                log.error("Kline flush error: {err}", err=e)

    async def _flush(self) -> None:
        """Write buffered klines to database."""
        if not self._buffer:
            return

        # Swap buffer atomically
        async with self._lock:
            batch = self._buffer[:]
            self._buffer.clear()

        if not batch:
            return

        await self._db.executemany(
            """INSERT OR IGNORE INTO klines
               (symbol, timestamp, open, high, low, close, volume, turnover)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        self._total_saved += len(batch)
        log.debug("Flushed {n} klines (total: {total:,})", n=len(batch), total=self._total_saved)

    async def backfill(self, symbols: list[str]) -> None:
        """Backfill missing klines from Bybit REST API for each symbol.

        Checks the most recent kline timestamp in DB for each symbol.
        If there's a gap of more than 2 minutes, fetches historical
        data to fill it.
        """
        log.info("Starting kline backfill for {n} symbols...", n=len(symbols))
        now_ms = int(time.time() * 1000)
        total_backfilled = 0

        for symbol in symbols:
            try:
                # Find most recent kline in DB
                row = await self._db.fetch_one(
                    "SELECT MAX(timestamp) as last_ts FROM klines WHERE symbol = ?",
                    (symbol,),
                )
                last_ts = row["last_ts"] if row and row["last_ts"] else None

                if last_ts is None:
                    # No data at all — fetch last 200 candles
                    start_ms = now_ms - (200 * 60 * 1000)
                else:
                    gap_minutes = (now_ms - last_ts) / 60000
                    if gap_minutes <= 2:
                        continue  # No gap
                    start_ms = last_ts + 60000  # Next minute after last candle

                # Fetch from Bybit REST (sync, run in thread)
                candles = await asyncio.to_thread(
                    self._fetch_klines_rest, symbol, start_ms, now_ms
                )
                if candles:
                    await self._db.executemany(
                        """INSERT OR IGNORE INTO klines
                           (symbol, timestamp, open, high, low, close, volume, turnover)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        candles,
                    )
                    total_backfilled += len(candles)
                    gap_str = f"{(now_ms - start_ms) / 60000:.0f}min"
                    log.info(
                        "Backfilled {n} klines for {sym} (gap: {gap})",
                        n=len(candles),
                        sym=symbol,
                        gap=gap_str,
                    )
            except Exception as e:
                log.warning("Backfill failed for {sym}: {err}", sym=symbol, err=e)

        log.info("Kline backfill complete: {n:,} candles total", n=total_backfilled)

    def _fetch_klines_rest(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[tuple]:
        """Synchronous REST call to fetch historical klines."""
        all_candles: list[tuple] = []
        current_start = start_ms

        while current_start < end_ms:
            resp = self._pybit_client.get_kline(
                category="linear",
                symbol=symbol,
                interval="1",
                start=current_start,
                end=end_ms,
                limit=BACKFILL_MAX_CANDLES,
            )
            if resp["retCode"] != 0:
                break

            klines = resp["result"]["list"]
            if not klines:
                break

            for k in klines:
                # Bybit returns [start_time, open, high, low, close, volume, turnover]
                all_candles.append((
                    symbol,
                    int(k[0]),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                    float(k[6]) if len(k) > 6 else 0.0,
                ))

            # Move start forward past the oldest returned candle
            # Bybit returns newest first, so last item is the oldest
            oldest_ts = int(klines[-1][0])
            if oldest_ts <= current_start:
                break  # No progress
            current_start = int(klines[0][0]) + 60000

        return all_candles
