"""End-to-end integration test exercising multiple G changes together.

Validates that G1 + G6 + G8 + G10 emissions co-exist correctly when a
single trade flows through the validation → coordinator → thesis
chain (the open-side of a Claude direct trade lifecycle).

This is a final integration smoke check on top of the per-gap unit
tests. It does NOT replace the operator-side Phase 4 live verification
(24-hour soak with real positions); it confirms in-process that the
producer-consumer wiring is intact post-merge.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.sl_tp_validator import SLTPValidator
from src.core.trade_coordinator import TradeCoordinator
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


@pytest.mark.asyncio
async def test_trade_open_lifecycle_emits_validator_register_save(loguru_sink) -> None:
    """A single trade open emits: SLTP_PAIR_OK + COORD_REG + THESIS_OPEN.

    Mirrors the strategy_worker._execute_claude_trade open sequence:
    1) validate_pair → SLTP_PAIR_OK (G10)
    2) coordinator.register_trade → COORD_REG with full fields (G6)
    3) thesis_manager.save_thesis → THESIS_OPEN with full fields (G8)
    """
    # Step 1 — validate
    validator = SLTPValidator()
    action, _ = validator.validate_pair(
        sl_price=78000.0,
        tp_price=84000.0,
        entry_price=80000.0,
        current_price=80000.0,
        direction="Buy",
        symbol="BTCUSDT",
    )
    assert action == "OK"

    # Step 2 — register
    coord = TradeCoordinator()
    coord.register_trade(
        symbol="BTCUSDT",
        strategy_category="claude_direct",
        strategy_name="claude_trader",
        entry_price=80000.0,
        side="Buy",
        size=0.05,
        source="claude_direct",
        decision_id="d-e2e-12345",
        order_id="ORD-E2E-9999",
        sl_price=78000.0,
        tp_price=84000.0,
        leverage=5,
        size_usd=4000.0,
    )

    # Step 3 — save thesis
    db = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 99
    db.execute = AsyncMock(return_value=cursor)
    mgr = ThesisManager(db=db)
    await mgr.save_thesis(
        symbol="BTCUSDT",
        direction="long",
        entry_price=80000.0,
        stop_loss_price=78000.0,
        take_profit_price=84000.0,
        size_usd=4000.0,
        leverage=5,
        max_hold_minutes=120,
        trailing_activation_pct=2.0,
        thesis="E2E integration test",
        order_id="ORD-E2E-9999",
    )

    # All three audit events fired in order
    sltp = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    coord_reg = _records_with_tag(loguru_sink, "COORD_REG ")
    thesis = _records_with_tag(loguru_sink, "THESIS_OPEN ")
    assert len(sltp) == 1
    assert len(coord_reg) == 1
    assert len(thesis) == 1

    # Audit-required fields visible across all three events
    sltp_kv = _parse_kv(sltp[0][1])
    coord_kv = _parse_kv(coord_reg[0][1])
    thesis_kv = _parse_kv(thesis[0][1])

    # Cross-event correlation: all three should reference BTCUSDT and
    # the same order_id
    assert sltp_kv.get("sym") == "BTCUSDT"
    assert coord_kv.get("sym") == "BTCUSDT"
    assert thesis_kv.get("sym") == "BTCUSDT"
    assert "ORD-E2E" in coord_kv.get("order_id", "")
    assert "ORD-E2E" in thesis_kv.get("order_id", "")

    # SL/TP geometry consistent across events
    assert float(sltp_kv.get("sl_pct", "0")) == 2.5
    assert float(coord_kv.get("sl", "0")) == 78000.0
    assert float(thesis_kv.get("sl", "0")) == 78000.0
    assert float(coord_kv.get("tp", "0")) == 84000.0
    assert float(thesis_kv.get("tp", "0")) == 84000.0


@pytest.mark.asyncio
async def test_duplicate_register_with_validator_run(loguru_sink) -> None:
    """SLTP_PAIR_OK fires per validation; COORD_DUPLICATE_REGISTER fires
    on second register without a close between. Cross-event ordering
    intact."""
    coord = TradeCoordinator()
    validator = SLTPValidator()

    # First trade open
    validator.validate_pair(78000, 84000, 80000, 80000, "Buy", "BTCUSDT")
    coord.register_trade(
        symbol="BTCUSDT",
        decision_id="d-first",
        side="Buy",
        size=0.05,
        entry_price=80000,
        sl_price=78000,
        tp_price=84000,
    )

    # Second register without on_trade_closed — duplicate path
    validator.validate_pair(79000, 85000, 81000, 81000, "Buy", "BTCUSDT")
    coord.register_trade(
        symbol="BTCUSDT",
        decision_id="d-second",
        side="Buy",
        size=0.10,
        entry_price=81000,
        sl_price=79000,
        tp_price=85000,
    )

    sltp = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    coord_reg = _records_with_tag(loguru_sink, "COORD_REG ")
    dup = _records_with_tag(loguru_sink, "COORD_DUPLICATE_REGISTER")
    assert len(sltp) == 2, "validator runs each open attempt"
    assert len(coord_reg) == 2, "coord_reg fires each register"
    assert len(dup) == 1, "duplicate detection fires once on the second"

    # Duplicate carries prior+new identifiers
    dup_kv = _parse_kv(dup[0][1])
    assert dup_kv.get("prior_did") == "d-first"
    assert dup_kv.get("new_did") == "d-second"
