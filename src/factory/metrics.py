"""Performance Metrics Calculator: computes comprehensive backtest statistics."""

import math
from collections import defaultdict

from src.core.logging import get_logger
from src.factory.models.backtest_types import BacktestConfig, SimulatedTrade

log = get_logger("factory")


class MetricsCalculator:
    """Computes comprehensive performance metrics from simulated trades."""

    def calculate(self, trades: list[SimulatedTrade], config: BacktestConfig) -> dict:
        """Calculate all metrics from a list of simulated trades."""
        if not trades:
            return self._empty_metrics()

        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]

        total = len(trades)
        win_rate = len(wins) / total if total > 0 else 0

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))

        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(abs(t.pnl_pct) for t in losses) / len(losses) if losses else 0

        pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
        ev = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Equity curve and drawdown
        equity_curve = self.build_equity_curve(trades, config.initial_capital)
        max_dd, avg_dd, max_dd_duration = self._compute_drawdowns(equity_curve)

        # Returns
        final_equity = equity_curve[-1]["equity"] if equity_curve else config.initial_capital
        total_return = ((final_equity - config.initial_capital) / config.initial_capital) * 100

        # Sharpe, Sortino, Calmar
        daily_returns = self._compute_daily_returns(trades, config.initial_capital)
        sharpe = self._sharpe(daily_returns)
        sortino = self._sortino(daily_returns)
        calmar = total_return / max_dd if max_dd > 0 else 0

        # Streaks
        win_streak, loss_streak = self._compute_streaks(trades)

        # Trade analysis
        best = max(t.pnl_pct for t in trades)
        worst = min(t.pnl_pct for t in trades)
        avg_hold = sum(t.hold_minutes for t in trades) / total

        # Regime and time breakdowns
        regime_perf = self._breakdown_by(trades, lambda t: t.regime or "unknown")
        hourly_perf = self._breakdown_by(trades, lambda t: str(t.hour_utc))
        daily_perf = self._breakdown_by(trades, lambda t: str(t.day_of_week))

        return {
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": pf,
            "expected_value": ev,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "avg_drawdown_pct": avg_dd,
            "max_drawdown_duration_hours": max_dd_duration,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "avg_hold_minutes": avg_hold,
            "best_trade_pct": best,
            "worst_trade_pct": worst,
            "longest_win_streak": win_streak,
            "longest_loss_streak": loss_streak,
            "regime_performance": regime_perf,
            "hourly_performance": hourly_perf,
            "daily_performance": daily_perf,
            "equity_curve": equity_curve,
        }

    def build_equity_curve(
        self, trades: list[SimulatedTrade], initial: float,
    ) -> list[dict]:
        """Build equity curve from trade sequence."""
        curve: list[dict] = [{"timestamp": "", "equity": initial, "drawdown_pct": 0}]
        equity = initial
        peak = initial
        for t in trades:
            equity += t.pnl_usd
            peak = max(peak, equity)
            dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
            curve.append({
                "timestamp": t.exit_time,
                "equity": round(equity, 2),
                "drawdown_pct": round(dd, 2),
            })
        return curve

    @staticmethod
    def _compute_drawdowns(curve: list[dict]) -> tuple[float, float, float]:
        """Compute max drawdown, average drawdown, and max drawdown duration."""
        if len(curve) < 2:
            return 0, 0, 0
        dds = [p["drawdown_pct"] for p in curve if p["drawdown_pct"] > 0]
        max_dd = max(p["drawdown_pct"] for p in curve) if curve else 0
        avg_dd = sum(dds) / len(dds) if dds else 0
        return max_dd, avg_dd, 0  # Duration needs timestamp parsing

    @staticmethod
    def _compute_daily_returns(
        trades: list[SimulatedTrade], initial: float,
    ) -> list[float]:
        """Compute daily return series for Sharpe/Sortino calculation."""
        if not trades:
            return []
        daily: dict[str, float] = defaultdict(float)
        for t in trades:
            day = t.exit_time[:10] if t.exit_time else ""
            if day:
                daily[day] += t.pnl_pct
        return list(daily.values())

    @staticmethod
    def _sharpe(daily_returns: list[float], risk_free: float = 0.0) -> float:
        """Annualized Sharpe ratio."""
        if not daily_returns or len(daily_returns) < 2:
            return 0
        avg = sum(daily_returns) / len(daily_returns)
        std = (sum((r - avg) ** 2 for r in daily_returns) / (len(daily_returns) - 1)) ** 0.5
        if std == 0:
            return 0
        return ((avg - risk_free) / std) * math.sqrt(365)

    @staticmethod
    def _sortino(daily_returns: list[float], risk_free: float = 0.0) -> float:
        """Annualized Sortino ratio (only downside deviation)."""
        if not daily_returns or len(daily_returns) < 2:
            return 0
        avg = sum(daily_returns) / len(daily_returns)
        downside = [r for r in daily_returns if r < 0]
        if not downside:
            return 99.0
        down_std = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5
        if down_std == 0:
            return 0
        return ((avg - risk_free) / down_std) * math.sqrt(365)

    @staticmethod
    def _compute_streaks(trades: list[SimulatedTrade]) -> tuple[int, int]:
        """Compute longest win and loss streaks."""
        max_win = max_loss = 0
        current_win = current_loss = 0
        for t in trades:
            if t.pnl_usd > 0:
                current_win += 1
                current_loss = 0
                max_win = max(max_win, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss = max(max_loss, current_loss)
        return max_win, max_loss

    @staticmethod
    def _breakdown_by(trades: list[SimulatedTrade], key_fn) -> dict:
        """Group trades by a key function and compute per-group stats."""
        groups: dict[str, list] = defaultdict(list)
        for t in trades:
            groups[key_fn(t)].append(t)
        result = {}
        for k, group in groups.items():
            wins = sum(1 for t in group if t.pnl_usd > 0)
            result[k] = {
                "trades": len(group),
                "win_rate": wins / len(group) if group else 0,
                "avg_pnl_pct": sum(t.pnl_pct for t in group) / len(group) if group else 0,
            }
        return result

    @staticmethod
    def _empty_metrics() -> dict:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "profit_factor": 0, "sharpe_ratio": 0,
            "max_drawdown_pct": 0, "total_return_pct": 0,
            "equity_curve": [],
        }
