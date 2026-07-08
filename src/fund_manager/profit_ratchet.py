"""M14: Profit Ratchet.

Locks 50% of new equity high profits permanently. Once locked, these profits
form a floor that the account should never go below. Individual trade profits
also have 25% locked immediately.
"""

import asyncio
import json
from datetime import datetime, timezone

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountState

log = get_logger("fund_manager")

# Lock percentages
EQUITY_HIGH_LOCK_PCT = 50.0    # Lock 50% of new equity high profits
TRADE_PROFIT_LOCK_PCT = 25.0   # Lock 25% of individual trade profits


class ProfitRatchet:
    """Lock profits permanently to create an ever-rising floor.

    As equity reaches new highs, 50% of the gain above the last high is
    locked. Individual winning trades also contribute 25% of their profit
    to the locked pool.

    Args:
        db: DatabaseManager for persisting ratchet state.
    """

    def __init__(self, settings=None, db=None) -> None:
        self._db = db
        self._total_locked: float = 0.0
        self._equity_high: float = 0.0
        self._trade_locked: float = 0.0
        self._last_saved: datetime | None = None

    async def initialize(self, state: AccountState) -> None:
        """Load locked_profits and equity_high from DB.

        Args:
            state: Current AccountState to populate with floor values.
        """
        try:
            if self._db is not None:
                row = await self._db.fetch_one(
                    "SELECT * FROM fund_manager_state WHERE key = 'profit_ratchet'"
                )
                if row:
                    data = json.loads(row.get("value", "{}"))
                    self._total_locked = data.get("total_locked", 0.0)
                    self._equity_high = data.get("equity_high", state.total_equity)
                    self._trade_locked = data.get("trade_locked", 0.0)
                    log.info(
                        "Profit ratchet loaded: locked={locked:.2f}, "
                        "equity_high={high:.2f}",
                        locked=self._total_locked,
                        high=self._equity_high,
                    )
                else:
                    self._equity_high = state.total_equity
                    log.info("Profit ratchet starting fresh")
            else:
                self._equity_high = state.total_equity
        except Exception:
            log.warning("Could not load profit ratchet from DB, starting fresh")
            self._equity_high = state.total_equity

        # Apply current values to state
        state.locked_profits = self._total_locked
        state.profit_floor = self.get_floor(state.starting_balance)

    def update(self, state: AccountState) -> None:
        """Check if equity has reached a new high and lock profits.

        If equity exceeds the last recorded high, 50% of the new profit
        is locked permanently.

        Args:
            state: Current AccountState with up-to-date total_equity.
        """
        current_equity = state.total_equity

        # Safety: if uninitialized, set high to starting balance (no false lock)
        if self._equity_high <= 0:
            self._equity_high = max(state.starting_balance, current_equity)

        if current_equity > self._equity_high:
            new_profit = current_equity - self._equity_high
            lock_amount = new_profit * (EQUITY_HIGH_LOCK_PCT / 100.0)

            self._total_locked += lock_amount
            self._equity_high = current_equity

            state.locked_profits = self._total_locked
            state.profit_floor = self.get_floor(state.starting_balance)

            log.info(
                "Profit ratchet: new equity high {high:.2f}, "
                "locked {lock:.2f} (total locked: {total:.2f})",
                high=current_equity,
                lock=lock_amount,
                total=self._total_locked,
            )

            self._save_state()

    def on_profit(self, pnl_usd: float) -> None:
        """Lock 25% of an individual trade's profit immediately.

        Called when a trade closes in profit.

        Args:
            pnl_usd: Trade profit in USD. Only positive values are processed.
        """
        if pnl_usd <= 0:
            return

        lock_amount = pnl_usd * (TRADE_PROFIT_LOCK_PCT / 100.0)
        self._trade_locked += lock_amount
        self._total_locked += lock_amount

        log.info(
            "Trade profit locked: {lock:.2f} from {pnl:.2f} trade "
            "(total locked: {total:.2f})",
            lock=lock_amount,
            pnl=pnl_usd,
            total=self._total_locked,
        )

        self._save_state()

    def get_floor(self, starting_balance: float) -> float:
        """Calculate the absolute profit floor.

        The floor is the starting balance plus all locked profits. The account
        should never drop below this value.

        Args:
            starting_balance: Initial account balance.

        Returns:
            The minimum acceptable account value.
        """
        return starting_balance + self._total_locked

    def _save_state(self) -> None:
        """Persist ratchet state to DB asynchronously."""
        if self._db is None:
            return

        try:
            data = json.dumps({
                "total_locked": self._total_locked,
                "equity_high": self._equity_high,
                "trade_locked": self._trade_locked,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

            async def _persist():
                try:
                    await self._db.execute(
                        """
                        INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at)
                        VALUES ('profit_ratchet', ?, datetime('now'))
                        """,
                        (data,),
                    )
                except Exception:
                    log.warning("Failed to persist profit ratchet state to DB")

            asyncio.create_task(_persist())
            self._last_saved = datetime.now(timezone.utc)
        except Exception:
            log.warning("Failed to schedule profit ratchet save")
