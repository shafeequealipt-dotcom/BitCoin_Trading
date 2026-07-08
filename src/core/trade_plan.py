"""TradePlan — Stores the Brain's trade plan for monitoring by the Watchdog.

Each trade gets a plan with specific exit conditions:
- Target price (take profit)
- Stop-loss price
- Maximum hold time (timer)
- Trailing stop activation
- Early exit conditions
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar


@dataclass
class TradePlan:
    """A complete trade plan designed by the Brain."""

    symbol: str
    direction: str = "Buy"
    entry_price: float = 0.0
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    max_hold_minutes: int = 120
    early_exit_conditions: list[str] = field(default_factory=list)
    # Bug 4 fix (2026-04-23): trailing no longer activates at +0.5%. Raised
    # default to 1.0% and enforced a hard floor in __post_init__ so the
    # Brain/Strategist cannot emit a lower override. See
    # FOUR_PRICE_AND_PARAMETER_BUGS_FIX.md Bug 4.
    trailing_activation_pct: float = 1.0
    trailing_distance_pct: float = 50.0
    size_tier: str = "medium"
    risk_reward_ratio: float = 2.0
    reasoning: str = ""

    # Runtime tracking (set when trade opens)
    opened_at: float = 0.0
    opened_at_dt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trailing_active: bool = False
    trailing_stop_price: float = 0.0
    peak_price: float = 0.0

    # Bug 4 constants (ClassVar so dataclass treats them as class
    # attributes, not instance fields — they are not part of __init__).
    TRAIL_ACTIVATION_FLOOR_PCT: ClassVar[float] = 1.0    # min % profit before trailing turns on
    TRAIL_MIN_DISTANCE_FRACTION: ClassVar[float] = 0.005  # 0.5% of current price below peak

    def __post_init__(self) -> None:
        # Bug 4: hard floor on trailing activation. Brain/Strategist may
        # emit smaller values for aggressive "lock tiny wins" trades, but
        # empirically those strangle winners (MAGMAUSDT captured 7% of its
        # potential move). 1.0% minimum is enforced regardless of source.
        if self.trailing_activation_pct < self.TRAIL_ACTIVATION_FLOOR_PCT:
            self.trailing_activation_pct = self.TRAIL_ACTIVATION_FLOOR_PCT

    def _min_trail_distance(self, current_price: float) -> float:
        """Bug 4: floor the computed trail distance at 0.5% of price so
        activate_trailing / update_trailing never place the SL tighter
        than the SL gateway's 0.3% min-distance rule and, more
        importantly, survive normal pullback noise.
        """
        return max(0.0, current_price) * self.TRAIL_MIN_DISTANCE_FRACTION

    @property
    def expires_at(self) -> float:
        if self.opened_at == 0:
            return 0
        return self.opened_at + (self.max_hold_minutes * 60)

    @property
    def is_expired(self) -> bool:
        if self.opened_at == 0:
            return False
        return time.time() > self.expires_at

    @property
    def remaining_minutes(self) -> float:
        if self.opened_at == 0:
            return self.max_hold_minutes
        remaining = (self.expires_at - time.time()) / 60
        return max(0, remaining)

    @property
    def age_minutes(self) -> float:
        if self.opened_at == 0:
            return 0
        return (time.time() - self.opened_at) / 60

    def activate_trailing(self, current_price: float) -> None:
        self.trailing_active = True
        self.peak_price = current_price
        profit_from_entry = abs(current_price - self.entry_price)
        trail_distance = profit_from_entry * (self.trailing_distance_pct / 100)
        # Bug 4: never trail tighter than the 0.5% floor.
        trail_distance = max(trail_distance, self._min_trail_distance(current_price))

        if self.direction == "Buy":
            self.trailing_stop_price = current_price - trail_distance
        else:
            self.trailing_stop_price = current_price + trail_distance

    def update_trailing(self, current_price: float) -> None:
        if not self.trailing_active:
            return

        if self.direction == "Buy":
            if current_price > self.peak_price:
                self.peak_price = current_price
                profit = current_price - self.entry_price
                trail = profit * (self.trailing_distance_pct / 100)
                # Bug 4: never trail tighter than the 0.5% floor.
                trail = max(trail, self._min_trail_distance(current_price))
                new_trail = current_price - trail
                self.trailing_stop_price = max(self.trailing_stop_price, new_trail)
        else:
            if current_price < self.peak_price:
                self.peak_price = current_price
                profit = self.entry_price - current_price
                trail = profit * (self.trailing_distance_pct / 100)
                trail = max(trail, self._min_trail_distance(current_price))
                new_trail = current_price + trail
                self.trailing_stop_price = min(self.trailing_stop_price, new_trail)

    def should_trail_exit(self, current_price: float) -> bool:
        if not self.trailing_active:
            return False
        if self.direction == "Buy":
            return current_price <= self.trailing_stop_price
        return current_price >= self.trailing_stop_price

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "target_price": self.target_price,
            "stop_loss_price": self.stop_loss_price,
            "max_hold_minutes": self.max_hold_minutes,
            "early_exit_conditions": self.early_exit_conditions,
            "trailing_activation_pct": self.trailing_activation_pct,
            "trailing_distance_pct": self.trailing_distance_pct,
            "size_tier": self.size_tier,
            "reasoning": self.reasoning[:100],
            "remaining_minutes": round(self.remaining_minutes, 1),
            "trailing_active": self.trailing_active,
            "trailing_stop_price": self.trailing_stop_price,
            "age_minutes": round(self.age_minutes, 1),
        }
