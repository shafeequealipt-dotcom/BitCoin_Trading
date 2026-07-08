"""Tests for ObservabilitySettings — Layer 1 restructure Phase 1."""

import pytest

from src.config.settings import ObservabilitySettings, _build_observability


def test_defaults() -> None:
    s = ObservabilitySettings()
    assert s.cycle_tracker_history == 100
    assert s.cycle_metrics_flush_seconds == 3600
    assert s.log_tick_done_at_info is True


def test_history_must_be_positive() -> None:
    with pytest.raises(ValueError, match="cycle_tracker_history"):
        ObservabilitySettings(cycle_tracker_history=0)


def test_flush_min_enforced() -> None:
    with pytest.raises(ValueError, match="cycle_metrics_flush_seconds"):
        ObservabilitySettings(cycle_metrics_flush_seconds=30)


def test_builder_uses_toml_overrides() -> None:
    s = _build_observability({
        "cycle_tracker_history": 50,
        "cycle_metrics_flush_seconds": 1800,
        "log_tick_done_at_info": False,
    })
    assert s.cycle_tracker_history == 50
    assert s.cycle_metrics_flush_seconds == 1800
    assert s.log_tick_done_at_info is False


def test_builder_falls_back_to_defaults() -> None:
    s = _build_observability({})
    assert s.cycle_tracker_history == 100
    assert s.cycle_metrics_flush_seconds == 3600
