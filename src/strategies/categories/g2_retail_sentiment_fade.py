"""Strategy G2: Retail Sentiment Fade — Contrarian trade against extreme crowd sentiment."""

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


class RetailSentimentFade(BaseStrategy):

    @property
    def name(self) -> str: return "G2_retail_fade"
    @property
    def category(self) -> str: return "predatory"
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
        if not sentiment_data or not altdata:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        sent_raw = sentiment_data.get("overall_score", 0)
        sent = sent_raw if isinstance(sent_raw, (int, float)) else 0
        news_raw = sentiment_data.get("news_score", 0)
        news = news_raw if isinstance(news_raw, (int, float)) else 0
        fg = _to_numeric(altdata.get("fear_greed", 50), "value", 50)
        funding = _to_numeric(altdata.get("funding_rate", 0), "value", 0)
        oi_raw = altdata.get("oi_change_24h_pct", 0)
        oi_change = oi_raw if isinstance(oi_raw, (int, float)) else 0
        price = ticker.last_price if ticker else (candles[-1].close if candles else 0)
        change = ticker.change_24h_pct if ticker else 0

        # SHORT: retail overwhelmingly bullish
        if sent > 0.7 and news > 0.6 and fg > 80 and funding > 0.0005 and rsi > 75 and oi_change > 3 and change > 5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.02, suggested_take_profit=price * 0.97,
                timeframe=self.timeframe.value,
                conditions_met={"sentiment_extreme_bull": sent, "news_bullish": news, "fg_extreme_greed": fg, "funding_crowded": funding, "rsi_overbought": rsi, "oi_leveraged": oi_change, "extended": change},
                conditions_strength={"sentiment": min(sent, 1.0), "fg_extreme": min((fg - 75) / 25, 1.0), "funding": min(abs(funding) / 0.001, 1.0)},
                created_at=now_utc(),
            )

        # LONG: retail overwhelmingly bearish
        if sent < -0.7 and news < -0.6 and fg < 20 and funding < -0.0005 and rsi < 25 and change < -5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.98, suggested_take_profit=price * 1.03,
                timeframe=self.timeframe.value,
                conditions_met={"sentiment_extreme_bear": sent, "news_bearish": news, "fg_extreme_fear": fg, "funding_crowded": funding, "rsi_oversold": rsi, "extended": change},
                conditions_strength={"sentiment": min(abs(sent), 1.0), "fg_extreme": min((25 - fg) / 25, 1.0), "funding": min(abs(funding) / 0.001, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        fg = _to_numeric(altdata.get("fear_greed", 50) if altdata else 50, "value", 50)
        if fg > 75 and direction == Side.SELL:
            return ("SELL", min((fg - 75) / 25, 1.0), f"Extreme greed ({fg}) — fade retail")
        if fg < 25 and direction == Side.BUY:
            return ("BUY", min((25 - fg) / 25, 1.0), f"Extreme fear ({fg}) — fade retail")
        return ("NEUTRAL", 0.3, f"F&G moderate ({fg})")
