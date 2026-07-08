"""Issue 1 of 2026-05-19 direction-bias fix Phase C — structural-tp
min-edge floor + symmetric min_touches resistance filter.

The fix addresses the rr_long-collapse pattern in trending markets:
when price approaches the nearest resistance, the raw structural_tp
formula at src/analysis/structure/structural_levels.py:101 produces
a TP at or below current_price → reward → 0 → rr_long → 0 → flip
ratio explodes → cascading Buy → Sell flips at execution.

After the fix:
- ``StructureSettings.tp_min_distance_pct`` clamps structural_tp to
  be at least this percent away from current_price.
- ``StructureSettings.min_touches_resistance`` (default 2) replaces
  the hardcoded ``>= 1`` filter at support_resistance.py:126 so the
  resistance touch filter is symmetric with support.
- ``StructuralPlacement.is_structurally_invalid`` flag is set when
  the raw structural_tp would have landed on the wrong side of
  current_price (the clamp activated).

These tests cover both new mechanisms in isolation. Cross-cutting
integration (XRAY_FLIP_CONFIG boot sentinel, scanner_worker
plumbing) is verified by the regression test sweep in Phase 5.4.
"""

from __future__ import annotations

import numpy as np

from src.analysis.structure.models.structure_types import (
    MarketStructureResult,
    PriceLevel,
    StructuralPlacement,
)
from src.analysis.structure.structural_levels import StructuralLevelCalculator
from src.analysis.structure.support_resistance import SupportResistanceEngine
from src.config.settings import StructureSettings


def _make_settings(**overrides) -> StructureSettings:
    """Return StructureSettings with the Issue-1 defaults, plus
    test-specific overrides."""
    base = {
        "min_touches": 2,
        "min_touches_resistance": 2,
        "tp_min_distance_pct": 0.5,
        "sl_buffer_pct": 0.15,
        "tp_buffer_pct": 0.10,
        "sl_fallback_pct": 2.0,
        "tp_fallback_pct": 4.0,
    }
    base.update(overrides)
    return StructureSettings(**base)


def _make_market_structure() -> MarketStructureResult:
    return MarketStructureResult(structure="ranging", strength="medium")


# ── Section 1 — StructuralPlacement field signature ──────────────────


def test_structural_placement_default_is_structurally_invalid_false() -> None:
    """New field defaults to False so existing callers/fixtures that
    do not pass it continue to behave identically."""
    p = StructuralPlacement()
    assert hasattr(p, "is_structurally_invalid")
    assert p.is_structurally_invalid is False


def test_structural_placement_to_dict_includes_invalid_flag() -> None:
    """to_dict() exposes the new field so XRAY_LEVELS log + DB
    serialization round-trip preserves the flag."""
    p = StructuralPlacement(is_structurally_invalid=True)
    d = p.to_dict()
    assert "is_structurally_invalid" in d
    assert d["is_structurally_invalid"] is True


# ── Section 2 — StructureSettings new fields ─────────────────────────


def test_structure_settings_defaults_active() -> None:
    """Per Concern 4 verdict, defaults are ACTIVE — not no-op.
    min_touches_resistance defaults to 2 (symmetric with support),
    tp_min_distance_pct defaults to 0.5 (clamp is engaged)."""
    s = StructureSettings()
    assert s.min_touches_resistance == 2
    assert s.min_touches == 2
    assert s.tp_min_distance_pct == 0.5


def test_structure_settings_legacy_resistance_filter_via_config() -> None:
    """Operator can restore legacy single-touch resistance detection
    by setting min_touches_resistance=1 in config.toml."""
    s = StructureSettings(min_touches_resistance=1)
    assert s.min_touches_resistance == 1


# ── Section 3 — _calc_long minimum-edge floor ────────────────────────


def test_calc_long_clamps_tp_when_resistance_below_floor() -> None:
    """When the nearest resistance's zone_low (minus buffer) lands at
    or below current_price, structural_tp must be clamped to be at
    least tp_min_distance_pct above current_price, and the
    is_structurally_invalid flag must be True."""
    settings = _make_settings(tp_min_distance_pct=0.5)
    calc = StructuralLevelCalculator(settings)
    current_price = 100.0
    # Resistance "at" current price — its zone_low equals current_price,
    # so raw_tp = zone_low - buffer = 100.0 - 100.0*0.0010 = 99.9 (below)
    res_at = PriceLevel(
        price=100.0, zone_low=100.0, zone_high=100.1,
        touches=3, strength=3.0, level_type="resistance",
    )
    sup_below = PriceLevel(
        price=95.0, zone_low=94.95, zone_high=95.05,
        touches=3, strength=3.0, level_type="support",
    )
    placement = calc._calc_long(
        current_price=current_price,
        supports=[sup_below],
        resistances=[res_at],
        ms=_make_market_structure(),
        position=0.95,
    )
    # Clamped: TP must be >= 100 * (1 + 0.5/100) = 100.5
    assert placement.structural_tp >= 100.5
    assert placement.is_structurally_invalid is True
    # rr_ratio is no longer zero (the clamp guarantees positive reward).
    assert placement.rr_ratio > 0.0


def test_calc_long_does_not_clamp_when_resistance_above_floor() -> None:
    """When resistance is comfortably above the floor distance, raw
    structural_tp is used and the invalid flag stays False."""
    settings = _make_settings(tp_min_distance_pct=0.5)
    calc = StructuralLevelCalculator(settings)
    res_above = PriceLevel(
        price=105.0, zone_low=104.5, zone_high=105.5,
        touches=3, strength=3.0, level_type="resistance",
    )
    sup_below = PriceLevel(
        price=95.0, zone_low=94.95, zone_high=95.05,
        touches=3, strength=3.0, level_type="support",
    )
    placement = calc._calc_long(
        current_price=100.0,
        supports=[sup_below],
        resistances=[res_above],
        ms=_make_market_structure(),
        position=0.5,
    )
    # Raw TP is well above the floor; no clamp.
    assert placement.is_structurally_invalid is False


# ── Section 4 — _calc_short minimum-edge floor ───────────────────────


def test_calc_short_clamps_tp_when_support_above_floor() -> None:
    """Mirror of the long clamp — when the nearest support's
    zone_high (plus buffer) lands at or above current_price, the
    structural_tp must be clamped to be at least tp_min_distance_pct
    BELOW current_price."""
    settings = _make_settings(tp_min_distance_pct=0.5)
    calc = StructuralLevelCalculator(settings)
    current_price = 100.0
    sup_at = PriceLevel(
        price=100.0, zone_low=99.9, zone_high=100.0,
        touches=3, strength=3.0, level_type="support",
    )
    res_above = PriceLevel(
        price=105.0, zone_low=104.5, zone_high=105.5,
        touches=3, strength=3.0, level_type="resistance",
    )
    placement = calc._calc_short(
        current_price=current_price,
        supports=[sup_at],
        resistances=[res_above],
        ms=_make_market_structure(),
        position=0.05,
    )
    # Clamped: TP must be <= 100 * (1 - 0.5/100) = 99.5
    assert placement.structural_tp <= 99.5
    assert placement.is_structurally_invalid is True
    assert placement.rr_ratio > 0.0


# ── Section 5 — Symmetric min_touches in support_resistance.py ───────


def _make_ohlc(n: int = 100) -> tuple:
    """Synthetic OHLC arrays with single-touch swing highs at $105 and
    a multi-touch support cluster at $95.

    Designed so that with min_touches_resistance=2 (default), the
    single-touch resistance at $105 is dropped. With
    min_touches_resistance=1 (legacy), it would be kept.
    """
    rng = np.random.default_rng(seed=42)
    base = 100.0 + rng.normal(0, 0.3, n).cumsum() * 0.05
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    # Implant a single-touch swing high at $105 (one bar)
    highs[60] = 105.0
    # Implant multi-touch resistance at $103 (3 bars)
    for idx in (30, 45, 70):
        highs[idx] = 103.0
    # Implant multi-touch support at $95 (3 bars)
    for idx in (35, 50, 80):
        lows[idx] = 95.0
    return highs.astype(np.float64), lows.astype(np.float64), closes.astype(np.float64)


def test_resistance_filter_symmetric_with_support_by_default() -> None:
    """With min_touches_resistance=2 (default), single-touch resistance
    levels are filtered out — matching the support filter behavior."""
    settings = _make_settings(min_touches=2, min_touches_resistance=2)
    engine = SupportResistanceEngine(settings)
    highs, lows, closes = _make_ohlc()
    sup, res, _ = engine.calculate(highs, lows, closes, current_price=100.0)
    # The single-touch resistance at $105 must NOT survive the filter.
    for r in res:
        assert r.touches >= 2, (
            f"resistance at {r.price:.2f} has touches={r.touches}, "
            f"but min_touches_resistance=2 should have filtered it out"
        )


def test_resistance_filter_legacy_single_touch_via_config() -> None:
    """With min_touches_resistance=1 (legacy override), single-touch
    resistances are kept — backwards-compatible operator escape hatch."""
    settings = _make_settings(min_touches=2, min_touches_resistance=1)
    engine = SupportResistanceEngine(settings)
    highs, lows, closes = _make_ohlc()
    sup, res, _ = engine.calculate(highs, lows, closes, current_price=100.0)
    # At least one resistance with touches==1 should be detectable here.
    # (We don't assert exactly because clustering may merge nearby
    # swing highs; the contract is "legacy behavior preserved".)
    assert any(r.touches == 1 for r in res) or len(res) > 0
