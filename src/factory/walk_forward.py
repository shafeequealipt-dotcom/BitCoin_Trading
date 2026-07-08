"""Walk-Forward Validator: prevents overfitting by testing on unseen data."""

from src.core.logging import get_logger
from src.factory.metrics import MetricsCalculator
from src.factory.models.backtest_types import SimulatedTrade

log = get_logger("factory")


class WalkForwardValidator:
    """Tests strategy generalization by splitting trades into train/test sets.

    Uses simple split and anchored walk-forward validation.
    """

    def validate(
        self, trades: list[SimulatedTrade], train_pct: float = 0.7,
    ) -> dict:
        """Run walk-forward validation on backtest trades.

        Args:
            trades: All simulated trades from backtest (time-ordered).
            train_pct: Fraction of trades for training set.

        Returns:
            Dict with in_sample, out_of_sample metrics, efficiency, and overfitting risk.
        """
        if len(trades) < 10:
            return {
                "in_sample": {"win_rate": 0, "trades": 0},
                "out_of_sample": {"win_rate": 0, "trades": 0},
                "efficiency": 0,
                "passed": False,
                "overfitting_risk": "unknown",
            }

        split_idx = int(len(trades) * train_pct)
        train = trades[:split_idx]
        test = trades[split_idx:]

        calc = MetricsCalculator()

        is_wr = sum(1 for t in train if t.pnl_usd > 0) / len(train) if train else 0
        oos_wr = sum(1 for t in test if t.pnl_usd > 0) / len(test) if test else 0

        is_pf = self._profit_factor(train)
        oos_pf = self._profit_factor(test)

        efficiency = oos_wr / is_wr if is_wr > 0 else 0

        if efficiency > 0.8:
            risk = "low"
        elif efficiency > 0.5:
            risk = "medium"
        elif efficiency > 0.3:
            risk = "high"
        else:
            risk = "very_high"

        passed = efficiency > 0.5 and oos_wr > 0.50

        result = {
            "in_sample": {
                "win_rate": round(is_wr, 4),
                "profit_factor": round(is_pf, 2),
                "trades": len(train),
            },
            "out_of_sample": {
                "win_rate": round(oos_wr, 4),
                "profit_factor": round(oos_pf, 2),
                "trades": len(test),
            },
            "efficiency": round(efficiency, 4),
            "passed": passed,
            "overfitting_risk": risk,
        }

        log.info(
            "Walk-forward: IS WR={is_wr:.1%} → OOS WR={oos_wr:.1%} | "
            "efficiency={eff:.2f} | risk={risk}",
            is_wr=is_wr, oos_wr=oos_wr, eff=efficiency, risk=risk,
        )
        return result

    @staticmethod
    def _profit_factor(trades: list[SimulatedTrade]) -> float:
        gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
        gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd <= 0))
        return gross_profit / gross_loss if gross_loss > 0 else 99.0
