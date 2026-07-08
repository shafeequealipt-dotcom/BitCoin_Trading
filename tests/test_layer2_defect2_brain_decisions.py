"""Layer 2 Defect 2 — enrich claude_decisions per-trade.

Pre-fix: claude_decisions had one row per strategic_review (~per Claude call)
with new_trades_count and full_response. Per-trade decision context was only
queryable by JSON-parsing full_response across every row. The dead
brain_decisions table had zero writers and was not the active path.

Operator chose: enrich claude_decisions per-trade rather than retire or wire
the dead table. Schema v40 adds symbol + trade_directive_id + conviction
columns; DataLakeWriter.write_claude_decision accepts the new kwargs;
layer_manager._record_decision_to_data_lake fires one extra
``decision_type='trade_directive'`` row per Claude trade alongside the
existing per-review row.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_v40_adds_per_trade_columns_and_indexes() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import SCHEMA_VERSION, run_migrations

    assert SCHEMA_VERSION >= 40, "D2 introduced schema v40"
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            rows = await db.fetch_all("PRAGMA table_info(claude_decisions)")
            cols = {r["name"] for r in rows}
            for c in ("symbol", "trade_directive_id", "conviction"):
                assert c in cols
            idx = await db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name IN ('idx_claude_decisions_directive','idx_claude_decisions_symbol')"
            )
            names = {r["name"] for r in idx}
            assert "idx_claude_decisions_directive" in names
            assert "idx_claude_decisions_symbol" in names
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_strategic_review_row_legacy_contract() -> None:
    """Strategic-review row uses default values for the new per-trade columns
    (NULL in DB). Backward-compatible with all existing callers."""
    from src.core.data_lake import DataLakeWriter
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            dl = DataLakeWriter(db)
            await dl.write_claude_decision(
                decision_type="strategic_review",
                new_trades_count=2, position_actions_count=1,
                market_view="trending up", risk_level="medium",
                response_time_ms=12000, prompt_length=8000,
                full_response="...",
            )
            row = await db.fetch_one(
                "SELECT decision_type, symbol, trade_directive_id, conviction "
                "FROM claude_decisions ORDER BY ts_epoch DESC LIMIT 1"
            )
            assert row["decision_type"] == "strategic_review"
            assert row["symbol"] is None
            assert row["trade_directive_id"] is None
            assert row["conviction"] is None
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_trade_directive_row_carries_per_trade_fields() -> None:
    """Per-trade row populates symbol + trade_directive_id + conviction;
    decision_type='trade_directive'."""
    from src.core.data_lake import DataLakeWriter
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            dl = DataLakeWriter(db)
            await dl.write_claude_decision(
                decision_type="trade_directive",
                market_view="Strong momentum on BTC",
                risk_level="Buy",
                symbol="BTCUSDT",
                trade_directive_id="1716364800_BTCUSDT_0",
                conviction=0.78,
            )
            row = await db.fetch_one(
                "SELECT decision_type, symbol, trade_directive_id, conviction "
                "FROM claude_decisions WHERE symbol=?", ("BTCUSDT",),
            )
            assert row is not None
            assert row["decision_type"] == "trade_directive"
            assert row["symbol"] == "BTCUSDT"
            assert row["trade_directive_id"] == "1716364800_BTCUSDT_0"
            assert row["conviction"] == pytest.approx(0.78)
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_layer_manager_writes_one_row_per_trade() -> None:
    """LayerManager._record_decision_to_data_lake writes the legacy
    strategic_review row PLUS one trade_directive row per Claude trade."""
    from src.core.data_lake import DataLakeWriter
    from src.core.layer_manager import LayerManager
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            dl = DataLakeWriter(db)
            services = {"data_lake": dl}
            lm = LayerManager.__new__(LayerManager)
            lm.services = services

            # Synthetic plan with 3 trade directives + 1 position action
            class FakePlan:
                new_trades = [
                    {"symbol": "BTCUSDT", "direction": "Buy", "conviction": 0.8,
                     "reasoning": "momentum continuation"},
                    {"symbol": "ETHUSDT", "direction": "Sell", "conviction": 0.6,
                     "reasoning": "range top fade"},
                    {"symbol": "SOLUSDT", "direction": "Buy", "confidence": 0.55,
                     "reasoning": "breakout pending"},
                ]
                position_actions = [{"symbol": "ADAUSDT", "action": "close"}]
                market_view = "mixed regime"
                risk_level = "medium"

            lm._record_decision_to_data_lake(FakePlan(), elapsed_ms=8500,
                                             decision_type="strategic_review")
            # Tasks fire async; wait briefly
            await asyncio.sleep(0.1)

            rows = await db.fetch_all(
                "SELECT decision_type, symbol, conviction FROM claude_decisions "
                "ORDER BY decision_type"
            )
            review_rows = [r for r in rows if r["decision_type"] == "strategic_review"]
            directive_rows = [r for r in rows if r["decision_type"] == "trade_directive"]
            assert len(review_rows) == 1
            assert len(directive_rows) == 3
            assert {r["symbol"] for r in directive_rows} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
            # conviction extracted from "conviction" key with "confidence" fallback
            convs = {r["symbol"]: r["conviction"] for r in directive_rows}
            assert convs["BTCUSDT"] == pytest.approx(0.8)
            assert convs["ETHUSDT"] == pytest.approx(0.6)
            assert convs["SOLUSDT"] == pytest.approx(0.55)
        finally:
            await db.disconnect()
