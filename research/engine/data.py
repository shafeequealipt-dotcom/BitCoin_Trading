"""Data access for the research harness.

Reads ONLY from research/data/market_history.db (backfilled via
research/backfill.py from Bybit's public market-data API). Never touches
shadow.db or trading.db -- the research harness is fully isolated from the
live paper-trading state, by design (see TRADING_OVERHAUL_PLAN.md Phase A).
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path

import pandas as pd

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "market_history.db"
_UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "data" / "universe.json"
# 1-minute candles for engine VALIDATION only (reproducing the live ledger's
# actual 5-30min-hold trades needs minute resolution; the backfilled research
# history is hourly+, built for the multi-hour/daily candidate strategies).
# Read-only, same isolation guarantee: research never WRITES to shadow.db.
_SHADOW_DB_PATH = Path(__file__).resolve().parents[2] / "shadow" / "data" / "shadow.db"


def _conn() -> sqlite3.Connection:
    if not _DB_PATH.exists():
        raise FileNotFoundError(
            f"{_DB_PATH} not found -- run research/backfill.py first "
            "(see research/logs/backfill.log for progress)."
        )
    return sqlite3.connect(str(_DB_PATH))


def _shadow_conn() -> sqlite3.Connection:
    uri = f"file:{_SHADOW_DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def universe() -> list[str]:
    return json.loads(_UNIVERSE_PATH.read_text())


def backfill_status() -> pd.DataFrame:
    """One row per (symbol, kind) with row counts -- coverage sanity check."""
    with _conn() as c:
        return pd.read_sql_query("SELECT * FROM backfill_progress ORDER BY symbol, kind", c)


@lru_cache(maxsize=512)
def klines(symbol: str, interval: str = "1h") -> pd.DataFrame:
    """OHLCV for one symbol/interval, indexed by UTC timestamp, oldest first.

    interval="1m" is special-cased to read shadow.db's live minute klines
    (read-only) instead of the research backfill -- see the module docstring
    on _SHADOW_DB_PATH. Every other interval reads market_history.db.

    Cached per-process: the same symbol/interval is re-read from disk only
    once per run, since a walk-forward pass over many folds/strategies would
    otherwise re-hit SQLite for the same data repeatedly.
    """
    if interval == "1m":
        with _shadow_conn() as c:
            df = pd.read_sql_query(
                "SELECT timestamp AS ts, open, high, low, close, volume, turnover FROM klines "
                "WHERE symbol=? ORDER BY timestamp", c, params=(symbol,),
            )
    else:
        with _conn() as c:
            df = pd.read_sql_query(
                "SELECT ts, open, high, low, close, volume, turnover FROM klines "
                "WHERE symbol=? AND interval=? ORDER BY ts",
                c, params=(symbol, interval),
            )
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


@lru_cache(maxsize=512)
def funding(symbol: str) -> pd.DataFrame:
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT ts, rate FROM funding WHERE symbol=? ORDER BY ts", c, params=(symbol,))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


@lru_cache(maxsize=512)
def open_interest(symbol: str, interval: str = "1h") -> pd.DataFrame:
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT ts, oi FROM open_interest WHERE symbol=? AND interval=? ORDER BY ts",
            c, params=(symbol, interval))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


def clear_cache() -> None:
    klines.cache_clear()
    funding.cache_clear()
    open_interest.cache_clear()
