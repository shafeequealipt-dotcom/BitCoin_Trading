"""Phase 6 — XRAY_NONE_REASON evidence enrichment + BoS retest relaxation.

Two related fixes:

1. ``diagnose_none()`` now returns 13 enriched evidence fields beyond
   the original 8 — operators can read in/counter zone state, BoS
   detail, sweep + range presence, ATR, and the FVG/OB window percents
   from a single XRAY_NONE_REASON log line.

2. BoS branches (BULLISH/BEARISH_STRUCTURAL_BREAK) now fire on minor
   BoS when ``structural_break_require_retest=False`` (Phase 6 default
   change). Confidence is reduced by
   ``structural_break_minor_confidence_multiplier`` (default 0.8) so
   minor BoS doesn't out-rank major BoS.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    MarketStructureResult,
    OrderBlock,
    SetupType,
    StructuralAnalysis,
    StructureEvent,
)
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import SetupTypesSettings, StructureSettings


def _make_engine(**overrides) -> StructureEngine:
    settings = StructureSettings(
        setup_types=SetupTypesSettings(**overrides),
    )
    return StructureEngine(settings)


# ──────────────────────────────────────────────────────────────────────────
# Phase 6.1 — Enriched NONE_REASON evidence fields
# ──────────────────────────────────────────────────────────────────────────


class TestEnrichedNoneReasonFields:
    def test_diagnose_none_returns_phase6_fields(self) -> None:
        """All 13 enriched fields must appear in the dict."""
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult(structure="ranging")
        a.position_in_range = 0.5
        a.atr_pct_h1 = 0.7
        d = eng.diagnose_none(a)
        # Phase 6 enriched fields:
        assert "in_direction_fvg" in d
        assert "in_direction_ob" in d
        assert "counter_direction_fvg" in d
        assert "counter_direction_ob" in d
        assert "last_bos_significance" in d
        assert "last_bos_age_bars" in d
        assert "recent_sweep" in d
        assert "range_compression" in d
        assert "atr_pct_h1" in d
        assert "window_pct_fvg" in d
        assert "window_pct_ob" in d
        assert "first_failure_branch" in d
        # Existing 8 fields preserved:
        for k in (
            "closest_type", "missed_by", "weakest_input", "mtf_score_01",
            "smc_01", "direction", "structure", "has_fvg",
        ):
            assert k in d, f"Phase 6 broke pre-existing field: {k}"

    def test_in_direction_fvg_states(self) -> None:
        eng = _make_engine()
        # missing
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        d = eng.diagnose_none(a)
        assert d["in_direction_fvg"] == "missing"
        # filled
        a.nearest_fvg = FairValueGap(direction="bullish", filled=True)
        d = eng.diagnose_none(a)
        assert d["in_direction_fvg"] == "filled"
        # available
        a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
        d = eng.diagnose_none(a)
        assert d["in_direction_fvg"] == "available"

    def test_counter_direction_fvg_states(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        a.nearest_fvg_counter = FairValueGap(direction="bearish", filled=False)
        d = eng.diagnose_none(a)
        assert d["counter_direction_fvg"] == "available"

    def test_in_direction_ob_states(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        a.nearest_ob = OrderBlock(direction="bullish", fresh=False)
        d = eng.diagnose_none(a)
        assert d["in_direction_ob"] == "stale"

    def test_atr_and_window_fields_match_config(self) -> None:
        eng = _make_engine(
            fvg_atr_multiplier=3.0,
            ob_atr_multiplier=4.0,
            fvg_min_distance_pct=2.0,
            ob_min_distance_pct=3.0,
        )
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        a.atr_pct_h1 = 1.0
        d = eng.diagnose_none(a)
        # atr=1.0 → fvg_window = max(2.0, 3*1.0) = 3.0 ; ob_window = max(3.0, 4*1.0) = 4.0
        assert d["atr_pct_h1"] == 1.0
        assert d["window_pct_fvg"] == 3.0
        assert d["window_pct_ob"] == 4.0

    def test_range_compression_detected(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        a.position_in_range = 0.96
        d = eng.diagnose_none(a)
        assert d["range_compression"] is True
        a.position_in_range = 0.5
        d = eng.diagnose_none(a)
        assert d["range_compression"] is False
        a.position_in_range = 0.04
        d = eng.diagnose_none(a)
        assert d["range_compression"] is True

    def test_last_bos_significance_captured(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult(
            structure="uptrend",
            last_bos=StructureEvent(event_type="bos", direction="bullish", significance="minor"),
        )
        d = eng.diagnose_none(a)
        assert d["last_bos_significance"] == "minor"

    def test_no_bos_returns_none_marker(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()  # no last_bos
        d = eng.diagnose_none(a)
        assert d["last_bos_significance"] == "none"


# ──────────────────────────────────────────────────────────────────────────
# Phase 6.2 — BoS retest relaxation + minor confidence multiplier
# ──────────────────────────────────────────────────────────────────────────


def _bos_long_analysis(
    significance: str, *, mtf_score: int = 6,
) -> StructuralAnalysis:
    """Build a minimal long-side BoS analysis."""
    a = StructuralAnalysis(
        symbol="XRPUSDT",
        suggested_direction="long",
        smc_confluence=50,
        position_in_range=0.4,
    )
    a.market_structure = MarketStructureResult(
        structure="uptrend",
        last_bos=StructureEvent(event_type="bos", direction="bullish", significance=significance),
    )
    a.mtf_confluence = MagicMock(score=mtf_score)
    return a


class TestBosRetestRelaxation:
    def test_minor_bos_rejected_when_retest_required(self) -> None:
        """Pre-Phase-6 default: require_retest=True rejects minor BoS."""
        eng = _make_engine(structural_break_require_retest=True)
        a = _bos_long_analysis("minor")
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_minor_bos_accepted_when_retest_off(self) -> None:
        """Phase 6 default: minor BoS qualifies."""
        eng = _make_engine(structural_break_require_retest=False)
        a = _bos_long_analysis("minor")
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_STRUCTURAL_BREAK

    def test_major_bos_unaffected_by_relaxation(self) -> None:
        # Major BoS confidence should be unchanged with retest=False.
        eng_strict = _make_engine(structural_break_require_retest=True)
        eng_relaxed = _make_engine(structural_break_require_retest=False)
        a1 = _bos_long_analysis("major")
        a2 = _bos_long_analysis("major")
        _, conf_strict = eng_strict.classify_setup(a1)
        _, conf_relaxed = eng_relaxed.classify_setup(a2)
        assert conf_strict == conf_relaxed

    def test_minor_bos_confidence_reduced_by_multiplier(self) -> None:
        """Confidence on minor BoS should be ~0.8 × major BoS confidence."""
        eng = _make_engine(
            structural_break_require_retest=False,
            structural_break_minor_confidence_multiplier=0.8,
        )
        a_major = _bos_long_analysis("major")
        a_minor = _bos_long_analysis("minor")
        _, conf_major = eng.classify_setup(a_major)
        _, conf_minor = eng.classify_setup(a_minor)
        # Major: max(0.6, 0.5, 0.5) = 0.6 → round to 0.6
        # Minor: 0.6 × 0.8 = 0.48 → round to 0.48
        assert conf_minor < conf_major
        assert conf_minor == pytest.approx(conf_major * 0.8, abs=0.01)

    def test_minor_bos_with_custom_multiplier(self) -> None:
        eng = _make_engine(
            structural_break_require_retest=False,
            structural_break_minor_confidence_multiplier=0.5,
        )
        a_major = _bos_long_analysis("major")
        a_minor = _bos_long_analysis("minor")
        _, conf_major = eng.classify_setup(a_major)
        _, conf_minor = eng.classify_setup(a_minor)
        assert conf_minor == pytest.approx(conf_major * 0.5, abs=0.01)

    def test_minor_multiplier_validation(self) -> None:
        with pytest.raises(ValueError, match="structural_break_minor_confidence_multiplier"):
            SetupTypesSettings(structural_break_minor_confidence_multiplier=0.0)
        with pytest.raises(ValueError, match="structural_break_minor_confidence_multiplier"):
            SetupTypesSettings(structural_break_minor_confidence_multiplier=1.1)

    def test_bear_minor_bos_mirror(self) -> None:
        eng = _make_engine(structural_break_require_retest=False)
        a = StructuralAnalysis(
            symbol="X",
            suggested_direction="short",
            position_in_range=0.6,
        )
        a.market_structure = MarketStructureResult(
            structure="downtrend",
            last_bos=StructureEvent(event_type="bos", direction="bearish", significance="minor"),
        )
        a.mtf_confluence = MagicMock(score=6)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BEARISH_STRUCTURAL_BREAK
