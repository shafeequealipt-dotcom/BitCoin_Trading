"""Tests for DrawdownTracker and circuit breakers."""

import pytest
from src.core.types import AccountInfo
from src.risk.drawdown import DrawdownTracker


class TestDrawdownTracking:
    @pytest.mark.asyncio
    async def test_initialize(self, risk_settings, test_db, sample_account):
        dt = DrawdownTracker(risk_settings, test_db)
        await dt.initialize(sample_account)
        assert dt.peak_equity == 10000
        assert dt.today_starting_equity == 10000

    def test_peak_only_goes_up(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.peak_equity = 10000
        dt.update_equity(9000)
        assert dt.peak_equity == 10000  # Didn't decrease
        dt.update_equity(11000)
        assert dt.peak_equity == 11000  # Increased

    def test_drawdown_calculation(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.peak_equity = 10000
        result = dt.get_current_drawdown(9000)
        assert result["drawdown_usd"] == 1000
        assert result["drawdown_pct"] == 10.0


class TestDailyPnL:
    def test_daily_tracking(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_starting_equity = 10000
        dt.today_date = "2026-03-21"
        dt.record_trade_result(50)
        dt.record_trade_result(-30)
        result = dt.get_daily_pnl()
        assert result["today_realized_pnl"] == 20
        assert result["limit_status"] == "safe"

    def test_daily_reset(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_date = "2024-01-01"  # Old date
        dt.today_realized_pnl = -999
        dt._reset_day_if_needed()
        assert dt.today_realized_pnl == 0


class TestCircuitBreakers:
    def test_safe_when_ok(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_starting_equity = 10000
        dt.today_date = "2026-03-21"
        is_safe, reason = dt.check_circuit_breakers()
        assert is_safe is True

    def test_daily_loss_triggers(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_starting_equity = 10000
        dt.today_date = "9999-12-31"  # Future date so no reset
        dt.record_trade_result(-600)  # 6% loss > 5% limit
        is_safe, reason = dt.check_circuit_breakers()
        assert is_safe is False
        assert "daily loss" in reason.lower()
        assert dt.trading_halted is True

    def test_consecutive_losses_triggers(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_starting_equity = 10000
        dt.today_date = "9999-12-31"
        for _ in range(5):
            dt.record_trade_result(-10)  # Small losses
        is_safe, reason = dt.check_circuit_breakers()
        assert is_safe is False
        assert "consecutive" in reason.lower()

    def test_cooldown_active(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_starting_equity = 10000
        dt.today_date = "9999-12-31"
        dt.record_trade_result(-10)
        is_safe, reason = dt.check_circuit_breakers()
        assert is_safe is False
        assert "cooldown" in reason.lower()

    def test_manual_reset(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.trading_halted = True
        dt.halt_reason = "test"
        dt.reset_halt("Manual reset")
        assert dt.trading_halted is False

    def test_win_resets_streak(self, risk_settings, test_db):
        dt = DrawdownTracker(risk_settings, test_db)
        dt.today_starting_equity = 10000
        dt.today_date = "9999-12-31"
        dt.record_trade_result(-10)
        dt.record_trade_result(-10)
        assert dt.consecutive_losses == 2
        dt.record_trade_result(50)
        assert dt.consecutive_losses == 0
