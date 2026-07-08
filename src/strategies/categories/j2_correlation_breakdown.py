"""Strategy J2: Correlation Breakdown — Trade when asset diverges from BTC."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class CorrelationBreakdown(BaseStrategy):

    @property
    def name(self) -> str: return "J2_correlation"
    @property
    def category(self) -> str: return "cross_market"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 720

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if symbol == "BTCUSDT" or not candles or len(candles) < 20:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        change = ticker.change_24h_pct if ticker else 0
        btc_change = (altdata or {}).get("btc_change_24h_pct", 0)
        news_score = (sentiment_data or {}).get("news_score", 0)

        # Target lagging BTC to upside → LONG to catch up
        if btc_change > 2 and change < 0.5 and rsi < 55:
            if news_score < -0.5:
                return None  # Specific negative news explains divergence
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.98, suggested_take_profit=price * 1.025,
                timeframe=self.timeframe.value,
                conditions_met={"btc_up": btc_change, "target_flat": change, "rsi_room": rsi, "no_negative_news": news_score > -0.5},
                conditions_strength={"divergence": min(abs(btc_change - change) / 5, 1.0), "catch_up_room": min((55 - rsi) / 20, 1.0)},
                created_at=now_utc(),
            )

        # Target lagging BTC to downside → SHORT
        if btc_change < -2 and change > -0.5 and rsi > 45:
            if news_score > 0.5:
                return None
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.02, suggested_take_profit=price * 0.975,
                timeframe=self.timeframe.value,
                conditions_met={"btc_down": btc_change, "target_flat": change, "rsi_room": rsi, "no_positive_news": news_score < 0.5},
                conditions_strength={"divergence": min(abs(btc_change - change) / 5, 1.0), "catch_up_room": min((rsi - 45) / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        btc_change = (altdata or {}).get("btc_change_24h_pct", 0)
        change = (candles[-1].close / candles[-24].close - 1) * 100 if candles and len(candles) > 24 else 0
        divergence = btc_change - change
        if direction == Side.BUY and divergence > 2:
            return ("BUY", 0.6, f"Lagging BTC by {divergence:.1f}%")
        if direction == Side.SELL and divergence < -2:
            return ("SELL", 0.6, f"Leading BTC by {abs(divergence):.1f}%")
        return ("NEUTRAL", 0.3, "No significant divergence")
