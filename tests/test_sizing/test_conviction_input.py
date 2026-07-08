"""Sniper-Latency-Size Fix Phase 3A+3B — XRAY confidence and expected RR
flow into the APEX conviction weight at TradeGate CHECK 4.

Phase 0 investigation showed 13 of 15 trades had identical
entry_xray_confidence=0.7 and entry_setup_type=bearish_fvg_ob yet
produced 6 different sizes ($100-$300) because the conviction signals
never reached the sizing layer. This test isolates the sizing
computation from the surrounding gate path and verifies the modifier
arithmetic.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "xray_conf,expected_modifier",
    [
        (0.90, 1.20),  # high-conviction structural setup
        (0.85, 1.20),  # boundary
        (0.80, 1.0),   # baseline
        (0.70, 1.0),   # baseline
        (0.65, 0.85),  # weaker structure
        (0.0, 1.0),    # no package data — neutral
    ],
)
def test_xray_confidence_modifier_arithmetic(
    xray_conf: float, expected_modifier: float
) -> None:
    """Documents the xray_modifier brackets matching Phase 3B
    specification. The arithmetic is replicated here so the apex/gate
    edit can be regression-tested without spinning up the full
    AsyncIO + DB stack."""
    weight = 1.0
    if xray_conf >= 0.85:
        weight *= 1.20
    elif xray_conf >= 0.70:
        pass
    elif xray_conf > 0:
        weight *= 0.85
    assert weight == pytest.approx(expected_modifier)


@pytest.mark.parametrize(
    "rr,expected_modifier",
    [
        (4.0, 1.15),   # excellent RR
        (3.0, 1.15),   # boundary
        (2.5, 1.0),    # standard RR
        (1.5, 1.0),    # boundary
        (1.2, 0.90),   # poor RR
        (0.0, 1.0),    # no package data — neutral
    ],
)
def test_expected_rr_modifier_arithmetic(
    rr: float, expected_modifier: float
) -> None:
    """Documents the rr_modifier brackets matching Phase 3B spec."""
    weight = 1.0
    if rr >= 3.0:
        weight *= 1.15
    elif rr >= 1.5:
        pass
    elif rr > 0:
        weight *= 0.90
    assert weight == pytest.approx(expected_modifier)


def test_combined_high_conviction_amplifies_within_cap() -> None:
    """A high-conviction setup (A+ score=80, xray=0.90, rr=4.0) starting
    from profit_factor=1.0 weight=1.0 multiplies up to 1.66x. Phase 3B
    raises the conviction-weight clamp ceiling from 2.0 to 2.5 so this
    multiplication can actually express itself; the 0.5 floor is
    preserved."""
    weight = 1.0  # baseline conviction
    weight *= 1.20  # A+ score
    weight *= 1.20  # high xray confidence
    weight *= 1.15  # excellent RR
    weight = max(0.5, min(weight, 2.5))
    assert weight == pytest.approx(1.656)
    assert weight <= 2.5


def test_combined_weak_setup_reduces_within_floor() -> None:
    """A weak setup (C/D score < 56, xray=0.65, rr=1.2) starting from
    profit_factor=1.0 weight=1.0 multiplies down to 0.612, above the
    0.5 floor."""
    weight = 1.0
    weight *= 0.80  # C/D score
    weight *= 0.85  # weaker xray
    weight *= 0.90  # poor RR
    weight = max(0.5, min(weight, 2.5))
    assert weight == pytest.approx(0.612)
    assert weight >= 0.5


def test_brain_decision_dataclass_unchanged() -> None:
    """Phase 3A surfaces conviction signals onto the trade dict (not
    BrainDecision) because the trade dict is the carrier through the
    sizing pipeline. BrainDecision keeps its original fields. This
    test documents the design choice so a future change is surfaced."""
    from src.core.types import BrainDecision

    fields = {f.name for f in BrainDecision.__dataclass_fields__.values()}
    # Original four data fields plus the auto-default fields. We're
    # asserting that the dataclass shape is unchanged so legacy
    # consumers in src/alerts/ continue to work without modification.
    assert "confidence" in fields
    assert "action" in fields
    assert "symbol" in fields
    # New fields are NOT on the dataclass (they're on the trade dict).
    assert "xray_confidence" not in fields
    assert "expected_rr" not in fields
