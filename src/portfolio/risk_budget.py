"""Risk Budget Manager: controls total portfolio risk across strategy tiers."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.portfolio.models.portfolio_types import RiskBudget

log = get_logger("portfolio")


class RiskBudgetManager:
    """Distributes risk budget across strategy tiers and tracks usage.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self.settings = settings
        self.db = db
        self._budget: RiskBudget | None = None
        self._used: dict[str, float] = {}
        self._today: str = ""

    def calculate_budget(self, account_equity: float) -> RiskBudget:
        """Calculate risk budget based on current equity."""
        cfg = self.settings.portfolio
        budget = RiskBudget(
            total_daily_risk_pct=cfg.daily_risk_budget_pct,
            proven_strategies_pct=cfg.proven_strategies_budget_pct,
            ai_strategies_pct=cfg.ai_strategies_budget_pct,
            trial_strategies_pct=cfg.trial_strategies_budget_pct,
            cash_reserve_pct=cfg.cash_reserve_pct,
            remaining_risk_pct=cfg.daily_risk_budget_pct,
        )
        self._budget = budget
        return budget

    def can_trade(self, strategy_name: str, proposed_risk_usd: float) -> tuple[bool, str]:
        """Check if a trade fits within the risk budget."""
        if not self._budget:
            return True, ""

        self._check_new_day()

        total_used = sum(self._used.values())
        if total_used + proposed_risk_usd > self._budget.total_daily_risk_pct * 100:
            return False, "Daily portfolio risk budget exceeded"

        return True, ""

    def update_used_risk(self, strategy_name: str, risk_usd: float) -> None:
        """Record risk used by a trade."""
        self._check_new_day()
        self._used[strategy_name] = self._used.get(strategy_name, 0) + risk_usd
        if self._budget:
            self._budget.used_risk_pct = sum(self._used.values())
            self._budget.remaining_risk_pct = max(0, self._budget.total_daily_risk_pct - self._budget.used_risk_pct)

    def reset_daily(self) -> None:
        """Reset daily risk usage."""
        self._used.clear()
        if self._budget:
            self._budget.used_risk_pct = 0
            self._budget.remaining_risk_pct = self._budget.total_daily_risk_pct

    def get_risk_utilization(self) -> dict:
        """Return current risk usage breakdown."""
        return {
            "total_used": sum(self._used.values()),
            "per_strategy": dict(self._used),
            "budget": self._budget.to_dict() if self._budget else {},
        }

    def _check_new_day(self) -> None:
        today = now_utc().strftime("%Y-%m-%d")
        if today != self._today:
            self._today = today
            self.reset_daily()
