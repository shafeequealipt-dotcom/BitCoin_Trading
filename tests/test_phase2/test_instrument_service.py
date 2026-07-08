"""Tests for InstrumentService: fetching, caching, and order validation."""

import pytest

from src.trading.models.instrument import InstrumentInfo
from src.trading.services.instrument_service import InstrumentService


class TestInstrumentService:
    @pytest.mark.asyncio
    async def test_get_instrument_info(self, mock_client):
        svc = InstrumentService(mock_client)
        info = await svc.get_instrument_info("BTCUSDT")

        assert isinstance(info, InstrumentInfo)
        assert info.symbol == "BTCUSDT"
        assert info.base_coin == "BTC"
        assert info.quote_coin == "USDT"
        assert info.min_qty == 0.001
        assert info.max_qty == 100.0
        assert info.qty_step == 0.001
        assert info.price_tick == 0.10
        assert info.status == "Trading"

    @pytest.mark.asyncio
    async def test_cache_hit(self, mock_client, mock_bybit_session):
        svc = InstrumentService(mock_client)
        await svc.get_instrument_info("BTCUSDT")
        await svc.get_instrument_info("BTCUSDT")

        # Should only call API once (second is cached)
        assert mock_bybit_session.get_instruments_info.call_count == 1

    @pytest.mark.asyncio
    async def test_validate_order_params_valid(self, mock_client):
        svc = InstrumentService(mock_client)
        await svc.get_instrument_info("BTCUSDT")

        issues = svc.validate_order_params("BTCUSDT", 0.01, 70000.0)
        assert issues == []

    @pytest.mark.asyncio
    async def test_validate_qty_below_min(self, mock_client):
        svc = InstrumentService(mock_client)
        await svc.get_instrument_info("BTCUSDT")

        issues = svc.validate_order_params("BTCUSDT", 0.0001, 70000.0)
        assert any("below minimum" in i for i in issues)

    @pytest.mark.asyncio
    async def test_validate_qty_above_max(self, mock_client):
        svc = InstrumentService(mock_client)
        await svc.get_instrument_info("BTCUSDT")

        issues = svc.validate_order_params("BTCUSDT", 999, 70000.0)
        assert any("above maximum" in i for i in issues)

    @pytest.mark.asyncio
    async def test_validate_no_cache(self, mock_client):
        svc = InstrumentService(mock_client)
        issues = svc.validate_order_params("BTCUSDT", 0.01, 70000.0)
        assert any("No instrument info cached" in i for i in issues)

    @pytest.mark.asyncio
    async def test_clear_cache(self, mock_client):
        svc = InstrumentService(mock_client)
        await svc.get_instrument_info("BTCUSDT")
        svc.clear_cache()
        assert svc._cache == {}


class TestInstrumentInfoFromBybit:
    def test_parse_bybit_response(self):
        data = {
            "symbol": "ETHUSDT",
            "baseCoin": "ETH",
            "quoteCoin": "USDT",
            "status": "Trading",
            "lotSizeFilter": {
                "minOrderQty": "0.01",
                "maxOrderQty": "1000",
                "qtyStep": "0.01",
                "minNotionalValue": "5",
            },
            "priceFilter": {
                "minPrice": "0.01",
                "maxPrice": "99999",
                "tickSize": "0.01",
            },
            "leverageFilter": {
                "minLeverage": "1",
                "maxLeverage": "50",
                "leverageStep": "0.01",
            },
        }
        info = InstrumentInfo.from_bybit(data)
        assert info.symbol == "ETHUSDT"
        assert info.min_qty == 0.01
        assert info.max_leverage == 50
        assert info.min_notional == 5.0
