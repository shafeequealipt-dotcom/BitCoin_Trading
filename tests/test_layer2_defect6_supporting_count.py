"""Layer 2 Defect 6 — per-trade supporting/opposing strategy count persistence.

Pre-fix: the herding-finding metric (count of strategies that agreed with the
trade direction) had to be reconstructed from STRAT_VOTE_TRACE logs which only
fire for STRONG-consensus trades. The trade_intelligence row had no numeric
field for it; the only proxy was the formatted ``ensemble_votes`` TEXT column.

Fix: schema v37 adds numeric ``supporting_count`` and ``opposing_count``
columns. strategy_worker reads them from EnsembleStateCache at register_trade
time; TradeCoordinator stores them on TradeState; on_trade_closed forwards
them via the close-record dict; collector populates the new TradeIntelligence
fields via the Phase 3 override path; repository INSERTs them via asdict.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_v37_adds_supporting_and_opposing_count_columns() -> None:
    """After migrations run, trade_intelligence must carry both numeric
    vote-count columns."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import SCHEMA_VERSION, run_migrations

    assert SCHEMA_VERSION >= 37, (
        "D6 introduced schema v37; SCHEMA_VERSION must be >= 37."
    )

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            rows = await db.fetch_all("PRAGMA table_info(trade_intelligence)")
            cols = {r["name"] for r in rows}
            for c in ("supporting_count", "opposing_count"):
                assert c in cols, (
                    f"D6 v37 should add {c!r} to trade_intelligence "
                    f"(post-fix columns: {sorted(c for c in cols if c.startswith(('s','o')))})"
                )
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_round_trip_preserves_vote_counts() -> None:
    """Save a TradeIntelligence with the new fields and read back; the
    integer values must survive."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.tias.models import TradeIntelligence
    from src.tias.repository import TradeIntelligenceRepo

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            repo = TradeIntelligenceRepo(db)
            ti = TradeIntelligence(
                symbol="BTCUSDT", direction="Buy",
                strategy_name="claude_trader",
                strategy_category="claude_direct",
                source="claude_direct", closed_by="tp",
                entry_price=65000.0, exit_price=66300.0,
                pnl_pct=2.0, pnl_usd=12.0, win=True, hold_seconds=180.0,
                supporting_count=12, opposing_count=3,
            )
            rid = await repo.save(ti)
            assert rid > 0
            row = await db.fetch_one(
                "SELECT supporting_count, opposing_count FROM trade_intelligence "
                "WHERE id=?", (rid,),
            )
            assert row is not None
            assert row["supporting_count"] == 12
            assert row["opposing_count"] == 3
        finally:
            await db.disconnect()


def test_tradestate_carries_vote_counts() -> None:
    """TradeCoordinator.TradeState must expose the new fields with safe
    defaults so legacy callers keep working."""
    from src.core.trade_coordinator import TradeState
    ts = TradeState(symbol="ETHUSDT", strategy_name="x", strategy_category="y")
    assert ts.supporting_count is None
    assert ts.opposing_count is None
    ts.supporting_count = 7
    ts.opposing_count = 2
    assert ts.supporting_count == 7
    assert ts.opposing_count == 2


@pytest.mark.asyncio
async def test_collector_reads_vote_counts_from_record() -> None:
    """The Phase 3 override path in _collect_group_b must populate
    supporting_count / opposing_count when the close-record dict
    carries them."""
    from unittest.mock import AsyncMock, MagicMock

    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)  # no thesis / strategy_trades row
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    record = {
        "trade_id": "test-trade-1",
        "symbol": "BTCUSDT",
        "supporting_count": 11,
        "opposing_count": 4,
    }
    group_b = await collector._collect_group_b("BTCUSDT", record)
    assert group_b["supporting_count"] == 11
    assert group_b["opposing_count"] == 4
