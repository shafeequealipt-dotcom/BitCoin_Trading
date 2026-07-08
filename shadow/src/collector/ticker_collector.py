"""Ticker collector — saves periodic ticker snapshots to database.

Every N seconds (default 60), takes a snapshot of all latest tickers
from the WebSocket manager's in-memory cache and batch-inserts into
the ticker_snapshots table.
"""

import asyncio
import time

from src.collector.websocket import WebSocketManager
from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("collector.ticker")

STALE_THRESHOLD = 300  # 5 minutes — skip coins with no recent tick


class TickerCollector:
    """Periodically snapshots ticker data from the WS cache to database.

    Args:
        db: Connected DatabaseManager instance.
        config: Shadow configuration.
        ws_manager: WebSocket manager with live ticker cache.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: ShadowConfig,
        ws_manager: WebSocketManager,
    ) -> None:
        self._db = db
        self._interval = config.collector.ticker_snapshot_interval
        self._ws = ws_manager
        self._total_snapshots = 0

    async def run(self) -> None:
        """Main loop — snapshot tickers at configured interval."""
        log.info("Ticker collector started (interval: {sec}s)", sec=self._interval)
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._snapshot()
            except asyncio.CancelledError:
                log.info(
                    "Ticker collector stopped. Total snapshots: {n:,}",
                    n=self._total_snapshots,
                )
                return
            except Exception as e:
                log.error("Ticker snapshot error: {err}", err=e)

    async def _snapshot(self) -> None:
        """Read all tickers from cache and batch-insert to DB."""
        now_ms = int(time.time() * 1000)
        now = time.time()
        rows: list[tuple] = []
        skipped = 0

        for symbol, ticker in self._ws._latest_tickers.items():
            # Skip stale tickers
            age = self._ws.get_ticker_age(symbol)
            if age is not None and age > STALE_THRESHOLD:
                skipped += 1
                continue

            rows.append((
                symbol,
                now_ms,
                _float(ticker.get("lastPrice")),
                _float(ticker.get("markPrice")),
                _float(ticker.get("indexPrice")),
                _float(ticker.get("bid1Price")),
                _float(ticker.get("bid1Size")),
                _float(ticker.get("ask1Price")),
                _float(ticker.get("ask1Size")),
                _float(ticker.get("highPrice24h")),
                _float(ticker.get("lowPrice24h")),
                _float(ticker.get("volume24h")),
                _float(ticker.get("turnover24h")),
                _float(ticker.get("price24hPcnt")),
                _float(ticker.get("fundingRate")),
                _float(ticker.get("openInterest")),
                _float(ticker.get("openInterestValue")),
            ))

        if not rows:
            return

        await self._db.executemany(
            """INSERT OR IGNORE INTO ticker_snapshots
               (symbol, timestamp, last_price, mark_price, index_price,
                bid1_price, bid1_size, ask1_price, ask1_size,
                high_24h, low_24h, volume_24h, turnover_24h,
                price_change_24h_pct, funding_rate,
                open_interest, open_interest_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        self._total_snapshots += len(rows)
        if skipped > 0:
            log.debug("Ticker snapshot: {skipped} stale coins skipped", skipped=skipped)
        log.info(
            "Ticker snapshot: {n}/{total} coins saved{skip}",
            n=len(rows),
            total=len(rows) + skipped,
            skip=f" ({skipped} stale skipped)" if skipped else "",
        )


def _float(val) -> float | None:
    """Safely convert to float, returning None on failure."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
