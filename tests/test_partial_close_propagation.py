"""Issue 4 Phase 3 — partial-close pathway tests.

Covers:
  - TradeState.partial_index field
  - TradeCoordinator.mark/pop_partial_close_pending roundtrip
  - TradeCoordinator.register_partial_close_callback wiring
  - TradeCoordinator.on_partial_close: builds record with closed_qty,
    decrements state.size, increments partial_index, fires partial
    callbacks but NOT full callbacks, guards invalid inputs

End-to-end exercise of reduce_position / WS subscriber is not in
scope here (those paths are integration-tested by the live worker
stack at deploy time); unit-test focus is on coordinator semantics.
"""

import os
import sys
import time as _time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_state(symbol="ABCUSDT", side="Buy", size=4000.0, entry=1.0):
    from src.core.trade_coordinator import TradeState

    return TradeState(
        symbol=symbol,
        strategy_name="claude_trader",
        strategy_category="claude_direct",
        opened_at=_time.time() - 600,
        side=side,
        size=size,
        entry_price=entry,
        order_id="oid-test-1",
        brain_decision_id="d-test-1",
    )


# ===========================================================================
# Test 1 — TradeState.partial_index default
# ===========================================================================

def test_trade_state_partial_index_default_zero():
    s = _make_state()
    assert s.partial_index == 0
    print("  PASS: TradeState.partial_index defaults to 0")


# ===========================================================================
# Test 2 — mark/pop_partial_close_pending roundtrip
# ===========================================================================

def test_mark_pop_pending_roundtrip():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    tc.mark_partial_close_pending("ABCUSDT", 2000.0, by="mode4_partial")
    pending = tc.pop_partial_close_pending("ABCUSDT")
    assert pending is not None
    assert pending["qty"] == 2000.0
    assert pending["by"] == "mode4_partial"
    assert "ts" in pending
    # second pop should return None (consumed)
    assert tc.pop_partial_close_pending("ABCUSDT") is None
    print("  PASS: mark/pop_partial_close_pending roundtrip works; consumed on pop")


def test_pop_without_mark_returns_none():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    assert tc.pop_partial_close_pending("NEVERSEEN") is None
    print("  PASS: pop without prior mark returns None")


# ===========================================================================
# Test 3 — on_partial_close with no state → warning, no callback
# ===========================================================================

def test_on_partial_close_without_state_no_op():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    callback_calls = []
    tc.register_partial_close_callback(lambda r: callback_calls.append(r))
    # No state registered for FOOUSDT — should warn and return without invoking callback
    tc.on_partial_close("FOOUSDT", closed_qty=1000.0, exec_price=1.1)
    assert callback_calls == [], f"callback fired despite missing state: {callback_calls}"
    print("  PASS: on_partial_close with no state is no-op (no callback)")


# ===========================================================================
# Test 4 — on_partial_close with invalid qty/price guards
# ===========================================================================

def test_on_partial_close_invalid_qty():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="GUARDUSDT", size=4000)
    tc._trades["GUARDUSDT"] = state
    calls = []
    tc.register_partial_close_callback(lambda r: calls.append(r))
    tc.on_partial_close("GUARDUSDT", closed_qty=0, exec_price=1.0)
    tc.on_partial_close("GUARDUSDT", closed_qty=-1, exec_price=1.0)
    assert calls == []
    print("  PASS: on_partial_close with qty<=0 is no-op")


def test_on_partial_close_invalid_price():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="GUARDUSDT", size=4000)
    tc._trades["GUARDUSDT"] = state
    calls = []
    tc.register_partial_close_callback(lambda r: calls.append(r))
    tc.on_partial_close("GUARDUSDT", closed_qty=1000, exec_price=0)
    state.entry_price = 0
    tc.on_partial_close("GUARDUSDT", closed_qty=1000, exec_price=1.0)
    assert calls == []
    print("  PASS: on_partial_close with non-positive entry/exit is no-op")


# ===========================================================================
# Test 5 — on_partial_close happy path: state decrement + record shape
# ===========================================================================

def test_on_partial_close_happy_path_buy():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="OKUSDT", side="Buy", size=4000.0, entry=1.0)
    tc._trades["OKUSDT"] = state
    records = []
    tc.register_partial_close_callback(lambda r: records.append(r))

    tc.on_partial_close(
        "OKUSDT", closed_qty=2000.0, exec_price=1.01,
        closed_by="mode4_partial",
    )

    assert len(records) == 1, f"expected 1 record, got {len(records)}"
    rec = records[0]
    assert rec["is_partial"] is True
    assert rec["partial_index"] == 1
    assert rec["size"] == 2000.0, f"size should be closed_qty (2000.0), got {rec['size']}"
    # Buy PnL: (1.01 - 1.0) / 1.0 * 100 = 1.0% pnl_pct
    assert abs(rec["pnl_pct"] - 1.0) < 0.001
    # pnl_usd = 1.0% of (2000 * 1.0) = $20
    assert abs(rec["pnl_usd"] - 20.0) < 0.01
    assert rec["was_win"] is True
    assert rec["direction"] == "Buy"
    assert rec["closed_by"] == "mode4_partial"
    # order_id has -partial-1 suffix for unique trade_id derivation
    assert rec["order_id"] == "oid-test-1-partial-1"
    # trade_id has the partial suffix
    assert "partial-1" in rec["trade_id"]
    # state mutations
    assert state.size == 2000.0, f"state.size should be 4000-2000=2000, got {state.size}"
    assert state.partial_index == 1
    # state still in _trades — NOT popped
    assert "OKUSDT" in tc._trades, "state must remain in _trades after partial"
    print("  PASS: on_partial_close Buy happy path; record correct; state.size=2000; index=1")


def test_on_partial_close_happy_path_sell():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="SHORTUSDT", side="Sell", size=4000.0, entry=2.0)
    tc._trades["SHORTUSDT"] = state
    records = []
    tc.register_partial_close_callback(lambda r: records.append(r))

    # Sell trade closed at 1.95 (price dropped 2.5% from entry → win for Sell)
    tc.on_partial_close(
        "SHORTUSDT", closed_qty=2000.0, exec_price=1.95,
        closed_by="mode4_partial",
    )

    rec = records[0]
    # Sell PnL: -((1.95 - 2.0) / 2.0 * 100) = 2.5% (positive — price dropped)
    assert abs(rec["pnl_pct"] - 2.5) < 0.001, f"expected +2.5%, got {rec['pnl_pct']}"
    # pnl_usd = 2.5% of (2000 * 2.0) = $100
    assert abs(rec["pnl_usd"] - 100.0) < 0.01
    assert rec["was_win"] is True
    assert rec["direction"] == "Sell"
    print("  PASS: on_partial_close Sell happy path; PnL sign correct (+2.5% on price drop)")


# ===========================================================================
# Test 6 — Multiple partials per trade: partial_index increments,
# unique trade_ids per row, state.size decrements cumulatively
# ===========================================================================

def test_multiple_partials_increment_and_unique_ids():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="MULTIUSDT", side="Buy", size=4000.0, entry=1.0)
    tc._trades["MULTIUSDT"] = state
    records = []
    tc.register_partial_close_callback(lambda r: records.append(r))

    # 3 partials: 25%, 25%, 25%; total closed = 3000 of 4000; residual = 1000
    tc.on_partial_close("MULTIUSDT", closed_qty=1000.0, exec_price=1.01)
    tc.on_partial_close("MULTIUSDT", closed_qty=1000.0, exec_price=1.02)
    tc.on_partial_close("MULTIUSDT", closed_qty=1000.0, exec_price=1.03)

    assert len(records) == 3
    indices = [r["partial_index"] for r in records]
    assert indices == [1, 2, 3], f"expected [1,2,3], got {indices}"
    oids = [r["order_id"] for r in records]
    assert oids == [
        "oid-test-1-partial-1",
        "oid-test-1-partial-2",
        "oid-test-1-partial-3",
    ]
    # All trade_ids unique
    tids = [r["trade_id"] for r in records]
    assert len(set(tids)) == 3
    # State size decremented cumulatively
    assert state.size == 1000.0, f"residual should be 1000, got {state.size}"
    assert state.partial_index == 3
    # State NOT popped
    assert "MULTIUSDT" in tc._trades
    print("  PASS: 3 partials => partial_index [1,2,3], unique order_ids, residual=1000, state retained")


# ===========================================================================
# Test 7 — Partial callbacks do NOT fire full callbacks (isolation)
# ===========================================================================

def test_partial_callbacks_isolated_from_full_callbacks():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="ISOUSDT", side="Buy", size=2000.0, entry=1.0)
    tc._trades["ISOUSDT"] = state

    full_calls = []
    partial_calls = []
    tc.register_close_callback(lambda r: full_calls.append(r))
    tc.register_partial_close_callback(lambda r: partial_calls.append(r))

    tc.on_partial_close("ISOUSDT", closed_qty=1000.0, exec_price=1.01)

    assert len(partial_calls) == 1
    assert len(full_calls) == 0, "full close callback must NOT fire on partial"
    print("  PASS: partial close fires only partial callbacks, full callbacks remain silent")


# ===========================================================================
# Test 8 — After all partials, final close fires full callbacks against
# the residual (state retained correctly)
# ===========================================================================

def test_residual_final_close_uses_decremented_size():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="RESIDUSDT", side="Buy", size=4000.0, entry=1.0)
    tc._trades["RESIDUSDT"] = state

    partial_records = []
    full_records = []
    tc.register_partial_close_callback(lambda r: partial_records.append(r))
    tc.register_close_callback(lambda r: full_records.append(r))

    # One partial of 2000 → residual = 2000
    tc.on_partial_close("RESIDUSDT", closed_qty=2000.0, exec_price=1.01)
    assert state.size == 2000.0

    # Final close at 1.02 — full close fan-out fires against residual qty
    tc.on_trade_closed(
        "RESIDUSDT",
        pnl_pct=0.0,  # back-derive triggered
        pnl_usd=0.0,
        was_win=False,
        closed_by="bybit_sl_hit",
        exit_price=1.02,
    )

    assert len(partial_records) == 1
    assert len(full_records) == 1
    final = full_records[0]
    # Final record's size is the RESIDUAL (2000), not the original 4000
    assert final["size"] == 2000.0, f"residual final size should be 2000, got {final['size']}"
    # State popped on final close
    assert "RESIDUSDT" not in tc._trades
    print("  PASS: final close after partial uses residual size 2000 and pops state")


# ===========================================================================
# Test 9 — Callback failure isolation: one failing partial callback does
# not prevent the next from firing
# ===========================================================================

def test_partial_callback_failure_isolated():
    from src.core.trade_coordinator import TradeCoordinator

    tc = TradeCoordinator()
    state = _make_state(symbol="FAILUSDT", size=4000.0)
    tc._trades["FAILUSDT"] = state

    fired = []

    def bad_cb(r):
        raise RuntimeError("boom")

    def good_cb(r):
        fired.append(r)

    tc.register_partial_close_callback(bad_cb)
    tc.register_partial_close_callback(good_cb)

    tc.on_partial_close("FAILUSDT", closed_qty=1000.0, exec_price=1.01)
    assert len(fired) == 1, "good callback must run even when sibling raised"
    print("  PASS: failing partial callback does not block subsequent callbacks")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    print("\n=== Issue 4 Phase 3 — partial-close propagation ===\n")
    tests = [
        ("TradeState.partial_index default", test_trade_state_partial_index_default_zero),
        ("mark/pop pending roundtrip", test_mark_pop_pending_roundtrip),
        ("pop without mark", test_pop_without_mark_returns_none),
        ("on_partial_close no state", test_on_partial_close_without_state_no_op),
        ("invalid qty guard", test_on_partial_close_invalid_qty),
        ("invalid price guard", test_on_partial_close_invalid_price),
        ("happy path Buy", test_on_partial_close_happy_path_buy),
        ("happy path Sell", test_on_partial_close_happy_path_sell),
        ("multiple partials", test_multiple_partials_increment_and_unique_ids),
        ("partial vs full isolation", test_partial_callbacks_isolated_from_full_callbacks),
        ("residual final close", test_residual_final_close_uses_decremented_size),
        ("partial callback failure isolation", test_partial_callback_failure_isolated),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\nResult: {len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
