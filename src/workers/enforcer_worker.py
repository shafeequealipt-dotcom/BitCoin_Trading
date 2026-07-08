"""Enforcer Worker: runs PerformanceEnforcer checks every cycle."""

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.strategies.performance_enforcer import PerformanceEnforcer
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class EnforcerWorker(BaseWorker):
    def __init__(self, settings: Settings, db: DatabaseManager, enforcer: PerformanceEnforcer) -> None:
        interval = getattr(getattr(settings, "enforcer", None), "check_interval_seconds", 300)
        super().__init__(name="enforcer_worker", interval_seconds=float(interval), settings=settings, db=db)
        self.enforcer = enforcer

    async def tick(self) -> None:
        """Run enforcer check every cycle with full error handling."""
        try:
            report = await self.enforcer.check_and_enforce()
            if report:
                log.info(f"ENFORCER_BEAT | total={report.get('trades_today', 0)}T W={report.get('wins', 0)} L={report.get('losses', 0)} wr={report.get('win_rate', 0):.1%} strk={report.get('streak', 0)} hb={'OK' if report.get('heartbeat_ok', True) else 'STALE'} | {ctx()}")
        except Exception as e:
            log.error("Enforcer tick FAILED: {err}", err=str(e))
