"""Tests for data cleanup."""

import pytest
from datetime import timedelta

from src.core.utils import now_utc
from src.database.cleanup import cleanup_old_data


class TestCleanup:
    @pytest.mark.asyncio
    async def test_deletes_old_data(self, test_db):
        old_time = (now_utc() - timedelta(days=60)).isoformat()
        await test_db.execute(
            "INSERT INTO news_articles (id, headline, source, sentiment_score, symbols, published_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("old1", "Old news", "src", 0, "[]", old_time),
        )
        result = await cleanup_old_data(test_db)
        assert result.get("news_articles", 0) == 1

    @pytest.mark.asyncio
    async def test_keeps_recent(self, test_db):
        recent = now_utc().isoformat()
        await test_db.execute(
            "INSERT INTO news_articles (id, headline, source, sentiment_score, symbols, published_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("new1", "Fresh", "src", 0, "[]", recent),
        )
        result = await cleanup_old_data(test_db)
        assert result.get("news_articles", 0) == 0

        rows = await test_db.fetch_all("SELECT * FROM news_articles")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_returns_counts(self, test_db):
        result = await cleanup_old_data(test_db)
        assert isinstance(result, dict)


class TestMigrations:
    @pytest.mark.asyncio
    async def test_all_tables_created(self, test_db):
        rows = await test_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        table_names = {r["name"] for r in rows}
        expected = {
            "klines", "ticker_cache", "orderbook_snapshots",
            "news_articles", "reddit_posts", "aggregated_sentiment",
            "economic_calendar", "fear_greed_index", "funding_rates", "open_interest",
            "orders", "positions", "trade_history", "account_snapshots",
            "signals", "schema_version",
            "strategy_performance", "signal_accuracy", "pattern_log",
            "brain_decisions", "user_preferences", "watchlists",
            "active_strategies", "session_log",
        }
        missing = expected - table_names
        assert not missing, f"Missing tables: {missing}"

    @pytest.mark.asyncio
    async def test_idempotent(self, test_db):
        """Running migrations twice should not error."""
        from src.database.migrations import run_migrations
        await run_migrations(test_db)  # Second run
        rows = await test_db.fetch_all("SELECT * FROM schema_version")
        assert len(rows) == 1
