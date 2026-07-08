"""Momentum/oscillator indicators: RSI, Stochastic, CCI, Williams %R, ROC,
Momentum, Awesome Oscillator, TSI, Ultimate Oscillator, Stochastic RSI.

All functions take numpy arrays and return numpy arrays.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.indicators.trend import ema, sma

FloatArray = NDArray[np.float64]


def rsi(close: FloatArray, period: int = 14) -> FloatArray:
    """Relative Strength Index using Wilder's smoothing.

    Formula: RS = avg_gain / avg_loss (Wilder smoothing)
             RSI = 100 - 100 / (1 + RS)

    Args:
        close: Close prices.
        period: RSI period.

    Returns:
        RSI array (0-100), first period values are NaN.
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1:
        return result

    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        result[period] = 100.0
    else:
        result[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            result[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    return result


def stochastic(high: FloatArray, low: FloatArray, close: FloatArray, k_period: int = 14, d_period: int = 3, slowing: int = 3) -> tuple[FloatArray, FloatArray]:
    """Stochastic Oscillator.

    Formula: %K = 100 * (close - lowest_low(k)) / (highest_high(k) - lowest_low(k))
             Smoothed %K = SMA(raw %K, slowing)
             %D = SMA(%K, d_period)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        k_period: %K lookback period.
        d_period: %D smoothing period.
        slowing: %K smoothing period.

    Returns:
        Tuple of (%K, %D), both 0-100.
    """
    n = len(close)
    raw_k = np.full(n, np.nan, dtype=np.float64)

    for i in range(k_period - 1, n):
        hh = np.max(high[i - k_period + 1:i + 1])
        ll = np.min(low[i - k_period + 1:i + 1])
        if hh - ll > 0:
            raw_k[i] = 100.0 * (close[i] - ll) / (hh - ll)
        else:
            raw_k[i] = 50.0

    k_smooth = sma(raw_k[~np.isnan(raw_k)], slowing) if np.any(~np.isnan(raw_k)) else np.array([])
    k_out = np.full(n, np.nan, dtype=np.float64)
    valid_start = k_period - 1 + slowing - 1
    if valid_start < n and len(k_smooth) > 0:
        valid_k = k_smooth[~np.isnan(k_smooth)]
        end = min(valid_start + len(valid_k), n)
        k_out[valid_start:end] = valid_k[:end - valid_start]

    d_vals = sma(k_out[~np.isnan(k_out)], d_period) if np.any(~np.isnan(k_out)) else np.array([])
    d_out = np.full(n, np.nan, dtype=np.float64)
    d_start = valid_start + d_period - 1
    if d_start < n and len(d_vals) > 0:
        valid_d = d_vals[~np.isnan(d_vals)]
        end = min(d_start + len(valid_d), n)
        d_out[d_start:end] = valid_d[:end - d_start]

    return k_out, d_out


def stochastic_rsi(close: FloatArray, rsi_period: int = 14, stoch_period: int = 14, k_period: int = 3, d_period: int = 3) -> tuple[FloatArray, FloatArray]:
    """Stochastic RSI.

    Formula: Apply stochastic formula to RSI values instead of price.
             StochRSI = (RSI - lowest_RSI) / (highest_RSI - lowest_RSI)

    Args:
        close: Close prices.
        rsi_period: RSI calculation period.
        stoch_period: Stochastic lookback on RSI.
        k_period: %K smoothing.
        d_period: %D smoothing.

    Returns:
        Tuple of (stoch_rsi_k, stoch_rsi_d), both 0-1.
    """
    n = len(close)
    rsi_vals = rsi(close, rsi_period)

    stoch_k = np.full(n, np.nan, dtype=np.float64)
    for i in range(rsi_period + stoch_period - 1, n):
        window = rsi_vals[i - stoch_period + 1:i + 1]
        if np.any(np.isnan(window)):
            continue
        rsi_max = np.max(window)
        rsi_min = np.min(window)
        if rsi_max - rsi_min > 0:
            stoch_k[i] = (rsi_vals[i] - rsi_min) / (rsi_max - rsi_min)
        else:
            stoch_k[i] = 0.5

    # Smooth K and D
    valid_k = stoch_k[~np.isnan(stoch_k)]
    k_smooth = sma(valid_k, k_period) if len(valid_k) >= k_period else np.array([])
    d_smooth = sma(valid_k, d_period) if len(valid_k) >= d_period else np.array([])

    k_out = np.full(n, np.nan, dtype=np.float64)
    d_out = np.full(n, np.nan, dtype=np.float64)

    k_start = rsi_period + stoch_period - 1 + k_period - 1
    if k_start < n and len(k_smooth) > 0:
        valid = k_smooth[~np.isnan(k_smooth)]
        end = min(k_start + len(valid), n)
        k_out[k_start:end] = valid[:end - k_start]

    d_start = rsi_period + stoch_period - 1 + d_period - 1
    if d_start < n and len(d_smooth) > 0:
        valid = d_smooth[~np.isnan(d_smooth)]
        end = min(d_start + len(valid), n)
        d_out[d_start:end] = valid[:end - d_start]

    return k_out, d_out


def cci(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 20) -> FloatArray:
    """Commodity Channel Index.

    Formula: TP = (H + L + C) / 3
             CCI = (TP - SMA(TP, period)) / (0.015 * mean_deviation)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: CCI period.

    Returns:
        CCI array.
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    tp = (high + low + close) / 3.0
    tp_sma = sma(tp, period)

    for i in range(period - 1, n):
        if np.isnan(tp_sma[i]):
            continue
        window = tp[i - period + 1:i + 1]
        mean_dev = np.mean(np.abs(window - tp_sma[i]))
        if mean_dev > 0:
            result[i] = (tp[i] - tp_sma[i]) / (0.015 * mean_dev)
        else:
            result[i] = 0.0

    return result


def williams_r(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> FloatArray:
    """Williams %R.

    Formula: %R = -100 * (highest_high - close) / (highest_high - lowest_low)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: Lookback period.

    Returns:
        Williams %R array (-100 to 0).
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)

    for i in range(period - 1, n):
        hh = np.max(high[i - period + 1:i + 1])
        ll = np.min(low[i - period + 1:i + 1])
        if hh - ll > 0:
            result[i] = -100.0 * (hh - close[i]) / (hh - ll)
        else:
            result[i] = -50.0

    return result


def roc(close: FloatArray, period: int = 12) -> FloatArray:
    """Rate of Change.

    Formula: ROC = ((close - close_n_periods_ago) / close_n_periods_ago) * 100

    Args:
        close: Close prices.
        period: Lookback period.

    Returns:
        ROC array (percentage).
    """
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) > period:
        prev = close[:-period]
        curr = close[period:]
        result[period:] = np.where(prev != 0, ((curr - prev) / np.abs(prev)) * 100.0, 0.0)
    return result


def momentum_indicator(close: FloatArray, period: int = 10) -> FloatArray:
    """Momentum indicator.

    Formula: MOM = close - close_n_periods_ago

    Args:
        close: Close prices.
        period: Lookback period.

    Returns:
        Momentum array (absolute difference).
    """
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) > period:
        result[period:] = close[period:] - close[:-period]
    return result


def awesome_oscillator(high: FloatArray, low: FloatArray, fast: int = 5, slow: int = 34) -> FloatArray:
    """Awesome Oscillator.

    Formula: AO = SMA(midpoint, fast) - SMA(midpoint, slow)
             midpoint = (H + L) / 2

    Args:
        high: High prices.
        low: Low prices.
        fast: Fast SMA period.
        slow: Slow SMA period.

    Returns:
        AO array.
    """
    midpoint = (high + low) / 2.0
    return sma(midpoint, fast) - sma(midpoint, slow)


def tsi(close: FloatArray, long_period: int = 25, short_period: int = 13, signal_period: int = 13) -> tuple[FloatArray, FloatArray]:
    """True Strength Index.

    Formula: TSI = 100 * EMA(EMA(momentum, long), short) / EMA(EMA(|momentum|, long), short)

    Args:
        close: Close prices.
        long_period: Long EMA period.
        short_period: Short EMA period.
        signal_period: Signal line EMA period.

    Returns:
        Tuple of (tsi_line, signal_line).
    """
    n = len(close)
    tsi_arr = np.full(n, np.nan, dtype=np.float64)
    signal_arr = np.full(n, np.nan, dtype=np.float64)

    if n < long_period + short_period + 1:
        return tsi_arr, signal_arr

    mom = np.diff(close)
    abs_mom = np.abs(mom)

    ema_mom_long = ema(mom, long_period)
    ema_abs_long = ema(abs_mom, long_period)

    valid_ml = ema_mom_long[~np.isnan(ema_mom_long)]
    valid_al = ema_abs_long[~np.isnan(ema_abs_long)]

    if len(valid_ml) >= short_period and len(valid_al) >= short_period:
        double_ema_mom = ema(valid_ml, short_period)
        double_ema_abs = ema(valid_al, short_period)

        valid_dm = double_ema_mom[~np.isnan(double_ema_mom)]
        valid_da = double_ema_abs[~np.isnan(double_ema_abs)]

        length = min(len(valid_dm), len(valid_da))
        tsi_vals = np.where(valid_da[:length] != 0, 100.0 * valid_dm[:length] / valid_da[:length], 0.0)

        offset = n - length
        if offset >= 0:
            tsi_arr[offset:offset + length] = tsi_vals

            # Signal line
            if len(tsi_vals) >= signal_period:
                sig = ema(tsi_vals, signal_period)
                valid_sig = sig[~np.isnan(sig)]
                sig_offset = n - len(valid_sig)
                if sig_offset >= 0:
                    signal_arr[sig_offset:sig_offset + len(valid_sig)] = valid_sig

    return tsi_arr, signal_arr


def ultimate_oscillator(high: FloatArray, low: FloatArray, close: FloatArray, period1: int = 7, period2: int = 14, period3: int = 28) -> FloatArray:
    """Ultimate Oscillator.

    Formula: BP = close - min(low, prev_close)
             TR = max(high, prev_close) - min(low, prev_close)
             avg_n = sum(BP, n) / sum(TR, n)
             UO = 100 * (4*avg1 + 2*avg2 + avg3) / 7

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period1: Short period.
        period2: Medium period.
        period3: Long period.

    Returns:
        Ultimate Oscillator array (0-100).
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period3 + 1:
        return result

    bp = close[1:] - np.minimum(low[1:], close[:-1])
    tr = np.maximum(high[1:], close[:-1]) - np.minimum(low[1:], close[:-1])

    for i in range(period3, len(bp) + 1):
        idx = i  # maps to original array at i
        tr_safe = np.where(tr[:i] == 0, 1e-10, tr[:i])
        avg1 = np.sum(bp[i - period1:i]) / np.sum(tr_safe[i - period1:i])
        avg2 = np.sum(bp[i - period2:i]) / np.sum(tr_safe[i - period2:i])
        avg3 = np.sum(bp[i - period3:i]) / np.sum(tr_safe[i - period3:i])
        result[i] = 100.0 * (4 * avg1 + 2 * avg2 + avg3) / 7.0

    return result
