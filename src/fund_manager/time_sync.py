"""M15 — Time-of-day allocation synchronization.

Returns a sizing multiplier based on UTC hour and day-of-week to
match capital deployment with market activity:

  Kill zones (high liquidity):
    - London open  (07:00-09:00 UTC): 1.1 - 1.2
    - NY session   (13:00-16:00 UTC): 1.1 - 1.2

  Active hours (06:00-20:00 UTC): 1.0
  Off hours    (20:00-06:00 UTC): 0.6 - 0.8
  Weekends     (Saturday-Sunday):  0.4
"""

from datetime import datetime, timezone

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import TimeHorizon  # noqa: F401

log = get_logger("fund_manager")

# Kill-zone definitions: (start_hour, end_hour, multiplier)
_KILL_ZONES: list[tuple[int, int, float]] = [
    (7, 9, 1.2),    # London open
    (13, 16, 1.15),  # NY session
]

# Off-hours taper: (start_hour, end_hour, multiplier)
_OFF_HOURS: list[tuple[int, int, float]] = [
    (20, 22, 0.8),   # Early off-hours
    (22, 24, 0.7),   # Late evening
    (0, 4, 0.6),     # Deep night
    (4, 6, 0.7),     # Pre-dawn
]

_WEEKEND_MULTIPLIER = 0.4
_ACTIVE_MULTIPLIER = 1.0


class TimeSync:
    """Time-of-day allocation synchronizer."""

    def __init__(self, settings=None) -> None:
        self._settings = settings

    @staticmethod
    def get_multiplier(now: datetime | None = None) -> float:
        """Return a sizing multiplier based on current UTC time.

        Args:
            now: Optional datetime override (for testing). Defaults to UTC now.

        Returns:
            Multiplier between 0.4 and 1.2.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Weekend check (Saturday=5, Sunday=6)
        if now.weekday() >= 5:
            log.debug("TimeSync: weekend detected, multiplier={m}", m=_WEEKEND_MULTIPLIER)
            return _WEEKEND_MULTIPLIER

        hour = now.hour

        # Check kill zones first (highest priority)
        for start, end, mult in _KILL_ZONES:
            if start <= hour < end:
                log.debug(
                    "TimeSync: kill zone {s}-{e} UTC, hour={h}, multiplier={m}",
                    s=start, e=end, h=hour, m=mult,
                )
                return mult

        # Check off-hours
        for start, end, mult in _OFF_HOURS:
            if start <= hour < end:
                log.debug(
                    "TimeSync: off-hours {s}-{e} UTC, hour={h}, multiplier={m}",
                    s=start, e=end, h=hour, m=mult,
                )
                return mult

        # Active hours (default)
        log.debug("TimeSync: active hours, hour={h}, multiplier={m}", h=hour, m=_ACTIVE_MULTIPLIER)
        return _ACTIVE_MULTIPLIER

    @staticmethod
    def is_kill_zone(now: datetime | None = None) -> bool:
        """Check if current time falls within a kill zone.

        Args:
            now: Optional datetime override.

        Returns:
            True if in a kill zone.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        hour = now.hour
        return any(start <= hour < end for start, end, _ in _KILL_ZONES)

    @staticmethod
    def is_off_hours(now: datetime | None = None) -> bool:
        """Check if current time is off-hours.

        Args:
            now: Optional datetime override.

        Returns:
            True if off-hours or weekend.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return True
        hour = now.hour
        return any(start <= hour < end for start, end, _ in _OFF_HOURS)
