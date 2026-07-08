"""Strategy A1: RSI Reversal Scalp — Buy oversold, sell overbought on 5-min chart."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class RSIReversalScalp(BaseStrategy):

    @property
    def name(self) -> str: return "A1_rsi_reversal"
    @property
    def category(self) -> str: return "scalping"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 20

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        bb_lower = safe_get(ta_data, "volatility", "bollinger", "lower")
        bb_upper = safe_get(ta_data, "volatility", "bollinger", "upper")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        stoch_k = safe_get(ta_data, "momentum", "stochastic", "k")
        stoch_d = safe_get(ta_data, "momentum", "stochastic", "d")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        plus_di = safe_get(ta_data, "trend", "adx", "plus_di", default=0)
        minus_di = safe_get(ta_data, "trend", "adx", "minus_di", default=0)

        # LONG conditions
        if rsi < 25 and bb_lower and price <= bb_lower:
            if vol_ratio < 1.5:
                return None
            if stoch_k is None or stoch_d is None or not (stoch_k > stoch_d and stoch_k < 25):
                return None
            if adx > 30 and minus_di > plus_di:
                return None  # Strong downtrend

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY,
                entry_price=price,
                suggested_stop_loss=price * 0.997,
                suggested_take_profit=price * 1.005,
                timeframe=self.timeframe.value,
                conditions_met={"rsi_oversold": rsi, "at_lower_bb": True, "volume_spike": vol_ratio, "stoch_cross": True},
                conditions_strength={"rsi_oversold": min((25 - rsi) / 25, 1.0), "volume_spike": min(vol_ratio / 3, 1.0), "stoch_cross": 0.7},
                created_at=now_utc(),
            )

        # SHORT conditions
        if rsi > 75 and bb_upper and price >= bb_upper:
            if vol_ratio < 1.5:
                return None
            if stoch_k is None or stoch_d is None or not (stoch_k < stoch_d and stoch_k > 75):
                return None
            if adx > 30 and plus_di > minus_di:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL,
                entry_price=price,
                suggested_stop_loss=price * 1.003,
                suggested_take_profit=price * 0.995,
                timeframe=self.timeframe.value,
                conditions_met={"rsi_overbought": rsi, "at_upper_bb": True, "volume_spike": vol_ratio, "stoch_cross": True},
                conditions_strength={"rsi_overbought": min((rsi - 75) / 25, 1.0), "volume_spike": min(vol_ratio / 3, 1.0), "stoch_cross": 0.7},
                created_at=now_utc(),
            )

        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return ("NEUTRAL", 0.3, "RSI unavailable")
        if direction == Side.BUY and rsi < 40:
            return ("BUY", min((40 - rsi) / 40, 1.0), f"RSI oversold at {rsi:.0f}")
        if direction == Side.SELL and rsi > 60:
            return ("SELL", min((rsi - 60) / 40, 1.0), f"RSI overbought at {rsi:.0f}")
        return ("NEUTRAL", 0.3, f"RSI neutral at {rsi:.0f}")
