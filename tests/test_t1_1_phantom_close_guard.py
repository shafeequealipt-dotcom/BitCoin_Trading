"""T1-1 / F18 phantom-close guard smoke tests (six-tier-fixes 2026-05-11).

Covers the three-layer defense and the UrgentQueue root-cause fix:

1. ``UrgentQueue.clear_for_symbol`` drops queued concerns and resets
   the per-symbol dedup cooldown. Idempotent.
2. ``should_allow_strategic_action`` rejects close on a symbol not in
   ``active_symbols`` even when source is ``call_b`` (trusted bypass
   does NOT cover close-on-closed-symbol).
3. ``should_allow_strategic_action`` still allows non-close actions
   (e.g. ``tighten_stop``) from trusted sources regardless of
   ``active_symbols``.
4. ``TradeCoordinator.queue_strategic_action`` rejects close on a
   symbol not in ``_trades`` independent of the firewall.

Pure functions / dataclasses. No IO, no mocks.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═════════════════════════════════════════════════════════════════════════
# 1. UrgentQueue.clear_for_symbol
# ═════════════════════════════════════════════════════════════════════════


def test_urgent_queue_clear_for_symbol_drops_concerns_and_resets_cooldown():
    """Clearing a symbol removes its concern AND lets a fresh add succeed."""
    from src.core.urgent_queue import UrgentQueue, WatchdogConcern

    uq = UrgentQueue()
    concern = WatchdogConcern(
        symbol="TESTUSDT",
        pnl_pct=-2.5,
        warnings=["sl_consumed_80pct"],
        current_price=1.0,
        entry_price=1.025,
        side="Buy",
        sl_proximity_pct=80.0,
        position_age_minutes=5.0,
        stop_loss=0.98,
        urgency="HIGH",
    )
    added = uq.add_concern(concern)
    assert added is True
    assert uq.has_concerns is True

    cleared = uq.clear_for_symbol("TESTUSDT")
    assert cleared == 1
    assert uq.has_concerns is False

    # The 150 s per-symbol cooldown is also reset, so a fresh concern
    # for the same symbol can be added immediately (would otherwise be
    # suppressed for the rest of the 150 s window).
    added_again = uq.add_concern(concern)
    assert added_again is True


def test_urgent_queue_clear_for_symbol_is_idempotent():
    """Clearing a symbol that has no queued concern returns 0 and logs nothing."""
    from src.core.urgent_queue import UrgentQueue

    uq = UrgentQueue()
    cleared = uq.clear_for_symbol("NEVER_QUEUED_USDT")
    assert cleared == 0
    assert uq.has_concerns is False


# ═════════════════════════════════════════════════════════════════════════
# 2 + 3. Firewall phantom-close precondition
# ═════════════════════════════════════════════════════════════════════════


def test_firewall_rejects_close_on_inactive_symbol_for_trusted_source():
    """call_b bypass does NOT cover close on a symbol not in active set."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, explanation = should_allow_strategic_action(
        "close",
        "TESTUSDT",
        "stale watchdog flag",
        source="call_b",
        active_symbols=frozenset(),  # nothing active
    )
    assert allowed is False
    assert "PHANTOM_CLOSE_REJECTED" in explanation


def test_firewall_rejects_take_profit_on_inactive_symbol_for_call_a_urgent():
    """call_a_urgent bypass does NOT cover take_profit on inactive symbol."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, explanation = should_allow_strategic_action(
        "take_profit",
        "TESTUSDT",
        "stale watchdog flag",
        source="call_a_urgent",
        active_symbols=frozenset({"OTHERUSDT"}),
    )
    assert allowed is False
    assert "PHANTOM_CLOSE_REJECTED" in explanation


def test_firewall_allows_close_on_active_symbol_for_trusted_source():
    """Legitimate close on an active symbol via trusted source still flows."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, _ = should_allow_strategic_action(
        "close",
        "TESTUSDT",
        "thesis invalid",
        source="call_b",
        active_symbols=frozenset({"TESTUSDT", "OTHERUSDT"}),
    )
    assert allowed is True


def test_firewall_allows_tighten_stop_on_inactive_symbol_for_trusted_source():
    """Non-close actions still flow for trusted sources regardless of active set.

    Defense scope is limited to close / take_profit on inactive symbols;
    tighten_stop or set_exit on an unknown symbol is the watchdog's
    problem, not the firewall's.
    """
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, _ = should_allow_strategic_action(
        "tighten_stop",
        "TESTUSDT",
        "preemptive tighten",
        source="call_b",
        active_symbols=frozenset(),
    )
    assert allowed is True


def test_firewall_legacy_callers_without_active_symbols_keep_prior_behavior():
    """When active_symbols is None (legacy caller), precondition is skipped."""
    from src.sentinel.firewall import should_allow_strategic_action

    # Trusted source + no active_symbols hint => allowed (legacy bypass).
    allowed, _ = should_allow_strategic_action(
        "close", "TESTUSDT", "r", source="call_b", active_symbols=None,
    )
    assert allowed is True


# ═════════════════════════════════════════════════════════════════════════
# 4. Coordinator queue_strategic_action precondition
# ═════════════════════════════════════════════════════════════════════════


def test_coordinator_queue_rejects_close_on_inactive_symbol():
    """queue_strategic_action drops close for symbol not in _trades."""
    from src.core.trade_coordinator import TradeCoordinator

    c = TradeCoordinator()
    # Pre-condition: _trades empty.
    assert c.active_symbols() == frozenset()

    c.queue_strategic_action(
        symbol="TESTUSDT",
        action="close",
        reason="stale",
    )
    pending = c.drain_strategic_actions()
    assert pending == []  # rejected, nothing queued


def test_coordinator_queue_allows_close_on_active_symbol():
    """queue_strategic_action passes close for a symbol with an active trade."""
    from src.core.trade_coordinator import TradeCoordinator

    c = TradeCoordinator()
    c.register_trade(
        symbol="TESTUSDT",
        strategy_category="default",
        strategy_name="test",
        entry_price=1.0,
        side="Buy",
    )
    assert "TESTUSDT" in c.active_symbols()

    c.queue_strategic_action(
        symbol="TESTUSDT",
        action="close",
        reason="thesis invalid",
    )
    pending = c.drain_strategic_actions()
    assert len(pending) == 1
    assert pending[0]["symbol"] == "TESTUSDT"
    assert pending[0]["action"] == "close"


def test_coordinator_queue_allows_non_close_actions_on_inactive_symbol():
    """tighten_stop is not gated by the phantom-close precondition."""
    from src.core.trade_coordinator import TradeCoordinator

    c = TradeCoordinator()
    c.queue_strategic_action(
        symbol="TESTUSDT",
        action="tighten_stop",
        reason="preemptive",
        new_sl=0.95,
    )
    pending = c.drain_strategic_actions()
    assert len(pending) == 1
    assert pending[0]["action"] == "tighten_stop"


def test_active_symbols_returns_frozenset_snapshot():
    """active_symbols() returns a frozenset safe to pass to firewall."""
    from src.core.trade_coordinator import TradeCoordinator

    c = TradeCoordinator()
    snap1 = c.active_symbols()
    assert isinstance(snap1, frozenset)
    assert snap1 == frozenset()

    c.register_trade(symbol="ABCUSDT", strategy_category="default")
    snap2 = c.active_symbols()
    assert snap2 == frozenset({"ABCUSDT"})

    # Snapshot is independent of subsequent state mutations.
    c.register_trade(symbol="XYZUSDT", strategy_category="default")
    assert snap2 == frozenset({"ABCUSDT"})
    assert c.active_symbols() == frozenset({"ABCUSDT", "XYZUSDT"})
