"""Layer 4 Realignment Phase 1B — recalibrated stall escape thresholds.

Smoke tests for the new defaults
``stall_escape_partial_after_ticks=120`` (10 min at 5 s cadence) and
``stall_escape_full_after_ticks=180`` (15 min at 5 s cadence). These
match the operator's 10-30 min hold strategy and preserve a 60-tick
(5-min) grace gap between partial-close emission and forced full-close.
"""

from __future__ import annotations

from src.config.settings import Mode4Settings, Settings


def test_mode4_default_thresholds_match_strategy_window() -> None:
    """Defaults align with the 10-30 min strategy window. The partial
    threshold sits at the start of the window (10 min); the full
    threshold sits 5 min later, giving the position room to recover
    after the partial-close signal before forced full-close fires."""
    cfg = Mode4Settings()
    assert cfg.stall_escape_partial_after_ticks == 120, (
        f"Phase 1B default should be 120 ticks (10 min @ 5 s cadence); "
        f"got {cfg.stall_escape_partial_after_ticks}"
    )
    assert cfg.stall_escape_full_after_ticks == 180, (
        f"Phase 1B default should be 180 ticks (15 min @ 5 s cadence); "
        f"got {cfg.stall_escape_full_after_ticks}"
    )
    # Grace gap is preserved at exactly 60 ticks = 5 min.
    grace_ticks = cfg.stall_escape_full_after_ticks - cfg.stall_escape_partial_after_ticks
    assert grace_ticks == 60, (
        f"partial-to-full grace gap should be 60 ticks (5 min); "
        f"got {grace_ticks}"
    )


def test_settings_load_picks_up_new_thresholds() -> None:
    """Settings.load() picks up the new defaults from Mode4Settings.
    Confirms config.toml [mode4] override matches the dataclass default."""
    s = Settings._load_fresh(config_path="config.toml")
    # config.toml is the canonical source; verify it matches the new
    # defaults so a deployment without an explicit config override
    # still gets the recalibrated values.
    assert s.mode4.stall_escape_partial_after_ticks == 120
    assert s.mode4.stall_escape_full_after_ticks == 180
