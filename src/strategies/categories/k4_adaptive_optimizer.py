"""Strategy K4: Adaptive Optimizer — Placeholder. Logic in src/strategies/optimizer.py."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.strategies.base_strategy import BaseStrategy
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class AdaptiveOptimizer(BaseStrategy):
    """K4: Weekly optimization. Actual logic lives in optimizer.py.
    This strategy doesn't trade — it tunes parameters and weights."""

    @property
    def name(self) -> str: return "K4_optimizer"
    @property
    def category(self) -> str: return "ai_enhanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.W1
    @property
    def risk_level(self) -> str: return "low"

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        return None  # Optimizer logic is in optimizer.py

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        return ("NEUTRAL", 0.0, "K4 does not vote — it optimizes other strategies")
