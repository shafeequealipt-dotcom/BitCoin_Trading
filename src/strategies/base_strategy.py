"""Abstract base class for all trading strategies.

Every strategy (A1-K4) inherits from this and implements scan() and vote().
The Scanner, Scorer, and Ensemble use this interface to interact with strategies
uniformly.
"""

from abc import ABC, abstractmethod

import numpy as np
from numpy.typing import NDArray

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    Every strategy must implement:
    - name: unique identifier (e.g., "A1_rsi_reversal")
    - category: group (e.g., "scalping", "momentum")
    - applicable_regimes: which market regimes this strategy works in
    - timeframe: primary timeframe for analysis
    - scan(): check if entry conditions are met -> RawSignal or None
    - vote(): given a setup from another strategy, agree/disagree
    """

    # --- METADATA (override in subclass) ---

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier, e.g., 'A1_rsi_reversal'."""
        ...

    @property
    @abstractmethod
    def category(self) -> str:
        """Strategy category: 'scalping', 'momentum', 'mean_reversion',
        'funding_arb', 'sentiment', 'advanced', 'predatory',
        'microstructure', 'time_based', 'cross_market', 'ai_enhanced'."""
        ...

    @property
    @abstractmethod
    def applicable_regimes(self) -> list[MarketRegime]:
        """Which market regimes this strategy is active in."""
        ...

    @property
    @abstractmethod
    def timeframe(self) -> TimeFrame:
        """Primary analysis timeframe."""
        ...

    @property
    def min_candles(self) -> int:
        """Minimum candles needed for analysis. Override if different."""
        return 50

    @property
    def risk_level(self) -> str:
        """'low', 'medium', 'high'. Used by PnL manager for filtering."""
        return "medium"

    @property
    def expected_hold_minutes(self) -> int:
        """Expected hold time in minutes. Used for time stops."""
        return 60

    # --- CORE METHODS (implement in subclass) ---

    @abstractmethod
    async def scan(
        self,
        symbol: str,
        candles: list[OHLCV],
        ticker: Ticker,
        ta_data: dict,
        sentiment_data: dict | None,
        altdata: dict | None,
    ) -> RawSignal | None:
        """Scan for entry conditions on a specific symbol.

        Returns RawSignal if ALL entry conditions are met, None otherwise.
        This method must be FAST -- runs every 60 seconds on every coin.
        Do NOT make API calls here -- use only pre-computed data passed in.
        """
        ...

    @abstractmethod
    def vote(
        self,
        symbol: str,
        direction: Side,
        candles: list[OHLCV],
        ta_data: dict,
        sentiment_data: dict | None,
        altdata: dict | None,
    ) -> tuple[str, float, str]:
        """Vote on another strategy's setup.

        Returns: (vote, confidence, reasoning)
        - vote: "BUY", "SELL", or "NEUTRAL"
        - confidence: 0.0 to 1.0
        - reasoning: brief explanation

        Called by the Ensemble Voter (Layer 3).
        """
        ...

    # --- HELPER METHODS (available to all strategies) ---

    @staticmethod
    def _is_bullish_candle(o: float, c: float) -> bool:
        return c > o

    @staticmethod
    def _is_bearish_candle(o: float, c: float) -> bool:
        return c < o

    @staticmethod
    def _body_size(o: float, c: float) -> float:
        return abs(c - o)

    @staticmethod
    def _upper_shadow(o: float, h: float, c: float) -> float:
        return h - max(o, c)

    @staticmethod
    def _lower_shadow(o: float, l: float, c: float) -> float:
        return min(o, c) - l

    @staticmethod
    def _pct_change(old: float, new: float) -> float:
        if old == 0:
            return 0.0
        return ((new - old) / old) * 100

    @staticmethod
    def _get_last_n(arr: NDArray, n: int) -> NDArray:
        """Get last N values from array, handling short arrays."""
        return arr[-n:] if len(arr) >= n else arr
