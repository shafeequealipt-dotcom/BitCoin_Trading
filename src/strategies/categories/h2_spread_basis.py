"""Strategy H2: Spread/Basis Exploitation — Trade perp premium/discount to index."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class SpreadBasisExploit(BaseStrategy):

    @property
    def name(self) -> str: return "H2_basis_exploit"
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
    def expected_hold_minutes(self) -> int: return 1440

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        adx = safe_get(ta_data, "trend", "adx", "adx", default=0)
        minus_di = safe_get(ta_data, "trend", "adx", "minus_di", default=0)
        plus_di = safe_get(ta_data, "trend", "adx", "plus_di", default=0)
        if rsi is None:
            return None

        funding = altdata.get("funding_rate", 0)
        price = ticker.last_price if ticker else (candles[-1].close if candles else 0)

        # SHORT: perp at premium (positive funding sustained)
        if funding > 0.0003 and rsi > 30:
            if adx > 30 and plus_di > minus_di:
                return None  # Strong uptrend, premium may persist
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.015, suggested_take_profit=price * 0.995,
                timeframe=self.timeframe.value,
                conditions_met={"funding_premium": funding, "rsi_ok": rsi, "trend_not_strong_bull": adx < 30},
                conditions_strength={"funding": min(abs(funding) / 0.001, 1.0), "basis_edge": 0.6},
                created_at=now_utc(),
            )

        # LONG: perp at discount (negative funding sustained)
        if funding < -0.0003 and rsi < 70:
            if adx > 30 and minus_di > plus_di:
                return None
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.985, suggested_take_profit=price * 1.005,
                timeframe=self.timeframe.value,
                conditions_met={"funding_discount": funding, "rsi_ok": rsi, "trend_not_strong_bear": adx < 30},
                conditions_strength={"funding": min(abs(funding) / 0.001, 1.0), "basis_edge": 0.6},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        funding = (altdata or {}).get("funding_rate", 0)
        if direction == Side.SELL and funding > 0.0002:
            return ("SELL", 0.5, f"Collect positive funding ({funding:.4f})")
        if direction == Side.BUY and funding < -0.0002:
            return ("BUY", 0.5, f"Collect negative funding ({funding:.4f})")
        return ("NEUTRAL", 0.3, "No basis edge")
