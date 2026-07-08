"""Strategy H3: Volatility Regime Switch — Trade breakout from ultra-tight squeeze."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class VolatilitySwitch(BaseStrategy):

    @property
    def name(self) -> str: return "H3_vol_switch"
    @property
    def category(self) -> str: return "microstructure"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING, MarketRegime.DEAD]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 240

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        bb_bw = safe_get(ta_data, "volatility", "bollinger", "bandwidth")
        bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
        bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
        bb_middle = safe_get(ta_data, "volatility", "bollinger", "middle")
        kc_upper = safe_get(ta_data, "volatility", "keltner", "upper")
        kc_lower = safe_get(ta_data, "volatility", "keltner", "lower")
        natr = safe_get(ta_data, "volatility", "natr_14", default=1.0)
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)

        if bb_bw is None or bb_upper is None or bb_lower is None:
            return None
        if bb_bw >= 1.5:
            return None  # Need ultra-tight squeeze

        # Keltner inside Bollinger confirmation
        if kc_upper and kc_lower:
            if not (kc_upper < bb_upper and kc_lower > bb_lower):
                return None

        if natr >= 0.5:
            return None  # ATR too high, not a true squeeze

        price = ticker.last_price if ticker else candles[-1].close

        # Detect breakout direction
        if price > bb_upper and vol_ratio >= 1.5:
            tp = price + 2 * (bb_upper - bb_lower)
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=bb_middle or price * 0.99,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"ultra_squeeze": bb_bw, "kc_inside_bb": True, "low_atr": natr, "breakout_up": True, "volume_confirm": vol_ratio},
                conditions_strength={"squeeze_tightness": min((1.5 - bb_bw) / 1.5, 1.0), "volume": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )

        if price < bb_lower and vol_ratio >= 1.5:
            tp = price - 2 * (bb_upper - bb_lower)
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=bb_middle or price * 1.01,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"ultra_squeeze": bb_bw, "kc_inside_bb": True, "low_atr": natr, "breakout_down": True, "volume_confirm": vol_ratio},
                conditions_strength={"squeeze_tightness": min((1.5 - bb_bw) / 1.5, 1.0), "volume": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        bb_bw = safe_get(ta_data, "volatility", "bollinger", "bandwidth")
        if bb_bw is None:
            return ("NEUTRAL", 0.3, "BB data unavailable")
        if bb_bw < 2:
            price = candles[-1].close if candles else 0
            bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
            bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
            if bb_upper and direction == Side.BUY and price > bb_upper:
                return ("BUY", 0.7, f"Vol squeeze breakout up (bw={bb_bw:.1f})")
            if bb_lower and direction == Side.SELL and price < bb_lower:
                return ("SELL", 0.7, f"Vol squeeze breakout down (bw={bb_bw:.1f})")
        return ("NEUTRAL", 0.3, "No squeeze condition")
