"""Strategy B1: Volume Breakout — Enter on high-volume breakout from consolidation."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class VolumeBreakout(BaseStrategy):

    @property
    def name(self) -> str: return "B1_volume_breakout"
    @property
    def category(self) -> str: return "momentum"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE]
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
        sma_20 = safe_get(ta_data, "trend", "sma_20")
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)

        if bb_bw is None or bb_upper is None or rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # LONG breakout
        if bb_bw < 3 and price > bb_upper and vol_ratio >= 3.0 and rsi > 60:
            if macd_hist is None or macd_hist <= 0:
                return None
            if adx < 20:
                return None

            consolidation_range = bb_upper - (bb_lower or bb_upper * 0.97)
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=sma_20 or price * 0.985,
                suggested_take_profit=price + consolidation_range,
                timeframe=self.timeframe.value,
                conditions_met={"consolidation": bb_bw, "breakout_above": True, "volume_3x": vol_ratio, "rsi_momentum": rsi, "macd_positive": macd_hist, "adx_rising": adx},
                conditions_strength={"volume_3x": min(vol_ratio / 5, 1.0), "rsi_momentum": min((rsi - 60) / 20, 1.0), "breakout": 0.8},
                created_at=now_utc(),
            )

        # SHORT breakout
        if bb_bw < 3 and bb_lower and price < bb_lower and vol_ratio >= 3.0 and rsi < 40:
            if macd_hist is None or macd_hist >= 0:
                return None
            if adx < 20:
                return None

            consolidation_range = (bb_upper or price * 1.03) - bb_lower
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=sma_20 or price * 1.015,
                suggested_take_profit=price - consolidation_range,
                timeframe=self.timeframe.value,
                conditions_met={"consolidation": bb_bw, "breakout_below": True, "volume_3x": vol_ratio, "rsi_momentum": rsi, "macd_negative": macd_hist, "adx_rising": adx},
                conditions_strength={"volume_3x": min(vol_ratio / 5, 1.0), "rsi_momentum": min((40 - rsi) / 20, 1.0), "breakout": 0.8},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        trend = safe_get(ta_data, "trend", "trend_summary", default="NEUTRAL")
        if vol_ratio > 1.5:
            if direction == Side.BUY and trend == "BULLISH":
                return ("BUY", 0.7, f"Volume {vol_ratio:.1f}x + bullish trend")
            if direction == Side.SELL and trend == "BEARISH":
                return ("SELL", 0.7, f"Volume {vol_ratio:.1f}x + bearish trend")
        return ("NEUTRAL", 0.3, "No volume breakout confirmation")
