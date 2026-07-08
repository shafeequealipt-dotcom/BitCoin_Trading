"""Backtest repository: CRUD for backtest results, trades, lifecycle, and trial data."""

import json

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.factory.models.backtest_types import BacktestResult, SimulatedTrade

log = get_logger("factory")


class BacktestRepository:
    """CRUD operations for backtesting data.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def save_result(self, result: BacktestResult) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO backtest_results "
            "(id, strategy_id, config_json, total_trades, win_rate, profit_factor, "
            "total_return_pct, max_drawdown_pct, sharpe_ratio, sortino_ratio, "
            "calmar_ratio, walk_forward_efficiency, mc_probability_of_profit, "
            "mc_probability_of_ruin, overall_grade, passed, "
            "pass_reasons_json, fail_reasons_json, regime_performance_json, "
            "monthly_returns_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                result.id, result.strategy_id, "{}",
                result.total_trades, result.win_rate, result.profit_factor,
                result.total_return_pct, result.max_drawdown_pct,
                result.sharpe_ratio, result.sortino_ratio, result.calmar_ratio,
                result.walk_forward_efficiency, result.mc_probability_of_profit,
                result.mc_probability_of_ruin, result.overall_grade,
                1 if result.passed else 0,
                json.dumps(result.pass_reasons), json.dumps(result.fail_reasons),
                json.dumps(result.regime_performance),
                json.dumps(result.monthly_returns),
            ),
        )

    async def save_trades(self, backtest_id: str, trades: list[SimulatedTrade]) -> None:
        for t in trades:
            await self._db.execute(
                "INSERT INTO backtest_trades "
                "(backtest_id, symbol, direction, entry_price, exit_price, "
                "entry_time, exit_time, exit_reason, pnl_usd, pnl_pct, "
                "commission_usd, hold_minutes, leverage, regime, hour_utc, day_of_week) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    backtest_id, t.symbol, t.direction, t.entry_price, t.exit_price,
                    t.entry_time, t.exit_time, t.exit_reason, t.pnl_usd, t.pnl_pct,
                    t.commission_usd, t.hold_minutes, t.leverage, t.regime,
                    t.hour_utc, t.day_of_week,
                ),
            )

    async def get_result(self, result_id: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM backtest_results WHERE id = ?", (result_id,),
        )

    async def get_results_for_strategy(self, strategy_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM backtest_results WHERE strategy_id = ? ORDER BY created_at DESC",
            (strategy_id,),
        )
        return [dict(r) for r in rows] if rows else []

    async def save_lifecycle_transition(
        self, strategy_id: str, from_status: str, to_status: str, reason: str,
    ) -> None:
        await self._db.execute(
            "INSERT INTO strategy_lifecycle "
            "(strategy_id, from_status, to_status, reason) VALUES (?,?,?,?)",
            (strategy_id, from_status, to_status, reason),
        )

    async def get_lifecycle_history(self, strategy_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM strategy_lifecycle WHERE strategy_id = ? ORDER BY transitioned_at DESC",
            (strategy_id,),
        )
        return [dict(r) for r in rows] if rows else []

    async def save_trial_performance(
        self, strategy_id: str, date: str, trades: int, wins: int,
        pnl: float, cum_trades: int, cum_pnl: float, cum_wr: float, max_dd: float,
    ) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO trial_performance "
            "(strategy_id, date, trades_today, wins_today, pnl_today, "
            "cumulative_trades, cumulative_pnl, cumulative_win_rate, max_drawdown) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (strategy_id, date, trades, wins, pnl, cum_trades, cum_pnl, cum_wr, max_dd),
        )
