"""Strategy A2: VWAP Bounce Scalp — Enter on pullback to VWAP in trending market."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import has_bearish_pattern, has_bullish_pattern, safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class VWAPBounceScalp(BaseStrategy):

    @property
    def name(self) -> str: return "A2_vwap_bounce"
    @property
    def category(self) -> str: return "scalping"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 30

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 15:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        vwap = safe_get(ta_data, "volume", "vwap")
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)

        if vwap is None or vwap <= 0 or rsi is None:
            return None

        vwap_dist_pct = abs(price - vwap) / vwap

        # LONG: price near VWAP from above, pulling back
        if vwap_dist_pct < 0.001 and 40 <= rsi <= 50:
            above_count = sum(1 for c in candles[-12:] if c.close > vwap)
            if above_count < 8:
                return None
            if not has_bullish_pattern(ta_data):
                return None
            if vol_ratio > 0.8:
                return None  # Want low volume pullback

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=vwap * 0.9975,
                suggested_take_profit=price * 1.005,
                timeframe=self.timeframe.value,
                conditions_met={"near_vwap": vwap_dist_pct, "rsi_pullback": rsi, "above_trend": above_count, "bullish_pattern": True, "low_vol_pullback": vol_ratio},
                conditions_strength={"near_vwap": max(0, 1 - vwap_dist_pct * 1000), "rsi_pullback": 0.7, "above_trend": min(above_count / 12, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: mirror
        if vwap_dist_pct < 0.001 and 50 <= rsi <= 60:
            below_count = sum(1 for c in candles[-12:] if c.close < vwap)
            if below_count < 8:
                return None
            if not has_bearish_pattern(ta_data):
                return None
            if vol_ratio > 0.8:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=vwap * 1.0025,
                suggested_take_profit=price * 0.995,
                timeframe=self.timeframe.value,
                conditions_met={"near_vwap": vwap_dist_pct, "rsi_rally": rsi, "below_trend": below_count, "bearish_pattern": True, "low_vol_rally": vol_ratio},
                conditions_strength={"near_vwap": max(0, 1 - vwap_dist_pct * 1000), "rsi_rally": 0.7, "below_trend": min(below_count / 12, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        price = candles[-1].close if candles else 0
        vwap = safe_get(ta_data, "volume", "vwap")
        if not vwap or price <= 0:
            return ("NEUTRAL", 0.3, "VWAP unavailable")
        if direction == Side.BUY and price > vwap:
            return ("BUY", 0.6, "Price above VWAP")
        if direction == Side.SELL and price < vwap:
            return ("SELL", 0.6, "Price below VWAP")
        return ("NEUTRAL", 0.3, "Price at VWAP")
