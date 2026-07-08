"""Layer 4 Realignment Phase 4.1 — Layer4ProtectionService unit tests.

Covers the four core behaviours of the new shared service:
- min-hold guardrail (block on young, bypass on allow-list reason)
- profit / development guards
- structural-invalidation gate (fail-safe when no state)
- compose: all checks combine cleanly + each independently togglable
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from src.config.settings import (
    Layer4SniperSettings,
    Settings,
    TimeDecaySettings,
    WatchdogSettings,
)
from src.risk.layer4_protection import (
    Layer4ProtectionService,
    ProtectionResult,
)
from src.risk.time_decay_sl import (
    TimeDecayConfig,
    TimeDecaySLCalculator,
    TimeDecayState,
)


def _make_settings(*, min_hold: float = 300.0) -> MagicMock:
    """Build a Settings-like object with the fields the service reads."""
    s = MagicMock(spec=Settings)
    s.watchdog = WatchdogSettings(strategic_action_min_hold_seconds=min_hold)
    s.layer4_sniper = Layer4SniperSettings()
    s.time_decay = TimeDecaySettings()
    return s


def _make_service(
    *,
    age_seconds: float = 600.0,
    structural_required: bool = True,
    cur_xray: object | None = None,
    cur_regime: object | None = None,
) -> Layer4ProtectionService:
    """Build a Layer4ProtectionService with mocked dependencies."""
    settings = _make_settings()
    coord = MagicMock()
    coord.get_age_seconds.return_value = float(age_seconds)
    structure_cache = MagicMock()
    structure_cache.get.return_value = cur_xray
    regime_detector = MagicMock()
    regime_detector.get_coin_regime.return_value = cur_regime
    td_calc = TimeDecaySLCalculator(
        config=TimeDecayConfig(
            structural_invalidation_required=structural_required,
        ),
    )
    return Layer4ProtectionService(
        settings=settings,
        coordinator=coord,
        structure_cache=structure_cache,
        regime_detector=regime_detector,
        time_decay_calculator=td_calc,
    )


def _make_state_with_anchors() -> TimeDecayState:
    """A TimeDecayState with entry-side anchors populated so the
    structural check has something to compare against."""
    return TimeDecayState(
        symbol="ETHUSDT",
        direction="Buy",
        entry_price=2000.0,
        original_sl_pct=1.0,
        max_hold_seconds=600,
        atr_5m_pct=0.5,
        regime_confidence=0.65,
        entry_xray_confidence=0.70,
        entry_setup_type="bullish_fvg_ob",
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.65,
    )


def test_min_hold_blocks_young_position() -> None:
    svc = _make_service(age_seconds=120.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="strategic_review: thesis broken",
            check_min_hold=True,
            check_profit=False,
            check_structural=False,
        ),
    )
    assert res.protected is True
    assert "min_hold" in res.reason
    assert "120s" in res.reason


def test_min_hold_bypassed_by_allow_list_reason() -> None:
    svc = _make_service(age_seconds=120.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="stop loss hit",
            check_min_hold=True,
            check_profit=False,
            check_structural=False,
        ),
    )
    assert res.protected is False, "SL hit must bypass min-hold"


def test_profit_guard_blocks_winners() -> None:
    svc = _make_service(age_seconds=600.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="mode4_p9_stall",
            pnl_pct=1.0,
            check_min_hold=False,
            check_profit=True,
            check_structural=False,
        ),
    )
    assert res.protected is True
    assert "profit_guard" in res.reason


def test_development_guard_blocks_small_losses() -> None:
    svc = _make_service(age_seconds=600.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="mode4_p9_stall",
            pnl_pct=-0.1,
            check_min_hold=False,
            check_profit=True,
            check_structural=False,
        ),
    )
    assert res.protected is True
    assert "development_guard" in res.reason


def test_meaningful_loss_passes_profit_guard() -> None:
    svc = _make_service(age_seconds=600.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="mode4_p9_stall",
            pnl_pct=-0.5,
            check_min_hold=False,
            check_profit=True,
            check_structural=False,
        ),
    )
    assert res.protected is False, "loss > -0.3% should not be guarded"


def test_structural_check_fail_safe_when_no_state() -> None:
    """Without time_decay_state we cannot evaluate invalidation —
    service must default to protected=True (fail-safe)."""
    svc = _make_service(age_seconds=600.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="mode4_p9_stall",
            check_min_hold=False,
            check_profit=False,
            check_structural=True,
        ),
    )
    assert res.protected is True
    assert "no_state_provided" in res.reason


def test_structural_check_blocks_when_evidence_stable() -> None:
    """With anchors and matching current state, no invalidation →
    structurally healthy → close blocked."""
    cur_xray = MagicMock()
    cur_xray.setup_type_confidence = 0.70   # same as entry
    cur_xray.setup_type = "bullish_fvg_ob"  # same as entry
    cur_regime = MagicMock()
    cur_regime.regime = MagicMock(value="trending_up")  # same direction
    cur_regime.confidence = 0.65
    svc = _make_service(
        age_seconds=600.0,
        cur_xray=cur_xray,
        cur_regime=cur_regime,
    )
    state = _make_state_with_anchors()
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="mode4_p9_stall",
            check_min_hold=False,
            check_profit=False,
            check_structural=True,
            time_decay_state=state,
        ),
    )
    assert res.protected is True
    assert "struct:intact" in res.reason


def test_structural_check_passes_when_invalidated() -> None:
    """Regime inverted with high confidence → invalidation evidence
    real → close NOT blocked by structural check."""
    cur_xray = MagicMock()
    cur_xray.setup_type_confidence = 0.70   # XRAY stable
    cur_xray.setup_type = "bullish_fvg_ob"   # setup stable
    cur_regime = MagicMock()
    cur_regime.regime = MagicMock(value="trending_down")  # INVERTED
    cur_regime.confidence = 0.75              # above threshold (0.60)
    svc = _make_service(
        age_seconds=600.0,
        cur_xray=cur_xray,
        cur_regime=cur_regime,
    )
    state = _make_state_with_anchors()
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="mode4_p9_stall",
            check_min_hold=False,
            check_profit=False,
            check_structural=True,
            time_decay_state=state,
        ),
    )
    assert res.protected is False
    # struct branch returns no_protection because invalidation evidence exists
    assert res.reason == "no_protection"


def test_no_protection_when_all_checks_disabled() -> None:
    """When the caller disables every check (paranoia path), service
    returns protected=False with reason='no_protection'."""
    svc = _make_service(age_seconds=120.0)
    res = asyncio.run(
        svc.is_protected(
            symbol="ETHUSDT",
            side="Buy",
            close_reason="anything",
            check_min_hold=False,
            check_profit=False,
            check_structural=False,
        ),
    )
    assert res.protected is False
    assert res.reason == "no_protection"


def test_compute_structural_invalidation_fail_safe_paths() -> None:
    """Direct unit-test for the structural function's no-data paths.
    Mirrors the verbatim behaviour moved from PositionWatchdog."""
    svc = _make_service(age_seconds=600.0, cur_xray=None)  # cache miss
    state = _make_state_with_anchors()
    invalidated, reason = svc.compute_structural_invalidation(
        symbol="ETHUSDT", side="Buy", state=state,
    )
    assert invalidated is False
    assert reason == "no_data:xray_cache_miss"
