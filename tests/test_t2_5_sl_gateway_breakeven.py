"""T2-5 sl_gateway breakeven step_exceeded fix tests (2026-05-12).

Pre-fix bug (F57): on 45-50 min positions near breakeven, the sniper
attempts to move SL to lock breakeven (move SL toward entry).
The proposed step is ~0.9 % (the original SL distance from entry).
sl_gateway max_step_pct=0.25 rejects with step_exceeded. Position
keeps its further SL, exposing more capital to potential loss.
4 symbols confirmed: RENDER, ALGO, BCH, plus one earlier.

Operator decision: special-case breakeven moves. Add an opt-in
`bypass_step_cap_for_breakeven` kwarg gated by a source allowlist.
The bypass is narrowly scoped to profit-locking moves (sniper /
sentinel breakeven-protect paths) and always logged via
SL_GATEWAY_BREAKEVEN_OVERRIDE so the operator sees every large-step
breakeven move that the cap would otherwise have rejected.

R1 tighten-only invariant is NEVER bypassed — a breakeven move that
would loosen SL is still rejected.

Tests are pure-logic — they exercise the bypass conditions without
requiring the full gateway / market_service wiring.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.sl_gateway import SLGateway  # noqa: E402  (after sys.path setup)

# Allowlist mirror — DERIVED from the real constant so it can never drift out of
# sync. It previously hard-coded only the original 3 sources and went stale as
# the profit-fetching (ladder, safety_sweeper) and loss-cutting (loss_cap, ...)
# systems added their R3-bypass sources; the dedicated drift-lock test below
# pins the constant's exact contents.
_ALLOWED_SOURCES = SLGateway._BREAKEVEN_BYPASS_SOURCES


def _bypass_logic(
    *,
    bypass_step_cap_for_breakeven: bool,
    source: str,
    bypass_step_cap: bool = False,
) -> bool:
    """Mirror of the inline T2-5 effective-bypass calc in
    sl_gateway.apply (post-fix lines around the R3 block).

    Returns True iff the R3 step cap is bypassed for this call.
    """
    breakeven_bypass = (
        bypass_step_cap_for_breakeven
        and source in _ALLOWED_SOURCES
    )
    return bypass_step_cap or breakeven_bypass


# ── T2-5 unit tests: bypass conditions ───────────────────────────────


def test_t2_5_bypass_with_allowed_source_honored():
    """profit_sniper_lock + bypass=True → R3 bypassed."""
    assert _bypass_logic(
        bypass_step_cap_for_breakeven=True,
        source="profit_sniper_lock",
    ) is True


def test_t2_5_trail_now_bypasses_r3_finding_h():
    """Finding H (2026-06-08): the Chandelier runner trail
    (profit_sniper_trail) is now IN the allowlist, so bypass=True bypasses R3.
    On a fast vertical runner the peak-anchored trail wins highest-stop-wins
    but, when R3-clamped to 0.25%/tick, the protected floor lagged the peak by
    ~1.4% per write (AAVE: chandelier raw 64.197 clamped to 63.298). The trail
    is a monotonic, peak-anchored protective tighten; bypassing R3 lets the
    floor reach (high_water - ATR leash) at speed. R1/R2/R4 still apply.
    (Arbitrary disallowed sources are covered by the sibling test below.)"""
    assert _bypass_logic(
        bypass_step_cap_for_breakeven=True,
        source="profit_sniper_trail",
    ) is True


def test_t2_5_bypass_with_arbitrary_source_rejected():
    """Any source not in the allowlist → no bypass. Defends against
    future callers misusing the kwarg without code review."""
    for src in ("time_decay", "claude_brain", "sentinel_advisor", "manual", ""):
        assert _bypass_logic(
            bypass_step_cap_for_breakeven=True,
            source=src,
        ) is False, f"source={src} should not be allowed"


def test_t2_5_bypass_disabled_with_allowed_source_no_bypass():
    """Allowed source + bypass=False → R3 still enforced. The kwarg
    must be EXPLICITLY set to True for the bypass to fire."""
    assert _bypass_logic(
        bypass_step_cap_for_breakeven=False,
        source="profit_sniper_lock",
    ) is False


def test_t2_5_legacy_bypass_step_cap_still_works():
    """The pre-T2-5 `bypass_step_cap=True` kwarg still bypasses R3
    regardless of source (used by Time-Decay urgent force-exits per
    the existing docstring). Backward-compat preserved."""
    assert _bypass_logic(
        bypass_step_cap_for_breakeven=False,
        source="time_decay",
        bypass_step_cap=True,
    ) is True


def test_t2_5_combined_bypass_does_not_double_count():
    """Both bypass kwargs True simultaneously → still True (idempotent)."""
    assert _bypass_logic(
        bypass_step_cap_for_breakeven=True,
        source="profit_sniper_lock",
        bypass_step_cap=True,
    ) is True


# ── T2-5 contract test: allowlist composition ────────────────────────


def test_t2_5_allowlist_locked_against_drift():
    """The allowlist content is locked by this test so adding a new
    source (e.g. someone wires a new caller into the bypass) requires
    a deliberate test update — preserves auditability."""
    from src.core.sl_gateway import SLGateway
    expected = frozenset({
        "profit_sniper_lock",
        "profit_sniper_breakeven",
        "sentinel_breakeven",
        # Profit-Fetching Exit System ladder (2026-05-29) — deliberate,
        # operator-gated addition. Ladder steps (~0.5%) exceed the R3
        # max_step cap; this source bypasses R3 only (R1/R2/R4 still apply).
        "profit_sniper_ladder",
        # Profit-Fetching safety stop / naked-position sweeper (2026-05-29) —
        # re-asserting the loss-cap floor bypasses R3 only.
        "safety_sweeper",
        # Loss-Cutting System (2026-05-31) — deliberate, operator-gated
        # additions. The sacred cap and the structure/recovery stops are placed
        # at their true distance (not ratcheted a quarter-step at a time), so
        # each can exceed the R3 max-step in one move; they bypass R3 ONLY
        # (R1 tighten-only and R2 min-distance still hold). The volatility-spike
        # catastrophe stop force-CLOSES (no SL), so it needs no bypass source.
        "loss_cap",
        "loss_cap_emergency",
        "loss_atr_initial",
        "loss_structure",
        "loss_recovery",
        # Profit-Fetching Chandelier runner trail (Finding H, 2026-06-08) —
        # deliberate, operator-gated addition. On a fast vertical runner the
        # peak-anchored trail wins highest-stop-wins; clamping it to R3
        # max-step lagged the protected floor behind the peak. It is a
        # monotonic peak-anchored protective tighten, so it bypasses R3 ONLY
        # (R1 tighten-only, R2 min-distance, R4 rate-limit still apply); the
        # ATR leash is the sole noise guard.
        "profit_sniper_trail",
    })
    assert SLGateway._BREAKEVEN_BYPASS_SOURCES == expected


def test_t2_5_apply_signature_accepts_new_kwarg():
    """The new kwarg is in the apply() signature with default False
    (backward-compat for all existing callers that don't pass it)."""
    import inspect

    from src.core.sl_gateway import SLGateway
    sig = inspect.signature(SLGateway.apply)
    assert "bypass_step_cap_for_breakeven" in sig.parameters
    assert sig.parameters["bypass_step_cap_for_breakeven"].default is False


# ── T2-5 contract test: R1 invariant preserved ───────────────────────


def _r1_check(direction: str, new_sl: float, cur_sl: float) -> bool:
    """Mirror of the gateway's R1 tighten-only check.
    Returns True if R1 PASSES (move is a tighten); False if R1 rejects."""
    if direction in ("Buy", "Long"):
        return new_sl > cur_sl
    return new_sl < cur_sl


def test_t2_5_breakeven_move_toward_entry_passes_r1():
    """A profit-lock move toward entry on a Long position
    (new_sl > cur_sl) passes R1. The bypass would then allow the
    large-step move through R3."""
    # Long position, entry=100, cur_sl=99, new_sl=99.5 (toward entry)
    assert _r1_check("Buy", new_sl=99.5, cur_sl=99.0) is True


def test_t2_5_breakeven_move_away_from_entry_fails_r1():
    """A move AWAY from entry (loosen) is rejected by R1 even with
    the breakeven bypass — the bypass only widens R3, not R1."""
    # Long position, entry=100, cur_sl=99.5, new_sl=99.0 (away)
    assert _r1_check("Buy", new_sl=99.0, cur_sl=99.5) is False
    # Sell position, mirror
    assert _r1_check("Sell", new_sl=101.0, cur_sl=100.5) is False
