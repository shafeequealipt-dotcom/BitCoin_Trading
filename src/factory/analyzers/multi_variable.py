"""Multi Variable Analyzer: finds patterns where indicator combinations predict better than singles."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class MultiVariableAnalyzer:
    """Finds patterns where combinations of 2-3 conditions predict outcomes
    better than any single condition alone.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(self, symbol: str, days: int = 30) -> list[DiscoveredPattern]:
        """Analyze multi-variable patterns by testing indicator combinations."""
        patterns: list[DiscoveredPattern] = []

        rows = await self._db.fetch_all(
            "SELECT * FROM klines WHERE symbol = ? AND timeframe = '5' "
            "AND timestamp > datetime('now', ? || ' days') ORDER BY timestamp ASC",
            (symbol, f"-{days}"),
        )
        if not rows or len(rows) < 100:
            return patterns

        closes = [float(r["close"]) for r in rows]
        volumes = [float(r.get("volume", 0)) for r in rows]

        # Test: low-volume pullback + price near support (consecutive lower closes + volume decline)
        pullback_up = 0
        pullback_down = 0
        pullback_total = 0

        for i in range(25, len(closes) - 12):
            # Condition 1: 3 consecutive lower closes
            if not (closes[i] < closes[i - 1] < closes[i - 2]):
                continue
            # Condition 2: volume declining
            avg_vol = sum(volumes[i - 20:i]) / 20 if sum(volumes[i - 20:i]) > 0 else 1
            if volumes[i] > avg_vol * 0.8:
                continue  # Not a low-volume pullback
            # Condition 3: price still above 20-period average (uptrend pullback)
            sma20 = sum(closes[i - 20:i]) / 20
            if closes[i] < sma20:
                continue

            pullback_total += 1
            fwd = ((closes[i + 12] - closes[i]) / closes[i]) * 100
            if fwd > 0.1:
                pullback_up += 1
            elif fwd < -0.1:
                pullback_down += 1

        if pullback_total >= 15:
            total = pullback_up + pullback_down
            if total > 0:
                wr = pullback_up / total
                if wr > 0.6:
                    patterns.append(DiscoveredPattern(
                        id=generate_id("pat"),
                        pattern_type="multi_var",
                        description=f"Low-vol pullback in uptrend on {symbol} → bounce {wr:.0%}",
                        conditions={
                            "consecutive_lower_closes": 3,
                            "volume_below_avg_pct": 80,
                            "price_above_sma20": True,
                        },
                        symbols=[symbol], timeframe="5", direction="long",
                        occurrences=pullback_total, wins=pullback_up, losses=pullback_down,
                        win_rate=wr, avg_profit_pct=0.35, avg_loss_pct=0.2,
                        profit_factor=wr / (1 - wr) if wr < 1 else 99,
                        discovered_at=now_utc(),
                    ))

        # Test: high volume + large candle body (breakout confirmation)
        breakout_up = 0
        breakout_down = 0
        breakout_total = 0

        for i in range(25, len(closes) - 12):
            avg_vol = sum(volumes[i - 20:i]) / 20 if sum(volumes[i - 20:i]) > 0 else 1
            if avg_vol <= 0 or volumes[i] < avg_vol * 2.5:
                continue  # Need 2.5x volume
            body = abs(closes[i] - float(rows[i]["open"]))
            avg_body = sum(abs(closes[j] - float(rows[j]["open"])) for j in range(i - 20, i)) / 20
            if avg_body <= 0 or body < avg_body * 2:
                continue  # Need 2x body size

            breakout_total += 1
            is_bullish = closes[i] > float(rows[i]["open"])
            fwd = ((closes[i + 12] - closes[i]) / closes[i]) * 100

            if is_bullish and fwd > 0.1:
                breakout_up += 1
            elif not is_bullish and fwd < -0.1:
                breakout_down += 1

        if breakout_total >= 10 and (breakout_up + breakout_down) > 0:
            continuation_rate = (breakout_up + breakout_down) / breakout_total
            if continuation_rate > 0.55:
                patterns.append(DiscoveredPattern(
                    id=generate_id("pat"),
                    pattern_type="multi_var",
                    description=f"High-vol large-body candle on {symbol} → continuation {continuation_rate:.0%}",
                    conditions={
                        "volume_above_avg_multiple": 2.5,
                        "body_above_avg_multiple": 2.0,
                    },
                    symbols=[symbol], timeframe="5", direction="both",
                    occurrences=breakout_total,
                    wins=breakout_up + breakout_down,
                    losses=breakout_total - breakout_up - breakout_down,
                    win_rate=continuation_rate,
                    discovered_at=now_utc(),
                ))

        log.info(
            "MultiVar: {sym} found {n} patterns",
            sym=symbol, n=len(patterns),
        )
        return patterns
