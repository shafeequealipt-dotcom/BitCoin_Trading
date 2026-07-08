"""Fixtures for MarketRepository tests."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.core.types import OHLCV, TimeFrame
from src.database.connection import DatabaseManager


@pytest.fixture
async def temp_db(tmp_path):
    """A connected DatabaseManager pointing at a temp SQLite file with the
    minimal schema MarketRepository.save_klines requires."""
    db_path = os.path.join(tmp_path, "trading.db")
    db = DatabaseManager(db_path, wal_mode=True)
    await db.connect()
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            turnover REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(symbol, timeframe, timestamp)
        )
        """
    )
    yield db
    await db.disconnect()


def make_klines(n: int, *, symbol: str = "TESTUSDT") -> list[OHLCV]:
    """Build N deterministic OHLCV rows on the M5 timeframe."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        OHLCV(
            symbol=symbol,
            timeframe=TimeFrame.M5,
            timestamp=base + timedelta(minutes=5 * i),
            open=100.0 + i * 0.01,
            high=100.5 + i * 0.01,
            low=99.5 + i * 0.01,
            close=100.2 + i * 0.01,
            volume=10.0 + (i % 7),
            turnover=(10.0 + (i % 7)) * (100.0 + i * 0.01),
        )
        for i in range(n)
    ]
