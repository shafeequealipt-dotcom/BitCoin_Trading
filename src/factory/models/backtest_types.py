"""Data models for the backtesting engine and strategy lifecycle."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""
    strategy_id: str
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT"])
    timeframe: str = "5"
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 10000.0
    leverage: int = 3
    commission_pct: float = 0.06
    slippage_pct: float = 0.02
    funding_rate_pct: float = 0.01
    max_positions: int = 5
    position_size_pct: float = 10.0
    walk_forward: bool = True
    train_pct: float = 0.7
    monte_carlo_runs: int = 1000


@dataclass
class SimulatedTrade:
    """A single simulated trade from backtesting."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    entry_time: str
    exit_price: float
    exit_time: str
    exit_reason: str
    qty: float
    pnl_usd: float
    pnl_pct: float
    commission_usd: float = 0.0
    slippage_usd: float = 0.0
    hold_minutes: int = 0
    leverage: int = 1
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    regime: str = ""
    hour_utc: int = 0
    day_of_week: int = 0

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl_usd": round(self.pnl_usd, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "exit_reason": self.exit_reason,
            "hold_minutes": self.hold_minutes,
            "leverage": self.leverage,
        }


@dataclass
class BacktestResult:
    """Complete results of a backtest run."""
    id: str
    strategy_id: str
    config: BacktestConfig

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    expected_value: float = 0.0

    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    monthly_returns: dict = field(default_factory=dict)

    max_drawdown_pct: float = 0.0
    avg_drawdown_pct: float = 0.0
    max_drawdown_duration_hours: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    avg_hold_minutes: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    trades_per_day: float = 0.0

    regime_performance: dict = field(default_factory=dict)
    hourly_performance: dict = field(default_factory=dict)
    daily_performance: dict = field(default_factory=dict)

    in_sample_win_rate: float = 0.0
    out_of_sample_win_rate: float = 0.0
    walk_forward_efficiency: float = 0.0

    mc_median_return: float = 0.0
    mc_worst_case_return: float = 0.0
    mc_best_case_return: float = 0.0
    mc_probability_of_profit: float = 0.0
    mc_probability_of_ruin: float = 0.0

    equity_curve: list[dict] = field(default_factory=list)

    passed: bool = False
    pass_reasons: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)
    overall_grade: str = "F"

    trades: list[SimulatedTrade] = field(default_factory=list)
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "overall_grade": self.overall_grade,
            "passed": self.passed,
            "walk_forward_efficiency": round(self.walk_forward_efficiency, 2),
            "mc_probability_of_profit": round(self.mc_probability_of_profit, 2),
            "mc_probability_of_ruin": round(self.mc_probability_of_ruin, 4),
        }


@dataclass
class TrialStatus:
    """Status of a strategy in paper trading trial."""
    strategy_id: str
    strategy_name: str
    status: str = "active"
    started_at: str = ""
    trial_duration_days: int = 14
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    days_remaining: int = 14
    promotion_eligible: bool = False

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "status": self.status,
            "trades_taken": self.trades_taken,
            "win_rate": round(self.win_rate, 3),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "days_remaining": self.days_remaining,
            "promotion_eligible": self.promotion_eligible,
        }
