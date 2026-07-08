"""Optimization Worker: runs weekly full portfolio optimization."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.portfolio.optimizer import PortfolioOptimizer
from src.portfolio.stress_test import StressTester
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class OptimizationWorker(BaseWorker):
    """Runs weekly portfolio optimization, stress tests, and reports.

    Args:
        settings: Application settings.
        db: Database manager.
        optimizer: Portfolio optimizer.
        stress_tester: Stress tester.
    """

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        optimizer: PortfolioOptimizer, stress_tester: StressTester,
    ) -> None:
        super().__init__(
            name="optimization_worker",
            interval_seconds=3600,
            settings=settings,
            db=db,
        )
        self.optimizer = optimizer
        self.stress_tester = stress_tester
        self._last_run_date: str = ""

    async def tick(self) -> None:
        """Run weekly optimization if it's the right day and hour."""
        now = now_utc()
        today = now.strftime("%Y-%m-%d")
        day_name = now.strftime("%A").lower()
        hour = now.hour

        if today == self._last_run_date:
            return
        if day_name != self.settings.portfolio.optimization_day:
            return
        if hour != self.settings.portfolio.optimization_hour_utc:
            return

        self._last_run_date = today

        log.info("Weekly portfolio optimization starting")

        # Run optimization
        actions = self.optimizer.optimize(10000)
        log.info("Optimization: {n} rebalance actions", n=len(actions))

        # Run stress tests
        if self.settings.portfolio.stress_test_enabled:
            results = self.stress_tester.run_scenarios(10000)
            failed = sum(1 for r in results if not r.survival)
            log.info("Stress tests: {n} scenarios, {f} failures", n=len(results), f=failed)
