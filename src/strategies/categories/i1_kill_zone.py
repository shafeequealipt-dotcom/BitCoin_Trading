"""Strategy I1: Kill Zone Trading — Trade during high-impact session opens."""

from datetime import datetime, timezone

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal

KILL_ZONES = [
    (0, 2, "asian_open"),
    (7, 9, "london_open"),
    (13, 15, "new_york_open"),
    (16, 17, "london_close"),
]


class KillZoneTrading(BaseStrategy):

    @property
    def name(self) -> str: return "I1_kill_zone"
    @property
    def category(self) -> str: return "time_based"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 120

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 5:
            return None

        now = now_utc()
        hour = now.hour
        minute = now.minute

        # Check if in kill zone and in first 30 minutes
        in_zone = False
        zone_name = ""
        for start, end, name in KILL_ZONES:
            if start <= hour < end and minute < 30:
                in_zone = True
                zone_name = name
                break
        if not in_zone:
            return None

        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        if vol_ratio < 1.5 or adx < 15:
            return None

        # Check first candle of kill zone
        c = candles[-1]
        candle_range = c.high - c.low
        if candle_range <= 0:
            return None

        close_position = (c.close - c.low) / candle_range
        price = ticker.last_price if ticker else c.close

        # Bullish opening candle
        if close_position > 0.7 and c.close > c.open:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=c.low * 0.998,
                suggested_take_profit=price * 1.01,
                timeframe=self.timeframe.value,
                conditions_met={"kill_zone": zone_name, "bullish_open": close_position, "volume_confirm": vol_ratio, "adx_active": adx},
                conditions_strength={"zone_strength": 0.7, "candle_quality": close_position, "volume": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )

        # Bearish opening candle
        if close_position < 0.3 and c.close < c.open:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=c.high * 1.002,
                suggested_take_profit=price * 0.99,
                timeframe=self.timeframe.value,
                conditions_met={"kill_zone": zone_name, "bearish_open": 1 - close_position, "volume_confirm": vol_ratio, "adx_active": adx},
                conditions_strength={"zone_strength": 0.7, "candle_quality": 1 - close_position, "volume": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        hour = now_utc().hour
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        in_zone = any(s <= hour < e for s, e, _ in KILL_ZONES)
        if in_zone and vol_ratio > 1.5:
            return (direction.value.upper() if isinstance(direction, Side) else "NEUTRAL",
                    0.5, f"In kill zone (hour={hour})")
        return ("NEUTRAL", 0.2, "Outside kill zone")
