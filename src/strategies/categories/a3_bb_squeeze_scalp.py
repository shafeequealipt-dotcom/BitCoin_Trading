"""Strategy A3: Bollinger Band Squeeze Scalp — Trade breakout from low-volatility squeeze."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class BBSqueezeScalp(BaseStrategy):

    @property
    def name(self) -> str: return "A3_bb_squeeze"
    @property
    def category(self) -> str: return "scalping"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING, MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 15

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        bb_bw = safe_get(ta_data, "volatility", "bollinger", "bandwidth")
        bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
        bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
        bb_middle = safe_get(ta_data, "volatility", "bollinger", "middle")
        kc_upper = safe_get(ta_data, "volatility", "keltner", "upper")
        kc_lower = safe_get(ta_data, "volatility", "keltner", "lower")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")

        if bb_bw is None or bb_upper is None or bb_lower is None:
            return None
        if bb_bw >= 2.0:
            return None  # No squeeze

        price = ticker.last_price if ticker else candles[-1].close

        # Keltner inside Bollinger check (squeeze confirmation)
        squeeze_confirmed = True
        if kc_upper and kc_lower:
            if not (kc_upper < bb_upper and kc_lower > bb_lower):
                squeeze_confirmed = False

        if vol_ratio < 2.0:
            return None  # Need volume on breakout

        # LONG breakout
        if price > bb_upper and macd_hist and macd_hist > 0:
            tp = price + 1.5 * (bb_upper - bb_lower) if bb_lower else price * 1.01
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=bb_middle or price * 0.995,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"bb_squeeze": bb_bw, "breakout_above": True, "volume_spike": vol_ratio, "macd_positive": macd_hist, "kc_squeeze": squeeze_confirmed},
                conditions_strength={"bb_squeeze": min((2.0 - bb_bw) / 2.0, 1.0), "volume_spike": min(vol_ratio / 4, 1.0), "macd_positive": 0.7},
                created_at=now_utc(),
            )

        # SHORT breakout
        if price < bb_lower and macd_hist and macd_hist < 0:
            tp = price - 1.5 * (bb_upper - bb_lower) if bb_upper else price * 0.99
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=bb_middle or price * 1.005,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"bb_squeeze": bb_bw, "breakout_below": True, "volume_spike": vol_ratio, "macd_negative": macd_hist, "kc_squeeze": squeeze_confirmed},
                conditions_strength={"bb_squeeze": min((2.0 - bb_bw) / 2.0, 1.0), "volume_spike": min(vol_ratio / 4, 1.0), "macd_negative": 0.7},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        bb_bw = safe_get(ta_data, "volatility", "bollinger", "bandwidth")
        price = candles[-1].close if candles else 0
        bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
        bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
        if bb_bw is None or not bb_upper or not bb_lower:
            return ("NEUTRAL", 0.3, "BB data unavailable")
        if bb_bw < 3 and direction == Side.BUY and price > bb_upper:
            return ("BUY", 0.7, "BB squeeze breakout up")
        if bb_bw < 3 and direction == Side.SELL and price < bb_lower:
            return ("SELL", 0.7, "BB squeeze breakout down")
        return ("NEUTRAL", 0.3, "No BB squeeze breakout")
