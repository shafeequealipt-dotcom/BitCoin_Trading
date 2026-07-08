"""Tests for CleanupWorker."""

import pytest

from src.core.utils import now_utc
from src.database.repositories.news_repo import NewsRepository
from src.core.types import NewsArticle
from src.workers.cleanup_worker import CleanupWorker


class TestCleanupWorker:
    @pytest.mark.asyncio
    async def test_tick_runs_without_error(self, mock_settings, test_db):
        worker = CleanupWorker(mock_settings, test_db)
        await worker.tick()  # Should not raise

    @pytest.mark.asyncio
    async def test_deletes_old_data(self, mock_settings, test_db):
        """Insert old data and verify cleanup removes it."""
        # Insert a very old article
        from datetime import timedelta
        old_time = (now_utc() - timedelta(days=60)).isoformat()
        await test_db.execute(
            "INSERT INTO news_articles (id, headline, source, url, summary, sentiment_score, symbols, category, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old_1", "Old news", "src", "url", "", 0.0, "[]", "crypto", old_time, old_time),
        )

        # Verify it exists
        rows = await test_db.fetch_all("SELECT * FROM news_articles")
        assert len(rows) == 1

        # Run cleanup
        worker = CleanupWorker(mock_settings, test_db)
        await worker.tick()

        # Old data should be deleted (retention for news = 30 days)
        rows = await test_db.fetch_all("SELECT * FROM news_articles")
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_keeps_recent_data(self, mock_settings, test_db):
        """Recent data should not be deleted."""
        recent_time = now_utc().isoformat()
        await test_db.execute(
            "INSERT INTO news_articles (id, headline, source, url, summary, sentiment_score, symbols, category, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("new_1", "Fresh news", "src", "url", "", 0.0, "[]", "crypto", recent_time, recent_time),
        )

        worker = CleanupWorker(mock_settings, test_db)
        await worker.tick()

        rows = await test_db.fetch_all("SELECT * FROM news_articles")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_vacuum_once_per_day(self, mock_settings, test_db):
        worker = CleanupWorker(mock_settings, test_db)
        await worker.tick()  # First tick: vacuums
        first_date = worker._last_vacuum_date
        assert first_date != ""

        await worker.tick()  # Second tick same day: should not vacuum again
        # No error, and date stays the same
        assert worker._last_vacuum_date == first_date
