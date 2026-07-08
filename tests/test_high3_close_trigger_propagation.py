"""Unit tests for HIGH-3 (close_trigger hardcoded "exchange_match").

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md HIGH-3.

Pre-fix: bybit_demo_adapter.get_last_close (line 242) hardcoded
close_trigger="exchange_match" in its return dict. The watchdog calls
get_last_close after detecting a flat position and uses the dict's
close_trigger for downstream attribution. Result: the original
sniper_p9 / callb_close / wd_emergency / time_decay_* trigger that the
caller passed to close_position was LOST in the get_last_close
roundtrip.

Fix: per-symbol close_trigger cache populated by close_position with a
60s TTL. get_last_close reads from the cache and falls back to
"exchange_match" when no entry (= genuinely exchange-initiated close
that didn't go through close_position).
"""

from __future__ import annotations

import time

import pytest


# ──────────────────────────────────────────────────────────────────────
# Group 1 — cache record/get helpers
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def adapter():
    """Build a BybitDemoPositionService with a stub client."""
    from unittest.mock import MagicMock
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService
    return BybitDemoPositionService(MagicMock(), trading_repo=None)


def test_cache_starts_empty(adapter) -> None:
    """No cache entries on construction. get returns None for any symbol."""
    assert adapter._get_cached_close_trigger("X") is None


def test_record_then_get_returns_trigger(adapter) -> None:
    """After _record_close_trigger, the same symbol returns the trigger."""
    adapter._record_close_trigger("BTCUSDT", "sniper_p9")
    assert adapter._get_cached_close_trigger("BTCUSDT") == "sniper_p9"


def test_different_symbols_isolated(adapter) -> None:
    """Recording for symbol A does not affect symbol B."""
    adapter._record_close_trigger("A", "sniper_p9")
    adapter._record_close_trigger("B", "callb_close")
    assert adapter._get_cached_close_trigger("A") == "sniper_p9"
    assert adapter._get_cached_close_trigger("B") == "callb_close"


def test_subsequent_record_overwrites(adapter) -> None:
    """If close_position fires twice for the same symbol (e.g., a re-open
    + re-close within the TTL), the latest trigger wins."""
    adapter._record_close_trigger("X", "sniper_p9")
    adapter._record_close_trigger("X", "wd_emergency")
    assert adapter._get_cached_close_trigger("X") == "wd_emergency"


def test_expired_entry_returns_none_and_prunes(adapter) -> None:
    """Entries past their TTL return None AND get removed from the dict."""
    # Manually insert a stale entry (expiry in the past)
    adapter._recent_close_triggers["X"] = ("sniper_p9", time.time() - 1.0)
    assert adapter._get_cached_close_trigger("X") is None
    # Pruned
    assert "X" not in adapter._recent_close_triggers


def test_unrelated_close_doesnt_disturb_cache(adapter) -> None:
    """Reading symbol A's cache entry does not affect symbol B."""
    adapter._record_close_trigger("A", "sniper_p9")
    adapter._record_close_trigger("B", "callb_close")
    _ = adapter._get_cached_close_trigger("A")
    assert "B" in adapter._recent_close_triggers


# ──────────────────────────────────────────────────────────────────────
# Group 2 — get_last_close fallback chain
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_last_close_returns_cached_trigger() -> None:
    """When close_position has been called recently, get_last_close
    returns the actual trigger (not the legacy "exchange_match")."""
    from unittest.mock import AsyncMock, MagicMock
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

    client = MagicMock()
    client.get = AsyncMock(return_value={
        "result": {
            "list": [{
                "avgEntryPrice": "100.0",
                "avgExitPrice": "99.5",
                "closedPnl": "-0.5",
                "qty": "1.0",
                "side": "Buy",
                "createdTime": "1715000000000",
                "updatedTime": "1715000060000",
            }],
        },
    })
    svc = BybitDemoPositionService(client, trading_repo=None)
    svc._record_close_trigger("BTCUSDT", "sniper_p9")
    result = await svc.get_last_close("BTCUSDT")
    assert result is not None
    assert result["close_trigger"] == "sniper_p9"


@pytest.mark.asyncio
async def test_get_last_close_falls_back_to_exchange_match() -> None:
    """Genuinely exchange-initiated closes (SL/TP hit on Bybit's side,
    manual UI close) don't go through close_position so no cache entry
    exists. The legacy "exchange_match" label remains correct for those."""
    from unittest.mock import AsyncMock, MagicMock
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

    client = MagicMock()
    client.get = AsyncMock(return_value={
        "result": {
            "list": [{
                "avgEntryPrice": "100.0",
                "avgExitPrice": "101.0",
                "closedPnl": "1.0",
                "qty": "1.0",
                "side": "Buy",
                "createdTime": "1715000000000",
                "updatedTime": "1715000060000",
            }],
        },
    })
    svc = BybitDemoPositionService(client, trading_repo=None)
    # No _record_close_trigger call → cache is empty
    result = await svc.get_last_close("BTCUSDT")
    assert result is not None
    assert result["close_trigger"] == "exchange_match"


@pytest.mark.asyncio
async def test_get_last_close_with_expired_cache_falls_back() -> None:
    """If the cache entry exists but has expired (TTL elapsed),
    get_last_close falls back to 'exchange_match' rather than returning
    a stale trigger."""
    from unittest.mock import AsyncMock, MagicMock
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

    client = MagicMock()
    client.get = AsyncMock(return_value={
        "result": {
            "list": [{
                "avgEntryPrice": "100.0",
                "avgExitPrice": "99.0",
                "closedPnl": "-1.0",
                "qty": "1.0",
                "side": "Buy",
                "createdTime": "1715000000000",
                "updatedTime": "1715000060000",
            }],
        },
    })
    svc = BybitDemoPositionService(client, trading_repo=None)
    # Stash an expired entry
    svc._recent_close_triggers["BTCUSDT"] = ("sniper_p9", time.time() - 1.0)
    result = await svc.get_last_close("BTCUSDT")
    assert result is not None
    assert result["close_trigger"] == "exchange_match"
