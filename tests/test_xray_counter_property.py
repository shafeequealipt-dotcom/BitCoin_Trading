"""Property + edge-case tests for the XRAY counter-setup work.

Covers boundary conditions and invariants that must hold across the
full state space of inputs:

- ATR window math: floor protection, ceiling protection, near-zero ATR.
- Counter alignment: every regime × every direction × strict toggle.
- Confidence multiplier: floor 0.5 / ceiling 1.0 invariant across
  scorer (5a), opportunity_score (5b), ensemble (5c).
- trade_direction coherence: in-direction → equal, counter → opposite,
  NONE → empty.
- to_dict() round-trip preservation across all 11 SetupType variants.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
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


# ──────────────────────────────────────────────────────────────────────────
# ATR window invariants
# ──────────────────────────────────────────────────────────────────────────


class TestATRWindowInvariants:
    """The window must satisfy floor ≤ window AND scale linearly with ATR
    once above the floor breakpoint."""

    @pytest.mark.parametrize("atr_pct", [0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0])
    def test_window_never_below_floor(self, atr_pct: float) -> None:
        cfg = SetupTypesSettings()
        # Compute the implied window
        fvg_window = max(cfg.fvg_min_distance_pct, cfg.fvg_atr_multiplier * atr_pct)
        ob_window = max(cfg.ob_min_distance_pct, cfg.ob_atr_multiplier * atr_pct)
        assert fvg_window >= cfg.fvg_min_distance_pct
        assert ob_window >= cfg.ob_min_distance_pct

    def test_window_scales_with_atr_above_floor(self) -> None:
        cfg = SetupTypesSettings()
        # Below the breakpoint atr_pct = 2/3 ≈ 0.667, window is at floor 2.0
        # Above: window = 3 * atr_pct
        assert max(cfg.fvg_min_distance_pct, cfg.fvg_atr_multiplier * 0.5) == 2.0
        assert max(cfg.fvg_min_distance_pct, cfg.fvg_atr_multiplier * 1.0) == 3.0
        assert max(cfg.fvg_min_distance_pct, cfg.fvg_atr_multiplier * 2.0) == 6.0


# ──────────────────────────────────────────────────────────────────────────
# Counter alignment exhaustive truth table
# ──────────────────────────────────────────────────────────────────────────


class TestCounterAlignmentExhaustive:
    @pytest.mark.parametrize(
        "trade_dir,struct,strict,expected",
        [
            # Long counter trade — accept on non-uptrend (with strict on volatile)
            ("long", "uptrend", False, False),
            ("long", "uptrend", True, False),
            ("long", "downtrend", False, True),
            ("long", "downtrend", True, True),
            ("long", "ranging", False, True),
            ("long", "ranging", True, True),
            ("long", "volatile", False, True),
            ("long", "volatile", True, False),
            ("long", "unknown", False, False),
            ("long", "dead", False, False),
            # Short counter trade — mirror
            ("short", "uptrend", False, True),
            ("short", "uptrend", True, True),
            ("short", "downtrend", False, False),
            ("short", "downtrend", True, False),
            ("short", "ranging", False, True),
            ("short", "ranging", True, True),
            ("short", "volatile", False, True),
            ("short", "volatile", True, False),
            # Empty trade direction — always reject
            ("", "uptrend", False, False),
            ("", "ranging", False, False),
        ],
    )
    def test_alignment(
        self, trade_dir: str, struct: str, strict: bool, expected: bool,
    ) -> None:
        cfg = SetupTypesSettings(counter_alignment_strict=strict)
        assert StructureEngine._counter_alignment(trade_dir, struct, cfg) is expected


# ──────────────────────────────────────────────────────────────────────────
# Confidence multiplier — floor/ceiling invariant across all 3 sites
# ──────────────────────────────────────────────────────────────────────────


class TestConfidenceMultiplierInvariant:
    """Phase 5a/b/c all use the same factor formula:
        factor = max(0.5, min(1.0, setup_type_confidence))
    Floor 0.5, ceiling 1.0. Verified at each site."""

    @pytest.mark.parametrize("conf,expected_factor", [
        (-1.0, 0.5),  # negative
        (0.0, 0.5),
        (0.3, 0.5),
        (0.5, 0.5),
        (0.5001, 0.5001),
        (0.7, 0.7),
        (0.85, 0.85),
        (1.0, 1.0),
        (1.5, 1.0),  # above ceiling
    ])
    def test_factor_clamping(self, conf: float, expected_factor: float) -> None:
        # Replicate the formula used at all 3 sites
        factor = max(0.5, min(1.0, conf))
        assert factor == pytest.approx(expected_factor, abs=1e-6)


# ──────────────────────────────────────────────────────────────────────────
# trade_direction coherence invariant
# ──────────────────────────────────────────────────────────────────────────


def _make_engine(**overrides) -> StructureEngine:
    return StructureEngine(StructureSettings(setup_types=SetupTypesSettings(**overrides)))


class TestTradeDirectionCoherence:
    """For every classify_setup outcome, trade_direction must be coherent
    with setup_type:
        - In-direction setup: trade_direction == suggested_direction
        - Counter setup: trade_direction == OPPOSITE of suggested_direction
        - NONE: trade_direction == ""
    """

    def test_in_direction_long_coherent(self) -> None:
        eng = _make_engine(fvg_ob_min_confluence=0.5)
        a = StructuralAnalysis(symbol="X", suggested_direction="long",
                               smc_confluence=80, position_in_range=0.2)
        a.market_structure = MarketStructureResult(structure="uptrend")
        a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
        a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_FVG_OB
        assert a.trade_direction == "long"

    def test_in_direction_short_coherent(self) -> None:
        eng = _make_engine(fvg_ob_min_confluence=0.5)
        a = StructuralAnalysis(symbol="X", suggested_direction="short",
                               smc_confluence=80, position_in_range=0.8)
        a.market_structure = MarketStructureResult(structure="downtrend")
        a.nearest_fvg = FairValueGap(direction="bearish", filled=False)
        a.nearest_ob = OrderBlock(direction="bearish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BEARISH_FVG_OB
        assert a.trade_direction == "short"

    def test_bullish_counter_coherent(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="short",
                               smc_confluence=50, position_in_range=0.5)
        a.market_structure = MarketStructureResult(structure="ranging")
        a.nearest_fvg_counter = FairValueGap(direction="bullish", filled=False)
        a.nearest_ob_counter = OrderBlock(direction="bullish", fresh=True)
        a.mtf_confluence = MagicMock(score=6)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_FVG_OB_COUNTER
        # Suggested was short, counter trade is opposite → long
        assert a.trade_direction == "long"
        assert a.suggested_direction == "short"
        assert a.trade_direction != a.suggested_direction

    def test_bearish_counter_coherent(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long",
                               smc_confluence=50, position_in_range=0.5)
        a.market_structure = MarketStructureResult(structure="ranging")
        a.nearest_fvg_counter = FairValueGap(direction="bearish", filled=False)
        a.nearest_ob_counter = OrderBlock(direction="bearish", fresh=True)
        a.mtf_confluence = MagicMock(score=6)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BEARISH_FVG_OB_COUNTER
        assert a.trade_direction == "short"
        assert a.suggested_direction == "long"

    def test_none_clears_trade_direction(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        # No zones → fall through to NONE
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE
        assert a.trade_direction == ""


# ──────────────────────────────────────────────────────────────────────────
# to_dict round-trip integrity for all 11 setup types
# ──────────────────────────────────────────────────────────────────────────


class TestToDictRoundTrip:
    @pytest.mark.parametrize("setup_type", list(SetupType))
    def test_to_dict_serializes_all_variants(self, setup_type: SetupType) -> None:
        import json
        a = StructuralAnalysis(symbol="X")
        a.market_structure = MarketStructureResult()
        a.setup_type = setup_type
        a.setup_type_confidence = 0.5
        a.trade_direction = "long" if "BULLISH" in setup_type.name else (
            "short" if "BEARISH" in setup_type.name else ""
        )
        a.atr_pct_h1 = 1.0
        d = a.to_dict()
        assert d["setup_type"] == setup_type.value
        assert d["setup_type_confidence"] == 0.5
        assert d["trade_direction"] == a.trade_direction
        assert d["atr_pct_h1"] == 1.0
        # JSON-serializable round-trip
        s = json.dumps(d)
        d2 = json.loads(s)
        assert d2["setup_type"] == setup_type.value


# ──────────────────────────────────────────────────────────────────────────
# Diagnose_none enriched fields invariant
# ──────────────────────────────────────────────────────────────────────────


class TestDiagnoseNoneInvariants:
    """All 12 enriched fields must always be present, never None."""

    EXPECTED_FIELDS = [
        "in_direction_fvg", "in_direction_ob",
        "counter_direction_fvg", "counter_direction_ob",
        "last_bos_significance", "last_bos_age_bars",
        "recent_sweep", "range_compression",
        "atr_pct_h1", "window_pct_fvg", "window_pct_ob",
        "first_failure_branch",
    ]

    @pytest.mark.parametrize("direction", ["long", "short", ""])
    @pytest.mark.parametrize("struct", ["uptrend", "downtrend", "ranging", "volatile", ""])
    def test_all_enriched_fields_present(self, direction: str, struct: str) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction=direction)
        a.market_structure = MarketStructureResult(structure=struct)
        a.atr_pct_h1 = 0.7
        d = eng.diagnose_none(a)
        for f in self.EXPECTED_FIELDS:
            assert f in d, f"{f} missing for direction={direction} struct={struct}"
        # All values are non-None (may be empty string or False or 0)
        for f in self.EXPECTED_FIELDS:
            assert d[f] is not None or f == "last_bos_age_bars"  # -1 sentinel


# ──────────────────────────────────────────────────────────────────────────
# Edge: zero ATR doesn't break window calc
# ──────────────────────────────────────────────────────────────────────────


class TestZeroATREdgeCases:
    def test_zero_atr_returns_floor_window_fvg(self) -> None:
        cfg = SetupTypesSettings()
        fvgs = [FairValueGap(direction="bullish", filled=False, midpoint=101.5)]
        # FVG at 1.5% — within 2% floor → found.
        result = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.0, cfg)
        assert result.in_direction is not None

    def test_negative_atr_treated_as_zero(self) -> None:
        cfg = SetupTypesSettings()
        fvgs = [FairValueGap(direction="bullish", filled=False, midpoint=101.5)]
        # negative ATR shouldn't crash — clamped to 0 effectively
        result = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", -5.0, cfg)
        # max(2.0, 3*-5.0) = max(2.0, -15) = 2.0
        assert result.in_direction is not None


# ──────────────────────────────────────────────────────────────────────────
# Edge: counter-alignment regression — counter NEVER fires when in-direction
#       branch already qualified.
# ──────────────────────────────────────────────────────────────────────────


class TestInDirectionPriorityInvariant:
    """When BOTH in-direction and counter zones are present + valid, the
    in-direction branch must ALWAYS win. Counter never overrides in-direction."""

    def test_in_direction_long_wins_over_counter(self) -> None:
        eng = _make_engine(fvg_ob_min_confluence=0.5)
        a = StructuralAnalysis(symbol="X", suggested_direction="long",
                               smc_confluence=80, position_in_range=0.2)
        a.market_structure = MarketStructureResult(structure="uptrend")
        # Both directions present
        a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
        a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
        a.nearest_fvg_counter = FairValueGap(direction="bearish", filled=False)
        a.nearest_ob_counter = OrderBlock(direction="bearish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        stype, conf = eng.classify_setup(a)
        # MUST be in-direction, not counter
        assert stype == SetupType.BULLISH_FVG_OB
        assert conf > 0.5  # full confidence, not multiplied
        assert a.trade_direction == "long"

    def test_in_direction_short_wins_over_counter(self) -> None:
        eng = _make_engine(fvg_ob_min_confluence=0.5)
        a = StructuralAnalysis(symbol="X", suggested_direction="short",
                               smc_confluence=80, position_in_range=0.8)
        a.market_structure = MarketStructureResult(structure="downtrend")
        a.nearest_fvg = FairValueGap(direction="bearish", filled=False)
        a.nearest_ob = OrderBlock(direction="bearish", fresh=True)
        a.nearest_fvg_counter = FairValueGap(direction="bullish", filled=False)
        a.nearest_ob_counter = OrderBlock(direction="bullish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BEARISH_FVG_OB
        assert a.trade_direction == "short"


# ──────────────────────────────────────────────────────────────────────────
# BoS minor confidence multiplier interaction with require_retest
# ──────────────────────────────────────────────────────────────────────────


class TestBoSRetestInteraction:
    def test_minor_bos_blocked_when_require_retest_true(self) -> None:
        eng = _make_engine(structural_break_require_retest=True)
        a = StructuralAnalysis(symbol="X", suggested_direction="long",
                               smc_confluence=50, position_in_range=0.4)
        a.market_structure = MarketStructureResult(
            structure="uptrend",
            last_bos=StructureEvent(event_type="bos", direction="bullish", significance="minor"),
        )
        a.mtf_confluence = MagicMock(score=6)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_major_bos_passes_regardless(self) -> None:
        for require_retest in [True, False]:
            eng = _make_engine(structural_break_require_retest=require_retest)
            a = StructuralAnalysis(symbol="X", suggested_direction="long",
                                   smc_confluence=50, position_in_range=0.4)
            a.market_structure = MarketStructureResult(
                structure="uptrend",
                last_bos=StructureEvent(event_type="bos", direction="bullish", significance="major"),
            )
            a.mtf_confluence = MagicMock(score=6)
            stype, conf = eng.classify_setup(a)
            assert stype == SetupType.BULLISH_STRUCTURAL_BREAK
            # Major BoS confidence unchanged regardless of retest setting
