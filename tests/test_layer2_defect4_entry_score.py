"""Layer 2 Defect 4 — entry_score sourced from apex_confidence.

Pre-fix: trade_intelligence.entry_score was 100% NULL across 2,345 rows.
The upstream signal_score path is broken (strategy_trades.score is hardcoded
100 at strategy_worker.py:2904; trade.get("score") is never set), so the
collector's existing fallback chain never produced a value.

Fix: when the existing signal_score path produces nothing, fall back to
record["apex_confidence"] (already computed by APEX optimizer, plumbed
through TradeCoordinator.TradeState, exposed on the close-record dict).
Zero values are rejected as "APEX never ran" — NULL is the honest absence
per Rule 5.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_entry_score_falls_back_to_apex_confidence() -> None:
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    record = {
        "trade_id": "test-1",
        "symbol": "BTCUSDT",
        # signal_score absent (the broken upstream path)
        "apex_confidence": 0.78,
    }
    group_b = await collector._collect_group_b("BTCUSDT", record)
    assert group_b["entry_score"] == 0.78


@pytest.mark.asyncio
async def test_entry_score_zero_apex_confidence_stays_null() -> None:
    """apex_confidence=0 indicates APEX never ran for this trade. NULL is
    the honest value (Rule 5 — no fake-zero placeholder)."""
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    record = {
        "trade_id": "test-2",
        "symbol": "BTCUSDT",
        "apex_confidence": 0.0,
    }
    group_b = await collector._collect_group_b("BTCUSDT", record)
    assert group_b["entry_score"] is None


@pytest.mark.asyncio
async def test_entry_score_signal_score_wins_over_apex_fallback() -> None:
    """If a future caller plumbs signal_score, it must take precedence
    over the apex_confidence fallback (no behavior regression)."""
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    record = {
        "trade_id": "test-3",
        "symbol": "BTCUSDT",
        "signal_score": 0.91,
        "apex_confidence": 0.55,
    }
    group_b = await collector._collect_group_b("BTCUSDT", record)
    assert group_b["entry_score"] == 0.91


@pytest.mark.asyncio
async def test_entry_score_both_missing_stays_null() -> None:
    """No signal_score AND no apex_confidence → NULL (honest absence)."""
    from src.tias.collector import TradeContextCollector

    db = MagicMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    collector = TradeContextCollector(db=db, services={})
    record = {"trade_id": "test-4", "symbol": "BTCUSDT"}
    group_b = await collector._collect_group_b("BTCUSDT", record)
    assert group_b["entry_score"] is None
