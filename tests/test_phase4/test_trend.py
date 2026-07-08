"""Tests for trend indicators."""

import numpy as np
import pytest

from src.analysis.indicators.trend import (
    sma, ema, wma, dema, tema, macd, adx, supertrend,
    ichimoku, parabolic_sar, linear_regression, moving_average_crossover,
)


class TestSMA:
    def test_basic(self):
        data = np.array([1, 2, 3, 4, 5], dtype=np.float64)
        result = sma(data, 3)
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_nan_prefix(self, uptrend_data):
        result = sma(uptrend_data, 20)
        assert np.all(np.isnan(result[:19]))
        assert not np.isnan(result[19])

    def test_too_short(self):
        result = sma(np.array([1.0, 2.0]), 5)
        assert np.all(np.isnan(result))


class TestEMA:
    def test_first_value_is_sma(self):
        data = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=np.float64)
        result = ema(data, 5)
        assert result[4] == pytest.approx(np.mean(data[:5]))

    def test_responds_to_price(self, uptrend_data):
        result = ema(uptrend_data, 20)
        # EMA should follow price direction
        valid = result[~np.isnan(result)]
        assert valid[-1] > valid[0]


class TestMACD:
    def test_output_shapes(self, uptrend_data):
        ml, sl, hist = macd(uptrend_data)
        assert len(ml) == len(uptrend_data)
        assert len(sl) == len(uptrend_data)
        assert len(hist) == len(uptrend_data)

    def test_histogram_is_diff(self, uptrend_data):
        ml, sl, hist = macd(uptrend_data)
        # Where both valid, histogram = macd - signal
        for i in range(len(hist)):
            if not np.isnan(ml[i]) and not np.isnan(sl[i]):
                assert hist[i] == pytest.approx(ml[i] - sl[i], abs=1e-6)

    def test_uptrend_positive_macd(self, uptrend_data):
        ml, sl, hist = macd(uptrend_data)
        valid_macd = ml[~np.isnan(ml)]
        assert valid_macd[-1] > 0  # Uptrend should have positive MACD


class TestADX:
    def test_output_ranges(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        adx_v, pdi, mdi = adx(highs, lows, closes)
        valid_adx = adx_v[~np.isnan(adx_v)]
        if len(valid_adx) > 0:
            assert np.all(valid_adx >= 0)
            assert np.all(valid_adx <= 100)


class TestSupertrend:
    def test_direction_values(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        st, direction = supertrend(highs, lows, closes)
        valid_dir = direction[~np.isnan(direction)]
        assert all(d in (1.0, -1.0) for d in valid_dir)

    def test_uptrend_mostly_bullish(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        st, direction = supertrend(highs, lows, closes)
        valid = direction[~np.isnan(direction)]
        bullish_pct = np.sum(valid > 0) / len(valid)
        assert bullish_pct > 0.5  # Uptrend should be mostly bullish


class TestIchimoku:
    def test_output_keys(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = ichimoku(highs, lows, closes)
        assert "tenkan_sen" in result
        assert "kijun_sen" in result
        assert "senkou_span_a" in result
        assert "senkou_span_b" in result
        assert "chikou_span" in result

    def test_array_lengths(self, ohlcv_arrays):
        _, highs, lows, closes, _ = ohlcv_arrays
        result = ichimoku(highs, lows, closes)
        for key, arr in result.items():
            assert len(arr) == len(closes)


class TestMACrossover:
    def test_values_are_signals(self, uptrend_data):
        result = moving_average_crossover(uptrend_data)
        valid = result[~np.isnan(result)]
        assert all(v in (0.0, 1.0, -1.0) for v in valid)


class TestWMA:
    def test_basic(self):
        data = np.array([1, 2, 3, 4, 5], dtype=np.float64)
        result = wma(data, 3)
        # WMA(3) = (1*1 + 2*2 + 3*3)/(1+2+3) = 14/6 = 2.333
        assert result[2] == pytest.approx(14.0 / 6.0, abs=1e-4)
