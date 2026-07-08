"""Fixtures for ShadowKlineReader tests.

Provides a deterministic temp shadow.db seeded with 360 minutes of 1-min
klines for two symbols, so H1 aggregation is expected to produce exactly
6 buckets per symbol.
"""

from __future__ import annotations

import sqlite3

import pytest


# 360 mins = 6 H1 buckets per symbol. base_ms is a fixed UTC ms timestamp
# aligned to an H1 boundary so bucket math is round.
# 1699999200000 ms = 2023-11-14 22:00:00 UTC — exact H1 boundary
# (1699999200000 % 3_600_000 == 0).
SEED_BASE_MS: int = 1699999200000
SEED_MINUTES: int = 360
SEED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


@pytest.fixture
def temp_shadow_db(tmp_path):
    """Create a temp SQLite file mimicking shadow.db schema and seed
    deterministic 1-min klines for two symbols over 6 hours."""
    db_path = tmp_path / "shadow.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE klines (
            symbol    TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open      REAL NOT NULL,
            high      REAL NOT NULL,
            low       REAL NOT NULL,
            close     REAL NOT NULL,
            volume    REAL NOT NULL,
            turnover  REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, timestamp)
        )
        """
    )
    rows: list[tuple] = []
    for sym_idx, sym in enumerate(SEED_SYMBOLS):
        # Different price scales per symbol to detect cross-symbol leaks.
        base_price = 100.0 + sym_idx * 1000.0
        for m in range(SEED_MINUTES):
            ts = SEED_BASE_MS + m * 60_000
            o = base_price + m * 0.1
            h = o + 0.5
            l = o - 0.5
            c = o + 0.1
            v = 1.0 + (m % 10) * 0.1   # varies per minute
            t = v * o                   # turnover = price × volume
            rows.append((sym, ts, o, h, l, c, v, t))
    conn.executemany(
        "INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()
    return str(db_path)
