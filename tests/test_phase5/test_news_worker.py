"""Tests for NewsWorker."""

import pytest
from unittest.mock import AsyncMock

from src.workers.news_worker import NewsWorker


class TestNewsWorker:
    @pytest.mark.asyncio
    async def test_tick_fetches_news(self, mock_settings, test_db, mock_news_service, mock_calendar_service):
        worker = NewsWorker(mock_settings, test_db, mock_news_service, mock_calendar_service)
        await worker.tick()
        mock_news_service.fetch_latest_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_updated_every_30_ticks(self, mock_settings, test_db, mock_news_service, mock_calendar_service):
        worker = NewsWorker(mock_settings, test_db, mock_news_service, mock_calendar_service)
        for _ in range(30):
            await worker.tick()
        mock_calendar_service.get_upcoming_events.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_not_called_before_30(self, mock_settings, test_db, mock_news_service, mock_calendar_service):
        worker = NewsWorker(mock_settings, test_db, mock_news_service, mock_calendar_service)
        for _ in range(5):
            await worker.tick()
        mock_calendar_service.get_upcoming_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_news_error(self, mock_settings, test_db, mock_news_service, mock_calendar_service):
        mock_news_service.fetch_latest_news = AsyncMock(side_effect=Exception("API error"))
        worker = NewsWorker(mock_settings, test_db, mock_news_service, mock_calendar_service)
        with pytest.raises(Exception):
            await worker.tick()
