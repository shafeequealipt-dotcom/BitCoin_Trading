"""Drawdown tracking and circuit breakers for trading safety."""

from datetime import timedelta

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import AccountInfo
from src.core.utils import now_utc, safe_divide
from src.database.connection import DatabaseManager

log = get_logger("risk")


class DrawdownTracker:
    """Tracks drawdown from peak equity and enforces circuit breakers.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self.settings = settings
        self.db = db
        self.peak_equity: float = 0.0
        self.today_starting_equity: float = 0.0
        self.today_realized_pnl: float = 0.0
        self.today_date: str = ""
        self.last_loss_time = None
        self.consecutive_losses: int = 0
        self.trading_halted: bool = False
        self.halt_reason: str = ""

    async def initialize(self, account: AccountInfo) -> None:
        """Initialize tracker with current equity state.

        Loads persisted peak_equity from DB to survive restarts.
        """
        # Load persisted peak_equity
        try:
            row = await self.db.fetch_one(
                "SELECT value FROM fund_manager_state WHERE key = 'peak_equity'"
            )
            if row:
                self.peak_equity = float(row["value"])
        except Exception:
            pass

        self.peak_equity = max(self.peak_equity, account.total_equity)
        self._reset_day_if_needed()
        if self.today_starting_equity == 0:
            self.today_starting_equity = account.total_equity

        # Persist updated peak
        await self._persist_peak_equity()

        log.info(
            "Drawdown tracker initialized: equity=${eq}, peak=${pk}",
            eq=account.total_equity, pk=self.peak_equity,
        )

    def _reset_day_if_needed(self) -> None:
        today = now_utc().strftime("%Y-%m-%d")
        if today != self.today_date:
            self.today_realized_pnl = 0.0
            self.today_date = today
            self.consecutive_losses = 0
            self.last_loss_time = None
            if self.trading_halted and "daily" in self.halt_reason.lower():
                self.trading_halted = False
                self.halt_reason = ""

    def update_equity(self, current_equity: float) -> None:
        """Update peak equity (only goes up). Persists to DB."""
        dd_pct = (self.peak_equity - current_equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0.0
        log.info(f"DRAWDOWN | eq={current_equity:.2f} | peak={self.peak_equity:.2f} | dd_pct={dd_pct:.2f} | {ctx()}")
        old = self.peak_equity
        self.peak_equity = max(self.peak_equity, current_equity)
        if self.peak_equity > old:
            # Schedule async persistence (fire-and-forget)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._persist_peak_equity())
            except Exception:
                pass

    async def _persist_peak_equity(self) -> None:
        """Save peak_equity to fund_manager_state table."""
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) "
                "VALUES ('peak_equity', ?, datetime('now'))",
                (str(self.peak_equity),),
            )
        except Exception:
            pass

    def record_trade_result(self, pnl: float) -> None:
        """Record a closed trade's PnL and update trackers."""
        self._reset_day_if_needed()
        self.today_realized_pnl += pnl

        if pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_time = now_utc()
        elif pnl > 0:
            self.consecutive_losses = 0

    def get_current_drawdown(self, current_equity: float) -> dict:
        """Calculate current drawdown from peak."""
        dd_usd = max(0, self.peak_equity - current_equity)
        dd_pct = safe_divide(dd_usd, self.peak_equity, 0) * 100
        max_dd = self.settings.risk.daily_loss_limit_pct * 2

        if dd_pct > max_dd:
            status = "halted"
        elif dd_pct > max_dd * 0.8:
            status = "critical"
        elif dd_pct > max_dd * 0.5:
            status = "warning"
        else:
            status = "safe"

        return {
            "peak_equity": round(self.peak_equity, 2),
            "current_equity": round(current_equity, 2),
            "drawdown_usd": round(dd_usd, 2),
            "drawdown_pct": round(dd_pct, 2),
            "max_allowed_drawdown_pct": max_dd,
            "status": status,
        }

    def get_daily_pnl(self, current_unrealized: float = 0.0) -> dict:
        """Get today's PnL summary."""
        self._reset_day_if_needed()
        total = self.today_realized_pnl + current_unrealized
        pct = safe_divide(total, self.today_starting_equity, 0) * 100 if self.today_starting_equity > 0 else 0
        limit = self.settings.risk.daily_loss_limit_pct
        remaining = limit - abs(min(pct, 0))

        if pct < -limit:
            status = "exceeded"
        elif pct < -limit * 0.8:
            status = "warning"
        else:
            status = "safe"

        return {
            "today_realized_pnl": round(self.today_realized_pnl, 2),
            "today_unrealized_pnl": round(current_unrealized, 2),
            "today_total_pnl": round(total, 2),
            "today_pnl_pct": round(pct, 2),
            "daily_loss_limit_pct": limit,
            "limit_remaining_pct": round(max(0, remaining), 2),
            "limit_status": status,
            "trading_halted": self.trading_halted,
            "halt_reason": self.halt_reason,
        }

    def check_circuit_breakers(self, current_equity: float | None = None) -> tuple[bool, str]:
        """Check all circuit breakers. Returns (is_safe, reason)."""
        self._reset_day_if_needed()

        # Daily loss limit
        if self.today_starting_equity > 0:
            loss_pct = abs(min(0, self.today_realized_pnl)) / self.today_starting_equity * 100
            if loss_pct >= self.settings.risk.daily_loss_limit_pct:
                reason = f"Daily loss limit exceeded ({loss_pct:.1f}% loss, limit is {self.settings.risk.daily_loss_limit_pct}%)"
                self.trading_halted = True
                self.halt_reason = reason
                return False, reason

        # Drawdown from peak
        if current_equity is not None and self.peak_equity > 0:
            dd_pct = (self.peak_equity - current_equity) / self.peak_equity * 100
            max_dd = self.settings.risk.daily_loss_limit_pct * 2
            if dd_pct >= max_dd:
                reason = f"Maximum drawdown exceeded ({dd_pct:.1f}% from peak)"
                self.trading_halted = True
                self.halt_reason = reason
                return False, reason

        # Consecutive losses
        if self.consecutive_losses >= 5:
            reason = "5 consecutive losing trades — taking a break"
            self.trading_halted = True
            self.halt_reason = reason
            return False, reason

        # Cooldown
        cooldown_min = getattr(self.settings.risk, "loss_cooldown_seconds", 300) / 60
        if self.last_loss_time:
            elapsed = (now_utc() - self.last_loss_time).total_seconds() / 60
            if elapsed < cooldown_min:
                remaining = cooldown_min - elapsed
                reason = f"Cooldown active — {remaining:.0f} minutes remaining after last loss"
                return False, reason

        return True, ""

    def reset_halt(self, reason: str = "Manual reset") -> None:
        """Manually reset trading halt."""
        self.trading_halted = False
        self.halt_reason = ""
        self.consecutive_losses = 0
        log.info("Trading halt reset: {reason}", reason=reason)

    def is_in_cooldown(self) -> bool:
        """Check if cooldown timer is active."""
        if not self.last_loss_time:
            return False
        cooldown_sec = getattr(self.settings.risk, "loss_cooldown_seconds", 300)
        elapsed = (now_utc() - self.last_loss_time).total_seconds()
        return elapsed < cooldown_sec
