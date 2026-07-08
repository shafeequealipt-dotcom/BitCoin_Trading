"""Volatility indicators: Bollinger Bands, ATR, Keltner Channels, Donchian Channels,
Standard Deviation, Historical Volatility, NATR, Choppiness Index.

All functions take numpy arrays and return numpy arrays.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.indicators.trend import ema, sma

FloatArray = NDArray[np.float64]


def bollinger_bands(close: FloatArray, period: int = 20, std_dev: float = 2.0) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """Bollinger Bands.

    Formula: Middle = SMA(close, period)
             Upper = Middle + std_dev * std(close, period)
             Lower = Middle - std_dev * std(close, period)
             Bandwidth = (Upper - Lower) / Middle * 100

    Args:
        close: Close prices.
        period: SMA period.
        std_dev: Standard deviation multiplier.

    Returns:
        Tuple of (upper, middle, lower, bandwidth).
    """
    middle = sma(close, period)
    std = standard_deviation(close, period)

    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = np.where(middle > 0, (upper - lower) / middle * 100, np.nan)

    return upper, middle, lower, bandwidth


def atr(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> FloatArray:
    """Average True Range with Wilder smoothing.

    Formula: TR = max(H-L, |H-Cp|, |L-Cp|)
             ATR = Wilder smoothed average of TR

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: ATR period.

    Returns:
        ATR array (first period values are NaN).
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1:
        return result

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )

    # First ATR is simple average
    result[period] = np.mean(tr[:period])

    # Wilder smoothing for remaining
    for i in range(period, len(tr)):
        result[i + 1] = (result[i] * (period - 1) + tr[i]) / period

    return result


def keltner_channels(high: FloatArray, low: FloatArray, close: FloatArray, ema_period: int = 20, atr_period: int = 14, multiplier: float = 2.0) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Keltner Channels.

    Formula: Middle = EMA(close, ema_period)
             Upper = Middle + multiplier * ATR(atr_period)
             Lower = Middle - multiplier * ATR(atr_period)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        ema_period: EMA period for middle band.
        atr_period: ATR period.
        multiplier: ATR multiplier.

    Returns:
        Tuple of (upper, middle, lower).
    """
    middle = ema(close, ema_period)
    atr_vals = atr(high, low, close, atr_period)
    upper = middle + multiplier * atr_vals
    lower = middle - multiplier * atr_vals
    return upper, middle, lower


def donchian_channels(high: FloatArray, low: FloatArray, period: int = 20) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Donchian Channels.

    Formula: Upper = highest_high(period)
             Lower = lowest_low(period)
             Middle = (Upper + Lower) / 2

    Args:
        high: High prices.
        low: Low prices.
        period: Lookback period.

    Returns:
        Tuple of (upper, middle, lower).
    """
    n = len(high)
    upper = np.full(n, np.nan, dtype=np.float64)
    lower = np.full(n, np.nan, dtype=np.float64)

    for i in range(period - 1, n):
        upper[i] = np.max(high[i - period + 1:i + 1])
        lower[i] = np.min(low[i - period + 1:i + 1])

    middle = (upper + lower) / 2.0
    return upper, middle, lower


def standard_deviation(close: FloatArray, period: int = 20) -> FloatArray:
    """Rolling standard deviation.

    Formula: std = sqrt(sum((x - mean)^2) / period)

    Args:
        close: Close prices.
        period: Rolling window size.

    Returns:
        Standard deviation array.
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)

    for i in range(period - 1, n):
        window = close[i - period + 1:i + 1]
        result[i] = np.std(window, ddof=0)

    return result


def historical_volatility(close: FloatArray, period: int = 20) -> FloatArray:
    """Annualized historical volatility.

    Formula: HV = std(log_returns, period) * sqrt(365) * 100
             (365 for crypto markets which trade 24/7)

    Args:
        close: Close prices.
        period: Rolling window for std calculation.

    Returns:
        Annualized volatility percentage.
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1:
        return result

    log_returns = np.log(close[1:] / close[:-1])
    log_returns = np.where(np.isfinite(log_returns), log_returns, 0.0)

    for i in range(period, n):
        window = log_returns[i - period:i]
        result[i] = np.std(window, ddof=0) * np.sqrt(365) * 100

    return result


def natr(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> FloatArray:
    """Normalized ATR (percentage-based for cross-pair comparison).

    Formula: NATR = (ATR / close) * 100

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: ATR period.

    Returns:
        NATR percentage array.
    """
    atr_vals = atr(high, low, close, period)
    return np.where(close > 0, (atr_vals / close) * 100, np.nan)


def choppiness_index(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> FloatArray:
    """Choppiness Index — measures if market is trending or ranging.

    Formula: CI = 100 * LOG10(sum(ATR, period) / (highest_high - lowest_low)) / LOG10(period)
             CI > 61.8 = choppy/ranging, CI < 38.2 = trending

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: Lookback period.

    Returns:
        Choppiness Index array (0-100).
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    atr_vals = atr(high, low, close, min(period, 1))

    # Compute true range for summing
    if n < 2:
        return result

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )

    log_period = np.log10(period)
    if log_period == 0:
        return result

    for i in range(period, n):
        tr_sum = np.sum(tr[i - period:i])
        hh = np.max(high[i - period + 1:i + 1])
        ll = np.min(low[i - period + 1:i + 1])
        hl_range = hh - ll
        if hl_range > 0 and tr_sum > 0:
            result[i] = 100.0 * np.log10(tr_sum / hl_range) / log_period

    return result
