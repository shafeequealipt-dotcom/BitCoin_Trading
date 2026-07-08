"""Strategy Lifecycle Manager: manages the full journey from generation to production."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.repositories.backtest_repo import BacktestRepository

log = get_logger("factory")

VALID_TRANSITIONS = {
    "generated": ["validated", "killed"],
    "validated": ["backtested_pass", "backtested_fail"],
    "backtested_pass": ["trial_active"],
    "backtested_fail": ["killed"],
    "trial_active": ["promoted", "killed", "trial_extended"],
    "trial_extended": ["promoted", "killed"],
    "promoted": ["demoted"],
    "demoted": ["promoted", "killed"],
    "killed": ["generated"],  # Quarterly revival
}


class StrategyLifecycleManager:
    """Manages strategy lifecycle transitions with validation.

    Args:
        db: Database manager.
        settings: Application settings.
    """

    def __init__(self, db: DatabaseManager, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = BacktestRepository(db)

    async def transition(
        self, strategy_id: str, from_status: str, to_status: str, reason: str = "",
    ) -> bool:
        """Execute a lifecycle transition.

        Returns True if transition was valid and executed.
        """
        allowed = VALID_TRANSITIONS.get(from_status, [])
        if to_status not in allowed:
            log.warning(
                "Invalid transition: {id} {from} -> {to} (allowed: {allowed})",
                id=strategy_id, **{"from": from_status},
                to=to_status, allowed=allowed,
            )
            return False

        await self.repo.save_lifecycle_transition(
            strategy_id, from_status, to_status, reason,
        )

        # Update strategy status
        from src.database.repositories.factory_repo import FactoryRepository
        factory_repo = FactoryRepository(self.db)
        await factory_repo.update_strategy_status(strategy_id, to_status)

        log.info(
            "Lifecycle: {id} {from_s} -> {to} ({reason})",
            id=strategy_id, from_s=from_status, to=to_status,
            reason=reason or "no reason",
        )
        return True

    async def evaluate_trial(self, strategy_id: str, trades_data: dict) -> str:
        """Evaluate a trial strategy and return recommendation.

        Args:
            trades_data: Dict with trades_taken, wins, total_pnl_pct, max_drawdown_pct

        Returns:
            "promote", "extend", or "kill"
        """
        cfg = self.settings.trial
        trades = trades_data.get("trades_taken", 0)
        wins = trades_data.get("wins", 0)
        pnl = trades_data.get("total_pnl_pct", 0)
        dd = trades_data.get("max_drawdown_pct", 0)

        if trades < cfg.min_trades_for_evaluation:
            return "extend"

        wr = wins / trades if trades > 0 else 0

        if wr >= cfg.promotion_min_win_rate and pnl >= cfg.promotion_min_pnl and dd <= cfg.promotion_max_drawdown:
            return "promote"
        elif wr >= 0.45 and pnl > -1.0:
            return "extend"
        else:
            return "kill"

    async def get_lifecycle_summary(self) -> dict:
        """Get summary of all strategy lifecycle states."""
        from src.database.repositories.factory_repo import FactoryRepository
        factory_repo = FactoryRepository(self.db)

        summary: dict[str, int] = {}
        for status in ["generated", "validated", "backtested_pass", "trial_active",
                        "promoted", "demoted", "killed"]:
            try:
                strategies = await factory_repo.get_strategies_by_status(status)
                summary[status] = len(strategies)
            except Exception:
                summary[status] = 0

        return summary
