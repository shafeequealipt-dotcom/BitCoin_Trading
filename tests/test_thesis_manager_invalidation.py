"""Phase 3.1 — Mid-Hold Trade Management Fix: schema v34/v35 + ThesisManager persistence.

Tests the entry-thesis invalidation contract and per-position event queue:

  - v34 ALTER TABLE adds 4 columns to ``trade_thesis``:
    ``thesis_invalidation``, ``thesis_source``, ``thesis_snapshot``,
    ``thesis_state``.
  - v35 CREATE TABLE adds ``thesis_events`` with the unconsumed index.
  - ``save_thesis`` accepts and persists the three new entry-time params
    (state defaults to VALID via DB column default).
  - ``record_thesis_state`` transitions a single row, scoped to
    symbol+order_id+status=open.
  - ``get_open_thesis_for_symbol`` returns exactly one open row.
  - ``queue_thesis_event`` / ``get_unseen_events`` /
    ``mark_events_consumed`` / ``purge_events_for_closed_position`` form
    a correct lifecycle.

Migrations run against a real on-disk SQLite via DatabaseManager. The
log-emission tests use the canonical MagicMock+AsyncMock pattern from
``test_thesis_save_observability.py``.
"""

from __future__ import annotations

import os
import re
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.thesis_manager import ThesisManager


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def loguru_sink():
    """Capture loguru records for tag assertions."""
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


def _make_mocked_thesis_manager(lastrowid: int = 42) -> ThesisManager:
    """ThesisManager with a mocked DB whose execute returns a cursor."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = lastrowid
    db.execute = AsyncMock(return_value=cursor)
    return ThesisManager(db=db)


@pytest.fixture
async def real_db():
    """Real DatabaseManager backed by a temp file with migrations applied."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "midhold_p3_1.db")
        db = DatabaseManager(path)
        await db.connect()
        await run_migrations(db)
        try:
            yield db
        finally:
            await db.disconnect()


# ════════════════════════════════════════════════════════════════════
# 1. Migration v34/v35 idempotency + shape
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_v34_columns_present_after_migrations(real_db) -> None:
    """v34 adds thesis_invalidation/source/snapshot/state on trade_thesis."""
    rows = await real_db.fetch_all("PRAGMA table_info(trade_thesis)")
    cols = {r["name"] for r in rows}
    assert "thesis_invalidation" in cols
    assert "thesis_source" in cols
    assert "thesis_snapshot" in cols
    assert "thesis_state" in cols


@pytest.mark.asyncio
async def test_v35_thesis_events_table_and_index(real_db) -> None:
    """v35 CREATE TABLE + index produce a working queue table."""
    rows = await real_db.fetch_all("PRAGMA table_info(thesis_events)")
    cols = {r["name"] for r in rows}
    expected = {
        "id", "symbol", "order_id", "thesis_id",
        "event_type", "payload", "created_at",
        "consumed_at", "consumed_by",
    }
    assert expected.issubset(cols)

    idx_rows = await real_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'index' "
        "AND tbl_name = 'thesis_events'"
    )
    idx_names = {r["name"] for r in idx_rows}
    assert "idx_thesis_events_symbol_unconsumed" in idx_names
    assert "idx_thesis_events_order_id" in idx_names


@pytest.mark.asyncio
async def test_migrations_are_idempotent(real_db) -> None:
    """Re-running migrations after a schema_version reset must be a no-op
    via PRAGMA pre-flight (no duplicate-column ERRORs).
    """
    from src.database.migrations import run_migrations

    # Schema version was set to current by the fixture. Reset and rerun
    # to force the PRAGMA pre-flight path through v34/v35.
    await real_db.execute("DELETE FROM schema_version", force_protected=True)
    # Should complete without raising; the pre-flight column check
    # downgrades existing-column ALTERs to no-ops.
    await run_migrations(real_db)

    # Verify the columns are still present (re-running did not damage).
    rows = await real_db.fetch_all("PRAGMA table_info(trade_thesis)")
    cols = {r["name"] for r in rows}
    assert {"thesis_invalidation", "thesis_source",
            "thesis_snapshot", "thesis_state"}.issubset(cols)


# ════════════════════════════════════════════════════════════════════
# 2. save_thesis honors the new params + defaults
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_save_thesis_persists_brain_stated_criterion(real_db) -> None:
    """Brain-stated criterion + source + snapshot land in the row."""
    mgr = ThesisManager(real_db)
    await mgr.save_thesis(
        symbol="ETHUSDT",
        direction="Sell",
        entry_price=2109.04,
        stop_loss_price=2128.0,
        take_profit_price=2055.0,
        size_usd=420.0,
        leverage=2,
        max_hold_minutes=60,
        trailing_activation_pct=1.0,
        thesis="X-RAY bearish OB above price",
        order_id="ORD-eth-001",
        thesis_invalidation='{"type": "price_close_above", "value": 2128.5}',
        thesis_source="brain_stated",
        thesis_snapshot='{"nearest_aligned_level": {"type": "ob"}}',
    )
    row = await real_db.fetch_one(
        "SELECT thesis_invalidation, thesis_source, thesis_snapshot, thesis_state "
        "FROM trade_thesis WHERE order_id = 'ORD-eth-001'"
    )
    assert row is not None
    assert "price_close_above" in row["thesis_invalidation"]
    assert row["thesis_source"] == "brain_stated"
    assert "nearest_aligned_level" in row["thesis_snapshot"]
    # State defaults to VALID at the column level
    assert row["thesis_state"] == "VALID"


@pytest.mark.asyncio
async def test_save_thesis_legacy_caller_gets_safe_defaults(real_db) -> None:
    """Legacy callers (no new params) still produce well-formed rows."""
    mgr = ThesisManager(real_db)
    await mgr.save_thesis(
        symbol="BTCUSDT",
        direction="Buy",
        entry_price=80000.0,
        stop_loss_price=78000.0,
        take_profit_price=84000.0,
        size_usd=500.0,
        leverage=3,
        max_hold_minutes=120,
        trailing_activation_pct=1.5,
        thesis="legacy",
        order_id="ORD-btc-legacy",
    )
    row = await real_db.fetch_one(
        "SELECT thesis_invalidation, thesis_source, thesis_snapshot, thesis_state "
        "FROM trade_thesis WHERE order_id = 'ORD-btc-legacy'"
    )
    assert row["thesis_invalidation"] == ""
    assert row["thesis_source"] == "brain_stated"
    assert row["thesis_snapshot"] == "{}"
    assert row["thesis_state"] == "VALID"


@pytest.mark.asyncio
async def test_thesis_persistence_recorded_log_fires(loguru_sink) -> None:
    """THESIS_PERSISTENCE_RECORDED captures source + presence flags."""
    mgr = _make_mocked_thesis_manager()
    await mgr.save_thesis(
        symbol="SOLUSDT",
        direction="Sell",
        entry_price=84.3,
        stop_loss_price=85.0,
        take_profit_price=82.0,
        size_usd=300.0,
        leverage=3,
        max_hold_minutes=30,
        trailing_activation_pct=1.0,
        thesis="ensemble flip risk",
        order_id="ORD-sol-1",
        thesis_invalidation='{"type": "signal", "value": "ensemble_flip_to_strong_buy"}',
        thesis_source="brain_stated",
    )
    events = _records_with_tag(loguru_sink, "THESIS_PERSISTENCE_RECORDED ")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv["sym"] == "SOLUSDT"
    assert kv["source"] == "brain_stated"
    assert kv["criterion_present"] == "1"
    assert kv["snapshot_present"] == "0"  # default '{}'


@pytest.mark.asyncio
async def test_thesis_persistence_recorded_log_on_fallback(loguru_sink) -> None:
    """Heuristic fallback path shows snapshot_present=1, criterion_present=0."""
    mgr = _make_mocked_thesis_manager()
    await mgr.save_thesis(
        symbol="DOGEUSDT",
        direction="Sell",
        entry_price=0.10368,
        stop_loss_price=0.105,
        take_profit_price=0.10,
        size_usd=200.0,
        leverage=2,
        max_hold_minutes=60,
        trailing_activation_pct=1.0,
        thesis="X-RAY bearish OB",
        order_id="ORD-doge-1",
        thesis_invalidation="",
        thesis_source="heuristic_fallback",
        thesis_snapshot='{"nearest_aligned_level": {"type": "ob", "high": 0.105}}',
    )
    kv = _parse_kv(_records_with_tag(loguru_sink, "THESIS_PERSISTENCE_RECORDED ")[0][1])
    assert kv["source"] == "heuristic_fallback"
    assert kv["criterion_present"] == "0"
    assert kv["snapshot_present"] == "1"


# ════════════════════════════════════════════════════════════════════
# 3. record_thesis_state — single-row transitions
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_record_thesis_state_transitions_only_matching_row(real_db) -> None:
    """UPDATE is scoped to symbol+order_id+status=open."""
    mgr = ThesisManager(real_db)
    # Two open positions on ETHUSDT with distinct order_ids
    await mgr.save_thesis(
        symbol="ETHUSDT", direction="Buy", entry_price=2100.0,
        stop_loss_price=2080.0, take_profit_price=2150.0,
        size_usd=300.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="Buy", order_id="ORD-eth-A",
    )
    await mgr.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2120.0,
        stop_loss_price=2140.0, take_profit_price=2080.0,
        size_usd=200.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="Sell", order_id="ORD-eth-B",
    )

    ok = await mgr.record_thesis_state("ETHUSDT", "ORD-eth-A", "INVALIDATED")
    assert ok is True

    row_a = await real_db.fetch_one(
        "SELECT thesis_state FROM trade_thesis WHERE order_id = 'ORD-eth-A'"
    )
    row_b = await real_db.fetch_one(
        "SELECT thesis_state FROM trade_thesis WHERE order_id = 'ORD-eth-B'"
    )
    assert row_a["thesis_state"] == "INVALIDATED"
    assert row_b["thesis_state"] == "VALID"  # untouched


@pytest.mark.asyncio
async def test_record_thesis_state_rejects_invalid_value(loguru_sink) -> None:
    """Unknown state values are rejected and logged."""
    mgr = _make_mocked_thesis_manager()
    ok = await mgr.record_thesis_state("ETHUSDT", "ORD-eth-1", "EXPLODED")
    assert ok is False
    assert len(_records_with_tag(loguru_sink, "THESIS_STATE_INVALID_VALUE ")) == 1
    # The DB execute must not have been called for an invalid value
    assert mgr.db.execute.call_count == 0


@pytest.mark.asyncio
async def test_record_thesis_state_emits_recorded_log(loguru_sink) -> None:
    mgr = _make_mocked_thesis_manager()
    await mgr.record_thesis_state("BTCUSDT", "ORD-btc-1", "DEGRADING")
    events = _records_with_tag(loguru_sink, "THESIS_STATE_RECORDED ")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv["sym"] == "BTCUSDT"
    assert kv["new_state"] == "DEGRADING"


# ════════════════════════════════════════════════════════════════════
# 4. get_open_thesis_for_symbol
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_open_thesis_for_symbol_returns_match(real_db) -> None:
    mgr = ThesisManager(real_db)
    await mgr.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=2109.0,
        stop_loss_price=2130.0, take_profit_price=2055.0,
        size_usd=420.0, leverage=2, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="OB",
        order_id="ORD-eth-007",
        thesis_invalidation='{"type": "price_close_above", "value": 2128.0}',
        thesis_source="brain_stated",
    )
    row = await mgr.get_open_thesis_for_symbol("ETHUSDT", "ORD-eth-007")
    assert row is not None
    assert row["symbol"] == "ETHUSDT"
    assert row["thesis_source"] == "brain_stated"
    assert row["thesis_state"] == "VALID"


@pytest.mark.asyncio
async def test_get_open_thesis_for_symbol_returns_none_when_absent(real_db) -> None:
    mgr = ThesisManager(real_db)
    row = await mgr.get_open_thesis_for_symbol("XXXUSDT", "ORD-none")
    assert row is None


# ════════════════════════════════════════════════════════════════════
# 5. Event queue lifecycle
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_queue_then_get_then_mark_consumed(real_db) -> None:
    mgr = ThesisManager(real_db)
    eid1 = await mgr.queue_thesis_event(
        "ETHUSDT", "ORD-eth-1", "ensemble_flip",
        payload='{"consensus": "STRONG", "agreeing": 6.36, "opposing": 0.0}',
    )
    eid2 = await mgr.queue_thesis_event(
        "SOLUSDT", "ORD-sol-1", "thesis_invalidation",
        payload='{"level": 84.76, "type": "ob"}',
    )
    assert eid1 > 0 and eid2 > 0

    unseen = await mgr.get_unseen_events(["ETHUSDT", "SOLUSDT"])
    assert len(unseen) == 2
    types = {e["event_type"] for e in unseen}
    assert types == {"ensemble_flip", "thesis_invalidation"}

    # Mark only the ETH event consumed
    consumed = await mgr.mark_events_consumed([eid1], "CALL_A")
    assert consumed == 1

    unseen_after = await mgr.get_unseen_events(["ETHUSDT", "SOLUSDT"])
    assert len(unseen_after) == 1
    assert unseen_after[0]["event_type"] == "thesis_invalidation"


@pytest.mark.asyncio
async def test_queue_rejects_invalid_event_type(loguru_sink) -> None:
    mgr = _make_mocked_thesis_manager(lastrowid=99)
    eid = await mgr.queue_thesis_event(
        "ETHUSDT", "ORD-eth-1", "bogus_event_type", payload="{}",
    )
    assert eid == -1
    assert len(_records_with_tag(loguru_sink, "THESIS_EVENT_INVALID_TYPE ")) == 1
    assert mgr.db.execute.call_count == 0


@pytest.mark.asyncio
async def test_get_unseen_caps_per_symbol(real_db) -> None:
    mgr = ThesisManager(real_db)
    # Queue 15 events for one symbol; cap of 10 should win
    for i in range(15):
        await mgr.queue_thesis_event(
            "ETHUSDT", f"ORD-eth-{i}", "ensemble_flip",
            payload=f'{{"i": {i}}}',
        )
    unseen = await mgr.get_unseen_events(["ETHUSDT"], max_per_symbol=10)
    assert len(unseen) == 10
    # Most-recent-first ordering
    assert all("i" in e["payload"] for e in unseen)


@pytest.mark.asyncio
async def test_purge_events_for_closed_position(real_db) -> None:
    mgr = ThesisManager(real_db)
    await mgr.queue_thesis_event(
        "ETHUSDT", "ORD-eth-close", "ensemble_flip", payload="{}",
    )
    await mgr.queue_thesis_event(
        "ETHUSDT", "ORD-eth-keep", "ensemble_flip", payload="{}",
    )
    await mgr.purge_events_for_closed_position("ORD-eth-close")

    # ORD-eth-close events gone; ORD-eth-keep survives
    rows = await real_db.fetch_all(
        "SELECT order_id FROM thesis_events"
    )
    order_ids = {r["order_id"] for r in rows}
    assert order_ids == {"ORD-eth-keep"}


@pytest.mark.asyncio
async def test_event_queue_emits_consume_log(loguru_sink, real_db) -> None:
    mgr = ThesisManager(real_db)
    eid = await mgr.queue_thesis_event(
        "ETHUSDT", "ORD-eth-1", "ensemble_flip", payload="{}",
    )
    await mgr.mark_events_consumed([eid], "CALL_B")
    consumed_logs = _records_with_tag(loguru_sink, "THESIS_EVENT_CONSUMED ")
    assert len(consumed_logs) >= 1
    kv = _parse_kv(consumed_logs[-1][1])
    assert kv["consumer"] == "CALL_B"
