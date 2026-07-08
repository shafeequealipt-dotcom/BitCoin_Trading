"""Strategy F1: Support/Resistance Bounce — Trade bounces off key S/R levels."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import has_bearish_pattern, has_bullish_pattern, safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class SupportResistanceBounce(BaseStrategy):

    @property
    def name(self) -> str: return "F1_support_resistance"
    @property
    def category(self) -> str: return "advanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 360

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        mfi = safe_get(ta_data, "volume", "mfi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        trend = safe_get(ta_data, "trend", "trend_summary", default="NEUTRAL")
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])

        if rsi is None:
            return None
        price = ticker.last_price if ticker else candles[-1].close

        # LONG: support bounce
        if supports and 30 <= rsi <= 40 and vol_ratio < 0.8:
            closest = min(supports, key=lambda s: abs(price - s))
            dist_pct = abs(price - closest) / closest if closest > 0 else 1
            if dist_pct > 0.005:
                return None
            if not has_bullish_pattern(ta_data):
                return None
            if mfi is not None and mfi < 30:
                pass  # OK
            if trend == "BEARISH":
                return None

            tp = resistances[0] if resistances else price * 1.02
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=closest * 0.995,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"near_support": closest, "rsi_pullback": rsi, "low_volume": vol_ratio, "bullish_pattern": True, "trend_ok": trend},
                conditions_strength={"near_support": max(0, 1 - dist_pct * 200), "rsi_pullback": min((40 - rsi) / 10, 1.0), "low_volume": max(0, 1 - vol_ratio)},
                created_at=now_utc(),
            )

        # SHORT: resistance rejection
        if resistances and 60 <= rsi <= 70 and vol_ratio < 0.8:
            closest = min(resistances, key=lambda r: abs(r - price))
            dist_pct = abs(closest - price) / closest if closest > 0 else 1
            if dist_pct > 0.005:
                return None
            if not has_bearish_pattern(ta_data):
                return None
            if trend == "BULLISH":
                return None

            tp = supports[0] if supports else price * 0.98
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=closest * 1.005,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"near_resistance": closest, "rsi_rally": rsi, "low_volume": vol_ratio, "bearish_pattern": True, "trend_ok": trend},
                conditions_strength={"near_resistance": max(0, 1 - dist_pct * 200), "rsi_rally": min((rsi - 60) / 10, 1.0), "low_volume": max(0, 1 - vol_ratio)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        price = candles[-1].close if candles else 0
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])
        if price <= 0:
            return ("NEUTRAL", 0.3, "No price data")
        if direction == Side.BUY and supports:
            dist = min(abs(price - s) / price for s in supports)
            if dist < 0.01:
                return ("BUY", max(0.4, 1 - dist * 100), "Near support")
        if direction == Side.SELL and resistances:
            dist = min(abs(r - price) / price for r in resistances)
            if dist < 0.01:
                return ("SELL", max(0.4, 1 - dist * 100), "Near resistance")
        return ("NEUTRAL", 0.3, "Not near key level")
