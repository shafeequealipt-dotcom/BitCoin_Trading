"""Phase 5 of the 1D briefing rewrite — mode flag wiring.

Through Phases 5-8 the default was ``exclusion`` so the briefing path
ran only when the operator opted in. Phase 9 cutover (2026-05-01)
flipped the default to ``briefing`` after the Phase 8 A/B harness
showed healthy behavior. The default-value assertion now lives in
``tests/test_phase9_1d_briefing/test_default_mode_briefing.py``; the
tests below cover the wiring contract that's invariant across phases:
both modes load from TOML, invalid modes are rejected, and the legacy
``exclusion`` value still loads cleanly (rollback path).
"""

import pytest

from src.config.settings import ScannerSettings, _build_scanner


def test_build_scanner_briefing_mode_loads() -> None:
    """Operator-set briefing mode loads correctly."""
    s = _build_scanner({"mode": "briefing"})
    assert s.mode == "briefing"


def test_build_scanner_exclusion_mode_loads_for_rollback() -> None:
    """Phase 9 rollback path: setting mode = "exclusion" in config.toml
    still loads the legacy gate."""
    s = _build_scanner({"mode": "exclusion"})
    assert s.mode == "exclusion"


def test_explicit_mode_round_trip() -> None:
    """Both modes can be set via the dataclass constructor."""
    assert ScannerSettings(mode="briefing").mode == "briefing"
    assert ScannerSettings(mode="exclusion").mode == "exclusion"


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError, match="scanner.mode must be one of"):
        ScannerSettings(mode="something_else")
