"""SENTINEL Deadline Engine — smart expiry based on current PnL.

Replaces the binary close when max_hold_minutes expires with tiered logic:
  Tier 1 (profit):     Close immediately — lock the win.
  Tier 2 (breakeven):  Tighten SL to entry, grant grace period.
  Tier 3 (small loss): Tighten SL to reduced distance, let it recover.
  Tier 4 (big loss):   Cut immediately — the thesis failed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from src.core.logging import get_logger
from src.core.log_context import ctx

log = get_logger("sentinel")


class DeadlineTier(Enum):
    PROFIT = "profit"
    BREAKEVEN = "breakeven"
    SMALL_LOSS = "small_loss"
    BIG_LOSS = "big_loss"


@dataclass
class DeadlineAction:
    """Result from deadline evaluation."""
    tier: DeadlineTier
    should_close: bool
    new_sl: float = 0.0        # 0 means no SL change
    grace_minutes: float = 0.0  # 0 means no grace period
    reason: str = ""


@dataclass
class DeadlineGrace:
    """Tracks grace period for a symbol after breakeven tightening."""
    symbol: str
    granted_at: float = 0.0
    grace_minutes: float = 5.0
    sl_set_to: float = 0.0

    @property
    def is_expired(self) -> bool:
        if self.granted_at == 0:
            return True
        return time.time() > (self.granted_at + self.grace_minutes * 60)


class DeadlineEngine:
    """Evaluates expired trade plans and determines the correct tiered action.

    Configurable thresholds are loaded from SentinelSettings at init time.
    Grace periods are tracked in-memory per symbol.
    """

    def __init__(self, settings=None) -> None:
        self._graces: dict[str, DeadlineGrace] = {}

        # Defaults (overridden by settings)
        self._profit_pct: float = 0.5
        self._breakeven_lower_pct: float = -0.3
        self._small_loss_pct: float = -1.5
        self._grace_minutes: float = 5.0
        self._small_loss_sl_pct: float = 0.5

        if settings:
            self._profit_pct = settings.deadline_profit_pct
            self._breakeven_lower_pct = settings.deadline_breakeven_lower_pct
            self._small_loss_pct = settings.deadline_small_loss_pct
            self._grace_minutes = settings.deadline_grace_minutes
            self._small_loss_sl_pct = settings.deadline_small_loss_sl_pct

    def evaluate(
        self,
        symbol: str,
        pnl_pct: float,
        entry_price: float,
        direction: str,
    ) -> DeadlineAction:
        """Evaluate what to do when a trade plan has expired.

        Args:
            symbol: Position symbol.
            pnl_pct: Current unrealized PnL percentage.
            entry_price: Entry price of the position.
            direction: "Buy" or "Sell".

        Returns:
            DeadlineAction with the appropriate tier and instructions.
        """
        # ── Check active grace period ──────────────────────────────
        grace = self._graces.get(symbol)
        if grace and not grace.is_expired:
            remaining = (grace.granted_at + grace.grace_minutes * 60) - time.time()

            # If it turned profitable during grace, take the win
            if pnl_pct >= self._profit_pct:
                self._graces.pop(symbol, None)
                log.info(
                    f"SENTINEL_DEADLINE_GRACE_TP | sym={symbol} "
                    f"pnl={pnl_pct:+.2f}% | Profitable during grace — closing | {ctx()}"
                )
                return DeadlineAction(
                    tier=DeadlineTier.PROFIT,
                    should_close=True,
                    reason=f"Turned profitable ({pnl_pct:+.2f}%) during grace period",
                )

            # Still in grace — hold, don't act
            log.debug(
                f"SENTINEL_DEADLINE_GRACE_HOLD | sym={symbol} "
                f"pnl={pnl_pct:+.2f}% remaining={remaining:.0f}s | {ctx()}"
            )
            return DeadlineAction(
                tier=DeadlineTier.BREAKEVEN,
                should_close=False,
                reason=f"Grace period active, {remaining:.0f}s remaining",
            )

        # ── Grace expired — close it out ───────────────────────────
        if grace and grace.is_expired:
            self._graces.pop(symbol, None)
            log.info(
                f"SENTINEL_DEADLINE_GRACE_EXPIRED | sym={symbol} "
                f"pnl={pnl_pct:+.2f}% | Grace expired — closing | {ctx()}"
            )
            return DeadlineAction(
                tier=DeadlineTier.BREAKEVEN,
                should_close=True,
                reason=f"Grace period expired, PnL={pnl_pct:+.2f}% — closing",
            )

        # ── TIER 1: Profitable ─────────────────────────────────────
        if pnl_pct >= self._profit_pct:
            return DeadlineAction(
                tier=DeadlineTier.PROFIT,
                should_close=True,
                reason=f"Timer expired, profitable at {pnl_pct:+.2f}% — locking win",
            )

        # ── TIER 2: Breakeven zone ─────────────────────────────────
        if pnl_pct >= self._breakeven_lower_pct:
            new_sl = entry_price  # SL at entry = breakeven
            self._graces[symbol] = DeadlineGrace(
                symbol=symbol,
                granted_at=time.time(),
                grace_minutes=self._grace_minutes,
                sl_set_to=new_sl,
            )
            return DeadlineAction(
                tier=DeadlineTier.BREAKEVEN,
                should_close=False,
                new_sl=new_sl,
                grace_minutes=self._grace_minutes,
                reason=(
                    f"Timer expired, near breakeven ({pnl_pct:+.2f}%) — "
                    f"SL to entry, {self._grace_minutes}min grace"
                ),
            )

        # ── TIER 3: Small loss ─────────────────────────────────────
        if pnl_pct >= self._small_loss_pct:
            if direction in ("Buy", "Long"):
                new_sl = entry_price * (1 - self._small_loss_sl_pct / 100)
            else:
                new_sl = entry_price * (1 + self._small_loss_sl_pct / 100)
            return DeadlineAction(
                tier=DeadlineTier.SMALL_LOSS,
                should_close=False,
                new_sl=new_sl,
                reason=(
                    f"Timer expired, small loss ({pnl_pct:+.2f}%) — "
                    f"tightening SL to -{self._small_loss_sl_pct}%"
                ),
            )

        # ── TIER 4: Big loss ───────────────────────────────────────
        return DeadlineAction(
            tier=DeadlineTier.BIG_LOSS,
            should_close=True,
            reason=f"Timer expired, big loss ({pnl_pct:+.2f}%) — cutting immediately",
        )

    def clear_grace(self, symbol: str) -> None:
        """Remove grace period tracking for a closed position."""
        self._graces.pop(symbol, None)

    def get_grace(self, symbol: str) -> DeadlineGrace | None:
        """Return active grace for a symbol, if any."""
        return self._graces.get(symbol)
