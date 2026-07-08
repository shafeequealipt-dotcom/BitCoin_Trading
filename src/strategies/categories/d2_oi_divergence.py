"""Strategy D2: OI Divergence — Trade when price and open interest diverge."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class OIDivergence(BaseStrategy):

    @property
    def name(self) -> str: return "D2_oi_divergence"
    @property
    def category(self) -> str: return "funding_arb"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.RANGING]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 720

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        vol_ratio = safe_get(ta_data, "volume", "volume_sma_ratio", default=1.0)
        if rsi is None:
            return None

        oi_change = altdata.get("oi_change_24h_pct", 0)
        funding = altdata.get("funding_rate", 0)
        price = ticker.last_price if ticker else (candles[-1].close if candles else 0)
        change_24h = ticker.change_24h_pct if ticker else 0

        # SHORT: price up but OI falling (weak rally)
        if change_24h > 1.0 and oi_change < -2.0 and funding > 0.0001 and vol_ratio < 0.8 and rsi > 65:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.01,
                suggested_take_profit=price * 0.99,
                timeframe=self.timeframe.value,
                conditions_met={"price_rising": change_24h, "oi_falling": oi_change, "funding_positive": funding, "volume_declining": vol_ratio, "rsi_high": rsi},
                conditions_strength={"oi_divergence": min(abs(oi_change) / 5, 1.0), "rsi_high": min((rsi - 65) / 20, 1.0)},
                created_at=now_utc(),
            )

        # LONG: price down but OI falling (weak selloff)
        if change_24h < -1.0 and oi_change < -2.0 and funding < -0.0001 and vol_ratio < 0.8 and rsi < 35:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.99,
                suggested_take_profit=price * 1.01,
                timeframe=self.timeframe.value,
                conditions_met={"price_falling": change_24h, "oi_falling": oi_change, "funding_negative": funding, "volume_declining": vol_ratio, "rsi_low": rsi},
                conditions_strength={"oi_divergence": min(abs(oi_change) / 5, 1.0), "rsi_low": min((35 - rsi) / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not altdata:
            return ("NEUTRAL", 0.3, "No alt data")
        oi_change = altdata.get("oi_change_24h_pct", 0)
        change_24h = candles[-1].close / candles[-24].close * 100 - 100 if candles and len(candles) > 24 else 0
        # Divergence: price and OI moving in opposite directions
        if direction == Side.SELL and change_24h > 0 and oi_change < -1:
            return ("SELL", 0.6, f"OI divergence: price up, OI down {oi_change:.1f}%")
        if direction == Side.BUY and change_24h < 0 and oi_change < -1:
            return ("BUY", 0.6, f"OI divergence: price down, OI down {oi_change:.1f}%")
        return ("NEUTRAL", 0.3, "No OI divergence")
