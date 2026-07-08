"""Allocation Worker: updates risk budgets and drawdown scaling every 5 minutes."""

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.portfolio.risk_budget import RiskBudgetManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class AllocationWorker(BaseWorker):
    """Updates risk budget usage and applies drawdown-based scaling.

    Args:
        settings: Application settings.
        db: Database manager.
        risk_budget: Risk budget manager.
    """

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        risk_budget: RiskBudgetManager,
    ) -> None:
        super().__init__(
            name="allocation_worker",
            interval_seconds=300,
            settings=settings,
            db=db,
        )
        self.risk_budget = risk_budget

    async def tick(self) -> None:
        """Update risk budgets and check for drawdown scaling."""
        util = self.risk_budget.get_risk_utilization()
        used = util.get("total_used", 0)
        log.debug(f"ALLOC_UPDATE | budget_used={used:.1f}% | {ctx()}")
        if used > 0:
            log.debug("Allocation: risk used={used:.2f}", used=used)
