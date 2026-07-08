"""Weekly Adaptive Optimizer (Strategy K4): adjusts strategy weights and parameters.

Runs weekly to analyze each strategy's recent performance, adjust ensemble
weights, disable underperforming strategies, and propose parameter changes.
"""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.strategies.registry import StrategyRegistry

log = get_logger("strategies")


class WeeklyOptimizer:
    """Analyzes strategy performance and adjusts weights weekly.

    Args:
        settings: Application settings.
        db: Database manager.
        registry: Strategy registry with performance data.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        registry: StrategyRegistry,
    ) -> None:
        self.settings = settings
        self.db = db
        self.registry = registry
        self._underperform_weeks: dict[str, int] = {}

    async def run_optimization(self) -> dict:
        """Run weekly optimization cycle.

        Returns:
            Optimization report with changes made.
        """
        cfg = self.settings.optimizer
        report: dict = {
            "timestamp": now_utc().isoformat(),
            "strategies_analyzed": 0,
            "weight_adjustments": [],
            "disabled": [],
            "re_enabled": [],
        }

        strategies = self.registry.get_all()
        report["strategies_analyzed"] = len(strategies)

        for strategy in strategies:
            name = strategy.name
            perf = self.registry.get_performance(name)

            if perf.total_trades < cfg.min_trades_for_optimization:
                continue

            baseline_wr = 0.5
            delta = (perf.win_rate - baseline_wr) * 100

            if delta > cfg.underperform_threshold_pct:
                # Outperforming: increase weight
                adj = cfg.weight_adjustment_pct / 100
                new_weight = perf.ensemble_weight * (1 + adj)
                self.registry.set_ensemble_weight(name, new_weight)
                self._underperform_weeks[name] = 0
                report["weight_adjustments"].append({
                    "strategy": name,
                    "direction": "increase",
                    "old_weight": round(perf.ensemble_weight, 2),
                    "new_weight": round(new_weight, 2),
                    "win_rate": round(perf.win_rate, 3),
                })

            elif delta < -cfg.underperform_threshold_pct:
                # Underperforming: decrease weight
                adj = cfg.weight_adjustment_pct / 100
                new_weight = perf.ensemble_weight * (1 - adj)
                self.registry.set_ensemble_weight(name, new_weight)

                weeks = self._underperform_weeks.get(name, 0) + 1
                self._underperform_weeks[name] = weeks

                report["weight_adjustments"].append({
                    "strategy": name,
                    "direction": "decrease",
                    "old_weight": round(perf.ensemble_weight, 2),
                    "new_weight": round(new_weight, 2),
                    "win_rate": round(perf.win_rate, 3),
                    "underperform_weeks": weeks,
                })

                if weeks >= cfg.disable_after_weeks and perf.enabled:
                    self.registry.set_enabled(name, False)
                    report["disabled"].append({
                        "strategy": name,
                        "reason": f"Underperforming for {weeks} consecutive weeks",
                        "win_rate": round(perf.win_rate, 3),
                    })
            else:
                self._underperform_weeks[name] = 0

        log.info(
            "Optimization complete: {n} strategies analyzed, "
            "{adj} weight adjustments, {dis} disabled",
            n=report["strategies_analyzed"],
            adj=len(report["weight_adjustments"]),
            dis=len(report["disabled"]),
        )

        return report
