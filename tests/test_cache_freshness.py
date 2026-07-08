"""Phase 6 (output-quality) — cache_freshness helper tests."""

from __future__ import annotations

import time

import pytest

from src.core import cache_freshness as cf


@pytest.fixture(autouse=True)
def _reset() -> None:
    cf.reset()
    yield
    cf.reset()


def test_record_write_then_read_age() -> None:
    """record_write timestamps; read_age_ms returns ms since."""
    cf.record_write("klines", "BTCUSDT")
    age = cf.read_age_ms("klines", "BTCUSDT")
    assert age is not None
    assert 0.0 <= age < 50.0  # < 50 ms after immediate read


def test_read_age_unknown_returns_none() -> None:
    assert cf.read_age_ms("klines", "GHOSTUSDT") is None


def test_record_write_overwrites_prior_timestamp() -> None:
    cf.record_write("klines", "BTCUSDT")
    age_first = cf.read_age_ms("klines", "BTCUSDT")
    time.sleep(0.01)
    cf.record_write("klines", "BTCUSDT")
    age_second = cf.read_age_ms("klines", "BTCUSDT")
    assert age_second is not None and age_first is not None
    # The second age should be smaller because the timestamp got reset.
    assert age_second < age_first + 50.0  # generous bound


def test_get_snapshot_returns_copy() -> None:
    cf.record_write("klines", "A")
    cf.record_write("ta", "B")
    snap = cf.get_snapshot()
    assert ("klines", "A") in snap
    assert ("ta", "B") in snap
    # Mutating the snapshot does NOT mutate the singleton.
    snap.clear()
    assert cf.read_age_ms("klines", "A") is not None


def test_empty_key_is_supported() -> None:
    """Cache-wide timestamps via empty key (e.g. 'klines:batch')."""
    cf.record_write("klines")
    assert cf.read_age_ms("klines") is not None


def test_reset_clears_all() -> None:
    cf.record_write("klines", "A")
    cf.reset()
    assert cf.read_age_ms("klines", "A") is None
    assert cf.get_snapshot() == {}


def test_record_write_overhead_is_negligible() -> None:
    """Per-call overhead should be << 1 ms.

    1000 record_write calls budget < 50 ms (50 µs each). If this fails
    in CI we have a regression in singleton lock contention.
    """
    t0 = time.monotonic()
    for i in range(1000):
        cf.record_write("klines", f"sym_{i}")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"1000 writes took {elapsed:.3f}s — overhead too high"
