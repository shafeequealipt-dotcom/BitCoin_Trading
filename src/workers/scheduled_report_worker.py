"""Scheduled Report Worker: sends scheduled reports at configured times."""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.features.scheduled_reports import ScheduledReportEngine
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class ScheduledReportWorker(BaseWorker):
    def __init__(self, settings: Settings, db: DatabaseManager, engine: ScheduledReportEngine) -> None:
        super().__init__(name="scheduled_report_worker", interval_seconds=300, settings=settings, db=db)
        self.engine = engine

    async def tick(self) -> None:
        # Phase 8 (post-Layer-1 fix): structured end-of-tick summary so
        # operators can see the worker is alive even when zero reports
        # are due (the typical case).
        #
        # Phase conn-pool/p5-3 (2026-05-14): gate the per-300 s DB poll
        # on the engine's in-memory active-reports count. ``scheduled_reports``
        # has had 0 rows across all observed sessions; the cached count
        # eliminates the per-tick DB read.
        t0 = time.monotonic()
        if not await self.engine.has_active():
            el_ms = (time.monotonic() - t0) * 1000
            log.info(
                f"SCHEDULED_REPORT_TICK_SUMMARY | due=0 cached=Y "
                f"el={el_ms:.0f}ms | {ctx()}"
            )
            return
        due = await self.engine.get_due_reports()
        el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"SCHEDULED_REPORT_TICK_SUMMARY | due={len(due)} "
            f"el={el_ms:.0f}ms | {ctx()}"
        )
        if due:
            log.debug("Scheduled reports: {n} due", n=len(due))
