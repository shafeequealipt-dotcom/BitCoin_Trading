"""M5 — Time-horizon capital pools.

Three capital pools partitioned by expected trade duration:
- FAST  (<60 min):   40% of active trading capital
- MEDIUM (60-480 min): 35%
- SLOW  (>480 min):  25%

Each pool tracks how much capital is currently locked so that
over-allocation in a single horizon is prevented.
"""

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import TimeHorizon

log = get_logger("fund_manager")

# Pool allocation percentages keyed by horizon
_POOL_PCT: dict[TimeHorizon, float] = {
    TimeHorizon.FAST: 0.40,
    TimeHorizon.MEDIUM: 0.35,
    TimeHorizon.SLOW: 0.25,
}

# Duration thresholds in minutes
_FAST_MAX_MINUTES = 60
_SLOW_MIN_MINUTES = 480


class TimePoolManager:
    """Manages capital allocation across three time-horizon pools."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        # Tracks how much capital is currently locked per horizon
        self._locked: dict[TimeHorizon, float] = {
            TimeHorizon.FAST: 0.0,
            TimeHorizon.MEDIUM: 0.0,
            TimeHorizon.SLOW: 0.0,
        }

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    @staticmethod
    def classify(expected_hold_minutes: int | float) -> TimeHorizon:
        """Classify a trade into a time horizon based on expected hold time.

        Args:
            expected_hold_minutes: Expected hold duration in minutes.

        Returns:
            The matching TimeHorizon enum value.
        """
        if expected_hold_minutes < _FAST_MAX_MINUTES:
            return TimeHorizon.FAST
        if expected_hold_minutes <= _SLOW_MIN_MINUTES:
            return TimeHorizon.MEDIUM
        return TimeHorizon.SLOW

    # ------------------------------------------------------------------
    # Pool queries
    # ------------------------------------------------------------------

    def get_available(self, horizon: TimeHorizon, trading_capital: float) -> float:
        """Return the capital still available in the given pool.

        Args:
            horizon: Which time-horizon pool to query.
            trading_capital: Current total trading capital (active pool).

        Returns:
            Available capital in USD for this horizon.
        """
        pool_total = trading_capital * _POOL_PCT[horizon]
        available = max(0.0, pool_total - self._locked[horizon])
        log.debug(
            "TimePools.get_available: horizon={hz}, pool_total={pt:.2f}, "
            "locked={lk:.2f}, available={av:.2f}",
            hz=horizon.value, pt=pool_total, lk=self._locked[horizon], av=available,
        )
        return available

    def get_pool_pct(self, horizon: TimeHorizon) -> float:
        """Return the allocation percentage for a horizon (0.0-1.0)."""
        return _POOL_PCT[horizon]

    # ------------------------------------------------------------------
    # Capital lock / release
    # ------------------------------------------------------------------

    def on_capital_locked(self, horizon: TimeHorizon, amount: float) -> None:
        """Reduce available capital in a pool when a trade is opened.

        Args:
            horizon: The time-horizon pool the trade belongs to.
            amount: Capital committed in USD.
        """
        self._locked[horizon] += amount
        log.info(
            "TimePools: locked {amt:.2f} USD in {hz} pool (total locked={total:.2f})",
            amt=amount, hz=horizon.value, total=self._locked[horizon],
        )

    def on_capital_released(self, horizon: TimeHorizon, amount: float) -> None:
        """Restore available capital in a pool when a trade is closed.

        Args:
            horizon: The time-horizon pool the trade belonged to.
            amount: Capital released in USD.
        """
        self._locked[horizon] = max(0.0, self._locked[horizon] - amount)
        log.info(
            "TimePools: released {amt:.2f} USD from {hz} pool (total locked={total:.2f})",
            amt=amount, hz=horizon.value, total=self._locked[horizon],
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def snapshot(self, trading_capital: float) -> dict:
        """Return a diagnostic snapshot of all pools."""
        return {
            horizon.value: {
                "allocation_pct": _POOL_PCT[horizon],
                "pool_total": trading_capital * _POOL_PCT[horizon],
                "locked": self._locked[horizon],
                "available": self.get_available(horizon, trading_capital),
            }
            for horizon in TimeHorizon
        }
