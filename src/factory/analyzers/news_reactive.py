"""News Reactive Analyzer: maps how prices react to different news sentiment levels."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class NewsReactiveAnalyzer:
    """Finds patterns in how prices react to news sentiment.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(self, days: int = 30) -> list[DiscoveredPattern]:
        """Analyze news → price reaction patterns across all symbols."""
        patterns: list[DiscoveredPattern] = []

        # Get news articles with sentiment
        articles = await self._db.fetch_all(
            "SELECT * FROM news_articles "
            "WHERE published_at > datetime('now', ? || ' days') "
            "ORDER BY published_at ASC",
            (f"-{days}",),
        )

        if not articles or len(articles) < 10:
            return patterns

        # For each sentiment zone, track price reactions
        zones = [
            ("strong_positive", 0.7, 1.0),
            ("moderate_positive", 0.3, 0.7),
            ("strong_negative", -1.0, -0.7),
            ("moderate_negative", -0.7, -0.3),
        ]

        for zone_name, zone_low, zone_high in zones:
            matching = [a for a in articles
                        if zone_low <= float(a.get("sentiment_score", 0)) < zone_high]

            if len(matching) < 5:
                continue

            # Check price movement after each article
            up_count = 0
            down_count = 0

            for article in matching:
                symbols_str = article.get("symbols", "[]")
                try:
                    import json
                    symbols = json.loads(symbols_str) if isinstance(symbols_str, str) else []
                except (json.JSONDecodeError, TypeError):
                    symbols = []

                if not symbols:
                    continue

                for sym in symbols[:1]:  # Check first mentioned symbol
                    # Get kline after article publication
                    pub_time = article.get("published_at", "")
                    price_rows = await self._db.fetch_all(
                        "SELECT close FROM klines WHERE symbol = ? AND timeframe = '60' "
                        "AND timestamp > ? ORDER BY timestamp ASC LIMIT 2",
                        (sym, pub_time),
                    )
                    if len(price_rows) >= 2:
                        p1 = float(price_rows[0]["close"])
                        p2 = float(price_rows[1]["close"])
                        if p1 > 0:
                            change = ((p2 - p1) / p1) * 100
                            if change > 0.1:
                                up_count += 1
                            elif change < -0.1:
                                down_count += 1

            total = up_count + down_count
            if total >= 5:
                direction = "long" if zone_low >= 0 else "short"
                expected_move = up_count if zone_low >= 0 else down_count
                wr = expected_move / total if total > 0 else 0

                if wr > 0.6:
                    patterns.append(DiscoveredPattern(
                        id=generate_id("pat"),
                        pattern_type="news_reactive",
                        description=f"{zone_name} news sentiment → price follows {wr:.0%} of the time",
                        conditions={"sentiment_range": [zone_low, zone_high]},
                        symbols=[], timeframe="60", direction=direction,
                        occurrences=total, wins=expected_move,
                        losses=total - expected_move, win_rate=wr,
                        discovered_at=now_utc(),
                    ))

        log.info("NewsReactive: analyzed {n} articles, found {p} patterns",
                 n=len(articles), p=len(patterns))
        return patterns
