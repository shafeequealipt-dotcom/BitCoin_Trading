"""Issue 2.2 (2026-06-07) — conform order qty to the symbol's exchange constraints.

Pure-logic mirror of the conform block added to
strategy_worker._execute_claude_trade. The observed live failure was EGLD/ALGO
orders EXCEEDING maxOrderQty (Bybit retCode 10001 "too large"); the conform
clamps DOWN to max_qty (rounded to step), bumps up to min_notional/min_qty where
possible, and skips cleanly only when the order genuinely cannot conform.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_UP


def _conform(qty, *, step, max_qty, min_qty, min_notional, price):
    """Mirror of the strategy_worker conform; returns (qty, skip_reason|None)."""
    dstep = Decimal(str(step))

    def floor_s(q):
        return float((Decimal(str(q)) / dstep).to_integral_value(ROUND_DOWN) * dstep)

    def ceil_s(q):
        return float((Decimal(str(q)) / dstep).to_integral_value(ROUND_UP) * dstep)

    if max_qty > 0 and qty > max_qty:
        qty = floor_s(max_qty)
    if min_notional > 0 and price > 0 and qty * price < min_notional:
        need = ceil_s(min_notional / price)
        if max_qty <= 0 or need <= max_qty:
            qty = max(qty, need)
    if (
        (min_qty > 0 and qty < min_qty)
        or (max_qty > 0 and qty > max_qty)
        or (min_notional > 0 and price > 0 and qty * price < min_notional)
    ):
        return qty, "qty_unconformable"
    return qty, None


def test_egld_too_large_clamps_down_to_max():
    qty, skip = _conform(3119.0, step=0.1, max_qty=2500.0, min_qty=0.1,
                         min_notional=5.0, price=3.36)
    assert skip is None
    assert qty == 2500.0          # clamped down, trade still places
    assert qty <= 2500.0


def test_small_order_bumped_to_min_notional():
    qty, skip = _conform(1.0, step=0.1, max_qty=2500.0, min_qty=0.1,
                         min_notional=10.0, price=3.36)
    assert skip is None
    assert qty * 3.36 >= 10.0     # bumped up to clear min-notional


def test_unconformable_when_min_exceeds_max_skips():
    qty, skip = _conform(10.0, step=0.1, max_qty=5.0, min_qty=20.0,
                         min_notional=0.0, price=3.36)
    assert skip == "qty_unconformable"   # cannot satisfy both bounds -> skip cleanly


def test_in_bounds_order_unchanged():
    qty, skip = _conform(100.0, step=0.1, max_qty=2500.0, min_qty=0.1,
                         min_notional=5.0, price=3.36)
    assert skip is None
    assert qty == 100.0           # already valid, untouched


def test_instrument_info_exposes_the_conform_fields():
    """Field-contract guard: the live conform block reads info.max_qty /
    min_qty / min_notional / qty_step off InstrumentInfo. If any is renamed,
    the production conform silently stops constraining — pin the names here
    (the pure-logic mirror above cannot catch a field drift)."""
    from src.trading.models.instrument import InstrumentInfo
    fields = set(getattr(InstrumentInfo, "__dataclass_fields__", {}).keys())
    for required in ("max_qty", "min_qty", "min_notional", "qty_step"):
        assert required in fields, f"InstrumentInfo lost field '{required}' — 2.2 conform would break"


def test_unconformable_when_min_notional_exceeds_max_capacity():
    # min_notional requires a qty larger than max_qty allows -> the bump-up is
    # capped at max_qty, the order still violates min_notional, so it must be
    # skipped cleanly rather than sent to the exchange undersized (audit fix).
    qty, skip = _conform(1.0, step=0.1, max_qty=5.0, min_qty=0.1,
                         min_notional=100.0, price=3.36)
    assert skip == "qty_unconformable"
    assert qty * 3.36 < 100.0     # confirms the violated-min-notional precondition
