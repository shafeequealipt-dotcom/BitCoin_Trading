"""Layer 2 Defect 5 — wire previously-dead metric columns.

Operator chose to WIRE (not retire). Two parts:

1. trade_intelligence.claude_confidence — wired from
   trade_thesis.entry_xray_confidence (the only numeric confidence
   value available pre-trade; the original collector read the string
   ``consensus`` into the numeric column, which never worked).

2. cycle_metrics dead aggregate columns — signal_buy_pct, signal_sell_pct,
   signal_neutral_pct, xray_setup_type_count, regime_distribution_json,
   l2_score_p50 — wired via SQL aggregations inside cycle_tracker._flush_once.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_collector_wires_claude_confidence_from_xray() -> None:
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(side_effect=[
        {
            "leverage": 3.0, "size_usd": 50.0,
            "thesis": "long momentum", "consensus": "STRONG",
            "entry_xray_confidence": 0.84,
        },
        None,
    ])
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    group_b = await collector._collect_group_b("BTCUSDT", {"trade_id": "t1"})
    assert group_b["claude_confidence"] == 0.84


@pytest.mark.asyncio
async def test_collector_zero_xray_confidence_stays_null() -> None:
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(side_effect=[
        {
            "leverage": 3.0, "size_usd": 50.0,
            "thesis": "x", "consensus": "GOOD",
            "entry_xray_confidence": 0.0,
        },
        None,
    ])
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    group_b = await collector._collect_group_b("BTCUSDT", {"trade_id": "t1"})
    assert group_b["claude_confidence"] is None


@pytest.mark.asyncio
async def test_cycle_metrics_flush_populates_dead_columns() -> None:
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
            # Production format duality: signals + trade_intelligence use
            # Python isoformat (T-separated, microseconds, +00:00 TZ);
            # coin_regime_history uses SQLite datetime() format
            # (space-separated, no microseconds, no TZ). The D5 follow-up
            # fix at cycle_tracker.py picks the right format per query;
            # this test must seed with the production formats to catch
            # any future regression.
            mid_iso = datetime.fromtimestamp(mid_ts, tz=timezone.utc).isoformat()
            mid_sql = datetime.fromtimestamp(mid_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            for sig_type in ("buy", "buy", "sell", "neutral"):
                await db.execute(
                    "INSERT INTO signals (symbol, signal_type, confidence, "
                    "source, components, reasoning, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("BTCUSDT", sig_type, 0.5, "test", "{}", "test", mid_iso),
                )

            for regime in ("trending_up", "trending_up", "ranging"):
                await db.execute(
                    "INSERT INTO coin_regime_history (symbol, regime, "
                    "confidence, adx, choppiness, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("BTCUSDT", regime, 0.7, 25.0, 50.0, mid_sql),
                )

            repo = TradeIntelligenceRepo(db)
            for score in (0.4, 0.7, 0.9):
                await repo.save(TradeIntelligence(
                    symbol="X", direction="Buy",
                    strategy_name="x", strategy_category="y",
                    source="test", closed_by="tp",
                    entry_price=1.0, exit_price=1.01,
                    pnl_pct=1.0, pnl_usd=0.1, win=True, hold_seconds=60.0,
                    entry_score=score,
                    captured_at=mid_iso,
                ))

            tracker = CycleTracker(db=db)
            tracker._history.append(CycleSummary(
                cycle_id="c1",
                completed_at_unix=float(mid_ts),
                layer1a_ms=100, layer1b_ms=120, layer1c_ms=80, layer1d_ms=50,
                packages_ready=30, qualified_pct=42.0,
            ))
            await tracker._flush_once()

            row = await db.fetch_one(
                "SELECT signal_buy_pct, signal_sell_pct, signal_neutral_pct, "
                "xray_setup_type_count, regime_distribution_json, l2_score_p50 "
                "FROM cycle_metrics WHERE hour_ts=?", (hour_start_ts,),
            )
            assert row is not None
            assert row["signal_buy_pct"] == pytest.approx(50.0)
            assert row["signal_sell_pct"] == pytest.approx(25.0)
            assert row["signal_neutral_pct"] == pytest.approx(25.0)
            # xray_setup_type_count deferred to follow-up (no entry_setup_type
            # column on trade_intelligence today — schema work out of scope)
            assert row["xray_setup_type_count"] is None
            reg_dist = json.loads(row["regime_distribution_json"])
            assert reg_dist == {"ranging": 1, "trending_up": 2}
            assert row["l2_score_p50"] == pytest.approx(0.7)
        finally:
            await db.disconnect()
