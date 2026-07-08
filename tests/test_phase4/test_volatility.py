"""Tests for volatility indicators."""

import numpy as np
import pytest

from src.analysis.indicators.volatility import (
    bollinger_bands, atr, keltner_channels, donchian_channels,
    standard_deviation, historical_volatility, natr, choppiness_index,
)


class TestBollingerBands:
    def test_band_ordering(self, uptrend_data):
        u, m, l, bw = bollinger_bands(uptrend_data)
        for i in range(len(u)):
            if not np.isnan(u[i]):
                assert u[i] >= m[i]
                assert m[i] >= l[i]

    def test_bandwidth_positive(self, uptrend_data):
        u, m, l, bw = bollinger_bands(uptrend_data)
        valid_bw = bw[~np.isnan(bw)]
        assert np.all(valid_bw >= 0)


class TestATR:
    def test_positive(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = atr(highs, lows, closes)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)

    def test_nan_prefix(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = atr(highs, lows, closes, 14)
        assert np.all(np.isnan(result[:14]))


class TestKeltnerChannels:
    def test_uses_ema_middle(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        from src.analysis.indicators.trend import ema
        u, m, l = keltner_channels(highs, lows, closes)
        ema_20 = ema(closes, 20)
        # Middle should match EMA
        for i in range(len(m)):
            if not np.isnan(m[i]) and not np.isnan(ema_20[i]):
                assert m[i] == pytest.approx(ema_20[i], abs=1e-6)

    def test_band_ordering(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        u, m, l = keltner_channels(highs, lows, closes)
        for i in range(len(u)):
            if not np.isnan(u[i]):
                assert u[i] >= m[i]
                assert m[i] >= l[i]


class TestDonchianChannels:
    def test_band_ordering(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        u, m, l = donchian_channels(highs, lows)
        for i in range(len(u)):
            if not np.isnan(u[i]):
                assert u[i] >= m[i]
                assert m[i] >= l[i]


class TestStdDev:
    def test_positive(self, uptrend_data):
        result = standard_deviation(uptrend_data, 20)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)

    def test_flat_is_zero(self, flat_data):
        result = standard_deviation(flat_data, 20)
        valid = result[~np.isnan(result)]
        assert np.all(valid == 0)


class TestHistoricalVolatility:
    def test_positive(self, uptrend_data):
        result = historical_volatility(uptrend_data)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)


class TestNATR:
    def test_positive(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = natr(highs, lows, closes)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)


class TestChoppinessIndex:
    def test_output_range(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = choppiness_index(highs, lows, closes)
        valid = result[~np.isnan(result)]
        if len(valid) > 0:
            assert np.all(valid >= 0) and np.all(valid <= 100)
