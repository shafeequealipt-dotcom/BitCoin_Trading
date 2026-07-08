"""Strategy H4: Order Flow Imbalance — Detect directional flow from consecutive candles."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class OrderFlowImbalance(BaseStrategy):

    @property
    def name(self) -> str: return "H4_order_flow"
    @property
    def category(self) -> str: return "microstructure"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 10

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 10:
            return None

        fi = safe_get(ta_data, "volume", "force_index")
        cmf = safe_get(ta_data, "volume", "chaikin_money_flow")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)

        if vol_ratio < 2.0:
            return None

        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        price = ticker.last_price if ticker else c3.close

        # LONG: 3 consecutive bullish candles, closing near highs, volume accelerating
        if all(c.close > c.open for c in [c1, c2, c3]):
            if not (c1.volume < c2.volume < c3.volume):
                return None  # Volume must accelerate
            for c in [c2, c3]:
                rng = c.high - c.low
                if rng <= 0 or (c.close - c.low) / rng < 0.8:
                    return None  # Must close near high
            if fi is not None and fi <= 0:
                return None
            if cmf is not None and cmf < 0.1:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.998, suggested_take_profit=price * 1.003,
                timeframe=self.timeframe.value,
                conditions_met={"three_bullish": True, "volume_accelerating": True, "closing_near_highs": True, "force_index_pos": fi, "cmf_pos": cmf, "volume_spike": vol_ratio},
                conditions_strength={"flow_strength": 0.8, "volume": min(vol_ratio / 4, 1.0)},
                created_at=now_utc(),
            )

        # SHORT: 3 consecutive bearish candles
        if all(c.close < c.open for c in [c1, c2, c3]):
            if not (c1.volume < c2.volume < c3.volume):
                return None
            for c in [c2, c3]:
                rng = c.high - c.low
                if rng <= 0 or (c.high - c.close) / rng < 0.8:
                    return None
            if fi is not None and fi >= 0:
                return None
            if cmf is not None and cmf > -0.1:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.002, suggested_take_profit=price * 0.997,
                timeframe=self.timeframe.value,
                conditions_met={"three_bearish": True, "volume_accelerating": True, "closing_near_lows": True, "force_index_neg": fi, "cmf_neg": cmf, "volume_spike": vol_ratio},
                conditions_strength={"flow_strength": 0.8, "volume": min(vol_ratio / 4, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        cmf = safe_get(ta_data, "volume", "chaikin_money_flow")
        fi = safe_get(ta_data, "volume", "force_index")
        if cmf is not None and fi is not None:
            if direction == Side.BUY and cmf > 0.1 and fi > 0:
                return ("BUY", 0.6, f"Positive order flow (CMF={cmf:.2f})")
            if direction == Side.SELL and cmf < -0.1 and fi < 0:
                return ("SELL", 0.6, f"Negative order flow (CMF={cmf:.2f})")
        return ("NEUTRAL", 0.3, "Mixed order flow")
