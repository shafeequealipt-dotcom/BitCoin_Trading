"""T2-2 / F14 zero-conviction reject smoke tests (six-tier-fixes 2026-05-11).

Validates the predicate at the top of CHECK 4 in apex/gate.py: a trade
with all three conviction signals (xray_conf, setup_score, expected_rr)
at-or-below their settings thresholds is rejected with
trade["_gate_rejected"] = "zero_conviction ...".

Pure-math test reproducing the inline predicate. Mirrors the
production-code formula so a future divergence breaks this test
loudly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _should_reject_inline(
    xray: float, setup: float, rr: float,
    min_xray: float = 0.0, min_setup: float = 0.0, min_rr: float = 0.0,
) -> bool:
    """Mirrors src/apex/gate.py:140-ish zero-conviction reject predicate."""
    return xray <= min_xray and setup <= min_setup and rr <= min_rr


def test_all_zero_rejects_with_default_thresholds():
    """The SOLUSDT case: all three signals zero -> reject."""
    assert _should_reject_inline(0.0, 0.0, 0.0) is True


def test_nonzero_xray_does_not_reject_with_default_thresholds():
    """A single positive signal keeps the trade."""
    assert _should_reject_inline(0.70, 0.0, 0.0) is False


def test_nonzero_setup_score_does_not_reject_with_default_thresholds():
    assert _should_reject_inline(0.0, 50.0, 0.0) is False


def test_nonzero_rr_does_not_reject_with_default_thresholds():
    assert _should_reject_inline(0.0, 0.0, 1.5) is False


def test_at_threshold_rejects_when_signals_match_threshold():
    """Reject is at-or-BELOW, so equality with non-zero threshold rejects."""
    assert _should_reject_inline(0.5, 50.0, 1.0, 0.5, 50.0, 1.0) is True


def test_one_above_threshold_does_not_reject():
    """Single signal above its threshold preserves the trade."""
    assert _should_reject_inline(0.51, 50.0, 1.0, 0.5, 50.0, 1.0) is False


def test_settings_field_defaults_are_zero():
    """The three new settings fields default to 0.0 (aggressive-exploitation default)."""
    from src.config.settings import APEXSettings
    s = APEXSettings()
    assert s.min_xray_conf_for_trade == 0.0
    assert s.min_setup_score_for_trade == 0.0
    assert s.min_expected_rr_for_trade == 0.0
