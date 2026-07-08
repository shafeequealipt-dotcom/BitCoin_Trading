"""News service: fetch, filter, score, deduplicate, and persist news articles."""

import re
from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.core.decorators import timed
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import NewsArticle
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.news_repo import NewsRepository
from src.intelligence.news.finnhub_client import FinnhubClient
from src.intelligence.sentiment.scorer import SentimentScorer
from src.intelligence.signals.signal_models import SYMBOL_EXTRACTION_MAP

log = get_logger("intelligence")


class NewsService:
    """Service for fetching, scoring, and persisting financial news.

    Args:
        finnhub_client: Finnhub API client.
        scorer: Sentiment scorer instance.
        db: Database manager.
        settings: Application settings.
    """

    def __init__(
        self,
        finnhub_client: FinnhubClient,
        scorer: SentimentScorer,
        db: DatabaseManager,
        settings: Settings,
    ) -> None:
        self._finnhub = finnhub_client
        self._scorer = scorer
        self._db = db
        self._settings = settings
        self._news_repo = NewsRepository(db)
        # Snapshot the config-driven extraction map at construction time;
        # Settings is immutable post-startup so this needs no refresh.
        self._extraction_map = settings.universe.extraction_map

    @timed
    async def fetch_latest_news(
        self,
        category: str = "crypto",
        max_articles: int | None = None,
    ) -> list[NewsArticle]:
        """Fetch, score, deduplicate, and persist latest news.

        Phase 7 (post-Layer-1 fix) added a structured ``FINNHUB_COVERAGE``
        log line so operators can attribute sentiment staleness to one
        of: upstream returning few articles, the 24 h cutoff dropping
        most of them, or dedup gating articles already persisted.

        Args:
            category: Finnhub news category.
            max_articles: Override max articles from settings.

        Returns:
            List of new (non-duplicate) NewsArticle objects.
        """
        if max_articles is None:
            max_articles = self._settings.finnhub.max_articles_per_fetch

        raw_articles = await self._finnhub.get_general_news(category=category)
        cutoff = now_utc() - timedelta(hours=24)
        new_articles: list[NewsArticle] = []
        # Phase 7: per-funnel-stage counters for the FINNHUB_COVERAGE log.
        skipped_old = 0
        skipped_no_headline = 0
        skipped_dedup = 0

        for raw in raw_articles[:max_articles]:
            # Filter old articles
            ts = raw.get("datetime", 0)
            published = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else now_utc()
            if published < cutoff:
                skipped_old += 1
                continue

            headline = raw.get("headline", "")
            if not headline:
                skipped_no_headline += 1
                continue

            # Deduplicate
            if await self._news_repo.headline_exists(headline):
                skipped_dedup += 1
                continue

            summary = raw.get("summary", "")
            text = f"{headline} {summary}"

            # Score sentiment
            sentiment = self._scorer.score_text(text)

            # Extract symbols using the config-driven map.
            symbols = extract_symbols(text, self._extraction_map)

            article = NewsArticle(
                id=str(raw.get("id", generate_id("news"))),
                headline=headline,
                source=raw.get("source", ""),
                url=raw.get("url", ""),
                summary=summary[:500],
                sentiment_score=sentiment,
                symbols=symbols,
                category=raw.get("category", category),
                published_at=published,
                fetched_at=now_utc(),
            )

            await self._news_repo.save_article(article)
            new_articles.append(article)

        log.info(
            f"FINNHUB_COVERAGE | category={category} returned={len(raw_articles)} "
            f"considered={min(len(raw_articles), max_articles)} new={len(new_articles)} "
            f"skipped_old={skipped_old} skipped_no_headline={skipped_no_headline} "
            f"skipped_dedup={skipped_dedup} | {ctx()}"
        )
        log.info(
            "Fetched {total} articles, {new} new after dedup",
            total=len(raw_articles),
            new=len(new_articles),
        )
        return new_articles

    @timed
    async def get_news_for_symbol(self, symbol: str, hours: int = 24) -> list[NewsArticle]:
        """Get news mentioning a specific symbol.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            hours: How far back to look.

        Returns:
            List of relevant NewsArticle objects.
        """
        return await self._news_repo.get_by_symbol(symbol, hours=hours)

    @timed
    async def search_news(self, keyword: str, limit: int = 20) -> list[NewsArticle]:
        """Search news articles by keyword.

        Args:
            keyword: Search term.
            limit: Max results.

        Returns:
            Matching NewsArticle objects.
        """
        return await self._news_repo.search(keyword, limit=limit)

    @timed
    async def get_news_summary(self, hours: int = 6) -> dict:
        """Generate a summary of recent news.

        Args:
            hours: How far back to analyze.

        Returns:
            Dict with total_articles, avg_sentiment, top_bullish, top_bearish,
            most_mentioned_symbols.
        """
        articles = await self._news_repo.get_recent(hours=hours)

        if not articles:
            return {
                "total_articles": 0,
                "avg_sentiment": 0.0,
                "top_bullish": None,
                "top_bearish": None,
                "most_mentioned_symbols": [],
            }

        scores = [a.sentiment_score for a in articles]
        avg = sum(scores) / len(scores)

        sorted_bull = sorted(articles, key=lambda a: a.sentiment_score, reverse=True)
        sorted_bear = sorted(articles, key=lambda a: a.sentiment_score)

        # Count symbol mentions
        symbol_counts: dict[str, int] = {}
        for a in articles:
            for s in a.symbols:
                symbol_counts[s] = symbol_counts.get(s, 0) + 1

        top_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total_articles": len(articles),
            "avg_sentiment": round(avg, 3),
            "top_bullish": sorted_bull[0].headline if sorted_bull else None,
            "top_bearish": sorted_bear[0].headline if sorted_bear else None,
            "most_mentioned_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
        }


def extract_symbols(
    text: str,
    extraction_map: dict[str, str] | None = None,
) -> list[str]:
    """Extract crypto symbols mentioned in text.

    Maps common names and tickers to system symbols.
    E.g. "Bitcoin" -> "BTCUSDT", "ETH" -> "ETHUSDT".

    Args:
        text: Input text.
        extraction_map: Optional override of the alias -> symbol map.
            Defaults to the legacy 10-coin ``SYMBOL_EXTRACTION_MAP`` for
            back-compat with tests/scripts that have no Settings reference.
            Production callers (NewsService, RedditService) inject the
            runtime map built from ``[universe.coin_aliases]`` at boot.

    Returns:
        Deduplicated list of symbol strings.
    """
    if extraction_map is None:
        extraction_map = SYMBOL_EXTRACTION_MAP
    lower = text.lower()
    found: set[str] = set()

    for name, symbol in extraction_map.items():
        # Use word boundary matching for short tickers to avoid false positives
        if len(name) <= 4:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, lower):
                found.add(symbol)
        else:
            if name in lower:
                found.add(symbol)

    return sorted(found)
