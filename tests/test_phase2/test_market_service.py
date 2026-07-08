"""Tests for MarketService: tickers, klines, orderbook, recent trades."""

import pytest

from src.core.types import OHLCV, Ticker, TimeFrame
from src.trading.services.market_service import MarketService


class TestMarketServiceTicker:
    @pytest.mark.asyncio
    async def test_get_ticker(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        ticker = await svc.get_ticker("BTCUSDT")

        assert isinstance(ticker, Ticker)
        assert ticker.symbol == "BTCUSDT"
        assert ticker.last_price == 70000.0
        assert ticker.bid == 69999.5
        assert ticker.ask == 70000.5
        assert ticker.high_24h == 71000.0
        assert ticker.change_24h_pct == 1.5  # 0.0150 * 100

    @pytest.mark.asyncio
    async def test_ticker_persisted(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        await svc.get_ticker("BTCUSDT")

        row = await test_db.fetch_one("SELECT * FROM ticker_cache WHERE symbol = 'BTCUSDT'")
        assert row is not None
        assert row["last_price"] == 70000.0

    @pytest.mark.asyncio
    async def test_get_tickers_default(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        tickers = await svc.get_tickers()
        # Default symbols from test_settings: ["BTCUSDT", "ETHUSDT"]
        assert len(tickers) == 2


class TestMarketServiceKlines:
    @pytest.mark.asyncio
    async def test_get_klines(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        klines = await svc.get_klines("BTCUSDT", TimeFrame.H1)

        assert len(klines) == 3
        assert all(isinstance(k, OHLCV) for k in klines)
        # Should be chronological order (oldest first)
        assert klines[0].timestamp < klines[-1].timestamp
        assert klines[0].close == 69800.0  # Oldest candle

    @pytest.mark.asyncio
    async def test_klines_persisted(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        await svc.get_klines("BTCUSDT", TimeFrame.H1)

        rows = await test_db.fetch_all("SELECT * FROM klines WHERE symbol = 'BTCUSDT'")
        assert len(rows) == 3


class TestMarketServiceOrderbook:
    @pytest.mark.asyncio
    async def test_get_orderbook(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        ob = await svc.get_orderbook("BTCUSDT")

        assert ob["symbol"] == "BTCUSDT"
        assert len(ob["bids"]) == 3
        assert len(ob["asks"]) == 3
        assert ob["bids"][0][0] == 69999.5  # Best bid price

    @pytest.mark.asyncio
    async def test_orderbook_persisted(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        await svc.get_orderbook("BTCUSDT")

        rows = await test_db.fetch_all("SELECT * FROM orderbook_snapshots")
        assert len(rows) == 1


class TestMarketServiceTrades:
    @pytest.mark.asyncio
    async def test_get_recent_trades(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        trades = await svc.get_recent_trades("BTCUSDT")

        assert len(trades) == 2
        assert trades[0]["price"] == 70000.0
        assert trades[0]["side"] == "Buy"

    @pytest.mark.asyncio
    async def test_get_24h_stats(self, mock_client, test_db):
        svc = MarketService(mock_client, test_db)
        stats = await svc.get_24h_stats("BTCUSDT")

        assert stats["symbol"] == "BTCUSDT"
        assert stats["high_24h"] == 71000.0
        assert stats["change_24h_pct"] == 1.5
