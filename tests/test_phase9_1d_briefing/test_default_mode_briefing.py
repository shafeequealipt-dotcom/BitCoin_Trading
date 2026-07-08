"""Phase 9 of the 1D briefing rewrite — cutover defaults flipped.

Single question per test: "Did Phase 9 successfully flip the production
defaults to briefing-mode + surface_briefing_fields?"

This test guards the cutover commit: if a future change accidentally
reverts a default, this test fails loudly.
"""

from src.config.settings import BrainSettings, ScannerSettings


def test_scanner_default_mode_is_briefing() -> None:
    """Phase 9 cutover: default mode is now 'briefing'."""
    s = ScannerSettings()
    assert s.mode == "briefing", (
        "Phase 9: scanner.mode default must be 'briefing' after cutover"
    )


def test_brain_surface_briefing_fields_defaults_true() -> None:
    """Phase 9 cutover: brain prompt surfaces briefing fields by default."""
    s = BrainSettings()
    assert s.surface_briefing_fields is True, (
        "Phase 9: brain.surface_briefing_fields default must be True after cutover"
    )


def test_ab_mode_remains_off_after_cutover() -> None:
    """A/B harness restored to off after Phase 9 cutover decision."""
    s = ScannerSettings()
    assert s.ab_mode == "off"


def test_rollback_restores_legacy_via_explicit_config() -> None:
    """Operator can flip back to legacy by setting both flags."""
    s = ScannerSettings(mode="exclusion")
    b = BrainSettings(surface_briefing_fields=False)
    assert s.mode == "exclusion"
    assert b.surface_briefing_fields is False
