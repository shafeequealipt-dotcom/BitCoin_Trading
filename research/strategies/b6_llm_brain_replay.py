"""B6 (TRADING_OVERHAUL_PLAN.md Phase B): the current LLM brain, judged by
the SAME bar as every other candidate.

Reads every proposal the brain ever made with a recorded price
(`trade_thesis`, both EXECUTED and gate-BLOCKED rows -- e.g. "voided" from
the untracked-symbol bug fixed 2026-09-19). Including blocked proposals is
deliberate: this tests the brain's DIRECTION/SL/TP judgment itself, not
"which of its ideas survived infrastructure", which is a different
question. `claude_decisions.full_response` (which would have carried the
raw text for every call, including ones with zero surviving trades) is
empty on every row checked 2026-09-27 -- never actually populated -- so
this is the most complete replay the stored data supports.

Uses each proposal's OWN entry price, direction, stop, target, and
max_hold_minutes exactly as the brain specified them -- this strategy has
no parameters of its own to fix in advance; it replays what already
happened. Minute-resolution candles (shadow.db, read-only, see
research/engine/data.py) since these are 5-90 minute holds.

Honest limitation: trade_thesis only covers the live bot's actual operating
history (~80 days), not the 2-year window B1-B5 get -- there is no way to
extend this one retroactively; the brain's own logging is the ceiling.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

INTERVAL = "1m"
SHADOW_DB = Path(__file__).resolve().parents[2] / "shadow" / "data" / "shadow.db"
TRADING_DB = Path(__file__).resolve().parents[2] / "data" / "trading.db"
DEFAULT_MAX_HOLD = 90  # minutes -- fallback when max_hold_minutes wasn't recorded


def generate_signals(universe: list[str]) -> pd.DataFrame:
    # Ignores `universe` -- this strategy replays actual logged proposals,
    # not a symbol scan, so it only ever covers symbols the brain actually
    # proposed (kept as a parameter for interface consistency with run_strategy.py).
    with sqlite3.connect(f"file:{TRADING_DB}?mode=ro", uri=True) as c:
        df = pd.read_sql_query(
            "SELECT symbol, direction, entry_price, stop_loss_price, take_profit_price, "
            "max_hold_minutes, opened_at FROM trade_thesis "
            "WHERE entry_price IS NOT NULL AND entry_price > 0 "
            "AND stop_loss_price IS NOT NULL AND take_profit_price IS NOT NULL", c)

    df["signal_ts"] = pd.to_datetime(df["opened_at"], utc=True) - pd.Timedelta(minutes=1)
    df["direction"] = df["direction"].map({"Buy": 1, "Sell": -1})
    df = df.dropna(subset=["direction"])
    df["stop_pct"] = (df["stop_loss_price"] - df["entry_price"]).abs() / df["entry_price"] * 100
    df["target_pct"] = (df["take_profit_price"] - df["entry_price"]).abs() / df["entry_price"] * 100
    df["max_hold"] = df["max_hold_minutes"].fillna(DEFAULT_MAX_HOLD).clip(lower=5, upper=240).astype(int)
    df = df[(df["stop_pct"] > 0) & (df["target_pct"] > 0)]

    return df[["symbol", "signal_ts", "direction", "stop_pct", "target_pct", "max_hold"]]
