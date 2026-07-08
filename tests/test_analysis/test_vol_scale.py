"""Unit tests for src/analysis/vol_scale.py.

Pure functions; no IO, no async. Covers every edge case callers rely on:
  * None / unknown class → default (medium)
  * Zero / negative ATR → legacy fallback
  * Absolute floor enforcement
  * Per-class ceiling clamp
  * scale_by_class: defaults + missing-class fallthrough
"""

from dataclasses import dataclass, field

import pytest

from src.analysis.vol_scale import (
    CLASS_ORDER,
    min_distance_for_class,
    scale_by_class,
)


@dataclass
class FakeSLCfg:
    """Minimal shape mirroring SLGatewaySettings for these tests."""
    min_distance_pct: float = 0.3
    min_distance_atr_multiplier: float = 0.5
    min_distance_abs_floor_pct: float = 0.05
    min_distance_class_ceiling: dict = field(default_factory=lambda: {
        "dead": 0.30, "low": 0.50, "medium": 1.00,
        "high": 2.00, "extreme": 3.50,
    })


# ─────────────────────────────────────────────────────────────────────────
# scale_by_class
# ─────────────────────────────────────────────────────────────────────────

class TestScaleByClass:
    def test_basic_lookup(self):
        factors = {"dead": 1.2, "low": 1.3, "medium": 1.3, "high": 1.4, "extreme": 1.5}
        assert scale_by_class(2.0, "dead", factors) == pytest.approx(2.4)
        assert scale_by_class(2.0, "extreme", factors) == pytest.approx(3.0)

    def test_none_class_uses_medium(self):
        factors = {"dead": 1.0, "medium": 2.0, "extreme": 3.0}
        assert scale_by_class(5.0, None, factors) == pytest.approx(10.0)

    def test_unknown_class_uses_medium(self):
        factors = {"dead": 1.0, "medium": 2.0, "extreme": 3.0}
        assert scale_by_class(5.0, "futuristic_new_class", factors) == pytest.approx(10.0)

    def test_medium_missing_returns_unscaled(self):
        # Pathological factors dict without 'medium' — should fall through to 1.0
        factors = {"dead": 0.5}
        assert scale_by_class(4.0, None, factors) == pytest.approx(4.0)

    def test_explicit_default_class(self):
        factors = {"low": 0.7, "high": 1.5}
        # vol_class=None with default_class='low' should use low
        assert scale_by_class(10.0, None, factors, default_class="low") == pytest.approx(7.0)


# ─────────────────────────────────────────────────────────────────────────
# min_distance_for_class
# ─────────────────────────────────────────────────────────────────────────

class TestMinDistanceForClass:
    def test_negative_atr_falls_back_to_global(self):
        cfg = FakeSLCfg(min_distance_pct=0.3)
        assert min_distance_for_class(-0.5, "medium", cfg) == pytest.approx(0.3)

    def test_zero_atr_falls_back_to_global(self):
        cfg = FakeSLCfg(min_distance_pct=0.3)
        assert min_distance_for_class(0.0, "dead", cfg) == pytest.approx(0.3)

    def test_normal_scaling_medium(self):
        # ATR=0.6 %, mult=0.5 → 0.30; no floor/ceiling bite → 0.30
        cfg = FakeSLCfg()
        got = min_distance_for_class(0.6, "medium", cfg)
        assert got == pytest.approx(0.30)

    def test_abs_floor_bites_on_dead_coin(self):
        # ATR=0.04 %, mult=0.5 → 0.02 < 0.05 floor → 0.05
        cfg = FakeSLCfg()
        got = min_distance_for_class(0.04, "dead", cfg)
        assert got == pytest.approx(0.05)

    def test_class_ceiling_clamps_freak_spike(self):
        # ATR=10 % (flash crash), dead ceiling=0.30 → clamp 0.30
        cfg = FakeSLCfg()
        got = min_distance_for_class(10.0, "dead", cfg)
        assert got == pytest.approx(0.30)

    def test_class_ceiling_clamps_extreme(self):
        # ATR=20 %, extreme ceiling=3.5 → clamp 3.5
        cfg = FakeSLCfg()
        got = min_distance_for_class(20.0, "extreme", cfg)
        assert got == pytest.approx(3.5)

    def test_unknown_class_uses_medium_ceiling(self):
        # ATR=10 %, unknown class → medium ceiling=1.0 → 1.0
        cfg = FakeSLCfg()
        got = min_distance_for_class(10.0, "cosmic", cfg)
        assert got == pytest.approx(1.0)

    def test_no_ceiling_map_uses_5pct_emergency(self):
        cfg = FakeSLCfg(min_distance_class_ceiling={})
        # ATR=20 %, mult=0.5 → 10.0; no ceiling map → 5.0 emergency cap
        got = min_distance_for_class(20.0, "extreme", cfg)
        assert got == pytest.approx(5.0)

    def test_aggressive_user_spec_on_low_vol(self):
        # User's stated "exploit market" formula: atr=0.15, mult=0.5 → 0.075,
        # floor=0.05 → 0.075 wins; low ceiling=0.50 → 0.075 passes.
        cfg = FakeSLCfg()
        got = min_distance_for_class(0.15, "low", cfg)
        assert got == pytest.approx(0.075)

    def test_high_vol_natural_range(self):
        # ATR=2.0 %, high ceiling=2.0. atr*0.5=1.0 < 2.0 → 1.0 (no clamp).
        cfg = FakeSLCfg()
        got = min_distance_for_class(2.0, "high", cfg)
        assert got == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────
# CLASS_ORDER sanity
# ─────────────────────────────────────────────────────────────────────────

def test_class_order_matches_volatility_profile_convention():
    # This ordering is relied on by any caller that wants to emit ordered
    # summary lines. Locking it down catches accidental reorderings.
    assert CLASS_ORDER == ("dead", "low", "medium", "high", "extreme")
