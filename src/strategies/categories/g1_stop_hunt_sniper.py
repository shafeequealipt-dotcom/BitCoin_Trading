"""Strategy G1: Stop Hunt Sniper — Trade reversals after stop hunt wicks beyond S/R."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class StopHuntSniper(BaseStrategy):

    @property
    def name(self) -> str: return "G1_stop_hunt"
    @property
    def category(self) -> str: return "predatory"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING, MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 60

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])

        if rsi is None or vol_ratio < 2.0:
            return None

        last = candles[-1]
        price = last.close

        # LONG: bear stop hunt (wick below support, close above)
        for s in supports:
            if s <= 0:
                continue
            pierce = (s - last.low) / price
            if last.low < s and last.close > s and pierce > 0.002:
                if rsi < 35:
                    continue  # Genuinely oversold, not a hunt

                return RawSignal(
                    strategy_name=self.name, strategy_category=self.category,
                    symbol=symbol, direction=Side.BUY, entry_price=price,
                    suggested_stop_loss=last.low * 0.998,
                    suggested_take_profit=price + (price - last.low),
                    timeframe=self.timeframe.value,
                    conditions_met={"wick_below_support": s, "pierce_pct": pierce, "reclaimed": True, "volume_spike": vol_ratio, "rsi_not_oversold": rsi},
                    conditions_strength={"pierce": min(pierce / 0.005, 1.0), "volume": min(vol_ratio / 4, 1.0), "reclaim": 0.8},
                    created_at=now_utc(),
                )

        # SHORT: bull stop hunt (wick above resistance, close below)
        for r in resistances:
            if r <= 0:
                continue
            pierce = (last.high - r) / price
            if last.high > r and last.close < r and pierce > 0.002:
                if rsi > 65:
                    continue

                return RawSignal(
                    strategy_name=self.name, strategy_category=self.category,
                    symbol=symbol, direction=Side.SELL, entry_price=price,
                    suggested_stop_loss=last.high * 1.002,
                    suggested_take_profit=price - (last.high - price),
                    timeframe=self.timeframe.value,
                    conditions_met={"wick_above_resistance": r, "pierce_pct": pierce, "reclaimed": True, "volume_spike": vol_ratio, "rsi_not_overbought": rsi},
                    conditions_strength={"pierce": min(pierce / 0.005, 1.0), "volume": min(vol_ratio / 4, 1.0), "reclaim": 0.8},
                    created_at=now_utc(),
                )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not candles or len(candles) < 3:
            return ("NEUTRAL", 0.3, "Insufficient data")
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])
        last = candles[-1]
        if direction == Side.BUY and supports:
            for s in supports:
                if last.low < s and last.close > s:
                    return ("BUY", 0.7, "Stop hunt wick below support detected")
        if direction == Side.SELL and resistances:
            for r in resistances:
                if last.high > r and last.close < r:
                    return ("SELL", 0.7, "Stop hunt wick above resistance detected")
        return ("NEUTRAL", 0.3, "No stop hunt pattern")
