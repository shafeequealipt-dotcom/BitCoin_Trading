"""Strategy J3: Cross-Exchange Price Lag — Arbitrage last_price vs mark_price."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class CrossExchangeLag(BaseStrategy):

    @property
    def name(self) -> str: return "J3_price_lag"
    @property
    def category(self) -> str: return "cross_market"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M1
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 5
    @property
    def min_candles(self) -> int: return 10

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not ticker or not candles or len(candles) < self.min_candles:
            return None

        price = ticker.last_price
        mark = getattr(ticker, 'bid', 0)  # Proxy: use bid as mark reference
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        natr = safe_get(ta_data, "volatility", "natr_14", default=1.0)

        # Use spread as proxy for price discrepancy
        if ticker.bid <= 0 or ticker.ask <= 0:
            return None

        spread_pct = (ticker.ask - ticker.bid) / ticker.bid * 100
        mid = (ticker.bid + ticker.ask) / 2

        deviation = abs(price - mid) / mid * 100 if mid > 0 else 0
        if deviation < 0.2 or vol_ratio < 1.5:
            return None

        # Don't trade during extreme volatility (divergence may widen)
        if natr and natr > 3.0:
            return None

        if price > mid * 1.002:
            # Overpriced → SHORT
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.004, suggested_take_profit=mid,
                timeframe=self.timeframe.value,
                conditions_met={"price_above_mid": deviation, "volume_active": vol_ratio, "not_extreme_vol": natr},
                conditions_strength={"deviation": min(deviation / 0.5, 1.0), "volume": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )

        if price < mid * 0.998:
            # Underpriced → LONG
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.996, suggested_take_profit=mid,
                timeframe=self.timeframe.value,
                conditions_met={"price_below_mid": deviation, "volume_active": vol_ratio, "not_extreme_vol": natr},
                conditions_strength={"deviation": min(deviation / 0.5, 1.0), "volume": min(vol_ratio / 3, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        return ("NEUTRAL", 0.3, "Price lag is pure arb — no directional vote")
