"""Keeps research/data/market_history.db's 4h klines current for the paper
trial's universe. Same Bybit PUBLIC market-data endpoint backfill.py uses
(no key, read-only) -- this just fetches the last few bars each poll and
upserts them, rather than 2 years of history. Writes to the SAME table
research.engine.data.klines() reads, so the exact backtested signal code
(b1v2_trend_breakout_trailing.generate_signals()) runs unmodified against
live data -- there is no separate "live version" of the signal logic to
accidentally diverge from what was backtested and passed.

Only ever upserts CLOSED bars (open_time + interval <= now) -- the strategy
was backtested exclusively on closed bars, and a still-forming candle's
high/low/close are not real numbers yet.
"""
from __future__ import annotations

import sqlite3
import time

import requests

from research.engine import data as rdata

BASE = "https://api.bybit.com/v5/market"
INTERVAL_MS = 4 * 3600_000
FETCH_LIMIT = 6  # a few bars of overlap per poll is cheap and self-healing after any missed poll


def _db() -> sqlite3.Connection:
    return sqlite3.connect(str(rdata._DB_PATH))


def refresh_symbol(symbol: str) -> int:
    now_ms = int(time.time() * 1000)
    try:
        r = requests.get(f"{BASE}/kline", params={
            "category": "linear", "symbol": symbol, "interval": "240", "limit": FETCH_LIMIT,
        }, timeout=15)
        d = r.json()
        if d.get("retCode") != 0:
            return 0
        rows = d["result"]["list"]
    except requests.RequestException:
        return 0

    batch = []
    for row in rows:
        ts = int(row[0])
        if ts + INTERVAL_MS > now_ms:
            continue  # still forming -- never trade on an incomplete bar
        batch.append((symbol, "4h", ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                      float(row[5]), float(row[6])))
    if not batch:
        return 0
    with _db() as c:
        c.executemany(
            "INSERT OR REPLACE INTO klines(symbol,interval,ts,open,high,low,close,volume,turnover) "
            "VALUES (?,?,?,?,?,?,?,?,?)", batch)
        c.commit()
    return len(batch)


def refresh_universe(universe: list[str]) -> dict[str, int]:
    counts = {}
    for symbol in universe:
        counts[symbol] = refresh_symbol(symbol)
        time.sleep(0.15)  # same pacing convention as backfill.py
    rdata.clear_cache()  # klines() is lru_cache'd -- must invalidate or new bars are invisible
    return counts
