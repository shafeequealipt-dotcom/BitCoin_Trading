"""Reddit sentiment service: multi-subreddit scanning, scoring, and persistence."""

from collections import Counter
from datetime import datetime, timezone

from src.config.settings import Settings
from src.core.decorators import timed
from src.core.logging import get_logger
from src.core.types import RedditPost, SentimentLevel
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.sentiment_repo import SentimentRepository
from src.intelligence.news.news_service import extract_symbols
from src.intelligence.sentiment.reddit_client import RedditClient
from src.intelligence.sentiment.scorer import SentimentScorer

log = get_logger("intelligence")


class RedditService:
    """Service for Reddit sentiment scanning and analysis.

    Scans configured subreddits, scores posts, extracts symbols, and persists.
    Gracefully handles per-subreddit failures.

    Args:
        reddit_client: Reddit PRAW client.
        scorer: Sentiment scorer.
        db: Database manager.
        settings: Application settings.
    """

    def __init__(
        self,
        reddit_client: RedditClient,
        scorer: SentimentScorer,
        db: DatabaseManager,
        settings: Settings,
    ) -> None:
        self._reddit = reddit_client
        self._scorer = scorer
        self._db = db
        self._settings = settings
        self._sentiment_repo = SentimentRepository(db)
        # Same config-driven map used by NewsService — both consume Finnhub
        # and Reddit text, and share the per-coin alias config.
        self._extraction_map = settings.universe.extraction_map

    @timed
    async def scan_subreddits(self) -> list[RedditPost]:
        """Scan all configured subreddits for hot posts.

        Scores each post, extracts symbols, deduplicates, and persists.
        If one subreddit fails, continues with others.

        Returns:
            List of new (non-duplicate) RedditPost objects.
        """
        subreddits = self._settings.reddit.subreddits
        limit = self._settings.reddit.max_posts_per_sub
        all_posts: list[RedditPost] = []

        for sub in subreddits:
            try:
                raw_posts = await self._reddit.get_hot_posts(sub, limit=limit)
                for raw in raw_posts:
                    post_id = raw.get("id", "")
                    if not post_id:
                        continue

                    # Deduplicate
                    if await self._sentiment_repo.post_exists(post_id):
                        continue

                    title = raw.get("title", "")
                    sentiment = self._scorer.score_text(title)
                    symbols = extract_symbols(title, self._extraction_map)

                    created_utc = raw.get("created_utc", 0)
                    created_at = (
                        datetime.fromtimestamp(created_utc, tz=timezone.utc)
                        if created_utc
                        else now_utc()
                    )

                    post = RedditPost(
                        id=post_id,
                        subreddit=raw.get("subreddit", sub),
                        title=title,
                        score=raw.get("score", 0),
                        num_comments=raw.get("num_comments", 0),
                        upvote_ratio=raw.get("upvote_ratio", 0.0),
                        sentiment_score=sentiment,
                        symbols_mentioned=symbols,
                        permalink=raw.get("permalink", ""),
                        created_at=created_at,
                        fetched_at=now_utc(),
                    )

                    await self._sentiment_repo.save_reddit_post(post)
                    all_posts.append(post)

            except Exception as e:
                log.warning(
                    "Failed to scan r/{sub}, continuing with others: {err}",
                    sub=sub, err=str(e),
                )

        log.info("Scanned {n} subreddits, {p} new posts", n=len(subreddits), p=len(all_posts))
        return all_posts

    @timed
    async def get_subreddit_mood(self, subreddit: str) -> dict:
        """Analyze the mood of a subreddit.

        Args:
            subreddit: Subreddit name.

        Returns:
            Dict with avg_sentiment, post_count, top_post, dominant_mood.
        """
        posts = await self._sentiment_repo.get_posts_by_subreddit(subreddit, hours=24)

        if not posts:
            return {
                "subreddit": subreddit,
                "avg_sentiment": 0.0,
                "post_count": 0,
                "top_post": None,
                "dominant_mood": SentimentLevel.NEUTRAL.value,
            }

        scores = [p.sentiment_score for p in posts]
        avg = sum(scores) / len(scores)
        top = max(posts, key=lambda p: p.score)

        return {
            "subreddit": subreddit,
            "avg_sentiment": round(avg, 3),
            "post_count": len(posts),
            "top_post": top.title,
            "dominant_mood": self._scorer.score_to_level(avg).value,
        }

    @timed
    async def get_symbol_buzz(self, symbol: str) -> dict:
        """Get buzz metrics for a symbol across Reddit.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").

        Returns:
            Dict with mention_count, avg_sentiment, trending direction.
        """
        recent = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=12)
        older = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=24)

        recent_count = len(recent)
        older_count = len(older) - recent_count

        recent_avg = sum(p.sentiment_score for p in recent) / len(recent) if recent else 0.0
        older_posts = [p for p in older if p not in recent]
        older_avg = sum(p.sentiment_score for p in older_posts) / len(older_posts) if older_posts else 0.0

        if recent_count > older_count * 1.5:
            trend = "increasing"
        elif recent_count < older_count * 0.5:
            trend = "decreasing"
        else:
            trend = "stable"

        return {
            "symbol": symbol,
            "mention_count_12h": recent_count,
            "mention_count_24h": len(older),
            "avg_sentiment": round(recent_avg, 3),
            "trend": trend,
            "sentiment_direction": "improving" if recent_avg > older_avg else "worsening" if recent_avg < older_avg else "stable",
        }

    @timed
    async def get_most_mentioned(self, hours: int = 24, top_n: int = 10) -> list[dict]:
        """Get the most mentioned symbols across all Reddit posts.

        Args:
            hours: How far back to look.
            top_n: Number of top symbols to return.

        Returns:
            List of dicts with symbol, mention_count, avg_sentiment, sample_titles.
        """
        posts = await self._sentiment_repo.get_recent_posts(hours=hours)

        symbol_data: dict[str, list[RedditPost]] = {}
        for post in posts:
            for sym in post.symbols_mentioned:
                if sym not in symbol_data:
                    symbol_data[sym] = []
                symbol_data[sym].append(post)

        results = []
        for symbol, sym_posts in symbol_data.items():
            avg_sent = sum(p.sentiment_score for p in sym_posts) / len(sym_posts)
            sample_titles = [p.title for p in sorted(sym_posts, key=lambda p: p.score, reverse=True)[:3]]
            results.append({
                "symbol": symbol,
                "mention_count": len(sym_posts),
                "avg_sentiment": round(avg_sent, 3),
                "sample_titles": sample_titles,
            })

        results.sort(key=lambda r: r["mention_count"], reverse=True)
        return results[:top_n]
