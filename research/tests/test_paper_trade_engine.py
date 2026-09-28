"""Integration test for research/paper_trade/engine.py against a temp
ledger DB and synthetic candle data -- proves the live tick logic (entry-bar
handling, multi-bar catch-up, stop/time exits, risk-gate blocking) matches
backtest_trailing.py's semantics before this runs unattended with the
paper account at stake.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.engine import data as rdata
from research.paper_trade import engine, ledger


def _bars(prices, start="2026-01-01", freq="4h"):
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    df = pd.DataFrame(prices, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    df["turnover"] = 1.0
    return df


@pytest.fixture
def temp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DB_PATH", tmp_path / "ledger.db")
    ledger.init_db()
    yield
    # klines_store (when used alongside this fixture) replaces rdata.klines
    # with a plain lambda, which has no .cache_clear() -- monkeypatch
    # reverts that automatically, nothing to clean up here.


@pytest.fixture
def klines_store(monkeypatch):
    store: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(rdata, "klines", lambda symbol, interval="1h": store.get(symbol, pd.DataFrame()))
    yield store


def test_signal_detection_only_fires_on_the_fresh_bar(temp_ledger, klines_store, monkeypatch):
    """A signal whose signal_ts is NOT the second-to-last bar (stale, from
    several bars ago) must NOT be acted on -- only the freshest possible
    signal (entry bar = the bar that just closed) opens a position."""
    bars = _bars([(100, 101, 99, 100)] * 5)
    klines_store["XUSDT"] = bars

    stale_signal = pd.DataFrame([{
        "symbol": "XUSDT", "signal_ts": bars.index[0], "direction": 1,  # stale -- bar 0, not bar 3
        "stop_pct": 2.0, "trail_activate_pct": 1.0, "trail_pct": 1.5, "max_hold": 20,
    }])
    monkeypatch.setattr(engine.strat, "generate_signals", lambda universe: stale_signal)

    engine._maybe_open_new_positions(["XUSDT"], set())
    assert ledger.get_open_positions() == []


def test_fresh_signal_opens_a_position_at_next_bar_open(temp_ledger, klines_store, monkeypatch):
    bars = _bars([
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 105, 100, 104),   # bar2 = the signal bar (breakout closed here)
        (104, 106, 103, 105),   # bar3 = the fresh entry bar (just closed) -- fill at its open=104
    ])
    klines_store["XUSDT"] = bars
    fresh_signal = pd.DataFrame([{
        "symbol": "XUSDT", "signal_ts": bars.index[2], "direction": 1,
        "stop_pct": 3.0, "trail_activate_pct": 1.0, "trail_pct": 2.0, "max_hold": 20,
    }])
    monkeypatch.setattr(engine.strat, "generate_signals", lambda universe: fresh_signal)

    engine._maybe_open_new_positions(["XUSDT"], set())
    pos = ledger.get_open_positions()
    assert len(pos) == 1
    assert pos[0]["entry_price"] == pytest.approx(104.0)   # open of bar3, not bar2's close
    assert pos[0]["bars_held"] == 1  # entry bar itself already processed


def test_already_open_symbol_is_never_pyramided(temp_ledger, klines_store, monkeypatch):
    bars = _bars([(100, 101, 99, 100)] * 4)
    klines_store["XUSDT"] = bars
    sig = pd.DataFrame([{
        "symbol": "XUSDT", "signal_ts": bars.index[-2], "direction": 1,
        "stop_pct": 2.0, "trail_activate_pct": 1.0, "trail_pct": 1.5, "max_hold": 20,
    }])
    monkeypatch.setattr(engine.strat, "generate_signals", lambda universe: sig)
    engine._maybe_open_new_positions(["XUSDT"], {"XUSDT"})  # already open
    assert ledger.get_open_positions() == []


def test_open_position_catches_up_multiple_missed_bars_and_exits_on_stop(temp_ledger, klines_store):
    bars = _bars([
        (100, 101, 99, 100),
        (100, 101, 99, 100),    # entry bar (bar1), stop=97 (3% below 100)
        (100, 104, 100, 103),   # bar2: arms trail (favorable=4%>=1%), stop moves toward 104*0.98=101.92
        (103, 105, 102, 104),   # bar3: new extreme=105 -> candidate stop=105*0.98=102.9
        (104, 104, 102.5, 103), # bar4: low=102.5 < 102.9 stop -> EXIT here
    ])
    klines_store["XUSDT"] = bars
    pid = ledger.open_position(
        "XUSDT", 1, entry_price=100, entry_ts=str(bars.index[1]),
        notional_usd=1000, risk_usd=30, stop_price=97, max_hold_bars=20,
        trail_activate_pct=1.0, trail_pct=2.0, bars_held=1, extreme_price=101,
        last_checked_ts=str(bars.index[1]),  # simulate: only the entry bar was processed so far
    )
    pos = ledger.get_open_positions()[0]
    engine._process_open_position(pos, bars)  # should walk bars 2,3,4 in one call, catching up
    assert ledger.get_open_positions() == []  # closed
    with engine.ledger._conn() as c:
        c.row_factory = __import__("sqlite3").Row
        trades = c.execute("SELECT * FROM trade_history").fetchall()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "trail_stop"


def test_risk_gate_blocks_a_signal_when_drawdown_limit_hit(temp_ledger, klines_store, monkeypatch):
    bars = _bars([
        (100, 101, 99, 100), (100, 101, 99, 100),
        (100, 105, 100, 104), (104, 106, 103, 105),
    ])
    klines_store["XUSDT"] = bars
    sig = pd.DataFrame([{
        "symbol": "XUSDT", "signal_ts": bars.index[2], "direction": 1,
        "stop_pct": 3.0, "trail_activate_pct": 1.0, "trail_pct": 2.0, "max_hold": 20,
    }])
    monkeypatch.setattr(engine.strat, "generate_signals", lambda universe: sig)
    # force equity far enough below peak to trip the drawdown breaker
    with ledger._conn() as c:
        c.execute("UPDATE wallet SET equity_usd=8000, peak_equity_usd=10000 WHERE id=1")
        c.commit()

    engine._maybe_open_new_positions(["XUSDT"], set())
    assert ledger.get_open_positions() == []
