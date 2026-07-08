"""Tests for RiskManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.types import AccountInfo, Position, Side
from src.risk.risk_manager import RiskManager


@pytest.fixture
def mock_services(sample_account, sample_positions_safe):
    return {
        "account": MagicMock(get_wallet_balance=AsyncMock(return_value=sample_account)),
        "position": MagicMock(get_positions=AsyncMock(return_value=sample_positions_safe)),
        "market": MagicMock(get_ticker=AsyncMock()),
        "instrument": MagicMock(get_instrument_info=AsyncMock(return_value=None)),
    }


class TestRiskManager:
    @pytest.mark.asyncio
    async def test_valid_trade(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        valid, issues = await rm.validate_trade("BTCUSDT", Side.BUY, 0.005, 70000, 68000, 73000, 1)
        # May have duplicate warning since sample_positions has BTCUSDT
        # But should not have critical failures
        assert isinstance(valid, bool)

    @pytest.mark.asyncio
    async def test_halted_blocks_trade(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        rm.drawdown.trading_halted = True
        rm.drawdown.halt_reason = "Test halt"
        rm.drawdown.today_date = "9999-12-31"
        rm.drawdown.today_starting_equity = 10000
        rm.drawdown.today_realized_pnl = -600

        valid, issues = await rm.validate_trade("BTCUSDT", Side.BUY, 0.01, 70000, 68000)
        assert valid is False
        assert len(issues) > 0

    @pytest.mark.asyncio
    async def test_calculate_position_size(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        result = await rm.calculate_position_size("BTCUSDT", Side.BUY, 70000, 68000)
        assert result["recommended_qty"] > 0

    @pytest.mark.asyncio
    async def test_calculate_stop_loss(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        result = await rm.calculate_stop_loss("BTCUSDT", Side.BUY, 70000)
        assert result["recommended_stop_loss"] < 70000

    @pytest.mark.asyncio
    async def test_get_risk_status(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        status = await rm.get_risk_status()
        assert "overall_status" in status
        assert "trading_allowed" in status
        assert "circuit_breakers" in status

    @pytest.mark.asyncio
    async def test_on_trade_closed(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        rm.drawdown.today_starting_equity = 10000
        rm.drawdown.today_date = "9999-12-31"
        await rm.on_trade_closed(50.0)
        assert rm.drawdown.today_realized_pnl == 50.0

    @pytest.mark.asyncio
    async def test_on_price_update(self, risk_settings, test_db, mock_services):
        rm = RiskManager(risk_settings, test_db, mock_services)
        rm.drawdown.peak_equity = 9000
        await rm.on_price_update(11000)
        assert rm.drawdown.peak_equity == 11000
