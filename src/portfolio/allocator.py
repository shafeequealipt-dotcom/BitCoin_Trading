"""Dynamic Capital Allocator: decides how much capital each strategy gets."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.portfolio.correlation import CorrelationTracker
from src.portfolio.kelly import KellyCalculator
from src.portfolio.models.portfolio_types import RebalanceAction, StrategyAllocation
from src.portfolio.risk_budget import RiskBudgetManager
from src.strategies.registry import StrategyRegistry

log = get_logger("portfolio")


class DynamicAllocator:
    """Allocates capital across strategies based on Kelly, correlation, and performance.

    Args:
        settings: Application settings.
        registry: Strategy registry with performance data.
        kelly: Kelly criterion calculator.
        correlation: Correlation tracker.
        risk_budget: Risk budget manager.
    """

    def __init__(
        self, settings: Settings, registry: StrategyRegistry,
        kelly: KellyCalculator, correlation: CorrelationTracker,
        risk_budget: RiskBudgetManager,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.kelly = kelly
        self.correlation = correlation
        self.risk_budget = risk_budget
        self._allocations: list[StrategyAllocation] = []

    def calculate_allocations(self, total_equity: float) -> list[StrategyAllocation]:
        """Calculate optimal allocation for each active strategy."""
        cfg = self.settings.portfolio
        strategies = self.registry.get_enabled()

        if not strategies:
            return []

        allocations: list[StrategyAllocation] = []

        for strategy in strategies:
            perf = self.registry.get_performance(strategy.name)
            kelly_data = self.kelly.calculate_for_strategy(perf)

            # Performance score
            wr = perf.win_rate
            pf = perf.profit_factor
            score = (
                wr * 0.35 +
                min(pf / 3, 1.0) * 0.35 +
                min(perf.total_trades / 50, 1.0) * 0.15 +
                (1 if perf.current_streak > 0 else 0.8) * 0.15
            )

            # Correlation penalty
            corr_penalty = self.correlation.calculate_correlation_penalty(strategy.name)

            # Category tier
            category = strategy.category
            if category == "ai_generated":
                tier = "ai"
            elif category in ("trial",):
                tier = "trial"
            else:
                tier = "proven"

            # Raw allocation
            kelly_pct = kelly_data["dynamic_kelly_pct"]
            raw = kelly_pct * score * max(0.5, 1 - corr_penalty)

            alloc = StrategyAllocation(
                strategy_name=strategy.name,
                category=category,
                full_kelly_pct=kelly_data["full_kelly_pct"],
                fractional_kelly_pct=kelly_pct,
                allocated_pct=raw,
                performance_score=score,
                correlation_penalty=corr_penalty,
                max_leverage=3,
            )
            allocations.append(alloc)

        # Normalize to sum to 100% (minus reserve)
        usable_pct = 100 - cfg.cash_reserve_pct
        total_raw = sum(a.allocated_pct for a in allocations)
        if total_raw > 0:
            for a in allocations:
                a.allocated_pct = (a.allocated_pct / total_raw) * usable_pct
                a.allocated_pct = max(cfg.min_strategy_allocation_pct,
                                      min(a.allocated_pct, cfg.max_strategy_allocation_pct))
                a.allocated_usd = total_equity * a.allocated_pct / 100
                a.max_position_usd = a.allocated_usd * 0.5  # Max 50% of allocation per trade

        # Enforce category tier budgets
        tier_budgets = {
            "proven": cfg.proven_strategies_budget_pct,
            "ai": cfg.ai_strategies_budget_pct,
            "trial": cfg.trial_strategies_budget_pct,
        }
        for tier_name, budget_pct in tier_budgets.items():
            tier_allocs = [a for a in allocations
                           if (a.category == "ai_generated" and tier_name == "ai")
                           or (a.category == "trial" and tier_name == "trial")
                           or (a.category not in ("ai_generated", "trial") and tier_name == "proven")]
            tier_total = sum(a.allocated_pct for a in tier_allocs)
            if tier_total > budget_pct and tier_total > 0:
                scale = budget_pct / tier_total
                for a in tier_allocs:
                    a.allocated_pct *= scale
                    a.allocated_usd = total_equity * a.allocated_pct / 100
                log.info(
                    "Category budget enforced: {tier} capped from {before:.1f}% to {after:.1f}%",
                    tier=tier_name, before=tier_total, after=budget_pct,
                )

        # Re-normalize after caps/floors
        total_after = sum(a.allocated_pct for a in allocations)
        if total_after > 0 and abs(total_after - usable_pct) > 0.1:
            factor = usable_pct / total_after
            for a in allocations:
                a.allocated_pct *= factor
                a.allocated_usd = total_equity * a.allocated_pct / 100

        self._allocations = allocations
        return allocations

    def get_current_allocations(self) -> list[StrategyAllocation]:
        return list(self._allocations)

    def rebalance(self, new_allocations: list[StrategyAllocation]) -> list[RebalanceAction]:
        """Compare new vs current allocations and generate rebalance actions."""
        cfg = self.settings.portfolio
        current_map = {a.strategy_name: a for a in self._allocations}
        actions: list[RebalanceAction] = []

        for new in new_allocations:
            old = current_map.get(new.strategy_name)
            old_pct = old.allocated_pct if old else 0
            change = new.allocated_pct - old_pct

            if abs(change) >= cfg.min_rebalance_change_pct:
                actions.append(RebalanceAction(
                    strategy_name=new.strategy_name,
                    current_allocation_pct=old_pct,
                    proposed_allocation_pct=new.allocated_pct,
                    change_pct=change,
                    reason=f"Performance score changed ({new.performance_score:.2f})",
                ))

        return actions
