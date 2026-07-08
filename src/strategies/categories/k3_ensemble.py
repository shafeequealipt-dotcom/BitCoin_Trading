"""Strategy K3: Multi-Strategy Ensemble — Placeholder. Logic in src/strategies/ensemble.py."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.strategies.base_strategy import BaseStrategy
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class MultiStrategyEnsemble(BaseStrategy):
    """K3: The ensemble voting system. Actual logic lives in ensemble.py.
    This strategy doesn't generate signals or vote — it IS the voting system."""

    @property
    def name(self) -> str: return "K3_ensemble"
    @property
    def category(self) -> str: return "ai_enhanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "low"

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        return None  # Ensemble logic is in ensemble.py

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        return ("NEUTRAL", 0.0, "K3 does not vote — it IS the voting system")
