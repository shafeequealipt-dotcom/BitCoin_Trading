"""J1 Phase 3 Step A (2026-05-14) — TradingRepository.prune_positions_not_in_set
SQL-level pins.

The adapter test suite covers the high-level behaviour via a fake repo.
This module pins the DB-level contract directly: the SQL filters
correctly by exchange_mode, deletes only stale rows, leaves other modes
untouched, and is idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.types import Position, Side
from src.database.connection import DatabaseManager
from src.database.repositories.trading_repo import TradingRepository


def _pos(symbol: str, size: float = 1.0) -> Position:
    return Position(
        symbol=symbol, side=Side.BUY, size=size,
        entry_price=50_000.0, mark_price=50_100.0,
        unrealized_pnl=0.0, realized_pnl=0.0, leverage=10,
        liquidation_price=45_000.0, stop_loss=49_000.0, take_profit=51_000.0,
        updated_at=datetime.now(UTC),
    )


@pytest.fixture()
async def db(tmp_path):
    """Spin up DB with the v32 positions schema (column + index)."""
    db_path = tmp_path / "j1_prune_test.db"
    mgr = DatabaseManager(str(db_path))
    await mgr.connect()
    await mgr.execute(
        """
        CREATE TABLE positions (
            symbol TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            entry_price REAL NOT NULL,
            mark_price REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            realized_pnl REAL NOT NULL DEFAULT 0,
            leverage INTEGER NOT NULL DEFAULT 1,
            liquidation_price REAL NOT NULL DEFAULT 0,
            stop_loss REAL,
            take_profit REAL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            exchange_mode TEXT NOT NULL DEFAULT 'shadow'
        )
        """,
    )
    await mgr.execute(
        "CREATE INDEX idx_positions_mode ON positions(exchange_mode)",
    )
    yield mgr
    await mgr.disconnect()


@pytest.mark.asyncio
async def test_prune_deletes_only_stale_bybit_demo_rows(db) -> None:
    """Cache has three bybit_demo rows and two shadow rows. Pruning with
    live_symbols={BTC} must remove the two stale bybit_demo rows and
    keep both shadow rows untouched."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("ETHUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("RUNEUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("SOLUSDT"), exchange_mode="shadow")
    await repo.save_position(_pos("ADAUSDT"), exchange_mode="shadow")

    pruned = await repo.prune_positions_not_in_set(
        mode="bybit_demo", live_symbols={"BTCUSDT"},
    )

    assert sorted(pruned) == ["ETHUSDT", "RUNEUSDT"]
    rows = await db.fetch_all("SELECT symbol, exchange_mode FROM positions ORDER BY symbol")
    by_mode = {(r["symbol"], r["exchange_mode"]) for r in rows}
    assert by_mode == {
        ("ADAUSDT", "shadow"),
        ("BTCUSDT", "bybit_demo"),
        ("SOLUSDT", "shadow"),
    }


@pytest.mark.asyncio
async def test_prune_with_full_live_set_is_noop(db) -> None:
    """When live_symbols covers every cached row, prune returns empty
    and the table is unchanged."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("ETHUSDT"), exchange_mode="bybit_demo")

    pruned = await repo.prune_positions_not_in_set(
        mode="bybit_demo", live_symbols={"BTCUSDT", "ETHUSDT"},
    )

    assert pruned == []
    rows = await db.fetch_all("SELECT symbol FROM positions ORDER BY symbol")
    assert {r["symbol"] for r in rows} == {"BTCUSDT", "ETHUSDT"}


@pytest.mark.asyncio
async def test_prune_with_empty_live_set_clears_mode(db) -> None:
    """When live_symbols is empty and the cache holds bybit_demo rows,
    every bybit_demo row is pruned. Shadow rows untouched."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("ETHUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("SOLUSDT"), exchange_mode="shadow")

    pruned = await repo.prune_positions_not_in_set(
        mode="bybit_demo", live_symbols=set(),
    )

    assert sorted(pruned) == ["BTCUSDT", "ETHUSDT"]
    rows = await db.fetch_all("SELECT symbol, exchange_mode FROM positions")
    assert [(r["symbol"], r["exchange_mode"]) for r in rows] == [
        ("SOLUSDT", "shadow"),
    ]


@pytest.mark.asyncio
async def test_prune_with_empty_mode_returns_empty(db) -> None:
    """Passing an empty mode string must be a no-op safety net — a
    caller without a mode must not accidentally clear the whole table."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("SOLUSDT"), exchange_mode="shadow")

    pruned = await repo.prune_positions_not_in_set(
        mode="", live_symbols=set(),
    )

    assert pruned == []
    rows = await db.fetch_all("SELECT symbol FROM positions ORDER BY symbol")
    assert {r["symbol"] for r in rows} == {"BTCUSDT", "SOLUSDT"}


@pytest.mark.asyncio
async def test_prune_is_idempotent(db) -> None:
    """Calling prune twice in a row with the same live set yields the
    pruned list on the first call and an empty list on the second."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("STALEUSDT"), exchange_mode="bybit_demo")

    first = await repo.prune_positions_not_in_set(
        mode="bybit_demo", live_symbols={"BTCUSDT"},
    )
    second = await repo.prune_positions_not_in_set(
        mode="bybit_demo", live_symbols={"BTCUSDT"},
    )

    assert first == ["STALEUSDT"]
    assert second == []
