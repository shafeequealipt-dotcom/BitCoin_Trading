"""Trade-Activity Watchdog logic tests (2026-08-18).

Covers the verdict matrix, severity assignment, precedence between the two
signals, fail-open behaviour, and the kill switches. See
src/core/trade_activity.py for the outage that motivated this module.
"""

from __future__ import annotations

import pytest

from src.config.settings import TradeActivitySettings
from src.core.trade_activity import (
    VERDICT_BRAIN_FAILING,
    VERDICT_HEALTHY,
    VERDICT_NO_TRADES,
    VERDICT_UNKNOWN,
    evaluate_trade_activity,
)

HOUR = 3600.0


def _ev(fails=None, age=None, fail_thr=3, no_trade_s=12 * HOUR):
    return evaluate_trade_activity(
        consecutive_failures=fails,
        seconds_since_last_trade=age,
        failure_threshold=fail_thr,
        no_trade_threshold_seconds=no_trade_s,
    )


# ── The signal that would have caught the 2026-08-16 outage ────────────

def test_consecutive_failures_at_threshold_is_critical() -> None:
    r = _ev(fails=3, age=1 * HOUR)
    assert r.verdict == VERDICT_BRAIN_FAILING
    assert r.should_alert is True
    assert r.severity == "critical"
    assert r.reason == "consecutive_brain_failures_threshold_reached"


def test_consecutive_failures_below_threshold_is_healthy() -> None:
    r = _ev(fails=2, age=1 * HOUR)
    assert r.verdict == VERDICT_HEALTHY
    assert r.should_alert is False


def test_the_actual_outage_shape_alerts() -> None:
    """ZenMux 403: brain looping and failing every cycle for days, so the
    failure streak is huge AND the dry spell is long. Must report the CAUSE
    (provider failing), not the downstream symptom (no trades)."""
    r = _ev(fails=700, age=60 * HOUR)
    assert r.verdict == VERDICT_BRAIN_FAILING
    assert r.severity == "critical"


# ── The broad backstop ─────────────────────────────────────────────────

def test_long_dry_spell_without_api_errors_is_warning() -> None:
    r = _ev(fails=0, age=13 * HOUR)
    assert r.verdict == VERDICT_NO_TRADES
    assert r.should_alert is True
    assert r.severity == "warning"
    assert r.reason == "no_trade_entry_within_threshold"


def test_short_dry_spell_is_healthy() -> None:
    """Quiet markets and by-design entry gates must not page anyone."""
    r = _ev(fails=0, age=3 * HOUR)
    assert r.verdict == VERDICT_HEALTHY
    assert r.should_alert is False


def test_failure_signal_takes_precedence_over_dry_spell() -> None:
    """Both tripped -> report the cause, not the symptom."""
    r = _ev(fails=5, age=99 * HOUR)
    assert r.verdict == VERDICT_BRAIN_FAILING


# ── Fail-open: missing data must never manufacture an alert ────────────

def test_both_signals_missing_is_unknown_and_silent() -> None:
    r = _ev(fails=None, age=None)
    assert r.verdict == VERDICT_UNKNOWN
    assert r.should_alert is False
    assert r.reason == "no_signals_available"


def test_missing_failure_stat_still_evaluates_dry_spell() -> None:
    r = _ev(fails=None, age=13 * HOUR)
    assert r.verdict == VERDICT_NO_TRADES


def test_no_trade_history_does_not_alert() -> None:
    """A fresh install has no trade rows; that is not an outage."""
    r = _ev(fails=0, age=None)
    assert r.verdict == VERDICT_HEALTHY
    assert r.should_alert is False


# ── Kill switches, independently ───────────────────────────────────────

def test_zero_failure_threshold_disables_only_that_check() -> None:
    r = _ev(fails=9999, age=1 * HOUR, fail_thr=0)
    assert r.verdict == VERDICT_HEALTHY

    r2 = _ev(fails=9999, age=13 * HOUR, fail_thr=0)
    assert r2.verdict == VERDICT_NO_TRADES  # dry-spell check still live


def test_zero_no_trade_threshold_disables_only_that_check() -> None:
    r = _ev(fails=0, age=9999 * HOUR, no_trade_s=0)
    assert r.verdict == VERDICT_HEALTHY

    r2 = _ev(fails=5, age=9999 * HOUR, no_trade_s=0)
    assert r2.verdict == VERDICT_BRAIN_FAILING  # failure check still live


def test_both_thresholds_zero_is_full_kill_switch() -> None:
    r = _ev(fails=9999, age=9999 * HOUR, fail_thr=0, no_trade_s=0)
    assert r.verdict == VERDICT_HEALTHY
    assert r.should_alert is False


# ── Settings ───────────────────────────────────────────────────────────

def test_settings_defaults() -> None:
    s = TradeActivitySettings()
    assert s.enabled is True
    assert s.watchdog_interval_sec == 60.0
    assert s.max_consecutive_failures == 3
    assert s.no_trade_alert_hours == 12.0
    assert s.alert_rate_limit_sec == 3600.0


def test_result_carries_thresholds_for_logging() -> None:
    r = _ev(fails=1, age=2 * HOUR)
    assert r.failure_threshold == 3
    assert r.no_trade_threshold_seconds == pytest.approx(12 * HOUR)
