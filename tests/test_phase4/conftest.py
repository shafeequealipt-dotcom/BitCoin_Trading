"""Test fixtures for Phase 4: reproducible OHLCV data with known properties."""

import numpy as np
import pytest

from datetime import datetime, timezone, timedelta
from src.core.types import OHLCV, TimeFrame


def _make_candles(closes, symbol="BTCUSDT", timeframe=TimeFrame.H1):
    """Generate OHLCV candles from close prices with realistic O/H/L."""
    candles = []
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, c in enumerate(closes):
        noise = abs(np.random.normal(0, c * 0.005))
        o = c - np.random.normal(0, c * 0.003) if i > 0 else c
        h = max(o, c) + noise
        l = min(o, c) - noise
        candles.append(OHLCV(
            symbol=symbol, timeframe=timeframe,
            timestamp=base_time + timedelta(hours=i),
            open=float(o), high=float(h), low=float(l), close=float(c),
            volume=float(np.random.uniform(50, 200)),
        ))
    return candles


@pytest.fixture
def uptrend_data():
    """200 candles with clear uptrend (higher highs, higher lows)."""
    np.random.seed(42)
    base = 70000
    t = np.linspace(0, 5000, 200)
    noise = np.random.normal(0, 150, 200)
    closes = base + t + noise
    return closes.astype(np.float64)


@pytest.fixture
def downtrend_data():
    """200 candles with clear downtrend."""
    np.random.seed(42)
    base = 75000
    t = np.linspace(0, -5000, 200)
    noise = np.random.normal(0, 150, 200)
    closes = base + t + noise
    return closes.astype(np.float64)


@pytest.fixture
def sideways_data():
    """200 candles ranging sideways."""
    np.random.seed(42)
    base = 70000
    noise = np.random.normal(0, 200, 200)
    closes = base + noise
    return closes.astype(np.float64)


@pytest.fixture
def ohlcv_arrays(uptrend_data):
    """Full OHLCV arrays from uptrend data."""
    np.random.seed(42)
    closes = uptrend_data
    n = len(closes)
    noise = np.abs(np.random.normal(0, 100, n))
    opens = closes - np.random.normal(0, 50, n)
    highs = np.maximum(opens, closes) + noise
    lows = np.minimum(opens, closes) - noise
    volumes = np.random.uniform(50, 300, n)
    return opens, highs, lows, closes, volumes


@pytest.fixture
def uptrend_candles(uptrend_data):
    """200 OHLCV candle objects in an uptrend."""
    np.random.seed(42)
    return _make_candles(uptrend_data)


@pytest.fixture
def downtrend_candles(downtrend_data):
    """200 OHLCV candle objects in a downtrend."""
    np.random.seed(42)
    return _make_candles(downtrend_data)


@pytest.fixture
def sideways_candles(sideways_data):
    """200 OHLCV candle objects ranging sideways."""
    np.random.seed(42)
    return _make_candles(sideways_data)


@pytest.fixture
def known_rsi_data():
    """Small dataset: 20 close prices for verifiable RSI calculation."""
    return np.array([
        44.0, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
        46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
        46.22, 45.64,
    ], dtype=np.float64)


@pytest.fixture
def hammer_candles():
    """5 candles ending with a hammer: small body at top, long lower shadow."""
    # Last candle: open=97.0, high=98.0, low=93.0, close=97.8
    # Body = 0.8, lower_shadow = 97.0-93.0 = 4.0, upper_shadow = 98.0-97.8 = 0.2
    # lower_shadow(4.0) >= 2.0 * body(0.8) = 1.6 ✓
    # upper_shadow(0.2) <= body(0.8) * 0.5 = 0.4 ✓
    return np.array([100.0, 99.0, 98.0, 97.5, 97.0]), \
           np.array([101.0, 100.0, 99.0, 98.0, 98.0]), \
           np.array([99.5, 98.5, 97.5, 96.5, 93.0]), \
           np.array([100.5, 99.5, 98.5, 97.2, 97.8])


@pytest.fixture
def engulfing_candles():
    """5 candles ending with bullish engulfing."""
    opens  = np.array([100.0, 101.0, 100.5, 100.0, 98.5])
    highs  = np.array([101.0, 101.5, 101.0, 100.5, 101.5])
    lows   = np.array([99.5,  100.5, 100.0,  98.0,  98.0])
    closes = np.array([100.5, 100.8, 100.2,  98.5, 101.0])
    return opens, highs, lows, closes


@pytest.fixture
def flat_data():
    """200 candles with identical prices."""
    return np.full(200, 70000.0, dtype=np.float64)
