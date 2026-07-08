"""Tests for MarketScanner."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.types import Ticker
from src.strategies.scanner import MarketScanner


class TestMarketScanner:
    @pytest.mark.asyncio
    async def test_scan_returns_top_coins(self, strategy_settings, sample_tickers):
        mock_market = MagicMock()
        mock_market.get_tickers = AsyncMock(return_value=sample_tickers)
        scanner = MarketScanner(strategy_settings, mock_market)
        results = await scanner.scan_market()
        assert len(results) <= strategy_settings.scanner.max_coins
        assert all("symbol" in r for r in results)
        assert all("score" in r for r in results)

    @pytest.mark.asyncio
    async def test_scan_sorted_by_score(self, strategy_settings, sample_tickers):
        mock_market = MagicMock()
        mock_market.get_tickers = AsyncMock(return_value=sample_tickers)
        scanner = MarketScanner(strategy_settings, mock_market)
        results = await scanner.scan_market()
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    @pytest.mark.asyncio
    async def test_scan_filters_low_volume(self, strategy_settings):
        low_vol = Ticker(
            symbol="LOWUSDT", last_price=0.01, bid=0.009, ask=0.011,
            high_24h=0.015, low_24h=0.005, volume_24h=100, change_24h_pct=5.0,
        )
        mock_market = MagicMock()
        mock_market.get_tickers = AsyncMock(return_value=[low_vol])
        scanner = MarketScanner(strategy_settings, mock_market)
        results = await scanner.scan_market()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_get_active_universe(self, strategy_settings, sample_tickers):
        mock_market = MagicMock()
        mock_market.get_tickers = AsyncMock(return_value=sample_tickers)
        scanner = MarketScanner(strategy_settings, mock_market)
        universe = await scanner.get_active_universe()
        assert isinstance(universe, list)
        assert all(isinstance(s, str) for s in universe)

    def test_coin_tier(self):
        assert MarketScanner.get_coin_tier("BTCUSDT") == 1
        assert MarketScanner.get_coin_tier("ETHUSDT") == 1
        assert MarketScanner.get_coin_tier("SOLUSDT") == 2
        assert MarketScanner.get_coin_tier("DOGEUSDT") == 3
