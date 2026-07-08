"""Definitive-fix Phase 2 smoke test — MTF threshold lowered to 0.5.

Asserts that an FVG+OB setup with `mtf_score=0.55` (formerly rejected
against the 0.70 gate) now classifies as BULLISH_FVG_OB.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    MarketStructureResult,
    OrderBlock,
    SetupType,
    StructuralAnalysis,
)
from src.analysis.structure.structure_engine import StructureEngine


@dataclass
class _MTFStub:
    score: int = 0


@dataclass
class _StructSettings:
    """Minimal stand-in for ``settings.structure`` exposing only what
    classify_setup reads."""

    setup_types: object = None


@dataclass
class _SetupTypesCfg:
    fvg_ob_min_confluence: float = 0.5
    structural_break_require_retest: bool = True
    sweep_min_displacement_pct: float = 0.5
    range_breakout_min_compression_bars: int = 20
    mtf_alignment_required: bool = True


def _make_engine(threshold: float) -> StructureEngine:
    settings = _StructSettings(setup_types=_SetupTypesCfg(fvg_ob_min_confluence=threshold))
    eng = StructureEngine.__new__(StructureEngine)
    eng._settings = settings
    return eng


def _make_bullish_fvg_ob_analysis(mtf_score: float) -> StructuralAnalysis:
    """Build the minimum StructuralAnalysis needed by classify_setup
    to take the BULLISH_FVG_OB branch given a particular mtf_score."""

    a = StructuralAnalysis.__new__(StructuralAnalysis)
    a.suggested_direction = "long"
    a.market_structure = MarketStructureResult.__new__(MarketStructureResult)
    a.market_structure.structure = "uptrend"
    a.market_structure.last_bos = None
    a.nearest_fvg = FairValueGap.__new__(FairValueGap)
    a.nearest_fvg.direction = "bullish"
    a.nearest_fvg.filled = False
    a.nearest_ob = OrderBlock.__new__(OrderBlock)
    a.nearest_ob.direction = "bullish"
    a.nearest_ob.fresh = True
    a.active_sweep_signal = None
    a.mtf_confluence = _MTFStub(score=int(round(mtf_score * 10)))
    a.smc_confluence = 60.0  # 0-100 scale
    a.position_in_range = 0.5
    a.total_confluence_factors = 5
    return a


def test_phase2_mtf_055_classifies_bullish_fvg_ob() -> None:
    """With the lowered threshold, mtf=0.55 now passes BULLISH_FVG_OB."""
    eng = _make_engine(threshold=0.5)
    a = _make_bullish_fvg_ob_analysis(mtf_score=0.55)
    setup_type, conf = eng.classify_setup(a)
    assert setup_type == SetupType.BULLISH_FVG_OB
    assert 0.0 < conf <= 1.0


def test_phase2_mtf_below_threshold_still_none() -> None:
    """mtf=0.45 still under the new 0.50 gate → NONE (no over-loosening)."""
    eng = _make_engine(threshold=0.5)
    a = _make_bullish_fvg_ob_analysis(mtf_score=0.45)
    setup_type, _ = eng.classify_setup(a)
    assert setup_type == SetupType.NONE
