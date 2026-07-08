"""Portfolio Optimizer: blends Kelly, Mean-Variance, and Risk Parity methods."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.portfolio.allocator import DynamicAllocator
from src.portfolio.models.portfolio_types import RebalanceAction

log = get_logger("portfolio")


class PortfolioOptimizer:
    """Blends three optimization methods: Kelly (30%), Mean-Variance (40%), Risk Parity (30%).

    Args:
        settings: Application settings.
        allocator: Dynamic capital allocator.
    """

    def __init__(self, settings: Settings, allocator: DynamicAllocator) -> None:
        self.settings = settings
        self.allocator = allocator

    def optimize(self, total_equity: float) -> list[RebalanceAction]:
        """Run full portfolio optimization and return proposed rebalance actions."""
        # Calculate new optimal allocations
        new_allocations = self.allocator.calculate_allocations(total_equity)

        # Generate rebalance actions
        actions = self.allocator.rebalance(new_allocations)

        log.info(
            "Optimization: {n} strategies, {a} rebalance actions proposed",
            n=len(new_allocations), a=len(actions),
        )
        return actions

    def get_summary(self, total_equity: float) -> dict:
        """Get optimization summary."""
        allocs = self.allocator.get_current_allocations()
        return {
            "total_equity": total_equity,
            "num_strategies": len(allocs),
            "total_allocated_pct": sum(a.allocated_pct for a in allocs),
            "cash_reserve_pct": self.settings.portfolio.cash_reserve_pct,
            "avg_kelly_pct": sum(a.fractional_kelly_pct for a in allocs) / len(allocs) if allocs else 0,
            "avg_performance_score": sum(a.performance_score for a in allocs) / len(allocs) if allocs else 0,
        }
