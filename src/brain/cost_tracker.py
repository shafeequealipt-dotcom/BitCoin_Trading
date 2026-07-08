"""API cost tracking and daily budget enforcement for Claude Brain."""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("brain")


class CostTracker:
    """Tracks Claude API call costs and enforces daily budget limits.

    Args:
        daily_budget_usd: Maximum daily spend. Default $1.00.
    """

    # Claude Sonnet pricing (per million tokens)
    INPUT_PRICE_PER_MILLION: float = 3.00
    OUTPUT_PRICE_PER_MILLION: float = 15.00

    def __init__(self, daily_budget_usd: float = 1.00) -> None:
        self.daily_budget_usd = daily_budget_usd
        self.today_cost: float = 0.0
        self.today_calls: int = 0
        self.today_date: str = ""
        self.lifetime_cost: float = 0.0
        self.lifetime_calls: int = 0

    def _reset_if_new_day(self) -> None:
        """Reset daily counters if the date has changed."""
        today = now_utc().strftime("%Y-%m-%d")
        if today != self.today_date:
            self.today_cost = 0.0
            self.today_calls = 0
            self.today_date = today

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate USD cost for a Claude API call.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.

        Returns:
            Cost in USD.
        """
        input_cost = (input_tokens / 1_000_000) * self.INPUT_PRICE_PER_MILLION
        output_cost = (output_tokens / 1_000_000) * self.OUTPUT_PRICE_PER_MILLION
        return input_cost + output_cost

    def record_call(self, input_tokens: int, output_tokens: int) -> float:
        """Record a completed API call and return its cost.

        Args:
            input_tokens: Input tokens used.
            output_tokens: Output tokens used.

        Returns:
            Cost of this call in USD.
        """
        self._reset_if_new_day()
        cost = self.calculate_cost(input_tokens, output_tokens)
        self.today_cost += cost
        self.today_calls += 1
        self.lifetime_cost += cost
        self.lifetime_calls += 1
        log.info(f"COST_TRACK | cost=${cost:.4f} today=${self.today_cost:.4f} calls={self.today_calls} | {ctx()}")
        log.info(
            "Brain cost: ${cost:.4f} this call | ${today:.4f} today ({calls} calls)",
            cost=cost, today=self.today_cost, calls=self.today_calls,
        )
        return cost

    def can_afford_call(self) -> bool:
        """Check if budget allows another API call.

        Estimates the maximum cost of one call and checks against remaining budget.

        Returns:
            True if budget allows another call.
        """
        self._reset_if_new_day()
        estimated_max = self.calculate_cost(4000, 1000)
        return self.today_cost + estimated_max < self.daily_budget_usd

    def get_daily_stats(self) -> dict:
        """Get today's cost statistics."""
        self._reset_if_new_day()
        remaining = max(0, self.daily_budget_usd - self.today_cost)
        used_pct = (self.today_cost / self.daily_budget_usd * 100) if self.daily_budget_usd > 0 else 0
        return {
            "date": self.today_date,
            "calls_today": self.today_calls,
            "cost_today_usd": round(self.today_cost, 6),
            "budget_usd": self.daily_budget_usd,
            "budget_remaining_usd": round(remaining, 6),
            "budget_used_pct": round(used_pct, 2),
        }

    def get_monthly_estimate(self) -> dict:
        """Estimate monthly cost based on today's usage."""
        self._reset_if_new_day()
        daily_avg = self.today_cost if self.today_calls > 0 else 0
        monthly = daily_avg * 30
        return {
            "daily_avg_usd": round(daily_avg, 4),
            "monthly_estimate_usd": round(monthly, 2),
            "monthly_estimate_inr": round(monthly * 85, 2),
        }
