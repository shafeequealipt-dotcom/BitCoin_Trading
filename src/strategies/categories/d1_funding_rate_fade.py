"""Strategy D1: Funding Rate Fade — Contrarian trade when funding rates are extreme."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class FundingRateFade(BaseStrategy):

    @property
    def name(self) -> str: return "D1_funding_fade"
    @property
    def category(self) -> str: return "funding_arb"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H4
    @property
    def risk_level(self) -> str: return "low"
    @property
    def expected_hold_minutes(self) -> int: return 960

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not altdata:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        funding = altdata.get("funding_rate", 0)
        fg = altdata.get("fear_greed", 50)
        price = ticker.last_price if ticker else (candles[-1].close if candles else 0)
        change_24h = ticker.change_24h_pct if ticker else 0

        # SHORT: longs overcrowded
        if funding > 0.0004 and rsi > 70 and fg > 70 and change_24h > 5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.015,
                suggested_take_profit=price * 0.99,
                timeframe=self.timeframe.value,
                conditions_met={"funding_extreme_pos": funding, "rsi_overbought": rsi, "fear_greed_greedy": fg, "price_extended": change_24h},
                conditions_strength={"funding_extreme": min(abs(funding) / 0.001, 1.0), "rsi_overbought": min((rsi - 70) / 20, 1.0), "fear_greed": min((fg - 70) / 30, 1.0)},
                created_at=now_utc(),
            )

        # LONG: shorts overcrowded
        if funding < -0.0004 and rsi < 30 and fg < 30 and change_24h < -5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.985,
                suggested_take_profit=price * 1.01,
                timeframe=self.timeframe.value,
                conditions_met={"funding_extreme_neg": funding, "rsi_oversold": rsi, "fear_greed_fearful": fg, "price_extended": change_24h},
                conditions_strength={"funding_extreme": min(abs(funding) / 0.001, 1.0), "rsi_oversold": min((30 - rsi) / 20, 1.0), "fear_greed": min((30 - fg) / 30, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        if not altdata:
            return ("NEUTRAL", 0.3, "No alt data")
        funding = altdata.get("funding_rate", 0)
        if abs(funding) < 0.0001:
            return ("NEUTRAL", 0.3, "Funding rate normal")
        if funding > 0.0003 and direction == Side.SELL:
            return ("SELL", min(abs(funding) / 0.001, 1.0), f"Funding very positive ({funding:.4f})")
        if funding < -0.0003 and direction == Side.BUY:
            return ("BUY", min(abs(funding) / 0.001, 1.0), f"Funding very negative ({funding:.4f})")
        return ("NEUTRAL", 0.3, f"Funding normal ({funding:.4f})")
