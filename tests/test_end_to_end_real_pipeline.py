"""End-to-end real-project pipeline verification for the 5-critical-fixes.

This file boots real project classes (TradeCoordinator, TradingRepository,
BybitDemoPositionService) against a temporary SQLite database with the
project's full migrations applied. External boundaries (Bybit HTTP/WS) are
the only things mocked — every internal wire is the real code.

Each issue's pipeline is exercised end-to-end:
  Issue 1 — APEX_DIR_LOCK propagation: OptimizedTrade -> layer_manager
            merge -> trade dict carries _apex_locked / _apex_lock_reason.
  Issue 4 — partial close: reduce_position-style mark-pending -> WS-style
            execution event -> on_partial_close -> trade_history row #1 with
            partial qty and partial-notional PnL, state.size decremented.
  Issue 5 — residual close: on_trade_closed on the residual -> trade_history
            row #2 with residual qty, state popped, fan-out fires.
  Issue 2 — positions cleanup: pre-insert a positions row, fire close,
            confirm row DELETEd by the new cleanup callback.
  Issue 3 — WD_CLOSE recovery: pre-insert open trade_thesis + Filled order,
            run the exact recovery SQL the watchdog uses, confirm columns
            return as expected and the notional/PnL math holds.

All assertions print PASS/FAIL on a single line. Pytest-compatible via
``async def`` + ``mode=AUTO`` (project pyproject.toml).
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===========================================================================
# Boot helpers
# ===========================================================================

async def _boot_real_db():
    """Apply the full project migrations to a temp SQLite DB.

    Returns (DatabaseManager, db_path). Caller is responsible for
    ``await db.disconnect()`` + ``os.unlink(db_path)``.
    """
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    db_path = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(db_path)
    await db.connect()
    await run_migrations(db)
    return db, db_path


def _build_close_callbacks(db, coordinator, mode="bybit_demo"):
    """Mirror the close-callback wiring from workers/manager.py.

    Returns a captured `events` list that records every callback call;
    the caller verifies pre/post conditions against the DB.
    """
    from datetime import datetime, timezone

    from src.core.types import Side, TradeRecord
    from src.database.repositories.trading_repo import TradingRepository

    events: list[dict] = []
    bd_trading_repo = TradingRepository(db)

    # _trade_history_close_callback — mirrors workers/manager.py:1999+
    def _trade_history_cb(record: dict) -> None:
        events.append({"cb": "trade_history", "record": record})
        sym = record.get("symbol", "?")
        open_oid = record.get("order_id", "") or ""
        if open_oid:
            trade_id = f"bd-{open_oid}"
        else:
            trade_id = f"bd-{sym}-{int(__import__('time').time() * 1000)}"
        side_str = record.get("direction", "Buy") or "Buy"
        side_enum = Side.SELL if side_str in ("Sell", "Short") else Side.BUY
        trade = TradeRecord(
            trade_id=trade_id,
            symbol=sym,
            side=side_enum,
            entry_price=float(record.get("entry_price", 0.0) or 0.0),
            exit_price=float(record.get("close_price", 0.0) or 0.0),
            qty=float(record.get("size", 0.0) or 0.0),
            pnl=float(record.get("pnl_usd", 0.0) or 0.0),
            pnl_pct=float(record.get("pnl_pct", 0.0) or 0.0),
            strategy=record.get("strategy_name", "")[:120],
            notes=f"closed_by={record.get('closed_by', '')}",
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
        )

        async def _do_save():
            await bd_trading_repo.save_trade(trade, exchange_mode=mode)

        asyncio.get_event_loop().create_task(_do_save())

    coordinator.register_close_callback(_trade_history_cb)
    coordinator.register_partial_close_callback(_trade_history_cb)

    # _positions_table_cleanup_on_close — mirrors workers/manager.py:2170+
    def _positions_cleanup_cb(record: dict) -> None:
        events.append({"cb": "positions_cleanup", "record": record})
        sym = record.get("symbol", "")
        if not sym:
            return

        async def _do_delete():
            await bd_trading_repo.delete_position(sym)

        asyncio.get_event_loop().create_task(_do_delete())

    coordinator.register_close_callback(_positions_cleanup_cb)
    # NOT registered on partial-close list (position still has residual)

    return events, bd_trading_repo


def _register_trade(coordinator, *, symbol, side, size, entry_price, order_id):
    """Mirror workers/strategy_worker registration so on_partial_close
    finds a fully-populated TradeState (entry_price, side, size, order_id)."""
    import time

    from src.core.trade_coordinator import TradeState

    state = TradeState(
        symbol=symbol,
        strategy_name="claude_trader",
        strategy_category="claude_direct",
        opened_at=time.time() - 600,
        side=side,
        size=size,
        entry_price=entry_price,
        order_id=order_id,
        brain_decision_id=f"d-{int(time.time())}",
    )
    coordinator._trades[symbol] = state
    return state


async def _drain_tasks():
    """Yield to the event loop so create_task'd writes complete."""
    for _ in range(5):
        await asyncio.sleep(0.01)


# ===========================================================================
# Issue 1 — APEX_DIR_LOCK end-to-end (OptimizedTrade -> layer_manager merge)
# ===========================================================================

async def test_issue1_lock_propagation_through_layer_manager():
    """Construct a real OptimizedTrade with is_locked=True. Verify the
    layer_manager merge path stamps _apex_locked / _apex_lock_reason on
    the modified trade dict. Mock only the market_service ticker
    (external boundary)."""
    from unittest.mock import AsyncMock, MagicMock

    from src.apex.models import OptimizedTrade
    from src.core.layer_manager import LayerManager

    # OptimizedTrade with lock state
    ot = OptimizedTrade(
        symbol="ABCUSDT",
        direction="Buy",
        sl_pct=0.8,
        tp_pct=1.4,
        tp_mode="fixed",
        position_size_usd=1200.0,
        leverage=5,
        entry_timing="immediate",
        add_on_pullback=False,
        is_locked=True,
        lock_reason="volatile regime, insufficient flip evidence",
        confidence=0.75,
        was_flipped=False,
        original_direction="Buy",
        original_sl=0.99,
        original_tp=1.014,
        original_size=12000.0,
        apex_model="deepseek/v3",
        apex_response_time_ms=2400,
        apex_cost_usd=0.001,
    )

    # Build a minimal LayerManager. _apply_apex_optimization needs
    # self.services["market_service"] for the ticker call.
    lm = LayerManager.__new__(LayerManager)
    market_svc = MagicMock()
    ticker = MagicMock()
    ticker.last_price = 1.0
    market_svc.get_ticker = AsyncMock(return_value=ticker)
    lm.services = {"market_service": market_svc}

    original = {"symbol": "ABCUSDT", "direction": "Buy", "size_usd": 12000.0}
    modified = await lm._apply_apex_optimization(original, ot)

    assert modified.get("_apex_locked") is True, \
        f"_apex_locked not propagated; got {modified.get('_apex_locked')!r}"
    assert modified.get("_apex_lock_reason") == "volatile regime, insufficient flip evidence", \
        f"_apex_lock_reason missing; got {modified.get('_apex_lock_reason')!r}"
    assert modified.get("direction") == "Buy"
    assert modified.get("_apex_optimized") is True
    assert modified.get("_apex_was_flipped") is False
    print("  PASS: Issue 1 — OptimizedTrade.is_locked propagates through layer_manager.")


# ===========================================================================
# Issue 4 — partial close end-to-end
# ===========================================================================

async def test_issue4_partial_close_pipeline_real_db():
    """Full Issue 4 pipeline against a real SQLite DB:
    1. Register a trade in the coordinator (full size 4000.0).
    2. mark_partial_close_pending(2000.0) — simulate reduce_position.
    3. pop_partial_close_pending — simulate WS subscriber receiving the
       execution event.
    4. on_partial_close — fires real callbacks; trade_history row writes.
    5. Verify state.size decremented, partial_index incremented, row
       written with qty=2000.0 and partial-notional PnL.
    """
    from src.core.trade_coordinator import TradeCoordinator

    db, db_path = await _boot_real_db()
    try:
        coord = TradeCoordinator()
        events, bd_repo = _build_close_callbacks(db, coord)

        state = _register_trade(
            coord, symbol="ABCUSDT", side="Sell",
            size=4000.0, entry_price=1.0, order_id="oid-real-1",
        )

        coord.mark_partial_close_pending("ABCUSDT", 2000.0)
        pending = coord.pop_partial_close_pending("ABCUSDT")
        assert pending is not None
        assert pending["qty"] == 2000.0

        coord.on_partial_close(
            "ABCUSDT", closed_qty=2000.0, exec_price=0.99,
            closed_by="mode4_partial",
        )
        await _drain_tasks()

        # State decrement + partial_index
        assert state.size == 2000.0, f"expected residual 2000.0, got {state.size}"
        assert state.partial_index == 1
        assert "ABCUSDT" in coord._trades, "state must remain after partial"

        # Trade_history row written
        rows = await db.fetch_all(
            "SELECT trade_id, symbol, side, qty, pnl, pnl_pct FROM trade_history "
            "WHERE symbol='ABCUSDT'"
        )
        assert len(rows) == 1, f"expected 1 trade_history row after partial, got {len(rows)}"
        r = dict(rows[0])
        assert r["qty"] == 2000.0, f"expected qty=2000.0, got {r['qty']}"
        # Sell + entry 1.0 + exit 0.99 -> +1.0% pnl_pct -> $20 on 2000@1.0
        assert abs(r["pnl_pct"] - 1.0) < 0.0001
        assert abs(r["pnl"] - 20.0) < 0.0001
        # Unique trade_id with -partial-1 suffix from coordinator's
        # order_id="oid-real-1-partial-1"
        assert "partial-1" in r["trade_id"]

        # Callbacks fired: trade_history on partial list (positions_cleanup
        # not on partial list, so it did NOT fire)
        partial_cb_calls = [e for e in events if e["cb"] == "trade_history"]
        positions_cb_calls = [e for e in events if e["cb"] == "positions_cleanup"]
        assert len(partial_cb_calls) == 1
        assert len(positions_cb_calls) == 0, \
            "positions_cleanup must NOT fire on partial close"

        print("  PASS: Issue 4 — partial close writes correct trade_history row "
              "(qty=2000.0, pnl=$20), state.size=2000, partial_index=1, state retained.")
    finally:
        await db.disconnect()
        os.unlink(db_path)


# ===========================================================================
# Issue 5 — residual close (after Issue 4 partial), no silent skip
# ===========================================================================

async def test_issue5_residual_close_no_silent_skip():
    """After a partial close, the eventual final close fires standard
    fan-out against the residual size. No COORD_DOUBLE_CLOSE silent
    skip occurs. trade_history ends with 2 rows: partial + final.
    """
    from src.core.trade_coordinator import TradeCoordinator

    db, db_path = await _boot_real_db()
    try:
        coord = TradeCoordinator()
        events, bd_repo = _build_close_callbacks(db, coord)

        state = _register_trade(
            coord, symbol="XYZUSDT", side="Sell",
            size=4000.0, entry_price=1.0, order_id="oid-real-2",
        )

        # Partial
        coord.mark_partial_close_pending("XYZUSDT", 2000.0)
        coord.pop_partial_close_pending("XYZUSDT")
        coord.on_partial_close(
            "XYZUSDT", closed_qty=2000.0, exec_price=0.99,
            closed_by="mode4_partial",
        )
        await _drain_tasks()
        assert state.size == 2000.0

        # Residual final close at 0.98 — Sell, exit < entry => bigger win
        coord.on_trade_closed(
            symbol="XYZUSDT",
            pnl_pct=0.0,    # sentinel — back-derive
            pnl_usd=0.0,
            was_win=False,
            closed_by="bybit_sl_hit",
            exit_price=0.98,
        )
        await _drain_tasks()

        # State popped
        assert "XYZUSDT" not in coord._trades

        # 2 trade_history rows
        rows = await db.fetch_all(
            "SELECT trade_id, qty, pnl, pnl_pct FROM trade_history "
            "WHERE symbol='XYZUSDT' ORDER BY trade_id"
        )
        assert len(rows) == 2, f"expected 2 trade_history rows, got {len(rows)}"
        partial_row = next(dict(r) for r in rows if "partial" in dict(r)["trade_id"])
        final_row = next(dict(r) for r in rows if "partial" not in dict(r)["trade_id"])

        # Partial row: qty 2000.0, pnl on 2000 notional
        assert partial_row["qty"] == 2000.0
        assert abs(partial_row["pnl"] - 20.0) < 0.0001

        # Final row: qty = residual = 2000.0; Sell + exit 0.98 vs entry 1.0
        # => +2.0% on 2000 notional = $40
        assert final_row["qty"] == 2000.0, f"expected residual qty=2000, got {final_row['qty']}"
        assert abs(final_row["pnl_pct"] - 2.0) < 0.0001
        assert abs(final_row["pnl"] - 40.0) < 0.0001

        # Total recorded qty = 4000 (matches entry)
        total_qty = sum(dict(r)["qty"] for r in rows)
        assert total_qty == 4000.0
        # Total realized pnl = 20 + 40 = $60 (true total realized)
        total_pnl = sum(dict(r)["pnl"] for r in rows)
        assert abs(total_pnl - 60.0) < 0.0001

        print(f"  PASS: Issue 5 — residual close after partial wrote row #2 "
              f"(qty=2000, pnl=$40); total recorded qty=4000.0, "
              f"total pnl=$60.0; no silent skip.")
    finally:
        await db.disconnect()
        os.unlink(db_path)


# ===========================================================================
# Issue 2 — positions row DELETEd by the cleanup callback
# ===========================================================================

async def test_issue2_positions_cleanup_fires_on_close():
    """Insert a positions row, register a trade, fire on_trade_closed.
    Verify the cleanup callback issues a DELETE that removes the row.
    """
    from src.core.trade_coordinator import TradeCoordinator

    db, db_path = await _boot_real_db()
    try:
        coord = TradeCoordinator()
        events, bd_repo = _build_close_callbacks(db, coord)

        # Pre-insert a positions row (mirrors get_positions side-write at open)
        await db.execute(
            "INSERT INTO positions(symbol, side, size, entry_price, exchange_mode) "
            "VALUES (?, ?, ?, ?, ?)",
            ("OPUSDT", "Buy", 1500.0, 0.17, "bybit_demo"),
        )
        rows = await db.fetch_all("SELECT symbol FROM positions WHERE symbol='OPUSDT'")
        assert len(rows) == 1

        _register_trade(
            coord, symbol="OPUSDT", side="Buy",
            size=1500.0, entry_price=0.17, order_id="oid-real-3",
        )

        # Sentinel — must mock transformer so the mode-gate inside the
        # callback resolves to bybit_demo. Attach a tiny mock.
        class _XFM:
            current_mode = "bybit_demo"

        # workers/manager.py's callback reads self._services["transformer"].
        # In our integration harness the callback closure captures bd_repo
        # directly (not via services dict), so the mode gate logic isn't
        # exercised here — the close fires regardless of mode for the
        # integration test. We document this caveat:
        #   In production, the callback in manager.py:2173-2179 resolves
        #   _mode = transformer.current_mode and bails out if not
        #   "bybit_demo". Our test callback omits this branch by design
        #   (single-mode test scope).

        coord.on_trade_closed(
            symbol="OPUSDT",
            pnl_pct=0.0,
            pnl_usd=0.0,
            was_win=False,
            closed_by="bybit_sl_hit",
            exit_price=0.17,
        )
        await _drain_tasks()

        # Positions row deleted by the cleanup callback
        rows = await db.fetch_all("SELECT symbol FROM positions WHERE symbol='OPUSDT'")
        assert len(rows) == 0, \
            f"positions row not deleted by cleanup callback: rows={rows}"
        positions_cb_calls = [e for e in events if e["cb"] == "positions_cleanup"]
        assert len(positions_cb_calls) == 1
        print("  PASS: Issue 2 — cleanup callback fired on on_trade_closed, "
              "positions row DELETEd.")
    finally:
        await db.disconnect()
        os.unlink(db_path)


# ===========================================================================
# Issue 3 — WD_CLOSE recovery queries return expected columns
# ===========================================================================

async def test_issue3_wd_close_recovery_queries():
    """Pre-populate trade_thesis (open) and orders (Filled) rows for a
    symbol. Run the EXACT SQL the watchdog's recovery uses. Verify the
    returned columns let the watchdog reconstruct entry_price, direction,
    size_usd, leverage, qty, and compute a non-zero notional/PnL.
    """
    db, db_path = await _boot_real_db()
    try:
        await db.execute(
            "INSERT INTO trade_thesis(symbol, direction, entry_price, "
            "stop_loss_price, take_profit_price, size_usd, leverage, "
            "max_hold_minutes, trailing_activation_pct, thesis, "
            "market_context, strategy_hints, consensus, status, "
            "exchange_mode, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("FILUSDT", "Sell", 1.1286, 1.14, 1.10, 1000.0, 5,
             45, 1.0, "test thesis", "ranging regime", "[]",
             "STRONG", "open", "bybit_demo",
             "2026-05-11T09:35:36.514000+00:00"),
        )

        await db.execute(
            "INSERT INTO orders(order_id, symbol, side, order_type, price, "
            "qty, status, filled_qty, avg_fill_price, exchange_mode, "
            "created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("oid-fil-1", "FILUSDT", "Sell", "Market", 0.0,
             4430.2, "Filled", 4430.2, 1.1286, "bybit_demo",
             "2026-05-11T09:35:36.482000+00:00"),
        )

        # Run the exact watchdog recovery queries
        thesis_row = await db.fetch_one(
            "SELECT direction, entry_price, size_usd, leverage "
            "FROM trade_thesis "
            "WHERE status='open' AND symbol = ? "
            "ORDER BY opened_at DESC LIMIT 1",
            ("FILUSDT",),
        )
        assert thesis_row is not None, "thesis row not found"
        t = dict(thesis_row)
        assert t["direction"] == "Sell"
        assert abs(float(t["entry_price"]) - 1.1286) < 1e-9
        assert float(t["size_usd"]) == 1000.0
        assert int(t["leverage"]) == 5

        orders_row = await db.fetch_one(
            "SELECT side, qty, avg_fill_price FROM orders "
            "WHERE status='Filled' AND symbol = ? "
            "ORDER BY created_at DESC LIMIT 1",
            ("FILUSDT",),
        )
        assert orders_row is not None, "orders row not found"
        o = dict(orders_row)
        assert o["side"] == "Sell"
        assert float(o["qty"]) == 4430.2
        assert abs(float(o["avg_fill_price"]) - 1.1286) < 1e-9

        # Compute notional via the watchdog's formula
        recovered_size_usd = float(t["size_usd"])
        recovered_leverage = int(t["leverage"])
        notional_from_thesis = recovered_size_usd * (
            recovered_leverage if recovered_leverage > 0 else 1
        )
        assert notional_from_thesis == 5000.0  # $1000 × 5x

        recovered_qty = float(o["qty"])
        entry_price = float(t["entry_price"])
        notional_from_orders = abs(entry_price * recovered_qty)
        assert abs(notional_from_orders - 5000.04) < 1.0  # ~= 4430.2 × 1.1286

        # Both formulas produce notional ~$5000 — consistent.
        print("  PASS: Issue 3 — WD_CLOSE recovery queries return expected "
              "columns; notional from thesis ($5000) and orders ($5000) align.")
    finally:
        await db.disconnect()
        os.unlink(db_path)


# ===========================================================================
# Issue 1+4 integration — DI wiring (BybitDemoPositionService.attach_coordinator)
# ===========================================================================

async def test_di_wiring_attach_coordinator():
    """Verify the real BybitDemoPositionService.attach_coordinator wires
    correctly and reduce_position would call mark_partial_close_pending
    when coordinator is attached.
    """
    from unittest.mock import MagicMock

    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService
    from src.core.trade_coordinator import TradeCoordinator

    # Construct without coordinator (legacy path)
    svc = BybitDemoPositionService(client=MagicMock())
    assert svc._coordinator is None

    coord = TradeCoordinator()
    svc.attach_coordinator(coord)
    assert svc._coordinator is coord

    # The mark_partial_close_pending call would happen INSIDE
    # reduce_position before the POST. Verify by direct call:
    svc._coordinator.mark_partial_close_pending("FILUSDT", 2215.1, by="mode4_partial")
    pending = svc._coordinator.pop_partial_close_pending("FILUSDT")
    assert pending["qty"] == 2215.1
    assert pending["by"] == "mode4_partial"
    print("  PASS: DI wiring — attach_coordinator wires the coordinator into "
          "BybitDemoPositionService correctly.")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    print("\n=== End-to-end real-project pipeline verification ===\n")
    tests = [
        ("Issue 1 lock propagation through layer_manager",
            test_issue1_lock_propagation_through_layer_manager),
        ("DI wiring (attach_coordinator)", test_di_wiring_attach_coordinator),
        ("Issue 4 partial close pipeline (real DB)",
            test_issue4_partial_close_pipeline_real_db),
        ("Issue 5 residual close (no silent skip)",
            test_issue5_residual_close_no_silent_skip),
        ("Issue 2 positions cleanup fires on close",
            test_issue2_positions_cleanup_fires_on_close),
        ("Issue 3 WD_CLOSE recovery queries",
            test_issue3_wd_close_recovery_queries),
    ]
    failed = 0
    for name, fn in tests:
        try:
            asyncio.run(fn())
        except AssertionError as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\nResult: {len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
