"""Strategy E1: Fear & Greed Extreme — Contrarian trade at extreme sentiment levels."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


def _to_numeric(raw, key="value", default=50):
    """Extract a numeric value from raw data (int, float, dict, dataclass, or None)."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        val = raw.get(key, default)
        return float(val) if isinstance(val, (int, float)) else float(default)
    if hasattr(raw, key):
        val = getattr(raw, key, default)
        return float(val) if isinstance(val, (int, float)) else float(default)
    return float(default)


class FearGreedExtreme(BaseStrategy):

    @property
    def name(self) -> str: return "E1_fear_greed"
    @property
    def category(self) -> str: return "sentiment"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 2880

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata or not candles or len(candles) < 50:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        fg = _to_numeric(altdata.get("fear_greed", 50), "value", 50)
        funding = _to_numeric(altdata.get("funding_rate", 0), "value", 0)
        price = ticker.last_price if ticker else candles[-1].close
        recent_high = max(c.high for c in candles[-50:])
        recent_low = min(c.low for c in candles[-50:])
        news_score = sentiment_data.get("news_score", 0) if sentiment_data else 0
        sent_score = sentiment_data.get("overall_score", 0) if sentiment_data else 0

        # LONG: buy extreme fear
        if fg <= 15 and rsi < 35 and sent_score < -0.5 and funding < -0.0001:
            drop_pct = ((recent_high - price) / recent_high) * 100 if recent_high > 0 else 0
            if drop_pct < 10:
                return None
            if news_score < -0.8:
                return None  # Fundamental breakdown
            if candles[-1].close <= candles[-1].open:
                return None  # Need first green candle

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.97,
                suggested_take_profit=price * 1.06,
                timeframe=self.timeframe.value,
                conditions_met={"fear_greed_extreme": fg, "rsi_oversold": rsi, "sentiment_bearish": sent_score, "funding_negative": funding, "price_dropped": drop_pct, "green_candle": True},
                conditions_strength={"fear_greed": min((25 - fg) / 25, 1.0), "rsi_oversold": min((35 - rsi) / 20, 1.0), "drop_severity": min(drop_pct / 15, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: sell extreme greed
        if fg >= 85 and rsi > 75 and sent_score > 0.5 and funding > 0.0001:
            rise_pct = ((price - recent_low) / recent_low) * 100 if recent_low > 0 else 0
            if rise_pct < 15:
                return None
            if candles[-1].close >= candles[-1].open:
                return None  # Need first red candle

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.03,
                suggested_take_profit=price * 0.94,
                timeframe=self.timeframe.value,
                conditions_met={"fear_greed_extreme": fg, "rsi_overbought": rsi, "sentiment_bullish": sent_score, "funding_positive": funding, "price_risen": rise_pct, "red_candle": True},
                conditions_strength={"fear_greed": min((fg - 75) / 25, 1.0), "rsi_overbought": min((rsi - 75) / 20, 1.0), "rise_severity": min(rise_pct / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        fg = _to_numeric(altdata.get("fear_greed", 50) if altdata else 50, "value", 50)
        if fg < 20 and direction == Side.BUY:
            return ("BUY", min((25 - fg) / 25, 1.0), f"Extreme fear ({fg})")
        if fg > 80 and direction == Side.SELL:
            return ("SELL", min((fg - 75) / 25, 1.0), f"Extreme greed ({fg})")
        return ("NEUTRAL", 0.3, f"F&G neutral ({fg})")
