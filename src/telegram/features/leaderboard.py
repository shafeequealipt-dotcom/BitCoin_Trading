"""Strategy Leaderboard: performance ranking table."""

from src.core.logging import get_logger
from src.strategies.registry import StrategyRegistry
from src.telegram.ui.formatters import format_timestamp

log = get_logger("telegram")


class Leaderboard:
    def __init__(self, registry: StrategyRegistry) -> None:
        self.registry = registry

    def generate(self, top_n: int = 10) -> str:
        """Generate leaderboard message."""
        summary = self.registry.get_registry_summary()
        strategies = sorted(summary["strategies"], key=lambda x: x["profit_factor"], reverse=True)

        msg = f"\U0001f3c6 <b>STRATEGY LEADERBOARD</b>\n\n"
        msg += f"Total: {summary['total_strategies']} | Active: {summary['enabled']}\n\n"

        medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
        for i, s in enumerate(strategies[:top_n]):
            prefix = medals[i] if i < 3 else f"{i+1}."
            msg += (
                f"{prefix} <b>{s['name']}</b>\n"
                f"   WR={s['win_rate']:.0%} PF={s['profit_factor']:.1f} "
                f"trades={s['total_trades']} cat={s['category']}\n"
            )

        msg += f"\n\U0001f550 {format_timestamp()}"
        return msg
