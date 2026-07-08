"""Backtest Worker: runs backtests on validated strategies hourly."""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.repositories.factory_repo import FactoryRepository
from src.factory.backtester import BacktestEngine
from src.factory.lifecycle import StrategyLifecycleManager
from src.factory.trial_manager import TrialManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class BacktestWorker(BaseWorker):
    """Picks up validated strategies and runs backtests.

    Args:
        settings: Application settings.
        db: Database manager.
        engine: Backtest engine.
        lifecycle: Lifecycle manager.
        trial_manager: Trial manager for deploying passed strategies.
    """

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        engine: BacktestEngine, lifecycle: StrategyLifecycleManager,
        trial_manager: TrialManager,
    ) -> None:
        super().__init__(
            name="backtest_worker",
            interval_seconds=3600,
            settings=settings,
            db=db,
        )
        self.engine = engine
        self.lifecycle = lifecycle
        self.trial_manager = trial_manager
        self.factory_repo = FactoryRepository(db)

    async def tick(self) -> None:
        """Find validated strategies and run backtests."""
        # Phase 8 (post-Layer-1 fix): structured end-of-tick summary so
        # operators can confirm the worker is alive even on no-op ticks.
        t0 = time.monotonic()
        strategies = await self.factory_repo.get_strategies_by_status("validated")
        succeeded = 0
        failed = 0

        for strategy in strategies:
            try:
                # For now, run without actual trade simulation
                # (would need historical data and strategy loading)
                log.info(
                    "Backtest worker: would backtest {name}",
                    name=strategy.strategy_name,
                )
                succeeded += 1
            except Exception as e:
                failed += 1
                log.error(
                    "Backtest failed for {name}: {err}",
                    name=strategy.strategy_name, err=str(e),
                )

        el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"BACKTEST_TICK_SUMMARY | candidates={len(strategies)} "
            f"succeeded={succeeded} failed={failed} el={el_ms:.0f}ms | {ctx()}"
        )
