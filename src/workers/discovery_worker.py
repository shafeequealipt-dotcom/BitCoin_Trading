"""Discovery Worker: runs pattern discovery daily and generates strategies."""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.factory.discoverer import PatternDiscoverer
from src.factory.generator import StrategyGenerator
from src.factory.validator import CodeValidator
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class DiscoveryWorker(BaseWorker):
    """Runs pattern discovery daily at the configured hour.

    Args:
        settings: Application settings.
        db: Database manager.
        discoverer: Pattern discovery engine.
        generator: Strategy code generator.
        validator: Code validator.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        discoverer: PatternDiscoverer,
        generator: StrategyGenerator | None = None,
        validator: CodeValidator | None = None,
    ) -> None:
        super().__init__(
            name="discovery_worker",
            interval_seconds=7200,  # Check every 2 hours (runs daily at scheduled hour)
            settings=settings,
            db=db,
        )
        self.discoverer = discoverer
        self.generator = generator
        self.validator = validator or CodeValidator(settings)
        self._last_run_date: str = ""

    async def tick(self) -> None:
        """Run discovery if it's the scheduled hour and hasn't run today."""
        # Phase 8 (post-Layer-1 fix): structured end-of-tick summary so
        # operators can confirm the worker is alive on the (very common)
        # no-op ticks where it's not the scheduled hour.
        t0 = time.monotonic()
        now = now_utc()
        today = now.strftime("%Y-%m-%d")
        hour = now.hour

        if today == self._last_run_date:
            el_ms = (time.monotonic() - t0) * 1000
            log.info(
                f"DISCOVERY_TICK_SUMMARY | reason=already_ran_today "
                f"date={today} el={el_ms:.0f}ms | {ctx()}"
            )
            return  # Already ran today
        if hour != self.settings.factory.discovery_schedule_hour_utc:
            el_ms = (time.monotonic() - t0) * 1000
            log.info(
                f"DISCOVERY_TICK_SUMMARY | reason=off_schedule "
                f"hour={hour} target_hour={self.settings.factory.discovery_schedule_hour_utc} "
                f"el={el_ms:.0f}ms | {ctx()}"
            )
            return  # Not the right hour

        self._last_run_date = today

        log.info("Discovery worker: starting daily pattern discovery")

        # Run discovery
        patterns = await self.discoverer.run_full_discovery()
        log.info("Discovered {n} validated patterns", n=len(patterns))

        # Generate strategies for top patterns
        n_strategies = 0
        n_validated = 0
        if self.generator and patterns:
            strategies = await self.generator.generate_batch(patterns[:5])
            n_strategies = len(strategies)

            for strategy in strategies:
                passed, errors = self.validator.validate(strategy)
                if passed:
                    n_validated += 1
                    from src.database.repositories.factory_repo import FactoryRepository
                    repo = FactoryRepository(self.db)
                    strategy.status = "validated"
                    await repo.save_generated_strategy(strategy)

            log.info(
                "Generated {g} strategies, {v} passed validation",
                g=n_strategies, v=n_validated,
            )

        el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"DISCOVERY_TICK_SUMMARY | reason=ran patterns={len(patterns)} "
            f"strategies_generated={n_strategies} validated={n_validated} "
            f"el={el_ms:.0f}ms | {ctx()}"
        )
