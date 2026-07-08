"""Trial Manager: manages 14-day paper trading trials for generated strategies."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.backtest_repo import BacktestRepository
from src.factory.lifecycle import StrategyLifecycleManager
from src.factory.models.backtest_types import TrialStatus

log = get_logger("factory")


class TrialManager:
    """Manages strategies in their paper trading probation period.

    Args:
        db: Database manager.
        settings: Application settings.
        lifecycle: Lifecycle manager for transitions.
    """

    def __init__(
        self,
        db: DatabaseManager,
        settings: Settings,
        lifecycle: StrategyLifecycleManager,
    ) -> None:
        self.db = db
        self.settings = settings
        self.lifecycle = lifecycle
        self.repo = BacktestRepository(db)

    async def deploy_to_trial(self, strategy_id: str, strategy_name: str) -> bool:
        """Deploy a strategy to paper trading trial.

        Returns True if deployment succeeded.
        """
        cfg = self.settings.trial

        await self.repo.save_trial_performance(
            strategy_id=strategy_id,
            date=now_utc().strftime("%Y-%m-%d"),
            trades=0, wins=0, pnl=0.0,
            cum_trades=0, cum_pnl=0.0, cum_wr=0.0, max_dd=0.0,
        )

        log.info(
            "Trial: deployed {name} for {days}-day trial (25% position size)",
            name=strategy_name, days=cfg.trial_duration_days,
        )
        return True

    async def get_trial_status(self, strategy_id: str, strategy_name: str) -> TrialStatus:
        """Get current trial status for a strategy."""
        cfg = self.settings.trial

        # Query trial performance history
        rows = await self.db.fetch_all(
            "SELECT * FROM trial_performance WHERE strategy_id = ? ORDER BY date ASC",
            (strategy_id,),
        )

        if not rows:
            return TrialStatus(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                status="active",
                started_at=now_utc().strftime("%Y-%m-%d"),
            )

        first_date = rows[0]["date"]
        last = rows[-1]
        days_elapsed = len(rows)
        days_remaining = max(0, cfg.trial_duration_days - days_elapsed)

        cum_trades = int(last.get("cumulative_trades", 0))
        cum_wr = float(last.get("cumulative_win_rate", 0))
        cum_pnl = float(last.get("cumulative_pnl", 0))
        max_dd = float(last.get("max_drawdown", 0))
        wins = int(cum_trades * cum_wr) if cum_trades > 0 else 0

        eligible = (
            days_remaining <= 0
            and cum_trades >= cfg.min_trades_for_evaluation
            and cum_wr >= cfg.promotion_min_win_rate
            and cum_pnl >= cfg.promotion_min_pnl
            and max_dd <= cfg.promotion_max_drawdown
        )

        return TrialStatus(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            status="active" if days_remaining > 0 else "expired",
            started_at=first_date,
            trial_duration_days=cfg.trial_duration_days,
            trades_taken=cum_trades,
            wins=wins,
            losses=cum_trades - wins,
            win_rate=cum_wr,
            total_pnl_pct=cum_pnl,
            max_drawdown_pct=max_dd,
            days_remaining=days_remaining,
            promotion_eligible=eligible,
        )

    async def evaluate_expired_trials(self) -> list[dict]:
        """Evaluate all expired trials and return recommendations."""
        results: list[dict] = []

        # Find strategies in trial_active status
        from src.database.repositories.factory_repo import FactoryRepository
        factory_repo = FactoryRepository(self.db)

        try:
            trial_strategies = await factory_repo.get_strategies_by_status("trial_active")
        except Exception:
            return results

        for strategy in trial_strategies:
            status = await self.get_trial_status(strategy.id, strategy.strategy_name)
            if status.days_remaining > 0:
                continue

            recommendation = await self.lifecycle.evaluate_trial(
                strategy.id,
                {
                    "trades_taken": status.trades_taken,
                    "wins": status.wins,
                    "total_pnl_pct": status.total_pnl_pct,
                    "max_drawdown_pct": status.max_drawdown_pct,
                },
            )

            results.append({
                "strategy_id": strategy.id,
                "strategy_name": strategy.strategy_name,
                "recommendation": recommendation,
                "status": status.to_dict(),
            })

            # Execute the recommendation
            if recommendation == "promote":
                await self.lifecycle.transition(
                    strategy.id, "trial_active", "promoted",
                    f"Trial passed: WR={status.win_rate:.0%}, PnL={status.total_pnl_pct:+.1f}%",
                )
            elif recommendation == "kill":
                await self.lifecycle.transition(
                    strategy.id, "trial_active", "killed",
                    f"Trial failed: WR={status.win_rate:.0%}, PnL={status.total_pnl_pct:+.1f}%",
                )

        return results
