"""T2-9 STRUCT_GUARD vs Sniper coordination tests (2026-05-12).

Pre-fix bug (F66): two protection layers reached opposite conclusions
on the same position.
  - Time-Decay STRUCT_GUARD said `blocked=true reason='stable'` for
    ENAUSDT (15 events in 5h window) — structure intact, hold.
  - profit_sniper counted 132 stall ticks and fired its escape
    WITHOUT consulting STRUCT_GUARD's verdict.
  - Sniper won unconditionally. STRUCT_GUARD's advisory was ignored.

Operator-approved decision (2026-05-12): STRUCT_GUARD wins on
'stable'. Sniper's stall escape defers when STRUCT_GUARD verdict is
stable AND not stale (< 60 s old). Logged via SNIPER_STRUCT_GUARD_DEFER.

Implementation:
  - Layer4ProtectionService gets a per-symbol verdict cache:
    record_struct_guard_verdict(symbol, "stable" | "unstable") +
    get_struct_guard_verdict(symbol) -> (verdict, age_s).
  - Watchdog calls record_struct_guard_verdict after every
    time_decay.calculate() call.
  - Sniper calls get_struct_guard_verdict before stall escape;
    defers when verdict is "stable" and fresh.
"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_l4p():
    """Build a Layer4ProtectionService with stub deps (cache API only)."""
    from src.risk.layer4_protection import Layer4ProtectionService
    return Layer4ProtectionService(
        settings=MagicMock(),
        coordinator=MagicMock(),
        structure_cache=None,
        regime_detector=None,
        time_decay_calculator=None,
    )


# ── T2-9 unit tests: verdict cache lifecycle ─────────────────────────


def test_t2_9_empty_cache_returns_no_verdict():
    """Newly-constructed L4P has no verdicts → ('', 0.0)."""
    l4p = _make_l4p()
    verdict, age = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == ""
    assert age == 0.0


def test_t2_9_record_then_get_returns_stable():
    """record_struct_guard_verdict('stable') → get returns ('stable', age_s)."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("BTCUSDT", "stable")
    verdict, age = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == "stable"
    assert age >= 0.0
    assert age < 1.0  # just-recorded


def test_t2_9_record_unstable_distinguishes_from_stable():
    """A verdict of 'unstable' is recorded as such; sniper does not defer."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("BTCUSDT", "unstable")
    verdict, _ = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == "unstable"


def test_t2_9_record_unknown_value_normalized_to_unstable():
    """Defensive: any value other than 'stable' is treated as 'unstable'
    (fail-open: don't accidentally defer sniper on unknown verdicts)."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("BTCUSDT", "weird_value")
    verdict, _ = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == "unstable"

    l4p.record_struct_guard_verdict("BTCUSDT", "")
    verdict, _ = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == "unstable"


def test_t2_9_per_symbol_isolation():
    """Verdicts for different symbols don't bleed into each other."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("BTCUSDT", "stable")
    l4p.record_struct_guard_verdict("ETHUSDT", "unstable")
    btc, _ = l4p.get_struct_guard_verdict("BTCUSDT")
    eth, _ = l4p.get_struct_guard_verdict("ETHUSDT")
    sol, _ = l4p.get_struct_guard_verdict("SOLUSDT")
    assert btc == "stable"
    assert eth == "unstable"
    assert sol == ""


def test_t2_9_stale_verdict_treated_as_missing():
    """Verdicts older than STRUCT_GUARD_VERDICT_MAX_AGE_S (60 s) are
    treated as missing — sniper proceeds normally. Bound the staleness
    window so a stale 'stable' cannot defer sniper indefinitely after
    structure has actually broken."""
    from src.risk.layer4_protection import Layer4ProtectionService
    l4p = _make_l4p()
    # Manually inject a stale entry (61 s old)
    l4p._struct_verdicts["BTCUSDT"] = (
        "stable", time.monotonic() - 61.0,
    )
    verdict, age = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == ""
    assert age == 0.0
    # Verify the constant is still 60.0 (locks the production cap)
    assert Layer4ProtectionService.STRUCT_GUARD_VERDICT_MAX_AGE_S == 60.0


def test_t2_9_record_overwrites_previous():
    """A newer verdict for the same symbol replaces the older one."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("BTCUSDT", "stable")
    time.sleep(0.05)
    l4p.record_struct_guard_verdict("BTCUSDT", "unstable")
    verdict, age = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == "unstable"
    assert age < 0.05  # newest verdict


def test_t2_9_fresh_verdict_at_boundary_returned():
    """A verdict at exactly the boundary (just under 60 s) is still
    returned (the > comparison in the code is strict)."""
    l4p = _make_l4p()
    l4p._struct_verdicts["BTCUSDT"] = (
        "stable", time.monotonic() - 59.5,
    )
    verdict, age = l4p.get_struct_guard_verdict("BTCUSDT")
    assert verdict == "stable"
    assert 59.0 < age < 60.0


# ── T2-9 contract test: simulated sniper-side decision ───────────────


def _sniper_should_defer(
    layer4_protection, symbol: str,
) -> bool:
    """Mirror of the sniper's inline T2-9 check in
    profit_sniper._stall_escape_action."""
    if layer4_protection is None:
        return False
    if not hasattr(layer4_protection, "get_struct_guard_verdict"):
        return False
    verdict, _ = layer4_protection.get_struct_guard_verdict(symbol)
    return verdict == "stable"


def test_t2_9_sniper_defers_when_struct_guard_says_stable():
    """End-to-end semantic: STRUCT_GUARD records 'stable' → sniper
    decision returns defer=True."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("ENAUSDT", "stable")
    assert _sniper_should_defer(l4p, "ENAUSDT") is True


def test_t2_9_sniper_proceeds_when_struct_guard_says_unstable():
    """STRUCT_GUARD records 'unstable' → sniper does not defer."""
    l4p = _make_l4p()
    l4p.record_struct_guard_verdict("ENAUSDT", "unstable")
    assert _sniper_should_defer(l4p, "ENAUSDT") is False


def test_t2_9_sniper_proceeds_when_no_verdict_recorded():
    """No verdict for symbol → sniper does not defer."""
    l4p = _make_l4p()
    assert _sniper_should_defer(l4p, "ENAUSDT") is False


def test_t2_9_sniper_proceeds_when_verdict_stale():
    """Stale verdict (>60 s old) → sniper does not defer."""
    l4p = _make_l4p()
    l4p._struct_verdicts["ENAUSDT"] = (
        "stable", time.monotonic() - 61.0,
    )
    assert _sniper_should_defer(l4p, "ENAUSDT") is False


def test_t2_9_sniper_handles_no_l4p_gracefully():
    """When layer4_protection is None (legacy boot), sniper does not
    defer (no breakage, same as pre-T2-9 behaviour)."""
    assert _sniper_should_defer(None, "ENAUSDT") is False


def test_t2_9_sniper_handles_l4p_without_method_gracefully():
    """When layer4_protection has no get_struct_guard_verdict method
    (downgrade scenario), sniper does not defer."""
    class _L4PNoMethod:
        pass
    assert _sniper_should_defer(_L4PNoMethod(), "ENAUSDT") is False
