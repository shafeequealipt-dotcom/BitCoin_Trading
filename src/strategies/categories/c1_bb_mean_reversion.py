"""Strategy C1: Bollinger Band Mean Reversion — Buy at lower BB, sell at upper BB in ranging markets."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import has_bearish_pattern, has_bullish_pattern, safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class BBMeanReversion(BaseStrategy):

    @property
    def name(self) -> str: return "C1_bb_mean_reversion"
    @property
    def category(self) -> str: return "mean_reversion"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 120

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        mfi = safe_get(ta_data, "volume", "mfi_14")
        chop = safe_get(ta_data, "volatility", "choppiness_index")
        bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
        bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
        sma_20 = safe_get(ta_data, "trend", "sma_20")
        atr = safe_get(ta_data, "volatility", "atr_14", default=0)

        if rsi is None or chop is None or bb_lower is None:
            return None
        if chop < 45:
            return None  # Trending — mean reversion won't work

        price = ticker.last_price if ticker else candles[-1].close

        # LONG
        if rsi < 25 and price < bb_lower * 0.997:
            if mfi is not None and mfi >= 20:
                return None
            if sma_20 and atr > 0 and abs(price - sma_20) < 1.5 * atr:
                return None  # Not overextended enough
            if not has_bullish_pattern(ta_data):
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price - atr if atr > 0 else price * 0.985,
                suggested_take_profit=sma_20 or price * 1.015,
                timeframe=self.timeframe.value,
                conditions_met={"rsi_oversold": rsi, "below_lower_bb": True, "mfi_oversold": mfi, "choppiness_ranging": chop, "overextended": True, "bullish_reversal": True},
                conditions_strength={"rsi_oversold": min((25 - rsi) / 25, 1.0), "choppiness_ranging": min(chop / 80, 1.0), "bullish_reversal": 0.7},
                created_at=now_utc(),
            )

        # SHORT
        if rsi > 75 and bb_upper and price > bb_upper * 1.003:
            if mfi is not None and mfi <= 80:
                return None
            if sma_20 and atr > 0 and abs(price - sma_20) < 1.5 * atr:
                return None
            if not has_bearish_pattern(ta_data):
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price + atr if atr > 0 else price * 1.015,
                suggested_take_profit=sma_20 or price * 0.985,
                timeframe=self.timeframe.value,
                conditions_met={"rsi_overbought": rsi, "above_upper_bb": True, "mfi_overbought": mfi, "choppiness_ranging": chop, "overextended": True, "bearish_reversal": True},
                conditions_strength={"rsi_overbought": min((rsi - 75) / 25, 1.0), "choppiness_ranging": min(chop / 80, 1.0), "bearish_reversal": 0.7},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        chop = safe_get(ta_data, "volatility", "choppiness_index")
        if chop is None or chop < 45:
            return ("NEUTRAL", 0.2, "Not ranging — mean reversion N/A")
        price = candles[-1].close if candles else 0
        bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
        bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
        if bb_lower and direction == Side.BUY and price <= bb_lower:
            return ("BUY", 0.7, f"At lower BB in ranging market (chop={chop:.0f})")
        if bb_upper and direction == Side.SELL and price >= bb_upper:
            return ("SELL", 0.7, f"At upper BB in ranging market (chop={chop:.0f})")
        return ("NEUTRAL", 0.3, "Not at BB extreme")
