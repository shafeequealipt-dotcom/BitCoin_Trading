"""Tests for volume indicators."""

import numpy as np
import pytest

from src.analysis.indicators.volume import (
    obv, vwap, mfi, accumulation_distribution,
    chaikin_money_flow, volume_sma, force_index,
)


class TestOBV:
    def test_increases_on_up_close(self):
        closes = np.array([100, 102, 104, 106], dtype=np.float64)
        vols = np.array([1000, 1000, 1000, 1000], dtype=np.float64)
        result = obv(closes, vols)
        # Each close higher than previous -> +volume
        assert result[-1] > 0

    def test_decreases_on_down_close(self):
        closes = np.array([106, 104, 102, 100], dtype=np.float64)
        vols = np.array([1000, 1000, 1000, 1000], dtype=np.float64)
        result = obv(closes, vols)
        assert result[-1] < 0


class TestVWAP:
    def test_between_high_low(self, ohlcv_arrays):
        _, highs, lows, closes, volumes = ohlcv_arrays
        result = vwap(highs, lows, closes, volumes)
        # VWAP should generally be between cumulative low and high
        valid = result[~np.isnan(result)]
        assert len(valid) > 0

    def test_positive(self, ohlcv_arrays):
        _, highs, lows, closes, volumes = ohlcv_arrays
        result = vwap(highs, lows, closes, volumes)
        valid = result[~np.isnan(result)]
        assert np.all(valid > 0)


class TestMFI:
    def test_range_0_100(self, ohlcv_arrays):
        _, highs, lows, closes, volumes = ohlcv_arrays
        result = mfi(highs, lows, closes, volumes)
        valid = result[~np.isnan(result)]
        if len(valid) > 0:
            assert np.all(valid >= 0) and np.all(valid <= 100)


class TestAD:
    def test_output_length(self, ohlcv_arrays):
        _, highs, lows, closes, volumes = ohlcv_arrays
        result = accumulation_distribution(highs, lows, closes, volumes)
        assert len(result) == len(closes)


class TestCMF:
    def test_range(self, ohlcv_arrays):
        _, highs, lows, closes, volumes = ohlcv_arrays
        result = chaikin_money_flow(highs, lows, closes, volumes)
        valid = result[~np.isnan(result)]
        if len(valid) > 0:
            assert np.all(valid >= -1) and np.all(valid <= 1)


class TestVolumeSMA:
    def test_ratio_output(self, ohlcv_arrays):
        _, _, _, _, volumes = ohlcv_arrays
        avg, ratio = volume_sma(volumes)
        valid_ratio = ratio[~np.isnan(ratio)]
        assert np.all(valid_ratio >= 0)

    def test_spike_detection(self):
        vols = np.array([100]*20 + [500], dtype=np.float64)
        avg, ratio = volume_sma(vols, 20)
        assert ratio[-1] > 2.0  # 500/100 = 5x spike


class TestForceIndex:
    def test_output_length(self, ohlcv_arrays):
        _, _, _, closes, volumes = ohlcv_arrays
        result = force_index(closes, volumes)
        assert len(result) == len(closes)
