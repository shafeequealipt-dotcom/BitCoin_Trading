"""Brain Liveness Heartbeat (2026-07-25) tests.

Verifies the pure-function evaluator produces correct verdicts across the
fail-open / staleness / kill-switch / clock-skew paths, the threshold
derivation formula, and that BrainLivenessSettings rejects invalid config.
See src/core/brain_liveness.py for the incident this exists to catch.
"""

from __future__ import annotations

import pytest

from src.config.settings import BrainLivenessSettings
from src.core.brain_liveness import (
    VERDICT_HEALTHY,
    VERDICT_STALE,
    VERDICT_UNKNOWN,
    compute_staleness_threshold,
    evaluate_brain_liveness,
)


def test_fresh_heartbeat_is_healthy() -> None:
    result = evaluate_brain_liveness(heartbeat_timestamp=1000.0, now=1000.0, threshold_seconds=60.0)
    assert result.verdict == VERDICT_HEALTHY
    assert result.age_seconds == 0.0


def test_heartbeat_within_threshold_is_healthy() -> None:
    result = evaluate_brain_liveness(heartbeat_timestamp=1000.0, now=1059.0, threshold_seconds=60.0)
    assert result.verdict == VERDICT_HEALTHY
    assert result.age_seconds == 59.0


def test_heartbeat_past_threshold_is_stale() -> None:
    result = evaluate_brain_liveness(heartbeat_timestamp=1000.0, now=1061.0, threshold_seconds=60.0)
    assert result.verdict == VERDICT_STALE
    assert result.age_seconds == 61.0


def test_missing_heartbeat_fails_open_to_unknown() -> None:
    """No heartbeat file (fresh boot, or a real problem) must never be
    immediately treated the same as a confirmed stale/hung process —
    matches the entry-gate fail-open convention."""
    result = evaluate_brain_liveness(heartbeat_timestamp=None, now=1000.0, threshold_seconds=60.0)
    assert result.verdict == VERDICT_UNKNOWN
    assert result.age_seconds is None


def test_future_heartbeat_is_unknown_not_stale() -> None:
    """Clock skew / a heartbeat read mid-rename must never falsely alarm."""
    result = evaluate_brain_liveness(heartbeat_timestamp=2000.0, now=1000.0, threshold_seconds=60.0)
    assert result.verdict == VERDICT_UNKNOWN
    assert result.reason == "heartbeat_in_future"


def test_zero_threshold_is_kill_switch() -> None:
    result = evaluate_brain_liveness(heartbeat_timestamp=1.0, now=1_000_000.0, threshold_seconds=0.0)
    assert result.verdict == VERDICT_HEALTHY
    assert result.reason == "check_disabled_threshold_zero"


def test_compute_staleness_threshold_uses_multiplier() -> None:
    assert compute_staleness_threshold(2700.0, 2.5, 600.0) == 6750.0


def test_compute_staleness_threshold_respects_floor() -> None:
    """A very fast cadence must not produce a threshold below the floor."""
    assert compute_staleness_threshold(60.0, 2.5, 600.0) == 600.0


def test_settings_defaults() -> None:
    settings = BrainLivenessSettings()
    assert settings.enabled is True
    assert settings.watchdog_interval_sec == 60.0
    assert settings.staleness_multiplier == 2.5
    assert settings.staleness_floor_sec == 600.0


def test_settings_rejects_low_interval() -> None:
    with pytest.raises(ValueError):
        BrainLivenessSettings(watchdog_interval_sec=5.0)


def test_settings_rejects_low_multiplier() -> None:
    with pytest.raises(ValueError):
        BrainLivenessSettings(staleness_multiplier=1.0)


def test_settings_rejects_low_floor() -> None:
    with pytest.raises(ValueError):
        BrainLivenessSettings(staleness_floor_sec=10.0)


def test_settings_rejects_low_alert_rate_limit() -> None:
    with pytest.raises(ValueError):
        BrainLivenessSettings(alert_rate_limit_sec=10.0)
