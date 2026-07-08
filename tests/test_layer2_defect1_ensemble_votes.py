"""Layer 2 Defect 1 — per-cycle ensemble_votes batched persistence.

Pre-fix: the ensemble_votes table (schema since v24) had ZERO INSERT
statements anywhere in src/. The setup_id NOT NULL column had no generator.
Per-strategy votes were computed in memory and discarded after each cycle.

Fix: EnsembleVoter.vote generates setup_id = f"{cycle_iso}_{symbol}" and
attaches it to the EnsembleResult. EnsembleVoter.persist_votes is a batched
executemany helper that writes all per-strategy vote rows in a single DB
roundtrip. strategy_worker calls it after vote_batch. The same setup_id
flows through TradeCoordinator + the close-record dict + the collector to
trade_intelligence.setup_id so JOIN ON setup_id ties votes to outcomes.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_v39_adds_setup_id_column_and_indexes() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import SCHEMA_VERSION, run_migrations

    assert SCHEMA_VERSION >= 39, "D1 introduced schema v39; SCHEMA_VERSION must be >= 39"
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            rows = await db.fetch_all("PRAGMA table_info(trade_intelligence)")
            cols = {r["name"] for r in rows}
            assert "setup_id" in cols
            # Indexes (created for the join performance)
            idx = await db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name IN ('idx_ensemble_votes_setup','idx_trade_intel_setup')"
            )
            names = {r["name"] for r in idx}
            assert "idx_ensemble_votes_setup" in names
            assert "idx_trade_intel_setup" in names
        finally:
            await db.disconnect()


def test_ensemble_result_carries_setup_id() -> None:
    """EnsembleResult dataclass must expose the setup_id field with empty
    string default (legacy callers)."""
    from src.strategies.models.signal_types import (
        EnsembleResult, RawSignal, ScoredSetup,
    )
    from src.core.types import Side

    rs = RawSignal(
        strategy_name="x", strategy_category="y", symbol="BTCUSDT",
        direction=Side.BUY, entry_price=100.0,
        suggested_stop_loss=98.0, suggested_take_profit=104.0,
        timeframe="5",
    )
    ss = ScoredSetup(
        raw_signal=rs, total_score=70.0,
        base_score=70.0, confluence_score=0.0, context_score=0.0,
        quality_score=0.0, grade="B",
    )
    er = EnsembleResult(scored_setup=ss, setup_id="20260522T120000_BTCUSDT")
    assert er.setup_id == "20260522T120000_BTCUSDT"
    er_default = EnsembleResult(scored_setup=ss)
    assert er_default.setup_id == ""


def test_ensemble_state_cache_exposes_setup_id() -> None:
    """EnsembleStateCache.record stores setup_id; get_current_consensus
    returns it so strategy_worker can plumb it at register_trade time."""
    from src.strategies.ensemble import EnsembleStateCache

    cache = EnsembleStateCache()
    cache.record(
        symbol="ETHUSDT", buy_votes=5.0, sell_votes=1.0, neutral_votes=0.0,
        setup_id="20260522T120500_ETHUSDT",
    )
    res = cache.get_current_consensus("ETHUSDT")
    assert res is not None
    assert res["setup_id"] == "20260522T120500_ETHUSDT"


@pytest.mark.asyncio
async def test_persist_votes_writes_batched_rows() -> None:
    """EnsembleVoter.persist_votes must call db.executemany with one row
    per EnsembleVote across all results, all keyed by their setup_id."""
    from src.core.types import Side
    from src.strategies.ensemble import EnsembleVoter
    from src.strategies.models.signal_types import (
        EnsembleResult, EnsembleVote, RawSignal, ScoredSetup,
    )

    rs = RawSignal(
        strategy_name="originator", strategy_category="y", symbol="BTCUSDT",
        direction=Side.BUY, entry_price=100.0,
        suggested_stop_loss=98.0, suggested_take_profit=104.0,
        timeframe="5",
    )
    ss = ScoredSetup(
        raw_signal=rs, total_score=80.0,
        base_score=80.0, confluence_score=0.0, context_score=0.0,
        quality_score=0.0, grade="A",
    )
    votes = [
        EnsembleVote(strategy_name="s1", vote="BUY", confidence=0.7, weight=1.0),
        EnsembleVote(strategy_name="s2", vote="BUY", confidence=0.6, weight=1.0),
        EnsembleVote(strategy_name="s3", vote="SELL", confidence=0.5, weight=1.0),
    ]
    er = EnsembleResult(
        scored_setup=ss, votes=votes,
        setup_id="20260522T120000_BTCUSDT",
    )
    db = MagicMock()
    db.executemany = AsyncMock(return_value=None)

    written = await EnsembleVoter.persist_votes(db, [er])
    assert written == 3
    db.executemany.assert_called_once()
    sql, rows = db.executemany.call_args.args
    assert "INSERT INTO ensemble_votes" in sql
    assert len(rows) == 3
    # Every row carries the setup_id and matches the EnsembleVote ordering
    assert all(r[0] == "20260522T120000_BTCUSDT" for r in rows)
    assert {r[3] for r in rows} == {"s1", "s2", "s3"}


@pytest.mark.asyncio
async def test_persist_votes_failure_is_non_fatal() -> None:
    """Rule 7 — persistence failure must log loud and return 0, NOT raise.
    Trading must continue even if the write fails."""
    from src.core.types import Side
    from src.strategies.ensemble import EnsembleVoter
    from src.strategies.models.signal_types import (
        EnsembleResult, EnsembleVote, RawSignal, ScoredSetup,
    )

    rs = RawSignal(
        strategy_name="orig", strategy_category="y", symbol="X",
        direction=Side.BUY, entry_price=1.0,
        suggested_stop_loss=0.98, suggested_take_profit=1.04,
        timeframe="5",
    )
    er = EnsembleResult(
        scored_setup=ScoredSetup(
            raw_signal=rs, total_score=70.0,
            base_score=70.0, confluence_score=0.0, context_score=0.0,
            quality_score=0.0, grade="B",
        ),
        votes=[EnsembleVote(strategy_name="s1", vote="BUY", confidence=0.5, weight=1.0)],
        setup_id="abc",
    )
    db = MagicMock()
    db.executemany = AsyncMock(side_effect=RuntimeError("DB locked"))
    # Must not raise
    written = await EnsembleVoter.persist_votes(db, [er])
    assert written == 0


@pytest.mark.asyncio
async def test_persist_votes_empty_input_is_zero() -> None:
    from src.strategies.ensemble import EnsembleVoter

    db = MagicMock()
    db.executemany = AsyncMock(return_value=None)
    written = await EnsembleVoter.persist_votes(db, [])
    assert written == 0
    db.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_collector_reads_setup_id_from_record() -> None:
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    record = {
        "trade_id": "t1", "symbol": "BTCUSDT",
        "setup_id": "20260522T120000_BTCUSDT",
    }
    group_b = await collector._collect_group_b("BTCUSDT", record)
    assert group_b["setup_id"] == "20260522T120000_BTCUSDT"


@pytest.mark.asyncio
async def test_full_round_trip_join() -> None:
    """End-to-end: write votes via persist_votes, write a trade_intelligence
    row with matching setup_id, JOIN returns the per-strategy breakdown."""
    from src.core.types import Side
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.ensemble import EnsembleVoter
    from src.strategies.models.signal_types import (
        EnsembleResult, EnsembleVote, RawSignal, ScoredSetup,
    )
    from src.tias.models import TradeIntelligence
    from src.tias.repository import TradeIntelligenceRepo

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)

            # Step 1 — persist votes
            rs = RawSignal(
                strategy_name="orig", strategy_category="y", symbol="BTCUSDT",
                direction=Side.BUY, entry_price=65000.0,
                suggested_stop_loss=63700.0, suggested_take_profit=67600.0,
                timeframe="5",
            )
            sid = "20260522T120000_BTCUSDT"
            er = EnsembleResult(
                scored_setup=ScoredSetup(
            raw_signal=rs, total_score=70.0,
            base_score=70.0, confluence_score=0.0, context_score=0.0,
            quality_score=0.0, grade="B",
        ),
                votes=[
                    EnsembleVote(strategy_name="A1", vote="BUY", confidence=0.8, weight=1.0),
                    EnsembleVote(strategy_name="B2", vote="BUY", confidence=0.6, weight=1.0),
                    EnsembleVote(strategy_name="C3", vote="SELL", confidence=0.4, weight=1.0),
                ],
                setup_id=sid,
            )
            written = await EnsembleVoter.persist_votes(db, [er])
            assert written == 3

            # Step 2 — persist trade_intelligence with matching setup_id
            repo = TradeIntelligenceRepo(db)
            await repo.save(TradeIntelligence(
                symbol="BTCUSDT", direction="Buy",
                strategy_name="claude_trader",
                strategy_category="claude_direct",
                source="claude_direct", closed_by="tp",
                entry_price=65000.0, exit_price=66000.0,
                pnl_pct=1.5, pnl_usd=8.0, win=True, hold_seconds=120.0,
                setup_id=sid,
            ))

            # Step 3 — JOIN: per-trade per-strategy breakdown
            rows = await db.fetch_all(
                """
                SELECT ti.symbol, ti.pnl_pct, ev.strategy_name, ev.vote, ev.confidence
                FROM trade_intelligence ti
                JOIN ensemble_votes ev ON ev.setup_id = ti.setup_id
                ORDER BY ev.strategy_name
                """
            )
            assert len(rows) == 3
            assert {r["strategy_name"] for r in rows} == {"A1", "B2", "C3"}
            assert all(r["symbol"] == "BTCUSDT" for r in rows)
        finally:
            await db.disconnect()
