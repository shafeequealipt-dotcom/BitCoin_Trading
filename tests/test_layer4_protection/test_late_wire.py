"""Issue 5 of cascade-fix series — Layer4ProtectionService late-wire.

Background: ``Layer4ProtectionService`` is constructed at
``manager.py:~1323`` with ``regime_detector=services.get("regime_detector")``.
But the ``RegimeDetector`` is built later (around ``manager.py:~1469``),
so at construction time ``services.get("regime_detector")`` returns
``None``. Without a late-wire, L4's ``compute_structural_invalidation``
returns ``(False, "no_data:services_unwired")`` perpetually; the
time-decay calculator (``time_decay_sl.py:397-412``) then BLOCKS every
loser-lane force-close.

The fix adds a late-wire block in ``WorkerManager._setup`` immediately
after the existing regime_detector late-wires for watchdog,
volatility_profiler, and scanner.

These tests pin:
1. The gate behavior: regime_detector=None → returns
   ``(False, "no_data:services_unwired")``.
2. The fix behavior: after attribute reassignment, the gate proceeds
   past the unwired branch into the real signal-disjunction logic.
3. Source-level guard: manager.py contains the late-wire block so
   future refactors that drop it fail loudly in CI rather than
   silently disabling the gate.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.risk.layer4_protection import Layer4ProtectionService


def _build_l4(
    *, regime_detector, structure_cache, time_decay_calculator,
) -> Layer4ProtectionService:
    """Bypass settings/coordinator wiring — only the structural guard
    is exercised, so a minimal L4 is enough."""
    settings = MagicMock()
    coordinator = MagicMock()
    return Layer4ProtectionService(
        settings=settings,
        coordinator=coordinator,
        structure_cache=structure_cache,
        regime_detector=regime_detector,
        time_decay_calculator=time_decay_calculator,
    )


def _make_calc_cfg() -> MagicMock:
    """Minimal time-decay calculator with cfg attribute populated."""
    cfg = MagicMock()
    cfg.xray_drop_threshold = 0.40
    cfg.regime_inversion_confidence_threshold = 0.60
    calc = MagicMock()
    calc.cfg = cfg
    return calc


def test_gate_returns_services_unwired_when_regime_detector_is_none() -> None:
    """Reproduces the pre-fix failure mode that fired 130 times in the
    Phase 0 baseline 2-hour log window."""
    state = MagicMock()
    state.symbol = "LINKUSDT"
    state.side = "Buy"
    state.entry_xray_confidence = 0.70
    state.entry_setup_type = "bullish_fvg_ob"
    state.entry_regime_at_open = "ranging"

    l4 = _build_l4(
        regime_detector=None,
        structure_cache=MagicMock(),
        time_decay_calculator=_make_calc_cfg(),
    )
    invalidated, reason = l4.compute_structural_invalidation(
        symbol="LINKUSDT", side="Buy", state=state,
    )
    assert invalidated is False
    assert reason == "no_data:services_unwired"


def test_gate_returns_services_unwired_when_structure_cache_is_none() -> None:
    """Same gate, the other half of the OR. Defensive — Phase 0
    confirmed structure_cache is created early enough that it's not
    actually None in production, but the late-wire idempotently
    re-attaches it so this branch must remain symmetric with the
    regime_detector branch."""
    state = MagicMock()
    state.symbol = "BTCUSDT"
    state.side = "Sell"

    l4 = _build_l4(
        regime_detector=MagicMock(),
        structure_cache=None,
        time_decay_calculator=_make_calc_cfg(),
    )
    invalidated, reason = l4.compute_structural_invalidation(
        symbol="BTCUSDT", side="Sell", state=state,
    )
    assert invalidated is False
    assert reason == "no_data:services_unwired"


def test_gate_proceeds_past_services_unwired_after_late_wire() -> None:
    """The fix: after attribute reassignment (mimicking the
    manager.py:_l4.regime_detector = detector pattern), the gate no
    longer returns services_unwired. It now reaches the per-signal
    cache lookups and returns a different reason
    (``no_data:xray_cache_miss`` here because we leave the structure
    cache unpopulated). The point is: services_unwired must NOT be
    the answer once both services are attached."""
    state = MagicMock()
    state.symbol = "ETHUSDT"
    state.side = "Buy"
    state.entry_xray_confidence = 0.70
    state.entry_setup_type = "bullish_fvg_ob"
    state.entry_regime_at_open = "ranging"

    # Build with None to simulate construction-time wiring.
    l4 = _build_l4(
        regime_detector=None,
        structure_cache=None,
        time_decay_calculator=_make_calc_cfg(),
    )
    # Confirm pre-late-wire reproducer fires.
    pre_invalidated, pre_reason = l4.compute_structural_invalidation(
        symbol="ETHUSDT", side="Buy", state=state,
    )
    assert pre_reason == "no_data:services_unwired"

    # Apply the late-wire — same operation manager.py performs.
    structure_cache = MagicMock()
    structure_cache.get = MagicMock(return_value=None)
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(return_value=None)
    l4.regime_detector = regime_detector
    l4.structure_cache = structure_cache

    # Now the gate proceeds past the services_unwired branch.
    post_invalidated, post_reason = l4.compute_structural_invalidation(
        symbol="ETHUSDT", side="Buy", state=state,
    )
    assert post_reason != "no_data:services_unwired", (
        f"After late-wire the gate must not return services_unwired; "
        f"got reason={post_reason!r}"
    )
    # Mock structure_cache.get returns None so we expect xray_cache_miss
    # — confirms the gate is now exercising real per-signal logic.
    assert post_reason == "no_data:xray_cache_miss"


def test_manager_contains_l4_late_wire_block() -> None:
    """Source-level pin — if a refactor removes the late-wire block,
    this test fails immediately rather than the bug shipping silently.
    Also pins the log tag so observability stays consistent."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/workers/manager.py", encoding="utf-8",
    ).read()

    # Late-wire fetches from the service container by the production
    # service-name key.
    assert 'self._services.get("layer4_protection")' in src, (
        "Late-wire must look up layer4_protection in the service "
        "container; key changed?"
    )
    # Both attribute reassignments happen.
    assert "_l4.regime_detector = detector" in src, (
        "L4 late-wire must reassign regime_detector after RegimeDetector "
        "is built. Issue 5 cascade-fix regressed."
    )
    assert "_l4.structure_cache = self._services.get" in src, (
        "L4 late-wire must idempotently re-attach structure_cache. "
        "Issue 5 cascade-fix regressed."
    )
    # L4_LATE_WIRE log tag fires for observability.
    assert "L4_LATE_WIRE" in src, (
        "L4 late-wire must emit L4_LATE_WIRE log for observability. "
        "Issue 5 cascade-fix regressed."
    )
