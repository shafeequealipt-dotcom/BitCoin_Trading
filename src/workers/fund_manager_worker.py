"""Fund Manager Worker — runs fund manager periodic checks every 60 seconds."""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class FundManagerWorker(BaseWorker):
    """Runs IntelligentFundManager.update_state() periodically."""

    def __init__(self, settings, db, fund_manager) -> None:
        interval = getattr(settings.fund_manager, "check_interval_seconds", 60)
        super().__init__(
            name="fund_manager_worker",
            interval_seconds=interval,
            settings=settings,
            db=db,
        )
        self.fund_manager = fund_manager

    async def tick(self) -> None:
        try:
            await self.fund_manager.update_state()
            log.debug(f"FUND_BEAT | ok=Y | {ctx()}")
        except Exception as e:
            log.error("Fund Manager tick failed: {err}", err=str(e))
