"""Unit tests for research/engine/backtest_trailing.py.

backtest.run() (fixed bracket) was validated by cross-checking against a
trusted prior script (research/validate_engine.py) -- there is no equivalent
prior script for a trailing stop, so this validates it directly against
hand-constructed price paths where the correct outcome is known by
inspection, covering: no-look-ahead, initial-stop-before-arming, trail-
ratchet-never-loosens, exit-on-trail-touch, time-exit, and the short side.

Run with: research/.venv/bin/python -m pytest tests/research/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.engine import backtest_trailing as bt
from research.engine import data as rdata


def _bars(prices: list[tuple[float, float, float, float]], start="2026-01-01", freq="1h") -> pd.DataFrame:
    """prices: list of (open, high, low, close) tuples, one per bar."""
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    df = pd.DataFrame(prices, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    df["turnover"] = 1.0
    return df


@pytest.fixture(autouse=True)
def _patch_klines(monkeypatch):
    """Route research.engine.data.klines() to an in-memory series set per test."""
    store: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(rdata, "klines", lambda symbol, interval="1h": store.get(symbol, pd.DataFrame()))
    yield store


LONG_SIGNAL = {"symbol": "X", "signal_ts": None, "direction": 1,
                "stop_pct": 5.0, "trail_activate_pct": 3.0, "trail_pct": 4.0, "max_hold": 20}


def _signal(**over):
    s = dict(LONG_SIGNAL)
    s.update(over)
    return pd.DataFrame([s])


def test_no_lookahead_entry_is_next_bar_open(_patch_klines):
    bars = _bars([(100, 101, 99, 100)] * 5)
    _patch_klines["X"] = bars
    sig = _signal(signal_ts=bars.index[0])
    out = bt.run(sig, "1h")
    assert out.iloc[0]["entry_ts"] == bars.index[1]
    assert out.iloc[0]["entry_price"] == 100  # open of bar 1


def test_lookahead_violation_raises(_patch_klines):
    bars = _bars([(100, 101, 99, 100)] * 5)
    _patch_klines["X"] = bars
    # signal_ts == the LAST bar's own timestamp -> no bar strictly after it -> dropped, not an error.
    sig = _signal(signal_ts=bars.index[-1])
    out = bt.run(sig, "1h")
    assert out.attrs["dropped"] == 1


def test_initial_stop_before_ever_arming(_patch_klines):
    # entry=100 (open of bar1). Price never moves 3% favorable (activation),
    # then drops straight through the 5% initial stop (95).
    bars = _bars([
        (100, 101, 99, 100),   # bar0 (signal bar, ignored for entry)
        (100, 101, 99, 100),   # bar1 -> entry_price=100
        (100, 100.5, 96, 97),  # bar2: dips to 96, still above 95 stop
        (97, 97, 94, 95),      # bar3: low=94 <= 95 stop -> exit here
        (95, 95, 90, 91),
    ])
    _patch_klines["X"] = bars
    out = bt.run(_signal(signal_ts=bars.index[0]), "1h")
    row = out.iloc[0]
    assert row["exit_reason"] == "initial_stop"
    assert row["armed"] == False
    assert row["exit_price"] == pytest.approx(95.0)
    assert row["exit_ts"] == bars.index[3]


def test_trail_arms_and_ratchets_then_exits_on_touch(_patch_klines):
    # entry=100. Activation at +3% (103). Trail = 4% behind the running high.
    bars = _bars([
        (100, 101, 99, 100),    # bar0 signal
        (100, 101, 99, 100),    # bar1 entry=100, initial stop=95
        (100, 104, 100, 104),   # bar2 high=104 -> favorable=4% >= 3% ACTIVATES.
                                 #      candidate stop = 104*0.96 = 99.84 -> stop moves 95->99.84
        (104, 110, 103, 109),   # bar3 high=110 -> new extreme. candidate = 110*0.96=105.6 -> stop 99.84->105.6
        (109, 109, 104, 105),   # bar4 low=104 < 105.6 stop -> EXIT at 105.6
        (105, 105, 100, 101),
    ])
    _patch_klines["X"] = bars
    out = bt.run(_signal(signal_ts=bars.index[0]), "1h")
    row = out.iloc[0]
    assert row["armed"] == True
    assert row["exit_reason"] == "trail_stop"
    assert row["exit_price"] == pytest.approx(105.6)
    assert row["exit_ts"] == bars.index[4]
    # locked in a real gain, not the initial stop's loss
    assert row["gross_pct"] > 0


def test_trail_never_loosens_on_a_pullback_before_new_high(_patch_klines):
    # After arming at bar2 (stop->99.84), bar3 pulls back (doesn't touch stop,
    # doesn't make a new high) -- the stop must NOT move back toward entry.
    bars = _bars([
        (100, 101, 99, 100),   # bar0 signal
        (100, 101, 99, 100),   # bar1 entry=100
        (100, 104, 100, 103),  # bar2 arms: extreme=104, stop=99.84
        (103, 103.5, 101, 102),  # bar3 pulls back, high=103.5 < 104 -> stop STAYS 99.84 (does not loosen)
        (102, 102, 99.8, 100),   # bar4 low=99.8 < 99.84 -> exits at 99.84, not lower
        (100, 100, 95, 96),
    ])
    _patch_klines["X"] = bars
    out = bt.run(_signal(signal_ts=bars.index[0]), "1h")
    row = out.iloc[0]
    assert row["exit_reason"] == "trail_stop"
    assert row["exit_price"] == pytest.approx(99.84)
    assert row["exit_ts"] == bars.index[4]


def test_time_exit_when_nothing_triggers(_patch_klines):
    bars = _bars([(100, 101, 99, 100)] * 4 + [(100, 100.5, 99.5, 100.2)])
    _patch_klines["X"] = bars
    out = bt.run(_signal(signal_ts=bars.index[0], max_hold=3), "1h")
    row = out.iloc[0]
    assert row["exit_reason"] == "time"
    assert row["bars_held"] == 3


def test_short_side_symmetry(_patch_klines):
    # Mirror of the arm-and-ratchet test, direction=-1.
    bars = _bars([
        (100, 101, 99, 100),
        (100, 101, 99, 100),    # entry=100, initial stop=105 (5% above)
        (100, 100, 96, 96),     # low=96 -> favorable=4% >= 3% activate. candidate=96*1.04=99.84 -> stop 105->99.84
        (96, 97, 90, 91),       # new low=90 -> candidate=90*1.04=93.6 -> stop 99.84->93.6
        (91, 96, 91, 95),       # high=96 >= 93.6 -> EXIT at 93.6
        (95, 95, 90, 91),
    ])
    _patch_klines["X"] = bars
    out = bt.run(_signal(signal_ts=bars.index[0], direction=-1), "1h")
    row = out.iloc[0]
    assert row["armed"] == True
    assert row["exit_reason"] == "trail_stop"
    assert row["exit_price"] == pytest.approx(93.6)
    assert row["gross_pct"] > 0  # short profited: price fell then bounced, locked in above entry
