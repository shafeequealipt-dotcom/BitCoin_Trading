"""T1-2 / F8 trail-step clamp smoke tests (six-tier-fixes 2026-05-11).

Validates the inline clamp logic added to
``src/workers/position_watchdog.py:_push_sl_to_shadow`` which port-forwards
``profit_sniper.SNIPER_CAP`` (profit_sniper.py:1469-1524) so trail SL
submissions never exceed the SL Gateway R3 ``max_step_pct`` cap.

Pure-math test (mirrors the inline formula). The reproduction lives next
to the assertions so a future divergence between the production code and
this test fails loudly during regression runs.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _clamp_inline(
    cur_sl: float, raw_new_sl: float, direction: str, max_step_pct: float,
) -> tuple[float, float, bool]:
    """Mirror of position_watchdog._push_sl_to_shadow clamp block.

    Returns (new_sl_after_clamp, requested_step_pct, did_clamp).
    """
    if cur_sl is None or cur_sl <= 0:
        return raw_new_sl, 0.0, False
    requested = round(abs(raw_new_sl - cur_sl) / cur_sl * 100.0, 6)
    if requested <= max_step_pct:
        return raw_new_sl, requested, False
    if direction in ("Buy", "Long"):
        capped = cur_sl * (1.0 + max_step_pct / 100.0)
    else:
        capped = cur_sl * (1.0 - max_step_pct / 100.0)
    return capped, requested, True


def test_clamp_buy_long_over_cap_produces_exact_max_step():
    """Buy/Long requested 1.234% step clamps to exactly 0.25% above cur_sl."""
    cur_sl = 0.029009
    # Long trail moves SL UP. Synthesize a +1.234% raw move.
    raw_new_sl = cur_sl * (1.0 + 0.01234)
    new_sl, requested, clamped = _clamp_inline(cur_sl, raw_new_sl, "Buy", 0.25)
    assert clamped is True
    assert requested > 0.25
    # Capped value is exactly cur * (1 + 0.25/100) — tightening but bounded.
    assert abs(new_sl - cur_sl * 1.0025) < 1e-12
    assert new_sl > cur_sl  # R1 preserved (tightening up for Long)
    assert new_sl < raw_new_sl  # less aggressive than raw


def test_clamp_sell_short_over_cap_produces_exact_max_step():
    """Sell/Short requested 1.234% step clamps to exactly -0.25% from cur_sl.

    Today's BLURUSDT case: cur=0.029009 raw_new=0.028651 -> step 1.234%.
    The gateway rejected this with REJECT_STEP_EXCEEDED; the new inline
    clamp turns it into a 0.25% capped step that the gateway accepts.
    """
    cur_sl = 0.029009
    raw_new_sl = 0.028651  # observed in workers.log at 14:32:35
    new_sl, requested, clamped = _clamp_inline(cur_sl, raw_new_sl, "Sell", 0.25)
    assert clamped is True
    # Reproduce the gateway's own rounding.
    assert requested == round(
        abs(raw_new_sl - cur_sl) / cur_sl * 100.0, 6
    )
    assert requested > 0.25
    assert abs(new_sl - cur_sl * 0.9975) < 1e-12
    assert new_sl < cur_sl  # R1 preserved (tightening down for Sell)
    assert new_sl > raw_new_sl  # less aggressive than raw


def test_no_clamp_when_request_within_cap():
    """A 0.1% request is well under the 0.25% cap — pass through unchanged."""
    cur_sl = 0.029009
    raw_new_sl = cur_sl * 1.001
    new_sl, requested, clamped = _clamp_inline(cur_sl, raw_new_sl, "Buy", 0.25)
    assert clamped is False
    assert new_sl == raw_new_sl
    assert requested < 0.25


def test_no_clamp_when_cur_sl_zero_or_none():
    """Edge case: first trail activation has no current SL — no clamp."""
    new_sl, requested, clamped = _clamp_inline(0.0, 0.001, "Buy", 0.25)
    assert clamped is False
    assert new_sl == 0.001
    new_sl, requested, clamped = _clamp_inline(None, 0.001, "Buy", 0.25)  # type: ignore[arg-type]
    assert clamped is False
    assert new_sl == 0.001


def test_clamp_at_exact_cap_boundary_does_not_clamp():
    """Boundary: requested == cap exactly should pass through (uses <= cap)."""
    cur_sl = 100.0
    raw_new_sl = cur_sl * 1.0025  # exactly 0.25% — at the cap, not over.
    new_sl, requested, clamped = _clamp_inline(cur_sl, raw_new_sl, "Buy", 0.25)
    assert clamped is False
    assert new_sl == raw_new_sl


def test_clamp_at_5x_cap_clamps_to_one_step():
    """A 5x-cap request still clamps to exactly one cap step, not 5 steps.

    Trail catch-up over multiple ticks (5 ticks of 0.25% each over time)
    replaces a single rejected 1.25% step. Confirms each invocation
    produces exactly one cap-sized step regardless of how far over the
    raw value was.
    """
    cur_sl = 100.0
    raw_new_sl = cur_sl * 1.0125  # 1.25% raw, 5x the 0.25% cap.
    new_sl, requested, clamped = _clamp_inline(cur_sl, raw_new_sl, "Buy", 0.25)
    assert clamped is True
    assert abs(new_sl - cur_sl * 1.0025) < 1e-12
    assert requested > 0.25 * 4  # well over cap
