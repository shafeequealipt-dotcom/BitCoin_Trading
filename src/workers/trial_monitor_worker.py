"""Trial Monitor Worker: evaluates paper trading trials hourly."""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.factory.trial_manager import TrialManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class TrialMonitorWorker(BaseWorker):
    """Monitors and evaluates paper trading trials.

    Args:
        settings: Application settings.
        db: Database manager.
        trial_manager: Trial manager.
    """

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        trial_manager: TrialManager,
    ) -> None:
        super().__init__(
            name="trial_monitor_worker",
            interval_seconds=3600,
            settings=settings,
            db=db,
        )
        self.trial_manager = trial_manager

    async def tick(self) -> None:
        """Evaluate expired trials and log active trial performance."""
        # Phase 8 (post-Layer-1 fix): structured end-of-tick summary so
        # operators can see the worker is alive on quiet ticks where
        # no trials have expired (the typical case).
        t0 = time.monotonic()
        results = await self.trial_manager.evaluate_expired_trials()

        for r in results:
            log.info(
                "Trial evaluation: {name} → {rec}",
                name=r["strategy_name"], rec=r["recommendation"],
            )

        el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"TRIAL_MONITOR_TICK_SUMMARY | evaluated={len(results)} "
            f"el={el_ms:.0f}ms | {ctx()}"
        )
