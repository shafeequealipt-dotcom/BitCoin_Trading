"""Definitive-fix Phase 4 smoke tests — scanner RR fixes.

Three smoke tests:
1. _get_directional_rr returns rr_long when consensus says long.
2. _get_directional_rr returns rr_short when consensus says short.
3. _compute_opportunity_score includes the new 6th ``rr`` component.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from src.workers.scanner_worker import ScannerWorker


@dataclass
class _Placement:
    rr_ratio: float = 0.0
    rr_long: float = 0.0
    rr_short: float = 0.0


@dataclass
class _Structure:
    structural_placement: _Placement = None


def _make_worker(structure, consensus, weights=None):
    """Build a bare ScannerWorker bound to mocked services."""
    sw = ScannerWorker.__new__(ScannerWorker)
    structure_worker_stub = MagicMock()
    structure_worker_stub._cache.get.return_value = structure
    layer_manager_stub = MagicMock()
    layer_manager_stub.get_strategy_consensus.return_value = consensus
    sw.services = {
        "structure_worker": structure_worker_stub,
        "layer_manager": layer_manager_stub,
        "altdata_worker": None,
        "regime_worker": None,
        "strategy_worker": None,
        "signal_worker": None,
    }
    sw.settings = MagicMock()
    sw.settings.scanner.scoring_weights.structure = 0.27
    sw.settings.scanner.scoring_weights.strategy = 0.27
    sw.settings.scanner.scoring_weights.signal = 0.13
    sw.settings.scanner.scoring_weights.regime = 0.13
    sw.settings.scanner.scoring_weights.funding = 0.10
    sw.settings.scanner.scoring_weights.rr = weights if weights is not None else 0.10
    return sw


def test_phase4_directional_rr_long() -> None:
    """Long consensus → rr_long is read."""
    structure = _Structure(structural_placement=_Placement(
        rr_long=2.5, rr_short=0.8, rr_ratio=2.5,
    ))
    sw = _make_worker(structure, {"direction": "long"})
    rr = sw._get_directional_rr("BTCUSDT")
    assert rr == 2.5


def test_phase4_directional_rr_short() -> None:
    """Short consensus → rr_short is read (NOT rr_best)."""
    structure = _Structure(structural_placement=_Placement(
        rr_long=2.5, rr_short=0.8, rr_ratio=2.5,
    ))
    sw = _make_worker(structure, {"direction": "short"})
    rr = sw._get_directional_rr("BTCUSDT")
    assert rr == 0.8


def test_phase4_composite_includes_rr_component() -> None:
    """The composite score now includes the 6th rr component."""
    structure = _Structure(structural_placement=_Placement(
        rr_long=3.0, rr_short=0.5, rr_ratio=3.0,
    ))
    sw = _make_worker(structure, {"direction": "long"})
    # Stub the other component accessors so only RR contributes.
    sw._get_setup_score = lambda c: 0.0
    sw._get_strategy_score = lambda c: 0.0
    sw._get_signal_confidence = lambda c: 0.0
    sw._get_regime_alignment = lambda c: -1.0  # → 0.0 normalized
    sw._get_funding_strength = lambda c: 0.0
    score, breakdown = sw._compute_opportunity_score("BTCUSDT")
    # rr=3.0 saturates at 1.0 → 0.10 weight × 1.0 = 0.10 contribution.
    assert "rr" in breakdown
    assert breakdown["rr"] == 1.0
    # Other components are zero so the score equals rr contribution.
    assert abs(score - 0.10) < 1e-6
