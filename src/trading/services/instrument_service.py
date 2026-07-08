"""Instrument info service: fetches and caches trading pair rules from Bybit.

Caches instrument info in memory since it rarely changes, avoiding
repeated API calls for order validation.
"""

import time
from typing import Any

from src.core.decorators import retry, timed
from src.core.logging import get_logger
from src.core.utils import decimals_for_tick, round_price, round_qty
from src.trading.client import BybitClient
from src.trading.models.instrument import InstrumentInfo

log = get_logger("trading")

# Cache TTL: 1 hour (instrument specs change very rarely)
CACHE_TTL_SECONDS = 3600


class InstrumentService:
    """Service for fetching and caching trading instrument specifications.

    Args:
        client: Connected BybitClient instance.
    """

    def __init__(self, client: BybitClient) -> None:
        self._client = client
        self._cache: dict[str, InstrumentInfo] = {}
        self._cache_time: float = 0.0

    def _is_cache_valid(self) -> bool:
        """Check if the instrument cache is still fresh."""
        return bool(self._cache) and (time.monotonic() - self._cache_time) < CACHE_TTL_SECONDS

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """Fetch trading rules for a specific instrument.

        Returns cached data if available and fresh.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").

        Returns:
            InstrumentInfo with lot sizes, tick sizes, leverage limits.
        """
        if symbol in self._cache and self._is_cache_valid():
            return self._cache[symbol]

        result = await self._client.call(
            "get_instruments_info",
            category="linear",
            symbol=symbol,
        )

        items = result.get("list", [])
        if not items:
            from src.core.exceptions import MarketDataError
            raise MarketDataError(
                f"No instrument info found for {symbol}",
                details={"symbol": symbol},
            )

        info = InstrumentInfo.from_bybit(items[0])
        self._cache[symbol] = info
        self._cache_time = time.monotonic()

        log.debug(
            "Instrument info loaded: {s} (qty_step={qs}, tick={t})",
            s=symbol,
            qs=info.qty_step,
            t=info.price_tick,
        )
        return info

    @retry(max_attempts=2, delay=2.0)
    @timed
    async def get_all_instruments(self) -> list[InstrumentInfo]:
        """Fetch all available USDT perpetual instruments.

        Returns:
            List of InstrumentInfo for all linear (USDT perp) contracts.
        """
        result = await self._client.call(
            "get_instruments_info",
            category="linear",
        )

        items = result.get("list", [])
        instruments = []
        for item in items:
            info = InstrumentInfo.from_bybit(item)
            self._cache[info.symbol] = info
            instruments.append(info)

        self._cache_time = time.monotonic()
        log.info("Loaded {n} instruments", n=len(instruments))
        return instruments

    def price_decimals(self, symbol: str) -> int | None:
        """Exact display decimal places for *symbol* from the cached tick size.

        Synchronous cache read (mirrors :meth:`validate_order_params`); does
        NOT trigger an API call. Returns ``None`` on a cache miss or a
        non-positive tick so display callers fall back to magnitude-aware
        precision. Open/traded symbols are reliably cached because order
        placement awaits :meth:`get_instrument_info` first, so live
        positions render at exact exchange precision.

        This is the resolver injected into the canonical ``PriceFormatter``;
        it is deliberately the only coupling between the display layer and
        instrument metadata.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").

        Returns:
            Decimal places (int) when the tick size is known, else ``None``.
        """
        info = self._cache.get(symbol)
        if info is None or info.price_tick <= 0:
            return None
        return decimals_for_tick(info.price_tick)

    def validate_order_params(
        self,
        symbol: str,
        qty: float,
        price: float | None = None,
    ) -> list[str]:
        """Validate order parameters against instrument rules.

        Uses cached instrument data. Returns empty list if valid.

        Args:
            symbol: Trading pair.
            qty: Order quantity.
            price: Order price (None for market orders).

        Returns:
            List of validation error strings. Empty means valid.
        """
        info = self._cache.get(symbol)
        if info is None:
            return [f"No instrument info cached for {symbol}. Call get_instrument_info() first."]

        issues: list[str] = []

        if info.status != "Trading":
            issues.append(f"{symbol} is not available for trading (status: {info.status})")

        if qty < info.min_qty:
            issues.append(f"Quantity {qty} below minimum {info.min_qty}")

        if qty > info.max_qty:
            issues.append(f"Quantity {qty} above maximum {info.max_qty}")

        if info.qty_step > 0:
            rounded = round_qty(qty, info.qty_step)
            if abs(rounded - qty) > info.qty_step * 0.01:
                issues.append(
                    f"Quantity {qty} does not align with step size {info.qty_step}. "
                    f"Use {rounded} instead."
                )

        if price is not None:
            if price < info.min_price:
                issues.append(f"Price {price} below minimum {info.min_price}")
            if price > info.max_price:
                issues.append(f"Price {price} above maximum {info.max_price}")
            if info.price_tick > 0:
                rounded = round_price(price, info.price_tick)
                if abs(rounded - price) > info.price_tick * 0.01:
                    issues.append(
                        f"Price {price} does not align with tick size {info.price_tick}. "
                        f"Use {rounded} instead."
                    )

        if price is not None and info.min_notional > 0:
            notional = qty * price
            if notional < info.min_notional:
                issues.append(
                    f"Notional value {notional:.2f} USDT below minimum {info.min_notional}"
                )

        return issues

    def clear_cache(self) -> None:
        """Clear the instrument cache."""
        self._cache.clear()
        self._cache_time = 0.0
