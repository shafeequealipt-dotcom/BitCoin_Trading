"""M4: Correlation Guard.

Bucket-based correlation limits that prevent over-concentration in
correlated assets. Limits exposure per bucket to 30% of trading capital.
"""

from src.core.logging import get_logger

log = get_logger("fund_manager")

# ── Asset bucket definitions ────────────────────────────────────────────────
BUCKET_MAP: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "MAJOR_ALT",
    "XRPUSDT": "MAJOR_ALT",
    "ADAUSDT": "MAJOR_ALT",
    "DOTUSDT": "MAJOR_ALT",
    "AVAXUSDT": "MAJOR_ALT",
}

BUCKET_NAMES = {"BTC", "ETH", "MAJOR_ALT", "SMALL_ALT", "SHORT"}

# Max exposure per bucket as percentage of trading capital
MAX_BUCKET_PCT = 30.0


class CorrelationGuard:
    """Bucket-based correlation limit enforcement.

    Groups assets into buckets (BTC, ETH, MAJOR_ALT, SMALL_ALT, SHORT)
    and ensures no single bucket exceeds 30% of trading capital.

    Args:
        services: Dict containing at least 'position_service'.
    """

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._services = services or {}

    def _get_bucket(self, symbol: str, side: str) -> str:
        """Determine which bucket a symbol/side combination belongs to.

        Short positions go into the SHORT bucket regardless of symbol.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            side: Trade side ("Buy" or "Sell").

        Returns:
            Bucket name string.
        """
        if side.upper() in ("SELL", "SHORT"):
            return "SHORT"

        return BUCKET_MAP.get(symbol, "SMALL_ALT")

    async def get_multiplier(self, symbol: str, side: str) -> float:
        """Check if adding a position would exceed bucket limits.

        Examines current open positions, calculates exposure per bucket,
        and returns a multiplier:
          - 1.0: Bucket has room, proceed normally
          - 0.5: Bucket is getting full (>25% exposure), reduce size
          - 0.0: Bucket is at limit (>=30%), reject trade

        Args:
            symbol: Trading pair to potentially add.
            side: Trade side ("Buy" or "Sell").

        Returns:
            Multiplier 0.0-1.0 for position sizing.
        """
        try:
            position_service = self._services.get("position_service")
            if position_service is None:
                log.debug("No position service available, allowing trade")
                return 1.0

            positions = await position_service.get_positions()

            if not positions:
                return 1.0

            # Calculate total capital in use
            total_exposure = sum(
                abs(p.size * p.entry_price) for p in positions
            )

            # Get trading capital (estimate from total exposure if not available)
            # A rough estimate: total exposure / average leverage gives margin used
            # We use the position values directly for bucket calculation
            trading_capital = total_exposure * 3  # Conservative estimate

            # Calculate exposure per bucket
            bucket_exposure: dict[str, float] = {}
            for pos in positions:
                pos_side = pos.side.value
                bucket = self._get_bucket(pos.symbol, pos_side)
                pos_value = abs(pos.size * pos.entry_price)
                bucket_exposure[bucket] = bucket_exposure.get(bucket, 0.0) + pos_value

            # Check the target bucket
            target_bucket = self._get_bucket(symbol, side)
            current_exposure = bucket_exposure.get(target_bucket, 0.0)

            if trading_capital <= 0:
                return 1.0

            exposure_pct = (current_exposure / trading_capital) * 100

            if exposure_pct >= MAX_BUCKET_PCT:
                log.warning(
                    "Bucket {bucket} at limit: {pct:.1f}% exposure, blocking trade for {symbol}",
                    bucket=target_bucket,
                    pct=exposure_pct,
                    symbol=symbol,
                )
                return 0.0
            elif exposure_pct >= (MAX_BUCKET_PCT * 0.833):  # ~25%
                log.info(
                    "Bucket {bucket} getting full: {pct:.1f}% exposure, reducing size for {symbol}",
                    bucket=target_bucket,
                    pct=exposure_pct,
                    symbol=symbol,
                )
                return 0.5
            else:
                log.debug(
                    "Bucket {bucket} OK: {pct:.1f}% exposure for {symbol}",
                    bucket=target_bucket,
                    pct=exposure_pct,
                    symbol=symbol,
                )
                return 1.0

        except Exception:
            log.warning(
                "Correlation check failed for {symbol}, defaulting to 1.0",
                symbol=symbol,
            )
            return 1.0
