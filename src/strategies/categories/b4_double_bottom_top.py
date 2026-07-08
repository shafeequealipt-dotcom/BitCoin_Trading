"""Strategy B4: Double Bottom/Top — Pattern-based reversal with divergence confirmation."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import has_bearish_pattern, has_bullish_pattern, safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class DoubleBottomTop(BaseStrategy):

    @property
    def name(self) -> str: return "B4_double_bottom_top"
    @property
    def category(self) -> str: return "momentum"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.RANGING]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 480

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < self.min_candles:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])

        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # Check chart patterns first
        chart_patterns = safe_get(ta_data, "patterns", "chart", default=[])
        has_double_bottom = any(p.get("name") == "double_bottom" and p.get("confidence", 0) > 0.5 for p in chart_patterns)
        has_double_top = any(p.get("name") == "double_top" and p.get("confidence", 0) > 0.5 for p in chart_patterns)

        # LONG: double bottom or proxy near support
        if has_double_bottom or (rsi < 35 and supports and has_bullish_pattern(ta_data)):
            if macd_hist is not None and macd_hist < -0.5:
                return None  # MACD still strongly bearish
            # Near support check
            near_support = False
            closest_support = 0.0
            for s in supports:
                if s > 0 and abs(price - s) / s < 0.01:
                    near_support = True
                    closest_support = s
                    break
            if not near_support and not has_double_bottom:
                return None

            sl = (closest_support * 0.995) if closest_support else price * 0.985
            tp = resistances[0] if resistances else price * 1.03
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=sl, suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"double_bottom": has_double_bottom, "near_support": near_support, "rsi_oversold": rsi, "bullish_pattern": has_bullish_pattern(ta_data), "macd_improving": macd_hist},
                conditions_strength={"double_bottom": 0.9 if has_double_bottom else 0.5, "rsi_oversold": min((35 - rsi) / 15, 1.0) if rsi < 35 else 0.3},
                created_at=now_utc(),
            )

        # SHORT: double top or proxy near resistance
        if has_double_top or (rsi > 65 and resistances and has_bearish_pattern(ta_data)):
            if macd_hist is not None and macd_hist > 0.5:
                return None
            near_resistance = False
            closest_resistance = 0.0
            for r in resistances:
                if r > 0 and abs(r - price) / r < 0.01:
                    near_resistance = True
                    closest_resistance = r
                    break
            if not near_resistance and not has_double_top:
                return None

            sl = (closest_resistance * 1.005) if closest_resistance else price * 1.015
            tp = supports[0] if supports else price * 0.97
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=sl, suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"double_top": has_double_top, "near_resistance": near_resistance, "rsi_overbought": rsi, "bearish_pattern": has_bearish_pattern(ta_data), "macd_declining": macd_hist},
                conditions_strength={"double_top": 0.9 if has_double_top else 0.5, "rsi_overbought": min((rsi - 65) / 15, 1.0) if rsi > 65 else 0.3},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        price = candles[-1].close if candles else 0
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])
        if price <= 0:
            return ("NEUTRAL", 0.3, "Price unavailable")
        if direction == Side.BUY and supports:
            dist = min(abs(price - s) / price for s in supports) if supports else 1
            if dist < 0.015:
                return ("BUY", 0.7, "Near support level")
        if direction == Side.SELL and resistances:
            dist = min(abs(r - price) / price for r in resistances) if resistances else 1
            if dist < 0.015:
                return ("SELL", 0.7, "Near resistance level")
        return ("NEUTRAL", 0.3, "Not near key level")
