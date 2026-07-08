"""Unit tests for HIGH-2 (exchange_mode columns on orders / account_snapshots / trade_history).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md HIGH-2.

Pre-fix: three tables (orders, account_snapshots, trade_history) lacked
the exchange_mode column. trade_intelligence had been migrated by P4
already (audit assumption was stale). Without the column, cross-mode
reads couldn't filter cleanly: DeepSeek learned from a mode-blind
dataset, MCP get_trade_history mixed modes, equity dashboards lost
disambiguation.

Fix: schema v30 ALTER TABLE + idempotent backfill + writer signature
update. Backfill heuristic per table:
- orders: created_at >= '2026-05-08T11:19:26' → bybit_demo
- account_snapshots: updated_at >= '2026-05-08T11:19:26' → bybit_demo
- trade_history: trade_id LIKE 'bd-%' → bybit_demo
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Group 1 — schema migration semantics
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_v30_adds_exchange_mode_to_three_tables() -> None:
    """The migration list at schema v30 adds exchange_mode to orders,
    account_snapshots, and trade_history."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import SCHEMA_VERSION, run_migrations

    # HIGH-2 introduced schema v30. Subsequent fix series have bumped
    # SCHEMA_VERSION further (v31 for I1 of cascade-fix series — ASC
    # index on fear_greed_index; v32 for I4 — exchange_mode on
    # positions). The HIGH-2 invariants (three tables gain
    # exchange_mode) must remain true at the current SCHEMA_VERSION
    # regardless, which is what the rest of this test asserts.
    assert SCHEMA_VERSION >= 30, "HIGH-2 invariants apply from schema v30 onward"

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)

            # All three tables now have exchange_mode column
            for table in ("orders", "account_snapshots", "trade_history"):
                rows = await db.fetch_all(f"PRAGMA table_info({table})")
                cols = {r["name"] for r in rows}
                assert "exchange_mode" in cols, f"{table} missing exchange_mode after v30"

            # And trade_intelligence still has it (P4 migration preserved)
            rows = await db.fetch_all("PRAGMA table_info(trade_intelligence)")
            cols = {r["name"] for r in rows}
            assert "exchange_mode" in cols
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_migrations_are_idempotent() -> None:
    """Running migrations twice must not error and must not duplicate
    columns. ALTER TABLE skips on existing columns via PRAGMA pre-check."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)
            # Second run should be a no-op
            await run_migrations(db)
            # Schema still has the column (and only one)
            rows = await db.fetch_all("PRAGMA table_info(orders)")
            mode_cols = [r for r in rows if r["name"] == "exchange_mode"]
            assert len(mode_cols) == 1
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_orders_backfill_post_cutover_to_bybit_demo() -> None:
    """Existing orders rows post-cutover (created_at >= 2026-05-08T11:19:26)
    are backfilled to 'bybit_demo'. Tests the backfill SQL directly
    since run_migrations short-circuits when schema_version is current."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)

            # Insert one pre-cutover order and one post-cutover order
            await db.execute(
                "INSERT INTO orders (order_id, symbol, side, order_type, qty, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                ("pre", "X", "Buy", "Market", 1.0,
                 "2026-05-01T00:00:00+00:00", "2026-05-01T00:00:00+00:00"),
            )
            await db.execute(
                "INSERT INTO orders (order_id, symbol, side, order_type, qty, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                ("post", "X", "Buy", "Market", 1.0,
                 "2026-05-09T00:00:00+00:00", "2026-05-09T00:00:00+00:00"),
            )

            # Apply the same idempotent backfill SQL the migration uses.
            # (Re-running run_migrations short-circuits because schema is
            # already at v30.)
            await db.execute(
                "UPDATE orders SET exchange_mode='bybit_demo' "
                "WHERE exchange_mode='shadow' AND created_at >= '2026-05-08T11:19:26'"
            )

            row_pre = await db.fetch_one("SELECT exchange_mode FROM orders WHERE order_id='pre'")
            row_post = await db.fetch_one("SELECT exchange_mode FROM orders WHERE order_id='post'")
            assert row_pre["exchange_mode"] == "shadow"
            assert row_post["exchange_mode"] == "bybit_demo"
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_trade_history_backfill_bd_prefix() -> None:
    """Existing trade_history rows with bd-* prefix are tagged bybit_demo."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)
            # Insert two rows
            await db.execute(
                "INSERT INTO trade_history (trade_id, symbol, side, entry_price, "
                "exit_price, qty, pnl, pnl_pct, entry_time) VALUES (?,?,?,?,?,?,?,?,?)",
                ("bd-X-close", "X", "Sell", 1.0, 0.95, 100.0, 5.0, 5.0,
                 "2026-05-09T00:00:00+00:00"),
            )
            await db.execute(
                "INSERT INTO trade_history (trade_id, symbol, side, entry_price, "
                "exit_price, qty, pnl, pnl_pct, entry_time) VALUES (?,?,?,?,?,?,?,?,?)",
                ("legacy", "Y", "Buy", 1.0, 1.05, 100.0, 5.0, 5.0,
                 "2026-04-01T00:00:00+00:00"),
            )
            # Apply the backfill SQL directly (run_migrations skips when
            # schema_version is already current).
            await db.execute(
                "UPDATE trade_history SET exchange_mode='bybit_demo' "
                "WHERE exchange_mode='shadow' AND trade_id LIKE 'bd-%'"
            )
            r1 = await db.fetch_one("SELECT exchange_mode FROM trade_history WHERE trade_id='bd-X-close'")
            r2 = await db.fetch_one("SELECT exchange_mode FROM trade_history WHERE trade_id='legacy'")
            assert r1["exchange_mode"] == "bybit_demo"
            assert r2["exchange_mode"] == "shadow"
        finally:
            await db.disconnect()


# ──────────────────────────────────────────────────────────────────────
# Group 2 — writer signature updates
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_order_with_exchange_mode_writes_correct_value() -> None:
    """trading_repo.save_order(order, exchange_mode='bybit_demo') writes
    'bybit_demo' into the exchange_mode column."""
    from src.core.types import Order, OrderStatus, OrderType, Side
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.trading_repo import TradingRepository

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)
            repo = TradingRepository(db)
            order = Order(
                order_id="oid-1", symbol="X", side=Side.BUY,
                order_type=OrderType.MARKET, price=100.0, qty=1.0,
                status=OrderStatus.FILLED, filled_qty=1.0,
                avg_fill_price=100.0,
            )
            await repo.save_order(order, exchange_mode="bybit_demo")
            row = await db.fetch_one("SELECT exchange_mode FROM orders WHERE order_id='oid-1'")
            assert row["exchange_mode"] == "bybit_demo"
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_save_order_without_exchange_mode_falls_back_to_default() -> None:
    """Legacy callers (no exchange_mode kwarg) still work — column DEFAULT
    'shadow' applies. Back-compat preserved."""
    from src.core.types import Order, OrderStatus, OrderType, Side
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.trading_repo import TradingRepository

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)
            repo = TradingRepository(db)
            order = Order(
                order_id="oid-2", symbol="X", side=Side.SELL,
                order_type=OrderType.MARKET, price=50.0, qty=1.0,
                status=OrderStatus.FILLED, filled_qty=1.0, avg_fill_price=50.0,
            )
            await repo.save_order(order)  # no exchange_mode kwarg
            row = await db.fetch_one("SELECT exchange_mode FROM orders WHERE order_id='oid-2'")
            assert row["exchange_mode"] == "shadow"  # column DEFAULT
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_save_trade_with_exchange_mode_writes_correct_value() -> None:
    """trading_repo.save_trade(trade, exchange_mode='bybit_demo') writes
    'bybit_demo' into the trade_history.exchange_mode column."""
    from src.core.types import Side, TradeRecord
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.trading_repo import TradingRepository

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)
            repo = TradingRepository(db)
            trade = TradeRecord(
                trade_id="bd-X-12345", symbol="X", side=Side.BUY,
                entry_price=100.0, exit_price=101.0, qty=1.0,
                pnl=1.0, pnl_pct=1.0,
            )
            await repo.save_trade(trade, exchange_mode="bybit_demo")
            row = await db.fetch_one("SELECT exchange_mode FROM trade_history WHERE trade_id='bd-X-12345'")
            assert row["exchange_mode"] == "bybit_demo"
        finally:
            await db.disconnect()


# ──────────────────────────────────────────────────────────────────────
# Group 3 — _save_account_snapshot exchange_mode plumbing
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_account_snapshot_with_exchange_mode() -> None:
    """transformer._save_account_snapshot(balance, exchange_mode='bybit_demo')
    writes the column correctly."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    class _FakeTransformer:
        def __init__(self, db):
            self._db = db

    # Borrow the actual method by binding it to a fake transformer
    from src.core.transformer import Transformer

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)
            fake = _FakeTransformer(db)
            balance = MagicMock(
                total_equity=5000.0, available_balance=4500.0,
                used_margin=500.0, unrealized_pnl=0.0, margin_level_pct=0.0,
            )
            await Transformer._save_account_snapshot(fake, balance, exchange_mode="bybit_demo")
            row = await db.fetch_one(
                "SELECT exchange_mode FROM account_snapshots ORDER BY id DESC LIMIT 1"
            )
            assert row["exchange_mode"] == "bybit_demo"
        finally:
            await db.disconnect()
