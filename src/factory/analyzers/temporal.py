"""Temporal Analyzer: finds time-based patterns (hour, day, session, funding)."""

from datetime import datetime

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class TemporalAnalyzer:
    """Finds time-based patterns that recur predictably.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(self, symbol: str, days: int = 30) -> list[DiscoveredPattern]:
        """Analyze hour-of-day, day-of-week, and session-based patterns."""
        patterns: list[DiscoveredPattern] = []

        rows = await self._db.fetch_all(
            "SELECT * FROM klines WHERE symbol = ? AND timeframe = '60' "
            "AND timestamp > datetime('now', ? || ' days') ORDER BY timestamp ASC",
            (symbol, f"-{days}"),
        )
        if not rows or len(rows) < 100:
            return patterns

        # Hour-of-day analysis
        hour_stats: dict[int, dict] = {}
        closes = [float(r["close"]) for r in rows]

        for i in range(len(rows) - 1):
            ts = rows[i]["timestamp"]
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
                hour = dt.hour
            except (ValueError, AttributeError):
                continue

            fwd_return = ((closes[i + 1] - closes[i]) / closes[i]) * 100 if closes[i] > 0 else 0

            if hour not in hour_stats:
                hour_stats[hour] = {"up": 0, "down": 0, "total": 0, "sum_return": 0}

            hour_stats[hour]["total"] += 1
            hour_stats[hour]["sum_return"] += fwd_return
            if fwd_return > 0.05:
                hour_stats[hour]["up"] += 1
            elif fwd_return < -0.05:
                hour_stats[hour]["down"] += 1

        # Find hours with significant directional bias
        for hour, stats in hour_stats.items():
            total = stats["up"] + stats["down"]
            if total < 15:
                continue

            up_rate = stats["up"] / total
            avg_return = stats["sum_return"] / stats["total"] if stats["total"] > 0 else 0

            if up_rate > 0.65:
                patterns.append(DiscoveredPattern(
                    id=generate_id("pat"),
                    pattern_type="temporal",
                    description=f"{symbol} hour {hour}:00 UTC has {up_rate:.0%} bullish bias (avg +{avg_return:.3f}%)",
                    conditions={"hour_utc": hour, "direction_bias": "bullish"},
                    symbols=[symbol], timeframe="60", direction="long",
                    occurrences=stats["total"], wins=stats["up"], losses=stats["down"],
                    win_rate=up_rate, avg_profit_pct=max(avg_return, 0),
                    discovered_at=now_utc(),
                ))
            elif up_rate < 0.35:
                patterns.append(DiscoveredPattern(
                    id=generate_id("pat"),
                    pattern_type="temporal",
                    description=f"{symbol} hour {hour}:00 UTC has {1-up_rate:.0%} bearish bias (avg {avg_return:.3f}%)",
                    conditions={"hour_utc": hour, "direction_bias": "bearish"},
                    symbols=[symbol], timeframe="60", direction="short",
                    occurrences=stats["total"], wins=stats["down"], losses=stats["up"],
                    win_rate=1 - up_rate, avg_profit_pct=max(-avg_return, 0),
                    discovered_at=now_utc(),
                ))

        log.info("Temporal: {sym} found {n} patterns", sym=symbol, n=len(patterns))
        return patterns
