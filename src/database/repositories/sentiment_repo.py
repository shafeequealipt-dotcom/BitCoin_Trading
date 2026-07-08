"""Sentiment repository: save and query Reddit posts, aggregated sentiment."""

import json
from datetime import datetime, timedelta, timezone

from src.core.logging import get_logger
from src.core.types import RedditPost
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")


class SentimentRepository:
    """Repository for sentiment data persistence.

    Args:
        db: Active DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def save_reddit_post(self, post: RedditPost) -> None:
        """Save a Reddit post, ignoring duplicates by ID.

        Args:
            post: RedditPost dataclass.
        """
        await self._db.execute(
            """
            INSERT OR IGNORE INTO reddit_posts
            (id, subreddit, title, score, num_comments, upvote_ratio,
             sentiment_score, symbols_mentioned, permalink, created_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post.id,
                post.subreddit,
                post.title,
                post.score,
                post.num_comments,
                post.upvote_ratio,
                post.sentiment_score,
                json.dumps(post.symbols_mentioned),
                post.permalink,
                post.created_at.isoformat(),
                post.fetched_at.isoformat(),
            ),
        )

    async def post_exists(self, post_id: str) -> bool:
        """Check if a Reddit post already exists in the database.

        Args:
            post_id: Reddit post ID.

        Returns:
            True if exists.
        """
        row = await self._db.fetch_one(
            "SELECT 1 FROM reddit_posts WHERE id = ? LIMIT 1", (post_id,)
        )
        return row is not None

    async def get_recent_posts(self, hours: int = 24, limit: int = 100) -> list[RedditPost]:
        """Fetch recent Reddit posts.

        Args:
            hours: How far back to look.
            limit: Max posts.

        Returns:
            List of RedditPost sorted by created_at descending.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM reddit_posts WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        )
        return [_row_to_post(r) for r in rows]

    async def get_posts_by_symbol(self, symbol: str, hours: int = 24, limit: int = 50) -> list[RedditPost]:
        """Fetch posts mentioning a specific symbol.

        Args:
            symbol: Trading pair.
            hours: How far back.
            limit: Max posts.

        Returns:
            Filtered RedditPost list.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM reddit_posts WHERE symbols_mentioned LIKE ? AND created_at > ? ORDER BY created_at DESC LIMIT ?",
            (f"%{symbol}%", cutoff, limit),
        )
        return [_row_to_post(r) for r in rows]

    async def get_posts_by_subreddit(self, subreddit: str, hours: int = 24, limit: int = 50) -> list[RedditPost]:
        """Fetch posts from a specific subreddit.

        Args:
            subreddit: Subreddit name.
            hours: How far back.
            limit: Max posts.

        Returns:
            RedditPost list.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM reddit_posts WHERE subreddit = ? AND created_at > ? ORDER BY created_at DESC LIMIT ?",
            (subreddit, cutoff, limit),
        )
        return [_row_to_post(r) for r in rows]

    async def save_aggregated_sentiment(self, data: dict) -> None:
        """Save an aggregated sentiment record.

        Args:
            data: Dict with symbol, overall_score, level, news_score, news_count,
                  reddit_score, reddit_count, fear_greed_value, momentum.
        """
        await self._db.execute(
            """
            INSERT INTO aggregated_sentiment
            (symbol, overall_score, level, news_score, news_count,
             reddit_score, reddit_count, fear_greed_value, momentum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("symbol", ""),
                data.get("overall_score", 0.0),
                data.get("level", "neutral"),
                data.get("news_score", 0.0),
                data.get("news_count", 0),
                data.get("reddit_score", 0.0),
                data.get("reddit_count", 0),
                data.get("fear_greed_value", 50),
                data.get("momentum", 0.0),
            ),
        )

    async def get_sentiment_for_symbol(self, symbol: str, limit: int = 1) -> list[dict]:
        """Fetch latest aggregated sentiment for a symbol.

        Args:
            symbol: Trading pair.
            limit: Number of records.

        Returns:
            List of sentiment dicts.
        """
        rows = await self._db.fetch_all(
            "SELECT * FROM aggregated_sentiment WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit),
        )
        return [dict(r) for r in rows]

    async def get_sentiment_history(self, symbol: str, hours: int = 24) -> list[dict]:
        """Fetch sentiment history for a symbol.

        Args:
            symbol: Trading pair.
            hours: How far back.

        Returns:
            Sentiment history dicts.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM aggregated_sentiment WHERE symbol = ? AND created_at > ? ORDER BY created_at ASC",
            (symbol, cutoff),
        )
        return [dict(r) for r in rows]


def _row_to_post(row: dict) -> RedditPost:
    """Convert a database row to a RedditPost dataclass."""
    symbols = row.get("symbols_mentioned", "[]")
    if isinstance(symbols, str):
        try:
            symbols = json.loads(symbols)
        except (json.JSONDecodeError, TypeError):
            symbols = []

    return RedditPost(
        id=row["id"],
        subreddit=row.get("subreddit", ""),
        title=row.get("title", ""),
        score=row.get("score", 0),
        num_comments=row.get("num_comments", 0),
        upvote_ratio=row.get("upvote_ratio", 0.0),
        sentiment_score=row.get("sentiment_score", 0.0),
        symbols_mentioned=symbols,
        permalink=row.get("permalink", ""),
        created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else now_utc(),
        fetched_at=datetime.fromisoformat(row["fetched_at"]) if row.get("fetched_at") else now_utc(),
    )
