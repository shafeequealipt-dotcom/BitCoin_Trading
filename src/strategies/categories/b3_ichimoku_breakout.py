"""Strategy B3: Ichimoku Breakout — Multi-indicator trend confirmation using Ichimoku proxies."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class IchimokuBreakout(BaseStrategy):

    @property
    def name(self) -> str: return "B3_ichimoku"
    @property
    def category(self) -> str: return "momentum"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 2880

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        sma_50 = safe_get(ta_data, "trend", "sma_50")
        sma_200 = safe_get(ta_data, "trend", "sma_200")
        ema_12 = safe_get(ta_data, "trend", "ema_12")
        ema_26 = safe_get(ta_data, "trend", "ema_26")
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        macd_line = safe_get(ta_data, "trend", "macd", "macd_line")
        macd_signal = safe_get(ta_data, "trend", "macd", "signal_line")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        plus_di = safe_get(ta_data, "trend", "adx", "plus_di", default=0)
        minus_di = safe_get(ta_data, "trend", "adx", "minus_di", default=0)
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)

        if sma_50 is None or ema_12 is None or ema_26 is None or rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # LONG (proxy for Ichimoku bullish)
        if price > sma_50 and (sma_200 is None or price > sma_200):
            if ema_12 <= ema_26:
                return None
            if rsi < 50:
                return None
            if macd_line is None or macd_signal is None or macd_line <= macd_signal:
                return None
            if macd_line <= 0:
                return None
            if adx < 25 or plus_di <= minus_di:
                return None
            if vol_ratio < 1.0:
                return None

            sl = sma_200 if sma_200 and sma_200 < price else price * 0.97
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=sl,
                suggested_take_profit=price * 1.04,
                timeframe=self.timeframe.value,
                conditions_met={"above_cloud_proxy": True, "ema_cross_bull": True, "rsi_bullish": rsi, "macd_bullish": True, "adx_trending": adx, "volume_ok": vol_ratio},
                conditions_strength={"above_cloud_proxy": 0.8, "ema_cross_bull": 0.7, "adx_trending": min(adx / 40, 1.0)},
                created_at=now_utc(),
            )

        # SHORT
        if price < sma_50 and (sma_200 is None or price < sma_200):
            if ema_12 >= ema_26:
                return None
            if rsi > 50:
                return None
            if macd_line is None or macd_signal is None or macd_line >= macd_signal:
                return None
            if macd_line >= 0:
                return None
            if adx < 25 or minus_di <= plus_di:
                return None
            if vol_ratio < 1.0:
                return None

            sl = sma_200 if sma_200 and sma_200 > price else price * 1.03
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=sl,
                suggested_take_profit=price * 0.96,
                timeframe=self.timeframe.value,
                conditions_met={"below_cloud_proxy": True, "ema_cross_bear": True, "rsi_bearish": rsi, "macd_bearish": True, "adx_trending": adx, "volume_ok": vol_ratio},
                conditions_strength={"below_cloud_proxy": 0.8, "ema_cross_bear": 0.7, "adx_trending": min(adx / 40, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        sma_50 = safe_get(ta_data, "trend", "sma_50")
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")
        price = candles[-1].close if candles else 0
        if sma_50 is None or price <= 0:
            return ("NEUTRAL", 0.3, "Data unavailable")
        if direction == Side.BUY and price > sma_50 and macd_hist and macd_hist > 0:
            return ("BUY", 0.7, "Above SMA50 + MACD positive")
        if direction == Side.SELL and price < sma_50 and macd_hist and macd_hist < 0:
            return ("SELL", 0.7, "Below SMA50 + MACD negative")
        return ("NEUTRAL", 0.3, "Mixed Ichimoku proxy signals")
