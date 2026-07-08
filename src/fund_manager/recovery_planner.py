"""M12: Recovery Planner.

Activated when equity drops below the starting balance. Imposes conservative
trading restrictions to systematically recover losses without taking
additional large risks.
"""

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountState, RecoveryPlan

log = get_logger("fund_manager")

# ── Recovery parameters ─────────────────────────────────────────────────────
# Max trade size during recovery (% of trading capital)
RECOVERY_MAX_TRADE_PCT = 3.0
# Max stop-loss during recovery
RECOVERY_MAX_SL_PCT = 1.5
# Target take-profit during recovery (conservative)
RECOVERY_TARGET_TP_PCT = 2.0
# Only allow proven strategies during recovery
RECOVERY_ALLOWED_STRATEGIES = [
    "rsi_oversold",
    "support_bounce",
    "trend_following",
    "mean_reversion",
]
# Days to aim for full recovery (for target calculation)
RECOVERY_TARGET_DAYS = 30


class RecoveryPlanner:
    """Systematic loss recovery with conservative restrictions.

    When equity is below starting balance, the planner activates and
    imposes limits on trade size, stop-loss, and strategy selection.
    """

    def __init__(self, settings=None, db=None) -> None:
        self._active: bool = False
        self._deficit: float = 0.0
        self._starting_balance: float = 0.0
        self._recovered: float = 0.0
        self._days_in_recovery: int = 0
        self._trade_count: int = 0
        self._winning_trades: int = 0

    async def initialize(self, state: AccountState) -> None:
        """Check if account is in drawdown and activate recovery if needed.

        Args:
            state: Current AccountState.
        """
        self._starting_balance = state.starting_balance

        if state.total_equity < state.starting_balance:
            self._deficit = state.starting_balance - state.total_equity
            self._active = True
            log.warning(
                "Recovery planner ACTIVATED: deficit={deficit:.2f} USD",
                deficit=self._deficit,
            )
        else:
            self._active = False
            log.info("Recovery planner: account is above starting balance, inactive")

    def get_plan(self, state: AccountState | None = None) -> RecoveryPlan:
        """Get the current recovery plan.

        If equity is below starting balance, returns an active plan with
        trading restrictions. Otherwise, returns an inactive plan.

        Args:
            state: Optional AccountState for live deficit calculation.

        Returns:
            RecoveryPlan with restrictions and progress.
        """
        # Update deficit if state is provided
        if state is not None:
            if state.total_equity < state.starting_balance:
                self._deficit = state.starting_balance - state.total_equity
                self._active = True
            else:
                self._active = False
                self._deficit = 0.0

        if not self._active:
            return RecoveryPlan(
                active=False,
                deficit_usd=0.0,
                target_daily_recovery=0.0,
                recovered_so_far=self._recovered,
                days_in_recovery=self._days_in_recovery,
                progress_pct=100.0,
            )

        # Calculate daily target
        days_remaining = max(1, RECOVERY_TARGET_DAYS - self._days_in_recovery)
        target_daily = self._deficit / days_remaining

        # Calculate progress
        initial_deficit = self._deficit + self._recovered
        progress = (self._recovered / initial_deficit * 100.0) if initial_deficit > 0 else 0.0

        plan = RecoveryPlan(
            active=True,
            deficit_usd=self._deficit,
            target_daily_recovery=target_daily,
            recovered_so_far=self._recovered,
            days_in_recovery=self._days_in_recovery,
            allowed_strategies=list(RECOVERY_ALLOWED_STRATEGIES),
            max_trade_size_pct=RECOVERY_MAX_TRADE_PCT,
            max_sl_pct=RECOVERY_MAX_SL_PCT,
            target_tp_pct=RECOVERY_TARGET_TP_PCT,
            progress_pct=min(100.0, progress),
        )

        log.debug(
            "Recovery plan: deficit={deficit:.2f}, daily_target={target:.2f}, "
            "progress={progress:.1f}%",
            deficit=self._deficit,
            target=target_daily,
            progress=progress,
        )

        return plan

    def on_trade_result(self, pnl_usd: float) -> None:
        """Track a trade result during recovery.

        Args:
            pnl_usd: Trade PnL in USD (positive = profit, negative = loss).
        """
        self._trade_count += 1

        if pnl_usd > 0:
            self._recovered += pnl_usd
            self._winning_trades += 1

            # Check if deficit is fully recovered
            if self._recovered >= self._deficit + self._recovered:
                self._active = False
                log.info(
                    "RECOVERY COMPLETE: recovered {recovered:.2f} USD "
                    "in {days} days ({wins}/{total} winning trades)",
                    recovered=self._recovered,
                    days=self._days_in_recovery,
                    wins=self._winning_trades,
                    total=self._trade_count,
                )
            else:
                log.info(
                    "Recovery progress: +{pnl:.2f} USD, "
                    "total recovered={recovered:.2f}/{deficit:.2f}",
                    pnl=pnl_usd,
                    recovered=self._recovered,
                    deficit=self._deficit + self._recovered,
                )
        else:
            # Loss during recovery
            self._deficit += abs(pnl_usd)
            log.warning(
                "Loss during recovery: {pnl:.2f} USD, "
                "deficit increased to {deficit:.2f}",
                pnl=pnl_usd,
                deficit=self._deficit,
            )

    def on_daily_close(self) -> None:
        """Increment recovery day counter. Call at end of each trading day."""
        if self._active:
            self._days_in_recovery += 1
            log.debug(
                "Recovery day {day} complete",
                day=self._days_in_recovery,
            )

    @property
    def is_active(self) -> bool:
        """Whether recovery mode is currently active."""
        return self._active
