"""Sequential Analyzer: finds patterns where event sequences predict outcomes."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class SequentialAnalyzer:
    """Finds patterns where A → B → outcome sequences are predictive.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(self, symbol: str, days: int = 30) -> list[DiscoveredPattern]:
        """Find sequential patterns: event chains that predict price direction."""
        patterns: list[DiscoveredPattern] = []

        rows = await self._db.fetch_all(
            "SELECT * FROM klines WHERE symbol = ? AND timeframe = '5' "
            "AND timestamp > datetime('now', ? || ' days') ORDER BY timestamp ASC",
            (symbol, f"-{days}"),
        )
        if not rows or len(rows) < 100:
            return patterns

        closes = [float(r["close"]) for r in rows]
        highs = [float(r["high"]) for r in rows]
        lows = [float(r["low"]) for r in rows]
        volumes = [float(r.get("volume", 0)) for r in rows]

        # Sequence: 3+ consecutive higher closes → continuation
        consec_up = 0
        consec_down = 0
        consec_total = 0

        for i in range(5, len(closes) - 12):
            if closes[i] > closes[i-1] > closes[i-2] > closes[i-3]:
                consec_total += 1
                fwd = ((closes[i + 12] - closes[i]) / closes[i]) * 100
                if fwd > 0.1:
                    consec_up += 1
                elif fwd < -0.1:
                    consec_down += 1

        if consec_total >= 15 and (consec_up + consec_down) > 0:
            wr = consec_up / (consec_up + consec_down)
            if wr > 0.55:
                patterns.append(DiscoveredPattern(
                    id=generate_id("pat"),
                    pattern_type="sequential",
                    description=f"4 consecutive higher closes on {symbol} → continuation UP {wr:.0%}",
                    conditions={"consecutive_higher_closes": 4},
                    symbols=[symbol], timeframe="5", direction="long",
                    occurrences=consec_total, wins=consec_up, losses=consec_down,
                    win_rate=wr, discovered_at=now_utc(),
                ))

        # Sequence: range contraction → expansion
        contract_up = 0
        contract_down = 0
        contract_total = 0

        for i in range(5, len(closes) - 12):
            # 3 candles with decreasing range
            ranges = [highs[i-j] - lows[i-j] for j in range(3)]
            if not (ranges[0] < ranges[1] < ranges[2]):
                continue
            # Then a candle with range > average
            avg_range = sum(highs[j] - lows[j] for j in range(i-20, i)) / 20 if i > 20 else 0
            current_range = highs[i] - lows[i]
            if avg_range <= 0 or current_range < avg_range * 1.5:
                continue

            contract_total += 1
            is_bullish = closes[i] > float(rows[i]["open"])
            fwd = ((closes[i + 6] - closes[i]) / closes[i]) * 100
            if is_bullish and fwd > 0.1:
                contract_up += 1
            elif not is_bullish and fwd < -0.1:
                contract_down += 1

        if contract_total >= 10 and (contract_up + contract_down) > 0:
            wr = (contract_up + contract_down) / contract_total
            if wr > 0.55:
                patterns.append(DiscoveredPattern(
                    id=generate_id("pat"),
                    pattern_type="sequential",
                    description=f"Range contraction → expansion on {symbol} → direction follows breakout {wr:.0%}",
                    conditions={"range_contracting_candles": 3, "expansion_multiplier": 1.5},
                    symbols=[symbol], timeframe="5", direction="both",
                    occurrences=contract_total, wins=contract_up + contract_down,
                    losses=contract_total - contract_up - contract_down,
                    win_rate=wr, discovered_at=now_utc(),
                ))

        log.info("Sequential: {sym} found {n} patterns", sym=symbol, n=len(patterns))
        return patterns
