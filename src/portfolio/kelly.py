"""Kelly Criterion Calculator: optimal position sizing based on edge and odds."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.strategies.models.signal_types import StrategyPerformance

log = get_logger("portfolio")


class KellyCalculator:
    """Calculates Kelly Criterion for optimal position sizing.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings | None) -> None:
        self.fraction = 0.25
        if settings and hasattr(settings, 'portfolio'):
            self.fraction = settings.portfolio.kelly_fraction

    def full_kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Calculate full Kelly fraction.

        Formula: f* = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        """
        if avg_win <= 0 or win_rate <= 0:
            return 0.0
        if avg_loss <= 0:
            return min(win_rate, 1.0)

        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        return max(0.0, min(kelly, 1.0))

    def fractional_kelly(
        self, win_rate: float, avg_win: float, avg_loss: float,
        fraction: float | None = None,
    ) -> float:
        """Use fraction of full Kelly (default 25%) for safety."""
        f = fraction or self.fraction
        return self.full_kelly(win_rate, avg_win, avg_loss) * f

    def dynamic_kelly(
        self, win_rate: float, avg_win: float, avg_loss: float,
        recent_streak: int = 0, drawdown_pct: float = 0.0,
    ) -> float:
        """Kelly adjusted for recent performance context."""
        base = self.fractional_kelly(win_rate, avg_win, avg_loss)

        # Losing streak: reduce
        if recent_streak < -3:
            base *= 0.7
        # In drawdown: reduce
        if drawdown_pct > 5:
            base *= 0.6
        elif drawdown_pct > 3:
            base *= 0.8
        # Winning streak + low drawdown: slightly more aggressive
        elif recent_streak > 3 and drawdown_pct < 1:
            base = self.fractional_kelly(win_rate, avg_win, avg_loss, fraction=0.30)

        return max(0.0, min(base, 0.5))

    def calculate_for_strategy(self, perf: StrategyPerformance) -> dict:
        """Calculate all Kelly variants for a strategy."""
        if perf.total_trades < 20:
            return {
                "full_kelly_pct": 0,
                "fractional_kelly_pct": 2.0,
                "dynamic_kelly_pct": 2.0,
                "suggested_allocation_pct": 2.0,
                "reasoning": "Insufficient data (<20 trades), using minimum allocation",
            }

        full = self.full_kelly(perf.win_rate, perf.avg_win_pct, perf.avg_loss_pct) * 100
        frac = self.fractional_kelly(perf.win_rate, perf.avg_win_pct, perf.avg_loss_pct) * 100
        dynamic = self.dynamic_kelly(
            perf.win_rate, perf.avg_win_pct, perf.avg_loss_pct,
            perf.current_streak, 0,
        ) * 100

        return {
            "full_kelly_pct": round(full, 2),
            "fractional_kelly_pct": round(frac, 2),
            "dynamic_kelly_pct": round(dynamic, 2),
            "suggested_allocation_pct": round(dynamic, 2),
            "reasoning": f"WR={perf.win_rate:.0%}, W/L={perf.avg_win_pct:.1f}/{perf.avg_loss_pct:.1f}",
        }
