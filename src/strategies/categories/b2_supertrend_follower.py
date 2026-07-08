"""Strategy B2: Supertrend Follower — Ride trends confirmed by Supertrend indicator."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class SupertrendFollower(BaseStrategy):

    @property
    def name(self) -> str: return "B2_supertrend"
    @property
    def category(self) -> str: return "momentum"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 480

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        st_dir = safe_get(ta_data, "trend", "supertrend", "direction")
        st_val = safe_get(ta_data, "trend", "supertrend", "value")
        sma_50 = safe_get(ta_data, "trend", "sma_50")
        macd_line = safe_get(ta_data, "trend", "macd", "macd_line")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)

        if st_dir is None or sma_50 is None or rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        atr = safe_get(ta_data, "volatility", "atr_14", default=price * 0.01)

        # LONG
        if st_dir == 1 and price > sma_50:
            if macd_line is None or macd_line <= 0:
                return None
            if adx < 25:
                return None
            if not (50 <= rsi <= 70):
                return None
            if vol_ratio < 1.0:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=st_val or price * 0.98,
                suggested_take_profit=price + 2 * atr,
                timeframe=self.timeframe.value,
                conditions_met={"supertrend_bull": True, "above_sma50": True, "macd_positive": macd_line, "adx_strong": adx, "rsi_room": rsi, "volume_ok": vol_ratio},
                conditions_strength={"supertrend_bull": 0.8, "adx_strong": min(adx / 40, 1.0), "rsi_room": min((70 - rsi) / 20, 1.0)},
                created_at=now_utc(),
            )

        # SHORT
        if st_dir == -1 and price < sma_50:
            if macd_line is None or macd_line >= 0:
                return None
            if adx < 25:
                return None
            if not (30 <= rsi <= 50):
                return None
            if vol_ratio < 1.0:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=st_val or price * 1.02,
                suggested_take_profit=price - 2 * atr,
                timeframe=self.timeframe.value,
                conditions_met={"supertrend_bear": True, "below_sma50": True, "macd_negative": macd_line, "adx_strong": adx, "rsi_room": rsi, "volume_ok": vol_ratio},
                conditions_strength={"supertrend_bear": 0.8, "adx_strong": min(adx / 40, 1.0), "rsi_room": min((rsi - 30) / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        st_dir = safe_get(ta_data, "trend", "supertrend", "direction")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        if st_dir is None:
            return ("NEUTRAL", 0.3, "Supertrend unavailable")
        conf = min(adx / 40, 1.0) if adx > 20 else 0.4
        if direction == Side.BUY and st_dir == 1:
            return ("BUY", conf, f"Supertrend bullish, ADX={adx:.0f}")
        if direction == Side.SELL and st_dir == -1:
            return ("SELL", conf, f"Supertrend bearish, ADX={adx:.0f}")
        return ("NEUTRAL", 0.3, "Supertrend disagrees")
