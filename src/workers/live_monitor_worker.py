"""Live Monitor Worker: checks for emerging patterns every 5 minutes."""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.factory.live_monitor import LivePatternMonitor
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class LiveMonitorWorker(BaseWorker):
    """Monitors for emerging patterns in real-time.

    Args:
        settings: Application settings.
        db: Database manager.
        monitor: LivePatternMonitor instance.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        monitor: LivePatternMonitor,
    ) -> None:
        super().__init__(
            name="live_monitor_worker",
            interval_seconds=float(settings.factory.live_monitor_interval_seconds),
            settings=settings,
            db=db,
        )
        self.monitor = monitor

    async def tick(self) -> None:
        """Check for emerging patterns."""
        # Phase 8 (post-Layer-1 fix): structured end-of-tick summary so
        # operators can see the worker is alive on quiet ticks (no hot
        # patterns), not just on the noisy ones.
        t0 = time.monotonic()
        emerging = await self.monitor.check_emerging()

        hot = [e for e in emerging if e.urgency in ("critical", "high")]
        if hot:
            log.info(
                "LiveMonitor: {n} HOT emerging patterns detected!",
                n=len(hot),
            )
            for e in hot:
                log.info(
                    "  HOT: {desc} (occurrences={occ}, WR={wr:.0%}, urgency={u})",
                    desc=e.description[:80], occ=e.recent_occurrences,
                    wr=e.recent_win_rate, u=e.urgency,
                )

        el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"LIVE_MONITOR_TICK_SUMMARY | emerging={len(emerging)} "
            f"hot={len(hot)} el={el_ms:.0f}ms | {ctx()}"
        )
