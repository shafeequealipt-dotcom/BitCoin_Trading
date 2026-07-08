"""Layer 4 Realignment Phase 3.2 — watchdog emergency-mode trigger boundaries.

Smoke tests for the configurable thresholds in
``WatchdogEmergencySettings``. Verifies:
1. The dataclass defaults match the recalibrated values (-5.0 % session
   pnl, 5 hard-stops/hour) — the latter raised from the pre-fix 3.
2. ``_determine_mode`` returns "emergency" only at/below the configured
   threshold for each trigger.
3. ``_last_emergency_trigger`` records the cause for the
   EMERGENCY_CLOSED event payload.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.config.settings import (
    Settings,
    WatchdogEmergencySettings,
    WatchdogSettings,
)
from src.workers.position_watchdog import PositionWatchdog


def _make_watchdog(
    *, session_threshold: float = -5.0, hard_stops_threshold: int = 5,
) -> PositionWatchdog:
    """Build a watchdog using __new__ to skip the heavy __init__.
    Mirrors test patterns used in tests/test_profit_sniper_partial_cap.py."""
    wd = PositionWatchdog.__new__(PositionWatchdog)
    settings = MagicMock()
    settings.watchdog = MagicMock()
    settings.watchdog.emergency = WatchdogEmergencySettings(
        session_pnl_threshold_pct=session_threshold,
        hard_stops_per_hour_threshold=hard_stops_threshold,
    )
    wd.settings = settings
    wd._watchdog_mode = "passive"
    wd._session_pnl_pct = 0.0
    wd._hard_stops_this_hour = 0
    # Recent monotonic time so the hourly reset does NOT fire mid-test.
    # ``_determine_mode`` resets _hard_stops_this_hour when
    # time.monotonic() - _hard_stop_hour_start > 3600; we need this
    # window to stay open for the duration of a unit test.
    wd._hard_stop_hour_start = time.monotonic()
    wd._consecutive_losses = 0
    wd._started_at = 0.0
    wd._last_emergency_trigger = ""
    wd.claude_client = None
    return wd


def test_emergency_settings_defaults() -> None:
    """Phase 3.2 defaults: -5.0 % session pnl, 5 hard-stops/h (raised
    from the pre-fix 3)."""
    cfg = WatchdogEmergencySettings()
    assert cfg.session_pnl_threshold_pct == -5.0
    assert cfg.hard_stops_per_hour_threshold == 5


def test_session_pnl_below_threshold_triggers_emergency() -> None:
    """Session PnL below the configured threshold flips the watchdog
    into emergency and records the trigger reason."""
    wd = _make_watchdog(session_threshold=-5.0)
    wd._session_pnl_pct = -5.5  # below threshold
    mode = wd._determine_mode()
    assert mode == "emergency"
    assert "session_pnl" in wd._last_emergency_trigger
    assert "-5.50" in wd._last_emergency_trigger
    assert "-5.00" in wd._last_emergency_trigger


def test_session_pnl_at_threshold_does_not_trigger() -> None:
    """Strict less-than: session pnl exactly at threshold does NOT
    trigger emergency. Off-by-one regression guard."""
    wd = _make_watchdog(session_threshold=-5.0)
    wd._session_pnl_pct = -5.0  # exactly at threshold
    mode = wd._determine_mode()
    assert mode != "emergency", "session pnl at threshold should not trigger"


def test_hard_stops_at_threshold_triggers_emergency() -> None:
    """Hard-stops at the configured threshold triggers emergency.
    With Phase 3.2's default of 5, an hour with 5 SL hits is the
    floor; pre-fix this fired at 3."""
    wd = _make_watchdog(hard_stops_threshold=5)
    wd._hard_stops_this_hour = 5
    mode = wd._determine_mode()
    assert mode == "emergency"
    assert "hard_stops" in wd._last_emergency_trigger
    assert "5" in wd._last_emergency_trigger


def test_hard_stops_below_threshold_does_not_trigger() -> None:
    """Pre-fix hardcode was 3; Phase 3.2 raises to 5. With the new
    default, 4 hard-stops in an hour does NOT trigger emergency —
    a noisy hour can ride out without the nuclear option."""
    wd = _make_watchdog(hard_stops_threshold=5)
    wd._hard_stops_this_hour = 4
    mode = wd._determine_mode()
    assert mode != "emergency"


def test_settings_load_picks_up_emergency_section() -> None:
    """Settings.load() wires up the [watchdog.emergency] sub-table.
    Confirms config.toml override matches the dataclass default so
    the recalibrated values reach the running watchdog."""
    s = Settings._load_fresh(config_path="config.toml")
    assert hasattr(s.watchdog, "emergency")
    assert isinstance(s.watchdog.emergency, WatchdogEmergencySettings)
    assert s.watchdog.emergency.session_pnl_threshold_pct == -5.0
    assert s.watchdog.emergency.hard_stops_per_hour_threshold == 5
