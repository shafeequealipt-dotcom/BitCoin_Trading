"""Tests for RedditClient: async wrapping, error handling."""

import pytest
from unittest.mock import MagicMock, patch

from src.core.exceptions import RedditError
from src.intelligence.sentiment.reddit_client import RedditClient


class TestRedditClient:
    @pytest.mark.asyncio
    async def test_get_hot_posts(self, test_settings, mock_reddit_posts):
        with patch("src.intelligence.sentiment.reddit_client.praw") as mock_praw:
            mock_reddit = MagicMock()
            mock_sub = MagicMock()
            mock_submissions = []
            for p in mock_reddit_posts:
                sub = MagicMock()
                sub.id = p["id"]
                sub.title = p["title"]
                sub.score = p["score"]
                sub.num_comments = p["num_comments"]
                sub.upvote_ratio = p["upvote_ratio"]
                sub.permalink = p["permalink"]
                sub.created_utc = p["created_utc"]
                sub.subreddit = MagicMock(__str__=lambda s: "cryptocurrency")
                mock_submissions.append(sub)

            mock_sub.hot.return_value = mock_submissions
            mock_reddit.subreddit.return_value = mock_sub
            mock_praw.Reddit.return_value = mock_reddit

            client = RedditClient(test_settings)
            client._reddit = mock_reddit
            posts = await client.get_hot_posts("cryptocurrency", limit=10)

            assert len(posts) == 3
            assert posts[0]["id"] == "post001"

    @pytest.mark.asyncio
    async def test_no_client_raises(self, test_settings):
        with patch("src.intelligence.sentiment.reddit_client.praw") as mock_praw:
            mock_praw.Reddit.side_effect = Exception("bad creds")
            client = RedditClient(test_settings)
            client._reddit = None
            with pytest.raises(RedditError, match="not initialized"):
                await client.get_hot_posts("cryptocurrency")

    @pytest.mark.asyncio
    async def test_validate_connection_success(self, test_settings):
        with patch("src.intelligence.sentiment.reddit_client.praw"):
            client = RedditClient(test_settings)
            client._reddit = MagicMock()
            client._reddit.user.me.return_value = MagicMock()
            result = await client.validate_connection()
            assert result is True

    @pytest.mark.asyncio
    async def test_validate_connection_failure(self, test_settings):
        with patch("src.intelligence.sentiment.reddit_client.praw"):
            client = RedditClient(test_settings)
            client._reddit = MagicMock()
            client._reddit.user.me.side_effect = Exception("auth fail")
            result = await client.validate_connection()
            assert result is False
