"""Strategy H1: Funding Rate Prediction — Position before extreme funding collection."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class FundingPrediction(BaseStrategy):

    @property
    def name(self) -> str: return "H1_funding_predict"
    @property
    def category(self) -> str: return "microstructure"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 600

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        funding = altdata.get("funding_rate", 0)
        predicted = altdata.get("predicted_funding", funding)
        price = ticker.last_price if ticker else (candles[-1].close if candles else 0)
        change = ticker.change_24h_pct if ticker else 0

        # Check if near funding time (every 8 hours: hours 5-7 in each 8h cycle)
        hour = now_utc().hour
        near_funding = (hour % 8) in (5, 6, 7)
        if not near_funding:
            return None

        # SHORT: extreme positive funding about to be collected
        if predicted > 0.0005 and rsi > 60 and change > 0:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.01, suggested_take_profit=price * 0.993,
                timeframe=self.timeframe.value,
                conditions_met={"predicted_funding": predicted, "near_funding_time": True, "rsi_confirms": rsi, "longs_dominant": change > 0},
                conditions_strength={"funding_extreme": min(abs(predicted) / 0.001, 1.0), "timing": 0.8},
                created_at=now_utc(),
            )

        # LONG: extreme negative funding
        if predicted < -0.0005 and rsi < 40 and change < 0:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.99, suggested_take_profit=price * 1.007,
                timeframe=self.timeframe.value,
                conditions_met={"predicted_funding": predicted, "near_funding_time": True, "rsi_confirms": rsi, "shorts_dominant": change < 0},
                conditions_strength={"funding_extreme": min(abs(predicted) / 0.001, 1.0), "timing": 0.8},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        funding = (altdata or {}).get("funding_rate", 0)
        if abs(funding) < 0.0003:
            return ("NEUTRAL", 0.3, "Funding normal")
        if funding > 0.0003 and direction == Side.SELL:
            return ("SELL", min(abs(funding) / 0.001, 0.8), f"Positive funding {funding:.4f}")
        if funding < -0.0003 and direction == Side.BUY:
            return ("BUY", min(abs(funding) / 0.001, 0.8), f"Negative funding {funding:.4f}")
        return ("NEUTRAL", 0.3, "Funding doesn't support direction")
