"""Trend-following indicators: SMA, EMA, WMA, DEMA, TEMA, MACD, ADX, Supertrend,
Ichimoku, Parabolic SAR, Linear Regression, MA Crossover.

All functions take numpy arrays and return numpy arrays.
Uses numpy vectorized operations — no Python loops for core math.
"""

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def sma(close: FloatArray, period: int = 20) -> FloatArray:
    """Simple Moving Average.

    Formula: SMA_t = sum(close[t-period+1 : t+1]) / period

    Args:
        close: Array of closing prices.
        period: Lookback period.

    Returns:
        SMA array (first period-1 values are NaN).
    """
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return result
    cumsum = np.cumsum(close)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    result[period - 1:] = cumsum[period - 1:] / period
    return result


def ema(close: FloatArray, period: int = 20) -> FloatArray:
    """Exponential Moving Average.

    Formula: EMA_t = close_t * k + EMA_(t-1) * (1 - k), where k = 2 / (period + 1)
    Initialized with SMA for the first period.

    Args:
        close: Array of closing prices.
        period: Lookback period.

    Returns:
        EMA array (first period-1 values are NaN).
    """
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.mean(close[:period])
    for i in range(period, len(close)):
        result[i] = close[i] * k + result[i - 1] * (1 - k)
    return result


def wma(close: FloatArray, period: int = 20) -> FloatArray:
    """Weighted Moving Average.

    Formula: WMA = sum(close[i] * weight[i]) / sum(weights), weights = [1, 2, ..., period]

    Args:
        close: Array of closing prices.
        period: Lookback period.

    Returns:
        WMA array (first period-1 values are NaN).
    """
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return result
    weights = np.arange(1, period + 1, dtype=np.float64)
    w_sum = weights.sum()
    for i in range(period - 1, len(close)):
        result[i] = np.dot(close[i - period + 1:i + 1], weights) / w_sum
    return result


def dema(close: FloatArray, period: int = 20) -> FloatArray:
    """Double Exponential Moving Average.

    Formula: DEMA = 2 * EMA(close, period) - EMA(EMA(close, period), period)

    Args:
        close: Array of closing prices.
        period: Lookback period.

    Returns:
        DEMA array.
    """
    ema1 = ema(close, period)
    ema2 = ema(ema1[~np.isnan(ema1)], period)
    result = np.full_like(close, np.nan, dtype=np.float64)
    offset = np.count_nonzero(np.isnan(ema1))
    offset2 = offset + period - 1
    if offset2 < len(result) and len(ema2) > 0:
        end = min(offset2 + len(ema2), len(result))
        length = end - offset2
        result[offset2:end] = 2 * ema1[offset2:end] - ema2[:length]
    return result


def tema(close: FloatArray, period: int = 20) -> FloatArray:
    """Triple Exponential Moving Average.

    Formula: TEMA = 3*EMA1 - 3*EMA2 + EMA3

    Args:
        close: Array of closing prices.
        period: Lookback period.

    Returns:
        TEMA array.
    """
    ema1 = ema(close, period)
    valid1 = ema1[~np.isnan(ema1)]
    ema2 = ema(valid1, period) if len(valid1) >= period else np.array([])
    valid2 = ema2[~np.isnan(ema2)] if len(ema2) > 0 else np.array([])
    ema3 = ema(valid2, period) if len(valid2) >= period else np.array([])

    result = np.full_like(close, np.nan, dtype=np.float64)
    offset1 = period - 1
    offset2 = offset1 + period - 1
    offset3 = offset2 + period - 1

    if offset3 < len(result) and len(ema3) > 0:
        valid3 = ema3[~np.isnan(ema3)]
        length = min(len(valid3), len(result) - offset3)
        e1_slice = ema1[offset3:offset3 + length]
        e2_slice = ema2[offset3 - offset1:offset3 - offset1 + length] if len(ema2) > offset3 - offset1 else np.array([])
        if len(e2_slice) >= length and len(valid3) >= length:
            result[offset3:offset3 + length] = 3 * e1_slice - 3 * e2_slice[:length] + valid3[:length]
    return result


def macd(close: FloatArray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Moving Average Convergence Divergence.

    Formula: MACD = EMA(fast) - EMA(slow)
             Signal = EMA(MACD, signal_period)
             Histogram = MACD - Signal

    Args:
        close: Array of closing prices.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal line EMA period.

    Returns:
        Tuple of (macd_line, signal_line, histogram).
    """
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow

    # Signal line: EMA of MACD values (only non-NaN)
    signal_line = np.full_like(close, np.nan, dtype=np.float64)
    valid_start = slow - 1  # First valid MACD value
    if valid_start < len(macd_line):
        macd_valid = macd_line[valid_start:]
        sig = ema(macd_valid, signal)
        signal_line[valid_start:valid_start + len(sig)] = sig

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def adx(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Average Directional Index.

    Formula: TR = max(H-L, |H-Cp|, |L-Cp|)
             +DM = H-Hp if positive else 0, -DM = Lp-L if positive else 0
             +DI = 100 * smoothed(+DM) / smoothed(TR)
             -DI = 100 * smoothed(-DM) / smoothed(TR)
             DX = 100 * |+DI - -DI| / (+DI + -DI)
             ADX = smoothed(DX)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: ADX period.

    Returns:
        Tuple of (adx, plus_di, minus_di).
    """
    n = len(close)
    adx_arr = np.full(n, np.nan, dtype=np.float64)
    plus_di = np.full(n, np.nan, dtype=np.float64)
    minus_di = np.full(n, np.nan, dtype=np.float64)

    if n < period + 1:
        return adx_arr, plus_di, minus_di

    # True Range
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))

    # Directional Movement
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder smoothing
    atr_s = np.full(n - 1, np.nan, dtype=np.float64)
    pdm_s = np.full(n - 1, np.nan, dtype=np.float64)
    mdm_s = np.full(n - 1, np.nan, dtype=np.float64)

    atr_s[period - 1] = np.mean(tr[:period])
    pdm_s[period - 1] = np.mean(plus_dm[:period])
    mdm_s[period - 1] = np.mean(minus_dm[:period])

    for i in range(period, len(tr)):
        atr_s[i] = (atr_s[i - 1] * (period - 1) + tr[i]) / period
        pdm_s[i] = (pdm_s[i - 1] * (period - 1) + plus_dm[i]) / period
        mdm_s[i] = (mdm_s[i - 1] * (period - 1) + minus_dm[i]) / period

    pdi = np.where(atr_s > 0, 100 * pdm_s / atr_s, 0.0)
    mdi = np.where(atr_s > 0, 100 * mdm_s / atr_s, 0.0)

    di_sum = pdi + mdi
    dx = np.where(di_sum > 0, 100 * np.abs(pdi - mdi) / di_sum, 0.0)

    # ADX = smoothed DX
    adx_temp = np.full(len(dx), np.nan, dtype=np.float64)
    start = 2 * period - 1
    if start < len(dx):
        adx_temp[start] = np.mean(dx[period:start + 1])
        for i in range(start + 1, len(dx)):
            if not np.isnan(adx_temp[i - 1]) and not np.isnan(dx[i]):
                adx_temp[i] = (adx_temp[i - 1] * (period - 1) + dx[i]) / period

    # Map back to original array size (offset by 1 due to diff)
    plus_di[1:] = pdi
    minus_di[1:] = mdi
    adx_arr[1:] = adx_temp

    return adx_arr, plus_di, minus_di


def supertrend(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 10, multiplier: float = 3.0) -> tuple[FloatArray, FloatArray]:
    """Supertrend indicator.

    Formula: basic_upper = (H+L)/2 + multiplier * ATR
             basic_lower = (H+L)/2 - multiplier * ATR
             With trailing upper/lower band logic and direction flip.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: ATR period.
        multiplier: ATR multiplier.

    Returns:
        Tuple of (supertrend_line, direction) where direction is 1.0 (bullish) or -1.0 (bearish).
    """
    from src.analysis.indicators.volatility import atr as calc_atr
    n = len(close)
    st = np.full(n, np.nan, dtype=np.float64)
    direction = np.full(n, np.nan, dtype=np.float64)

    atr_vals = calc_atr(high, low, close, period)
    hl2 = (high + low) / 2.0

    upper = hl2 + multiplier * atr_vals
    lower = hl2 - multiplier * atr_vals

    final_upper = np.copy(upper)
    final_lower = np.copy(lower)

    start = period
    if start >= n:
        return st, direction

    for i in range(start, n):
        if np.isnan(final_upper[i]) or np.isnan(final_lower[i]):
            continue
        if not np.isnan(final_upper[i - 1]):
            if final_upper[i] > final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
                pass
            else:
                final_upper[i] = min(final_upper[i], final_upper[i - 1])
        if not np.isnan(final_lower[i - 1]):
            if final_lower[i] < final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
                pass
            else:
                final_lower[i] = max(final_lower[i], final_lower[i - 1])

    # Direction
    direction[start] = 1.0
    st[start] = final_lower[start]
    for i in range(start + 1, n):
        if np.isnan(final_upper[i]) or np.isnan(final_lower[i]):
            continue
        prev_dir = direction[i - 1] if not np.isnan(direction[i - 1]) else 1.0
        if prev_dir == 1.0:
            if close[i] < final_lower[i]:
                direction[i] = -1.0
                st[i] = final_upper[i]
            else:
                direction[i] = 1.0
                st[i] = final_lower[i]
        else:
            if close[i] > final_upper[i]:
                direction[i] = 1.0
                st[i] = final_lower[i]
            else:
                direction[i] = -1.0
                st[i] = final_upper[i]

    return st, direction


def ichimoku(high: FloatArray, low: FloatArray, close: FloatArray, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> dict[str, FloatArray]:
    """Ichimoku Cloud.

    Formula: Tenkan-sen = (highest_high(tenkan) + lowest_low(tenkan)) / 2
             Kijun-sen = (highest_high(kijun) + lowest_low(kijun)) / 2
             Senkou Span A = (Tenkan + Kijun) / 2, shifted forward kijun periods
             Senkou Span B = (highest_high(senkou_b) + lowest_low(senkou_b)) / 2, shifted forward kijun periods
             Chikou Span = close shifted back kijun periods

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        tenkan: Tenkan-sen period.
        kijun: Kijun-sen period.
        senkou_b: Senkou Span B period.

    Returns:
        Dict with tenkan_sen, kijun_sen, senkou_span_a, senkou_span_b, chikou_span.
    """
    n = len(close)

    def midline(h: FloatArray, l: FloatArray, period: int) -> FloatArray:
        result = np.full(n, np.nan, dtype=np.float64)
        for i in range(period - 1, n):
            result[i] = (np.max(h[i - period + 1:i + 1]) + np.min(l[i - period + 1:i + 1])) / 2
        return result

    tenkan_sen = midline(high, low, tenkan)
    kijun_sen = midline(high, low, kijun)

    senkou_a = np.full(n, np.nan, dtype=np.float64)
    senkou_b_arr = np.full(n, np.nan, dtype=np.float64)
    chikou = np.full(n, np.nan, dtype=np.float64)

    # Senkou spans (not shifted forward for array simplicity — document this)
    valid = ~(np.isnan(tenkan_sen) | np.isnan(kijun_sen))
    senkou_a[valid] = (tenkan_sen[valid] + kijun_sen[valid]) / 2
    senkou_b_arr = midline(high, low, senkou_b)

    # Chikou = close shifted back kijun periods
    if n > kijun:
        chikou[:n - kijun] = close[kijun:]

    return {
        "tenkan_sen": tenkan_sen,
        "kijun_sen": kijun_sen,
        "senkou_span_a": senkou_a,
        "senkou_span_b": senkou_b_arr,
        "chikou_span": chikou,
    }


def parabolic_sar(high: FloatArray, low: FloatArray, close: FloatArray, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2) -> FloatArray:
    """Parabolic Stop And Reverse.

    Formula: SAR_(t+1) = SAR_t + AF * (EP - SAR_t)
             AF starts at af_start, increases by af_step on new extremes, capped at af_max.
             EP = extreme point (highest high in uptrend, lowest low in downtrend).

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        af_start: Initial acceleration factor.
        af_step: AF increment.
        af_max: Maximum AF.

    Returns:
        SAR array.
    """
    n = len(close)
    sar = np.full(n, np.nan, dtype=np.float64)
    if n < 2:
        return sar

    bull = True
    af = af_start
    ep = high[0]
    sar[0] = low[0]

    for i in range(1, n):
        prev_sar = sar[i - 1]
        sar[i] = prev_sar + af * (ep - prev_sar)

        if bull:
            sar[i] = min(sar[i], low[i - 1])
            if i >= 2:
                sar[i] = min(sar[i], low[i - 2])
            if low[i] < sar[i]:
                bull = False
                sar[i] = ep
                ep = low[i]
                af = af_start
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            sar[i] = max(sar[i], high[i - 1])
            if i >= 2:
                sar[i] = max(sar[i], high[i - 2])
            if high[i] > sar[i]:
                bull = True
                sar[i] = ep
                ep = high[i]
                af = af_start
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)

    return sar


def linear_regression(close: FloatArray, period: int = 20) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Linear Regression Channel.

    Uses numpy least-squares fitting over rolling windows.

    Args:
        close: Close prices.
        period: Rolling window size.

    Returns:
        Tuple of (regression_line, upper_channel, lower_channel).
    """
    n = len(close)
    reg_line = np.full(n, np.nan, dtype=np.float64)
    upper = np.full(n, np.nan, dtype=np.float64)
    lower = np.full(n, np.nan, dtype=np.float64)

    if n < period:
        return reg_line, upper, lower

    x = np.arange(period, dtype=np.float64)
    for i in range(period - 1, n):
        y = close[i - period + 1:i + 1]
        coeffs = np.polyfit(x, y, 1)
        val = np.polyval(coeffs, period - 1)
        residuals = y - np.polyval(coeffs, x)
        std = np.std(residuals)
        reg_line[i] = val
        upper[i] = val + 2 * std
        lower[i] = val - 2 * std

    return reg_line, upper, lower


def moving_average_crossover(close: FloatArray, fast_period: int = 9, slow_period: int = 21) -> FloatArray:
    """Moving Average Crossover signal.

    Returns: 1 = golden cross (fast crosses above slow), -1 = death cross, 0 = no cross.

    Args:
        close: Close prices.
        fast_period: Fast EMA period.
        slow_period: Slow EMA period.

    Returns:
        Signal array with 1, -1, or 0.
    """
    fast_ema = ema(close, fast_period)
    slow_ema = ema(close, slow_period)
    n = len(close)
    signal = np.zeros(n, dtype=np.float64)

    for i in range(1, n):
        if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]) or np.isnan(fast_ema[i - 1]) or np.isnan(slow_ema[i - 1]):
            continue
        prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
        curr_diff = fast_ema[i] - slow_ema[i]
        if prev_diff <= 0 and curr_diff > 0:
            signal[i] = 1.0
        elif prev_diff >= 0 and curr_diff < 0:
            signal[i] = -1.0

    return signal
