"""Strategy A4: EMA Crossover Momentum — Fast EMA cross with trend confirmation."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class EMACrossoverMomentum(BaseStrategy):

    @property
    def name(self) -> str: return "A4_ema_crossover"
    @property
    def category(self) -> str: return "scalping"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M1
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 10

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        ema_12 = safe_get(ta_data, "trend", "ema_12")
        ema_26 = safe_get(ta_data, "trend", "ema_26")
        sma_50 = safe_get(ta_data, "trend", "sma_50")
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        vwap = safe_get(ta_data, "volume", "vwap")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)

        if ema_12 is None or ema_26 is None or sma_50 is None or rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # LONG
        if ema_12 > ema_26 and ema_12 > sma_50 and ema_26 > sma_50:
            if not (50 <= rsi <= 70):
                return None
            if vol_ratio < 1.5:
                return None
            if vwap and price < vwap:
                return None
            if adx < 20:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.998,
                suggested_take_profit=price * 1.003,
                timeframe=self.timeframe.value,
                conditions_met={"ema_cross_bull": True, "above_sma50": True, "rsi_momentum": rsi, "volume_confirm": vol_ratio, "adx_trend": adx},
                conditions_strength={"ema_cross_bull": 0.8, "rsi_momentum": min((rsi - 50) / 20, 1.0), "volume_confirm": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )

        # SHORT
        if ema_12 < ema_26 and ema_12 < sma_50 and ema_26 < sma_50:
            if not (30 <= rsi <= 50):
                return None
            if vol_ratio < 1.5:
                return None
            if vwap and price > vwap:
                return None
            if adx < 20:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.002,
                suggested_take_profit=price * 0.997,
                timeframe=self.timeframe.value,
                conditions_met={"ema_cross_bear": True, "below_sma50": True, "rsi_momentum": rsi, "volume_confirm": vol_ratio, "adx_trend": adx},
                conditions_strength={"ema_cross_bear": 0.8, "rsi_momentum": min((50 - rsi) / 20, 1.0), "volume_confirm": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        ema_12 = safe_get(ta_data, "trend", "ema_12")
        ema_26 = safe_get(ta_data, "trend", "ema_26")
        if ema_12 is None or ema_26 is None:
            return ("NEUTRAL", 0.3, "EMA data unavailable")
        if direction == Side.BUY and ema_12 > ema_26:
            return ("BUY", 0.65, "EMA 12 > EMA 26 (bullish)")
        if direction == Side.SELL and ema_12 < ema_26:
            return ("SELL", 0.65, "EMA 12 < EMA 26 (bearish)")
        return ("NEUTRAL", 0.3, "EMA alignment unclear")
