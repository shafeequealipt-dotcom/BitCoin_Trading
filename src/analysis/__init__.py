"""Technical Analysis Engine: indicators, patterns, and market assessment."""

from src.analysis.engine import TAEngine
from src.analysis.patterns.candlestick import CandlestickDetector
from src.analysis.patterns.chart_patterns import ChartPatternDetector

__all__ = ["TAEngine", "CandlestickDetector", "ChartPatternDetector"]
