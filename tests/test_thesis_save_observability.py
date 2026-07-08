"""Observability G8 — THESIS_OPEN field completeness.

The audit (2026-05-13) claimed THESIS_SAVE fires zero times.
Investigation showed THESIS_OPEN is the canonical save-side event
(THESIS_OPEN/THESIS_CLOSE lifecycle pattern; _SAVE exists only once
in the entire codebase as TIAS_SAVE). G8 adds the audit's
target_pct, stop_pct, max_hold_min fields to THESIS_OPEN so the
emission is field-complete against the audit schema.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.thesis_manager import ThesisManager


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


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _make_thesis_manager() -> ThesisManager:
    """ThesisManager with a mocked DB whose execute returns a cursor."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 42
    db.execute = AsyncMock(return_value=cursor)
    return ThesisManager(db=db)


@pytest.mark.asyncio
async def test_thesis_open_carries_target_stop_hold_fields(loguru_sink) -> None:
    """THESIS_OPEN must include target_pct, stop_pct, max_hold_min, size_usd."""
    mgr = _make_thesis_manager()
    await mgr.save_thesis(
        symbol="BTCUSDT",
        direction="long",
        entry_price=80000.0,
        stop_loss_price=78000.0,
        take_profit_price=84000.0,
        size_usd=500.0,
        leverage=5,
        max_hold_minutes=120,
        trailing_activation_pct=2.0,
        thesis="Strong momentum + bullish regime",
        order_id="ORD-AB-1234",
    )

    events = _records_with_tag(loguru_sink, "THESIS_OPEN ")
    assert len(events) == 1
    msg = events[0][1]
    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("dir") == "long"
    assert kv.get("id") == "42"
    assert float(kv.get("ent", "0")) == 80000.0
    assert float(kv.get("sl", "0")) == 78000.0
    assert float(kv.get("tp", "0")) == 84000.0
    # target_pct = |84000 - 80000| / 80000 * 100 = 5.0
    assert abs(float(kv.get("target_pct", "0")) - 5.0) < 0.01
    # stop_pct = |80000 - 78000| / 80000 * 100 = 2.5
    assert abs(float(kv.get("stop_pct", "0")) - 2.5) < 0.01
    assert int(kv.get("max_hold_min", "0")) == 120
    assert float(kv.get("size_usd", "0")) == 500.0
    assert kv.get("lev") == "5"


@pytest.mark.asyncio
async def test_thesis_open_short_side_target_stop_correct(loguru_sink) -> None:
    """For short positions tp < entry < sl; pct fields use absolute distance."""
    mgr = _make_thesis_manager()
    await mgr.save_thesis(
        symbol="ETHUSDT",
        direction="short",
        entry_price=3000.0,
        stop_loss_price=3060.0,        # 2% above entry
        take_profit_price=2880.0,      # 4% below entry
        size_usd=200.0,
        leverage=3,
        max_hold_minutes=60,
        trailing_activation_pct=1.0,
        thesis="Short setup",
    )

    events = _records_with_tag(loguru_sink, "THESIS_OPEN ")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert abs(float(kv.get("target_pct", "0")) - 4.0) < 0.01
    assert abs(float(kv.get("stop_pct", "0")) - 2.0) < 0.01


@pytest.mark.asyncio
async def test_thesis_open_zero_entry_does_not_crash(loguru_sink) -> None:
    """Degenerate entry_price=0 must not raise ZeroDivisionError."""
    mgr = _make_thesis_manager()
    await mgr.save_thesis(
        symbol="XYZUSDT",
        direction="long",
        entry_price=0.0,
        stop_loss_price=0.0,
        take_profit_price=0.0,
        size_usd=0.0,
        leverage=1,
        max_hold_minutes=30,
        trailing_activation_pct=0.0,
        thesis="degenerate",
    )
    events = _records_with_tag(loguru_sink, "THESIS_OPEN ")
    assert len(events) == 1, "must not crash on zero entry"


@pytest.mark.asyncio
async def test_thesis_open_emits_order_id_or_dash(loguru_sink) -> None:
    """Empty order_id falls back to `-` (grep-friendly)."""
    mgr = _make_thesis_manager()
    await mgr.save_thesis(
        symbol="ADAUSDT",
        direction="long",
        entry_price=0.50,
        stop_loss_price=0.48,
        take_profit_price=0.55,
        size_usd=100.0,
        leverage=2,
        max_hold_minutes=30,
        trailing_activation_pct=1.0,
        thesis="no order id",
    )
    events = _records_with_tag(loguru_sink, "THESIS_OPEN ")
    kv = _parse_kv(events[0][1])
    assert kv.get("order_id") == "-"
