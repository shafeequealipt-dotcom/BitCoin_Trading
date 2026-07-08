"""Tests for CalendarService: event filtering, high-impact events."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.intelligence.news.calendar_service import CalendarService


class TestCalendarService:
    @pytest.mark.asyncio
    async def test_get_upcoming_events(self, test_db, mock_finnhub_calendar_response):
        finnhub = MagicMock()
        finnhub.get_economic_calendar = AsyncMock(return_value=mock_finnhub_calendar_response)

        svc = CalendarService(finnhub, test_db)
        events = await svc.get_upcoming_events(days=7)

        # Should filter out "low" impact
        assert len(events) == 2
        assert all(e["impact"] in ("high", "medium") for e in events)

    @pytest.mark.asyncio
    async def test_events_sorted_by_time(self, test_db, mock_finnhub_calendar_response):
        finnhub = MagicMock()
        finnhub.get_economic_calendar = AsyncMock(return_value=mock_finnhub_calendar_response)

        svc = CalendarService(finnhub, test_db)
        events = await svc.get_upcoming_events()

        times = [e["event_time"] for e in events]
        assert times == sorted(times)

    @pytest.mark.asyncio
    async def test_events_persisted(self, test_db, mock_finnhub_calendar_response):
        finnhub = MagicMock()
        finnhub.get_economic_calendar = AsyncMock(return_value=mock_finnhub_calendar_response)

        svc = CalendarService(finnhub, test_db)
        await svc.get_upcoming_events()

        rows = await test_db.fetch_all("SELECT * FROM economic_calendar")
        assert len(rows) == 2
