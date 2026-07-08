"""Cross-Asset Analyzer: finds patterns where one coin's behavior predicts another."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class CrossAssetAnalyzer:
    """Finds lead-lag relationships and correlation breakdowns between coins.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(self, symbols: list[str], days: int = 30) -> list[DiscoveredPattern]:
        """Find cross-asset patterns across multiple symbols."""
        patterns: list[DiscoveredPattern] = []

        if len(symbols) < 2 or "BTCUSDT" not in symbols:
            return patterns

        # Get BTC data
        btc_rows = await self._db.fetch_all(
            "SELECT * FROM klines WHERE symbol = 'BTCUSDT' AND timeframe = '60' "
            "AND timestamp > datetime('now', ? || ' days') ORDER BY timestamp ASC",
            (f"-{days}",),
        )
        if not btc_rows or len(btc_rows) < 50:
            return patterns

        btc_closes = [float(r["close"]) for r in btc_rows]

        # For each alt, check lead-lag with BTC
        for alt_symbol in symbols:
            if alt_symbol == "BTCUSDT":
                continue

            alt_rows = await self._db.fetch_all(
                "SELECT * FROM klines WHERE symbol = ? AND timeframe = '60' "
                "AND timestamp > datetime('now', ? || ' days') ORDER BY timestamp ASC",
                (alt_symbol, f"-{days}"),
            )
            if not alt_rows or len(alt_rows) < 50:
                continue

            alt_closes = [float(r["close"]) for r in alt_rows]
            min_len = min(len(btc_closes), len(alt_closes))
            if min_len < 50:
                continue

            # Calculate: when BTC moves >1% in 1h, what does alt do in NEXT hour?
            catchup_up = 0
            catchup_down = 0
            catchup_total = 0

            for i in range(1, min_len - 1):
                btc_move = ((btc_closes[i] - btc_closes[i - 1]) / btc_closes[i - 1]) * 100
                if abs(btc_move) < 1.0:
                    continue

                alt_move_prev = ((alt_closes[i] - alt_closes[i - 1]) / alt_closes[i - 1]) * 100
                alt_move_next = ((alt_closes[min(i + 1, min_len - 1)] - alt_closes[i]) / alt_closes[i]) * 100

                # If BTC moved up but alt didn't follow → does alt catch up?
                if btc_move > 1.0 and alt_move_prev < btc_move * 0.3:
                    catchup_total += 1
                    if alt_move_next > 0.3:
                        catchup_up += 1
                    elif alt_move_next < -0.3:
                        catchup_down += 1

            if catchup_total >= 10 and (catchup_up + catchup_down) > 0:
                wr = catchup_up / (catchup_up + catchup_down)
                if wr > 0.6:
                    patterns.append(DiscoveredPattern(
                        id=generate_id("pat"),
                        pattern_type="cross_asset",
                        description=f"BTC up >1% but {alt_symbol} lagged → {alt_symbol} catches up {wr:.0%}",
                        conditions={"btc_move_pct_above": 1.0, "alt_lag_ratio_below": 0.3},
                        symbols=["BTCUSDT", alt_symbol], timeframe="60", direction="long",
                        occurrences=catchup_total, wins=catchup_up, losses=catchup_down,
                        win_rate=wr, discovered_at=now_utc(),
                    ))

        log.info("CrossAsset: analyzed {n} pairs, found {p} patterns",
                 n=len(symbols) - 1, p=len(patterns))
        return patterns
