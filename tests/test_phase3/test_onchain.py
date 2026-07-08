"""Tests for OnChainClient (CoinGecko)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.exceptions import APIError
from src.intelligence.altdata.onchain import OnChainClient


class TestOnChainClient:
    @pytest.mark.asyncio
    async def test_get_global_metrics(self, test_db, test_settings, mock_coingecko_global):
        client = OnChainClient(test_settings, test_db)

        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_coingecko_global):
            metrics = await client.get_global_metrics()

        assert metrics["total_market_cap_usd"] == 2500000000000
        assert metrics["btc_dominance"] == 52.3
        assert metrics["eth_dominance"] == 17.1
        assert metrics["active_cryptocurrencies"] == 12000

    @pytest.mark.asyncio
    async def test_get_coin_metrics(self, test_db, test_settings):
        mock_response = {
            "market_data": {
                "market_cap": {"usd": 1300000000000},
                "total_volume": {"usd": 50000000000},
                "circulating_supply": 19500000,
                "price_change_percentage_24h": 2.5,
                "price_change_percentage_7d": 5.0,
            },
            "community_data": {
                "reddit_subscribers": 5000000,
                "twitter_followers": 6000000,
            },
        }

        client = OnChainClient(test_settings, test_db)
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            metrics = await client.get_coin_metrics("bitcoin")

        assert metrics["symbol"] == "BTCUSDT"
        assert metrics["market_cap_usd"] == 1300000000000
        assert metrics["reddit_subscribers"] == 5000000

    @pytest.mark.asyncio
    async def test_get_market_dominance(self, test_db, test_settings, mock_coingecko_global):
        client = OnChainClient(test_settings, test_db)
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_coingecko_global):
            dom = await client.get_market_dominance()

        assert dom["btc_dominance"] == 52.3
        assert dom["eth_dominance"] == 17.1
