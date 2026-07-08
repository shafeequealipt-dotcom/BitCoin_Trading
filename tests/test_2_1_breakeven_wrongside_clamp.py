"""Issue 2.1 (2026-06-07) — breakeven-floor wrong-side clamp + terminal guard.

Pure-logic mirror of the SL-gateway R2 breakeven-floor clamp (sl_gateway.py
lines ~562-575) and the terminal correct-side guard, following the established
pure-logic convention of test_t2_5_sl_gateway_breakeven.py (no full gateway /
market_service wiring).

Root cause being pinned: on a round-tripped armed long, price can fall BELOW the
breakeven floor, so the old ``max(breakeven_floor, boundary)`` held the floor
ABOVE current price (wrong-side) — which the exchange rejected and the urgent
breakeven lane (rate-limit bypassed) re-spammed every tick (~150x/16min, BLUR).
The fix: never hold the floor on the wrong side — clamp it to the min-distance
boundary just below price (operator's chosen behaviour) — and a terminal guard
rejects any still-wrong-side stop without wiring it (so nothing is retried).
"""
from __future__ import annotations


def _r2_held_floor(is_long: bool, breakeven_floor: float,
                   r2_boundary: float, current_price: float) -> float:
    """Mirror of the R2 breakeven-floor clamp WITH the Issue 2.1 fix."""
    if is_long:
        bounded = max(breakeven_floor, r2_boundary)
        if bounded >= current_price:        # 2.1: never hold wrong-side
            bounded = r2_boundary
    else:
        bounded = min(breakeven_floor, r2_boundary)
        if bounded <= current_price:        # 2.1: never hold wrong-side
            bounded = r2_boundary
    return bounded


def _wrong_side(is_long: bool, new_sl: float, current_price: float) -> bool:
    """Mirror of the terminal correct-side guard."""
    return (is_long and new_sl >= current_price) or (
        (not is_long) and new_sl <= current_price
    )


def test_roundtrip_long_floor_clamped_below_price():
    """Long round-trip: floor (101) above price (100) -> clamp to boundary 99.5."""
    price = 100.0
    res = _r2_held_floor(True, breakeven_floor=101.0, r2_boundary=99.5,
                         current_price=price)
    assert res == 99.5
    assert res < price                       # correct side
    assert not _wrong_side(True, res, price)  # never wrong-side


def test_highvol_mode1_long_floor_held_unchanged():
    """High-vol Problem-1.1 case (floor 99.8 below price 100, above boundary
    99.5) is UNAFFECTED — the floor is still held, not over-clamped."""
    res = _r2_held_floor(True, breakeven_floor=99.8, r2_boundary=99.5,
                         current_price=100.0)
    assert res == 99.8                       # held at breakeven, not clamped down


def test_roundtrip_short_floor_clamped_above_price():
    """Short round-trip: floor (99) at/below price (100) -> clamp to boundary 100.5."""
    price = 100.0
    res = _r2_held_floor(False, breakeven_floor=99.0, r2_boundary=100.5,
                         current_price=price)
    assert res == 100.5
    assert res > price                        # correct side
    assert not _wrong_side(False, res, price)


def test_terminal_guard_flags_only_wrong_side():
    assert _wrong_side(True, 101.0, 100.0) is True    # long stop above price
    assert _wrong_side(True, 99.0, 100.0) is False    # long stop below price (ok)
    assert _wrong_side(False, 99.0, 100.0) is True    # short stop below price
    assert _wrong_side(False, 101.0, 100.0) is False  # short stop above price (ok)
