"""Strategy C2: RSI Divergence — Detect price/RSI divergence for reversals."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class RSIDivergence(BaseStrategy):

    @property
    def name(self) -> str: return "C2_rsi_divergence"
    @property
    def category(self) -> str: return "mean_reversion"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_DOWN, MarketRegime.TRENDING_UP, MarketRegime.RANGING]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 360

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram")
        stoch_k = safe_get(ta_data, "momentum", "stochastic", "k")
        stoch_d = safe_get(ta_data, "momentum", "stochastic", "d")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])

        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        recent_low = min(c.low for c in candles[-20:])
        recent_high = max(c.high for c in candles[-20:])

        # LONG: bullish divergence
        if candles[-1].low <= recent_low * 1.002 and rsi < 35 and rsi > 25:
            # RSI not making new low (divergence proxy)
            if vol_ratio > 0.8:
                return None  # Want selling drying up
            if stoch_k is not None and stoch_d is not None:
                if not (stoch_k > stoch_d and stoch_k < 35):
                    return None
            near_support = any(abs(price - s) / price < 0.01 for s in supports) if supports else False
            if not near_support and rsi > 30:
                return None

            tp = resistances[0] if resistances else price * 1.02
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=recent_low * 0.995,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"price_new_low": True, "rsi_higher_low": rsi, "volume_declining": vol_ratio, "stoch_cross": stoch_k is not None, "near_support": near_support},
                conditions_strength={"rsi_divergence": min((35 - rsi) / 15, 1.0), "volume_declining": max(0, 1 - vol_ratio), "near_support": 0.8 if near_support else 0.3},
                created_at=now_utc(),
            )

        # SHORT: bearish divergence
        if candles[-1].high >= recent_high * 0.998 and rsi > 65 and rsi < 75:
            if vol_ratio > 0.8:
                return None
            if stoch_k is not None and stoch_d is not None:
                if not (stoch_k < stoch_d and stoch_k > 65):
                    return None
            near_resistance = any(abs(r - price) / price < 0.01 for r in resistances) if resistances else False
            if not near_resistance and rsi < 70:
                return None

            tp = supports[0] if supports else price * 0.98
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=recent_high * 1.005,
                suggested_take_profit=tp,
                timeframe=self.timeframe.value,
                conditions_met={"price_new_high": True, "rsi_lower_high": rsi, "volume_declining": vol_ratio, "stoch_cross": stoch_k is not None, "near_resistance": near_resistance},
                conditions_strength={"rsi_divergence": min((rsi - 65) / 15, 1.0), "volume_declining": max(0, 1 - vol_ratio), "near_resistance": 0.8 if near_resistance else 0.3},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return ("NEUTRAL", 0.3, "RSI unavailable")
        if direction == Side.BUY and rsi < 35 and rsi > 20:
            return ("BUY", 0.65, f"RSI rising from oversold ({rsi:.0f})")
        if direction == Side.SELL and rsi > 65 and rsi < 80:
            return ("SELL", 0.65, f"RSI falling from overbought ({rsi:.0f})")
        return ("NEUTRAL", 0.3, f"RSI neutral ({rsi:.0f})")
