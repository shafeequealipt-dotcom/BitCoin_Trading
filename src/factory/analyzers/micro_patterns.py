"""Micro Pattern Analyzer: finds tiny high-frequency patterns from 1-min data."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class MicroPatternAnalyzer:
    """Finds tiny, high-frequency patterns that individually are small but compound.

    Uses 1-minute kline data for maximum granularity. Targets 0.2-0.5% profit
    occurring 10-30 times per day.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(self, symbol: str, days: int = 7) -> list[DiscoveredPattern]:
        """Find micro-patterns from 1-minute data."""
        patterns: list[DiscoveredPattern] = []

        rows = await self._db.fetch_all(
            "SELECT * FROM klines WHERE symbol = ? AND timeframe = '1' "
            "AND timestamp > datetime('now', ? || ' days') ORDER BY timestamp ASC",
            (symbol, f"-{days}"),
        )
        if not rows or len(rows) < 200:
            return patterns

        closes = [float(r["close"]) for r in rows]
        opens = [float(r["open"]) for r in rows]
        highs = [float(r["high"]) for r in rows]
        lows = [float(r["low"]) for r in rows]
        volumes = [float(r.get("volume", 0)) for r in rows]

        # Micro-pattern 1: Doji cluster → breakout
        # 3+ tiny-body candles → direction of next large candle
        doji_up = 0
        doji_down = 0
        doji_total = 0

        for i in range(5, len(closes) - 5):
            # Check for 3 consecutive tiny-body candles
            tiny_count = 0
            for j in range(i - 3, i):
                body = abs(closes[j] - opens[j])
                full_range = highs[j] - lows[j]
                if full_range > 0 and body / full_range < 0.3:
                    tiny_count += 1
            if tiny_count < 3:
                continue

            # Check next candle for breakout
            next_body = abs(closes[i] - opens[i])
            next_range = highs[i] - lows[i]
            avg_body = sum(abs(closes[j] - opens[j]) for j in range(i - 10, i)) / 10
            if avg_body <= 0 or next_body < avg_body * 1.5:
                continue

            doji_total += 1
            fwd = ((closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[i]) * 100
            is_bull_breakout = closes[i] > opens[i]
            if is_bull_breakout and fwd > 0.05:
                doji_up += 1
            elif not is_bull_breakout and fwd < -0.05:
                doji_down += 1

        if doji_total >= 20 and (doji_up + doji_down) > 0:
            wr = (doji_up + doji_down) / doji_total
            if wr > 0.55:
                patterns.append(DiscoveredPattern(
                    id=generate_id("pat"),
                    pattern_type="micro",
                    description=f"Doji cluster → breakout on {symbol} 1-min continues {wr:.0%}",
                    conditions={"doji_cluster_candles": 3, "breakout_body_multiplier": 1.5},
                    symbols=[symbol], timeframe="1", direction="both",
                    occurrences=doji_total, wins=doji_up + doji_down,
                    losses=doji_total - doji_up - doji_down,
                    win_rate=wr, avg_profit_pct=0.15,
                    discovered_at=now_utc(),
                ))

        # Micro-pattern 2: Narrowing range → explosion
        narrow_up = 0
        narrow_down = 0
        narrow_total = 0

        for i in range(5, len(closes) - 5):
            ranges = [highs[i - j] - lows[i - j] for j in range(4)]
            if not all(r > 0 for r in ranges):
                continue
            if not (ranges[0] < ranges[1] < ranges[2] < ranges[3]):
                continue  # Range narrowing

            narrow_total += 1
            fwd = ((closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[i]) * 100
            if fwd > 0.1:
                narrow_up += 1
            elif fwd < -0.1:
                narrow_down += 1

        if narrow_total >= 15:
            # Just track that narrowing predicts explosion (direction TBD by other indicators)
            patterns.append(DiscoveredPattern(
                id=generate_id("pat"),
                pattern_type="micro",
                description=f"4-candle narrowing range on {symbol} 1-min → volatility expansion",
                conditions={"narrowing_candles": 4},
                symbols=[symbol], timeframe="1", direction="both",
                occurrences=narrow_total, wins=narrow_up + narrow_down,
                losses=narrow_total - narrow_up - narrow_down,
                win_rate=(narrow_up + narrow_down) / narrow_total if narrow_total > 0 else 0,
                discovered_at=now_utc(),
            ))

        log.info("Micro: {sym} analyzed {n} 1-min candles, found {p} patterns",
                 sym=symbol, n=len(closes), p=len(patterns))
        return patterns
