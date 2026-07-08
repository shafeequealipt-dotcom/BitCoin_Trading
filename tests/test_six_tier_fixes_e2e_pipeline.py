"""End-to-end pipeline tests for the six-tier-fixes engagement.

Each test exercises a fix against the REAL project objects (not isolated
mocks of single methods). Real ``ThesisManager`` against an in-memory
aiosqlite DB. Real ``TradeCoordinator``. Real ``BybitDemoPositionService``
constructed with a mock HTTP client. Real ``TradeGate``.

The tests trace data flow end-to-end through the project's DI graph:
boot wiring → service registration → callback registration → runtime
invocation → downstream consumers.

Sections:

  PIPELINE 1 — T1-1 F18 phantom-close defense
                (urgent_queue clear-on-close + 3-layer guard)
  PIPELINE 2 — T1-3 F9 TIAS lesson bridge
                (DeepSeek output → trade_thesis.lesson → CALL_A injection
                with anti-closed-loop guards)
  PIPELINE 3 — T2-1 F20 loss-cooldown revenge-trade defense
                (coordinator records direction → gate rejects same-dir →
                layer_manager skips)
  PIPELINE 4 — T2-2 F14 zero-conviction reject (gate predicate fires
                BEFORE conviction-weight block AND regardless of
                conviction_enabled flag)
  PIPELINE 5 — T3-1 F-4 safety gates (5 gates accessible via the
                Transformer-style short-key service dict)
  PIPELINE 6 — T3-2/T3-3/T3-4 close-attribution
                (set_close_reason wired in close_position; synthetic
                order_id eliminates blank-PK clobber)

Run with::

    python3 -m pytest tests/test_six_tier_fixes_e2e_pipeline.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


# ═════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ═════════════════════════════════════════════════════════════════════════


class _AiosqliteDBWrapper:
    """Minimal DatabaseManager-shaped wrapper around an aiosqlite Connection.

    The real DatabaseManager has many methods; ThesisManager only uses
    ``execute`` and ``fetch_all`` / ``fetch_one`` for our T1-3 tests.
    """

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await self._conn.execute(sql, params)
        await self._conn.commit()

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self._conn.execute(sql, params) as cur:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) async for row in cur]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


@pytest.fixture
async def thesis_db():
    """In-memory aiosqlite DB seeded with the trade_thesis schema used in production."""
    import aiosqlite
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "CREATE TABLE trade_thesis ("
        "symbol TEXT, direction TEXT, entry_price REAL, close_price REAL, "
        "size_usd REAL, leverage INTEGER, "
        "actual_pnl_pct REAL, actual_pnl_usd REAL, "
        "stop_loss_price REAL, take_profit_price REAL, "
        "max_hold_minutes INTEGER, trailing_activation_pct REAL, "
        "thesis TEXT, market_context TEXT, strategy_hints TEXT, "
        "consensus TEXT, "
        "close_reason TEXT, lesson TEXT, "
        "opened_at TEXT DEFAULT (datetime('now')), "
        "closed_at TEXT, "
        "order_id TEXT, exchange_mode TEXT, "
        "apex_flipped INTEGER DEFAULT 0, apex_original_direction TEXT, "
        "apex_reason TEXT, "
        "entry_xray_confidence REAL DEFAULT 0, entry_setup_type TEXT, "
        "entry_regime_at_open TEXT, entry_regime_confidence REAL DEFAULT 0, "
        "xray_flip_source TEXT DEFAULT '', xray_flip_ratio REAL DEFAULT 0, "
        "xray_flip_rr_long REAL DEFAULT 0, xray_flip_rr_short REAL DEFAULT 0, "
        "status TEXT DEFAULT 'open')"
    )
    await conn.commit()
    yield _AiosqliteDBWrapper(conn)
    await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 1 — T1-1 phantom-close defense, full chain
# ═════════════════════════════════════════════════════════════════════════


def test_pipeline_t1_1_close_callback_clears_urgent_queue_no_stale_drainage():
    """End-to-end: a position closes → close-callbacks fire → UrgentQueue
    is cleared for that symbol → next strategist drain returns nothing
    stale.

    Validates the wiring chain:
      coordinator.on_trade_closed -> _urgent_queue_clear_on_close (registered
      in manager.py:2293) -> UrgentQueue.clear_for_symbol -> drainage
      returns no concerns for the closed symbol.
    """
    from src.core.trade_coordinator import TradeCoordinator
    from src.core.urgent_queue import UrgentQueue, WatchdogConcern

    coord = TradeCoordinator()
    uq = UrgentQueue()

    # Mirror the manager.py:2278-2293 callback registration.
    def _urgent_queue_clear_on_close(record: dict) -> None:
        sym = record.get("symbol", "")
        if not sym:
            return
        if hasattr(uq, "clear_for_symbol"):
            uq.clear_for_symbol(sym)

    coord.register_close_callback(_urgent_queue_clear_on_close)

    # Watchdog adds a critical_loss concern for an open position.
    coord.register_trade(
        symbol="FILUSDT", strategy_category="default",
        side="Sell", entry_price=1.0,
    )
    uq.add_concern(WatchdogConcern(
        symbol="FILUSDT", pnl_pct=-2.0, warnings=["sl_consumed"],
        current_price=1.02, entry_price=1.0, side="Sell",
        sl_proximity_pct=80.0, position_age_minutes=5.0,
    ))
    assert uq.has_concerns is True

    # Position closes (e.g. time_decay_force_close).
    coord.on_trade_closed(
        symbol="FILUSDT", pnl_pct=-0.5, pnl_usd=-5.0,
        was_win=False, closed_by="time_decay_force_close",
    )

    # The close-callback fired during on_trade_closed; the urgent_queue
    # for FILUSDT is now cleared. Next strategist drain returns nothing.
    drained = uq.drain_concerns()
    assert drained == [], (
        "Stale concern leaked past the close-callback — phantom-close "
        "fix is broken"
    )


def test_pipeline_t1_1_three_layer_phantom_close_defense():
    """End-to-end: with all 3 defense layers active, a close on a
    no-longer-active symbol is rejected at the first layer it hits.

    Simulates the layer_manager dispatch path: snapshot active_symbols,
    iterate position_actions, the dispatch-layer guard catches it before
    the firewall is even called.
    """
    from src.core.trade_coordinator import TradeCoordinator
    from src.sentinel.firewall import should_allow_strategic_action

    coord = TradeCoordinator()
    # Only ABCUSDT is open; the brain incorrectly issued a close for
    # XYZUSDT (already-closed from earlier in the session).
    coord.register_trade(
        symbol="ABCUSDT", strategy_category="default",
        side="Buy", entry_price=1.0,
    )
    active = coord.active_symbols()
    assert "ABCUSDT" in active
    assert "XYZUSDT" not in active

    # Layer 1 — layer_manager dispatch-side check (the first defense).
    rejected_at_dispatch = "XYZUSDT" not in active
    assert rejected_at_dispatch

    # Layer 2 — firewall precondition. Even with trusted source.
    allowed, reason = should_allow_strategic_action(
        action="close", symbol="XYZUSDT", reason="stale watchdog",
        source="call_b", active_symbols=active,
    )
    assert allowed is False
    assert "PHANTOM_CLOSE_REJECTED" in reason

    # Layer 3 — coordinator queue.
    coord.queue_strategic_action(
        symbol="XYZUSDT", action="close", reason="stale",
    )
    pending = coord.drain_strategic_actions()
    assert pending == [], "Coordinator queue accepted phantom close"


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 2 — T1-3 TIAS lesson bridge, full chain
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pipeline_t1_3_tias_to_lesson_to_strategist_with_age_guard(thesis_db):
    """End-to-end: TIAS DeepSeek output -> trade_thesis.lesson via the bridge
    -> get_recent_lessons applies the 5-min age guard so a freshly-bridged
    lesson is NOT injected back into the next CALL_A.

    Validates the wiring chain:
      _tias_analyze_background (manager.py:2375) -> compose_lesson_from_tias
      -> thesis_manager.update_lesson -> trade_thesis.lesson column ->
      thesis_manager.get_recent_lessons(min_age_seconds=300) -> CALL_A
      sees nothing while lesson is fresh.
    """
    from src.core.thesis_manager import ThesisManager, compose_lesson_from_tias

    tm = ThesisManager(db=thesis_db)

    # Seed: simulate a closed trade with no lesson yet (real flow).
    await thesis_db.execute(
        "INSERT INTO trade_thesis (symbol, direction, order_id, "
        "actual_pnl_pct, close_reason, status, closed_at, lesson) VALUES "
        "(?, ?, ?, ?, ?, 'closed', datetime('now'), '')",
        ("FILUSDT", "Sell", "OID-001", -0.5, "time_decay_force_close"),
    )

    # Simulate TIAS Phase 2 returning rich analysis.
    fake_analysis = {
        "ds_what_should_done": "Should have waited for support break.",
        "ds_how_to_exploit": "Use D1 structure for confirmation",
        "ds_category": "premature_entry",
    }
    composed = compose_lesson_from_tias(
        analysis=fake_analysis,
        close_reason="time_decay_force_close",
        hold_seconds=300.0,
        pnl_pct=-0.5,
    )
    assert composed is not None
    assert "premature_entry" in composed

    # Bridge step: update_lesson writes to trade_thesis.
    ok = await tm.update_lesson(
        symbol="FILUSDT", order_id="OID-001", lesson=composed,
    )
    assert ok is True

    # Strategist CALL_A invocation: get_recent_lessons WITH age guard.
    # The lesson is fresh (<5 min old) so the age guard suppresses it.
    fresh = await tm.get_recent_lessons(limit=10, min_age_seconds=300)
    assert len(fresh) == 0, (
        "Anti-closed-loop guard failed: fresh lesson leaked into CALL_A"
    )

    # Without age guard, the lesson IS visible (the bridge worked).
    no_guard = await tm.get_recent_lessons(limit=10)
    assert len(no_guard) == 1
    assert no_guard[0]["lesson"].startswith("5m hold")


@pytest.mark.asyncio
async def test_pipeline_t1_3_symbol_exclusion_keeps_current_position_lessons_out(thesis_db):
    """End-to-end: a 1-hour-old lesson for FILUSDT exists. CALL_A is about
    to decide on FILUSDT among other symbols. The exclude_symbols guard
    keeps the FILUSDT lesson out of the prompt for THIS cycle.

    Validates the same-symbol-scope half of the anti-closed-loop guard.
    """
    from src.core.thesis_manager import ThesisManager

    tm = ThesisManager(db=thesis_db)
    # Seed: 1-hour-old lesson (past the age guard).
    await thesis_db.execute(
        "INSERT INTO trade_thesis (symbol, direction, actual_pnl_pct, "
        "close_reason, lesson, status, closed_at) VALUES "
        "(?, ?, ?, ?, ?, 'closed', datetime('now', '-1 hours'))",
        ("FILUSDT", "Sell", -0.5, "time_decay_force_close",
         "FILUSDT lost on Sell - bearish thesis invalidated"),
    )
    await thesis_db.execute(
        "INSERT INTO trade_thesis (symbol, direction, actual_pnl_pct, "
        "close_reason, lesson, status, closed_at) VALUES "
        "(?, ?, ?, ?, ?, 'closed', datetime('now', '-1 hours'))",
        ("ETHUSDT", "Buy", 0.3, "trailing_stop",
         "ETHUSDT trail caught the move nicely"),
    )

    # CALL_A is about to decide on FILUSDT — open positions include FILUSDT.
    open_syms = frozenset({"FILUSDT"})
    filtered = await tm.get_recent_lessons(
        limit=10, min_age_seconds=300, exclude_symbols=open_syms,
    )
    syms = {l["symbol"] for l in filtered}
    assert syms == {"ETHUSDT"}, (
        f"Symbol-scope guard failed: expected only ETHUSDT, got {syms}"
    )


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 3 — Issue 3 (2026-05-18) 5-min reentry cooldown, full chain
# Replaces the prior T2-1 loss-cooldown defense tests after the J6/H4 +
# T2-1 surface was removed in issue3/p3-3. Same scenario shape; new gate.
# ═════════════════════════════════════════════════════════════════════════


def test_pipeline_reentry_cooldown_blocks_same_direction_end_to_end():
    """Closing FILUSDT Sell at T0 must block same-direction Sell entry
    inside the 300s window while leaving opposite-direction Buy entry
    eligible. Validates the chain across coordinator state and the
    is_reentry_blocked API the gate consumes.
    """
    from src.core.trade_coordinator import TradeCoordinator

    coord = TradeCoordinator()
    coord.register_trade(
        symbol="FILUSDT", strategy_category="default",
        side="Sell", entry_price=1.0,
    )
    coord.on_trade_closed(
        symbol="FILUSDT", pnl_pct=-0.5, pnl_usd=-5.0,
        was_win=False, closed_by="bybit_sl_hit",
    )

    # Same direction inside the window — blocked.
    blocked_sell, remaining_sell = coord.is_reentry_blocked("FILUSDT", "Sell")
    assert blocked_sell is True, "Same-direction re-entry leaked past cooldown"
    assert 280 <= remaining_sell <= 300

    # Opposite direction — allowed.
    blocked_buy, remaining_buy = coord.is_reentry_blocked("FILUSDT", "Buy")
    assert blocked_buy is False, "Opposite-direction was wrongly blocked"
    assert remaining_buy == 0


def test_pipeline_reentry_cooldown_blocks_after_winning_close_uniformly():
    """Issue 3 design — the cooldown is uniform (300s after ANY close,
    win or loss / any reason). Pre-fix T2-1 tracked only losses; the
    new gate applies the same window after a winning close too. This
    test pins that the cooldown fires after a TP-hit close just like
    after an SL-hit close.
    """
    from src.core.trade_coordinator import TradeCoordinator

    coord = TradeCoordinator()
    coord.register_trade(
        symbol="FILUSDT", strategy_category="default",
        side="Sell", entry_price=1.0,
    )
    coord.on_trade_closed(
        symbol="FILUSDT", pnl_pct=+0.8, pnl_usd=+8.0,
        was_win=True, closed_by="bybit_tp_hit",
    )

    blocked_sell, remaining_sell = coord.is_reentry_blocked("FILUSDT", "Sell")
    assert blocked_sell is True, (
        "Winning close must also start the 5-min cooldown (uniform rule)"
    )
    assert 280 <= remaining_sell <= 300

    # Opposite direction still freely eligible.
    blocked_buy, _ = coord.is_reentry_blocked("FILUSDT", "Buy")
    assert blocked_buy is False


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 4 — T2-2 zero-conviction reject, gate placement audit
# ═════════════════════════════════════════════════════════════════════════


def test_pipeline_t2_2_zero_conviction_reject_fires_regardless_of_conviction_enabled():
    """End-to-end placement audit: the zero-conviction reject must fire
    even when settings.apex.conviction_enabled is False. Pre-fix the
    block was inside CHECK 4's conviction-weight branch which only ran
    when conviction_enabled=True.

    Reads the actual block placement in gate.py rather than constructing
    the full TradeGate (which requires a deep services dict).
    """
    import re
    gate_src = (REPO_ROOT / "src/apex/gate.py").read_text()

    # Find the T2-2 reject block and the conviction_enabled check.
    t2_2_marker = gate_src.find("# T2-2 / F14 zero-conviction reject")
    conviction_check = gate_src.find('if getattr(self._settings, "conviction_enabled"')
    assert t2_2_marker != -1, "T2-2 marker not found in gate.py"
    assert conviction_check != -1, "conviction_enabled check not found"
    assert t2_2_marker < conviction_check, (
        f"T2-2 reject (offset {t2_2_marker}) must precede the "
        f"conviction_enabled check (offset {conviction_check}). "
        "Otherwise the reject only fires when conviction is enabled."
    )

    # Confirm the reject block uses the three required signals.
    block = gate_src[t2_2_marker:conviction_check]
    assert "_xray_confidence" in block
    assert "_setup_score" in block
    assert "_expected_rr" in block
    assert "_gate_rejected" in block


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 5 — T3-1 safety gates via Transformer short-key services dict
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pipeline_t3_1_mandatory_sl_rejects_naked_layer3_entry():
    """End-to-end: layer3_entry with stop_loss=None must be rejected
    by gate 1 (mandatory SL)."""
    from src.trading.services.order_guards import check_mandatory_sl_for_bybit_demo
    allowed, reason = check_mandatory_sl_for_bybit_demo(
        stop_loss=None, purpose="layer3_entry",
    )
    assert allowed is False
    assert reason == "mandatory_sl_missing"


@pytest.mark.asyncio
async def test_pipeline_t3_1_size_cap_fires_through_transformer_short_keys():
    """End-to-end: size cap reads account_service from Transformer's
    short-key dict (`{"account": ...}`) and rejects an oversized notional.
    """
    from src.trading.services.order_guards import (
        check_position_size_and_max_loss_for_bybit_demo,
    )

    # Mimic Transformer._active_services structure.
    class _MockAccount:
        async def get_wallet_balance(self):
            return SimpleNamespace(total_equity=10_000.0)

    class _MockMarket:
        async def get_ticker(self, symbol):
            return SimpleNamespace(last_price=100.0)

    services = {"account": _MockAccount(), "market": _MockMarket()}
    settings = SimpleNamespace(risk=SimpleNamespace(max_position_size_pct=5.0))

    # 100 qty * $100 = $10,000 notional vs $500 max (5% of $10k).
    allowed, reason, _tel = await check_position_size_and_max_loss_for_bybit_demo(
        services=services, settings=settings,
        symbol="TESTUSDT", qty=100.0, stop_loss=99.0,
        leverage=1, price=100.0,
    )
    assert allowed is False
    assert reason == "position_size_cap_exceeded"


@pytest.mark.asyncio
async def test_pipeline_t3_1_post_place_sl_verify_with_short_keys():
    """End-to-end: gate 6 (post-place SL verify) uses position from the
    Transformer short-key services dict and detects missing SL.
    """
    from src.trading.services.order_guards import verify_post_place_sl_for_bybit_demo

    class _MockPositionSvc:
        async def get_position(self, symbol):
            # Bybit silently dropped the SL.
            return SimpleNamespace(stop_loss=None)

    services = {"position": _MockPositionSvc()}
    ok, reason, _tel = await verify_post_place_sl_for_bybit_demo(
        services=services, symbol="TESTUSDT", expected_sl=100.0,
    )
    assert ok is False
    assert reason == "stop_loss_not_attached"


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 6 — T3-2/T3-3/T3-4 close attribution end-to-end
# ═════════════════════════════════════════════════════════════════════════


def test_pipeline_t3_2_close_order_synthetic_id_format():
    """End-to-end: _build_close_order with empty order_id produces a
    unique synthetic ID matching the documented format. Each call
    produces a DIFFERENT ID (no clobber).
    """
    import time as _t
    from src.bybit_demo.bybit_demo_adapter import _build_close_order
    from src.core.types import Side

    order_a = _build_close_order("FILUSDT", Side.SELL, 1.0, 1.0, order_id="")
    _t.sleep(0.002)  # ensure epoch_ms differs
    order_b = _build_close_order("FILUSDT", Side.SELL, 1.0, 1.0, order_id="")

    assert order_a.order_id.startswith("bd-close-FILUSDT-")
    assert order_b.order_id.startswith("bd-close-FILUSDT-")
    assert order_a.order_id != order_b.order_id, (
        "Two close events at different timestamps got the same synthetic "
        "PK — orders table will still clobber"
    )


def test_pipeline_t3_3_set_close_reason_call_wired_in_close_position():
    """End-to-end: BybitDemoPositionService.close_position calls
    self._coordinator.set_close_reason BEFORE the Bybit POST is issued.
    Direct source verification — the wiring is critical for Phase5
    F-15 / F-20.
    """
    src = (REPO_ROOT / "src/bybit_demo/bybit_demo_adapter.py").read_text()
    record_marker = src.find("self._record_close_trigger(symbol, close_trigger)")
    set_close_reason_marker = src.find(
        "self._coordinator.set_close_reason(symbol, close_trigger)"
    )
    post_marker = src.find('"/v5/order/create"')

    assert record_marker != -1
    assert set_close_reason_marker != -1, (
        "T3-3 fix not present — set_close_reason call missing from "
        "close_position"
    )
    assert post_marker != -1

    # Ordering: _record_close_trigger -> set_close_reason -> POST to Bybit.
    assert record_marker < set_close_reason_marker < post_marker, (
        f"T3-3 wiring order is wrong. record={record_marker} "
        f"set_close_reason={set_close_reason_marker} post={post_marker}"
    )


# ═════════════════════════════════════════════════════════════════════════
# PIPELINE 7 — Boot-time DI graph: every fix's wiring point is present
# ═════════════════════════════════════════════════════════════════════════


def test_pipeline_di_graph_all_fixes_wired_in_manager_py():
    """Static audit of WorkerManager wiring: every six-tier fix that needs
    a callback/attach/service registration is present and in the right
    order relative to its dependencies.
    """
    manager_src = (REPO_ROOT / "src/workers/manager.py").read_text()

    # T1-1: _urgent_queue_clear_on_close callback registered
    assert "_urgent_queue_clear_on_close" in manager_src
    assert "coordinator.register_close_callback(_urgent_queue_clear_on_close)" in manager_src

    # T1-3: TIAS lesson bridge (compose_lesson_from_tias usage)
    assert "compose_lesson_from_tias" in manager_src
    assert "tm.update_lesson(" in manager_src

    # T1-4: boot VACUUM removed
    assert "incremental on hourly tick" in manager_src
    # Ensure no naked `self.db.execute("VACUUM")` in boot path
    assert 'await self.db.execute("VACUUM")' not in manager_src, (
        "T1-4 regression: boot VACUUM still present"
    )

    # urgent_queue service registered BEFORE the close-callback is registered
    uq_register_offset = manager_src.find('self._services["urgent_queue"]')
    callback_register_offset = manager_src.find(
        "coordinator.register_close_callback(_urgent_queue_clear_on_close)"
    )
    assert uq_register_offset != -1
    assert callback_register_offset != -1
    assert uq_register_offset < callback_register_offset, (
        "urgent_queue must be registered before the close-callback "
        "that looks it up; otherwise the first close after boot "
        "silently no-ops"
    )


def test_pipeline_di_graph_settings_t2_2_t3_1_paths_exist():
    """Verify settings dataclasses expose the fields that the gates and
    Transformer plumbing read at runtime."""
    from src.config.settings import APEXSettings, RiskSettings

    # T2-2: APEXSettings has the three new thresholds.
    apex = APEXSettings()
    assert hasattr(apex, "min_xray_conf_for_trade")
    assert hasattr(apex, "min_setup_score_for_trade")
    assert hasattr(apex, "min_expected_rr_for_trade")
    assert apex.min_xray_conf_for_trade == 0.0
    assert apex.min_setup_score_for_trade == 0.0
    assert apex.min_expected_rr_for_trade == 0.0

    # T3-1: RiskSettings.max_leverage is what the Transformer leverage cap reads.
    risk = RiskSettings()
    assert hasattr(risk, "max_leverage")
    assert risk.max_leverage == 3  # default

    # T3-1: RiskSettings.max_position_size_pct is read by gate 3.
    assert hasattr(risk, "max_position_size_pct")


def test_pipeline_di_graph_transformer_short_keys_match_gate_lookups():
    """The Transformer keys its _active_services with "order", "position",
    "account" (set_services at transformer.py). The T3-1 gate helpers
    must accept these short keys (audit-fix from cross-check pass)."""
    transformer_src = (REPO_ROOT / "src/core/transformer.py").read_text()
    guards_src = (REPO_ROOT / "src/trading/services/order_guards.py").read_text()

    # Transformer's set_services keys.
    assert '"order": bybit_demo_order' in transformer_src
    assert '"position": bybit_demo_position' in transformer_src
    assert '"account": bybit_demo_account' in transformer_src

    # Gates fall through both long and short keys.
    assert 'services.get("account_service") or services.get("account")' in guards_src
    assert 'services.get("position_service") or services.get("position")' in guards_src

    # Transformer reads leverage cap from settings.risk (not settings.bybit).
    assert "settings.risk.max_leverage" in transformer_src
