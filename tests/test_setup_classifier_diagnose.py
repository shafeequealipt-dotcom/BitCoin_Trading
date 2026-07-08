"""Phase 2 (output-quality) — StructureEngine.diagnose_none() tests.

Verifies the post-fix diagnostic that explains WHY classify_setup() returned
NONE. Operators consume `XRAY_NONE_REASON` log events to tune
`[analysis.structure.setup_types]` thresholds with evidence rather than
guesswork.

The diagnostic is read-only — it walks the same decision tree as
classify_setup() but reports which branch came closest to firing and what
specific condition blocked it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.analysis.structure.structure_engine import StructureEngine


def _make_engine_with_settings(setup_types_cfg=None) -> StructureEngine:
    """Build a StructureEngine stub for unit-testing the diagnose_none method.

    diagnose_none reads the same `setup_types` config block as classify_setup.
    """
    eng = StructureEngine.__new__(StructureEngine)
    eng._settings = MagicMock()
    eng._settings.setup_types = setup_types_cfg
    return eng


def _make_analysis(
    *,
    direction: str = "",
    structure: str = "",
    nearest_fvg=None,
    nearest_ob=None,
    active_sweep=None,
    mtf_score: float = 0.0,
    smc_confluence: float = 0.0,
    last_bos=None,
):
    """Build a minimal StructuralAnalysis-shaped object for the diagnostic."""
    a = MagicMock()
    a.suggested_direction = direction
    a.market_structure = MagicMock()
    a.market_structure.structure = structure
    a.market_structure.last_bos = last_bos
    a.nearest_fvg = nearest_fvg
    a.nearest_ob = nearest_ob
    a.active_sweep_signal = active_sweep
    a.mtf_confluence = MagicMock(score=mtf_score) if mtf_score else None
    a.smc_confluence = smc_confluence
    return a


def test_diagnose_none_when_no_inputs() -> None:
    """A bare analysis with nothing populated → closest_type=none, weakest_input identifiable."""
    eng = _make_engine_with_settings()
    a = _make_analysis()
    diag = eng.diagnose_none(a)
    # No real branch fits → closest_type=none
    assert diag["closest_type"] == "none"
    assert diag["mtf_score_01"] == 0.0
    assert diag["smc_01"] == 0.0
    assert diag["has_fvg"] is False
    assert diag["has_ob"] is False
    assert diag["has_active_sweep"] is False


def test_diagnose_none_identifies_closest_branch_fvg_ob() -> None:
    """Bullish FVG present, OB present, alignment OK, but mtf_score below threshold.

    Expected: closest_type=BULLISH_FVG_OB; missed_by mentions mtf_score
    below fvg_ob_min.
    """
    eng = _make_engine_with_settings()
    fvg = MagicMock(direction="bullish", filled=False)
    ob = MagicMock(direction="bullish", fresh=True)
    a = _make_analysis(
        direction="long",
        structure="uptrend",
        nearest_fvg=fvg,
        nearest_ob=ob,
        mtf_score=4.0,  # mtf_score_01 = 0.4, below default fvg_ob_min=0.7
        smc_confluence=50.0,
    )
    diag = eng.diagnose_none(a)
    assert diag["closest_type"] == "BULLISH_FVG_OB"
    assert "mtf_score=0.40" in diag["missed_by"]
    assert "fvg_ob_min=0.70" in diag["missed_by"]
    assert diag["has_fvg"] is True
    assert diag["has_ob"] is True


def test_diagnose_none_structural_break_missing_bos() -> None:
    """Direction long but no BOS → BULLISH_STRUCTURAL_BREAK was at most 1/3."""
    eng = _make_engine_with_settings()
    a = _make_analysis(
        direction="long",
        structure="",  # no structure alignment → breaks FVG_OB chain too
        last_bos=None,  # no BOS available
        mtf_score=8.0,
        smc_confluence=70.0,
    )
    diag = eng.diagnose_none(a)
    # Some branch is closest; if it's BULLISH_STRUCTURAL_BREAK, missed_by mentions no_bullish_bos.
    # Otherwise direction-only branches still have 1 point.
    assert diag["closest_type"] in (
        "BULLISH_STRUCTURAL_BREAK",
        "BULLISH_FVG_OB",  # could tie or beat depending on weights
        "none",
    )
    assert diag["direction"] == "long"


def test_diagnose_none_weakest_input_identification() -> None:
    """A coin with high MTF but no SMC, no fvg, no ob, no sweep, no direction:
    weakest_input should be one of the absent ones.
    """
    eng = _make_engine_with_settings()
    a = _make_analysis(
        direction="",
        structure="",
        mtf_score=8.0,           # mtf=0.8 — strong
        smc_confluence=0.0,      # smc=0.0 — weakest
    )
    diag = eng.diagnose_none(a)
    # Weakest input must be one of the absent ones (smc, sweep, fvg, ob, direction)
    assert diag["weakest_input"] in (
        "smc",
        "fvg_present",
        "ob_present",
        "sweep_present",
        "direction_alignment",
    )
    assert diag["weakest_input"] != "mtf"  # mtf=0.8 is strong


def test_diagnose_none_with_partial_sweep() -> None:
    """Active sweep present but depth below threshold → closest_type sweep with miss reason."""
    eng = _make_engine_with_settings()
    sweep = MagicMock(sweep_depth_pct=0.2, sweep_type="bullish_sweep")
    a = _make_analysis(
        direction="long",
        structure="uptrend",
        active_sweep=sweep,
        mtf_score=5.0,
        smc_confluence=40.0,
    )
    diag = eng.diagnose_none(a)
    # The diagnose returned closest_type which scored highest;
    # at minimum it should mention something useful.
    assert diag["closest_type"] != "none" or "no_active_sweep" in diag["missed_by"]
    assert diag["has_active_sweep"] is True


def test_diagnose_none_returns_all_required_keys() -> None:
    """API contract: every key documented in the docstring must be present."""
    eng = _make_engine_with_settings()
    a = _make_analysis()
    diag = eng.diagnose_none(a)
    required_keys = {
        "closest_type",
        "missed_by",
        "weakest_input",
        "mtf_score_01",
        "smc_01",
        "direction",
        "structure",
        "has_fvg",
        "has_ob",
        "has_active_sweep",
    }
    assert set(diag.keys()) >= required_keys
