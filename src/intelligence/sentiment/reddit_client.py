"""Reddit PRAW client wrapper with async support, rate limiting, and error handling.

PRAW is synchronous — all calls are wrapped with asyncio.to_thread().
"""

import asyncio
from typing import Any

import praw
import prawcore

from src.config.settings import Settings
from src.core.decorators import rate_limit, retry, timed
from src.core.exceptions import RedditError
from src.core.logging import get_logger

log = get_logger("intelligence")


class RedditClient:
    """Reddit API client using PRAW for sentiment data.

    Args:
        settings: Application settings with reddit credentials.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        reddit_cfg = settings.reddit
        try:
            self._reddit = praw.Reddit(
                client_id=reddit_cfg.client_id,
                client_secret=reddit_cfg.client_secret,
                username=reddit_cfg.username,
                password=reddit_cfg.password,
                user_agent="TradingIntelligenceMCP/0.1.0",
            )
        except Exception as e:
            log.warning("Failed to initialize PRAW: {err}", err=str(e))
            self._reddit = None

    @retry(max_attempts=2, delay=3.0, exceptions=(RedditError, Exception))
    @rate_limit(calls_per_second=1.5)
    @timed
    async def get_hot_posts(self, subreddit: str, limit: int = 25) -> list[dict]:
        """Fetch hot posts from a subreddit.

        Args:
            subreddit: Subreddit name (without r/).
            limit: Max posts to fetch.

        Returns:
            List of post dicts with id, title, score, num_comments, etc.

        Raises:
            RedditError: On API failure.
        """
        if self._reddit is None:
            raise RedditError("Reddit client not initialized — check credentials")

        try:
            posts = await asyncio.to_thread(self._fetch_hot, subreddit, limit)
            log.debug("Fetched {n} hot posts from r/{sub}", n=len(posts), sub=subreddit)
            return posts
        except RedditError:
            raise
        except (praw.exceptions.PRAWException, prawcore.exceptions.PrawcoreException) as e:
            raise RedditError(
                f"Reddit API error for r/{subreddit}: {e}",
                details={"subreddit": subreddit},
            )
        except Exception as e:
            raise RedditError(
                f"Unexpected error fetching r/{subreddit}: {e}",
                details={"subreddit": subreddit, "error": str(e)},
            )

    @retry(max_attempts=2, delay=3.0, exceptions=(RedditError, Exception))
    @rate_limit(calls_per_second=1.5)
    @timed
    async def get_new_posts(self, subreddit: str, limit: int = 25) -> list[dict]:
        """Fetch newest posts from a subreddit.

        Args:
            subreddit: Subreddit name.
            limit: Max posts.

        Returns:
            List of post dicts.
        """
        if self._reddit is None:
            raise RedditError("Reddit client not initialized")

        try:
            posts = await asyncio.to_thread(self._fetch_new, subreddit, limit)
            log.debug("Fetched {n} new posts from r/{sub}", n=len(posts), sub=subreddit)
            return posts
        except (praw.exceptions.PRAWException, prawcore.exceptions.PrawcoreException) as e:
            raise RedditError(f"Reddit API error: {e}", details={"subreddit": subreddit})
        except Exception as e:
            raise RedditError(f"Error fetching new posts: {e}", details={"subreddit": subreddit})

    @retry(max_attempts=2, delay=3.0, exceptions=(RedditError, Exception))
    @rate_limit(calls_per_second=1.5)
    @timed
    async def search_posts(self, subreddit: str, query: str, limit: int = 10) -> list[dict]:
        """Search posts in a subreddit.

        Args:
            subreddit: Subreddit name.
            query: Search query.
            limit: Max results.

        Returns:
            List of matching post dicts.
        """
        if self._reddit is None:
            raise RedditError("Reddit client not initialized")

        try:
            posts = await asyncio.to_thread(self._fetch_search, subreddit, query, limit)
            log.debug("Search '{q}' in r/{sub}: {n} results", q=query, sub=subreddit, n=len(posts))
            return posts
        except (praw.exceptions.PRAWException, prawcore.exceptions.PrawcoreException) as e:
            raise RedditError(f"Reddit search error: {e}", details={"subreddit": subreddit, "query": query})
        except Exception as e:
            raise RedditError(f"Error searching posts: {e}", details={"subreddit": subreddit})

    async def validate_connection(self) -> bool:
        """Test the Reddit connection by fetching the authenticated user.

        Returns:
            True if connection is valid.
        """
        if self._reddit is None:
            return False
        try:
            await asyncio.to_thread(lambda: self._reddit.user.me())
            log.info("Reddit connection validated")
            return True
        except Exception as e:
            log.warning("Reddit connection validation failed: {err}", err=str(e))
            return False

    # --- Sync helpers (run in thread) ---

    def _fetch_hot(self, subreddit: str, limit: int) -> list[dict]:
        """Sync: fetch hot posts."""
        return [
            self._post_to_dict(post)
            for post in self._reddit.subreddit(subreddit).hot(limit=limit)
        ]

    def _fetch_new(self, subreddit: str, limit: int) -> list[dict]:
        """Sync: fetch new posts."""
        return [
            self._post_to_dict(post)
            for post in self._reddit.subreddit(subreddit).new(limit=limit)
        ]

    def _fetch_search(self, subreddit: str, query: str, limit: int) -> list[dict]:
        """Sync: search posts."""
        return [
            self._post_to_dict(post)
            for post in self._reddit.subreddit(subreddit).search(query, limit=limit)
        ]

    @staticmethod
    def _post_to_dict(post: Any) -> dict:
        """Extract relevant fields from a PRAW Submission."""
        return {
            "id": post.id,
            "title": post.title,
            "score": post.score,
            "num_comments": post.num_comments,
            "upvote_ratio": post.upvote_ratio,
            "permalink": post.permalink,
            "created_utc": post.created_utc,
            "subreddit": str(post.subreddit),
        }
