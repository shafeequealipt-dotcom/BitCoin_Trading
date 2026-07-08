"""Definitive-fix Phase 3 — alignment broadening for ranging markets.

The forensic data showed ~46% of the universe in ``ranging`` regime
permanently NONE-ifying because ``_bull_alignment``/``_bear_alignment``
required strict uptrend/downtrend match. Phase 3 broadens the helpers
to accept ``ranging`` when ``mtf_score_01 >= ranging_market_mtf_threshold``
(default 0.55).
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
class _SetupTypesCfg:
    fvg_ob_min_confluence: float = 0.5
    structural_break_require_retest: bool = True
    sweep_min_displacement_pct: float = 0.5
    range_breakout_min_compression_bars: int = 20
    mtf_alignment_required: bool = True
    ranging_market_mtf_threshold: float = 0.55


@dataclass
class _StructSettings:
    setup_types: object = None


def _engine(cfg: _SetupTypesCfg) -> StructureEngine:
    eng = StructureEngine.__new__(StructureEngine)
    eng._settings = _StructSettings(setup_types=cfg)
    return eng


def _bullish_fvg_ob_in_struct(struct: str, mtf_score: float) -> StructuralAnalysis:
    a = StructuralAnalysis.__new__(StructuralAnalysis)
    a.suggested_direction = "long"
    a.market_structure = MarketStructureResult.__new__(MarketStructureResult)
    a.market_structure.structure = struct
    a.market_structure.last_bos = None
    a.nearest_fvg = FairValueGap.__new__(FairValueGap)
    a.nearest_fvg.direction = "bullish"
    a.nearest_fvg.filled = False
    a.nearest_ob = OrderBlock.__new__(OrderBlock)
    a.nearest_ob.direction = "bullish"
    a.nearest_ob.fresh = True
    a.active_sweep_signal = None
    a.mtf_confluence = _MTFStub(score=int(round(mtf_score * 10)))
    a.smc_confluence = 60.0
    a.position_in_range = 0.5
    a.total_confluence_factors = 5
    return a


def test_phase3_ranging_long_qualifies_when_mtf_clears_threshold() -> None:
    """ranging + long + mtf=0.65 (>=0.55) → BULLISH_FVG_OB."""
    eng = _engine(_SetupTypesCfg(ranging_market_mtf_threshold=0.55))
    a = _bullish_fvg_ob_in_struct("ranging", mtf_score=0.65)
    setup_type, _ = eng.classify_setup(a)
    assert setup_type == SetupType.BULLISH_FVG_OB


def test_phase3_ranging_long_rejects_when_mtf_below_threshold() -> None:
    """ranging + long + mtf=0.50 (<0.55) → NONE."""
    eng = _engine(_SetupTypesCfg(ranging_market_mtf_threshold=0.55))
    a = _bullish_fvg_ob_in_struct("ranging", mtf_score=0.50)
    setup_type, _ = eng.classify_setup(a)
    assert setup_type == SetupType.NONE


def test_phase3_volatile_still_rejected() -> None:
    """Volatile market still rejected regardless of mtf_score."""
    eng = _engine(_SetupTypesCfg(ranging_market_mtf_threshold=0.55))
    a = _bullish_fvg_ob_in_struct("volatile", mtf_score=0.95)
    setup_type, _ = eng.classify_setup(a)
    assert setup_type == SetupType.NONE


def test_phase3_uptrend_still_qualifies_at_lower_mtf() -> None:
    """Strict uptrend keeps the original lower-bar path (mtf>=fvg_ob_min)."""
    eng = _engine(_SetupTypesCfg(
        fvg_ob_min_confluence=0.5, ranging_market_mtf_threshold=0.55,
    ))
    a = _bullish_fvg_ob_in_struct("uptrend", mtf_score=0.55)
    setup_type, _ = eng.classify_setup(a)
    assert setup_type == SetupType.BULLISH_FVG_OB
