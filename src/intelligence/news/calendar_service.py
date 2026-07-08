"""Economic calendar service: upcoming events, high-impact filtering."""

from datetime import datetime, timedelta, timezone

from src.core.decorators import timed
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.news_repo import NewsRepository
from src.intelligence.news.finnhub_client import FinnhubClient

log = get_logger("intelligence")


class CalendarService:
    """Service for economic calendar events.

    Args:
        finnhub_client: Finnhub API client.
        db: Database manager.
    """

    def __init__(self, finnhub_client: FinnhubClient, db: DatabaseManager) -> None:
        self._finnhub = finnhub_client
        self._db = db
        self._news_repo = NewsRepository(db)

    @timed
    async def get_upcoming_events(self, days: int = 7) -> list[dict]:
        """Fetch upcoming economic events, filtered by impact.

        Args:
            days: How many days ahead to look.

        Returns:
            List of event dicts sorted by event_time.
        """
        today = now_utc()
        from_date = today.strftime("%Y-%m-%d")
        to_date = (today + timedelta(days=days)).strftime("%Y-%m-%d")

        raw_events = await self._finnhub.get_economic_calendar(from_date, to_date)

        events = []
        for raw in raw_events:
            impact = raw.get("impact", "low")
            if impact not in ("high", "medium"):
                continue

            event = {
                "event_name": raw.get("event", ""),
                "country": raw.get("country", ""),
                "impact": impact,
                "actual": str(raw.get("actual", "")),
                "estimate": str(raw.get("estimate", "")),
                "previous": str(raw.get("prev", "")),
                "event_time": raw.get("time", ""),
            }

            await self._news_repo.save_calendar_event(event)
            events.append(event)

        events.sort(key=lambda e: e.get("event_time", ""))
        log.info("Fetched {n} upcoming economic events", n=len(events))
        return events

    @timed
    async def get_high_impact_today(self) -> list[dict]:
        """Get today's high-impact economic events.

        Returns:
            List of high-impact event dicts for today.
        """
        today = now_utc().strftime("%Y-%m-%d")
        events = await self.get_upcoming_events(days=1)
        return [
            e for e in events
            if e.get("impact") == "high" and e.get("event_time", "").startswith(today)
        ]

    @timed
    async def get_next_event(self) -> dict | None:
        """Get the next upcoming economic event.

        Returns:
            Event dict or None.
        """
        events = await self.get_upcoming_events(days=3)
        current_time = now_utc().isoformat()
        for event in events:
            if event.get("event_time", "") >= current_time:
                return event
        return None
