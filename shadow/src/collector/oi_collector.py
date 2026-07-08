"""Open interest collector — stores OI snapshots every 5 minutes.

Reads open interest data from the WebSocket ticker cache (which includes
openInterest and openInterestValue fields) and batch-inserts into the
open_interest_history table.
"""

import asyncio
import time

from src.collector.websocket import WebSocketManager
from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("collector.oi")

STALE_THRESHOLD = 300  # 5 minutes


class OICollector:
    """Periodically snapshots open interest from the ticker cache.

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
        self._interval = config.collector.open_interest_interval
        self._ws = ws_manager
        self._total_saved = 0

    async def run(self) -> None:
        """Main loop — snapshot OI at configured interval."""
        log.info("OI collector started (interval: {sec}s)", sec=self._interval)
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._snapshot()
            except asyncio.CancelledError:
                log.info(
                    "OI collector stopped. Total saved: {n:,}",
                    n=self._total_saved,
                )
                return
            except Exception as e:
                log.error("OI snapshot error: {err}", err=e)

    async def _snapshot(self) -> None:
        """Read OI from ticker cache and batch-insert to DB."""
        now_ms = int(time.time() * 1000)
        rows: list[tuple] = []
        skipped = 0

        for symbol, ticker in self._ws._latest_tickers.items():
            # Skip stale tickers
            age = self._ws.get_ticker_age(symbol)
            if age is not None and age > STALE_THRESHOLD:
                skipped += 1
                continue

            oi = ticker.get("openInterest")
            oi_val = ticker.get("openInterestValue")
            if oi is None or oi == "" or oi_val is None or oi_val == "":
                continue

            try:
                rows.append((
                    symbol,
                    now_ms,
                    float(oi),
                    float(oi_val),
                ))
            except (ValueError, TypeError):
                continue

        if not rows:
            return

        await self._db.executemany(
            """INSERT OR IGNORE INTO open_interest_history
               (symbol, timestamp, open_interest, open_interest_value)
               VALUES (?, ?, ?, ?)""",
            rows,
        )
        self._total_saved += len(rows)
        if skipped > 0:
            log.debug("OI snapshot: {skipped} stale coins skipped", skipped=skipped)
        log.info("OI snapshot: {n} coins saved", n=len(rows))
