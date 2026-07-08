"""Tests for ContextRepository."""

import pytest
from src.database.repositories.context_repo import ContextRepository


class TestPreferences:
    @pytest.mark.asyncio
    async def test_set_and_get(self, test_db):
        repo = ContextRepository(test_db)
        await repo.set_preference("theme", "dark")
        val = await repo.get_preference("theme")
        assert val == "dark"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, test_db):
        repo = ContextRepository(test_db)
        val = await repo.get_preference("nonexistent")
        assert val is None

    @pytest.mark.asyncio
    async def test_get_all(self, test_db):
        repo = ContextRepository(test_db)
        await repo.set_preference("a", "1")
        await repo.set_preference("b", "2")
        all_prefs = await repo.get_all_preferences()
        assert all_prefs["a"] == "1"
        assert all_prefs["b"] == "2"

    @pytest.mark.asyncio
    async def test_delete(self, test_db):
        repo = ContextRepository(test_db)
        await repo.set_preference("temp", "val")
        await repo.delete_preference("temp")
        assert await repo.get_preference("temp") is None

    @pytest.mark.asyncio
    async def test_upsert(self, test_db):
        repo = ContextRepository(test_db)
        await repo.set_preference("key", "v1")
        await repo.set_preference("key", "v2")
        assert await repo.get_preference("key") == "v2"


class TestWatchlists:
    @pytest.mark.asyncio
    async def test_create_and_get(self, test_db):
        repo = ContextRepository(test_db)
        await repo.create_watchlist("main", ["BTCUSDT", "ETHUSDT"])
        symbols = await repo.get_watchlist("main")
        assert symbols == ["BTCUSDT", "ETHUSDT"]

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, test_db):
        repo = ContextRepository(test_db)
        assert await repo.get_watchlist("fake") is None

    @pytest.mark.asyncio
    async def test_get_all(self, test_db):
        repo = ContextRepository(test_db)
        await repo.create_watchlist("a", ["BTC"])
        await repo.create_watchlist("b", ["ETH"])
        all_wl = await repo.get_all_watchlists()
        assert len(all_wl) == 2

    @pytest.mark.asyncio
    async def test_update(self, test_db):
        repo = ContextRepository(test_db)
        await repo.create_watchlist("wl", ["BTC"])
        await repo.update_watchlist("wl", ["BTC", "ETH"])
        assert await repo.get_watchlist("wl") == ["BTC", "ETH"]

    @pytest.mark.asyncio
    async def test_delete(self, test_db):
        repo = ContextRepository(test_db)
        await repo.create_watchlist("del", ["X"])
        await repo.delete_watchlist("del")
        assert await repo.get_watchlist("del") is None


class TestStrategies:
    @pytest.mark.asyncio
    async def test_set_and_get(self, test_db):
        repo = ContextRepository(test_db)
        await repo.set_active_strategy("momentum", "BTCUSDT", True, {"period": 14})
        strats = await repo.get_active_strategies("BTCUSDT")
        assert len(strats) == 1
        assert strats[0]["strategy_name"] == "momentum"

    @pytest.mark.asyncio
    async def test_disable(self, test_db):
        repo = ContextRepository(test_db)
        await repo.set_active_strategy("scalp", "ETHUSDT")
        await repo.disable_strategy("scalp", "ETHUSDT")
        strats = await repo.get_active_strategies("ETHUSDT")
        assert len(strats) == 0


class TestSessionLog:
    @pytest.mark.asyncio
    async def test_log_and_query(self, test_db):
        repo = ContextRepository(test_db)
        await repo.log_session_event("startup", "System started", {"mode": "paper"})
        await repo.log_session_event("trade", "Order placed")

        all_events = await repo.get_session_log()
        assert len(all_events) == 2

        startups = await repo.get_session_log("startup")
        assert len(startups) == 1
