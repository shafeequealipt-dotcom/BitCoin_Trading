"""Tests for SentimentAggregator: multi-source aggregation, persistence."""

import pytest
from unittest.mock import AsyncMock

from src.core.types import FearGreedData, NewsArticle, RedditPost
from src.core.utils import now_utc
from src.database.repositories.altdata_repo import AltDataRepository
from src.database.repositories.news_repo import NewsRepository
from src.database.repositories.sentiment_repo import SentimentRepository
from src.intelligence.sentiment.aggregator import SentimentAggregator
from src.intelligence.sentiment.scorer import SentimentScorer


async def _seed_test_data(test_db):
    """Seed DB with test news, reddit, and fear & greed data."""
    news_repo = NewsRepository(test_db)
    sent_repo = SentimentRepository(test_db)
    alt_repo = AltDataRepository(test_db)

    # Bullish news
    await news_repo.save_article(NewsArticle(
        id="n1", headline="Bitcoin rallies bullish", source="Test",
        url="", summary="", sentiment_score=0.5,
        symbols=["BTCUSDT"], published_at=now_utc(), fetched_at=now_utc(),
    ))

    # Bearish reddit
    await sent_repo.save_reddit_post(RedditPost(
        id="r1", subreddit="crypto", title="BTC crash incoming bearish",
        score=100, num_comments=50, upvote_ratio=0.8,
        sentiment_score=-0.3, symbols_mentioned=["BTCUSDT"],
        created_at=now_utc(), fetched_at=now_utc(),
    ))

    # Fear & Greed
    await alt_repo.save_fear_greed(FearGreedData(
        value=30, classification="Fear", timestamp=now_utc(),
    ))


class TestSentimentAggregator:
    @pytest.mark.asyncio
    async def test_aggregate_for_symbol(self, test_db):
        await _seed_test_data(test_db)
        scorer = SentimentScorer()
        agg = SentimentAggregator(test_db, scorer)

        result = await agg.aggregate_for_symbol("BTCUSDT")

        assert result["symbol"] == "BTCUSDT"
        assert isinstance(result["overall_score"], float)
        assert result["news_count"] == 1
        assert result["reddit_count"] == 1
        assert result["fear_greed_value"] == 30
        assert result["level"] in ("very_bullish", "bullish", "neutral", "bearish", "very_bearish")

    @pytest.mark.asyncio
    async def test_aggregate_persisted(self, test_db):
        await _seed_test_data(test_db)
        scorer = SentimentScorer()
        agg = SentimentAggregator(test_db, scorer)

        await agg.aggregate_for_symbol("BTCUSDT")

        rows = await test_db.fetch_all("SELECT * FROM aggregated_sentiment")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_aggregate_all_symbols(self, test_db):
        await _seed_test_data(test_db)
        scorer = SentimentScorer()
        agg = SentimentAggregator(test_db, scorer)

        results = await agg.aggregate_all_symbols(["BTCUSDT", "ETHUSDT"])
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_no_data_returns_neutral(self, test_db):
        scorer = SentimentScorer()
        agg = SentimentAggregator(test_db, scorer)

        result = await agg.aggregate_for_symbol("DOGEUSDT")
        assert result["overall_score"] == 0.0 or abs(result["overall_score"]) < 0.5

    @pytest.mark.asyncio
    async def test_sentiment_shift(self, test_db):
        await _seed_test_data(test_db)
        scorer = SentimentScorer()
        agg = SentimentAggregator(test_db, scorer)

        # Create two aggregation snapshots
        await agg.aggregate_for_symbol("BTCUSDT")
        shift = await agg.get_sentiment_shift("BTCUSDT")

        assert shift["symbol"] == "BTCUSDT"
        assert shift["direction"] in ("improving", "worsening", "stable")

    @pytest.mark.asyncio
    async def test_market_mood(self, test_db):
        await _seed_test_data(test_db)
        scorer = SentimentScorer()
        agg = SentimentAggregator(test_db, scorer)

        mood = await agg.get_market_mood()
        assert "overall_mood" in mood
        assert "fear_greed" in mood
        assert "avg_sentiment" in mood
