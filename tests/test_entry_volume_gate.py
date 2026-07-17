"""Entry Quality Gates (volume-ratio 2026-07-15, ATR 2026-07-16,
recent-loss 2026-07-17) tests.

Verifies the pure-function gate evaluators produce correct verdicts
across the fail-open / threshold / kill-switch paths, and that
EntryVolumeGateSettings rejects invalid config. See
IMPLEMENT_ENTRY_VOLUME_GATE.md and IMPLEMENT_ENTRY_QUALITY_SELECTIVITY.md
for the evidence behind each threshold.
"""

from __future__ import annotations

import pytest

from src.config.settings import EntryVolumeGateSettings
from src.core.entry_volume_gate import (
    VERDICT_BLOCK,
    VERDICT_PASS,
    VERDICT_UNKNOWN_PASS,
    evaluate_entry_atr_gate,
    evaluate_entry_volume_gate,
    evaluate_recent_loss_gate,
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


# ── ATR gate (2026-07-16) ──────────────────────────────────────────────

def test_atr_below_threshold_blocks() -> None:
    result = evaluate_entry_atr_gate(atr_pct=0.15, min_atr_pct=0.20)
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True
    assert result.atr_pct == 0.15


def test_atr_at_or_above_threshold_passes() -> None:
    result = evaluate_entry_atr_gate(atr_pct=0.20, min_atr_pct=0.20)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False

    result_high = evaluate_entry_atr_gate(atr_pct=0.85, min_atr_pct=0.20)
    assert result_high.verdict == VERDICT_PASS
    assert result_high.would_block is False


def test_none_atr_fails_open() -> None:
    """Missing ATR data must never block a trade (same fail-open
    convention as the volume gate)."""
    result = evaluate_entry_atr_gate(atr_pct=None, min_atr_pct=0.20)
    assert result.verdict == VERDICT_UNKNOWN_PASS
    assert result.would_block is False


def test_atr_zero_threshold_is_kill_switch() -> None:
    result = evaluate_entry_atr_gate(atr_pct=0.001, min_atr_pct=0.0)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_atr_settings_defaults() -> None:
    settings = EntryVolumeGateSettings()
    assert settings.min_atr_pct == 0.20
    assert settings.atr_mode == "observe"


def test_atr_settings_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(atr_mode="block_everything")


def test_atr_settings_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(min_atr_pct=-0.1)


# ── Recent-loss gate (2026-07-17) ──────────────────────────────────────

def test_recent_loss_at_threshold_blocks() -> None:
    """Zero-tolerance default: reaching the threshold blocks, matching
    the RECENT_LOSER_COOLDOWN rule's 'do NOT re-enter' intent."""
    result = evaluate_recent_loss_gate(recent_loss_count=1, max_recent_losses=1)
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True
    assert result.recent_loss_count == 1


def test_recent_loss_below_threshold_passes() -> None:
    result = evaluate_recent_loss_gate(recent_loss_count=0, max_recent_losses=1)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_recent_loss_exceeding_threshold_blocks() -> None:
    result = evaluate_recent_loss_gate(recent_loss_count=3, max_recent_losses=1)
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True


def test_recent_loss_higher_threshold_allows_one_retry() -> None:
    """max_recent_losses=2 permits exactly one prior loss before blocking."""
    result = evaluate_recent_loss_gate(recent_loss_count=1, max_recent_losses=2)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False

    result_at_2 = evaluate_recent_loss_gate(recent_loss_count=2, max_recent_losses=2)
    assert result_at_2.verdict == VERDICT_BLOCK
    assert result_at_2.would_block is True


def test_recent_loss_zero_threshold_is_kill_switch() -> None:
    result = evaluate_recent_loss_gate(recent_loss_count=5, max_recent_losses=0)
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_recent_loss_settings_defaults() -> None:
    settings = EntryVolumeGateSettings()
    assert settings.recent_loss_enabled is True
    assert settings.recent_loss_mode == "observe"
    assert settings.recent_loss_lookback_hours == 1.0
    assert settings.max_recent_losses == 1


def test_recent_loss_settings_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(recent_loss_mode="block_everything")


def test_recent_loss_settings_rejects_negative_lookback() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(recent_loss_lookback_hours=-1.0)


def test_recent_loss_settings_rejects_negative_max_losses() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(max_recent_losses=-1)
