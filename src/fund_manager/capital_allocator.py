"""M1: Progressive Capital Allocator.

Unlocks more capital as the account grows. Tracks account level based on
equity growth multiplier and enforces demotion rules for drawdowns.
"""

import asyncio
from datetime import datetime, timezone

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountLevel, AccountState

log = get_logger("fund_manager")

# ── Level configuration ─────────────────────────────────────────────────────
# Each level defines: unlock_pct, max_leverage, max_positions, max_trade_pct
LEVEL_CONFIG: dict[AccountLevel, dict] = {
    AccountLevel.ROOKIE: {
        "unlock_pct": 20.0,
        "max_leverage": 3,
        "max_positions": 3,
        "max_trade_pct": 5.0,
        "growth_threshold": 1.0,
    },
    AccountLevel.PROVEN: {
        "unlock_pct": 30.0,
        "max_leverage": 4,
        "max_positions": 5,
        "max_trade_pct": 7.0,
        "growth_threshold": 1.5,
    },
    AccountLevel.VETERAN: {
        "unlock_pct": 40.0,
        "max_leverage": 5,
        "max_positions": 7,
        "max_trade_pct": 10.0,
        "growth_threshold": 2.0,
    },
    AccountLevel.ELITE: {
        "unlock_pct": 50.0,
        "max_leverage": 5,
        "max_positions": 10,
        "max_trade_pct": 12.0,
        "growth_threshold": 3.0,
    },
    AccountLevel.MASTER: {
        "unlock_pct": 60.0,
        "max_leverage": 5,
        "max_positions": 10,
        "max_trade_pct": 15.0,
        "growth_threshold": 5.0,
    },
}

# Ordered list of levels for promotion/demotion traversal
LEVEL_ORDER: list[AccountLevel] = [
    AccountLevel.ROOKIE,
    AccountLevel.PROVEN,
    AccountLevel.VETERAN,
    AccountLevel.ELITE,
    AccountLevel.MASTER,
]

# Demotion thresholds
DEMOTION_DROP_PCT = 10.0        # 10% drop from level-up equity triggers demotion
CONSECUTIVE_LOSS_DAYS = 3       # 3 consecutive losing days triggers demotion
EMERGENCY_DRAWDOWN_PCT = 15.0   # 15% drawdown from peak → force ROOKIE


class CapitalAllocator:
    """Progressive capital unlock based on account growth.

    Args:
        db: DatabaseManager for persisting level state.
    """

    def __init__(self, settings=None, db=None) -> None:
        self._db = db
        self._level_up_equity: float = 0.0
        self._peak_equity: float = 0.0
        self._consecutive_losing_days: int = 0
        self._last_updated: datetime | None = None

    async def initialize(self, state: AccountState) -> None:
        """Load persisted level data and apply to state.

        Args:
            state: Current AccountState to populate.
        """
        try:
            if self._db is not None:
                row = await self._db.fetch_one(
                    "SELECT * FROM fund_manager_state WHERE key = 'capital_level'"
                )
                if row:
                    import json
                    data = json.loads(row.get("value", "{}"))
                    saved_level = data.get("level", "rookie")
                    state.level = AccountLevel(saved_level)
                    self._level_up_equity = data.get("level_up_equity", state.total_equity)
                    self._peak_equity = data.get("peak_equity", state.total_equity)
                    self._consecutive_losing_days = data.get("consecutive_losing_days", 0)
                    log.info(
                        "Capital allocator initialized at level {level}",
                        level=state.level.value,
                    )
                else:
                    self._level_up_equity = state.total_equity
                    self._peak_equity = state.total_equity
                    log.info("Capital allocator starting fresh at ROOKIE")
            else:
                self._level_up_equity = state.total_equity
                self._peak_equity = state.total_equity
        except Exception:
            log.warning("Could not load capital level from DB, defaulting to ROOKIE")
            self._level_up_equity = state.total_equity
            self._peak_equity = state.total_equity

        # Apply level config to state
        self._apply_level(state)

    def update_level(self, state: AccountState) -> None:
        """Recalculate level based on current equity growth multiplier.

        Handles both promotions and demotions.

        Args:
            state: Current AccountState with up-to-date total_equity.
        """
        if state.starting_balance <= 0:
            return

        growth = state.total_equity / state.starting_balance
        state.growth_multiplier = growth

        # Track peak equity
        if state.total_equity > self._peak_equity:
            self._peak_equity = state.total_equity

        # ── Emergency demotion: 15% drawdown from peak → ROOKIE ──
        if self._peak_equity > 0:
            drawdown_pct = ((self._peak_equity - state.total_equity) / self._peak_equity) * 100
            if drawdown_pct >= EMERGENCY_DRAWDOWN_PCT:
                if state.level != AccountLevel.ROOKIE:
                    log.warning(
                        "EMERGENCY demotion to ROOKIE: drawdown {dd:.1f}% from peak",
                        dd=drawdown_pct,
                    )
                    state.level = AccountLevel.ROOKIE
                    self._level_up_equity = state.total_equity
                    self._apply_level(state)
                    self._save_level(state)
                    return

        # ── Consecutive losing days demotion ──
        if self._consecutive_losing_days >= CONSECUTIVE_LOSS_DAYS:
            current_idx = LEVEL_ORDER.index(state.level)
            if current_idx > 0:
                new_level = LEVEL_ORDER[current_idx - 1]
                log.warning(
                    "Demotion from {old} to {new}: {days} consecutive losing days",
                    old=state.level.value,
                    new=new_level.value,
                    days=self._consecutive_losing_days,
                )
                state.level = new_level
                self._level_up_equity = state.total_equity
                self._consecutive_losing_days = 0
                self._apply_level(state)
                self._save_level(state)
                return

        # ── Drop-based demotion: 10% drop from level-up equity ──
        if self._level_up_equity > 0:
            drop_pct = ((self._level_up_equity - state.total_equity) / self._level_up_equity) * 100
            if drop_pct >= DEMOTION_DROP_PCT:
                current_idx = LEVEL_ORDER.index(state.level)
                if current_idx > 0:
                    new_level = LEVEL_ORDER[current_idx - 1]
                    log.warning(
                        "Demotion from {old} to {new}: {drop:.1f}% drop from level-up equity",
                        old=state.level.value,
                        new=new_level.value,
                        drop=drop_pct,
                    )
                    state.level = new_level
                    self._level_up_equity = state.total_equity
                    self._apply_level(state)
                    self._save_level(state)
                    return

        # ── Promotion check ──
        old_level = state.level
        new_level = AccountLevel.ROOKIE
        for level in reversed(LEVEL_ORDER):
            threshold = LEVEL_CONFIG[level]["growth_threshold"]
            if growth >= threshold:
                new_level = level
                break

        if new_level != old_level:
            new_idx = LEVEL_ORDER.index(new_level)
            old_idx = LEVEL_ORDER.index(old_level)
            if new_idx > old_idx:
                log.info(
                    "PROMOTION: {old} -> {new} (growth {g:.2f}x)",
                    old=old_level.value,
                    new=new_level.value,
                    g=growth,
                )
                state.level = new_level
                self._level_up_equity = state.total_equity
                self._apply_level(state)
                self._save_level(state)

    def get_max_leverage(self, level: AccountLevel) -> int:
        """Return the max leverage allowed for a given level.

        Args:
            level: Account level.

        Returns:
            Maximum leverage multiplier.
        """
        return LEVEL_CONFIG[level]["max_leverage"]

    def get_max_positions(self, level: AccountLevel) -> int:
        """Return the max open positions for a given level.

        Args:
            level: Account level.

        Returns:
            Maximum number of concurrent positions.
        """
        return LEVEL_CONFIG[level]["max_positions"]

    def get_max_trade_pct(self, level: AccountLevel) -> float:
        """Return the max percentage of capital per trade.

        Args:
            level: Account level.

        Returns:
            Maximum trade size as percent of trading capital.
        """
        return LEVEL_CONFIG[level]["max_trade_pct"]

    def on_daily_close(self, daily_pnl: float) -> None:
        """Track daily PnL for consecutive loss tracking.

        Args:
            daily_pnl: The day's total PnL in USD.
        """
        if daily_pnl < 0:
            self._consecutive_losing_days += 1
            log.debug(
                "Losing day #{count}, daily PnL: {pnl:.2f}",
                count=self._consecutive_losing_days,
                pnl=daily_pnl,
            )
        else:
            if self._consecutive_losing_days > 0:
                log.debug(
                    "Losing streak broken after {count} days",
                    count=self._consecutive_losing_days,
                )
            self._consecutive_losing_days = 0

    def _apply_level(self, state: AccountState) -> None:
        """Apply level configuration to state.

        Args:
            state: AccountState to update with level-specific limits.
        """
        config = LEVEL_CONFIG[state.level]
        state.unlock_pct = config["unlock_pct"]
        state.trading_capital = state.total_equity * (config["unlock_pct"] / 100.0)
        self._last_updated = datetime.now(timezone.utc)

    def _save_level(self, state: AccountState) -> None:
        """Persist level state to DB asynchronously.

        Args:
            state: AccountState containing the current level.
        """
        if self._db is None:
            return

        try:
            import json
            data = json.dumps({
                "level": state.level.value,
                "level_up_equity": self._level_up_equity,
                "peak_equity": self._peak_equity,
                "consecutive_losing_days": self._consecutive_losing_days,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

            async def _persist():
                try:
                    await self._db.execute(
                        """
                        INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at)
                        VALUES ('capital_level', ?, datetime('now'))
                        """,
                        (data,),
                    )
                except Exception:
                    log.warning("Failed to persist capital level to DB")

            asyncio.create_task(_persist())
        except Exception:
            log.warning("Failed to schedule capital level save")
