"""Strategy I4: Hourly Candle Close Momentum — Trade consecutive strong closes."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class HourlyCloseMomentum(BaseStrategy):

    @property
    def name(self) -> str: return "I4_hourly_close"
    @property
    def category(self) -> str: return "time_based"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 540

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 5:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")
        if rsi is None:
            return None

        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        price = ticker.last_price if ticker else c3.close

        def close_position(c):
            rng = c.high - c.low
            if rng <= 0:
                return 0.5
            return (c.close - c.low) / rng

        cp1, cp2, cp3 = close_position(c1), close_position(c2), close_position(c3)

        # LONG: 3 candles closing in top 25%
        if all(cp > 0.75 for cp in [cp1, cp2, cp3]):
            if not (c1.close < c2.close < c3.close):
                return None  # Each higher
            if not (c1.volume < c2.volume < c3.volume):
                return None  # Volume increasing
            if not (55 <= rsi <= 75):
                return None
            if macd_hist is not None and macd_hist <= 0:
                return None

            sl = min(c.low for c in [c1, c2, c3])
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=sl * 0.998, suggested_take_profit=price * 1.02,
                timeframe=self.timeframe.value,
                conditions_met={"three_top_closes": [cp1, cp2, cp3], "ascending_closes": True, "volume_increasing": True, "rsi_momentum": rsi, "macd_positive": macd_hist},
                conditions_strength={"close_quality": min(cp3, 1.0), "momentum": min((rsi - 55) / 20, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: 3 candles closing in bottom 25%
        if all(cp < 0.25 for cp in [cp1, cp2, cp3]):
            if not (c1.close > c2.close > c3.close):
                return None
            if not (c1.volume < c2.volume < c3.volume):
                return None
            if not (25 <= rsi <= 45):
                return None
            if macd_hist is not None and macd_hist >= 0:
                return None

            sl = max(c.high for c in [c1, c2, c3])
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=sl * 1.002, suggested_take_profit=price * 0.98,
                timeframe=self.timeframe.value,
                conditions_met={"three_bottom_closes": [cp1, cp2, cp3], "descending_closes": True, "volume_increasing": True, "rsi_momentum": rsi, "macd_negative": macd_hist},
                conditions_strength={"close_quality": min(1 - cp3, 1.0), "momentum": min((45 - rsi) / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not candles:
            return ("NEUTRAL", 0.3, "No candle data")
        c = candles[-1]
        rng = c.high - c.low
        if rng <= 0:
            return ("NEUTRAL", 0.3, "No range")
        cp = (c.close - c.low) / rng
        if direction == Side.BUY and cp > 0.75:
            return ("BUY", 0.6, f"Candle closed in top 25% ({cp:.0%})")
        if direction == Side.SELL and cp < 0.25:
            return ("SELL", 0.6, f"Candle closed in bottom 25% ({cp:.0%})")
        return ("NEUTRAL", 0.3, f"Candle close mid-range ({cp:.0%})")
