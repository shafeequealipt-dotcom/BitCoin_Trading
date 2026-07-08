"""Strategy F3: Liquidation Hunt — Trade liquidation cascades in leveraged markets."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class LiquidationHunt(BaseStrategy):

    @property
    def name(self) -> str: return "F3_liquidation_hunt"
    @property
    def category(self) -> str: return "advanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "high"
    @property
    def expected_hold_minutes(self) -> int: return 60

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata or not candles or len(candles) < 5:
            return None

        oi_change = altdata.get("oi_change_24h_pct", 0)
        funding = altdata.get("funding_rate", 0)
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        atr = safe_get(ta_data, "volatility", "atr_14", default=0)

        if rsi is None or oi_change < 5.0:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        # SHORT: long liquidation cascade
        if funding > 0.0003 and vol_ratio >= 2.5 and rsi < 40:
            if candles[-1].close >= candles[-1].open:
                return None  # Need bearish candle
            body = abs(candles[-1].close - candles[-1].open)
            if atr > 0 and body < 1.5 * atr:
                return None  # Need large candle
            if len(candles) >= 3 and candles[-1].close >= candles[-3].close:
                return None  # Price should be declining

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.01,
                suggested_take_profit=price * 0.985,
                timeframe=self.timeframe.value,
                conditions_met={"oi_high": oi_change, "funding_positive": funding, "volume_spike": vol_ratio, "rsi_dropping": rsi, "bearish_candle": True, "large_body": True},
                conditions_strength={"oi_leverage": min(oi_change / 10, 1.0), "funding_extreme": min(abs(funding) / 0.001, 1.0), "volume_spike": min(vol_ratio / 4, 1.0)},
                created_at=now_utc(),
            )

        # LONG: short liquidation cascade
        if funding < -0.0003 and vol_ratio >= 2.5 and rsi > 60:
            if candles[-1].close <= candles[-1].open:
                return None
            body = abs(candles[-1].close - candles[-1].open)
            if atr > 0 and body < 1.5 * atr:
                return None
            if len(candles) >= 3 and candles[-1].close <= candles[-3].close:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.99,
                suggested_take_profit=price * 1.015,
                timeframe=self.timeframe.value,
                conditions_met={"oi_high": oi_change, "funding_negative": funding, "volume_spike": vol_ratio, "rsi_rising": rsi, "bullish_candle": True, "large_body": True},
                conditions_strength={"oi_leverage": min(oi_change / 10, 1.0), "funding_extreme": min(abs(funding) / 0.001, 1.0), "volume_spike": min(vol_ratio / 4, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not altdata:
            return ("NEUTRAL", 0.3, "No alt data")
        oi_change = altdata.get("oi_change_24h_pct", 0)
        funding = altdata.get("funding_rate", 0)
        if oi_change > 5 and abs(funding) > 0.0003:
            if direction == Side.SELL and funding > 0:
                return ("SELL", 0.7, f"High OI + positive funding → long liq risk")
            if direction == Side.BUY and funding < 0:
                return ("BUY", 0.7, f"High OI + negative funding → short liq risk")
        return ("NEUTRAL", 0.3, "No liquidation cascade signals")
