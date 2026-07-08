"""ScannerWorker._qualifies — Layer 1 restructure Phase 5."""

from unittest.mock import MagicMock

import pytest

from src.analysis.structure.models.structure_types import SetupType
from src.config.settings import (
    ScannerQualitativeSettings,
    ScannerScoringWeights,
    ScannerSettings,
    Settings,
    UniverseSettings,
)
from src.workers.scanner_worker import ScannerWorker


def _stub_structure(setup_type: SetupType = SetupType.BULLISH_FVG_OB, rr: float = 3.0, manipulation: bool = False):
    """Build a minimal StructuralAnalysis-like stub with the fields _qualifies reads.

    Updated for Definitive-fix Phase 4 (2026-04-28): the qualifier now
    selects ``rr_long``/``rr_short`` by consensus direction, so the stub
    sets all three identically (production placement engine returns the
    same value when both directions evaluate symmetrically).
    """
    stub = MagicMock()
    stub.setup_type = setup_type
    stub.structural_placement = MagicMock(
        rr_ratio=rr, rr_long=rr, rr_short=rr,
    )
    stub.session_context = MagicMock(manipulation_likely=manipulation)
    return stub


def _stub_consensus(consensus: str = "STRONG", direction: str = "long") -> dict:
    return {
        "consensus": consensus,
        "consensus_score": 0.9,
        "vote_count": 5,
        "direction": direction,
        "last_updated": 0.0,
    }


def _stub_regime_state(regime_value: str = "trending_up") -> MagicMock:
    state = MagicMock()
    state.regime = MagicMock()
    state.regime.value = regime_value
    return state


def _stub_worker(
    *,
    structure_for=None,
    consensus_for=None,
    regime_for=None,
    funding_for=None,
    qualitative=None,
) -> ScannerWorker:
    """Construct a ScannerWorker bypassing the heavy DI."""
    w = ScannerWorker.__new__(ScannerWorker)
    settings = MagicMock()
    settings.scanner = MagicMock()
    settings.scanner.qualitative = qualitative or ScannerQualitativeSettings()
    settings.universe = MagicMock()
    settings.universe.watch_list = ("BTCUSDT",)
    w.settings = settings

    structure_worker = MagicMock()
    cache = MagicMock()
    cache.get = lambda sym: structure_for if structure_for is not None else None
    structure_worker._cache = cache

    layer_manager = MagicMock()
    layer_manager.get_strategy_consensus = (
        lambda sym: consensus_for if consensus_for is not None else None
    )

    regime_worker = MagicMock()
    regime_worker.get_regime = lambda sym: regime_for

    altdata_worker = MagicMock()
    altdata_worker.get_funding = lambda sym: funding_for

    w.services = {
        "structure_worker": structure_worker,
        "layer_manager": layer_manager,
        "regime_worker": regime_worker,
        "altdata_worker": altdata_worker,
    }
    return w


class TestQualifies:
    def test_passes_all_criteria(self) -> None:
        w = _stub_worker(
            structure_for=_stub_structure(),
            consensus_for=_stub_consensus(),
            regime_for=_stub_regime_state("trending_up"),
            funding_for=0.0001,
        )
        ok, record = w._qualifies("BTCUSDT")
        assert ok is True
        assert any("xray_setup=" in r for r in record["reasons_passed"])
        assert any("consensus=STRONG" in r for r in record["reasons_passed"])

    def test_no_xray_analysis(self) -> None:
        w = _stub_worker(structure_for=None)
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert "no_xray_analysis" in record["reasons_failed"]

    def test_setup_type_none_fails(self) -> None:
        w = _stub_worker(structure_for=_stub_structure(setup_type=SetupType.NONE))
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert "no_xray_setup_type" in record["reasons_failed"]

    def test_weak_consensus_fails_with_default(self) -> None:
        w = _stub_worker(
            structure_for=_stub_structure(),
            consensus_for=_stub_consensus(consensus="WEAK"),
            regime_for=_stub_regime_state("trending_up"),
        )
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert any("consensus=WEAK" in r for r in record["reasons_failed"])

    def test_lean_consensus_fails(self) -> None:
        w = _stub_worker(
            structure_for=_stub_structure(),
            consensus_for=_stub_consensus(consensus="LEAN"),
            regime_for=_stub_regime_state("trending_up"),
        )
        ok, _ = w._qualifies("BTCUSDT")
        assert ok is False

    def test_min_consensus_strong_only(self) -> None:
        cfg = ScannerQualitativeSettings(min_consensus="STRONG")
        w = _stub_worker(
            qualitative=cfg,
            structure_for=_stub_structure(),
            consensus_for=_stub_consensus(consensus="GOOD"),
            regime_for=_stub_regime_state("trending_up"),
        )
        ok, _ = w._qualifies("BTCUSDT")
        assert ok is False  # STRONG-only mode rejects GOOD

    def test_regime_misalignment_fails(self) -> None:
        w = _stub_worker(
            structure_for=_stub_structure(),
            consensus_for=_stub_consensus(direction="long"),
            regime_for=_stub_regime_state("trending_down"),  # long vs trending_down
        )
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert any("regime=" in r for r in record["reasons_failed"])

    def test_low_rr_fails(self) -> None:
        # Definitive-fix Phase 4 (2026-04-28): default min_rr_ratio is now
        # 1.3 (was 2.0). Use rr=1.0 to assert "below default → reject".
        w = _stub_worker(
            structure_for=_stub_structure(rr=1.0),
            consensus_for=_stub_consensus(),
            regime_for=_stub_regime_state("trending_up"),
        )
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert any("rr=1.00" in r for r in record["reasons_failed"])

    def test_manipulation_blocker(self) -> None:
        w = _stub_worker(
            structure_for=_stub_structure(manipulation=True),
            consensus_for=_stub_consensus(),
            regime_for=_stub_regime_state("trending_up"),
        )
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert "manipulation_likely_session" in record["blockers"]

    def test_funding_against_long_blocker(self) -> None:
        w = _stub_worker(
            structure_for=_stub_structure(),
            consensus_for=_stub_consensus(direction="long"),
            regime_for=_stub_regime_state("trending_up"),
            funding_for=0.005,  # 0.5% positive — longs paying — blocker
        )
        ok, record = w._qualifies("BTCUSDT")
        assert ok is False
        assert any("funding_against_long" in b for b in record["blockers"])


class TestRegimeAligns:
    def test_long_aligns_trending_up(self) -> None:
        assert ScannerWorker._regime_aligns("trending_up", "long") is True

    def test_long_aligns_ranging(self) -> None:
        assert ScannerWorker._regime_aligns("ranging", "long") is True

    def test_short_aligns_trending_down(self) -> None:
        assert ScannerWorker._regime_aligns("trending_down", "short") is True

    def test_long_misaligns_trending_down(self) -> None:
        assert ScannerWorker._regime_aligns("trending_down", "long") is False

    def test_neutral_direction_fails(self) -> None:
        assert ScannerWorker._regime_aligns("trending_up", "neutral") is False


class TestQualitativeSettings:
    def test_validation(self) -> None:
        with pytest.raises(ValueError, match="min_consensus"):
            ScannerQualitativeSettings(min_consensus="FOO")
        with pytest.raises(ValueError, match="min_rr_ratio"):
            ScannerQualitativeSettings(min_rr_ratio=0)
        with pytest.raises(ValueError, match="max_selection"):
            ScannerQualitativeSettings(max_selection=2, min_selection=5)

    def test_defaults(self) -> None:
        c = ScannerQualitativeSettings()
        assert c.min_consensus == "GOOD"
        # Definitive-fix Phase 4 (2026-04-28) — default lowered 2.0 → 1.3.
        assert c.min_rr_ratio == 1.3
        assert c.max_selection == 15
