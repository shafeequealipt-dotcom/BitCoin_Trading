"""Tests for FundingRateTracker."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.types import FundingRate
from src.intelligence.altdata.funding_rates import FundingRateTracker


@pytest.fixture
def mock_bybit_client(test_settings):
    """Mock BybitClient that returns funding rate data."""
    client = MagicMock()
    client._settings = test_settings

    async def mock_call(method, **kwargs):
        if method == "get_tickers":
            return {
                "list": [{
                    "symbol": kwargs.get("symbol", "BTCUSDT"),
                    "fundingRate": "0.0003",
                    "nextFundingTime": "1704110400000",
                }]
            }
        return {"list": []}

    client.call = AsyncMock(side_effect=mock_call)
    return client


class TestFundingRateTracker:
    @pytest.mark.asyncio
    async def test_fetch_current_rates(self, mock_bybit_client, test_db):
        tracker = FundingRateTracker(mock_bybit_client, test_db)
        rates = await tracker.fetch_current_rates(["BTCUSDT"])

        assert len(rates) == 1
        assert isinstance(rates[0], FundingRate)
        assert rates[0].funding_rate == 0.0003

    @pytest.mark.asyncio
    async def test_rates_persisted(self, mock_bybit_client, test_db):
        tracker = FundingRateTracker(mock_bybit_client, test_db)
        await tracker.fetch_current_rates(["BTCUSDT"])

        rows = await test_db.fetch_all("SELECT * FROM funding_rates")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_extreme_rates(self, mock_bybit_client, test_db):
        async def extreme_call(method, **kwargs):
            return {
                "list": [{
                    "symbol": kwargs.get("symbol", "BTCUSDT"),
                    "fundingRate": "0.015",  # Extreme positive
                    "nextFundingTime": "1704110400000",
                }]
            }

        mock_bybit_client.call = AsyncMock(side_effect=extreme_call)
        tracker = FundingRateTracker(mock_bybit_client, test_db)
        extreme = await tracker.get_extreme_rates(threshold=0.01)

        assert len(extreme) >= 1
        assert abs(extreme[0].funding_rate) >= 0.01

    @pytest.mark.asyncio
    async def test_get_rate_history(self, mock_bybit_client, test_db):
        tracker = FundingRateTracker(mock_bybit_client, test_db)
        await tracker.fetch_current_rates(["BTCUSDT"])
        history = await tracker.get_rate_history("BTCUSDT", hours=24)
        assert len(history) >= 1
