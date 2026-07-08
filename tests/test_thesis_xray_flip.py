"""CALL_B Framing Fix Phase 1E (2026-05-06) — XRAY flip metadata persistence.

Schema v28 adds 4 columns to `trade_thesis`:
  xray_flip_source    — 'xray' / 'apex' / '' (empty when no flip)
  xray_flip_ratio     — RR_chosen / RR_rejected at flip time
  xray_flip_rr_long   — long-direction RR at flip time
  xray_flip_rr_short  — short-direction RR at flip time

These tests use an in-memory aiosqlite-equivalent DB stub mirroring the
pattern from test_thesis_order_id_keying.py. They verify:

  1. Round-trip: save_thesis(..., xray_flip_source='xray', ...) →
     get_open_theses() returns the values intact.
  2. Render: a position whose thesis row carries xray flip metadata
     produces the verbatim "FLIPPED via XRAY ... Nx better" line.
  3. Back-compat: rows pre-dating v28 (without the columns) load
     without error and CALL_B falls back to the legacy apex_flipped
     branch correctly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brain.strategist import ClaudeStrategist
from src.core.thesis_manager import ThesisManager


# Mirror of the v28 trade_thesis shape — the in-memory test DB exercises
# every column save_thesis/get_open_theses touch.
_SCHEMA_V28 = """
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
    -- v27
    entry_xray_confidence REAL NOT NULL DEFAULT 0.0,
    entry_setup_type TEXT NOT NULL DEFAULT '',
    entry_regime_at_open TEXT NOT NULL DEFAULT '',
    entry_regime_confidence REAL NOT NULL DEFAULT 0.0,
    -- v28 (CALL_B Framing Fix Phase 1E, 2026-05-06)
    xray_flip_source TEXT NOT NULL DEFAULT '',
    xray_flip_ratio REAL NOT NULL DEFAULT 0.0,
    xray_flip_rr_long REAL NOT NULL DEFAULT 0.0,
    xray_flip_rr_short REAL NOT NULL DEFAULT 0.0,
    -- v34 (Mid-Hold Trade Management Fix Phase 3.1, 2026-05-19)
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
    conn.executescript(_SCHEMA_V28)
    return _DBStub(conn)


# ─── Test 1: schema v28 round-trip ──────────────────────────────────


@pytest.mark.asyncio
async def test_schema_v28_round_trip_persists_xray_flip_metadata(db) -> None:
    """save_thesis with xray flip kwargs must persist them and
    get_open_theses must surface them as dict keys with the original
    types (TEXT for source, REAL for ratios). Regression guard: a
    rename or removal of any of the 4 columns surfaces here.
    """
    mgr = ThesisManager(db)
    tid = await mgr.save_thesis(
        symbol="BCHUSDT",
        direction="Sell",
        entry_price=320.0,
        stop_loss_price=330.0,
        take_profit_price=290.0,
        size_usd=100.0,
        leverage=3,
        max_hold_minutes=30,
        trailing_activation_pct=0.5,
        thesis="Reversion at resistance",
        order_id="OID-XRAY-A",
        apex_flipped=True,
        apex_original_direction="Buy",
        apex_reason="XRAY recheck flipped due to RR asymmetry",
        xray_flip_source="xray",
        xray_flip_ratio=7.2,
        xray_flip_rr_long=0.5,
        xray_flip_rr_short=3.6,
    )
    assert tid > 0
    rows = await mgr.get_open_theses()
    assert len(rows) == 1
    row = rows[0]
    assert row["xray_flip_source"] == "xray"
    assert row["xray_flip_ratio"] == pytest.approx(7.2)
    assert row["xray_flip_rr_long"] == pytest.approx(0.5)
    assert row["xray_flip_rr_short"] == pytest.approx(3.6)
    # Legacy APEX columns still populated alongside.
    assert row["apex_flipped"] == 1
    assert row["apex_original_direction"] == "Buy"


@pytest.mark.asyncio
async def test_schema_v28_default_values_for_non_flipped_trades(db) -> None:
    """save_thesis without flip kwargs persists default values ('' /
    0.0). CALL_B's render code reads these as 'no flip' so the FLIPPED
    notice is suppressed. Regression guard: a default change ripples to
    spurious notices on non-flipped positions.
    """
    mgr = ThesisManager(db)
    await mgr.save_thesis(
        symbol="BTCUSDT",
        direction="Buy",
        entry_price=65000.0,
        stop_loss_price=64000.0,
        take_profit_price=67000.0,
        size_usd=100.0,
        leverage=3,
        max_hold_minutes=30,
        trailing_activation_pct=0.5,
        thesis="Pullback long",
        order_id="OID-CLEAN",
    )
    rows = await mgr.get_open_theses()
    assert len(rows) == 1
    row = rows[0]
    assert row["xray_flip_source"] == ""
    assert row["xray_flip_ratio"] == 0.0
    assert row["xray_flip_rr_long"] == 0.0
    assert row["xray_flip_rr_short"] == 0.0
    assert row["apex_flipped"] == 0


# ─── Test 2: CALL_B prompt rendering ────────────────────────────────


def _make_strategist_with_thesis_row(thesis_row: dict, position) -> ClaudeStrategist:
    thesis_mgr = MagicMock()
    thesis_mgr.get_open_theses = AsyncMock(return_value=[thesis_row])
    thesis_mgr.get_recent_lessons = AsyncMock(return_value=[])

    position_service = MagicMock()
    position_service.get_positions = AsyncMock(return_value=[position])
    coordinator = MagicMock()
    coordinator.get_trade_plan = MagicMock(return_value=None)
    coordinator.get_trade_info = MagicMock(return_value={})
    coordinator.get_active_reentry_cooldowns = MagicMock(return_value=[])
    pnl_manager = SimpleNamespace(current_pnl_pct=0.0)
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(return_value=None)
    urgent_queue = MagicMock()
    urgent_queue.has_concerns = False

    services = {
        "thesis_manager": thesis_mgr,
        "position_service": position_service,
        "trade_coordinator": coordinator,
        "pnl_manager": pnl_manager,
        "regime_detector": regime_detector,
        "urgent_queue": urgent_queue,
    }
    settings = SimpleNamespace(
        brain=SimpleNamespace(use_packages=True, surface_briefing_fields=False),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
    )
    strat = ClaudeStrategist(
        claude_client=None,
        services=services,
        settings=settings,
    )
    strat.refresh_positions = AsyncMock(return_value=[position])
    return strat


@pytest.mark.asyncio
async def test_callb_prompt_renders_xray_flip_with_concrete_rr() -> None:
    """For an XRAY-flipped position, the CALL_B prompt must show the
    concrete RR justification: "FLIPPED via XRAY from Buy to Sell:
    RR_chosen=3.60 vs RR_rejected=0.50 (7.2x better)"
    """
    pos = SimpleNamespace(
        symbol="BCHUSDT",
        side=SimpleNamespace(value="Sell"),
        entry_price=320.0,
        mark_price=315.0,
        size=0.1,
        leverage=3,
    )
    thesis_row = {
        "symbol": "BCHUSDT",
        "direction": "Sell",
        "stop_loss_price": 330.0,
        "take_profit_price": 290.0,
        "leverage": 3,
        "apex_flipped": 1,
        "apex_original_direction": "Buy",
        "apex_reason": "XRAY recheck flipped",
        "xray_flip_source": "xray",
        "xray_flip_ratio": 7.2,
        "xray_flip_rr_long": 0.5,
        "xray_flip_rr_short": 3.6,
    }
    strat = _make_strategist_with_thesis_row(thesis_row, pos)
    prompt = await strat._build_position_prompt()
    assert "FLIPPED via XRAY from Buy to Sell" in prompt
    assert "RR_chosen=3.60" in prompt
    assert "RR_rejected=0.50" in prompt
    assert "(7.2x better)" in prompt
    # Legacy "APEX-FLIPPED:" label must NOT appear (Phase 1E unifies
    # both sources under the new "FLIPPED via X" form).
    assert "APEX-FLIPPED:" not in prompt


@pytest.mark.asyncio
async def test_callb_prompt_renders_apex_flip_with_legacy_text() -> None:
    """For an APEX-flipped position (or a legacy v23-v27 row pre-dating
    the v28 columns), the notice falls back to the apex_reason free-text
    under the unified "FLIPPED via APEX" label.
    """
    pos = SimpleNamespace(
        symbol="ETHUSDT",
        side=SimpleNamespace(value="Sell"),
        entry_price=2000.0,
        mark_price=1980.0,
        size=0.5,
        leverage=3,
    )
    thesis_row = {
        "symbol": "ETHUSDT",
        "direction": "Sell",
        "stop_loss_price": 2050.0,
        "take_profit_price": 1900.0,
        "leverage": 3,
        "apex_flipped": 1,
        "apex_original_direction": "Buy",
        "apex_reason": "Qwen flipped on confidence asymmetry — reasoning here",
        # v28 columns absent → simulates legacy row
    }
    strat = _make_strategist_with_thesis_row(thesis_row, pos)
    prompt = await strat._build_position_prompt()
    assert "FLIPPED via APEX from Buy to Sell" in prompt
    assert "Qwen flipped on confidence asymmetry" in prompt
    assert "FLIPPED via XRAY" not in prompt


@pytest.mark.asyncio
async def test_callb_prompt_no_flip_notice_for_non_flipped_position() -> None:
    """Non-flipped positions render no FLIPPED notice."""
    pos = SimpleNamespace(
        symbol="LTCUSDT",
        side=SimpleNamespace(value="Buy"),
        entry_price=85.0,
        mark_price=86.0,
        size=1.0,
        leverage=3,
    )
    thesis_row = {
        "symbol": "LTCUSDT",
        "direction": "Buy",
        "stop_loss_price": 83.0,
        "take_profit_price": 90.0,
        "leverage": 3,
        "apex_flipped": 0,
        "apex_original_direction": "",
        "apex_reason": "",
        "xray_flip_source": "",
        "xray_flip_ratio": 0.0,
    }
    strat = _make_strategist_with_thesis_row(thesis_row, pos)
    prompt = await strat._build_position_prompt()
    # The contract section (Sub-phase 1D) mentions "FLIPPED" once
    # ("For positions marked FLIPPED below: ..."). What we assert is
    # that the per-position notice line "FLIPPED via XRAY/APEX" is
    # absent for non-flipped positions.
    assert "FLIPPED via XRAY" not in prompt
    assert "FLIPPED via APEX" not in prompt
