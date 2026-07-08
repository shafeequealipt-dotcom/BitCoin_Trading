"""Tests for OrderService: placement, validation, safety checks, cancellation."""

import pytest

from src.core.exceptions import InvalidOrderError, RiskLimitExceededError
from src.core.types import Order, OrderStatus, OrderType, Side
from src.trading.services.order_service import OrderService


class TestOrderPlacement:
    @pytest.mark.asyncio
    async def test_place_market_order(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        order = await svc.place_order(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=0.01,
            stop_loss=68000.0,
            take_profit=73000.0,
        )

        assert isinstance(order, Order)
        assert order.order_id == "test-order-id-001"
        assert order.symbol == "BTCUSDT"
        assert order.side == Side.BUY
        assert order.qty == 0.01
        assert order.stop_loss == 68000.0

    @pytest.mark.asyncio
    async def test_place_order_persisted(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        await svc.place_order(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=0.01,
            stop_loss=68000.0,
        )

        rows = await test_db.fetch_all("SELECT * FROM orders")
        assert len(rows) == 1
        assert rows[0]["order_id"] == "test-order-id-001"

    @pytest.mark.asyncio
    async def test_place_limit_order(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        order = await svc.place_order(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            qty=0.01,
            price=69000.0,
            stop_loss=67000.0,
        )
        assert order.price == 69000.0


class TestOrderSafetyChecks:
    @pytest.mark.asyncio
    async def test_reject_unsupported_symbol(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        with pytest.raises(InvalidOrderError, match="Unsupported symbol"):
            await svc.place_order(
                symbol="FAKECOIN",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=1.0,
                stop_loss=100.0,
            )

    @pytest.mark.asyncio
    async def test_reject_missing_stop_loss(self, mock_client, test_db, test_settings):
        """Mandatory stop-loss enforcement."""
        svc = OrderService(mock_client, test_db, test_settings)
        with pytest.raises(InvalidOrderError, match="mandatory"):
            await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                # No stop_loss!
            )

    @pytest.mark.asyncio
    async def test_reject_excessive_leverage(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        with pytest.raises(RiskLimitExceededError, match="exceeds max"):
            await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                stop_loss=68000.0,
                leverage=10,  # Max is 3
            )

    @pytest.mark.asyncio
    async def test_reject_limit_without_price(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        with pytest.raises(InvalidOrderError, match="Price is required"):
            await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                qty=0.01,
                stop_loss=68000.0,
                # No price for limit order!
            )

    @pytest.mark.asyncio
    async def test_optional_stop_loss_when_not_mandatory(self, mock_client, test_db, test_settings):
        """When mandatory_stop_loss is False, orders without SL should work."""
        test_settings.risk.mandatory_stop_loss = False
        svc = OrderService(mock_client, test_db, test_settings)
        order = await svc.place_order(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=0.01,
        )
        assert order.stop_loss is None


class TestOrderManagement:
    @pytest.mark.asyncio
    async def test_cancel_order(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        result = await svc.cancel_order("BTCUSDT", "test-order-id-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_all_orders(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        count = await svc.cancel_all_orders("BTCUSDT")
        assert count == 2

    @pytest.mark.asyncio
    async def test_get_open_orders(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        orders = await svc.get_open_orders("BTCUSDT")

        assert len(orders) == 1
        assert orders[0].symbol == "BTCUSDT"
        assert orders[0].status == OrderStatus.NEW

    @pytest.mark.asyncio
    async def test_get_order_history(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        orders = await svc.get_order_history("BTCUSDT")

        assert len(orders) == 1
        assert orders[0].status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_open_orders_persisted(self, mock_client, test_db, test_settings):
        svc = OrderService(mock_client, test_db, test_settings)
        await svc.get_open_orders("BTCUSDT")

        rows = await test_db.fetch_all("SELECT * FROM orders")
        assert len(rows) == 1
