"""Definitive-fix Phase 1 — StructureCache freshness breakdown.

Adds coverage for the new ``get_freshness_breakdown`` accessor used by
``structure_worker``'s extended ``XRAY_CACHE_HEALTH`` log so operators
can see fresh-vs-stale entry counts at a glance.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from src.analysis.structure.structure_cache import StructureCache


def _stub_analysis(symbol: str = "BTCUSDT"):
    """Cheap stand-in for StructuralAnalysis — the cache only stores it."""

    class _Stub:
        setup_score = 50.0

    return _Stub()


def test_freshness_breakdown_empty_cache() -> None:
    """Empty cache reports zeros across the board."""
    cache = StructureCache(ttl_seconds=300.0)
    breakdown = cache.get_freshness_breakdown()
    assert breakdown == {"total": 0, "fresh": 0, "stale": 0}


def test_freshness_breakdown_all_fresh_default_threshold() -> None:
    """Entries within TTL count as fresh; none stale."""
    cache = StructureCache(ttl_seconds=300.0)
    cache.set("BTCUSDT", _stub_analysis())
    cache.set("ETHUSDT", _stub_analysis())
    breakdown = cache.get_freshness_breakdown()
    assert breakdown["total"] == 2
    assert breakdown["fresh"] == 2
    assert breakdown["stale"] == 0


def test_freshness_breakdown_with_stale_entries() -> None:
    """Entries past the explicit threshold count as stale."""
    cache = StructureCache(ttl_seconds=600.0)  # generous so both entries stay alive
    # Patch monotonic so the second .set() lands "earlier" → it's then stale.
    with patch("src.analysis.structure.structure_cache.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        cache.set("BTCUSDT", _stub_analysis())  # ts=0
        mock_time.monotonic.return_value = 100.0
        cache.set("ETHUSDT", _stub_analysis())  # ts=100
        # Now look 250 s into the future. Threshold = 200 s.
        mock_time.monotonic.return_value = 250.0
        breakdown = cache.get_freshness_breakdown(fresh_within_seconds=200.0)

    assert breakdown["total"] == 2
    # ETH (age 150) fresh; BTC (age 250) stale.
    assert breakdown["fresh"] == 1
    assert breakdown["stale"] == 1


def test_freshness_breakdown_threshold_smaller_than_ttl() -> None:
    """Caller may pass a tighter threshold than the cache TTL."""
    cache = StructureCache(ttl_seconds=600.0)
    with patch("src.analysis.structure.structure_cache.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        cache.set("BTCUSDT", _stub_analysis())
        mock_time.monotonic.return_value = 350.0
        # TTL window = 600 → entry still alive, but the scanner's 300s
        # window is what we care about here.
        breakdown = cache.get_freshness_breakdown(fresh_within_seconds=300.0)
    assert breakdown == {"total": 1, "fresh": 0, "stale": 1}


def test_full_sweep_per_tick_contract() -> None:
    """When batch_size >= universe, every entry should be fresh after one tick.

    This is the operator-visible contract introduced by Phase 1: a single
    tick covers the whole watch_list, so XRAY_CACHE_HEALTH must show
    fresh == size and stale == 0 immediately afterwards.
    """
    cache = StructureCache(ttl_seconds=300.0)
    universe = [f"COIN{i}USDT" for i in range(50)]
    for sym in universe:
        cache.set(sym, _stub_analysis(sym))

    breakdown = cache.get_freshness_breakdown()
    assert breakdown["total"] == 50
    assert breakdown["fresh"] == 50
    assert breakdown["stale"] == 0


def test_oldest_age_seconds_unchanged_by_new_method() -> None:
    """The new accessor doesn't perturb the existing get_oldest_entry_age_seconds."""
    cache = StructureCache(ttl_seconds=300.0)
    cache.set("BTCUSDT", _stub_analysis())
    time.sleep(0.01)
    age1 = cache.get_oldest_entry_age_seconds()
    _ = cache.get_freshness_breakdown()
    age2 = cache.get_oldest_entry_age_seconds()
    assert age2 >= age1  # monotonic, unaffected by the read
