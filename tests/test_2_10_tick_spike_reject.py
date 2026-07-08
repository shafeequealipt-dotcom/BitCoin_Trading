"""Issue 2.10 (2026-06-07) — preventive anomalous-tick rejection in TickerCacheBuffer."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from src.workers.ticker_cache_buffer import TickerCacheBuffer, _SPIKE_MAX_CONSECUTIVE


def _buf():
    return TickerCacheBuffer(MagicMock(), spike_reject_pct=0.15)


def _tk(sym, px):
    return SimpleNamespace(symbol=sym, last_price=px)


def test_outlier_tick_is_held_normal_passes():
    b = _buf()
    b.put(_tk("X", 100.0))          # baseline
    assert b.get("X").last_price == 100.0
    b.put(_tk("X", 105.0))          # +5% normal -> accepted
    assert b.get("X").last_price == 105.0
    b.put(_tk("X", 200.0))          # +90% outlier -> held
    assert b.get("X").last_price == 105.0
    assert b.stats()["spike_reject_count"] == 1


def test_sustained_move_accepted_after_streak():
    b = _buf()
    b.put(_tk("X", 100.0))
    for px in (200.0, 201.0):       # 2 consecutive rejects (streak < max)
        b.put(_tk("X", px))
    assert b.get("X").last_price == 100.0
    b.put(_tk("X", 202.0))          # streak hits max -> accepted as new baseline
    assert b.get("X").last_price == 202.0


def test_disabled_when_threshold_zero():
    b = TickerCacheBuffer(MagicMock(), spike_reject_pct=0.0)
    b.put(_tk("X", 100.0))
    b.put(_tk("X", 999.0))          # no guard -> passes through
    assert b.get("X").last_price == 999.0
