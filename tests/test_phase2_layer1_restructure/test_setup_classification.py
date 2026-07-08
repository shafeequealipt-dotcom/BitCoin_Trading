"""Tests for StructureEngine.classify_setup — Layer 1 restructure Phase 2."""

from unittest.mock import MagicMock

import pytest

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    LiquiditySweep,
    MarketStructureResult,
    OrderBlock,
    SetupType,
    StructureEvent,
    StructuralAnalysis,
)
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import SetupTypesSettings, StructureSettings


def _make_engine(**setup_overrides) -> StructureEngine:
    settings = StructureSettings(
        setup_types=SetupTypesSettings(**setup_overrides),
    )
    return StructureEngine(settings)


def _bullish_fvg_ob_analysis() -> StructuralAnalysis:
    """Construct an analysis that should classify as BULLISH_FVG_OB."""
    a = StructuralAnalysis(
        symbol="BTCUSDT",
        suggested_direction="long",
        smc_confluence=80,
        position_in_range=0.2,
        total_confluence_factors=4,
    )
    a.market_structure = MarketStructureResult(structure="uptrend", strength="strong")
    a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
    a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
    # MTF score 0-10 in real model — fake one with .score=8
    a.mtf_confluence = MagicMock(score=8)
    return a


class TestClassifySetup:
    def test_bullish_fvg_ob(self) -> None:
        eng = _make_engine()
        a = _bullish_fvg_ob_analysis()
        stype, conf = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_FVG_OB
        assert 0.5 <= conf <= 1.0

    def test_bearish_fvg_ob_mirror(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(
            symbol="ETHUSDT",
            suggested_direction="short",
            smc_confluence=70,
        )
        a.market_structure = MarketStructureResult(structure="downtrend")
        a.nearest_fvg = FairValueGap(direction="bearish", filled=False)
        a.nearest_ob = OrderBlock(direction="bearish", fresh=True)
        a.mtf_confluence = MagicMock(score=8)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BEARISH_FVG_OB

    def test_bullish_structural_break(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult(
            structure="uptrend",
            last_bos=StructureEvent(
                event_type="bos",
                direction="bullish",
                significance="major",
            ),
        )
        a.smc_confluence = 60
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_STRUCTURAL_BREAK

    def test_bullish_structural_break_requires_major_when_retest_required(self) -> None:
        eng = _make_engine(structural_break_require_retest=True)
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult(
            last_bos=StructureEvent(
                event_type="bos", direction="bullish", significance="minor",
            ),
        )
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE  # minor BOS doesn't pass retest gate

    def test_bullish_liquidity_sweep(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        a.active_sweep_signal = LiquiditySweep(
            sweep_type="bullish_sweep", sweep_depth_pct=0.8,
        )
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_LIQUIDITY_SWEEP

    def test_sweep_below_threshold_falls_to_none(self) -> None:
        eng = _make_engine(sweep_min_displacement_pct=1.0)
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        a.active_sweep_signal = LiquiditySweep(
            sweep_type="bullish_sweep", sweep_depth_pct=0.4,
        )
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_bullish_range_breakout(self) -> None:
        eng = _make_engine(range_breakout_min_compression_bars=10)
        a = StructuralAnalysis(
            symbol="X",
            suggested_direction="long",
            position_in_range=0.97,
            total_confluence_factors=6,  # >= 10/2 = 5
        )
        a.market_structure = MarketStructureResult()
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_RANGE_BREAKOUT

    def test_none_default(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X")  # all defaults
        a.market_structure = MarketStructureResult()
        stype, conf = eng.classify_setup(a)
        assert stype == SetupType.NONE
        assert conf == 0.0


class TestStructuralAnalysisFields:
    def test_default_setup_type_is_none(self) -> None:
        a = StructuralAnalysis(symbol="X")
        assert a.setup_type == SetupType.NONE
        assert a.setup_type_confidence == 0.0

    def test_to_dict_includes_setup_type(self) -> None:
        a = StructuralAnalysis(symbol="X")
        a.market_structure = MarketStructureResult()
        a.setup_type = SetupType.BULLISH_FVG_OB
        a.setup_type_confidence = 0.85
        d = a.to_dict()
        assert d["setup_type"] == "bullish_fvg_ob"
        assert d["setup_type_confidence"] == 0.85


class TestSetupTypesSettings:
    def test_validation(self) -> None:
        with pytest.raises(ValueError, match="fvg_ob_min_confluence"):
            SetupTypesSettings(fvg_ob_min_confluence=1.5)
        with pytest.raises(ValueError, match="sweep_min_displacement_pct"):
            SetupTypesSettings(sweep_min_displacement_pct=0)
        with pytest.raises(ValueError, match="range_breakout_min_compression_bars"):
            SetupTypesSettings(range_breakout_min_compression_bars=0)

    def test_defaults(self) -> None:
        s = SetupTypesSettings()
        assert s.fvg_ob_min_confluence == 0.7
        assert s.structural_break_require_retest is True
        assert s.sweep_min_displacement_pct == 0.5
        assert s.range_breakout_min_compression_bars == 20


class TestSetupTypeCounterVariants:
    """Phase 1 — counter setup variants on the SetupType enum.

    Pure enum presence + serialization checks. The variants are emitted
    by classify_setup() in Phase 4; in Phase 1 we only verify they
    exist and serialize correctly (str mixin).
    """

    def test_bullish_counter_variant_exists(self) -> None:
        assert hasattr(SetupType, "BULLISH_FVG_OB_COUNTER")
        assert SetupType.BULLISH_FVG_OB_COUNTER.value == "bullish_fvg_ob_counter"

    def test_bearish_counter_variant_exists(self) -> None:
        assert hasattr(SetupType, "BEARISH_FVG_OB_COUNTER")
        assert SetupType.BEARISH_FVG_OB_COUNTER.value == "bearish_fvg_ob_counter"

    def test_counter_variants_are_str_mixin(self) -> None:
        # str mixin lets the value compare directly to a string literal
        # and serialize cleanly through json.dumps without a custom encoder.
        assert SetupType.BULLISH_FVG_OB_COUNTER == "bullish_fvg_ob_counter"
        assert SetupType.BEARISH_FVG_OB_COUNTER == "bearish_fvg_ob_counter"

    def test_counter_variants_distinct_from_in_direction(self) -> None:
        # In-direction and counter variants must be distinguishable by
        # downstream consumers (TradeScorer, scanner._qualifies, brain prompt).
        assert SetupType.BULLISH_FVG_OB != SetupType.BULLISH_FVG_OB_COUNTER
        assert SetupType.BEARISH_FVG_OB != SetupType.BEARISH_FVG_OB_COUNTER

    def test_total_variant_count(self) -> None:
        # 11 variants total: NONE + 5 bullish (incl. counter) + 5 bearish (incl. counter)
        assert len(SetupType) == 11
