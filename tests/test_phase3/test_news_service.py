"""Tests for NewsService: dedup, symbol extraction, scoring, persistence."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.types import NewsArticle
from src.intelligence.news.news_service import NewsService, extract_symbols
from src.intelligence.sentiment.scorer import SentimentScorer


class TestSymbolExtraction:
    def test_bitcoin_name(self):
        assert "BTCUSDT" in extract_symbols("Bitcoin price hits new high")

    def test_eth_ticker(self):
        assert "ETHUSDT" in extract_symbols("ETH surges 10% today")

    def test_solana_name(self):
        assert "SOLUSDT" in extract_symbols("Solana ecosystem growing fast")

    def test_multiple_symbols(self):
        symbols = extract_symbols("Bitcoin and Ethereum both rally, SOL follows")
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols
        assert "SOLUSDT" in symbols

    def test_no_symbols(self):
        assert extract_symbols("The stock market is up today") == []

    def test_short_ticker_word_boundary(self):
        # "sol" in "solution" should NOT match
        symbols = extract_symbols("A solution to the problem")
        assert "SOLUSDT" not in symbols

    def test_with_injected_map(self):
        """A caller-supplied extraction_map overrides the legacy default.

        Production callers (NewsService, RedditService) pass the
        config-driven map built in ``UniverseSettings.__post_init__`` so
        that all 50 watch_list coins — not just the 10 in the legacy
        constant — get tagged.
        """
        injected = {
            "aave": "AAVEUSDT",
            "render": "RENDERUSDT",
            "render network": "RENDERUSDT",
            "ondo": "ONDOUSDT",
            "ondo finance": "ONDOUSDT",
        }
        text = (
            "AAVE governance vote concludes; Render Network sees inflows; "
            "Ondo Finance launches new product."
        )
        symbols = extract_symbols(text, injected)
        assert "AAVEUSDT" in symbols
        assert "RENDERUSDT" in symbols
        assert "ONDOUSDT" in symbols
        # Legacy default would have returned [] for this text — confirm
        # the injection is what produced the matches.
        assert extract_symbols(text) == []


class TestNewsService:
    @pytest.mark.asyncio
    async def test_fetch_latest_news(self, test_db, test_settings, mock_finnhub_news_response):
        finnhub = MagicMock()
        finnhub.get_general_news = AsyncMock(return_value=mock_finnhub_news_response)
        scorer = SentimentScorer()

        svc = NewsService(finnhub, scorer, test_db, test_settings)
        articles = await svc.fetch_latest_news()

        # Old article should be filtered
        assert len(articles) == 3
        assert all(isinstance(a, NewsArticle) for a in articles)

    @pytest.mark.asyncio
    async def test_deduplication(self, test_db, test_settings, mock_finnhub_news_response):
        finnhub = MagicMock()
        finnhub.get_general_news = AsyncMock(return_value=mock_finnhub_news_response)
        scorer = SentimentScorer()

        svc = NewsService(finnhub, scorer, test_db, test_settings)
        first = await svc.fetch_latest_news()
        second = await svc.fetch_latest_news()

        assert len(first) == 3
        assert len(second) == 0  # All already in DB

    @pytest.mark.asyncio
    async def test_sentiment_scored(self, test_db, test_settings, mock_finnhub_news_response):
        finnhub = MagicMock()
        finnhub.get_general_news = AsyncMock(return_value=mock_finnhub_news_response)
        scorer = SentimentScorer()

        svc = NewsService(finnhub, scorer, test_db, test_settings)
        articles = await svc.fetch_latest_news()

        bullish = [a for a in articles if "rallies" in a.headline.lower()]
        assert bullish[0].sentiment_score > 0

    @pytest.mark.asyncio
    async def test_symbols_extracted(self, test_db, test_settings, mock_finnhub_news_response):
        finnhub = MagicMock()
        finnhub.get_general_news = AsyncMock(return_value=mock_finnhub_news_response)
        scorer = SentimentScorer()

        svc = NewsService(finnhub, scorer, test_db, test_settings)
        articles = await svc.fetch_latest_news()

        btc_articles = [a for a in articles if "BTCUSDT" in a.symbols]
        assert len(btc_articles) > 0

    @pytest.mark.asyncio
    async def test_persisted_to_db(self, test_db, test_settings, mock_finnhub_news_response):
        finnhub = MagicMock()
        finnhub.get_general_news = AsyncMock(return_value=mock_finnhub_news_response)
        scorer = SentimentScorer()

        svc = NewsService(finnhub, scorer, test_db, test_settings)
        await svc.fetch_latest_news()

        rows = await test_db.fetch_all("SELECT * FROM news_articles")
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_news_summary(self, test_db, test_settings, mock_finnhub_news_response):
        finnhub = MagicMock()
        finnhub.get_general_news = AsyncMock(return_value=mock_finnhub_news_response)
        scorer = SentimentScorer()

        svc = NewsService(finnhub, scorer, test_db, test_settings)
        await svc.fetch_latest_news()
        summary = await svc.get_news_summary(hours=24)

        assert summary["total_articles"] == 3
        assert isinstance(summary["avg_sentiment"], float)
