"""Funding rate collector — stores funding rates every 8 hours.

Reads funding rate data from the WebSocket ticker cache (which includes
fundingRate and nextFundingTime fields). Funding settles at 00:00, 08:00,
and 16:00 UTC on Bybit.
"""

import asyncio
import time
from datetime import datetime, timezone

from src.collector.websocket import WebSocketManager
from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("collector.funding")

FUNDING_HOURS = [0, 8, 16]  # UTC hours when funding settles


class FundingCollector:
    """Collects funding rates at settlement times from the ticker cache.

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
        self._ws = ws_manager
        self._total_saved = 0

    async def run(self) -> None:
        """Main loop — save funding rates at each settlement time."""
        log.info("Funding collector started")

        while True:
            try:
                # Calculate seconds until next funding time
                wait = _seconds_until_next_funding()
                log.info(
                    "Next funding collection in {min:.0f} minutes",
                    min=wait / 60,
                )
                await asyncio.sleep(wait)

                # Collect after settlement
                await asyncio.sleep(60)  # Wait 1 min after settlement for data to propagate
                await self._collect()

            except asyncio.CancelledError:
                log.info(
                    "Funding collector stopped. Total saved: {n:,}",
                    n=self._total_saved,
                )
                return
            except Exception as e:
                log.error("Funding collection error: {err}", err=e)
                await asyncio.sleep(60)  # Retry after 1 minute

    async def collect_now(self) -> None:
        """Force an immediate funding rate collection (for startup)."""
        await self._collect()

    async def _collect(self) -> None:
        """Read funding rates from ticker cache and save to DB."""
        now_ms = int(time.time() * 1000)
        rows: list[tuple] = []

        for symbol, ticker in self._ws._latest_tickers.items():
            rate = ticker.get("fundingRate")
            if rate is None or rate == "":
                continue
            try:
                rows.append((symbol, now_ms, float(rate)))
            except (ValueError, TypeError):
                continue

        if not rows:
            log.warning("No funding rate data available in ticker cache")
            return

        await self._db.executemany(
            "INSERT OR IGNORE INTO funding_rates (symbol, timestamp, funding_rate) VALUES (?, ?, ?)",
            rows,
        )
        self._total_saved += len(rows)
        avg_rate = sum(r[2] for r in rows) / len(rows) if rows else 0
        log.info(
            "Funding rates saved: {n} coins, avg rate {rate:.4f}%",
            n=len(rows),
            rate=avg_rate * 100,
        )


def _seconds_until_next_funding() -> float:
    """Calculate seconds until the next funding settlement time."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour

    # Find next funding hour
    next_hour = None
    for h in FUNDING_HOURS:
        if h > current_hour:
            next_hour = h
            break

    if next_hour is None:
        # Past 16:00 UTC — next is 00:00 tomorrow
        next_hour = 0
        next_time = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Add 1 day
        from datetime import timedelta
        next_time += timedelta(days=1)
    else:
        next_time = now.replace(
            hour=next_hour, minute=0, second=0, microsecond=0
        )

    delta = (next_time - now).total_seconds()
    return max(delta, 0)
