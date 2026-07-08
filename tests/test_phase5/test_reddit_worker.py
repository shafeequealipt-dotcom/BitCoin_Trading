"""Tests for RedditWorker."""

import pytest

from src.workers.reddit_worker import RedditWorker


class TestRedditWorker:
    @pytest.mark.asyncio
    async def test_tick_scans_subreddits(self, mock_settings, test_db, mock_reddit_service):
        worker = RedditWorker(mock_settings, test_db, mock_reddit_service)
        await worker.tick()
        mock_reddit_service.scan_subreddits.assert_called_once()
