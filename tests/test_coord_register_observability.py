"""Observability G6 — COORD_REG field completeness + duplicate detection.

The audit (2026-05-13) claimed COORD_REGISTER fires zero times.
Investigation showed COORD_REG fires correctly (20 events / 20 trades
in the audited window) under its canonical tag name. The real gaps
were field completeness (audit asked for side / qty / entry_price)
and the missing COORD_DUPLICATE_REGISTER cluster event.

G6 adds side, qty, entry_price to COORD_REG and emits a
COORD_DUPLICATE_REGISTER WARNING when register_trade overwrites an
existing entry — no behaviour change (the overwrite is preserved),
but operators can now audit whether the cooldown gate is holding.
"""

from __future__ import annotations

import re
import time
from unittest.mock import MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.trade_coordinator import TradeCoordinator


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


def _make_coordinator() -> TradeCoordinator:
    """TradeCoordinator's __init__ takes no args; just construct."""
    return TradeCoordinator()


def test_register_emits_full_field_set(loguru_sink) -> None:
    """COORD_REG must carry the full audit-required field set."""
    coord = _make_coordinator()
    coord.register_trade(
        symbol="BTCUSDT",
        strategy_category="claude_direct",
        strategy_name="claude_brain",
        entry_price=82000.5,
        side="Buy",
        source="brain_v2",
        decision_id="d-12345",
        size=0.05,
        order_id="ORD-XYZ-1234567890",
        sl_price=80000.0,
        tp_price=84000.0,
        leverage=5,
        size_usd=4100.0,
    )

    events = _records_with_tag(loguru_sink, "COORD_REG ")
    assert len(events) == 1
    msg = events[0][1]
    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("src") == "brain_v2"
    assert kv.get("cat") == "claude_direct"
    assert kv.get("side") == "Buy"
    assert float(kv.get("qty", "0")) == 0.05
    assert float(kv.get("entry_price", "0")) == 82000.5
    assert float(kv.get("sl", "0")) == 80000.0
    assert float(kv.get("tp", "0")) == 84000.0
    assert int(kv.get("leverage", "0")) == 5
    assert float(kv.get("size_usd", "0")) == 4100.0
    assert kv.get("did") == "d-12345"
    assert kv.get("order_id", "").startswith("ORD-XYZ")


def test_register_legacy_caller_emits_with_defaults(loguru_sink) -> None:
    """Legacy callers (brain_v2 shape) that don't pass new kwargs get
    default 0s — no crash, just informational defaults."""
    coord = _make_coordinator()
    # Mirror brain_v2.py:526 — no sl/tp/leverage/size_usd kwargs.
    coord.register_trade(
        symbol="BTCUSDT",
        strategy_category="momentum",
        strategy_name="trend_following",
        entry_price=82000.5,
        side="Buy",
        source="brain_v2",
    )
    events = _records_with_tag(loguru_sink, "COORD_REG ")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    # New kwargs default to 0 / 0.0 — emitted but informational only.
    assert float(kv.get("sl", "X")) == 0.0
    assert float(kv.get("tp", "X")) == 0.0
    assert int(kv.get("leverage", "X")) == 0
    assert float(kv.get("size_usd", "X")) == 0.0


def test_register_with_empty_side_falls_back_to_dash(loguru_sink) -> None:
    """Empty side should emit `side=-` (not blank) so the field is grep-friendly."""
    coord = _make_coordinator()
    coord.register_trade(
        symbol="ETHUSDT",
        size=0.1,
    )
    events = _records_with_tag(loguru_sink, "COORD_REG ")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv.get("side") == "-"


def test_register_duplicate_emits_warning(loguru_sink) -> None:
    """Registering the same symbol twice fires COORD_DUPLICATE_REGISTER warning."""
    coord = _make_coordinator()
    coord.register_trade(
        symbol="BTCUSDT",
        decision_id="d-first",
        source="brain_v2",
        size=0.05,
        side="Buy",
        entry_price=80000,
    )
    # Brief sleep so prior_age > 0 (test stability)
    time.sleep(0.05)
    coord.register_trade(
        symbol="BTCUSDT",
        decision_id="d-second",
        source="claude_direct",
        size=0.10,
        side="Sell",
        entry_price=81000,
    )

    regs = _records_with_tag(loguru_sink, "COORD_REG ")
    dups = _records_with_tag(loguru_sink, "COORD_DUPLICATE_REGISTER")
    assert len(regs) == 2, "both register_trade calls must emit COORD_REG"
    assert len(dups) == 1, "second registration must emit duplicate warning"

    # Verify the warning carries the audit-relevant context
    level, msg = dups[0]
    assert level == "WARNING"
    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("prior_did") == "d-first"
    assert kv.get("new_did") == "d-second"
    assert kv.get("new_src") == "claude_direct"
    # prior_age_s should be positive
    assert float(kv.get("prior_age_s", "0")) > 0


def test_register_first_time_does_not_emit_duplicate_warning(loguru_sink) -> None:
    """A clean first registration must NOT fire DUPLICATE_REGISTER."""
    coord = _make_coordinator()
    coord.register_trade(symbol="ADAUSDT", size=10, side="Buy", entry_price=0.5)
    assert _records_with_tag(loguru_sink, "COORD_DUPLICATE_REGISTER") == []


def test_register_overwrite_is_preserved(loguru_sink) -> None:
    """Duplicate registration emits the warning but the overwrite still
    happens (behaviour preserved per Rule 3)."""
    coord = _make_coordinator()
    coord.register_trade(
        symbol="BTCUSDT", side="Buy", size=0.05, entry_price=80000,
    )
    state_first = coord._trades["BTCUSDT"]

    coord.register_trade(
        symbol="BTCUSDT", side="Sell", size=0.10, entry_price=82000,
    )
    state_second = coord._trades["BTCUSDT"]

    # Overwrite happened — second registration replaced first
    assert state_second is not state_first
    assert state_second.side == "Sell"
    assert state_second.size == 0.10
    assert state_second.entry_price == 82000
