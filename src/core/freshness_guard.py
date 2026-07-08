"""Data Freshness Guard — prevents trading on stale data.

Checks that market data (tickers, klines) is recent enough before
allowing trade decisions. Prevents the system from executing trades
based on outdated prices.
"""

import time

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("core")

# Maximum acceptable data ages (seconds)
MAX_TICKER_AGE = 120
MAX_KLINE_AGE = 300


class FreshnessGuard:
    """Checks whether market data is fresh enough for trading decisions."""

    def __init__(self, db, services: dict | None = None) -> None:
        self.db = db
        self._services = services or {}

    async def is_fresh(self, symbol: str) -> tuple[bool, str]:
        """Check if data for a symbol is fresh enough for trading.

        Returns:
            (is_fresh, reason) — reason explains staleness if not fresh.
        """
        # Check ticker freshness via MarketService cache
        market_svc = self._services.get("market_service") or self._services.get("market")
        if market_svc and hasattr(market_svc, "_ticker_cache"):
            cached = market_svc._ticker_cache.get(symbol)
            if not cached:
                return False, f"No cached ticker for {symbol}"
            cache_time, _ = cached
            age = time.time() - cache_time
            if age > MAX_TICKER_AGE:
                log.warning(f"FRESH_BLOCK | sym={symbol} ticker_age={age:.0f}s limit={MAX_TICKER_AGE}s | {ctx()}")
                return False, f"Ticker {age:.0f}s old (max {MAX_TICKER_AGE}s)"

        # Check kline freshness via DB
        if self.db:
            try:
                row = await self.db.fetch_one(
                    "SELECT timestamp FROM klines WHERE symbol = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (symbol,),
                )
                if not row:
                    return False, f"No kline data for {symbol}"
            except Exception:
                pass

        # Check TA cache freshness
        ta_cache = self._services.get("ta_cache")
        if ta_cache and hasattr(ta_cache, "is_fresh"):
            if not ta_cache.is_fresh(symbol, "5", 120):
                log.debug(
                    "FreshnessGuard: TA stale for {sym}", sym=symbol,
                )

        log.debug(f"FRESH_OK | sym={symbol} | {ctx()}")
        return True, "OK"

    async def filter_fresh_symbols(self, symbols: list[str]) -> list[str]:
        """Return only symbols with fresh data."""
        fresh = []
        for sym in symbols:
            ok, reason = await self.is_fresh(sym)
            if ok:
                fresh.append(sym)
            else:
                log.debug("Stale data for {s}: {r}", s=sym, r=reason)
        return fresh
