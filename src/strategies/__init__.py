"""Aggressive Multi-Strategy Trading Infrastructure.

Provides the 4-layer decision architecture:
  Layer 1: Strategy Scanner (raw signals)
  Layer 2: Trade Scorer (scored setups)
  Layer 3: Ensemble Voter (consensus filter)
  Layer 4: Claude Brain v2 (final decision)
"""

from src.strategies.base_strategy import BaseStrategy
from src.strategies.registry import StrategyRegistry
from src.strategies.scanner import MarketScanner
from src.strategies.regime import RegimeDetector
from src.strategies.scorer import TradeScorer
from src.strategies.ensemble import EnsembleVoter
from src.strategies.pnl_manager import DailyPnLManager
from src.strategies.smart_leverage import SmartLeverage
from src.strategies.optimizer import WeeklyOptimizer

__all__ = [
    "BaseStrategy", "StrategyRegistry", "MarketScanner", "RegimeDetector",
    "TradeScorer", "EnsembleVoter", "DailyPnLManager", "SmartLeverage",
    "WeeklyOptimizer",
]
