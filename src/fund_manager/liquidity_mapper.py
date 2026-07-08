"""M21: Liquidity Mapper.

Checks 24-hour trading volume to ensure sufficient liquidity before
entering a position. Rejects illiquid pairs and scales down for
below-average volume.
"""

from src.core.logging import get_logger

log = get_logger("fund_manager")

# ── Volume thresholds ────────────────────────────────────────────────────────
# Reject if 24h volume is less than 30% of normal
REJECT_THRESHOLD_PCT = 30.0
# Reduce size if volume is less than 50% of normal
LOW_THRESHOLD_PCT = 50.0
# Normal volume means full size
NORMAL_THRESHOLD_PCT = 100.0

# Default "normal" volumes per symbol (in quote currency, e.g. USDT)
# These are ballpark figures; in production, compare against rolling averages
DEFAULT_NORMAL_VOLUMES: dict[str, float] = {
    "BTCUSDT": 500_000_000.0,
    "ETHUSDT": 300_000_000.0,
    "SOLUSDT": 100_000_000.0,
    "XRPUSDT": 80_000_000.0,
    "DOGEUSDT": 50_000_000.0,
    "ADAUSDT": 40_000_000.0,
    "DOTUSDT": 30_000_000.0,
    "AVAXUSDT": 30_000_000.0,
}

# Fallback normal volume for unknown symbols
DEFAULT_VOLUME = 20_000_000.0


class LiquidityMapper:
    """Liquidity assessment for trade entry decisions.

    Checks 24-hour volume against expected normal volume to determine
    if a symbol is liquid enough to trade and what size adjustment is needed.

    Args:
        services: Dict containing at least 'market_service'.
    """

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._services = services or {}

    async def is_liquid_enough(self, symbol: str) -> bool:
        """Check if a symbol has sufficient 24h volume for trading.

        Rejects if volume is below 30% of normal.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").

        Returns:
            True if volume is sufficient, False if too illiquid.
        """
        try:
            volume_pct = await self._get_volume_pct(symbol)

            if volume_pct < REJECT_THRESHOLD_PCT:
                log.warning(
                    "Symbol {symbol} rejected: volume at {pct:.0f}% of normal "
                    "(threshold: {threshold}%)",
                    symbol=symbol,
                    pct=volume_pct,
                    threshold=REJECT_THRESHOLD_PCT,
                )
                return False

            log.debug(
                "Symbol {symbol} liquidity OK: volume at {pct:.0f}% of normal",
                symbol=symbol,
                pct=volume_pct,
            )
            return True

        except Exception:
            log.warning(
                "Liquidity check failed for {symbol}, allowing trade by default",
                symbol=symbol,
            )
            return True

    async def get_multiplier(self, symbol: str) -> float:
        """Get a sizing multiplier based on current liquidity.

        - Volume < 50% of normal -> 0.5
        - Volume 50-100% of normal -> 0.8
        - Volume >= 100% of normal -> 1.0

        Args:
            symbol: Trading pair.

        Returns:
            Liquidity multiplier for position sizing.
        """
        try:
            volume_pct = await self._get_volume_pct(symbol)

            if volume_pct < LOW_THRESHOLD_PCT:
                mult = 0.5
            elif volume_pct < NORMAL_THRESHOLD_PCT:
                mult = 0.8
            else:
                mult = 1.0

            log.debug(
                "Liquidity multiplier for {symbol}: volume={pct:.0f}% -> {mult}",
                symbol=symbol,
                pct=volume_pct,
                mult=mult,
            )
            return mult

        except Exception:
            log.warning(
                "Liquidity multiplier failed for {symbol}, defaulting to 1.0",
                symbol=symbol,
            )
            return 1.0

    async def _get_volume_pct(self, symbol: str) -> float:
        """Get current 24h volume as a percentage of normal volume.

        Args:
            symbol: Trading pair.

        Returns:
            Volume as percentage of normal (100 = normal, 50 = half).
        """
        market_service = self._services.get("market_service")
        if market_service is None:
            return 100.0  # Assume normal if no service

        ticker = await market_service.get_ticker(symbol)
        current_volume = ticker.volume_24h

        # Use last price to convert volume to USDT value if needed
        # ticker.volume_24h from Bybit is already in base currency units
        volume_usd = current_volume * ticker.last_price

        normal_volume = DEFAULT_NORMAL_VOLUMES.get(symbol, DEFAULT_VOLUME)

        if normal_volume <= 0:
            return 100.0

        return (volume_usd / normal_volume) * 100.0
