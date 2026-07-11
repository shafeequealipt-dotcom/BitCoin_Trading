"""Coin selector — picks the top N USDT perpetual pairs by 24h volume.

Uses Bybit's public REST API (no auth needed) via pybit. Runs synchronously
wrapped in asyncio.to_thread() since it only executes at startup and daily.

Force-included symbols (``FORCE_INCLUDE``) are always tracked regardless of
volume rank — used to keep the shadow and trading-bot universes in sync.
"""

import asyncio
from datetime import datetime, timezone

from pybit.unified_trading import HTTP

from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("collector.coins")

FORCE_INCLUDE: set[str] = {
    "METAUSDT", "SPCXUSDT", "UNIUSDT", "MMTUSDT", "XPINUSDT",
    "SYRUPUSDT", "XLMUSDT", "WIFUSDT", "WLDUSDT",
    "AVGOUSDT", "SKLUSDT",
}


class CoinSelector:
    """Selects top coins by 24h trading volume from Bybit mainnet.

    Args:
        db: Connected DatabaseManager instance.
        config: Shadow configuration.
    """

    def __init__(self, db: DatabaseManager, config: ShadowConfig) -> None:
        self._db = db
        self._config = config
        self._client = HTTP(testnet=False)

    async def select_top_coins(self, count: int = 100) -> list[str]:
        """Fetch top N USDT perpetual pairs by 24h turnover.

        Args:
            count: Number of coins to select.

        Returns:
            List of symbol strings (e.g., ["BTCUSDT", "ETHUSDT", ...]).
        """
        try:
            symbols = await asyncio.to_thread(self._fetch_and_rank, count)
            await self._save_to_db(symbols)
            top5 = ", ".join(symbols[:5])
            log.info(
                "Selected {n} coins. Top 5: {top5}",
                n=len(symbols),
                top5=top5,
            )
            return symbols
        except Exception as e:
            log.error("Coin selection failed: {err}. Falling back to cached list.", err=e)
            return await self._get_cached_coins()

    def _fetch_and_rank(self, count: int) -> list[str]:
        """Synchronous Bybit API calls — runs in thread pool.

        1. Get all linear instruments with Trading status
        2. Get tickers with 24h turnover
        3. Rank by turnover, return top N
        """
        # Step 1: Get all active USDT perpetual instruments
        instruments_resp = self._client.get_instruments_info(
            category="linear",
        )
        if instruments_resp["retCode"] != 0:
            raise RuntimeError(f"instruments-info failed: {instruments_resp['retMsg']}")

        active_symbols: set[str] = set()
        for inst in instruments_resp["result"]["list"]:
            if (
                inst["status"] == "Trading"
                and inst["quoteCoin"] == "USDT"
                and inst["contractType"] == "LinearPerpetual"
            ):
                active_symbols.add(inst["symbol"])

        log.debug("Active USDT perpetual instruments: {n}", n=len(active_symbols))

        # Step 2: Get tickers with volume data
        tickers_resp = self._client.get_tickers(category="linear")
        if tickers_resp["retCode"] != 0:
            raise RuntimeError(f"tickers failed: {tickers_resp['retMsg']}")

        # Step 3: Filter and rank by 24h turnover
        ranked: list[tuple[str, float]] = []
        for ticker in tickers_resp["result"]["list"]:
            symbol = ticker["symbol"]
            if symbol in active_symbols:
                turnover = float(ticker.get("turnover24h", 0))
                ranked.append((symbol, turnover))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return [symbol for symbol, _ in ranked[:count]]

    async def _save_to_db(self, symbols: list[str]) -> None:
        """Save selected coins to tracked_coins table.

        New coins are inserted. Coins that fell out of the top N are
        marked is_active=0 (not deleted — preserve history). Force-
        included coins are unconditionally reactivated.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Mark all existing coins as inactive first
        await self._db.execute("UPDATE tracked_coins SET is_active = 0")

        # Upsert selected coins as active with rank
        params = [
            (symbol, now, rank + 1, 1)
            for rank, symbol in enumerate(symbols)
        ]

        # Ensure force-included coins are always tracked
        all_active = list(symbols)
        for sym in FORCE_INCLUDE:
            if sym not in all_active:
                all_active.append(sym)

        params = [
            (symbol, now, rank + 1, 1)
            for rank, symbol in enumerate(all_active)
        ]
        await self._db.executemany(
            """INSERT INTO tracked_coins (symbol, added_at, rank_by_volume, is_active)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                   rank_by_volume = excluded.rank_by_volume,
                   is_active = 1""",
            params,
        )

    async def _get_cached_coins(self) -> list[str]:
        """Fallback: return previously cached coins from database."""
        rows = await self._db.fetch_all(
            "SELECT symbol FROM tracked_coins WHERE is_active = 1 ORDER BY rank_by_volume"
        )
        symbols = [r["symbol"] for r in rows]
        if symbols:
            log.info("Using {n} cached coins from database", n=len(symbols))
        else:
            log.warning("No cached coins found — database is empty")
        return symbols
