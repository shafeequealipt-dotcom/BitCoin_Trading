"""M20 — Profit reinvestment / compound optimizer.

Determines how realised profits should be split:

  50% — Locked by ratchet (M14 profit floor), never risked again
  25% — Added to active trading pool (compounds growth)
  25% — Added to reserve pool (safety buffer)

Also tracks the compound growth rate over time.
"""

from __future__ import annotations

import time

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountState

log = get_logger("fund_manager")

# ── Split ratios ───────────────────────────────────────────────────
_LOCK_PCT = 0.50     # Locked by ratchet (profit floor)
_ACTIVE_PCT = 0.25   # Reinvested into active trading pool
_RESERVE_PCT = 0.25  # Added to reserve pool


class CompoundOptimizer:
    """Optimizes profit reinvestment for compound growth."""

    def __init__(self, settings=None, db=None) -> None:
        self._settings = settings
        self._db = db
        # Tracking for compound growth rate
        self._total_profits: float = 0.0
        self._total_locked: float = 0.0
        self._total_reinvested: float = 0.0
        self._total_reserved: float = 0.0
        self._start_time: float = time.monotonic()
        self._starting_capital: float = 0.0
        self._current_capital: float = 0.0

    # ------------------------------------------------------------------
    # Reinvestment split
    # ------------------------------------------------------------------

    @staticmethod
    def get_reinvestment_split(pnl_usd: float) -> dict:
        """Calculate how to split a realised profit.

        Args:
            pnl_usd: Realised profit in USD. Only positive values are split;
                     losses return zeros.

        Returns:
            Dict with keys:
              locked:     Amount to lock in profit floor (50%)
              active:     Amount to add to active pool (25%)
              reserve:    Amount to add to reserve (25%)
              total_pnl:  The original PnL amount
        """
        if pnl_usd <= 0:
            return {
                "locked": 0.0,
                "active": 0.0,
                "reserve": 0.0,
                "total_pnl": pnl_usd,
            }

        locked = round(pnl_usd * _LOCK_PCT, 4)
        active = round(pnl_usd * _ACTIVE_PCT, 4)
        reserve = round(pnl_usd * _RESERVE_PCT, 4)

        log.info(
            "CompoundOptimizer: split {pnl:.2f} USD → "
            "locked={lk:.2f}, active={ac:.2f}, reserve={rs:.2f}",
            pnl=pnl_usd, lk=locked, ac=active, rs=reserve,
        )

        return {
            "locked": locked,
            "active": active,
            "reserve": reserve,
            "total_pnl": pnl_usd,
        }

    # ------------------------------------------------------------------
    # State tracking
    # ------------------------------------------------------------------

    async def update(self, state: AccountState) -> None:
        """Update compound growth tracking from current account state.

        Args:
            state: Current AccountState with equity and capital info.
        """
        try:
            if self._starting_capital <= 0 and state.starting_balance > 0:
                self._starting_capital = state.starting_balance

            self._current_capital = state.total_equity

            log.debug(
                "CompoundOptimizer: updated state — starting={start:.2f}, "
                "current={cur:.2f}, growth={g:.2f}%",
                start=self._starting_capital,
                cur=self._current_capital,
                g=self.growth_pct,
            )
        except Exception:
            log.warning("CompoundOptimizer: error updating state")

    def record_profit(self, pnl_usd: float) -> dict:
        """Record a profit and return the reinvestment split.

        Args:
            pnl_usd: Realised PnL in USD (positive or negative).

        Returns:
            The reinvestment split dict.
        """
        split = self.get_reinvestment_split(pnl_usd)

        if pnl_usd > 0:
            self._total_profits += pnl_usd
            self._total_locked += split["locked"]
            self._total_reinvested += split["active"]
            self._total_reserved += split["reserve"]

        return split

    # ------------------------------------------------------------------
    # Growth metrics
    # ------------------------------------------------------------------

    @property
    def growth_pct(self) -> float:
        """Current compound growth percentage from starting capital."""
        if self._starting_capital <= 0:
            return 0.0
        return ((self._current_capital - self._starting_capital) / self._starting_capital) * 100

    @property
    def daily_growth_rate(self) -> float:
        """Average daily growth rate percentage."""
        elapsed_days = (time.monotonic() - self._start_time) / 86400.0
        if elapsed_days < 0.1 or self._starting_capital <= 0:
            return 0.0
        total_growth = self._current_capital / self._starting_capital
        if total_growth <= 0:
            return 0.0
        # Simple average daily return
        return ((total_growth - 1.0) / elapsed_days) * 100

    def snapshot(self) -> dict:
        """Return compound growth diagnostics."""
        return {
            "total_profits": round(self._total_profits, 2),
            "total_locked": round(self._total_locked, 2),
            "total_reinvested": round(self._total_reinvested, 2),
            "total_reserved": round(self._total_reserved, 2),
            "starting_capital": round(self._starting_capital, 2),
            "current_capital": round(self._current_capital, 2),
            "growth_pct": round(self.growth_pct, 2),
            "daily_growth_rate": round(self.daily_growth_rate, 4),
            "split_ratios": {
                "locked_pct": _LOCK_PCT,
                "active_pct": _ACTIVE_PCT,
                "reserve_pct": _RESERVE_PCT,
            },
        }
