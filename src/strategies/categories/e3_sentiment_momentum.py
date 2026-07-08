"""Strategy E3: Sentiment Momentum — Trade sentiment shifts confirmed by price and volume."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class SentimentMomentum(BaseStrategy):

    @property
    def name(self) -> str: return "E3_sentiment_momentum"
    @property
    def category(self) -> str: return "sentiment"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 720

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not sentiment_data or not candles or len(candles) < 10:
            return None

        sent_score = sentiment_data.get("overall_score", 0)
        news_score = sentiment_data.get("news_score", 0)
        news_count = sentiment_data.get("news_count", 0)
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vwap = safe_get(ta_data, "volume", "vwap")
        ema_12 = safe_get(ta_data, "trend", "ema_12")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        fg = altdata.get("fear_greed", 50) if altdata else 50

        if rsi is None:
            return None
        price = ticker.last_price if ticker else candles[-1].close

        # LONG: sentiment shifted bullish
        if sent_score > 0.4 and news_score > 0.3 and news_count >= 3:
            if rsi > 70:
                return None
            if vwap and price < vwap:
                return None
            if ema_12 and price < ema_12:
                return None
            if vol_ratio < 1.2:
                return None
            if fg < 30 or fg > 60:
                return None  # Want room to move

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.985,
                suggested_take_profit=price * 1.025,
                timeframe=self.timeframe.value,
                conditions_met={"sentiment_bullish": sent_score, "news_positive": news_score, "news_count": news_count, "rsi_ok": rsi, "above_vwap": True, "volume_rising": vol_ratio, "fg_room": fg},
                conditions_strength={"sentiment": min(sent_score, 1.0), "news": min(news_score, 1.0), "volume": min(vol_ratio / 2, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: sentiment shifted bearish
        if sent_score < -0.4 and news_score < -0.3 and news_count >= 3:
            if rsi < 30:
                return None
            if vwap and price > vwap:
                return None
            if ema_12 and price > ema_12:
                return None
            if vol_ratio < 1.2:
                return None
            if fg < 40 or fg > 70:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.015,
                suggested_take_profit=price * 0.975,
                timeframe=self.timeframe.value,
                conditions_met={"sentiment_bearish": sent_score, "news_negative": news_score, "news_count": news_count, "rsi_ok": rsi, "below_vwap": True, "volume_rising": vol_ratio, "fg_room": fg},
                conditions_strength={"sentiment": min(abs(sent_score), 1.0), "news": min(abs(news_score), 1.0), "volume": min(vol_ratio / 2, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not sentiment_data:
            return ("NEUTRAL", 0.3, "No sentiment data")
        sent = sentiment_data.get("overall_score", 0)
        if direction == Side.BUY and sent > 0.3:
            return ("BUY", min(abs(sent), 1.0), f"Sentiment bullish ({sent:.2f})")
        if direction == Side.SELL and sent < -0.3:
            return ("SELL", min(abs(sent), 1.0), f"Sentiment bearish ({sent:.2f})")
        return ("NEUTRAL", 0.3, f"Sentiment neutral ({sent:.2f})")
