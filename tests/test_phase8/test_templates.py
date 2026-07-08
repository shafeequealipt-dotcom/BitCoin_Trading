"""Tests for AlertTemplates."""

from src.alerts.templates import AlertTemplates
from src.core.types import AlertLevel, Side, SignalType


class TestTemplates:
    def test_trade_executed(self, sample_order):
        msg = AlertTemplates().trade_executed(sample_order, 5000.0)
        assert "TRADE EXECUTED" in msg
        assert "BTCUSDT" in msg
        assert "Market" in msg
        assert len(msg) < 4096

    def test_position_closed(self):
        msg = AlertTemplates().position_closed("BTCUSDT", Side.BUY, 69000, 71000, 100, 2.9)
        assert "POSITION CLOSED" in msg
        assert "BTCUSDT" in msg
        assert len(msg) < 4096

    def test_signal_detected(self, sample_signal):
        msg = AlertTemplates.signal_detected(sample_signal)
        assert "SIGNAL DETECTED" in msg
        assert "BTCUSDT" in msg
        assert len(msg) < 4096

    def test_brain_decision_buy(self, sample_brain_buy):
        msg = AlertTemplates.brain_decision(sample_brain_buy, "scheduled", 0.0085)
        assert "BRAIN DECISION" in msg
        assert "BUY" in msg
        assert "$0.0085" in msg
        assert len(msg) < 4096

    def test_brain_decision_hold(self, sample_brain_hold):
        msg = AlertTemplates.brain_decision(sample_brain_hold, "scheduled", 0.007)
        assert "HOLD" in msg
        assert len(msg) < 4096

    def test_error_alert(self):
        msg = AlertTemplates.error_alert("news_worker", "API timeout", AlertLevel.WARNING)
        assert "ERROR" in msg
        assert "news_worker" in msg
        assert len(msg) < 4096

    def test_daily_summary(self):
        data = {
            "total_pnl": 142.5, "total_pnl_pct": 1.8, "trades_count": 4,
            "wins": 3, "positions": [{"symbol": "BTCUSDT", "pnl": 85}],
            "fear_greed": {"value": 68, "classification": "Greed"},
            "brain_calls": 12, "brain_cost": 0.09,
            "workers_running": 7, "workers_total": 7,
        }
        msg = AlertTemplates.daily_summary(data)
        assert "DAILY SUMMARY" in msg
        assert len(msg) < 4096

    def test_worker_crash_recovering(self):
        msg = AlertTemplates.worker_crash("reddit_worker", "timeout", 2, 5)
        assert "WORKER ALERT" in msg
        assert "2/5" in msg

    def test_worker_crash_stopped(self):
        msg = AlertTemplates.worker_crash("reddit_worker", "timeout", 5, 5)
        assert "WORKER DOWN" in msg

    def test_risk_warning(self):
        msg = AlertTemplates.risk_warning("Daily loss limit approaching", {"pnl": "-$245", "limit": "-3%"})
        assert "RISK WARNING" in msg

    def test_price_alert(self):
        msg = AlertTemplates().price_alert("BTCUSDT", 73500, 3.2, 5)
        assert "PRICE SPIKE" in msg
        assert "3.2%" in msg

    def test_system_startup(self):
        msg = AlertTemplates.system_startup("paper", ["BTCUSDT", "ETHUSDT"], 7)
        assert "SYSTEM STARTED" in msg
        assert "Paper" in msg

    def test_system_shutdown(self):
        msg = AlertTemplates.system_shutdown("Manual shutdown")
        assert "SYSTEM STOPPED" in msg
