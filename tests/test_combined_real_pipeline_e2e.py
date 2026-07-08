"""Real-project end-to-end pipeline check.

Exercises G-suite + I-suite fixes through PRODUCTION classes against a
REAL aiosqlite DB with the REAL migrations applied. No production code
is mocked or stubbed — only the network-bound adapters (Bybit / Claude /
Telegram) which are not in scope for these fixes.

Pipeline driven:

  1. DatabaseManager.connect() + run_migrations()
       — real schema (trade_thesis, daily_pnl, positions, etc.)
       — verifies I2's exchange_mode columns are applied (v29/v30/v32)
       — verifies I5's trade_thesis + daily_pnl tables exist
  2. TradeCoordinator (real) + attach_transformer (real DI pattern)
       — exercises G6 (COORD_REG fields, COORD_DUPLICATE_REGISTER)
       — exercises I2 (exchange_mode capture on TradeState)
       — exercises I5 (TRADEPLAN_PERSISTED + recover_state_from_db)
  3. ThesisManager (real, against real DB)
       — exercises G8 (THESIS_OPEN with target_pct/stop_pct/max_hold/order_id)
  4. DailyPnLManager (real, against real DB)
       — exercises I5 (_restore_today_from_db + DASHBOARD_STATE_RECOVERED)
  5. SLTPValidator (real)
       — exercises G10 (SLTP_PAIR_OK with checks=…)
  6. Simulated restart — new instances against the same DB
       — confirms I5 recovery is idempotent (existing keys preserved)
       — confirms recovery actually populates from the real DB row

Captures all emissions through a loguru sink and asserts the production
code path emits the expected tags with the expected fields.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.config.settings import Settings
from src.core.sl_tp_validator import SLTPValidator
from src.core.thesis_manager import ThesisManager
from src.core.trade_coordinator import TradeCoordinator
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.strategies.pnl_manager import DailyPnLManager


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _tags(records, *tag_prefixes):
    return [r for r in records if any(r[1].startswith(t) for t in tag_prefixes)]


@pytest.fixture
async def real_db():
    """Real aiosqlite DB with all production migrations applied."""
    fd, path = tempfile.mkstemp(prefix="ti_mcp_e2e_", suffix=".db")
    os.close(fd)
    db = DatabaseManager(path, wal_mode=True)
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()
    try:
        os.unlink(path)
        for ext in ("-shm", "-wal"):
            if os.path.exists(path + ext):
                os.unlink(path + ext)
    except OSError:
        pass


# ─── Schema confirmation: real migrations applied ───────────────────────


@pytest.mark.asyncio
async def test_real_schema_has_exchange_mode_and_recovery_tables(real_db) -> None:
    """I2: positions.exchange_mode column exists; I5: trade_thesis +
    daily_pnl tables exist with their key columns."""
    cols_positions = await real_db.fetch_all("PRAGMA table_info(positions)")
    col_names_pos = {r["name"] for r in cols_positions}
    assert "exchange_mode" in col_names_pos, (
        "I2: positions.exchange_mode missing — migration not applied"
    )

    cols_thesis = await real_db.fetch_all("PRAGMA table_info(trade_thesis)")
    col_names_thesis = {r["name"] for r in cols_thesis}
    for required in (
        "symbol", "direction", "entry_price", "size_usd",
        "leverage", "opened_at", "order_id", "exchange_mode",
    ):
        assert required in col_names_thesis, (
            f"I5: trade_thesis.{required} missing"
        )

    cols_pnl = await real_db.fetch_all("PRAGMA table_info(daily_pnl)")
    col_names_pnl = {r["name"] for r in cols_pnl}
    assert col_names_pnl, "I5: daily_pnl table missing"


# ─── End-to-end trade lifecycle on real DB ──────────────────────────────


@pytest.mark.asyncio
async def test_open_lifecycle_fires_all_emissions_against_real_db(
    real_db, loguru_sink,
) -> None:
    """Drive validate → register → plan → save-thesis through real
    classes against the real DB. Every G/I emission for the open
    pipeline must fire."""

    # G10 — real validator
    validator = SLTPValidator()
    action, _ = validator.validate_pair(
        sl_price=78000.0, tp_price=84000.0,
        entry_price=80000.0, current_price=80000.0,
        direction="Buy", symbol="BTCUSDT",
    )
    assert action == "OK"

    # G6 + I2 — real coordinator with real transformer attach
    coord = TradeCoordinator()
    xfm = MagicMock()
    xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)
    coord.register_trade(
        symbol="BTCUSDT", side="Buy", size=0.05, entry_price=80000.0,
        decision_id="d-real-1",
        source="claude_direct",
        order_id="ORD-REAL-1",
        sl_price=78000.0, tp_price=84000.0,
        leverage=5, size_usd=4000.0,
    )

    # I2 — TradeState.exchange_mode captured from transformer
    state = coord._trades["BTCUSDT"]
    assert state.exchange_mode == "bybit_demo"

    # I5 — register_trade_plan emits TRADEPLAN_PERSISTED
    plan = SimpleNamespace(
        direction="Buy", entry_price=80000.0, peak_price=80000.0,
        stop_loss_price=78000.0, target_price=84000.0,
        max_hold_minutes=120, trailing_activation_pct=2.0,
        size_tier="standard", opened_at=0,
    )
    coord.register_trade_plan("BTCUSDT", plan)

    # G8 — real ThesisManager against real DB
    mgr = ThesisManager(db=real_db)
    thesis_id = await mgr.save_thesis(
        symbol="BTCUSDT", direction="long", entry_price=80000.0,
        stop_loss_price=78000.0, take_profit_price=84000.0,
        size_usd=4000.0, leverage=5, max_hold_minutes=120,
        trailing_activation_pct=2.0, thesis="real E2E test",
        order_id="ORD-REAL-1",
    )
    assert thesis_id > 0

    # Confirm the row really hit the real DB
    row = await real_db.fetch_one(
        "SELECT symbol, exchange_mode, order_id FROM trade_thesis WHERE id=?",
        (thesis_id,),
    )
    assert row is not None
    assert row["symbol"] == "BTCUSDT"
    assert row["order_id"] == "ORD-REAL-1"

    # Verify every expected production-code emission fired
    assert _tags(loguru_sink, "SLTP_PAIR_OK"), "G10 must fire"
    assert _tags(loguru_sink, "COORD_REG "), "G6 must fire"
    assert _tags(loguru_sink, "TRADEPLAN_PERSISTED"), "I5 must fire"
    assert _tags(loguru_sink, "THESIS_OPEN "), "G8 must fire"

    # G10 checks list visible in the actual production log message
    sltp_msg = _tags(loguru_sink, "SLTP_PAIR_OK")[0][1]
    assert "checks=invalid_price,sl_equals_tp,wrong_side" in sltp_msg

    # G6 duplicate path on the same real coordinator
    coord.register_trade(
        symbol="BTCUSDT", side="Sell", size=0.10, entry_price=81000.0,
        decision_id="d-dup",
    )
    assert _tags(loguru_sink, "COORD_DUPLICATE_REGISTER"), "G6 duplicate must fire"


# ─── I5 restart-recovery against real persisted state ───────────────────


@pytest.mark.asyncio
async def test_i5_recovery_reads_real_open_thesis_after_simulated_restart(
    real_db, loguru_sink,
) -> None:
    """Persist an open thesis to the real DB, then construct a fresh
    TradeCoordinator (simulating restart) and call recover_state_from_db.
    The state must be rebuilt from the real DB row."""

    # Step 1 — pre-populate the real DB with an open thesis (simulates
    # state from before the SEGV restart).
    mgr = ThesisManager(db=real_db)
    await mgr.save_thesis(
        symbol="ETHUSDT", direction="short", entry_price=3000.0,
        stop_loss_price=3100.0, take_profit_price=2800.0,
        size_usd=600.0, leverage=3, max_hold_minutes=180,
        trailing_activation_pct=2.0, thesis="pre-restart state",
        order_id="ORD-PRE-RESTART",
    )

    # Make sure the row is committed with exchange_mode populated
    row = await real_db.fetch_one(
        "SELECT exchange_mode FROM trade_thesis WHERE symbol=?",
        ("ETHUSDT",),
    )
    assert row is not None
    # Default 'shadow' from the migration; the real worker boot path
    # writes 'bybit_demo' when relevant. Either is fine for this test.
    assert row["exchange_mode"] in {"shadow", "bybit_demo"}

    # Step 2 — fresh coordinator (post-restart). _trades is empty.
    fresh_coord = TradeCoordinator()
    assert fresh_coord._trades == {}

    # Step 3 — production recovery call against the real DB.
    restored = await fresh_coord.recover_state_from_db(real_db)
    assert restored >= 1, "I5: recover_state_from_db must rebuild the open thesis"
    assert "ETHUSDT" in fresh_coord._trades
    state = fresh_coord._trades["ETHUSDT"]
    assert state.entry_price == 3000.0
    assert state.side.lower().startswith("s")  # short / Sell

    # Step 4 — DASHBOARD_STATE_RECOVERED fired from production path
    recovered_events = _tags(loguru_sink, "DASHBOARD_STATE_RECOVERED")
    assert any("ETHUSDT" in m for _, m in recovered_events)


@pytest.mark.asyncio
async def test_i5_recovery_idempotent_against_real_live_trade(real_db) -> None:
    """A live trade already in _trades survives the recovery call —
    the DB row does not overwrite live in-memory state."""
    # Seed an open thesis in the real DB
    mgr = ThesisManager(db=real_db)
    await mgr.save_thesis(
        symbol="BTCUSDT", direction="long", entry_price=80000.0,
        stop_loss_price=78000.0, take_profit_price=84000.0,
        size_usd=4000.0, leverage=5, max_hold_minutes=120,
        trailing_activation_pct=2.0, thesis="stale",
        order_id="ORD-STALE",
    )

    coord = TradeCoordinator()
    # Live trade at a different entry price than the DB row
    coord.register_trade(
        symbol="BTCUSDT", side="Buy", size=0.05, entry_price=82500.0,
        decision_id="d-live",
    )

    restored = await coord.recover_state_from_db(real_db)
    # Live state must NOT be overwritten
    assert coord._trades["BTCUSDT"].entry_price == 82500.0
    # restored count reports rows that were ADDED, not those skipped
    assert restored == 0 or coord._trades["BTCUSDT"].brain_decision_id == "d-live"


# ─── I5 DailyPnLManager restore against real daily_pnl row ──────────────


@pytest.mark.asyncio
async def test_i5_pnl_manager_restore_against_real_db(real_db, loguru_sink) -> None:
    """Insert a daily_pnl row for today, then construct DailyPnLManager
    against the real DB and confirm initialize() restores the counters."""

    today = date.today().strftime("%Y-%m-%d")
    # Real daily_pnl schema (migrations.py:459): date, starting_equity,
    # ending_equity, realized_pnl, total_trades, wins, losses,
    # max_drawdown_pct, target_hit, halted, brain_calls, brain_cost_usd.
    await real_db.execute(
        "INSERT INTO daily_pnl "
        "(date, starting_equity, realized_pnl, total_trades, wins, losses, max_drawdown_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (today, 1000.0, 42.5, 3, 2, 1, -3.0),
    )

    # Real DailyPnLManager — no account_service so the second initialize
    # block (equity fetch) short-circuits — exactly what we want for
    # the restore test.
    settings = Settings()
    pnl_mgr = DailyPnLManager(settings, account_service=None, position_service=None, db=real_db)
    await pnl_mgr.initialize()

    # I5 expectation: restore actually populates the real counters from
    # the real DB row through the real production code path.
    assert pnl_mgr.realized_pnl == 42.5, (
        f"I5: realized_pnl not restored — got {pnl_mgr.realized_pnl}"
    )
    assert pnl_mgr._trades_today == 3
    assert pnl_mgr._wins_today == 2
    assert pnl_mgr._losses_today == 1
    # The canonical event tag must fire from production code
    assert _tags(loguru_sink, "DASHBOARD_STATE_RECOVERED")
