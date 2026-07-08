"""Backtest Engine: runs full strategy simulation on historical data."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.factory.metrics import MetricsCalculator
from src.factory.models.backtest_types import BacktestConfig, BacktestResult
from src.factory.monte_carlo import MonteCarloSimulator
from src.factory.walk_forward import WalkForwardValidator

log = get_logger("factory")


class BacktestEngine:
    """Runs strategy backtests with walk-forward and Monte Carlo validation.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.metrics = MetricsCalculator()
        self.walk_forward = WalkForwardValidator()
        self.monte_carlo = MonteCarloSimulator()

    def run_on_trades(
        self,
        strategy_id: str,
        trades: list,
        config: BacktestConfig | None = None,
    ) -> BacktestResult:
        """Run metrics, walk-forward, and Monte Carlo on pre-simulated trades.

        This is the primary entry point for backtesting.
        """
        cfg = config or BacktestConfig(strategy_id=strategy_id)
        bt_cfg = self.settings.backtesting

        result = BacktestResult(
            id=generate_id("bt"),
            strategy_id=strategy_id,
            config=cfg,
            trades=trades,
            created_at=now_utc(),
        )

        if not trades:
            result.fail_reasons.append("No trades generated")
            return result

        # Step 1: Calculate metrics
        metrics = self.metrics.calculate(trades, cfg)
        result.total_trades = metrics["total_trades"]
        result.winning_trades = metrics["winning_trades"]
        result.losing_trades = metrics["losing_trades"]
        result.win_rate = metrics["win_rate"]
        result.avg_win_pct = metrics.get("avg_win_pct", 0)
        result.avg_loss_pct = metrics.get("avg_loss_pct", 0)
        result.profit_factor = metrics["profit_factor"]
        result.expected_value = metrics.get("expected_value", 0)
        result.total_return_pct = metrics["total_return_pct"]
        result.max_drawdown_pct = metrics["max_drawdown_pct"]
        result.sharpe_ratio = metrics["sharpe_ratio"]
        result.sortino_ratio = metrics.get("sortino_ratio", 0)
        result.calmar_ratio = metrics.get("calmar_ratio", 0)
        result.avg_hold_minutes = metrics.get("avg_hold_minutes", 0)
        result.best_trade_pct = metrics.get("best_trade_pct", 0)
        result.worst_trade_pct = metrics.get("worst_trade_pct", 0)
        result.longest_win_streak = metrics.get("longest_win_streak", 0)
        result.longest_loss_streak = metrics.get("longest_loss_streak", 0)
        result.regime_performance = metrics.get("regime_performance", {})
        result.hourly_performance = metrics.get("hourly_performance", {})
        result.daily_performance = metrics.get("daily_performance", {})
        result.equity_curve = metrics.get("equity_curve", [])

        # Step 2: Walk-forward validation
        if bt_cfg.walk_forward_enabled and len(trades) >= 20:
            wf = self.walk_forward.validate(trades, bt_cfg.train_pct)
            result.in_sample_win_rate = wf["in_sample"]["win_rate"]
            result.out_of_sample_win_rate = wf["out_of_sample"]["win_rate"]
            result.walk_forward_efficiency = wf["efficiency"]
        else:
            result.walk_forward_efficiency = 1.0  # Skip if too few trades

        # Step 3: Monte Carlo
        mc = self.monte_carlo.simulate(
            trades, cfg.initial_capital, bt_cfg.monte_carlo_runs,
        )
        result.mc_median_return = mc["median_return_pct"]
        result.mc_worst_case_return = mc["p5_return_pct"]
        result.mc_best_case_return = mc.get("p95_return_pct", 0)
        result.mc_probability_of_profit = mc["probability_of_profit"]
        result.mc_probability_of_ruin = mc["probability_of_ruin"]

        # Step 4: Grade and pass/fail
        self._grade(result, bt_cfg)

        log.info(
            "Backtest {id}: {n} trades | WR={wr:.1%} | PF={pf:.2f} | "
            "Sharpe={sh:.2f} | DD={dd:.1f}% | Grade={g} | {verdict}",
            id=result.id, n=result.total_trades, wr=result.win_rate,
            pf=result.profit_factor, sh=result.sharpe_ratio,
            dd=result.max_drawdown_pct, g=result.overall_grade,
            verdict="PASS" if result.passed else "FAIL",
        )
        return result

    def _grade(self, result: BacktestResult, cfg) -> None:
        """Apply pass/fail criteria and assign grade."""
        reasons: list[str] = []
        fails: list[str] = []

        if result.total_trades >= cfg.min_trades_to_pass:
            reasons.append(f"Sufficient trades ({result.total_trades})")
        else:
            fails.append(f"Too few trades ({result.total_trades} < {cfg.min_trades_to_pass})")

        if result.win_rate >= cfg.min_win_rate:
            reasons.append(f"Win rate {result.win_rate:.1%}")
        else:
            fails.append(f"Low win rate ({result.win_rate:.1%} < {cfg.min_win_rate:.0%})")

        if result.profit_factor >= cfg.min_profit_factor:
            reasons.append(f"Profit factor {result.profit_factor:.2f}")
        else:
            fails.append(f"Low PF ({result.profit_factor:.2f} < {cfg.min_profit_factor})")

        if result.max_drawdown_pct <= cfg.max_drawdown_pct:
            reasons.append(f"Drawdown {result.max_drawdown_pct:.1f}%")
        else:
            fails.append(f"High DD ({result.max_drawdown_pct:.1f}% > {cfg.max_drawdown_pct}%)")

        if result.sharpe_ratio >= cfg.min_sharpe:
            reasons.append(f"Sharpe {result.sharpe_ratio:.2f}")
        else:
            fails.append(f"Low Sharpe ({result.sharpe_ratio:.2f} < {cfg.min_sharpe})")

        if result.walk_forward_efficiency >= cfg.min_walk_forward_efficiency:
            reasons.append(f"WF efficiency {result.walk_forward_efficiency:.2f}")
        else:
            fails.append(f"Poor WF ({result.walk_forward_efficiency:.2f})")

        if result.mc_probability_of_ruin <= cfg.max_ruin_probability:
            reasons.append(f"Ruin prob {result.mc_probability_of_ruin:.2%}")
        else:
            fails.append(f"High ruin prob ({result.mc_probability_of_ruin:.2%})")

        result.pass_reasons = reasons
        result.fail_reasons = fails
        result.passed = len(fails) == 0

        # Grading
        wr = result.win_rate
        sh = result.sharpe_ratio
        pf = result.profit_factor

        if wr > 0.65 and sh > 2.0 and pf > 2.0:
            result.overall_grade = "A+"
        elif wr > 0.60 and sh > 1.5 and pf > 1.8:
            result.overall_grade = "A"
        elif wr > 0.55 and sh > 1.0 and pf > 1.5:
            result.overall_grade = "B"
        elif wr > 0.52 and sh > 0.5 and pf > 1.3:
            result.overall_grade = "C"
        elif result.passed:
            result.overall_grade = "D"
        else:
            result.overall_grade = "F"
