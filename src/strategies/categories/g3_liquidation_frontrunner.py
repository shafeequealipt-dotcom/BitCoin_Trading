"""Strategy G3: Liquidation Cascade Frontrunner — Front-run liquidation cascades."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class LiquidationFrontrunner(BaseStrategy):

    @property
    def name(self) -> str: return "G3_liq_frontrunner"
    @property
    def category(self) -> str: return "predatory"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.VOLATILE]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "high"
    @property
    def expected_hold_minutes(self) -> int: return 30

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata or not candles or len(candles) < 5:
            return None

        oi_change = altdata.get("oi_change_24h_pct", 0)
        funding = altdata.get("funding_rate", 0)
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=0)
        atr = safe_get(ta_data, "volatility", "atr_14", default=0)

        if rsi is None or oi_change < 8.0 or vol_ratio < 2.0:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        body = abs(candles[-1].close - candles[-1].open)

        # SHORT: front-run long liquidation
        if funding > 0.0004 and rsi < 50:
            if len(candles) >= 3 and candles[-1].close >= candles[-3].close:
                return None
            if atr > 0 and body < atr:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.01, suggested_take_profit=price * 0.985,
                timeframe=self.timeframe.value,
                conditions_met={"oi_extreme": oi_change, "funding_crowded_long": funding, "rsi_dropping": rsi, "volume_cascade": vol_ratio, "large_bearish_candle": body},
                conditions_strength={"oi": min(oi_change / 15, 1.0), "funding": min(abs(funding) / 0.001, 1.0), "volume": min(vol_ratio / 4, 1.0)},
                created_at=now_utc(),
            )

        # LONG: front-run short squeeze
        if funding < -0.0004 and rsi > 50:
            if len(candles) >= 3 and candles[-1].close <= candles[-3].close:
                return None
            if atr > 0 and body < atr:
                return None

            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.99, suggested_take_profit=price * 1.015,
                timeframe=self.timeframe.value,
                conditions_met={"oi_extreme": oi_change, "funding_crowded_short": funding, "rsi_rising": rsi, "volume_cascade": vol_ratio, "large_bullish_candle": body},
                conditions_strength={"oi": min(oi_change / 15, 1.0), "funding": min(abs(funding) / 0.001, 1.0), "volume": min(vol_ratio / 4, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not altdata:
            return ("NEUTRAL", 0.3, "No alt data")
        oi = altdata.get("oi_change_24h_pct", 0)
        funding = altdata.get("funding_rate", 0)
        if oi > 5 and funding > 0.0003 and direction == Side.SELL:
            return ("SELL", 0.7, "High OI + positive funding = liq risk for longs")
        if oi > 5 and funding < -0.0003 and direction == Side.BUY:
            return ("BUY", 0.7, "High OI + negative funding = liq risk for shorts")
        return ("NEUTRAL", 0.3, "No cascade conditions")
