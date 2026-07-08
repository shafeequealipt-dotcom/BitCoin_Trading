"""M11: Capital Velocity Tracker.

Monitors how actively capital is being deployed by tracking the ratio of
daily traded volume to total trading capital. Prevents over-trading and
identifies under-utilization.
"""

from datetime import date

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import CapitalVelocity

log = get_logger("fund_manager")

# ── Velocity categories ─────────────────────────────────────────────────────
LAZY_THRESHOLD = 0.5       # Below this: capital is underutilized
HEALTHY_LOW = 0.5          # Healthy range lower bound
HEALTHY_HIGH = 2.0         # Healthy range upper bound
OVERWORKED_THRESHOLD = 4.0 # Above this: extreme over-trading

# Target velocity for optimal capital usage
TARGET_VELOCITY = 1.5


class CapitalVelocityTracker:
    """Tracks capital deployment velocity (traded volume / trading capital).

    Maintains daily traded volume and provides multipliers to scale
    position sizes based on how actively capital is being used.
    """

    def __init__(self, settings=None, db=None) -> None:
        self._today_volume: float = 0.0
        self._current_date: date = date.today()
        self._trading_capital: float = 0.0

    async def get_current(self, trading_capital: float = 0.0) -> CapitalVelocity:
        """Calculate current capital velocity.

        Velocity = traded_volume_today / trading_capital

        Args:
            trading_capital: Current trading capital. If 0, uses last known.

        Returns:
            CapitalVelocity dataclass with current metrics.
        """
        self._check_day_rollover()

        if trading_capital > 0:
            self._trading_capital = trading_capital

        if self._trading_capital <= 0:
            return CapitalVelocity(
                current_velocity=0.0,
                target_velocity=TARGET_VELOCITY,
                status="unknown",
                recommendation="Trading capital not set",
            )

        velocity = self._today_volume / self._trading_capital

        status, recommendation = self._classify_velocity(velocity)

        result = CapitalVelocity(
            current_velocity=round(velocity, 3),
            target_velocity=TARGET_VELOCITY,
            status=status,
            recommendation=recommendation,
        )

        log.debug(
            "Capital velocity: {vel:.2f}x (status={status})",
            vel=velocity,
            status=status,
        )

        return result

    def get_multiplier(self, velocity: float) -> float:
        """Get position sizing multiplier based on velocity.

        - Lazy (<0.5x): 1.0 (no penalty for being cautious)
        - Healthy (0.5-2x): 1.0 (normal)
        - Overworked (2-4x): 0.7 (slow down)
        - Extreme (>4x): 0.4 (significant reduction)

        Args:
            velocity: Current velocity ratio.

        Returns:
            Sizing multiplier.
        """
        if velocity > OVERWORKED_THRESHOLD:
            mult = 0.4
            log.warning(
                "Extreme capital velocity: {vel:.2f}x, reducing size to {mult}",
                vel=velocity,
                mult=mult,
            )
        elif velocity > HEALTHY_HIGH:
            mult = 0.7
            log.info(
                "Overworked capital velocity: {vel:.2f}x, reducing size to {mult}",
                vel=velocity,
                mult=mult,
            )
        else:
            mult = 1.0

        return mult

    def on_trade(self, amount: float) -> None:
        """Record a trade to increment today's volume.

        Args:
            amount: Trade notional value in USD.
        """
        self._check_day_rollover()
        self._today_volume += abs(amount)

        log.debug(
            "Trade recorded: {amount:.2f} USD, today total: {total:.2f}",
            amount=abs(amount),
            total=self._today_volume,
        )

    def _check_day_rollover(self) -> None:
        """Reset daily volume counter if the day has changed."""
        today = date.today()
        if today != self._current_date:
            log.info(
                "Day rollover: resetting volume from {vol:.2f}",
                vol=self._today_volume,
            )
            self._today_volume = 0.0
            self._current_date = today

    def _classify_velocity(self, velocity: float) -> tuple[str, str]:
        """Classify velocity into a status and recommendation.

        Args:
            velocity: Current velocity ratio.

        Returns:
            Tuple of (status, recommendation).
        """
        if velocity < LAZY_THRESHOLD:
            return "lazy", "Capital underutilized; look for setups"
        elif velocity <= HEALTHY_HIGH:
            return "healthy", "Good capital velocity"
        elif velocity <= OVERWORKED_THRESHOLD:
            return "overworked", "Slow down; high churn erodes profits"
        else:
            return "extreme", "STOP trading; capital is being churned dangerously"
