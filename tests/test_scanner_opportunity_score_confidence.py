"""Phase 5b — opportunity_score honors structural confidence.

Verifies that ``ScannerWorker._compute_opportunity_score`` multiplies
``struct_norm`` by ``setup_type_confidence`` (clamped to [0.5, 1.0])
so counter setups (≈0.35 confidence) don't out-rank in-direction
setups (≈0.55-0.85) when their setup_score happens to land close.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _StubScannerCfg:
    structure: float = 0.27
    strategy: float = 0.27
    signal: float = 0.13
    regime: float = 0.13
    funding: float = 0.10
    rr: float = 0.10


@dataclass
class _StubQualitative:
    min_rr_ratio: float = 1.1
    min_consensus: str = "GOOD"
    require_regime_alignment: bool = True
    funding_blocker_threshold_pct: float = 0.001
    recent_failure_blocker_hours: int = 12
    manipulation_blocker_enabled: bool = True


@dataclass
class _StubScanner:
    scoring_weights: _StubScannerCfg
    qualitative: _StubQualitative
    top_n_min: int = 10
    top_n_max: int = 15


@dataclass
class _StubSettings:
    scanner: _StubScanner


class _StubStructureWorker:
    """Drop-in fake of structure_worker for the accessor surface."""

    def __init__(self, score: float, conf: float | None) -> None:
        self._score = score
        self._conf = conf

    def get_setup_score(self, coin: str) -> float | None:
        return self._score

    def get_setup_type_confidence(self, coin: str) -> float | None:
        return self._conf


def _make_scanner(setup_score: float, setup_conf: float | None):
    """Construct a minimal ScannerWorker with stubbed accessors."""
    from src.workers.scanner_worker import ScannerWorker

    settings = _StubSettings(
        scanner=_StubScanner(
            scoring_weights=_StubScannerCfg(),
            qualitative=_StubQualitative(),
        ),
    )
    services = {"structure_worker": _StubStructureWorker(setup_score, setup_conf)}
    # Construct without invoking the full sweet-spot worker init by
    # patching the parts we exercise. ScannerWorker._compute_opportunity_score
    # only touches self.services + self.settings.scanner.scoring_weights.
    sw = ScannerWorker.__new__(ScannerWorker)
    sw.settings = settings
    sw.services = services
    return sw


class TestStructureConfidenceWeighting:
    def test_counter_confidence_reduces_struct_norm(self) -> None:
        # Same setup_score=80 in both, only confidence differs.
        sw_in = _make_scanner(setup_score=80.0, setup_conf=0.85)
        sw_counter = _make_scanner(setup_score=80.0, setup_conf=0.35)
        score_in, bd_in = sw_in._compute_opportunity_score("BTCUSDT")
        score_counter, bd_counter = sw_counter._compute_opportunity_score("BTCUSDT")
        # struct_raw same, struct_conf differs, struct_norm = raw × conf.
        assert bd_in["structure_raw"] == bd_counter["structure_raw"]
        assert bd_counter["structure"] < bd_in["structure"]
        assert score_counter < score_in

    def test_floor_at_0_5(self) -> None:
        # Confidence 0.0 → factor 0.5 (floor).
        sw = _make_scanner(setup_score=100.0, setup_conf=0.0)
        _, bd = sw._compute_opportunity_score("BTCUSDT")
        # struct_raw = 1.0 (clamped from 100/100), conf factor = 0.5,
        # struct_norm = 0.5
        assert bd["structure_conf"] == 0.5
        assert bd["structure"] == pytest.approx(0.5, abs=0.001)

    def test_ceiling_at_1_0(self) -> None:
        # Confidence 1.5 → factor 1.0 (ceiling).
        sw = _make_scanner(setup_score=100.0, setup_conf=1.5)
        _, bd = sw._compute_opportunity_score("BTCUSDT")
        assert bd["structure_conf"] == 1.0
        assert bd["structure"] == pytest.approx(1.0, abs=0.001)

    def test_legacy_no_accessor_uses_default_0_85(self) -> None:
        # When accessor returns None, default 0.85 is applied.
        sw = _make_scanner(setup_score=100.0, setup_conf=None)
        _, bd = sw._compute_opportunity_score("BTCUSDT")
        assert bd["structure_conf"] == 0.85
        assert bd["structure"] == pytest.approx(0.85, abs=0.001)

    def test_in_direction_outranks_counter_at_similar_setup_score(self) -> None:
        # Pre-fix: in-direction at score=70/conf=0.85 vs counter at score=70/conf=0.35
        # would tie. Post-Phase-5b: in-direction outranks.
        sw_in = _make_scanner(setup_score=70.0, setup_conf=0.85)
        sw_counter = _make_scanner(setup_score=70.0, setup_conf=0.35)
        score_in, _ = sw_in._compute_opportunity_score("X")
        score_counter, _ = sw_counter._compute_opportunity_score("X")
        assert score_in > score_counter

    def test_high_score_high_conf_beats_low_score_high_conf(self) -> None:
        # Sanity: structure ranking still preserves setup_score signal.
        sw_high = _make_scanner(setup_score=90.0, setup_conf=0.85)
        sw_low = _make_scanner(setup_score=50.0, setup_conf=0.85)
        score_high, _ = sw_high._compute_opportunity_score("X")
        score_low, _ = sw_low._compute_opportunity_score("X")
        assert score_high > score_low

    def test_breakdown_has_structure_conf_keys(self) -> None:
        sw = _make_scanner(setup_score=80.0, setup_conf=0.7)
        _, bd = sw._compute_opportunity_score("X")
        assert "structure" in bd
        assert "structure_raw" in bd
        assert "structure_conf" in bd
        assert bd["structure_raw"] == 0.8  # 80/100
        assert bd["structure_conf"] == 0.7
