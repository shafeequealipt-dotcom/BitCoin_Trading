"""Strategy X1: Always Trade — Forces trades on testnet for data generation.
TESTNET ONLY. Generates signals on every coin based on simple RSI+MACD.
DISABLE on mainnet."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class AlwaysTradeStrategy(BaseStrategy):

    @property
    def name(self) -> str: return "X1_always_trade"
    @property
    def category(self) -> str: return "kickstart"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 30

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14", default=50)
        macd_hist = safe_get(ta_data, "trend", "macd", "histogram", default=0)
        price = ticker.last_price if ticker else candles[-1].close

        if rsi < 45 and macd_hist and macd_hist > 0:
            direction = Side.BUY
        elif rsi > 55 and macd_hist and macd_hist < 0:
            direction = Side.SELL
        elif rsi < 40:
            direction = Side.BUY
        elif rsi > 60:
            direction = Side.SELL
        elif candles[-1].close > candles[-1].open:
            direction = Side.BUY
        else:
            direction = Side.SELL

        if direction == Side.BUY:
            sl = price * 0.98
            tp = price * 1.03
        else:
            sl = price * 1.02
            tp = price * 0.97

        return RawSignal(
            strategy_name=self.name, strategy_category=self.category,
            symbol=symbol, direction=direction, entry_price=price,
            suggested_stop_loss=sl, suggested_take_profit=tp,
            timeframe=self.timeframe.value,
            conditions_met={"rsi": rsi, "macd_histogram": macd_hist, "bullish_candle": candles[-1].close > candles[-1].open},
            conditions_strength={"rsi": 0.7, "macd_histogram": 0.6, "candle": 0.5},
            created_at=now_utc(),
        )

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        rsi = safe_get(ta_data, "momentum", "rsi_14", default=50)
        if direction == Side.BUY and rsi < 55:
            return ("BUY", 0.7, "RSI supports upside")
        if direction == Side.SELL and rsi > 45:
            return ("SELL", 0.7, "RSI supports downside")
        return (direction.value.upper() if isinstance(direction, Side) else "BUY", 0.5, "Default agreement")
