"""Layer 4 Realignment Phase 1D — raised max_partials_per_position default.

Smoke tests for the new default ``max_partials_per_position=3``.
Combined with Phase 1B's 60-tick grace gap, three partials provide
roughly 15 minutes of recovery opportunity before forced full close.

The existing ``tests/test_profit_sniper_partial_cap.py`` covers the
behavioural contract (partial→full at the cap). This file confirms
the new default value is wired through Mode4Settings and config.toml.
"""

from __future__ import annotations

from src.config.settings import Mode4Settings, Settings


def test_max_partials_default_is_three() -> None:
    """Mode4Settings dataclass default is 3 (Phase 1D recalibration)."""
    cfg = Mode4Settings()
    assert cfg.max_partials_per_position == 3, (
        f"Phase 1D default should be 3 (was 1 in Phase 10); "
        f"got {cfg.max_partials_per_position}"
    )


def test_settings_load_picks_up_partial_cap_default() -> None:
    """config.toml [mode4] override matches the dataclass default so a
    fresh deployment without any explicit config edit gets the
    recalibrated value."""
    s = Settings._load_fresh(config_path="config.toml")
    assert s.mode4.max_partials_per_position == 3
