"""Strategy F2: Multi-Timeframe Alignment — Enter when all TF indicators align."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class MultiTFAlignment(BaseStrategy):

    @property
    def name(self) -> str: return "F2_multi_tf_alignment"
    @property
    def category(self) -> str: return "advanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 720

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        trend = safe_get(ta_data, "trend", "trend_summary", default="NEUTRAL")
        sma_50 = safe_get(ta_data, "trend", "sma_50")
        ema_12 = safe_get(ta_data, "trend", "ema_12")
        macd_line = safe_get(ta_data, "trend", "macd", "macd_line")
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")
        st_dir = safe_get(ta_data, "trend", "supertrend", "direction")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])

        if sma_50 is None or ema_12 is None or rsi is None:
            return None
        price = ticker.last_price if ticker else candles[-1].close

        # LONG: all indicators aligned bullish
        if trend == "BULLISH" and price > sma_50 and st_dir == 1:
            if macd_line is None or macd_hist is None or macd_line <= 0 or macd_hist <= 0:
                return None
            if adx < 25:
                return None
            if not (40 <= rsi <= 55):
                return None  # Pullback entry
            if ema_12 and abs(price - ema_12) / ema_12 > 0.003:
                return None  # Not near EMA pullback
            if vol_ratio < 1.0:
                return None

            sl = supports[0] if supports else price * 0.985
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=sl,
                suggested_take_profit=price * 1.03,
                timeframe=self.timeframe.value,
                conditions_met={"trend_bullish": True, "above_sma50": True, "supertrend_bull": True, "macd_positive": True, "adx_strong": adx, "rsi_pullback": rsi, "near_ema": True, "volume_ok": vol_ratio},
                conditions_strength={"trend_alignment": 0.9, "adx_strong": min(adx / 40, 1.0), "rsi_pullback": min((55 - rsi) / 15, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: all indicators aligned bearish
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])
        if trend == "BEARISH" and price < sma_50 and st_dir == -1:
            if macd_line is None or macd_hist is None or macd_line >= 0 or macd_hist >= 0:
                return None
            if adx < 25:
                return None
            if not (45 <= rsi <= 60):
                return None
            if ema_12 and abs(price - ema_12) / ema_12 > 0.003:
                return None
            if vol_ratio < 1.0:
                return None

            sl = resistances[0] if resistances else price * 1.015
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=sl,
                suggested_take_profit=price * 0.97,
                timeframe=self.timeframe.value,
                conditions_met={"trend_bearish": True, "below_sma50": True, "supertrend_bear": True, "macd_negative": True, "adx_strong": adx, "rsi_rally": rsi, "near_ema": True, "volume_ok": vol_ratio},
                conditions_strength={"trend_alignment": 0.9, "adx_strong": min(adx / 40, 1.0), "rsi_rally": min((rsi - 45) / 15, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        trend = safe_get(ta_data, "trend", "trend_summary", default="NEUTRAL")
        st_dir = safe_get(ta_data, "trend", "supertrend", "direction")
        if direction == Side.BUY and trend == "BULLISH" and st_dir == 1:
            return ("BUY", 0.85, "Full trend + Supertrend alignment (bullish)")
        if direction == Side.SELL and trend == "BEARISH" and st_dir == -1:
            return ("SELL", 0.85, "Full trend + Supertrend alignment (bearish)")
        if trend == "NEUTRAL":
            return ("NEUTRAL", 0.3, "Trend unclear")
        return ("NEUTRAL", 0.3, "Trend/Supertrend disagree")
