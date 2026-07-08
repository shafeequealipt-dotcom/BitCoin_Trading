"""Phase 3.5 — Mid-Hold Trade Management Fix: thesis-invalidation monitoring.

Tests both the pure evaluator (``ThesisManager.evaluate_thesis_state``)
and the watchdog monitoring path (``PositionWatchdog._monitor_thesis_state``):

  - evaluate_thesis_state — pure function, all branches.
  - Watchdog transitions VALID → DEGRADING → INVALIDATED.
  - Queues thesis_invalidation event on INVALIDATED transition.
  - Heuristic fallback path uses snapshot nearest_aligned_level.
  - Kill switch + no-anchor fallback handled.
"""

from __future__ import annotations

import os
import re
import tempfile
from unittest.mock import MagicMock

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


@pytest.fixture
async def real_db():
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "midhold_p3_5.db")
        db = DatabaseManager(path)
        await db.connect()
        await run_migrations(db)
        try:
            yield db
        finally:
            await db.disconnect()


# ════════════════════════════════════════════════════════════════════
# 1. evaluate_thesis_state — pure unit tests
# ════════════════════════════════════════════════════════════════════


def test_evaluate_brain_price_close_above_valid_when_below_level() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 245.30}',
        "thesis_snapshot": "{}",
    }
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=243.0, last_m5_close=243.5,
    )
    assert state == "VALID"


def test_evaluate_brain_price_close_above_degrading_on_wick() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 100.0}',
        "thesis_snapshot": "{}",
    }
    # Wick to 100.30 (above wick buffer 0.1%) but M5 close still at 99.5.
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=100.30, last_m5_close=99.5,
    )
    assert state == "DEGRADING"
    assert reason == "brain_price_close_above_degrading"


def test_evaluate_brain_price_close_above_invalidated_on_close() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 100.0}',
        "thesis_snapshot": "{}",
    }
    # M5 close at 100.6 (above level + 0.5% buffer).
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=100.6, last_m5_close=100.6,
    )
    assert state == "INVALIDATED"
    assert reason == "brain_price_close_above_invalidated"


def test_evaluate_brain_price_close_below_invalidated_for_buy() -> None:
    row = {
        "direction": "Buy",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_below", "value": 80000.0}',
        "thesis_snapshot": "{}",
    }
    # Below 80000 by more than 0.5% close buffer (< 79600).
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=79500.0, last_m5_close=79500.0,
    )
    assert state == "INVALIDATED"
    assert reason == "brain_price_close_below_invalidated"


def test_evaluate_brain_signal_type_is_valid_at_price_evaluator() -> None:
    """Signal criterion is handled by Phase 3.4 ensemble-flip path."""
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "signal", "value": "ensemble_flip_to_strong_buy"}',
        "thesis_snapshot": "{}",
    }
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=100.0, last_m5_close=100.0,
    )
    assert state == "VALID"


def test_evaluate_brain_none_type_is_always_valid() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "none", "value": null}',
        "thesis_snapshot": "{}",
    }
    state, _ = ThesisManager.evaluate_thesis_state(
        row, current_price=999999.0, last_m5_close=999999.0,
    )
    assert state == "VALID"


def test_evaluate_heuristic_fallback_invalidated_on_bearish_ob_close() -> None:
    snapshot = (
        '{"nearest_aligned_level": {"type": "ob", "side": "bearish",'
        ' "high": 245.30, "low": 244.10, "midpoint": 244.70}}'
    )
    row = {
        "direction": "Sell",
        "thesis_source": "heuristic_fallback",
        "thesis_invalidation": "",
        "thesis_snapshot": snapshot,
    }
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=246.6, last_m5_close=246.6,
    )
    assert state == "INVALIDATED"
    assert reason == "heuristic_fallback_invalidated"


def test_evaluate_heuristic_fallback_no_anchor() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "heuristic_fallback",
        "thesis_invalidation": "",
        "thesis_snapshot": '{"nearest_aligned_level": {"type": "none"}}',
    }
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=100.0, last_m5_close=100.0,
    )
    assert state == "VALID"
    assert reason == "heuristic_fallback_no_anchor"


def test_evaluate_legacy_row_with_empty_snapshot_is_valid_no_anchor() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "heuristic_fallback",
        "thesis_invalidation": "",
        "thesis_snapshot": "{}",
    }
    state, reason = ThesisManager.evaluate_thesis_state(
        row, current_price=100.0, last_m5_close=100.0,
    )
    assert state == "VALID"
    assert reason == "heuristic_fallback_no_anchor"


# ════════════════════════════════════════════════════════════════════
# 2. Watchdog _monitor_thesis_state — integrated mocked
# ════════════════════════════════════════════════════════════════════


def _make_watchdog_for_thesis_test(db, enabled=True):
    from src.workers.position_watchdog import PositionWatchdog

    settings = MagicMock()
    settings.watchdog.thesis_invalidation_detection_enabled = enabled
    settings.watchdog.thesis_invalidation_close_buffer_pct = 0.5
    settings.watchdog.thesis_invalidation_wick_buffer_pct = 0.1

    thesis_manager = ThesisManager(db)

    wd = PositionWatchdog.__new__(PositionWatchdog)
    wd.settings = settings
    wd.thesis_manager = thesis_manager
    wd._position_thesis_state = {}
    wd._wd_klines_m5 = {}
    return wd, thesis_manager


@pytest.mark.asyncio
async def test_watchdog_persists_invalidated_and_queues_event(real_db, loguru_sink) -> None:
    wd, thesis = _make_watchdog_for_thesis_test(real_db)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-inv",
        thesis_invalidation='{"type": "price_close_above", "value": 2120.0}',
        thesis_source="brain_stated",
    )
    pos = MagicMock()
    pos.symbol = "ETHUSDT"

    # Simulate an M5 close above 2120 + 0.5% = 2130.6.
    class _K:
        close = 2131.0
    wd._wd_klines_m5["ETHUSDT"] = [_K()]

    await wd._monitor_thesis_state(pos, current_price=2131.0)

    # State persisted.
    row = await real_db.fetch_one(
        "SELECT thesis_state FROM trade_thesis WHERE order_id = 'ORD-eth-inv'"
    )
    assert row["thesis_state"] == "INVALIDATED"
    # Event queued.
    events = await real_db.fetch_all(
        "SELECT event_type FROM thesis_events WHERE order_id = 'ORD-eth-inv'"
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "thesis_invalidation"
    # Log tags emitted.
    assert len(_records_with_tag(loguru_sink, "THESIS_LEVEL_MONITORED ")) >= 1
    assert len(_records_with_tag(loguru_sink, "THESIS_INVALIDATION_DETECTED ")) == 1
    # IMPLEMENT_MIDHOLD doc Rule 7: watchdog-layer EVENT_QUEUED tag.
    assert len(_records_with_tag(loguru_sink, "THESIS_INVALIDATION_EVENT_QUEUED ")) == 1


@pytest.mark.asyncio
async def test_watchdog_degrading_persists_no_event(real_db, loguru_sink) -> None:
    wd, thesis = _make_watchdog_for_thesis_test(real_db)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-deg",
        thesis_invalidation='{"type": "price_close_above", "value": 2120.0}',
        thesis_source="brain_stated",
    )
    pos = MagicMock()
    pos.symbol = "ETHUSDT"

    # Wick to 2122.5 (above wick buffer 2120 * 1.001 = 2122.12) but M5
    # close at 2119 (below the level).
    class _K:
        close = 2119.0
    wd._wd_klines_m5["ETHUSDT"] = [_K()]

    await wd._monitor_thesis_state(pos, current_price=2122.5)

    row = await real_db.fetch_one(
        "SELECT thesis_state FROM trade_thesis WHERE order_id = 'ORD-eth-deg'"
    )
    assert row["thesis_state"] == "DEGRADING"
    events = await real_db.fetch_all("SELECT id FROM thesis_events")
    assert len(events) == 0
    assert len(_records_with_tag(loguru_sink, "THESIS_INVALIDATION_DETECTED ")) == 0


@pytest.mark.asyncio
async def test_watchdog_kill_switch_short_circuits(real_db, loguru_sink) -> None:
    wd, thesis = _make_watchdog_for_thesis_test(real_db, enabled=False)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="bearish OB",
        order_id="ORD-eth-killed",
        thesis_invalidation='{"type": "price_close_above", "value": 2120.0}',
        thesis_source="brain_stated",
    )
    pos = MagicMock()
    pos.symbol = "ETHUSDT"

    class _K:
        close = 2200.0
    wd._wd_klines_m5["ETHUSDT"] = [_K()]

    await wd._monitor_thesis_state(pos, current_price=2200.0)

    row = await real_db.fetch_one(
        "SELECT thesis_state FROM trade_thesis WHERE order_id = 'ORD-eth-killed'"
    )
    # Should remain at default 'VALID'.
    assert row["thesis_state"] == "VALID"


@pytest.mark.asyncio
async def test_watchdog_heuristic_fallback_no_anchor_emits_diagnostic(
    real_db, loguru_sink,
) -> None:
    wd, thesis = _make_watchdog_for_thesis_test(real_db)
    await thesis.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="ensemble-driven",
        order_id="ORD-eth-noanchor",
        thesis_invalidation="",
        thesis_source="heuristic_fallback",
        thesis_snapshot="{}",
    )
    # Pre-seed cache state so that calling monitor advances the cached
    # state out of the column-default-VALID equality short-circuit.
    wd._position_thesis_state["ETHUSDT"] = "INVALIDATED"  # mismatched on purpose

    pos = MagicMock()
    pos.symbol = "ETHUSDT"

    await wd._monitor_thesis_state(pos, current_price=100.0)

    assert len(_records_with_tag(loguru_sink, "THESIS_INVALIDATION_NO_ANCHOR ")) == 1
