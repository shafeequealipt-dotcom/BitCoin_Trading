"""Tests for PositionService: positions, close, PnL, SL/TP management."""

import pytest

from src.core.exceptions import PositionError, RiskLimitExceededError
from src.core.types import Order, Position, Side
from src.trading.services.position_service import PositionService


class TestPositionFetching:
    @pytest.mark.asyncio
    async def test_get_positions(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        positions = await svc.get_positions()

        assert len(positions) == 1
        pos = positions[0]
        assert isinstance(pos, Position)
        assert pos.symbol == "BTCUSDT"
        assert pos.side == Side.BUY
        assert pos.size == 0.01
        assert pos.entry_price == 69000.0
        assert pos.mark_price == 70000.0
        assert pos.leverage == 2

    @pytest.mark.asyncio
    async def test_get_position_single(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        pos = await svc.get_position("BTCUSDT")
        assert pos is not None
        assert pos.symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_get_position_none(self, mock_client, test_db, test_settings, mock_bybit_session):
        mock_bybit_session.get_positions.return_value = {
            "retCode": 0, "retMsg": "OK",
            "result": {"list": [{"symbol": "BTCUSDT", "side": "Buy", "size": "0"}]},
        }
        svc = PositionService(mock_client, test_db, test_settings)
        pos = await svc.get_position("BTCUSDT")
        assert pos is None

    @pytest.mark.asyncio
    async def test_positions_persisted(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        await svc.get_positions()

        rows = await test_db.fetch_all("SELECT * FROM positions")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTCUSDT"


class TestPositionClose:
    @pytest.mark.asyncio
    async def test_close_position(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        order = await svc.close_position("BTCUSDT")

        assert isinstance(order, Order)
        assert order.side == Side.SELL  # Opposite of BUY position

    @pytest.mark.asyncio
    async def test_close_creates_trade_record(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        await svc.close_position("BTCUSDT")

        trades = await test_db.fetch_all("SELECT * FROM trade_history")
        assert len(trades) == 1
        assert trades[0]["symbol"] == "BTCUSDT"
        assert trades[0]["side"] == "Buy"
        assert trades[0]["entry_price"] == 69000.0

    @pytest.mark.asyncio
    async def test_close_nonexistent_raises(self, mock_client, test_db, test_settings, mock_bybit_session):
        mock_bybit_session.get_positions.return_value = {
            "retCode": 0, "retMsg": "OK",
            "result": {"list": []},
        }
        svc = PositionService(mock_client, test_db, test_settings)
        with pytest.raises(PositionError, match="No open position"):
            await svc.close_position("BTCUSDT")

    @pytest.mark.asyncio
    async def test_close_all_positions(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        orders = await svc.close_all_positions()
        assert len(orders) == 1


class TestLeverageAndStops:
    @pytest.mark.asyncio
    async def test_set_leverage(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        result = await svc.set_leverage("BTCUSDT", 2)
        assert result is True

    @pytest.mark.asyncio
    async def test_set_leverage_exceeds_max(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        with pytest.raises(RiskLimitExceededError, match="exceeds max"):
            await svc.set_leverage("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_set_stop_loss(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        result = await svc.set_stop_loss("BTCUSDT", 67000.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_set_take_profit(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        result = await svc.set_take_profit("BTCUSDT", 75000.0)
        assert result is True


class TestPnLSummary:
    @pytest.mark.asyncio
    async def test_pnl_summary(self, mock_client, test_db, test_settings):
        svc = PositionService(mock_client, test_db, test_settings)
        summary = await svc.get_pnl_summary()

        assert summary["position_count"] == 1
        assert summary["total_unrealized_pnl"] == 10.0
        assert len(summary["positions"]) == 1
        assert summary["positions"][0]["symbol"] == "BTCUSDT"
