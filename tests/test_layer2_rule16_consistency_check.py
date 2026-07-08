"""Layer 2 Rule 16 — periodic persistence consistency self-check.

Per IMPLEMENT_LAYER2_PERSISTENCE.md Rule 16: a periodic consistency check
that asserts every closed trade with a setup_id has a joinable record in
ensemble_votes. If any divergence is detected, log loudly.

The check runs once per hour inside cycle_tracker._flush_once (same cadence
as the cycle_metrics aggregates). On orphan detection (trade with setup_id
but zero matching votes), emits RULE16_CONSISTENCY_FAIL at ERROR with
sample orphan setup_ids; otherwise RULE16_CONSISTENCY_OK at INFO.

This guards against silent persistence regressions — e.g., a future refactor
that drops setup_id plumbing on the brain_v2 path would leave new trades
unjoinable, but this check fires within an hour rather than waiting until
Layer 3/4 analysis breaks.
"""
from __future__ import annotations

import io
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest


@contextmanager
def capture_logs():
    from loguru import logger
    buf = io.StringIO()
    hid = logger.add(buf, level="DEBUG", format="{level} | {message}")
    try:
        yield buf
    finally:
        logger.remove(hid)


@pytest.mark.asyncio
async def test_consistency_ok_when_every_trade_has_votes() -> None:
    """Two trades, each with matching ensemble_votes rows → RULE16_OK."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.tias.models import TradeIntelligence
    from src.tias.repository import TradeIntelligenceRepo

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(minute=10, second=0, microsecond=0)
            hour_start_ts = int(now.timestamp() // 3600 * 3600)
            mid_ts = hour_start_ts + 600
            mid_iso = datetime.fromtimestamp(mid_ts, tz=timezone.utc).isoformat()

            # Two trades + matching vote rows
            repo = TradeIntelligenceRepo(db)
            for sid in ("20260522T1010_BTCUSDT", "20260522T1010_ETHUSDT"):
                await repo.save(TradeIntelligence(
                    symbol=sid.split("_")[1], direction="Buy",
                    strategy_name="x", strategy_category="y",
                    source="test", closed_by="tp",
                    entry_price=1.0, exit_price=1.01,
                    pnl_pct=1.0, pnl_usd=0.1, win=True, hold_seconds=60.0,
                    setup_id=sid, captured_at=mid_iso,
                ))
                # Matching vote row
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight, reasoning) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, sid.split("_")[1], "Buy", "s1", "BUY", 0.7, 1.0, ""),
                )

            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "RULE16_CONSISTENCY_OK" in log_text
            assert "trades_with_setup_id=2" in log_text
            assert "RULE16_CONSISTENCY_FAIL" not in log_text
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_consistency_fail_on_orphan_trade() -> None:
    """One trade WITH matching votes + one trade WITHOUT votes → log FAIL
    with the orphan setup_id."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.tias.models import TradeIntelligence
    from src.tias.repository import TradeIntelligenceRepo

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(minute=10, second=0, microsecond=0)
            hour_start_ts = int(now.timestamp() // 3600 * 3600)
            mid_ts = hour_start_ts + 600
            mid_iso = datetime.fromtimestamp(mid_ts, tz=timezone.utc).isoformat()

            repo = TradeIntelligenceRepo(db)
            # Good trade — has matching votes
            await repo.save(TradeIntelligence(
                symbol="BTCUSDT", direction="Buy",
                strategy_name="x", strategy_category="y",
                source="test", closed_by="tp",
                entry_price=1.0, exit_price=1.01,
                pnl_pct=1.0, pnl_usd=0.1, win=True, hold_seconds=60.0,
                setup_id="20260522T1010_BTCUSDT", captured_at=mid_iso,
            ))
            await db.execute(
                "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                "strategy_name, vote, confidence, weight, reasoning) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("20260522T1010_BTCUSDT", "BTCUSDT", "Buy", "s1", "BUY", 0.7, 1.0, ""),
            )
            # Orphan trade — no matching votes (simulates persistence regression)
            await repo.save(TradeIntelligence(
                symbol="ETHUSDT", direction="Sell",
                strategy_name="x", strategy_category="y",
                source="test", closed_by="tp",
                entry_price=1.0, exit_price=0.99,
                pnl_pct=-1.0, pnl_usd=-0.1, win=False, hold_seconds=60.0,
                setup_id="20260522T1010_ETHUSDT_ORPHAN", captured_at=mid_iso,
            ))

            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "RULE16_CONSISTENCY_FAIL" in log_text
            assert "ERROR" in log_text  # Loud per Rule 16
            assert "orphan_trades_without_votes=1" in log_text
            assert "20260522T1010_ETHUSDT_ORPHAN" in log_text
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_consistency_check_handles_zero_trades_gracefully() -> None:
    """An hour with no trade_intelligence rows → RULE16_OK with count 0;
    no orphan log."""
    from src.core.cycle_tracker import CycleSummary, CycleTracker
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            now = datetime.now(timezone.utc).replace(minute=10, second=0, microsecond=0)
            mid_ts = int(now.timestamp() // 3600 * 3600) + 600

            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1", completed_at_unix=float(mid_ts),
                layer1a_ms=80, layer1b_ms=100, layer1c_ms=70, layer1d_ms=40,
                packages_ready=28, qualified_pct=44.0,
            ))
            with capture_logs() as buf:
                await tracker._flush_once()
            log_text = buf.getvalue()
            assert "RULE16_CONSISTENCY_OK" in log_text
            assert "trades_with_setup_id=0" in log_text
            assert "RULE16_CONSISTENCY_FAIL" not in log_text
        finally:
            await db.disconnect()
