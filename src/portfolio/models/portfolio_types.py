"""Data models for portfolio management."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class StrategyAllocation:
    """Capital allocation for a single strategy."""
    strategy_name: str
    category: str = ""
    full_kelly_pct: float = 0.0
    fractional_kelly_pct: float = 0.0
    allocated_pct: float = 0.0
    allocated_usd: float = 0.0
    max_position_usd: float = 0.0
    max_leverage: int = 3
    performance_score: float = 0.0
    correlation_penalty: float = 0.0
    regime_bonus: float = 0.0
    recency_factor: float = 1.0
    risk_contribution_pct: float = 0.0
    var_contribution: float = 0.0
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "category": self.category,
            "allocated_pct": round(self.allocated_pct, 2),
            "allocated_usd": round(self.allocated_usd, 2),
            "max_position_usd": round(self.max_position_usd, 2),
            "max_leverage": self.max_leverage,
            "performance_score": round(self.performance_score, 3),
            "kelly_pct": round(self.fractional_kelly_pct, 3),
            "correlation_penalty": round(self.correlation_penalty, 3),
        }


@dataclass
class CorrelationPair:
    """Correlation between two strategies' returns."""
    strategy_a: str
    strategy_b: str
    correlation: float = 0.0
    rolling_period_days: int = 30
    sample_size: int = 0
    updated_at: Optional[datetime] = None


@dataclass
class RiskBudget:
    """Risk budget allocation across strategy tiers."""
    total_daily_risk_pct: float = 5.0
    proven_strategies_pct: float = 55.0
    ai_strategies_pct: float = 30.0
    trial_strategies_pct: float = 10.0
    cash_reserve_pct: float = 5.0
    used_risk_pct: float = 0.0
    remaining_risk_pct: float = 5.0
    strategy_budgets: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_daily_risk_pct": self.total_daily_risk_pct,
            "proven_pct": self.proven_strategies_pct,
            "ai_pct": self.ai_strategies_pct,
            "trial_pct": self.trial_strategies_pct,
            "reserve_pct": self.cash_reserve_pct,
            "used_pct": round(self.used_risk_pct, 2),
            "remaining_pct": round(self.remaining_risk_pct, 2),
        }


@dataclass
class StressTestResult:
    """Result of a stress test scenario."""
    scenario_name: str
    description: str = ""
    estimated_portfolio_impact_pct: float = 0.0
    estimated_loss_usd: float = 0.0
    strategies_most_affected: list[str] = field(default_factory=list)
    strategies_that_profit: list[str] = field(default_factory=list)
    survival: bool = True
    margin_call_risk: bool = False

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario_name,
            "impact_pct": round(self.estimated_portfolio_impact_pct, 2),
            "loss_usd": round(self.estimated_loss_usd, 2),
            "survival": self.survival,
            "margin_call_risk": self.margin_call_risk,
        }


@dataclass
class PerformanceAttribution:
    """Which strategies contributed to PnL."""
    period: str
    total_pnl_usd: float = 0.0
    total_pnl_pct: float = 0.0
    strategy_contributions: list[dict] = field(default_factory=list)
    category_contributions: dict = field(default_factory=dict)
    best_strategy: str = ""
    worst_strategy: str = ""
    best_trade: dict = field(default_factory=dict)
    worst_trade: dict = field(default_factory=dict)
    regime_factor: float = 0.0
    timing_factor: float = 0.0
    sizing_factor: float = 0.0

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "best_strategy": self.best_strategy,
            "worst_strategy": self.worst_strategy,
            "strategies": len(self.strategy_contributions),
        }


@dataclass
class RebalanceAction:
    """A proposed change to portfolio allocation."""
    strategy_name: str
    current_allocation_pct: float = 0.0
    proposed_allocation_pct: float = 0.0
    change_pct: float = 0.0
    reason: str = ""
    approved: bool = False

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "current_pct": round(self.current_allocation_pct, 2),
            "proposed_pct": round(self.proposed_allocation_pct, 2),
            "change_pct": round(self.change_pct, 2),
            "reason": self.reason,
            "approved": self.approved,
        }
