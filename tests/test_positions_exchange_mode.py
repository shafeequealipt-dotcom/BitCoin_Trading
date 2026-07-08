"""Issue 4 of cascade-fix series — positions table exchange_mode column
plus BybitDemoPositionService.get_positions persistence parity.

Pins:
  1. Schema migration v32 adds the ``exchange_mode`` column with
     DEFAULT 'shadow' and a supporting index.
  2. ``TradingRepository.save_position`` honours the new
     ``exchange_mode`` kwarg and writes the column.
  3. ``BybitDemoPositionService.get_positions`` calls save_position
     once per non-zero open position with ``exchange_mode='bybit_demo'``.
  4. ``BybitDemoPositionService.close_position`` save_position site
     also passes ``exchange_mode='bybit_demo'``.
  5. ``PositionService.get_positions`` (live) passes
     ``exchange_mode='shadow'``.
  6. Source-level pin against future regressions on all four call
     sites and the migration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.types import Position, Side
from src.database.connection import DatabaseManager
from src.database.repositories.trading_repo import TradingRepository


def _pos(symbol: str = "BTCUSDT", size: float = 1.0) -> Position:
    return Position(
        symbol=symbol, side=Side.BUY, size=size,
        entry_price=50_000.0, mark_price=50_100.0,
        unrealized_pnl=10.0, realized_pnl=0.0, leverage=10,
        liquidation_price=45_000.0, stop_loss=49_000.0, take_profit=51_000.0,
        updated_at=datetime.now(UTC),
    )


@pytest.fixture()
async def db(tmp_path):
    """Spin up DB with the v32 positions schema (column + index)."""
    db_path = tmp_path / "positions_test.db"
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
async def test_save_position_writes_exchange_mode_when_passed(db) -> None:
    repo = TradingRepository(db)
    await repo.save_position(_pos("ETHUSDT"), exchange_mode="bybit_demo")
    row = await db.fetch_one(
        "SELECT exchange_mode FROM positions WHERE symbol = ?", ("ETHUSDT",),
    )
    assert row is not None
    assert row["exchange_mode"] == "bybit_demo"


@pytest.mark.asyncio
async def test_save_position_legacy_path_uses_default(db) -> None:
    """Legacy callers that don't pass the kwarg get the column DEFAULT
    of 'shadow' — preserves back-compat."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"))  # no kwarg
    row = await db.fetch_one(
        "SELECT exchange_mode FROM positions WHERE symbol = ?", ("BTCUSDT",),
    )
    assert row is not None
    assert row["exchange_mode"] == "shadow"


@pytest.mark.asyncio
async def test_save_position_zero_size_deletes_row(db) -> None:
    """Setting size==0 deletes the row regardless of exchange_mode."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    # Now zero it.
    zero = _pos("BTCUSDT", size=0.0)
    await repo.save_position(zero, exchange_mode="bybit_demo")
    row = await db.fetch_one(
        "SELECT * FROM positions WHERE symbol = ?", ("BTCUSDT",),
    )
    assert row is None  # deleted


@pytest.mark.asyncio
async def test_mode_distribution_query_returns_per_mode_counts(db) -> None:
    """Operator-style audit query — verify the index supports it."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("ETHUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("LINKUSDT"), exchange_mode="shadow")
    rows = await db.fetch_all(
        "SELECT exchange_mode, COUNT(*) AS n FROM positions "
        "GROUP BY exchange_mode ORDER BY exchange_mode"
    )
    by_mode = {r["exchange_mode"]: r["n"] for r in rows}
    assert by_mode == {"bybit_demo": 2, "shadow": 1}


@pytest.mark.asyncio
async def test_bybit_demo_get_positions_persists_each_open_position() -> None:
    """The fix: BybitDemoPositionService.get_positions now calls
    save_position with exchange_mode='bybit_demo' for every non-zero
    position. This test mocks the HTTP client + repo and asserts the
    correct save calls fire."""
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

    # Mock client.get returns two open positions and one zero-size.
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value={
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT", "side": "Buy",
                    "size": "0.5", "avgPrice": "50000",
                    "markPrice": "50100", "unrealisedPnl": "50",
                    "cumRealisedPnl": "0", "leverage": "10",
                    "liqPrice": "45000", "stopLoss": "0",
                    "takeProfit": "0", "updatedTime": "0",
                },
                {
                    "symbol": "ETHUSDT", "side": "Buy",
                    "size": "1.0", "avgPrice": "3000",
                    "markPrice": "3010", "unrealisedPnl": "10",
                    "cumRealisedPnl": "0", "leverage": "10",
                    "liqPrice": "2700", "stopLoss": "0",
                    "takeProfit": "0", "updatedTime": "0",
                },
                {
                    "symbol": "OLDUSDT", "side": "Buy",
                    "size": "0",  # zero — must be skipped (no save)
                    "avgPrice": "1", "markPrice": "1",
                    "unrealisedPnl": "0", "cumRealisedPnl": "0",
                    "leverage": "1", "liqPrice": "0",
                    "stopLoss": "0", "takeProfit": "0",
                    "updatedTime": "0",
                },
            ],
        },
    })

    fake_repo = MagicMock()
    fake_repo.save_position = AsyncMock()

    svc = BybitDemoPositionService(fake_client, trading_repo=fake_repo)
    positions = await svc.get_positions()

    # Two non-zero positions returned (zero-size filtered out).
    assert len(positions) == 2
    syms = {p.symbol for p in positions}
    assert syms == {"BTCUSDT", "ETHUSDT"}

    # save_position fired exactly twice — once per non-zero position
    # — each with exchange_mode='bybit_demo'.
    assert fake_repo.save_position.await_count == 2
    for call in fake_repo.save_position.await_args_list:
        # Kwargs include exchange_mode='bybit_demo'.
        assert call.kwargs.get("exchange_mode") == "bybit_demo"


@pytest.mark.asyncio
async def test_bybit_demo_get_positions_swallows_save_failure() -> None:
    """The save_position failure path must NOT cause get_positions to
    fail — the watchdog and other in-memory consumers depend on the
    return value. The failure logs but the positions list is still
    returned intact."""
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value={
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT", "side": "Buy",
                    "size": "0.5", "avgPrice": "50000",
                    "markPrice": "50100", "unrealisedPnl": "50",
                    "cumRealisedPnl": "0", "leverage": "10",
                    "liqPrice": "45000", "stopLoss": "0",
                    "takeProfit": "0", "updatedTime": "0",
                },
            ],
        },
    })

    fake_repo = MagicMock()
    fake_repo.save_position = AsyncMock(side_effect=RuntimeError("DB down"))

    svc = BybitDemoPositionService(fake_client, trading_repo=fake_repo)
    positions = await svc.get_positions()
    # Position still returned despite save failure.
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"


def test_source_pin_bybit_demo_get_positions_persists() -> None:
    """Source-level pin — Issue 4 fix must remain present."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/bybit_demo/bybit_demo_adapter.py", encoding="utf-8",
    ).read()
    # The save_position call inside the get_positions loop must exist
    # with the bybit_demo tag. Two save_position calls in the file:
    # one in get_positions (this fix) and one in close_position
    # (existing).
    assert src.count("save_position(") >= 2, (
        "Expected ≥2 save_position calls in adapter "
        "(get_positions + close_position); Issue 4 cascade-fix may "
        "have regressed."
    )
    assert 'exchange_mode="bybit_demo"' in src, (
        "BybitDemoPositionService must pass "
        'exchange_mode="bybit_demo" to save_position. Issue 4 '
        "cascade-fix regressed."
    )


def test_source_pin_live_position_service_passes_shadow() -> None:
    """Source-level pin — live PositionService passes
    exchange_mode='shadow'."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/trading/services/position_service.py", encoding="utf-8",
    ).read()
    assert 'save_position(pos, exchange_mode="shadow")' in src, (
        "Live PositionService.get_positions must pass "
        'exchange_mode="shadow" to save_position.'
    )
    assert 'save_position(position, exchange_mode="shadow")' in src, (
        "Live PositionService.close_position must pass "
        'exchange_mode="shadow" to save_position.'
    )


def test_source_pin_migration_v32_includes_column_and_index() -> None:
    """Source-level pin — schema v32 adds exchange_mode + idx.

    The I4 cascade-fix introduced ``exchange_mode`` on the positions
    table at SCHEMA_VERSION 32. The version constant legitimately keeps
    climbing as later migrations land (e.g. layer2/D2 bumped it to 40),
    so this pin asserts a numeric LOWER BOUND (>= 32) parsed from the
    source rather than an exact-equality on the literal "= 32". The real
    intent of this pin — that the exchange_mode column and the
    idx_positions_mode index introduced by I4 are still present — is
    preserved by the two assertions below.
    """
    import re

    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/database/migrations.py", encoding="utf-8",
    ).read()
    m = re.search(r"^SCHEMA_VERSION\s*=\s*(\d+)", src, re.MULTILINE)
    assert m is not None, "SCHEMA_VERSION assignment not found in migrations.py"
    schema_version = int(m.group(1))
    assert schema_version >= 32, (
        f"SCHEMA_VERSION must be at least 32 for I4 cascade-fix, "
        f"got {schema_version}."
    )
    assert (
        "ALTER TABLE positions ADD COLUMN exchange_mode" in src
    ), "I4 migration must ADD COLUMN exchange_mode on positions."
    assert "idx_positions_mode" in src, (
        "I4 migration must create idx_positions_mode index."
    )


@pytest.mark.asyncio
async def test_save_position_bybit_demo_then_overwrite_with_shadow_keeps_one_row(db) -> None:
    """Same symbol, then two writes — second wins. PK is symbol so
    cross-mode collision overwrites. This pins the current behavior
    explicitly so a future composite-PK migration would surface as a
    test failure rather than a silent semantic shift."""
    repo = TradingRepository(db)
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="bybit_demo")
    await repo.save_position(_pos("BTCUSDT"), exchange_mode="shadow")
    row = await db.fetch_one(
        "SELECT exchange_mode FROM positions WHERE symbol = ?", ("BTCUSDT",),
    )
    assert row is not None
    assert row["exchange_mode"] == "shadow"  # latest write wins
