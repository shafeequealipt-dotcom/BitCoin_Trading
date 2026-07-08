"""Monte Carlo Simulator: randomizes trade order to understand outcome distributions."""

import random

from src.core.logging import get_logger
from src.factory.models.backtest_types import SimulatedTrade

log = get_logger("factory")


class MonteCarloSimulator:
    """Shuffles trade order to compute probability distributions of outcomes."""

    def simulate(
        self,
        trades: list[SimulatedTrade],
        initial_capital: float,
        num_runs: int = 1000,
    ) -> dict:
        """Run Monte Carlo simulation.

        Shuffles trade order (same trades, different sequence) to see how
        path dependency affects outcomes.

        Returns:
            Dict with percentile distributions, probability of profit/ruin.
        """
        if not trades or len(trades) < 5:
            return {
                "runs": 0, "median_return_pct": 0,
                "p5_return_pct": 0, "p95_return_pct": 0,
                "probability_of_profit": 0, "probability_of_ruin": 1.0,
                "median_max_drawdown": 0, "worst_max_drawdown": 0,
                "confidence_interval_90": [0, 0],
            }

        pnl_list = [t.pnl_usd for t in trades]
        final_returns: list[float] = []
        max_drawdowns: list[float] = []
        ruin_count = 0

        for _ in range(num_runs):
            shuffled = pnl_list.copy()
            random.shuffle(shuffled)

            equity = initial_capital
            peak = initial_capital
            run_max_dd = 0.0

            for pnl in shuffled:
                equity += pnl
                peak = max(peak, equity)
                dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
                run_max_dd = max(run_max_dd, dd)

            final_return = ((equity - initial_capital) / initial_capital) * 100
            final_returns.append(final_return)
            max_drawdowns.append(run_max_dd)

            if run_max_dd >= 20:
                ruin_count += 1

        final_returns.sort()
        max_drawdowns.sort()

        n = len(final_returns)
        prob_profit = sum(1 for r in final_returns if r > 0) / n
        prob_ruin = ruin_count / n

        result = {
            "runs": num_runs,
            "median_return_pct": round(final_returns[n // 2], 2),
            "p5_return_pct": round(final_returns[int(n * 0.05)], 2),
            "p25_return_pct": round(final_returns[int(n * 0.25)], 2),
            "p75_return_pct": round(final_returns[int(n * 0.75)], 2),
            "p95_return_pct": round(final_returns[int(n * 0.95)], 2),
            "probability_of_profit": round(prob_profit, 4),
            "probability_of_ruin": round(prob_ruin, 4),
            "median_max_drawdown": round(max_drawdowns[n // 2], 2),
            "worst_max_drawdown": round(max_drawdowns[-1], 2),
            "confidence_interval_90": [
                round(final_returns[int(n * 0.05)], 2),
                round(final_returns[int(n * 0.95)], 2),
            ],
        }

        log.info(
            "Monte Carlo ({n} runs): median={med:.1f}% | P5={p5:.1f}% | P95={p95:.1f}% | "
            "P(profit)={pp:.0%} | P(ruin)={pr:.2%}",
            n=num_runs, med=result["median_return_pct"],
            p5=result["p5_return_pct"], p95=result["p95_return_pct"],
            pp=prob_profit, pr=prob_ruin,
        )
        return result
