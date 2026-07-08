"""Volume indicators: OBV, VWAP, MFI, A/D Line, Chaikin Money Flow,
Volume SMA, Force Index.

All functions take numpy arrays and return numpy arrays.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.indicators.trend import ema, sma

FloatArray = NDArray[np.float64]


def obv(close: FloatArray, volume: FloatArray) -> FloatArray:
    """On-Balance Volume.

    Formula: OBV_t = OBV_(t-1) + volume if close > prev_close,
                                  - volume if close < prev_close,
                                  0 if equal

    Args:
        close: Close prices.
        volume: Volume data.

    Returns:
        OBV array.
    """
    n = len(close)
    result = np.zeros(n, dtype=np.float64)
    if n < 2:
        return result

    direction = np.sign(np.diff(close))
    result[1:] = np.cumsum(direction * volume[1:])
    return result


def vwap(high: FloatArray, low: FloatArray, close: FloatArray, volume: FloatArray) -> FloatArray:
    """Volume Weighted Average Price.

    Formula: VWAP = cumsum(TP * volume) / cumsum(volume)
             TP = (H + L + C) / 3

    Note: VWAP normally resets daily. This implementation computes over
    the entire provided array for simplicity.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        volume: Volume data.

    Returns:
        VWAP array.
    """
    tp = (high + low + close) / 3.0
    cum_tp_vol = np.cumsum(tp * volume)
    cum_vol = np.cumsum(volume)
    return np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)


def mfi(high: FloatArray, low: FloatArray, close: FloatArray, volume: FloatArray, period: int = 14) -> FloatArray:
    """Money Flow Index (volume-weighted RSI).

    Formula: TP = (H + L + C) / 3
             Raw MF = TP * volume
             Positive MF = sum of MF where TP > prev TP
             Negative MF = sum of MF where TP < prev TP
             MFI = 100 - 100 / (1 + positive_MF / negative_MF)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        volume: Volume data.
        period: MFI period.

    Returns:
        MFI array (0-100).
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1:
        return result

    tp = (high + low + close) / 3.0
    raw_mf = tp * volume

    tp_diff = np.diff(tp)
    pos_mf = np.where(tp_diff > 0, raw_mf[1:], 0.0)
    neg_mf = np.where(tp_diff < 0, raw_mf[1:], 0.0)

    for i in range(period, len(pos_mf) + 1):
        pos_sum = np.sum(pos_mf[i - period:i])
        neg_sum = np.sum(neg_mf[i - period:i])
        if neg_sum > 0:
            mf_ratio = pos_sum / neg_sum
            result[i] = 100.0 - 100.0 / (1.0 + mf_ratio)
        else:
            result[i] = 100.0

    return result


def accumulation_distribution(high: FloatArray, low: FloatArray, close: FloatArray, volume: FloatArray) -> FloatArray:
    """Accumulation/Distribution Line.

    Formula: CLV = ((C - L) - (H - C)) / (H - L)
             AD = cumsum(CLV * volume)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        volume: Volume data.

    Returns:
        A/D line array.
    """
    hl_range = high - low
    clv = np.where(hl_range > 0, ((close - low) - (high - close)) / hl_range, 0.0)
    return np.cumsum(clv * volume)


def chaikin_money_flow(high: FloatArray, low: FloatArray, close: FloatArray, volume: FloatArray, period: int = 20) -> FloatArray:
    """Chaikin Money Flow.

    Formula: CLV = ((C - L) - (H - C)) / (H - L)
             CMF = sum(CLV * volume, period) / sum(volume, period)

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        volume: Volume data.
        period: Lookback period.

    Returns:
        CMF array (-1 to 1).
    """
    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)

    hl_range = high - low
    clv = np.where(hl_range > 0, ((close - low) - (high - close)) / hl_range, 0.0)
    mf_volume = clv * volume

    for i in range(period - 1, n):
        vol_sum = np.sum(volume[i - period + 1:i + 1])
        if vol_sum > 0:
            result[i] = np.sum(mf_volume[i - period + 1:i + 1]) / vol_sum
        else:
            result[i] = 0.0

    return result


def volume_sma(volume: FloatArray, period: int = 20) -> tuple[FloatArray, FloatArray]:
    """Volume Simple Moving Average and volume ratio.

    Args:
        volume: Volume data.
        period: SMA period.

    Returns:
        Tuple of (volume_sma, ratio) where ratio = current_volume / volume_sma.
        Ratio > 2.0 indicates a volume spike.
    """
    vol_avg = sma(volume, period)
    ratio = np.where((vol_avg > 0) & ~np.isnan(vol_avg), volume / vol_avg, np.nan)
    return vol_avg, ratio


def force_index(close: FloatArray, volume: FloatArray, period: int = 13) -> FloatArray:
    """Force Index.

    Formula: FI = EMA(close_change * volume, period)

    Args:
        close: Close prices.
        volume: Volume data.
        period: EMA period.

    Returns:
        Force Index array.
    """
    n = len(close)
    if n < 2:
        return np.full(n, np.nan, dtype=np.float64)

    close_change = np.zeros(n, dtype=np.float64)
    close_change[1:] = np.diff(close)
    raw_fi = close_change * volume
    return ema(raw_fi, period)
