"""Strategy I3: Options Expiry Play — Trade mean reversion near monthly expiry."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class OptionsExpiryPlay(BaseStrategy):

    @property
    def name(self) -> str: return "I3_options_expiry"
    @property
    def category(self) -> str: return "time_based"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 1440

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None

        now = now_utc()
        day = now.day
        weekday = now.weekday()

        # Approximate last week of month (options expiry)
        if not (day > 22 and weekday < 5):
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # Find nearest round number level
        if price >= 1000:
            round_level = round(price / 1000) * 1000
        elif price >= 100:
            round_level = round(price / 100) * 100
        else:
            round_level = round(price / 10) * 10

        dist_pct = ((price - round_level) / round_level) * 100 if round_level > 0 else 0

        # SHORT: price extended above round number
        if dist_pct > 3.0 and rsi > 65 and vol_ratio <= 1.0:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.02, suggested_take_profit=round_level,
                timeframe=self.timeframe.value,
                conditions_met={"expiry_week": True, "extended_above_round": dist_pct, "round_level": round_level, "rsi_overbought": rsi, "low_volume": vol_ratio},
                conditions_strength={"expiry_proximity": 0.6, "extension": min(dist_pct / 5, 1.0)},
                created_at=now_utc(),
            )

        # LONG: price extended below round number
        if dist_pct < -3.0 and rsi < 35 and vol_ratio <= 1.0:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.98, suggested_take_profit=round_level,
                timeframe=self.timeframe.value,
                conditions_met={"expiry_week": True, "extended_below_round": dist_pct, "round_level": round_level, "rsi_oversold": rsi, "low_volume": vol_ratio},
                conditions_strength={"expiry_proximity": 0.6, "extension": min(abs(dist_pct) / 5, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        now = now_utc()
        if now.day > 22 and now.weekday() < 5:
            return (direction.value.upper() if isinstance(direction, Side) else "NEUTRAL",
                    0.4, "Expiry week — slight mean reversion bias")
        return ("NEUTRAL", 0.2, "Not expiry week")
