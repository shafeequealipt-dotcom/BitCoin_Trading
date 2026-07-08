"""Performance Analytics: answers WHERE profit/loss came from."""

from collections import defaultdict

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.portfolio.models.portfolio_types import PerformanceAttribution
from src.strategies.registry import StrategyRegistry

log = get_logger("portfolio")


class PerformanceAnalytics:
    """Performance attribution by strategy, category, and factors.

    Args:
        db: Database manager.
        registry: Strategy registry.
    """

    def __init__(self, db: DatabaseManager, registry: StrategyRegistry) -> None:
        self.db = db
        self.registry = registry

    async def attribute(self, period: str = "today") -> PerformanceAttribution:
        """Calculate performance attribution for a period."""
        if period == "today":
            time_filter = "date(created_at) = date('now')"
        elif period == "this_week":
            time_filter = "created_at > datetime('now', '-7 days')"
        elif period == "this_month":
            time_filter = "created_at > datetime('now', '-30 days')"
        else:
            time_filter = "1=1"

        rows = await self.db.fetch_all(
            f"SELECT * FROM strategy_trades WHERE {time_filter} ORDER BY created_at",
        )

        if not rows:
            return PerformanceAttribution(period=period)

        # Per-strategy breakdown
        by_strategy: dict[str, dict] = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        by_category: dict[str, dict] = defaultdict(lambda: {"pnl": 0, "trades": 0, "strategies": set()})

        total_pnl = 0.0

        for r in rows:
            name = r.get("strategy_name", "unknown")
            pnl = float(r.get("pnl", 0) or 0)
            was_win = bool(r.get("was_win", 0))
            total_pnl += pnl

            by_strategy[name]["pnl"] += pnl
            by_strategy[name]["trades"] += 1
            if was_win:
                by_strategy[name]["wins"] += 1

            # Get category from registry
            strat = self.registry.get(name)
            cat = strat.category if strat else "unknown"
            by_category[cat]["pnl"] += pnl
            by_category[cat]["trades"] += 1
            by_category[cat]["strategies"].add(name)

        # Build contributions
        contributions = []
        for name, data in sorted(by_strategy.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = data["wins"] / data["trades"] if data["trades"] > 0 else 0
            contrib = (data["pnl"] / total_pnl * 100) if total_pnl != 0 else 0
            contributions.append({
                "name": name, "pnl_usd": round(data["pnl"], 2),
                "trades": data["trades"], "win_rate": round(wr, 3),
                "contribution_pct": round(contrib, 1),
            })

        cat_contrib = {}
        for cat, data in by_category.items():
            cat_contrib[cat] = {
                "pnl_usd": round(data["pnl"], 2),
                "trades": data["trades"],
                "strategies_count": len(data["strategies"]),
            }

        best = contributions[0]["name"] if contributions else ""
        worst = contributions[-1]["name"] if contributions else ""

        return PerformanceAttribution(
            period=period,
            total_pnl_usd=round(total_pnl, 2),
            strategy_contributions=contributions,
            category_contributions=cat_contrib,
            best_strategy=best,
            worst_strategy=worst,
        )

    async def strategy_comparison(self, strategy_names: list[str] | None = None) -> list[dict]:
        """Side-by-side strategy comparison."""
        strategies = self.registry.get_all() if not strategy_names else [
            self.registry.get(n) for n in strategy_names if self.registry.get(n)
        ]
        comparison = []
        for s in strategies:
            perf = self.registry.get_performance(s.name)
            comparison.append({
                "name": s.name, "category": s.category,
                "trades": perf.total_trades, "win_rate": round(perf.win_rate, 3),
                "profit_factor": round(perf.profit_factor, 2),
                "avg_pnl_pct": round(perf.avg_pnl_pct, 3),
                "streak": perf.current_streak, "enabled": perf.enabled,
            })
        return sorted(comparison, key=lambda x: x["profit_factor"], reverse=True)
