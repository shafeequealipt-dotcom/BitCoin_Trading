"""Entry Volume-Ratio Gate (2026-07-15, Phase 0) tests.

Verifies the pure-function gate evaluator produces correct verdicts
across the fail-open / threshold / kill-switch paths, and that
EntryVolumeGateSettings rejects invalid config. See
IMPLEMENT_ENTRY_VOLUME_GATE.md for the evidence behind the threshold.
"""

from __future__ import annotations

import pytest

from src.config.settings import EntryVolumeGateSettings
from src.core.entry_volume_gate import (
    VERDICT_BLOCK,
    VERDICT_PASS,
    VERDICT_UNKNOWN_PASS,
    evaluate_entry_volume_gate,
)


def test_below_threshold_blocks() -> None:
    result = evaluate_entry_volume_gate(volume_ratio=0.20, min_volume_ratio=0.30)
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True
    assert result.volume_ratio == 0.20


def test_at_or_above_threshold_passes() -> None:
    result = evaluate_entry_volume_gate(volume_ratio=0.30, min_volume_ratio=0.30)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False

    result_high = evaluate_entry_volume_gate(volume_ratio=1.5, min_volume_ratio=0.30)
    assert result_high.verdict == VERDICT_PASS
    assert result_high.would_block is False


def test_none_volume_ratio_fails_open() -> None:
    """Missing data must never block a trade — matches the state-labeler
    convention ('volume_ratio gate bypassed when input is None')."""
    result = evaluate_entry_volume_gate(volume_ratio=None, min_volume_ratio=0.30)
    assert result.verdict == VERDICT_UNKNOWN_PASS
    assert result.would_block is False


def test_zero_threshold_is_kill_switch() -> None:
    """min_volume_ratio <= 0 disables the gate even for a near-zero vr."""
    result = evaluate_entry_volume_gate(volume_ratio=0.001, min_volume_ratio=0.0)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_settings_defaults() -> None:
    settings = EntryVolumeGateSettings()
    assert settings.enabled is True
    assert settings.mode == "observe"
    assert settings.min_volume_ratio == 0.30


def test_settings_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(mode="block_everything")


def test_settings_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(min_volume_ratio=-0.1)
