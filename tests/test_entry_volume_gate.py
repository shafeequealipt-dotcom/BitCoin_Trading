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
    evaluate_min_move_gate,
    evaluate_recent_loss_gate,
    evaluate_symbol_circuit_breaker_gate,
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


# ── Symbol circuit-breaker gate (2026-08-02) ──────────────────────────


def test_symbol_breaker_dollar_threshold_blocks() -> None:
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-10.0, loss_count=1,
        max_cumulative_loss_usd=10.0, max_loss_count=3,
    )
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True
    assert result.reason == "cumulative_loss_threshold_reached"


def test_symbol_breaker_count_threshold_blocks() -> None:
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-2.0, loss_count=3,
        max_cumulative_loss_usd=10.0, max_loss_count=3,
    )
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True
    assert result.reason == "loss_count_threshold_reached"


def test_symbol_breaker_both_thresholds_blocks_with_combined_reason() -> None:
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-15.0, loss_count=5,
        max_cumulative_loss_usd=10.0, max_loss_count=3,
    )
    assert result.verdict == VERDICT_BLOCK
    assert result.reason == "cumulative_loss_and_count_threshold_reached"


def test_symbol_breaker_below_both_thresholds_passes() -> None:
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-5.0, loss_count=2,
        max_cumulative_loss_usd=10.0, max_loss_count=3,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False
    assert result.reason == "symbol_loss_history_ok"


def test_symbol_breaker_no_losses_passes() -> None:
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=0.0, loss_count=0,
        max_cumulative_loss_usd=10.0, max_loss_count=3,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_symbol_breaker_dollar_check_alone_disabled() -> None:
    """max_cumulative_loss_usd <= 0 disables ONLY the dollar check —
    the count check still applies independently."""
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-1000.0, loss_count=1,
        max_cumulative_loss_usd=0.0, max_loss_count=3,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False

    result_count_trips = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-1000.0, loss_count=3,
        max_cumulative_loss_usd=0.0, max_loss_count=3,
    )
    assert result_count_trips.verdict == VERDICT_BLOCK
    assert result_count_trips.reason == "loss_count_threshold_reached"


def test_symbol_breaker_count_check_alone_disabled() -> None:
    """max_loss_count <= 0 disables ONLY the count check — the dollar
    check still applies independently."""
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-2.0, loss_count=1000,
        max_cumulative_loss_usd=10.0, max_loss_count=0,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_symbol_breaker_both_thresholds_zero_is_full_kill_switch() -> None:
    result = evaluate_symbol_circuit_breaker_gate(
        cumulative_loss_usd=-1000.0, loss_count=1000,
        max_cumulative_loss_usd=0.0, max_loss_count=0,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False
    assert result.reason == "gate_disabled_thresholds_zero"


def test_symbol_breaker_settings_defaults() -> None:
    settings = EntryVolumeGateSettings()
    assert settings.symbol_breaker_enabled is True
    assert settings.symbol_breaker_mode == "observe"
    assert settings.symbol_breaker_lookback_hours == 48.0
    assert settings.symbol_breaker_max_cumulative_loss_usd == 10.0
    assert settings.symbol_breaker_max_loss_count == 3


def test_symbol_breaker_settings_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(symbol_breaker_mode="block_everything")


def test_symbol_breaker_settings_rejects_negative_lookback() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(symbol_breaker_lookback_hours=-1.0)


def test_symbol_breaker_settings_rejects_negative_max_loss_count() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(symbol_breaker_max_loss_count=-1)


# ── Minimum-expected-move fee gate (2026-08-02) ───────────────────────


def test_min_move_below_required_blocks() -> None:
    # fee=0.24%, multiple=2.5 -> required 0.60%; an arm of 0.30% is below it
    # (that is a ~0.20%-ATR coin: it cannot lock profit above its own cost).
    result = evaluate_min_move_gate(
        expected_capture_pct=0.30, round_trip_fee_pct=0.24, min_fee_multiple=2.5,
    )
    assert result.verdict == VERDICT_BLOCK
    assert result.would_block is True
    assert result.reason == "capture_below_fee_multiple"
    assert abs(result.required_pct - 0.60) < 1e-9


def test_min_move_at_or_above_required_passes() -> None:
    # arm of 0.60% == required 0.60% (a 0.40%-ATR coin at arm_r=1.5): boundary passes
    result = evaluate_min_move_gate(
        expected_capture_pct=0.60, round_trip_fee_pct=0.24, min_fee_multiple=2.5,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False
    assert result.reason == "capture_clears_fee"


def test_min_move_none_capture_fails_open() -> None:
    """ATR unavailable -> cannot compute the arm -> must not block."""
    result = evaluate_min_move_gate(
        expected_capture_pct=None, round_trip_fee_pct=0.24, min_fee_multiple=2.5,
    )
    assert result.verdict == VERDICT_UNKNOWN_PASS
    assert result.would_block is False
    assert result.reason == "expected_capture_unavailable"


def test_min_move_zero_multiple_is_kill_switch() -> None:
    result = evaluate_min_move_gate(
        expected_capture_pct=0.01, round_trip_fee_pct=0.24, min_fee_multiple=0,
    )
    assert result.verdict == VERDICT_PASS
    assert result.would_block is False


def test_min_move_settings_defaults() -> None:
    settings = EntryVolumeGateSettings()
    assert settings.min_move_enabled is True
    assert settings.min_move_mode == "observe"
    assert settings.min_move_fee_multiple == 2.5


def test_min_move_settings_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(min_move_mode="block_everything")


def test_min_move_settings_rejects_negative_multiple() -> None:
    with pytest.raises(ValueError):
        EntryVolumeGateSettings(min_move_fee_multiple=-1.0)
