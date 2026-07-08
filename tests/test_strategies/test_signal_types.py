"""Tests for strategy data models."""

from src.core.types import Side
from src.strategies.models.signal_types import (
    EnsembleResult, EnsembleVote, RawSignal, ScoredSetup, StrategyPerformance,
)


class TestRawSignal:
    def test_to_dict(self):
        sig = RawSignal(
            strategy_name="A1_test", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
            conditions_met={"rsi": True}, conditions_strength={"rsi": 0.8},
        )
        d = sig.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["direction"] == "Buy"
        assert d["conditions_strength"]["rsi"] == 0.8


class TestStrategyPerformance:
    def test_update_win(self):
        perf = StrategyPerformance(strategy_name="test")
        perf.update(2.5, True)
        assert perf.wins == 1
        assert perf.total_trades == 1
        assert perf.win_rate == 1.0
        assert perf.current_streak == 1
        assert perf.avg_win_pct == 2.5

    def test_update_loss(self):
        perf = StrategyPerformance(strategy_name="test")
        perf.update(-1.5, False)
        assert perf.losses == 1
        assert perf.win_rate == 0.0
        assert perf.current_streak == -1
        assert perf.avg_loss_pct == 1.5

    def test_mixed_sequence(self):
        perf = StrategyPerformance(strategy_name="test")
        perf.update(3.0, True)
        perf.update(2.0, True)
        perf.update(-1.0, False)
        assert perf.total_trades == 3
        assert perf.wins == 2
        assert perf.losses == 1
        assert abs(perf.win_rate - 2/3) < 0.01
        assert perf.current_streak == -1
        assert perf.profit_factor == 5.0  # 5.0 / 1.0

    def test_profit_factor_no_losses(self):
        perf = StrategyPerformance(strategy_name="test")
        perf.update(2.0, True)
        assert perf.profit_factor == 99.0


class TestEnsembleResult:
    def test_to_dict(self):
        sig = RawSignal(
            strategy_name="A1", strategy_category="scalping",
            symbol="BTCUSDT", direction=Side.BUY,
            entry_price=70000, suggested_stop_loss=69000,
            suggested_take_profit=72000, timeframe="5",
        )
        scored = ScoredSetup(
            raw_signal=sig, base_score=30, confluence_score=15,
            context_score=12, quality_score=10, total_score=67, grade="B",
        )
        result = EnsembleResult(
            scored_setup=scored,
            buy_votes=6.5, sell_votes=0.5, neutral_votes=2.0,
            consensus_strength="STRONG", consensus_direction="BUY",
            passed=True,
        )
        d = result.to_dict()
        assert d["consensus_strength"] == "STRONG"
        assert d["passed"] is True
        assert d["buy_votes"] == 6.5
