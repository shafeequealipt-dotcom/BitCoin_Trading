"""M6: Volatility Scaler.

ATR-based position scaling that adjusts size inversely to current volatility.
When volatility is high, positions are smaller. When low, positions can be larger.
Results are cached per symbol for 5 minutes.
"""

import time

from src.core.logging import get_logger

log = get_logger("fund_manager")

# ATR calculation period
ATR_PERIOD = 14

# Multiplier clamp range
MIN_MULTIPLIER = 0.4
MAX_MULTIPLIER = 2.0

# Cache TTL in seconds
CACHE_TTL = 300.0  # 5 minutes


def _calculate_atr(candles: list, period: int = ATR_PERIOD) -> float:
    """Calculate Average True Range from OHLCV candles.

    Args:
        candles: List of OHLCV dataclass instances.
        period: Number of candles for ATR calculation.

    Returns:
        ATR value, or 0.0 if insufficient data.
    """
    if len(candles) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # Use last `period` true ranges
    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


class VolatilityScaler:
    """ATR-based position size scaling.

    Compares current ATR to a historical "normal" ATR and returns a
    multiplier. High volatility -> smaller positions, low volatility ->
    larger positions.

    Args:
        services: Dict containing at least 'market_service'.
    """

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._services = services or {}
        self._cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, multiplier)
        self._percentile_cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, percentile)

    async def get_multiplier(self, symbol: str) -> float:
        """Get volatility-based sizing multiplier for a symbol.

        Returns normal_ATR / current_ATR, clamped to [0.4, 2.0].
        High current volatility yields a value < 1.0 (reduce size).
        Low current volatility yields a value > 1.0 (increase size).

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").

        Returns:
            Multiplier between 0.4 and 2.0.
        """
        now = time.time()

        # Check cache
        cached = self._cache.get(symbol)
        if cached is not None:
            cache_time, mult = cached
            if (now - cache_time) < CACHE_TTL:
                return mult

        try:
            market_service = self._services.get("market_service")
            if market_service is None:
                log.debug("No market service, volatility multiplier defaulting to 1.0")
                return 1.0

            from src.core.types import TimeFrame

            # Fetch enough candles for both current and historical ATR
            klines = await market_service.get_klines(symbol, TimeFrame.H1, limit=100)

            if len(klines) < 30:
                log.debug(
                    "Insufficient klines for {symbol} ({n} candles), defaulting to 1.0",
                    symbol=symbol,
                    n=len(klines),
                )
                return 1.0

            # Current ATR: last 14 candles
            current_atr = _calculate_atr(klines[-ATR_PERIOD:], ATR_PERIOD)

            # Normal ATR: historical period (candles before the current window)
            historical_candles = klines[:-ATR_PERIOD]
            normal_atr = _calculate_atr(historical_candles, min(len(historical_candles) - 1, 50))

            if current_atr <= 0 or normal_atr <= 0:
                log.debug("ATR is zero for {symbol}, defaulting to 1.0", symbol=symbol)
                return 1.0

            # Inverse relationship: normal / current
            raw_multiplier = normal_atr / current_atr
            multiplier = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, raw_multiplier))

            # Cache result
            self._cache[symbol] = (now, multiplier)

            # Also cache percentile
            percentile = (current_atr / normal_atr) * 100.0
            self._percentile_cache[symbol] = (now, percentile)

            log.debug(
                "Volatility scaler for {symbol}: current_ATR={current:.4f} "
                "normal_ATR={normal:.4f} multiplier={mult:.2f}",
                symbol=symbol,
                current=current_atr,
                normal=normal_atr,
                mult=multiplier,
            )

            return multiplier

        except Exception:
            log.warning(
                "Volatility scaler failed for {symbol}, defaulting to 1.0",
                symbol=symbol,
            )
            return 1.0

    async def get_percentile(self, symbol: str) -> float:
        """Get current ATR as a percentage of normal ATR.

        100 = normal volatility, >100 = above normal, <100 = below normal.

        Args:
            symbol: Trading pair.

        Returns:
            Percentile value (100.0 = normal).
        """
        now = time.time()

        # Check cache
        cached = self._percentile_cache.get(symbol)
        if cached is not None:
            cache_time, pct = cached
            if (now - cache_time) < CACHE_TTL:
                return pct

        # Calling get_multiplier will also populate the percentile cache
        await self.get_multiplier(symbol)

        cached = self._percentile_cache.get(symbol)
        if cached is not None:
            return cached[1]

        return 100.0  # Default to normal
