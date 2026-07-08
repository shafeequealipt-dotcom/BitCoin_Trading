"""Tests for RedditService: multi-subreddit scanning, dedup, graceful degradation."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.types import RedditPost
from src.intelligence.sentiment.reddit_service import RedditService
from src.intelligence.sentiment.scorer import SentimentScorer


class TestRedditService:
    @pytest.mark.asyncio
    async def test_scan_subreddits(self, test_db, test_settings, mock_reddit_posts):
        reddit_client = MagicMock()
        reddit_client.get_hot_posts = AsyncMock(return_value=mock_reddit_posts)
        scorer = SentimentScorer()

        svc = RedditService(reddit_client, scorer, test_db, test_settings)
        posts = await svc.scan_subreddits()

        # 2 subreddits * 3 posts = 6, but dedup by ID means 3 unique
        assert len(posts) == 3
        assert all(isinstance(p, RedditPost) for p in posts)

    @pytest.mark.asyncio
    async def test_deduplication_by_id(self, test_db, test_settings, mock_reddit_posts):
        reddit_client = MagicMock()
        reddit_client.get_hot_posts = AsyncMock(return_value=mock_reddit_posts)
        scorer = SentimentScorer()

        svc = RedditService(reddit_client, scorer, test_db, test_settings)
        first = await svc.scan_subreddits()
        second = await svc.scan_subreddits()

        assert len(first) == 3
        assert len(second) == 0  # All already in DB

    @pytest.mark.asyncio
    async def test_graceful_degradation(self, test_db, test_settings, mock_reddit_posts):
        """If one subreddit fails, others should still work."""
        call_count = 0

        async def flaky_fetch(sub, limit=25):
            nonlocal call_count
            call_count += 1
            if sub == "cryptocurrency":
                raise Exception("Reddit API timeout")
            return mock_reddit_posts

        reddit_client = MagicMock()
        reddit_client.get_hot_posts = AsyncMock(side_effect=flaky_fetch)
        scorer = SentimentScorer()

        svc = RedditService(reddit_client, scorer, test_db, test_settings)
        posts = await svc.scan_subreddits()

        # First sub fails, second succeeds
        assert len(posts) == 3

    @pytest.mark.asyncio
    async def test_sentiment_scored(self, test_db, test_settings, mock_reddit_posts):
        reddit_client = MagicMock()
        reddit_client.get_hot_posts = AsyncMock(return_value=mock_reddit_posts)
        scorer = SentimentScorer()

        svc = RedditService(reddit_client, scorer, test_db, test_settings)
        posts = await svc.scan_subreddits()

        bullish_post = [p for p in posts if "moon" in p.title.lower()]
        assert bullish_post[0].sentiment_score > 0

    @pytest.mark.asyncio
    async def test_persisted_to_db(self, test_db, test_settings, mock_reddit_posts):
        reddit_client = MagicMock()
        reddit_client.get_hot_posts = AsyncMock(return_value=mock_reddit_posts)
        scorer = SentimentScorer()

        svc = RedditService(reddit_client, scorer, test_db, test_settings)
        await svc.scan_subreddits()

        rows = await test_db.fetch_all("SELECT * FROM reddit_posts")
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_get_subreddit_mood(self, test_db, test_settings, mock_reddit_posts):
        reddit_client = MagicMock()
        reddit_client.get_hot_posts = AsyncMock(return_value=mock_reddit_posts)
        scorer = SentimentScorer()

        svc = RedditService(reddit_client, scorer, test_db, test_settings)
        await svc.scan_subreddits()
        mood = await svc.get_subreddit_mood("cryptocurrency")

        assert mood["post_count"] == 3
        assert isinstance(mood["avg_sentiment"], float)
        assert mood["dominant_mood"] is not None
