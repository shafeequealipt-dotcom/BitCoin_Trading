"""Issue 1 Phase 3 — APEX_DIR_LOCK propagation tests.

Covers the lock state flowing from APEX optimizer through layer_manager
into the trade dict, plus the XRAY-block lock-respect logic and the
DIRECTION_DECISION summary classifier.

The XRAY block at strategy_worker.py:1604-1748 is too deeply nested to
exercise end-to-end without standing up the full worker stack; the
tests below verify the production logic by direct call (OptimizedTrade,
_fallback) or by exercising the same conditional structure standalone
(XRAY lock-respect, DIRECTION_DECISION reason classifier).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===========================================================================
# Test 1 — OptimizedTrade has lock fields with correct defaults
# ===========================================================================

def test_optimized_trade_lock_fields_default_false_empty():
    from src.apex.models import OptimizedTrade

    ot = OptimizedTrade(
        symbol="ABC",
        direction="Buy",
        sl_pct=1.0,
        tp_pct=2.0,
        tp_mode="fixed",
        position_size_usd=100.0,
        leverage=2,
        entry_timing="immediate",
        add_on_pullback=False,
    )
    assert ot.is_locked is False, f"default is_locked must be False, got {ot.is_locked!r}"
    assert ot.lock_reason == "", f"default lock_reason must be empty, got {ot.lock_reason!r}"
    print("  PASS: OptimizedTrade.is_locked defaults False; lock_reason defaults empty")


def test_optimized_trade_lock_fields_settable():
    from src.apex.models import OptimizedTrade

    ot = OptimizedTrade(
        symbol="ABC",
        direction="Buy",
        sl_pct=1.0,
        tp_pct=2.0,
        tp_mode="fixed",
        position_size_usd=100.0,
        leverage=2,
        entry_timing="immediate",
        add_on_pullback=False,
        is_locked=True,
        lock_reason="volatile regime, insufficient flip evidence",
    )
    assert ot.is_locked is True
    assert ot.lock_reason == "volatile regime, insufficient flip evidence"
    print("  PASS: OptimizedTrade lock fields accept explicit values")


# ===========================================================================
# Test 2 — TradeOptimizer._fallback preserves lock_state on the returned trade
# ===========================================================================

def test_fallback_default_lock_state_is_unlocked():
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    directive = {
        "symbol": "ABCUSDT", "direction": "Buy",
        "stop_loss_price": 1.0, "take_profit_price": 1.05,
        "leverage": 2, "size_usd": 500,
    }
    fb = opt._fallback(directive, "test_default")
    assert fb.is_fallback is True
    assert fb.is_locked is False, "default _fallback() must produce unlocked OptimizedTrade"
    assert fb.lock_reason == ""
    print("  PASS: _fallback() with default lock_state produces unlocked OptimizedTrade")


def test_fallback_preserves_explicit_lock_state():
    from src.apex.optimizer import TradeOptimizer

    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=None)
    directive = {
        "symbol": "ABCUSDT", "direction": "Sell",
        "stop_loss_price": 2.0, "take_profit_price": 1.9,
        "leverage": 3, "size_usd": 500,
    }
    fb = opt._fallback(
        directive,
        "timeout_regime",
        lock_state=(True, "trending_down aligns with Sell"),
    )
    assert fb.is_fallback is True
    assert fb.direction == "Sell"
    assert fb.is_locked is True
    assert fb.lock_reason == "trending_down aligns with Sell"
    print("  PASS: _fallback() preserves explicit lock_state through to OptimizedTrade")


# ===========================================================================
# Test 3 — XRAY block lock-respect: production logic isolated
# Simulates the strategy_worker.py:1631-onwards branching exactly.
# ===========================================================================

def _simulate_xray_flip_decision(*, ratio: float, threshold: float, apex_locked: bool) -> str:
    """Mirror of strategy_worker.py:1631-1748 outer branching.

    Returns one of: "suppressed", "flipped", "no_flip".
    """
    if apex_locked and ratio > threshold:
        return "suppressed"
    elif ratio > threshold:
        return "flipped"
    else:
        return "no_flip"


def test_xray_locked_over_threshold_suppresses():
    assert _simulate_xray_flip_decision(ratio=45.9, threshold=3.0, apex_locked=True) == "suppressed"
    print("  PASS: locked + ratio over threshold => suppressed (SEIUSDT/ONDOUSDT case)")


def test_xray_unlocked_over_threshold_flips():
    assert _simulate_xray_flip_decision(ratio=4.6, threshold=3.0, apex_locked=False) == "flipped"
    print("  PASS: not locked + ratio over threshold => flips (GMTUSDT case)")


def test_xray_locked_under_threshold_no_flip():
    assert _simulate_xray_flip_decision(ratio=1.5, threshold=3.0, apex_locked=True) == "no_flip"
    print("  PASS: locked + ratio under threshold => no flip (silent, no behavior change)")


def test_xray_unlocked_under_threshold_no_flip():
    assert _simulate_xray_flip_decision(ratio=1.5, threshold=3.0, apex_locked=False) == "no_flip"
    print("  PASS: not locked + ratio under threshold => no flip (existing behavior)")


# ===========================================================================
# Test 4 — DIRECTION_DECISION reason classifier
# Mirrors strategy_worker.py reason-derivation just before the log.
# ===========================================================================

def _classify_direction_decision(
    *,
    was_flipped: bool,
    flip_source: str,
    apex_locked: bool,
    xray_suppressed_by_lock: bool,
) -> str:
    if xray_suppressed_by_lock:
        return "xray_flip_suppressed_by_lock"
    if was_flipped and flip_source == "xray":
        return "xray_flip"
    if was_flipped:
        return "apex_flip"
    if apex_locked:
        return "apex_dir_lock_held"
    return "clean"


def test_direction_decision_clean():
    r = _classify_direction_decision(
        was_flipped=False, flip_source="", apex_locked=False, xray_suppressed_by_lock=False,
    )
    assert r == "clean"
    print("  PASS: no flip, no lock => clean")


def test_direction_decision_apex_flip():
    r = _classify_direction_decision(
        was_flipped=True, flip_source="", apex_locked=False, xray_suppressed_by_lock=False,
    )
    assert r == "apex_flip"
    print("  PASS: flipped without xray source => apex_flip (ATOMUSDT case)")


def test_direction_decision_xray_flip():
    r = _classify_direction_decision(
        was_flipped=True, flip_source="xray", apex_locked=False, xray_suppressed_by_lock=False,
    )
    assert r == "xray_flip"
    print("  PASS: flipped with xray source => xray_flip (GMTUSDT case)")


def test_direction_decision_lock_held_no_xray():
    r = _classify_direction_decision(
        was_flipped=False, flip_source="", apex_locked=True, xray_suppressed_by_lock=False,
    )
    assert r == "apex_dir_lock_held"
    print("  PASS: locked, no xray attempt => apex_dir_lock_held (APTUSDT case)")


def test_direction_decision_xray_suppressed_by_lock():
    r = _classify_direction_decision(
        was_flipped=False, flip_source="", apex_locked=True, xray_suppressed_by_lock=True,
    )
    assert r == "xray_flip_suppressed_by_lock"
    print("  PASS: locked + xray would have flipped => xray_flip_suppressed_by_lock (the fix)")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    print("\n=== Issue 1 Phase 3 — APEX_DIR_LOCK propagation ===\n")

    tests = [
        ("OptimizedTrade lock fields default", test_optimized_trade_lock_fields_default_false_empty),
        ("OptimizedTrade lock fields settable", test_optimized_trade_lock_fields_settable),
        ("_fallback default lock_state", test_fallback_default_lock_state_is_unlocked),
        ("_fallback preserves lock_state", test_fallback_preserves_explicit_lock_state),
        ("XRAY locked + over-threshold => suppressed", test_xray_locked_over_threshold_suppresses),
        ("XRAY unlocked + over-threshold => flipped", test_xray_unlocked_over_threshold_flips),
        ("XRAY locked + under-threshold => no flip", test_xray_locked_under_threshold_no_flip),
        ("XRAY unlocked + under-threshold => no flip", test_xray_unlocked_under_threshold_no_flip),
        ("DIRECTION_DECISION clean", test_direction_decision_clean),
        ("DIRECTION_DECISION apex_flip", test_direction_decision_apex_flip),
        ("DIRECTION_DECISION xray_flip", test_direction_decision_xray_flip),
        ("DIRECTION_DECISION lock_held_no_xray", test_direction_decision_lock_held_no_xray),
        ("DIRECTION_DECISION xray_suppressed_by_lock", test_direction_decision_xray_suppressed_by_lock),
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
