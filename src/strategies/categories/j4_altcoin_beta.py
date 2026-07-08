"""Strategy J4: Altcoin Beta Amplification — Trade lagging alts that will catch up to BTC."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class AltcoinBetaAmplification(BaseStrategy):

    @property
    def name(self) -> str: return "J4_alt_beta"
    @property
    def category(self) -> str: return "cross_market"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 360

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if symbol == "BTCUSDT" or not candles or len(candles) < 20:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        change = ticker.change_24h_pct if ticker else 0
        btc_change = (altdata or {}).get("btc_change_24h_pct", 0)
        news = (sentiment_data or {}).get("news_score", 0)

        # LONG: BTC up, alt hasn't moved yet
        if btc_change > 2 and change < btc_change * 0.3 and rsi < 65 and vol_ratio < 1.5:
            if news < -0.5:
                return None  # Negative news specific to this alt
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.985, suggested_take_profit=price * 1.025,
                timeframe=self.timeframe.value,
                conditions_met={"btc_leading_up": btc_change, "alt_lagging": change, "rsi_room": rsi, "low_vol": vol_ratio, "no_bad_news": news > -0.5},
                conditions_strength={"lag_size": min(abs(btc_change - change) / 5, 1.0), "rsi_room": min((65 - rsi) / 20, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: BTC down, alt hasn't dropped yet
        if btc_change < -2 and change > btc_change * 0.3 and rsi > 35 and vol_ratio < 1.5:
            if news > 0.5:
                return None
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.015, suggested_take_profit=price * 0.975,
                timeframe=self.timeframe.value,
                conditions_met={"btc_leading_down": btc_change, "alt_lagging": change, "rsi_room": rsi, "low_vol": vol_ratio, "no_good_news": news < 0.5},
                conditions_strength={"lag_size": min(abs(btc_change - change) / 5, 1.0), "rsi_room": min((rsi - 35) / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if symbol == "BTCUSDT":
            return ("NEUTRAL", 0.3, "J4 only applies to alts")
        btc_change = (altdata or {}).get("btc_change_24h_pct", 0)
        change = (candles[-1].close / candles[-4].close - 1) * 100 if candles and len(candles) > 4 else 0
        if direction == Side.BUY and btc_change > 1 and change < btc_change * 0.5:
            return ("BUY", 0.6, f"Alt lagging BTC ({change:.1f}% vs {btc_change:.1f}%)")
        if direction == Side.SELL and btc_change < -1 and change > btc_change * 0.5:
            return ("SELL", 0.6, f"Alt lagging BTC drop")
        return ("NEUTRAL", 0.3, "No alt beta opportunity")
