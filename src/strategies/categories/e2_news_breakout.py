"""Strategy E2: News Breakout — Trade strong news-driven moves with volume confirmation."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class NewsBreakout(BaseStrategy):

    @property
    def name(self) -> str: return "E2_news_breakout"
    @property
    def category(self) -> str: return "sentiment"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "high"
    @property
    def expected_hold_minutes(self) -> int: return 120

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not sentiment_data or not candles or len(candles) < 5:
            return None

        news_score = sentiment_data.get("news_score", 0)
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        price = ticker.last_price if ticker else candles[-1].close

        if abs(news_score) < 0.7:
            return None
        if vol_ratio < 3.0:
            return None

        # Check price moved in news direction
        prev_price = candles[-2].close if len(candles) >= 2 else price
        price_change_pct = ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0

        # LONG: positive news + price up + volume spike
        if news_score > 0.7 and price_change_pct > 0.5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.995,
                suggested_take_profit=price * 1.01,
                timeframe=self.timeframe.value,
                conditions_met={"news_positive": news_score, "price_confirms": price_change_pct, "volume_spike": vol_ratio},
                conditions_strength={"news_strength": min(abs(news_score), 1.0), "volume_spike": min(vol_ratio / 5, 1.0), "price_move": min(price_change_pct / 2, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: negative news + price down + volume spike
        if news_score < -0.7 and price_change_pct < -0.5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.005,
                suggested_take_profit=price * 0.99,
                timeframe=self.timeframe.value,
                conditions_met={"news_negative": news_score, "price_confirms": price_change_pct, "volume_spike": vol_ratio},
                conditions_strength={"news_strength": min(abs(news_score), 1.0), "volume_spike": min(vol_ratio / 5, 1.0), "price_move": min(abs(price_change_pct) / 2, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not sentiment_data:
            return ("NEUTRAL", 0.3, "No sentiment data")
        news_score = sentiment_data.get("news_score", 0)
        if direction == Side.BUY and news_score > 0.5:
            return ("BUY", min(abs(news_score), 1.0), f"Positive news ({news_score:.2f})")
        if direction == Side.SELL and news_score < -0.5:
            return ("SELL", min(abs(news_score), 1.0), f"Negative news ({news_score:.2f})")
        return ("NEUTRAL", 0.3, "No significant news")
