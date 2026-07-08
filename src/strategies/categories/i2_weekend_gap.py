"""Strategy I2: Weekend Gap Exploit — Trade thin-volume weekend stop hunts."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class WeekendGapExploit(BaseStrategy):

    @property
    def name(self) -> str: return "I2_weekend_gap"
    @property
    def category(self) -> str: return "time_based"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 1440

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None

        now = now_utc()
        weekday = now.weekday()
        if weekday not in (5, 6):
            return None  # Only weekends

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])

        if rsi is None or vol_ratio > 0.5:
            return None  # Want thin weekend volume

        price = ticker.last_price if ticker else candles[-1].close

        # LONG: weekend dip to support on thin volume
        if rsi < 35 and supports:
            near_support = any(abs(price - s) / price < 0.01 for s in supports)
            if near_support:
                return RawSignal(
                    strategy_name=self.name, strategy_category=self.category,
                    symbol=symbol, direction=Side.BUY, entry_price=price,
                    suggested_stop_loss=min(supports) * 0.995 if supports else price * 0.985,
                    suggested_take_profit=price * 1.02,
                    timeframe=self.timeframe.value,
                    conditions_met={"weekend": weekday, "thin_volume": vol_ratio, "rsi_oversold": rsi, "near_support": True},
                    conditions_strength={"weekend_thin": max(0, 1 - vol_ratio * 2), "rsi": min((35 - rsi) / 15, 1.0)},
                    created_at=now_utc(),
                )

        # SHORT: weekend rally to resistance on thin volume
        if rsi > 65 and resistances:
            near_resistance = any(abs(r - price) / price < 0.01 for r in resistances)
            if near_resistance:
                return RawSignal(
                    strategy_name=self.name, strategy_category=self.category,
                    symbol=symbol, direction=Side.SELL, entry_price=price,
                    suggested_stop_loss=max(resistances) * 1.005 if resistances else price * 1.015,
                    suggested_take_profit=price * 0.98,
                    timeframe=self.timeframe.value,
                    conditions_met={"weekend": weekday, "thin_volume": vol_ratio, "rsi_overbought": rsi, "near_resistance": True},
                    conditions_strength={"weekend_thin": max(0, 1 - vol_ratio * 2), "rsi": min((rsi - 65) / 15, 1.0)},
                    created_at=now_utc(),
                )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        weekday = now_utc().weekday()
        if weekday not in (5, 6):
            return ("NEUTRAL", 0.2, "Not weekend")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        if vol_ratio < 0.5:
            return (direction.value.upper() if isinstance(direction, Side) else "NEUTRAL",
                    0.5, "Weekend thin volume — S/R likely holds")
        return ("NEUTRAL", 0.3, "Weekend but volume normal")
