"""Surgical tests for the Issue 3 (2026-05-18) 5-min per-(symbol,
direction) reentry cooldown.

Maps the four operator trial scenarios from
``IMPLEMENT_THREE_ISSUES_FIX.md`` Issue 3 §D Step 3.5 onto deterministic
unit tests that exercise the new TradeCoordinator API
(``is_reentry_blocked`` / ``clear_expired_reentry_cooldowns`` /
``get_active_reentry_cooldowns``) without depending on wall-clock
timing — the underlying state uses ``time.monotonic`` so we manipulate
the dict directly to age entries past expiry.

Replaces ``test_j6_reentry_learning_gate.py``,
``test_h4_reentry_gate_calibration.py``, and ``test_t2_1_loss_cooldown.py``
(all removed in issue3/p3-6) along with the production surface they
covered (removed in issue3/p3-3).
"""

from __future__ import annotations

import time

import pytest

from src.core.trade_coordinator import TradeCoordinator


@pytest.fixture
def coord() -> TradeCoordinator:
    c = TradeCoordinator()
    # Pin a deterministic 300s cooldown — matches the operator-stated
    # intent and the default from APEXSettings.reentry_cooldown_seconds.
    c.set_reentry_cooldown_seconds(300)
    return c


def _close_trade(c: TradeCoordinator, symbol: str, side: str) -> None:
    """Drive the canonical close hook so on_trade_closed populates the
    new cooldown dict end-to-end. Other coordinator state (PnL, exit
    price) is irrelevant for the cooldown — we send placeholders."""
    c.register_trade(
        symbol=symbol, strategy_category="default", side=side,
        entry_price=1.0,
    )
    c.on_trade_closed(
        symbol=symbol, pnl_pct=-0.5, pnl_usd=-5.0,
        was_win=False, closed_by="bybit_sl_hit",
    )


def test_scenario1_block_within_window(coord: TradeCoordinator) -> None:
    """Close AVAXUSDT Sell at T0; immediate re-entry at T+small must be
    blocked with remaining_seconds close to the full window."""
    _close_trade(coord, "AVAXUSDT", "Sell")

    blocked, remaining = coord.is_reentry_blocked("AVAXUSDT", "Sell")
    assert blocked is True
    assert 290 <= remaining <= 300, (
        f"Expected ~300s window immediately after close, got {remaining}s"
    )


def test_scenario2_allow_after_window(coord: TradeCoordinator) -> None:
    """Close AVAXUSDT Sell at T0; re-entry attempt after the 300s window
    must be allowed and the lazy cleanup must drop the entry."""
    _close_trade(coord, "AVAXUSDT", "Sell")

    # Manipulate the monotonic expiry one second into the past — the
    # lazy cleanup path inside is_reentry_blocked must fire.
    coord._reentry_cooldown[("AVAXUSDT", "Sell")] = time.monotonic() - 1

    blocked, remaining = coord.is_reentry_blocked("AVAXUSDT", "Sell")
    assert blocked is False
    assert remaining == 0
    assert ("AVAXUSDT", "Sell") not in coord._reentry_cooldown, (
        "Expired entry must be popped on read (lazy cleanup)"
    )


def test_scenario3_opposite_direction_allowed(
    coord: TradeCoordinator,
) -> None:
    """Closing AVAXUSDT Sell must NOT block AVAXUSDT Buy. The cooldown
    is per-(symbol, direction); opposite-direction entry is eligible."""
    _close_trade(coord, "AVAXUSDT", "Sell")

    blocked_buy, remaining_buy = coord.is_reentry_blocked("AVAXUSDT", "Buy")
    assert blocked_buy is False
    assert remaining_buy == 0

    # Same-direction is still blocked (sanity).
    blocked_sell, _ = coord.is_reentry_blocked("AVAXUSDT", "Sell")
    assert blocked_sell is True


def test_scenario4_rearm_on_reclose(coord: TradeCoordinator) -> None:
    """Close AVAXUSDT Sell at T0, then close AVAXUSDT Buy after re-entry
    at T1: both (sym, dir) keys live independently in the cooldown dict,
    each with its own 300s clock."""
    _close_trade(coord, "AVAXUSDT", "Sell")
    # Open a Buy at T+60 and close it immediately.
    _close_trade(coord, "AVAXUSDT", "Buy")

    blocked_sell, remaining_sell = coord.is_reentry_blocked(
        "AVAXUSDT", "Sell",
    )
    blocked_buy, remaining_buy = coord.is_reentry_blocked(
        "AVAXUSDT", "Buy",
    )
    assert blocked_sell is True
    assert blocked_buy is True
    # Both windows are independent — Buy was re-armed by the second close.
    assert 290 <= remaining_sell <= 300
    assert 290 <= remaining_buy <= 300


def test_long_short_aliases_normalize_to_buy_sell(
    coord: TradeCoordinator,
) -> None:
    """Legacy callers pass ``"long"`` / ``"short"``; the coordinator
    canonicalises them to ``"Buy"`` / ``"Sell"`` so the cooldown is
    consistent regardless of input casing."""
    _close_trade(coord, "BTCUSDT", "Sell")

    blocked_short, _ = coord.is_reentry_blocked("BTCUSDT", "short")
    blocked_long, _ = coord.is_reentry_blocked("BTCUSDT", "long")
    assert blocked_short is True
    assert blocked_long is False


def test_empty_or_unknown_direction_falls_through(
    coord: TradeCoordinator,
) -> None:
    """An empty / malformed direction must not key into the cooldown.
    Treat it as "not blocked" so a misformed payload cannot accidentally
    block all future trades on the symbol."""
    _close_trade(coord, "ETHUSDT", "Sell")

    blocked, remaining = coord.is_reentry_blocked("ETHUSDT", "")
    assert blocked is False
    assert remaining == 0

    blocked, _ = coord.is_reentry_blocked("ETHUSDT", "totally-not-a-direction")
    assert blocked is False


def test_clear_expired_periodic_sweep(coord: TradeCoordinator) -> None:
    """Periodic sweep drops expired keys and leaves fresh ones."""
    _close_trade(coord, "LINKUSDT", "Sell")
    _close_trade(coord, "ADAUSDT", "Buy")
    # Age the LINK entry into the past; leave ADA fresh.
    coord._reentry_cooldown[("LINKUSDT", "Sell")] = time.monotonic() - 1

    cleared = coord.clear_expired_reentry_cooldowns()
    assert cleared == 1
    assert ("LINKUSDT", "Sell") not in coord._reentry_cooldown
    assert ("ADAUSDT", "Buy") in coord._reentry_cooldown


def test_get_active_snapshot_returns_per_direction_tuples(
    coord: TradeCoordinator,
) -> None:
    """Snapshot returns ``(symbol, direction, remaining_seconds)`` for
    every active entry — the surface the brain prompt consumes."""
    _close_trade(coord, "SOLUSDT", "Buy")
    _close_trade(coord, "SOLUSDT", "Sell")

    snap = coord.get_active_reentry_cooldowns()
    assert len(snap) == 2
    pairs = {(sym, direction) for sym, direction, _ in snap}
    assert pairs == {("SOLUSDT", "Buy"), ("SOLUSDT", "Sell")}
    for _, _, remaining in snap:
        assert 290 <= remaining <= 300


def test_set_reentry_cooldown_seconds_clamps_non_positive(
    coord: TradeCoordinator,
) -> None:
    """Non-positive overrides are ignored; the default (300s) is kept
    so a misconfigured config.toml cannot disable the cooldown."""
    coord.set_reentry_cooldown_seconds(120)
    assert coord._reentry_cooldown_seconds == 120

    coord.set_reentry_cooldown_seconds(0)
    assert coord._reentry_cooldown_seconds == 120

    coord.set_reentry_cooldown_seconds(-30)
    assert coord._reentry_cooldown_seconds == 120

    coord.set_reentry_cooldown_seconds(450)
    assert coord._reentry_cooldown_seconds == 450
