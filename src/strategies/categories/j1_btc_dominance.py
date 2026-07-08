"""Strategy J1: BTC Dominance Rotation — Trade BTC vs alts rotation."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class BTCDominanceRotation(BaseStrategy):

    @property
    def name(self) -> str: return "J1_btc_dominance"
    @property
    def category(self) -> str: return "cross_market"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.D1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 4320

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        change = ticker.change_24h_pct if ticker else 0
        btc_dom = (altdata or {}).get("btc_dominance", 50)

        # BTC dominance rising: LONG BTC
        if symbol == "BTCUSDT" and btc_dom > 55 and change > 1 and rsi > 50 and rsi < 70:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.97, suggested_take_profit=price * 1.05,
                timeframe=self.timeframe.value,
                conditions_met={"btc_dom_rising": btc_dom, "btc_outperforming": change, "rsi_bullish": rsi},
                conditions_strength={"dominance": min((btc_dom - 50) / 20, 1.0), "momentum": min(change / 5, 1.0)},
                created_at=now_utc(),
            )

        # BTC dominance falling: LONG alts
        if symbol != "BTCUSDT" and btc_dom < 45 and change > 2 and rsi > 50 and rsi < 70:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.97, suggested_take_profit=price * 1.06,
                timeframe=self.timeframe.value,
                conditions_met={"btc_dom_falling": btc_dom, "alt_outperforming": change, "rsi_bullish": rsi},
                conditions_strength={"dominance": min((50 - btc_dom) / 20, 1.0), "momentum": min(change / 5, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        btc_dom = (altdata or {}).get("btc_dominance", 50)
        if symbol == "BTCUSDT" and direction == Side.BUY and btc_dom > 55:
            return ("BUY", 0.5, f"BTC dominance rising ({btc_dom})")
        if symbol != "BTCUSDT" and direction == Side.BUY and btc_dom < 45:
            return ("BUY", 0.5, f"Alt season ({btc_dom} dominance)")
        return ("NEUTRAL", 0.3, f"BTC dominance neutral ({btc_dom})")
