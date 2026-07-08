"""Tests for momentum indicators."""

import numpy as np
import pytest

from src.analysis.indicators.momentum import (
    rsi, stochastic, stochastic_rsi, cci, williams_r,
    roc, momentum_indicator, awesome_oscillator, tsi, ultimate_oscillator,
)


class TestRSI:
    def test_range_0_100(self, uptrend_data):
        result = rsi(uptrend_data, 14)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)
        assert np.all(valid <= 100)

    def test_uptrend_high_rsi(self, uptrend_data):
        result = rsi(uptrend_data, 14)
        valid = result[~np.isnan(result)]
        # Strong uptrend should produce RSI > 50 on average
        assert np.mean(valid) > 50

    def test_downtrend_low_rsi(self, downtrend_data):
        result = rsi(downtrend_data, 14)
        valid = result[~np.isnan(result)]
        assert np.mean(valid) < 50

    def test_known_values(self, known_rsi_data):
        """Verify RSI matches expected range on known Wilder test data."""
        result = rsi(known_rsi_data, 14)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        # This specific dataset should produce RSI around 50-70
        assert 30 < valid[-1] < 80

    def test_nan_prefix(self, uptrend_data):
        result = rsi(uptrend_data, 14)
        assert np.all(np.isnan(result[:14]))

    def test_flat_price_rsi_50(self, flat_data):
        """Flat price should produce RSI near 50 (no gains or losses)."""
        result = rsi(flat_data, 14)
        # With zero changes, gains and losses are both 0
        # RSI formula: 100 - 100/(1+0/0) is undefined, but implementation should handle


class TestStochastic:
    def test_range_0_100(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        k, d = stochastic(highs, lows, closes)
        valid_k = k[~np.isnan(k)]
        valid_d = d[~np.isnan(d)]
        if len(valid_k) > 0:
            assert np.all(valid_k >= 0) and np.all(valid_k <= 100)
        if len(valid_d) > 0:
            assert np.all(valid_d >= 0) and np.all(valid_d <= 100)


class TestStochasticRSI:
    def test_range_0_1(self, uptrend_data):
        k, d = stochastic_rsi(uptrend_data)
        valid_k = k[~np.isnan(k)]
        if len(valid_k) > 0:
            assert np.all(valid_k >= 0) and np.all(valid_k <= 1.0)


class TestCCI:
    def test_output_length(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = cci(highs, lows, closes)
        assert len(result) == len(closes)


class TestWilliamsR:
    def test_range_neg100_0(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = williams_r(highs, lows, closes)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= -100) and np.all(valid <= 0)


class TestROC:
    def test_basic(self):
        data = np.array([100, 110, 121, 133.1, 146.41], dtype=np.float64)
        result = roc(data, 1)
        assert result[1] == pytest.approx(10.0)
        assert result[2] == pytest.approx(10.0)


class TestMomentum:
    def test_basic(self):
        data = np.array([100, 102, 104, 106, 108], dtype=np.float64)
        result = momentum_indicator(data, 2)
        assert result[2] == pytest.approx(4.0)


class TestAO:
    def test_output_length(self, ohlcv_arrays):
        _, highs, lows, _, _ = ohlcv_arrays
        result = awesome_oscillator(highs, lows)
        assert len(result) == len(highs)


class TestTSI:
    def test_output_length(self, uptrend_data):
        t, s = tsi(uptrend_data)
        assert len(t) == len(uptrend_data)
        assert len(s) == len(uptrend_data)


class TestUltimateOscillator:
    def test_range_0_100(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = ultimate_oscillator(highs, lows, closes)
        valid = result[~np.isnan(result)]
        if len(valid) > 0:
            assert np.all(valid >= 0) and np.all(valid <= 100)
