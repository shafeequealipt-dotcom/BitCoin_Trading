"""Portfolio repository: CRUD for allocations, correlations, risk budgets, and reports."""

import json

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.portfolio.models.portfolio_types import StrategyAllocation

log = get_logger("portfolio")


class PortfolioRepository:
    """CRUD for portfolio optimizer data."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def save_allocation(self, alloc: StrategyAllocation) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO portfolio_allocations "
            "(strategy_name, category, full_kelly_pct, fractional_kelly_pct, "
            "allocated_pct, allocated_usd, max_position_usd, max_leverage, "
            "performance_score, correlation_penalty, risk_contribution_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (alloc.strategy_name, alloc.category, alloc.full_kelly_pct,
             alloc.fractional_kelly_pct, alloc.allocated_pct, alloc.allocated_usd,
             alloc.max_position_usd, alloc.max_leverage, alloc.performance_score,
             alloc.correlation_penalty, alloc.risk_contribution_pct),
        )

    async def get_all_allocations(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM portfolio_allocations ORDER BY allocated_pct DESC",
        )
        return [dict(r) for r in rows] if rows else []

    async def save_correlation(
        self, strategy_a: str, strategy_b: str, correlation: float,
        sample_size: int, period_days: int,
    ) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO correlation_matrix "
            "(strategy_a, strategy_b, correlation, sample_size, period_days) "
            "VALUES (?,?,?,?,?)",
            (strategy_a, strategy_b, correlation, sample_size, period_days),
        )

    async def save_rebalance(
        self, name: str, old_pct: float, new_pct: float, change: float, reason: str,
    ) -> None:
        await self._db.execute(
            "INSERT INTO rebalance_history "
            "(strategy_name, old_allocation_pct, new_allocation_pct, change_pct, reason) "
            "VALUES (?,?,?,?,?)",
            (name, old_pct, new_pct, change, reason),
        )

    async def save_stress_test(
        self, name: str, desc: str, impact: float, loss: float,
        survival: bool, margin_risk: bool,
    ) -> None:
        await self._db.execute(
            "INSERT INTO stress_test_results "
            "(scenario_name, description, portfolio_impact_pct, loss_usd, "
            "survival, margin_call_risk) VALUES (?,?,?,?,?,?)",
            (name, desc, impact, loss, 1 if survival else 0, 1 if margin_risk else 0),
        )
