"""Tests for Phase 3 of the Time-Decay Force-Close Definitive Fix
(2026-05-06) — structural-invalidation gate.

The gate sits inside `time_decay_sl.calculate()` AFTER the Phase 1
min-age guardrail and AFTER `_update_p_win`, BEFORE the existing
`p_win < p_win_force_close` test. When `structural_invalidation_required`
is True (default), force-close is permitted ONLY if the watchdog has
computed `structural_invalidation=True` from the disjunction:

  - XRAY confidence drop >= cfg.xray_drop_threshold from entry, OR
  - setup_type drift (entry_setup_type != current_setup_type), OR
  - regime inverted (Long with current=trending_down OR Short with
    current=trending_up) at >= cfg.regime_inversion_confidence_threshold.

Cache-miss / missing-anchor fail-safe: when the watchdog can't compute
real evidence (services unwired, structure cache miss, regime not yet
computed, no entry-anchor), it returns
`(False, "no_data:<which>")` so the calculator BLOCKS force-close.

Tests A-E exercise the calculator directly with hand-shaped state.
Tests F-G exercise the watchdog's `_compute_structural_invalidation`
helper with mocked services. Test H is the integration check (Phase
1 + 2 + 3 stacked).

No IO, no real DB. Mocks for structure_cache and regime_detector.
"""

import os
import sys
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── Fixtures ───────────────────────────────────────────────────────


def _make_state_with_anchors(**overrides):
    """Factory for a TimeDecayState that already has Phase 3 entry-anchors
    populated. Matches the lazy-init behaviour in PositionWatchdog."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator

    cfg = overrides.pop("_cfg", TimeDecayConfig())
    calc = TimeDecaySLCalculator(cfg)
    defaults = dict(
        symbol="PHASE3",
        direction="Buy",
        entry_price=100.0,
        original_sl_pct=2.0,
        max_hold_seconds=2700,
        atr_5m_pct=0.5,
        regime_confidence=0.6,
        tick_seconds=5.0,
        # Phase 3 anchors — caller sets these in lazy-init
        entry_xray_confidence=0.65,
        entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.70,
    )
    defaults.update(overrides)
    state = calc.create_state(**defaults)
    return calc, state


# ─── A. structural_invalidation=True flips the gate open ────────────


def test_struct_invalidation_true_allows_force_close():
    """When the watchdog has computed real invalidation evidence and
    p_win is below force-close threshold, calculate() must return -1.0."""
    calc, state = _make_state_with_anchors()
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.1                                # Phase 2 ratio 0.55
    state.last_pnl_pct = -1.1
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True,
        invalidation_reason="xray_drop=0.45",
    )
    assert out == -1.0, (
        f"Phase 3 must allow force-close when invalidated; got {out}"
    )


# ─── B. structural_invalidation=False blocks force-close ────────────


def test_struct_invalidation_false_blocks_force_close():
    """When p_win is below force-close but the watchdog reports no
    structural evidence, the gate must block (return None)."""
    calc, state = _make_state_with_anchors()
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.1
    state.last_pnl_pct = -1.1
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=False,
        invalidation_reason="stable",
    )
    assert out is None, (
        f"Phase 3 must block force-close when not invalidated; got {out}"
    )


# ─── C. structural_invalidation_required=False is back-compat no-op ─


def test_struct_invalidation_disabled_falls_through_to_p_win():
    """When the operator disables the gate via config, the calculator
    falls through to the pre-fix p_win-only force-close test."""
    from src.risk.time_decay_sl import TimeDecayConfig
    cfg = TimeDecayConfig(structural_invalidation_required=False)
    calc, state = _make_state_with_anchors(_cfg=cfg)
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.1
    state.last_pnl_pct = -1.1
    # struct_inv=False but the gate is disabled → force-close fires anyway
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=False,
        invalidation_reason="stable",
    )
    assert out == -1.0, (
        f"Phase 3 disabled must defer to existing p_win check; got {out}"
    )


# ─── D. Healthy p_win never reaches the Phase 3 gate ────────────────


def test_struct_gate_inactive_when_p_win_above_threshold():
    """The Phase 3 block-condition is `p_win < p_win_force_close AND
    not structural_invalidation`. With healthy p_win the gate cannot
    fire even when struct_inv=False."""
    calc, state = _make_state_with_anchors()
    # Healthy p_win — well above force-close threshold.
    state.p_win = 0.50
    state.mae_pct = -1.1
    state.last_pnl_pct = -1.1
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=False,
        invalidation_reason="stable",
    )
    # Calculator computed a tighter SL; never reached force-close test
    assert out is not None and out != -1.0


# ─── E. Phase 1 + 2 + 3 stacked — gate ordering ─────────────────────


def test_phases_1_2_3_stacked_block_in_order():
    """Verify gate ordering: Phase 1 (age) fires first, then Phase 2
    (MAE/SL), then Phase 3 (struct), then existing p_win test. Each
    stricter gate is reached only if all prior gates have released."""
    calc, state = _make_state_with_anchors()
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    # Phase 1 trips first: age below 300 s → blocked regardless of others
    state.mae_pct = -1.1
    state.last_pnl_pct = -1.1
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=200,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True,        # would allow Phase 3 → moot
        invalidation_reason="xray_drop",
    )
    assert out is None, "Phase 1 must block at age 200 s before Phase 2/3"

    # Move past Phase 1 but below Phase 2 (MAE/SL ratio 0.20)
    calc2, state2 = _make_state_with_anchors()
    state2.p_win = max(calc2.cfg.p_win_min, calc2.cfg.p_win_force_close - 0.01)
    state2.mae_pct = -0.4                    # ratio 0.20 < 0.50
    state2.last_pnl_pct = -0.4
    out = calc2.calculate(
        state2, current_pnl_pct=-0.4, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True,
        invalidation_reason="xray_drop",
    )
    assert out is None, "Phase 2 must block at MAE/SL ratio 0.20 before Phase 3"

    # Past Phase 1+2 but no struct evidence → Phase 3 blocks
    calc3, state3 = _make_state_with_anchors()
    state3.p_win = max(calc3.cfg.p_win_min, calc3.cfg.p_win_force_close - 0.01)
    state3.mae_pct = -1.1                   # ratio 0.55 > 0.50
    state3.last_pnl_pct = -1.1
    out = calc3.calculate(
        state3, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=False,
        invalidation_reason="stable",
    )
    assert out is None, "Phase 3 must block when no struct evidence"

    # Past all three → p_win force-close fires
    calc4, state4 = _make_state_with_anchors()
    state4.p_win = max(calc4.cfg.p_win_min, calc4.cfg.p_win_force_close - 0.01)
    state4.mae_pct = -1.1
    state4.last_pnl_pct = -1.1
    out = calc4.calculate(
        state4, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True,
        invalidation_reason="xray_drop=0.45,regime_inv:trending_down@0.78",
    )
    assert out == -1.0, "All gates released → p_win check fires force-close"


# ─── F. Watchdog helper detects XRAY drop ───────────────────────────


def test_compute_structural_invalidation_xray_drop_triggers():
    """Watchdog's _compute_structural_invalidation returns True with
    reason 'xray_drop=...' when current XRAY confidence has fallen
    >= cfg.xray_drop_threshold below the entry-time anchor."""
    from src.risk.time_decay_sl import TimeDecayConfig

    @dataclass
    class FakeXray:
        setup_type_confidence: float
        setup_type: Any = None

    @dataclass
    class FakeRegimeEnum:
        value: str

    @dataclass
    class FakeRegime:
        regime: Any
        confidence: float

    structure_cache = MagicMock()
    structure_cache.get = MagicMock(
        return_value=FakeXray(setup_type_confidence=0.30),
    )
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(
        return_value=FakeRegime(FakeRegimeEnum("trending_up"), 0.50),
    )

    # Build a stub watchdog that has just enough surface for the helper.
    from src.workers.position_watchdog import PositionWatchdog
    from src.risk.time_decay_sl import TimeDecaySLCalculator

    wd = PositionWatchdog.__new__(PositionWatchdog)  # bypass __init__
    wd._time_decay = TimeDecaySLCalculator(TimeDecayConfig())
    wd.structure_cache = structure_cache
    wd.regime_detector = regime_detector

    _, state = _make_state_with_anchors(
        entry_xray_confidence=0.65,
        entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.70,
    )
    invalidated, reason = wd._compute_structural_invalidation(
        symbol="PHASE3", side="Buy", state=state,
    )
    # drop = (0.65 - 0.30) / 0.65 = 0.538 > 0.40 threshold
    assert invalidated is True
    assert "xray_drop" in reason


def test_compute_structural_invalidation_minor_xray_drop_blocks():
    """Drop below 40% threshold does NOT trigger invalidation."""
    from src.risk.time_decay_sl import TimeDecayConfig

    @dataclass
    class FakeXray:
        setup_type_confidence: float
        setup_type: Any = None

    @dataclass
    class FakeRegimeEnum:
        value: str

    @dataclass
    class FakeRegime:
        regime: Any
        confidence: float

    structure_cache = MagicMock()
    structure_cache.get = MagicMock(
        return_value=FakeXray(setup_type_confidence=0.55),
    )
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(
        return_value=FakeRegime(FakeRegimeEnum("trending_up"), 0.50),
    )
    from src.workers.position_watchdog import PositionWatchdog
    from src.risk.time_decay_sl import TimeDecaySLCalculator

    wd = PositionWatchdog.__new__(PositionWatchdog)
    wd._time_decay = TimeDecaySLCalculator(TimeDecayConfig())
    wd.structure_cache = structure_cache
    wd.regime_detector = regime_detector

    _, state = _make_state_with_anchors(
        entry_xray_confidence=0.65,
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.70,
    )
    invalidated, reason = wd._compute_structural_invalidation(
        symbol="PHASE3", side="Buy", state=state,
    )
    # drop = (0.65 - 0.55) / 0.65 = 0.154 < 0.40 → no invalidation
    assert invalidated is False
    assert reason == "stable"


# ─── G. Cache-miss fail-safe (no_data:...) blocks force-close ───────


def test_compute_structural_invalidation_cache_miss_fail_safe():
    """Missing structure_cache or returning None → fail-safe block."""
    from src.risk.time_decay_sl import TimeDecayConfig
    from src.workers.position_watchdog import PositionWatchdog
    from src.risk.time_decay_sl import TimeDecaySLCalculator

    structure_cache = MagicMock()
    structure_cache.get = MagicMock(return_value=None)        # cache miss
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(return_value=None)

    wd = PositionWatchdog.__new__(PositionWatchdog)
    wd._time_decay = TimeDecaySLCalculator(TimeDecayConfig())
    wd.structure_cache = structure_cache
    wd.regime_detector = regime_detector

    _, state = _make_state_with_anchors(entry_xray_confidence=0.65)
    invalidated, reason = wd._compute_structural_invalidation(
        symbol="PHASE3", side="Buy", state=state,
    )
    assert invalidated is False
    assert reason.startswith("no_data:")


# ─── H. Regime inversion triggers (Long → trending_down at high conf) ─


def test_compute_structural_invalidation_regime_inversion_triggers_for_long():
    """Long position with current regime trending_down at >=0.60 conf
    triggers invalidation. Ranging/Volatile/Dead do NOT count as inversion."""
    from src.risk.time_decay_sl import TimeDecayConfig
    from src.workers.position_watchdog import PositionWatchdog
    from src.risk.time_decay_sl import TimeDecaySLCalculator

    @dataclass
    class FakeXray:
        setup_type_confidence: float
        setup_type: Any = None

    @dataclass
    class FakeRegimeEnum:
        value: str

    @dataclass
    class FakeRegime:
        regime: Any
        confidence: float

    # XRAY confidence stable so regime is the only signal in play.
    structure_cache = MagicMock()
    structure_cache.get = MagicMock(
        return_value=FakeXray(setup_type_confidence=0.65),
    )
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(
        return_value=FakeRegime(FakeRegimeEnum("trending_down"), 0.78),
    )

    wd = PositionWatchdog.__new__(PositionWatchdog)
    wd._time_decay = TimeDecaySLCalculator(TimeDecayConfig())
    wd.structure_cache = structure_cache
    wd.regime_detector = regime_detector

    _, state = _make_state_with_anchors(
        entry_xray_confidence=0.65,
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.70,
    )
    invalidated, reason = wd._compute_structural_invalidation(
        symbol="PHASE3", side="Buy", state=state,
    )
    assert invalidated is True
    assert "regime_inv:trending_down" in reason

    # Same setup but regime = ranging → NOT inversion (per IMPLEMENT doc test).
    regime_detector.get_coin_regime = MagicMock(
        return_value=FakeRegime(FakeRegimeEnum("ranging"), 0.85),
    )
    _, state2 = _make_state_with_anchors(
        entry_xray_confidence=0.65,
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.70,
    )
    inv2, reason2 = wd._compute_structural_invalidation(
        symbol="PHASE3", side="Buy", state=state2,
    )
    assert inv2 is False, "ranging is regime weakening, not inversion"


# ═══════════════════════════════════════════════════════════════════
# Runner so the file works without pytest
# ═══════════════════════════════════════════════════════════════════


def _run_all() -> None:
    tests = [
        fn for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as e:
            failed.append((fn.__name__, str(e)))
            print(f"  [FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed.append((fn.__name__, f"{type(e).__name__}: {e}"))
            print(f"  [ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    _run_all()


# ─── Issue 3 (2026-06-08): MAE recovery-responsive tightening ──────────


def _recovery_state(cfg):
    """A state that dipped to a worst MAE then recovered, with a wide budget
    already locked during the dip (last_allowed_loss=1.5)."""
    calc, state = _make_state_with_anchors(_cfg=cfg)
    state.p_win = 0.9                  # high → no force-close
    state.mae_pct = -1.09             # worst excursion held
    state.last_allowed_loss = 1.5     # wide budget set during the worst dip
    state.last_pnl_pct = -0.2
    return calc, state


def _run_recovery(calc, state, current_pnl):
    return calc.calculate(
        state, current_pnl_pct=current_pnl,
        position_age_seconds=400, regime_still_supports=True,
        velocity_pct_per_s=0.02, acceleration_pct_per_s2=0.005,
        structural_invalidation=False, invalidation_reason="stable",
    )


def test_issue3_recovery_tighten_fires_on_strong_recovery():
    from src.risk.time_decay_sl import TimeDecayConfig
    cfg = TimeDecayConfig(mae_recovery_tighten_enabled=True,
                          mae_tightening_recovery_threshold=0.75,
                          recovery_tightening_buffer_pct=0.3)
    calc, state = _recovery_state(cfg)
    # recovery = (-0.2 - (-1.09))/1.09 = 0.817 >= 0.75 → tighten to
    # allowed_loss = -(-0.2) + 0.3 = 0.5% → SL = 100*(1-0.005) = 99.5
    out = _run_recovery(calc, state, current_pnl=-0.2)
    assert out is not None, "strong recovery must tighten the stop"
    assert state.last_allowed_loss <= 0.5 + 1e-9, state.last_allowed_loss
    assert abs(out - 99.5) < 0.05, out


def test_issue3_recovery_tighten_skips_moderate_recovery():
    """A moderate recovery (below the threshold) keeps the room the 1.2 bonus
    grants — the recovery-tighten does NOT fire, so the wide budget stands."""
    from src.risk.time_decay_sl import TimeDecayConfig
    cfg = TimeDecayConfig(mae_recovery_tighten_enabled=True,
                          mae_tightening_recovery_threshold=0.75,
                          recovery_tightening_buffer_pct=0.3)
    calc, state = _recovery_state(cfg)
    # recovery = (-0.6 - (-1.09))/1.09 = 0.45 < 0.75 → no recovery-tighten
    out = _run_recovery(calc, state, current_pnl=-0.6)
    # not tightened to the recovery level (0.6+0.3=0.9); stays wider/no-tighten
    assert state.last_allowed_loss > 0.9 or out is None, state.last_allowed_loss


def test_issue3_recovery_tighten_off_switch():
    from src.risk.time_decay_sl import TimeDecayConfig
    cfg = TimeDecayConfig(mae_recovery_tighten_enabled=False)
    calc, state = _recovery_state(cfg)
    out = _run_recovery(calc, state, current_pnl=-0.2)
    # off-switch: no recovery-tighten — must NOT pin the stop at the 0.5 level
    assert not (out is not None and abs(out - 99.5) < 0.05), (
        "off-switch must not apply the recovery-tighten"
    )
