"""News repository: save and query news articles and economic calendar events."""

import json
import re
from datetime import datetime, timedelta, timezone

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import NewsArticle
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")


# Phase 6 (session-stability): base-asset normaliser.
#
# The news_articles.symbols column stores the base asset ticker (BTC, ETH,
# BONK) not the derivative trading pair (1000BONKUSDT, 10000SATSUSDT).
# Pre-fix, ``get_by_symbol("1000BONKUSDT")`` did a literal ``LIKE
# %1000BONKUSDT%`` match and returned nothing — 92 % of 644 lookups in the
# 2026-04-24 observability window returned zero. The normaliser strips
# the quote-currency suffix and the numeric prefix that Bybit prepends
# for low-priced coins, yielding the base asset the news feed actually
# indexes on.
_NUMERIC_PREFIXES: tuple[str, ...] = ("10000", "1000", "100")
_QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "USD", "PERP")
_BASE_ASSET_RE = re.compile(r"^[A-Z][A-Z0-9]*$")


def extract_base_asset(symbol: str) -> str | None:
    """Return the base asset ticker for a derivative symbol, or None.

    Examples:
        "BTCUSDT"      -> "BTC"
        "1000BONKUSDT" -> "BONK"
        "10000SATSUSDT"-> "SATS"
        "ETHPERP"      -> "ETH"
        "BTC"          -> None   (already base)
        "bonkusdt"     -> "BONK" (case-insensitive)

    Returns None when the normaliser cannot extract a distinct base (no
    match / equal to input) so callers can decide whether to retry.
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    if not s:
        return None

    for suffix in _QUOTE_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break

    for prefix in _NUMERIC_PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix):]
            break

    if not s or s == symbol.strip().upper():
        return None
    if not _BASE_ASSET_RE.match(s):
        return None
    return s


class NewsRepository:
    """Repository for news article persistence.

    Args:
        db: Active DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def save_article(self, article: NewsArticle) -> None:
        """Save a news article, ignoring duplicates by ID.

        Args:
            article: NewsArticle dataclass.
        """
        await self._db.execute(
            """
            INSERT OR IGNORE INTO news_articles
            (id, headline, source, url, summary, sentiment_score, symbols, category, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article.id,
                article.headline,
                article.source,
                article.url,
                article.summary,
                article.sentiment_score,
                json.dumps(article.symbols),
                article.category,
                article.published_at.isoformat(),
                article.fetched_at.isoformat(),
            ),
        )

    async def get_recent(self, hours: int = 24, limit: int = 100) -> list[NewsArticle]:
        """Fetch recent news articles.

        Args:
            hours: How far back to look.
            limit: Maximum articles to return.

        Returns:
            List of NewsArticle sorted by published_at descending.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM news_articles WHERE published_at > ? ORDER BY published_at DESC LIMIT ?",
            (cutoff, limit),
        )
        return [_row_to_article(r) for r in rows]

    async def get_by_symbol(self, symbol: str, hours: int = 24, limit: int = 50) -> list[NewsArticle]:
        """Fetch news mentioning a specific symbol.

        Phase 6 (session-stability): on a miss, retry with the
        normalised base asset (e.g. 1000BONKUSDT → BONK). News feeds
        index on the base ticker, so the literal derivative symbol
        almost never matches. Emits ``SENT_BASEASSET_FALLBACK`` when
        the fallback returns rows so operators can see coverage lift.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            hours: How far back to look.
            limit: Max articles.

        Returns:
            Filtered list of NewsArticle.
        """
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            "SELECT * FROM news_articles WHERE symbols LIKE ? AND published_at > ? ORDER BY published_at DESC LIMIT ?",
            (f"%{symbol}%", cutoff, limit),
        )
        if rows:
            return [_row_to_article(r) for r in rows]

        # Miss on the literal symbol — try base-asset fallback.
        base = extract_base_asset(symbol)
        if base and base != symbol:
            fallback_rows = await self._db.fetch_all(
                "SELECT * FROM news_articles WHERE symbols LIKE ? AND published_at > ? ORDER BY published_at DESC LIMIT ?",
                (f"%{base}%", cutoff, limit),
            )
            if fallback_rows:
                log.info(
                    f"SENT_BASEASSET_FALLBACK | sym={symbol} base={base} "
                    f"rows={len(fallback_rows)} | {ctx()}"
                )
                return [_row_to_article(r) for r in fallback_rows]

        return []

    async def search(self, keyword: str, limit: int = 20) -> list[NewsArticle]:
        """Search articles by headline keyword.

        Args:
            keyword: Search term.
            limit: Max results.

        Returns:
            Matching articles.
        """
        rows = await self._db.fetch_all(
            "SELECT * FROM news_articles WHERE headline LIKE ? ORDER BY published_at DESC LIMIT ?",
            (f"%{keyword}%", limit),
        )
        return [_row_to_article(r) for r in rows]

    async def headline_exists(self, headline: str) -> bool:
        """Check if a headline already exists in the database.

        Args:
            headline: Article headline.

        Returns:
            True if a matching headline exists.
        """
        row = await self._db.fetch_one(
            "SELECT 1 FROM news_articles WHERE headline = ? LIMIT 1",
            (headline,),
        )
        return row is not None

    async def save_calendar_event(self, event: dict) -> None:
        """Save an economic calendar event.

        Args:
            event: Dict with event_name, country, impact, actual, estimate, previous, event_time.
        """
        await self._db.execute(
            """
            INSERT INTO economic_calendar
            (event_name, country, impact, actual, estimate, previous, event_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("event_name", ""),
                event.get("country", ""),
                event.get("impact", "low"),
                event.get("actual", ""),
                event.get("estimate", ""),
                event.get("previous", ""),
                event.get("event_time", ""),
            ),
        )

    async def get_calendar_events(self, impact: str | None = None, limit: int = 50) -> list[dict]:
        """Fetch economic calendar events.

        Args:
            impact: Filter by impact level ("high", "medium", "low").
            limit: Max events.

        Returns:
            List of event dicts.
        """
        if impact:
            rows = await self._db.fetch_all(
                "SELECT * FROM economic_calendar WHERE impact = ? ORDER BY event_time DESC LIMIT ?",
                (impact, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM economic_calendar ORDER BY event_time DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]


def _row_to_article(row: dict) -> NewsArticle:
    """Convert a database row to a NewsArticle dataclass."""
    symbols = row.get("symbols", "[]")
    if isinstance(symbols, str):
        try:
            symbols = json.loads(symbols)
        except (json.JSONDecodeError, TypeError):
            symbols = []

    return NewsArticle(
        id=row["id"],
        headline=row["headline"],
        source=row.get("source", ""),
        url=row.get("url", ""),
        summary=row.get("summary", ""),
        sentiment_score=row.get("sentiment_score", 0.0),
        symbols=symbols,
        category=row.get("category", ""),
        published_at=datetime.fromisoformat(row["published_at"]) if row.get("published_at") else now_utc(),
        fetched_at=datetime.fromisoformat(row["fetched_at"]) if row.get("fetched_at") else now_utc(),
    )
