"""Shared helpers for all strategy implementations."""

import numpy as np


def safe_get(ta_data: dict, *keys, default=None):
    """NaN-safe nested dict access for ta_data indicators."""
    val = ta_data
    for k in keys:
        if not isinstance(val, dict):
            return default
        val = val.get(k)
        if val is None:
            return default
    if isinstance(val, (float, np.floating)) and np.isnan(val):
        return default
    return val


def has_bullish_pattern(ta_data: dict) -> bool:
    """Check if any bullish candlestick pattern is detected."""
    patterns = safe_get(ta_data, "patterns", "candlestick", default=[])
    return any(p.get("type") == "bullish" for p in patterns)


def has_bearish_pattern(ta_data: dict) -> bool:
    """Check if any bearish candlestick pattern is detected."""
    patterns = safe_get(ta_data, "patterns", "candlestick", default=[])
    return any(p.get("type") == "bearish" for p in patterns)
