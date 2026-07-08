"""Tests for BacktestEngine, Simulator, Metrics, WalkForward, MonteCarlo, Lifecycle."""

import pytest

from src.core.types import Side
from src.core.utils import generate_id
from src.factory.backtester import BacktestEngine
from src.factory.lifecycle import StrategyLifecycleManager, VALID_TRANSITIONS
from src.factory.metrics import MetricsCalculator
from src.factory.models.backtest_types import BacktestConfig, SimulatedTrade
from src.factory.monte_carlo import MonteCarloSimulator
from src.factory.simulator import TradeSimulator
from src.factory.walk_forward import WalkForwardValidator
from src.strategies.models.signal_types import RawSignal


def _make_trade(pnl_usd, pnl_pct, direction="Buy", exit_reason="take_profit", hold=60):
    return SimulatedTrade(
        trade_id=generate_id("t"), symbol="BTCUSDT", direction=direction,
        entry_price=70000, entry_time="2026-03-01T10:00:00",
        exit_price=70000 + pnl_usd, exit_time="2026-03-01T11:00:00",
        exit_reason=exit_reason, qty=0.01, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
        hold_minutes=hold, leverage=3,
    )


def _winning_trades(n):
    return [_make_trade(10, 1.0) for _ in range(n)]


def _losing_trades(n):
    return [_make_trade(-5, -0.5, exit_reason="stop_loss") for _ in range(n)]


class TestSimulator:
    def test_open_position(self):
        config = BacktestConfig(strategy_id="test")
        sim = TradeSimulator(config)
        signal = RawSignal(
            strategy_name="test", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69500,
            suggested_take_profit=70500, timeframe="5",
        )
        pos = sim.open_position(signal, "2026-03-01T10:00:00", 10000)
        assert pos["symbol"] == "BTCUSDT"
        assert pos["direction"] == "Buy"
        assert pos["qty"] > 0
        assert sim.open_count == 1


class TestMetrics:
    def test_all_wins(self):
        calc = MetricsCalculator()
        trades = _winning_trades(10)
        config = BacktestConfig(strategy_id="test")
        result = calc.calculate(trades, config)
        assert result["win_rate"] == 1.0
        assert result["profit_factor"] == 99.0
        assert result["total_return_pct"] > 0

    def test_all_losses(self):
        calc = MetricsCalculator()
        trades = _losing_trades(10)
        config = BacktestConfig(strategy_id="test")
        result = calc.calculate(trades, config)
        assert result["win_rate"] == 0.0
        assert result["total_return_pct"] < 0

    def test_mixed_trades(self):
        calc = MetricsCalculator()
        trades = _winning_trades(6) + _losing_trades(4)
        config = BacktestConfig(strategy_id="test")
        result = calc.calculate(trades, config)
        assert 0.5 < result["win_rate"] < 0.7
        assert result["profit_factor"] > 1.0

    def test_empty_trades(self):
        calc = MetricsCalculator()
        result = calc.calculate([], BacktestConfig(strategy_id="test"))
        assert result["total_trades"] == 0

    def test_equity_curve(self):
        calc = MetricsCalculator()
        trades = _winning_trades(3) + _losing_trades(1)
        curve = calc.build_equity_curve(trades, 10000)
        assert len(curve) == 5  # initial + 4 trades
        assert curve[0]["equity"] == 10000
        assert curve[-1]["equity"] != 10000

    def test_streaks(self):
        calc = MetricsCalculator()
        win, loss = calc._compute_streaks(
            _winning_trades(5) + _losing_trades(3) + _winning_trades(2)
        )
        assert win == 5
        assert loss == 3


class TestWalkForward:
    def test_good_strategy(self):
        wf = WalkForwardValidator()
        # Consistent wins across train and test
        trades = _winning_trades(20) + _losing_trades(5) + _winning_trades(8) + _losing_trades(2)
        result = wf.validate(trades, 0.7)
        assert result["efficiency"] > 0
        assert "in_sample" in result
        assert "out_of_sample" in result

    def test_overfit_strategy(self):
        wf = WalkForwardValidator()
        # Wins in train, losses in test
        trades = _winning_trades(20) + _losing_trades(15)
        result = wf.validate(trades, 0.6)
        # OOS should be worse
        assert result["out_of_sample"]["win_rate"] < result["in_sample"]["win_rate"]

    def test_few_trades(self):
        wf = WalkForwardValidator()
        result = wf.validate(_winning_trades(3), 0.7)
        assert result["passed"] is False


class TestMonteCarlo:
    def test_profitable_trades(self):
        mc = MonteCarloSimulator()
        trades = _winning_trades(20) + _losing_trades(5)
        result = mc.simulate(trades, 10000, num_runs=100)
        assert result["probability_of_profit"] > 0.5
        assert result["median_return_pct"] > 0

    def test_losing_trades(self):
        mc = MonteCarloSimulator()
        trades = _losing_trades(20) + _winning_trades(3)
        result = mc.simulate(trades, 10000, num_runs=100)
        assert result["probability_of_profit"] < 0.5

    def test_few_trades(self):
        mc = MonteCarloSimulator()
        result = mc.simulate([], 10000, num_runs=100)
        assert result["runs"] == 0


class TestBacktestEngine:
    def test_passing_strategy(self, factory_settings):
        engine = BacktestEngine(factory_settings)
        # 60% win rate strategy — interleaved to pass walk-forward
        trades = []
        for i in range(60):
            if i % 5 < 3:  # 3 wins per 5 = 60%
                trades.append(_make_trade(10, 1.0))
            else:
                trades.append(_make_trade(-5, -0.5, exit_reason="stop_loss"))
        result = engine.run_on_trades("test_strat", trades)
        assert result.total_trades == 60
        assert abs(result.win_rate - 0.6) < 0.01
        assert result.profit_factor > 1.0

    def test_failing_strategy(self, factory_settings):
        engine = BacktestEngine(factory_settings)
        trades = _winning_trades(5) + _losing_trades(25)
        result = engine.run_on_trades("bad_strat", trades)
        assert result.passed is False
        assert result.overall_grade == "F"

    def test_no_trades(self, factory_settings):
        engine = BacktestEngine(factory_settings)
        result = engine.run_on_trades("empty_strat", [])
        assert result.passed is False


class TestLifecycle:
    def test_valid_transitions(self):
        assert "validated" in VALID_TRANSITIONS["generated"]
        assert "backtested_pass" in VALID_TRANSITIONS["validated"]
        assert "trial_active" in VALID_TRANSITIONS["backtested_pass"]
        assert "promoted" in VALID_TRANSITIONS["trial_active"]
        assert "killed" in VALID_TRANSITIONS["trial_active"]
        assert "demoted" in VALID_TRANSITIONS["promoted"]

    def test_invalid_transition_blocked(self):
        # Can't go directly from generated to promoted
        assert "promoted" not in VALID_TRANSITIONS["generated"]

    @pytest.mark.asyncio
    async def test_evaluate_trial_promote(self, factory_settings):
        from unittest.mock import MagicMock
        lm = StrategyLifecycleManager(MagicMock(), factory_settings)
        result = await lm.evaluate_trial("test", {
            "trades_taken": 15, "wins": 9, "total_pnl_pct": 2.5, "max_drawdown_pct": 5.0,
        })
        assert result == "promote"

    @pytest.mark.asyncio
    async def test_evaluate_trial_kill(self, factory_settings):
        from unittest.mock import MagicMock
        lm = StrategyLifecycleManager(MagicMock(), factory_settings)
        result = await lm.evaluate_trial("test", {
            "trades_taken": 15, "wins": 3, "total_pnl_pct": -5.0, "max_drawdown_pct": 12.0,
        })
        assert result == "kill"

    @pytest.mark.asyncio
    async def test_evaluate_trial_extend(self, factory_settings):
        from unittest.mock import MagicMock
        lm = StrategyLifecycleManager(MagicMock(), factory_settings)
        result = await lm.evaluate_trial("test", {
            "trades_taken": 5, "wins": 3, "total_pnl_pct": 0.5, "max_drawdown_pct": 3.0,
        })
        assert result == "extend"
