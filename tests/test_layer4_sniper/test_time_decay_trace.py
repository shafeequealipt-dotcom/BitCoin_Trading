"""Layer 4 Realignment Phase 2 — TIME_DECAY_FORCE_CLOSE_TRACE event.

Smoke test for the new evidence trace emitted by
``time_decay_sl.calculate()`` immediately before any force-close. The
trace fires unconditionally — even when ``structural_invalidation_
required=False`` — so every force-close has a forensic record of
entry anchors, current structural state, and the calculator config.
"""

from __future__ import annotations

from loguru import logger

from src.risk.time_decay_sl import (
    TimeDecayConfig,
    TimeDecaySLCalculator,
    TimeDecayState,
)


class _LogCapture:
    """Loguru sink that captures emitted records into a list. The
    project uses loguru and pytest's stdlib ``caplog`` does not see
    loguru output unless a propagation handler is configured. This
    captor avoids the propagation dance for unit tests."""

    def __init__(self) -> None:
        self.records: list[str] = []
        self._handle: int | None = None

    def __enter__(self):
        self._handle = logger.add(
            lambda msg: self.records.append(msg.record["message"]),
            level="DEBUG",
            format="{message}",
        )
        return self

    def __exit__(self, *exc):
        if self._handle is not None:
            logger.remove(self._handle)


def _make_calculator(*, structural_required: bool = True) -> TimeDecaySLCalculator:
    """Build a calculator whose Phase 3 gate is configurable so the
    TRACE event can be verified in both required and back-compat modes."""
    cfg = TimeDecayConfig(
        structural_invalidation_required=structural_required,
        # Disable Phase 1 (min-age) + grace + Phase 2 (MAE/SL) gates so
        # the test reaches the force-close branch with a low p_win.
        min_age_seconds=0.0,
        grace_seconds=0,
        mae_to_sl_ratio_threshold=0.0,
    )
    return TimeDecaySLCalculator(config=cfg)


def _make_state_at_force_close() -> TimeDecayState:
    """Build a state with low p_win + entry anchors populated."""
    return TimeDecayState(
        symbol="ETHUSDT",
        direction="Buy",
        entry_price=2000.0,
        original_sl_pct=1.0,
        max_hold_seconds=600,
        atr_5m_pct=0.5,
        regime_confidence=0.65,
        p_win=0.10,                       # below default p_win_force_close=0.15
        mae_pct=-0.5,
        last_pnl_pct=-0.4,
        # Phase 3 entry anchors:
        entry_xray_confidence=0.70,
        entry_setup_type="bullish_fvg_ob",
        entry_regime_at_open="trending_up",
        entry_regime_confidence=0.65,
    )


def test_force_close_trace_emitted_with_evidence() -> None:
    """When force-close fires, TIME_DECAY_FORCE_CLOSE_TRACE precedes it
    in the log stream and carries full entry-side evidence."""
    calc = _make_calculator(structural_required=True)
    state = _make_state_at_force_close()

    with _LogCapture() as cap:
        outcome = calc.calculate(
            state,
            current_pnl_pct=-0.4,
            position_age_seconds=600.0,
            regime_still_supports=True,
            velocity_pct_per_s=0.0,
            acceleration_pct_per_s2=0.0,
            structural_invalidation=True,
            invalidation_reason="xray_drop=0.42,regime_inv:trending_down@0.70",
        )

    assert outcome == -1.0, "expected force-close outcome (-1.0)"
    trace_lines = [r for r in cap.records if "TIME_DECAY_FORCE_CLOSE_TRACE" in r]
    assert trace_lines, "expected TIME_DECAY_FORCE_CLOSE_TRACE event"
    msg = trace_lines[0]
    # Entry anchors present
    assert "entry_xray=0.70" in msg
    assert "entry_setup=bullish_fvg_ob" in msg
    assert "entry_regime=trending_up" in msg
    # Phase 3 mode + result
    assert "struct_required=True" in msg
    assert "struct_invalidation=True" in msg
    # Evidence reason passed through verbatim
    assert "xray_drop=0.42" in msg
    assert "regime_inv:trending_down@0.70" in msg

    # FORCE_CLOSE itself still emits AFTER the trace.
    fc_lines = [r for r in cap.records if "TIME_DECAY_FORCE_CLOSE |" in r]
    assert fc_lines, "TIME_DECAY_FORCE_CLOSE should still emit"


def test_force_close_trace_fires_when_structural_required_false() -> None:
    """Even in back-compat mode (structural_invalidation_required=False),
    the TRACE event still fires so operators get a forensic record."""
    calc = _make_calculator(structural_required=False)
    state = _make_state_at_force_close()

    with _LogCapture() as cap:
        outcome = calc.calculate(
            state,
            current_pnl_pct=-0.4,
            position_age_seconds=600.0,
            regime_still_supports=True,
            velocity_pct_per_s=0.0,
            acceleration_pct_per_s2=0.0,
            structural_invalidation=False,
            invalidation_reason="stable",
        )

    assert outcome == -1.0
    trace_lines = [r for r in cap.records if "TIME_DECAY_FORCE_CLOSE_TRACE" in r]
    assert trace_lines, "TRACE should fire even when struct_required=False"
    msg = trace_lines[0]
    assert "struct_required=False" in msg
    assert "struct_invalidation=False" in msg
    assert "reason='stable'" in msg
    # In back-compat mode the existing STRUCT_INVALIDATED line does NOT
    # fire (gated on structural_invalidation_required), but FORCE_CLOSE
    # itself still emits.
    si_lines = [r for r in cap.records if "TIME_DECAY_STRUCT_INVALIDATED" in r]
    assert not si_lines, "STRUCT_INVALIDATED should NOT fire when structural_required=False"
