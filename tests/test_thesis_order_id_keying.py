"""Definitive-fix Phase 8 — thesis manager keyed by (symbol, order_id).

The forensic S5 regression: closing a Buy ETH thesis with the legacy
``WHERE symbol = ? AND status = 'open'`` clause silently closed a
freshly-opened Sell ETH thesis on the same symbol. ``close_thesis``
now accepts ``order_id``; when non-empty, the WHERE clause is
narrowed so only the matching thesis row is affected.

These tests use an in-memory aiosqlite database with the minimum
``trade_thesis`` schema needed to exercise the WHERE clause.
"""

from __future__ import annotations

import pytest

from src.core.thesis_manager import ThesisManager


_SCHEMA = """
CREATE TABLE trade_thesis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    stop_loss_price REAL,
    take_profit_price REAL,
    size_usd REAL,
    leverage INTEGER,
    max_hold_minutes INTEGER,
    trailing_activation_pct REAL,
    thesis TEXT,
    market_context TEXT,
    strategy_hints TEXT,
    consensus TEXT,
    status TEXT DEFAULT 'open',
    order_id TEXT DEFAULT '',
    exchange_mode TEXT DEFAULT 'shadow',
    apex_flipped INTEGER DEFAULT 0,
    apex_original_direction TEXT DEFAULT '',
    apex_reason TEXT DEFAULT '',
    -- Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) v27
    entry_xray_confidence REAL NOT NULL DEFAULT 0.0,
    entry_setup_type TEXT NOT NULL DEFAULT '',
    entry_regime_at_open TEXT NOT NULL DEFAULT '',
    entry_regime_confidence REAL NOT NULL DEFAULT 0.0,
    -- CALL_B Framing Fix Phase 1E (2026-05-06) v28 — XRAY flip metadata
    xray_flip_source TEXT NOT NULL DEFAULT '',
    xray_flip_ratio REAL NOT NULL DEFAULT 0.0,
    xray_flip_rr_long REAL NOT NULL DEFAULT 0.0,
    xray_flip_rr_short REAL NOT NULL DEFAULT 0.0,
    -- Mid-Hold Trade Management Fix Phase 3.1 (2026-05-19) v34
    thesis_invalidation TEXT NOT NULL DEFAULT '',
    thesis_source TEXT NOT NULL DEFAULT 'brain_stated',
    thesis_snapshot TEXT NOT NULL DEFAULT '{}',
    thesis_state TEXT NOT NULL DEFAULT 'VALID',
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    close_price REAL,
    actual_pnl_pct REAL,
    actual_pnl_usd REAL,
    close_reason TEXT,
    lesson TEXT
)
"""


class _DBStub:
    """In-memory async wrapper compatible with ThesisManager.execute / fetch_all."""

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    async def fetch_all(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


@pytest.fixture()
def db():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return _DBStub(conn)


@pytest.mark.asyncio
async def test_phase8_close_with_order_id_only_affects_matching_row(db) -> None:
    """Closing thesis A by order_id leaves thesis B (same symbol, different id) open."""
    mgr = ThesisManager(db)
    # Open two theses for the same symbol but different order_ids.
    await mgr.save_thesis(
        symbol="ETHUSDT", direction="Buy", entry_price=1000.0,
        stop_loss_price=950.0, take_profit_price=1100.0,
        size_usd=100.0, leverage=3, max_hold_minutes=30,
        trailing_activation_pct=0.5, thesis="Buy ETH", order_id="OID-A",
    )
    await mgr.save_thesis(
        symbol="ETHUSDT", direction="Sell", entry_price=1010.0,
        stop_loss_price=1060.0, take_profit_price=910.0,
        size_usd=100.0, leverage=3, max_hold_minutes=30,
        trailing_activation_pct=0.5, thesis="Sell ETH", order_id="OID-B",
    )
    # Close ONLY OID-A. With Phase 8 the WHERE clause filters by order_id.
    await mgr.close_thesis(
        symbol="ETHUSDT", close_price=1050.0,
        actual_pnl_pct=5.0, actual_pnl_usd=5.0,
        close_reason="tp_hit", order_id="OID-A",
    )
    open_rows = await mgr.get_open_theses()
    # OID-B must remain open. get_open_theses doesn't currently SELECT
    # order_id, so we assert via direction (Sell uniquely identifies B).
    assert len(open_rows) == 1
    assert open_rows[0]["direction"] == "Sell"


@pytest.mark.asyncio
async def test_phase8_close_without_order_id_legacy_close_all_for_symbol(db) -> None:
    """Empty order_id preserves legacy "close all open theses for symbol" semantics."""
    mgr = ThesisManager(db)
    await mgr.save_thesis(
        symbol="BTCUSDT", direction="Buy", entry_price=50000.0,
        stop_loss_price=48000.0, take_profit_price=55000.0,
        size_usd=200.0, leverage=3, max_hold_minutes=30,
        trailing_activation_pct=0.5, thesis="Buy BTC", order_id="LEGACY-1",
    )
    await mgr.close_thesis(
        symbol="BTCUSDT", close_price=51000.0,
        actual_pnl_pct=2.0, actual_pnl_usd=4.0,
        close_reason="manual", order_id="",  # legacy caller — no id
    )
    open_rows = await mgr.get_open_theses()
    assert open_rows == []
