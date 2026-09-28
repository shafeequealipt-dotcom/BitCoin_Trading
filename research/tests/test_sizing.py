"""Unit tests for research/engine/sizing.py (Phase C)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.engine.sizing import Position, RiskLimits, can_open, position_size


def test_position_size_scales_inversely_with_stop_distance():
    """A wider stop must produce a SMALLER position, for the SAME dollar
    risk -- this is the entire point of fixed-fractional sizing (F10: the
    old system sized by LLM "conviction", not by the stop it itself set)."""
    notional_tight, risk_tight = position_size(10_000, stop_distance_pct=1.0, risk_pct_per_trade=0.5)
    notional_wide, risk_wide = position_size(10_000, stop_distance_pct=4.0, risk_pct_per_trade=0.5)
    assert risk_tight == pytest.approx(risk_wide)  # same $ risk regardless of stop width
    assert notional_tight == pytest.approx(notional_wide * 4)  # 4x wider stop -> 1/4 the notional


def test_position_size_risk_usd_is_exact():
    notional, risk = position_size(10_000, stop_distance_pct=2.0, risk_pct_per_trade=0.5)
    assert risk == pytest.approx(50.0)          # 0.5% of 10,000
    assert notional == pytest.approx(2500.0)    # 50 / (2/100)


def test_position_size_rejects_zero_or_negative_stop():
    with pytest.raises(ValueError):
        position_size(10_000, stop_distance_pct=0, risk_pct_per_trade=0.5)
    with pytest.raises(ValueError):
        position_size(10_000, stop_distance_pct=-1, risk_pct_per_trade=0.5)


def test_can_open_blocks_on_max_concurrent_positions():
    limits = RiskLimits(max_concurrent_positions=2)
    open_pos = [Position("A", 1, 100, 1000, 50, 99), Position("B", 1, 100, 1000, 50, 99)]
    ok, reason = can_open(10_000, open_pos, 50, limits, 0, 10_000)
    assert not ok and reason == "max_concurrent_positions"


def test_can_open_blocks_on_total_open_risk():
    limits = RiskLimits(max_total_open_risk_pct=1.0, max_concurrent_positions=99)
    # 90 + 20 new = 110 -> 1.1% of 10,000, over the 1.0% cap
    open_pos = [Position("A", 1, 100, 1000, 90, 99)]
    ok, reason = can_open(10_000, open_pos, 20, limits, 0, 10_000)
    assert not ok and reason == "max_total_open_risk_pct"


def test_can_open_blocks_on_daily_loss_limit():
    limits = RiskLimits(daily_loss_limit_pct=5.0)
    ok, reason = can_open(10_000, [], 10, limits, realized_pnl_today_usd=-600, peak_equity_usd=10_000)
    assert not ok and reason == "daily_loss_limit_pct"


def test_can_open_allows_a_positive_day_regardless_of_size():
    limits = RiskLimits(daily_loss_limit_pct=5.0)
    ok, _ = can_open(10_000, [], 10, limits, realized_pnl_today_usd=+5000, peak_equity_usd=10_000)
    assert ok


def test_can_open_blocks_on_total_drawdown():
    limits = RiskLimits(total_drawdown_limit_pct=15.0)
    ok, reason = can_open(8400, [], 10, limits, 0, peak_equity_usd=10_000)  # 16% down from peak
    assert not ok and reason == "total_drawdown_limit_pct"


def test_can_open_allows_when_every_limit_clears():
    limits = RiskLimits()
    ok, reason = can_open(10_000, [], 50, limits, realized_pnl_today_usd=0, peak_equity_usd=10_000)
    assert ok and reason == ""
