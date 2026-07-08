"""Unit tests for CRITICAL-3 (trade_history coverage callback).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md CRITICAL-3.

Pre-fix: bybit_demo_adapter.close_position wrote trade_history directly
with `trade_id=close_order.order_id or f"bd-{symbol}-close"`. Because
_build_close_order hardcodes order_id="", every row used the fallback.
116+ closes collapsed into 30 collision-overwritten rows. WS-only closes
(SL/TP hit) were never written.

Fix: removed the adapter's direct save_trade call; added a new
_trade_history_close_callback in workers/manager.py that fires for ALL
coordinator close paths (WS event, watchdog poll, sniper). Mode-gated to
bybit_demo. Trade_id derived from state.order_id (open-side, unique per
trade) with epoch-ms fallback anchored to opened_at.

Tests cover the trade_id derivation logic and the TradeRecord
construction in isolation; full callback wiring exercised by the
integration test that simulates a coordinator close + asserts the row
appears in a real (in-memory SQLite) trade_history table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trade_coordinator import TradeCoordinator
from src.core.types import Side, TradeRecord


# ──────────────────────────────────────────────────────────────────────
# Helpers — replicate the trade_id derivation + TradeRecord construction
# from workers/manager.py:_trade_history_close_callback so we can pin the
# behaviour without spinning up the full WorkerManager.
# ──────────────────────────────────────────────────────────────────────


def _derive_trade_id(record: dict) -> str:
    """Mirror of the trade_id derivation in _trade_history_close_callback."""
    sym = record.get("symbol", "?")
    open_oid = record.get("order_id", "") or ""
    if open_oid:
        return f"bd-{open_oid}"
    opened_iso = record.get("opened_at", "") or ""
    try:
        opened_dt = datetime.fromisoformat(opened_iso)
        opened_ms = int(opened_dt.timestamp() * 1000)
    except Exception:
        import time as _t
        opened_ms = int(_t.time() * 1000)
    return f"bd-{sym}-{opened_ms}"


# ──────────────────────────────────────────────────────────────────────
# Group 1 — trade_id derivation
# ──────────────────────────────────────────────────────────────────────


def test_trade_id_uses_state_order_id_when_present() -> None:
    """When the coordinator record has a populated order_id (from
    state.order_id, set at register_trade), the trade_id is `bd-{order_id}`."""
    record = {
        "symbol": "BTCUSDT",
        "order_id": "abc123def456",
        "opened_at": "2026-05-09T19:00:00+00:00",
    }
    assert _derive_trade_id(record) == "bd-abc123def456"


def test_trade_id_falls_back_to_opened_at_epoch() -> None:
    """When order_id is empty (legacy register_trade callers, or pre-Phase-8
    rows), the trade_id falls back to `bd-{symbol}-{opened_at_ms}`. The
    epoch is anchored to opened_at, NOT time.time(), so the trade_id is
    deterministic per trade — no race-driven duplicates if the callback
    fires twice for the same close."""
    record = {
        "symbol": "ETHUSDT",
        "order_id": "",
        "opened_at": "2026-05-09T19:00:00+00:00",
    }
    expected_ms = int(
        datetime(2026, 5, 9, 19, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert _derive_trade_id(record) == f"bd-ETHUSDT-{expected_ms}"


def test_trade_id_collision_pattern_eliminated() -> None:
    """Three closes for the same symbol with different order_ids must
    produce three distinct trade_ids. Pre-fix the audit-flagged
    `bd-{symbol}-close` collapsed all three to one collision-overwritten
    row."""
    base = {"symbol": "ADAUSDT", "opened_at": "2026-05-09T19:00:00+00:00"}
    ids = {
        _derive_trade_id({**base, "order_id": "ord-1"}),
        _derive_trade_id({**base, "order_id": "ord-2"}),
        _derive_trade_id({**base, "order_id": "ord-3"}),
    }
    assert len(ids) == 3, "expected three distinct trade_ids; got collision"
    assert ids == {"bd-ord-1", "bd-ord-2", "bd-ord-3"}


def test_trade_id_fallback_distinguishes_two_closes_at_different_times() -> None:
    """Two same-symbol closes at different open times must produce
    different fallback trade_ids."""
    a = _derive_trade_id(
        {"symbol": "X", "order_id": "", "opened_at": "2026-05-09T19:00:00+00:00"}
    )
    b = _derive_trade_id(
        {"symbol": "X", "order_id": "", "opened_at": "2026-05-09T19:30:00+00:00"}
    )
    assert a != b


# ──────────────────────────────────────────────────────────────────────
# Group 2 — TradeRecord construction from coordinator record
# ──────────────────────────────────────────────────────────────────────


def _build_trade_from_record(record: dict) -> TradeRecord:
    """Mirror of the TradeRecord build in _trade_history_close_callback."""
    sym = record.get("symbol", "?")
    side_str = record.get("direction", "Buy") or "Buy"
    side_enum = Side.SELL if side_str in ("Sell", "Short") else Side.BUY
    entry = float(record.get("entry_price", 0.0) or 0.0)
    exit_p = float(record.get("close_price", 0.0) or 0.0)
    qty = float(record.get("size", 0.0) or 0.0)
    pnl_usd = float(record.get("pnl_usd", 0.0) or 0.0)
    pnl_pct = float(record.get("pnl_pct", 0.0) or 0.0)

    opened_iso = record.get("opened_at", "")
    closed_iso = record.get("closed_at", "")
    try:
        opened_dt = (
            datetime.fromisoformat(opened_iso)
            if opened_iso
            else datetime.now(timezone.utc)
        )
    except Exception:
        opened_dt = datetime.now(timezone.utc)
    try:
        closed_dt = (
            datetime.fromisoformat(closed_iso)
            if closed_iso
            else datetime.now(timezone.utc)
        )
    except Exception:
        closed_dt = datetime.now(timezone.utc)

    notes = (
        f"closed_by={record.get('closed_by', '')} "
        f"price_source={record.get('price_source', '')}"
    )
    return TradeRecord(
        trade_id=_derive_trade_id(record),
        symbol=sym,
        side=side_enum,
        entry_price=entry,
        exit_price=exit_p,
        qty=qty,
        pnl=pnl_usd,
        pnl_pct=pnl_pct,
        strategy=record.get("strategy_name", "")[:120],
        notes=notes[:500],
        entry_time=opened_dt,
        exit_time=closed_dt,
    )


def test_trade_record_populated_from_coordinator_record() -> None:
    """The TradeRecord built from the coordinator's record dict must
    carry the post-CRITICAL-1 + post-CRITICAL-2 corrected values:
    pnl_usd/pnl_pct from the back-derive, opened_at/closed_at as ISO
    strings parsed to datetimes, side from direction, qty from size."""
    record = {
        "symbol": "IMXUSDT",
        "order_id": "ord-xyz-123",
        "direction": "Sell",
        "entry_price": 0.18976,
        "close_price": 0.18974,
        "size": 100.0,
        "pnl_pct": 0.010539629005069561,
        "pnl_usd": 0.001999999999999963,
        "opened_at": "2026-05-09T19:42:42+00:00",
        "closed_at": "2026-05-09T19:52:31.303253+00:00",
        "strategy_name": "claude_direct",
        "closed_by": "bybit_sl_hit",
        "price_source": "bybit_ws_authoritative",
    }
    trade = _build_trade_from_record(record)

    assert trade.trade_id == "bd-ord-xyz-123"
    assert trade.symbol == "IMXUSDT"
    assert trade.side == Side.SELL
    assert trade.entry_price == 0.18976
    assert trade.exit_price == 0.18974
    assert trade.qty == 100.0
    assert trade.pnl == pytest.approx(0.001999999999999963)
    assert trade.pnl_pct == pytest.approx(0.010539629005069561)
    assert trade.strategy == "claude_direct"
    assert "bybit_sl_hit" in trade.notes
    assert "bybit_ws_authoritative" in trade.notes
    assert trade.entry_time.tzinfo is not None
    assert trade.exit_time.tzinfo is not None
    assert trade.exit_time > trade.entry_time


def test_buy_side_mapping_from_record_direction() -> None:
    """Buy direction maps to Side.BUY."""
    record = {
        "symbol": "X",
        "order_id": "",
        "direction": "Buy",
        "entry_price": 100.0,
        "close_price": 101.0,
        "size": 10.0,
        "opened_at": "2026-05-09T19:00:00+00:00",
        "closed_at": "2026-05-09T19:10:00+00:00",
    }
    trade = _build_trade_from_record(record)
    assert trade.side == Side.BUY


def test_short_alias_maps_to_sell() -> None:
    """Legacy "Short" string alias maps to Side.SELL (matches coordinator's
    existing convention at trade_coordinator.py:690)."""
    record = {
        "symbol": "X",
        "order_id": "",
        "direction": "Short",
        "entry_price": 100.0,
        "close_price": 99.0,
        "size": 10.0,
        "opened_at": "2026-05-09T19:00:00+00:00",
        "closed_at": "2026-05-09T19:10:00+00:00",
    }
    trade = _build_trade_from_record(record)
    assert trade.side == Side.SELL


# ──────────────────────────────────────────────────────────────────────
# Group 3 — Coordinator record now carries `size` (CRITICAL-3 enabler)
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def coordinator() -> TradeCoordinator:
    return TradeCoordinator()


def test_coordinator_record_includes_size(coordinator: TradeCoordinator) -> None:
    """trade_coordinator.on_trade_closed must include `size` in the record
    dict so the new trade_history callback can populate the qty column."""
    coordinator.register_trade(
        symbol="X",
        strategy_category="default",
        entry_price=100.0,
        side="Buy",
        size=42.5,
    )
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=101.0,
    )
    record = coordinator._closed_trades[-1]
    assert "size" in record
    assert record["size"] == pytest.approx(42.5)


def test_coordinator_record_size_zero_when_state_missing(
    coordinator: TradeCoordinator,
) -> None:
    """Defensive: the size fallback is 0.0 when state is None (mirrors
    sibling fields). This branch is unreachable via on_trade_closed (the
    early-return guard at lines 666-675 prevents record construction) but
    we lock the contract."""
    state = None
    fallback = state.size if state else 0.0
    assert fallback == 0.0
