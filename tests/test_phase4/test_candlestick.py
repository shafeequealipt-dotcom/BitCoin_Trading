"""Tests for candlestick pattern detection."""

import numpy as np
import pytest

from src.analysis.patterns.candlestick import CandlestickDetector


@pytest.fixture
def detector():
    return CandlestickDetector()


class TestHammer:
    def test_detects_hammer(self, detector, hammer_candles):
        opens, highs, lows, closes = hammer_candles
        patterns = detector.detect_all(opens, highs, lows, closes)
        names = [p["name"] for p in patterns]
        assert "hammer" in names

    def test_hammer_is_bullish(self, detector, hammer_candles):
        opens, highs, lows, closes = hammer_candles
        patterns = detector.detect_all(opens, highs, lows, closes)
        hammer = [p for p in patterns if p["name"] == "hammer"]
        if hammer:
            assert hammer[0]["type"] == "bullish"


class TestEngulfing:
    def test_detects_bullish_engulfing(self, detector, engulfing_candles):
        opens, highs, lows, closes = engulfing_candles
        patterns = detector.detect_all(opens, highs, lows, closes)
        names = [p["name"] for p in patterns]
        assert "bullish_engulfing" in names

    def test_engulfing_is_bullish(self, detector, engulfing_candles):
        opens, highs, lows, closes = engulfing_candles
        patterns = detector.detect_all(opens, highs, lows, closes)
        engulf = [p for p in patterns if p["name"] == "bullish_engulfing"]
        if engulf:
            assert engulf[0]["type"] == "bullish"
            assert engulf[0]["confidence"] > 0.5


class TestDoji:
    def test_detects_doji(self, detector):
        opens  = np.array([100, 101, 102, 103, 104.0])
        highs  = np.array([101, 102, 103, 104, 105.0])
        lows   = np.array([99,  100, 101, 102, 103.0])
        closes = np.array([100.5, 101.5, 102.5, 103.5, 104.1])  # Very small body
        patterns = detector.detect_all(opens, highs, lows, closes)
        names = [p["name"] for p in patterns]
        assert "doji" in names


class TestThreeWhiteSoldiers:
    def test_detects_pattern(self, detector):
        opens  = np.array([100, 101, 102])
        highs  = np.array([102, 103, 104])
        lows   = np.array([100, 101, 102])
        closes = np.array([101.8, 102.8, 103.8])
        patterns = detector.detect_all(opens, highs, lows, closes)
        names = [p["name"] for p in patterns]
        assert "three_white_soldiers" in names


class TestThreeBlackCrows:
    def test_detects_pattern(self, detector):
        opens  = np.array([104, 103, 102])
        highs  = np.array([104, 103, 102])
        lows   = np.array([102, 101, 100])
        closes = np.array([102.2, 101.2, 100.2])
        patterns = detector.detect_all(opens, highs, lows, closes)
        names = [p["name"] for p in patterns]
        assert "three_black_crows" in names


class TestNoFalsePositives:
    def test_random_data_few_patterns(self, detector):
        np.random.seed(123)
        n = 100
        opens = np.random.normal(100, 2, n)
        highs = opens + np.abs(np.random.normal(0, 1, n))
        lows = opens - np.abs(np.random.normal(0, 1, n))
        closes = opens + np.random.normal(0, 1, n)
        patterns = detector.detect_all(opens, highs, lows, closes)
        # Random data should produce very few patterns
        assert len(patterns) < 10


class TestConfidence:
    def test_confidence_in_range(self, detector, engulfing_candles):
        opens, highs, lows, closes = engulfing_candles
        patterns = detector.detect_all(opens, highs, lows, closes)
        for p in patterns:
            assert 0 < p["confidence"] <= 1.0
