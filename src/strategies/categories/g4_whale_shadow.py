"""Strategy G4: Whale Shadow — Follow unusually large volume candles (whale activity)."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class WhaleShadow(BaseStrategy):

    @property
    def name(self) -> str: return "G4_whale_shadow"
    @property
    def category(self) -> str: return "predatory"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 120

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None

        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        plus_di = safe_get(ta_data, "trend", "adx", "plus_di", default=0)
        minus_di = safe_get(ta_data, "trend", "adx", "minus_di", default=0)
        oi_change = (altdata or {}).get("oi_change_24h_pct", 0)

        avg_vol = sum(c.volume for c in candles[-20:]) / 20 if len(candles) >= 20 else 1
        if avg_vol <= 0:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # Check last 3 candles for whale activity (5x volume)
        for i in range(-3, 0):
            if abs(i) > len(candles):
                continue
            c = candles[i]
            vol_multiple = c.volume / avg_vol if avg_vol > 0 else 0
            if vol_multiple < 5.0:
                continue

            candle_range = c.high - c.low
            if candle_range <= 0:
                continue

            # LONG: bullish whale candle
            close_position = (c.close - c.low) / candle_range
            if close_position > 0.7 and c.close > c.open:
                if oi_change <= 0:
                    continue  # Want new positions opened, not just closing
                if adx > 30 and minus_di > plus_di:
                    continue  # Fighting strong downtrend
                if price < c.close:
                    continue  # No follow-through

                return RawSignal(
                    strategy_name=self.name, strategy_category=self.category,
                    symbol=symbol, direction=Side.BUY, entry_price=price,
                    suggested_stop_loss=c.low * 0.998,
                    suggested_take_profit=price * 1.015,
                    timeframe=self.timeframe.value,
                    conditions_met={"whale_volume": vol_multiple, "bullish_close": close_position, "oi_increasing": oi_change, "follow_through": True},
                    conditions_strength={"whale_volume": min(vol_multiple / 10, 1.0), "close_quality": close_position, "follow_through": 0.7},
                    created_at=now_utc(),
                )

            # SHORT: bearish whale candle
            if close_position < 0.3 and c.close < c.open:
                if oi_change <= 0:
                    continue
                if adx > 30 and plus_di > minus_di:
                    continue
                if price > c.close:
                    continue

                return RawSignal(
                    strategy_name=self.name, strategy_category=self.category,
                    symbol=symbol, direction=Side.SELL, entry_price=price,
                    suggested_stop_loss=c.high * 1.002,
                    suggested_take_profit=price * 0.985,
                    timeframe=self.timeframe.value,
                    conditions_met={"whale_volume": vol_multiple, "bearish_close": 1 - close_position, "oi_increasing": oi_change, "follow_through": True},
                    conditions_strength={"whale_volume": min(vol_multiple / 10, 1.0), "close_quality": 1 - close_position, "follow_through": 0.7},
                    created_at=now_utc(),
                )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not candles or len(candles) < 10:
            return ("NEUTRAL", 0.3, "Insufficient data")
        avg_vol = sum(c.volume for c in candles[-20:]) / min(len(candles), 20)
        for c in candles[-5:]:
            if avg_vol > 0 and c.volume / avg_vol > 5:
                if direction == Side.BUY and c.close > c.open:
                    return ("BUY", 0.75, f"Whale buy detected ({c.volume/avg_vol:.0f}x vol)")
                if direction == Side.SELL and c.close < c.open:
                    return ("SELL", 0.75, f"Whale sell detected ({c.volume/avg_vol:.0f}x vol)")
        return ("NEUTRAL", 0.3, "No whale activity")
