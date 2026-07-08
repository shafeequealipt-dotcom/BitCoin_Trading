"""T2-7 CALL_B stale snapshot fix tests (2026-05-12).

Pre-fix bug (F62):
  CALL_B takes a position snapshot at prompt-build time. The slow
  Claude call (60-240 s per T2-1) means positions can close during
  processing. The existing T1-1/F18 guard at layer_manager.
  _execute_position_actions:1150 only protected `close`/`take_profit`
  actions. Other actions (tighten_stop, set_exit, scale-out) could
  still queue and produce no-op work or spurious watchdog logs.

Fix: extend the active_symbols check to ALL non-hold actions and
emit CALL_B_STALE_SNAPSHOT_DETECTED so the rate of detection is
visible in production logs.

Tests are pure-logic — they exercise the filter conditions without
requiring the full LayerManager wiring.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _filter_logic(action: str, symbol: str, active_symbols: set[str]) -> bool:
    """Mirror of the inline T2-7 check in
    layer_manager._execute_position_actions.

    Returns True if the action should be SKIPPED (stale-snapshot
    rejected); False if it should proceed to firewall + queue.
    """
    if action == "hold":
        return False  # holds are no-ops; not affected by the check
    return symbol not in active_symbols


def test_t2_7_close_on_closed_position_rejected():
    """T2-7: close action on a position no longer in active_symbols
    is rejected (layer-1 of the three-layer phantom-close defense)."""
    assert _filter_logic("close", "BTCUSDT", set()) is True
    assert _filter_logic("close", "BTCUSDT", {"ETHUSDT"}) is True


def test_t2_7_take_profit_on_closed_position_rejected():
    """T2-7: take_profit on a closed symbol — same path as close."""
    assert _filter_logic("take_profit", "BTCUSDT", set()) is True


def test_t2_7_tighten_stop_on_closed_position_rejected():
    """T2-7 NEW BEHAVIOUR: tighten_stop on a closed symbol is now
    rejected (pre-fix this would queue a no-op action and waste
    a watchdog tick)."""
    assert _filter_logic("tighten_stop", "BTCUSDT", set()) is True
    assert _filter_logic("tighten_stop", "BTCUSDT", {"ETHUSDT"}) is True


def test_t2_7_set_exit_on_closed_position_rejected():
    """T2-7 NEW BEHAVIOUR: set_exit on a closed symbol is rejected."""
    assert _filter_logic("set_exit", "BTCUSDT", set()) is True


def test_t2_7_arbitrary_non_hold_action_on_closed_position_rejected():
    """T2-7 NEW BEHAVIOUR: any non-hold action on a closed symbol is
    rejected. Future action types added to the StrategicPlan API are
    automatically protected by this check (no maintenance burden)."""
    for act in ("partial_close", "scale_out", "move_to_breakeven", "custom_x"):
        assert _filter_logic(act, "BTCUSDT", set()) is True, (
            f"action {act} should be rejected when symbol is closed"
        )


def test_t2_7_close_on_open_position_proceeds():
    """T2-7: close on a still-open symbol proceeds normally (no
    spurious rejection)."""
    assert _filter_logic("close", "BTCUSDT", {"BTCUSDT"}) is False


def test_t2_7_tighten_stop_on_open_position_proceeds():
    """T2-7: tighten_stop on a still-open symbol proceeds normally."""
    assert _filter_logic("tighten_stop", "BTCUSDT", {"BTCUSDT"}) is False


def test_t2_7_hold_action_unaffected_by_active_symbols():
    """T2-7: hold actions are no-ops by design — the filter must skip
    them BEFORE the active_symbols check (matches the inline `if
    action.action == "hold": continue` short-circuit)."""
    assert _filter_logic("hold", "BTCUSDT", set()) is False
    assert _filter_logic("hold", "BTCUSDT", {"BTCUSDT"}) is False


def test_t2_7_multiple_symbols_filtered_independently():
    """T2-7: in a CALL_B plan with multiple symbols, each is filtered
    against the same fresh active_symbols snapshot — closed symbols
    are dropped, open ones proceed."""
    active = {"ETHUSDT", "SOLUSDT"}
    plan_actions = [
        ("BTCUSDT", "close"),         # closed → reject
        ("ETHUSDT", "tighten_stop"),  # open → proceed
        ("SOLUSDT", "close"),         # open → proceed
        ("DOGEUSDT", "set_exit"),     # closed → reject
        ("XRPUSDT", "hold"),          # hold → unaffected
    ]
    rejections = [
        sym for sym, act in plan_actions
        if _filter_logic(act, sym, active)
    ]
    assert rejections == ["BTCUSDT", "DOGEUSDT"], (
        f"Expected 2 rejections (BTC, DOGE); got {rejections}"
    )
