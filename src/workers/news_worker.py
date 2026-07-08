"""News worker: polls Finnhub for crypto news and economic calendar."""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import WorkerTier
from src.database.connection import DatabaseManager
from src.intelligence.news.calendar_service import CalendarService
from src.intelligence.news.news_service import NewsService
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class NewsWorker(BaseWorker):
    """Fetches crypto news and periodically updates economic calendar.

    Args:
        settings: Application settings.
        db: Database manager.
        news_service: NewsService for fetching and scoring news.
        calendar_service: CalendarService for economic events (optional).
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1A

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        news_service: NewsService,
        calendar_service: CalendarService | None = None,
    ) -> None:
        super().__init__(
            name="news_worker",
            interval_seconds=float(settings.workers.news_interval),
            settings=settings,
            db=db,
        )
        self.news_service = news_service
        self.calendar_service = calendar_service
        self._calendar_tick_count = 0

    async def tick(self) -> None:
        """Fetch latest news and periodically update calendar."""
        # Phase 7 (post-Layer-1 fix): tick wall-clock for el= field on the
        # NEWS_TICK_SUMMARY log. The detailed funnel breakdown is emitted
        # inside ``news_service.fetch_latest_news`` via ``FINNHUB_COVERAGE``.
        t0 = time.monotonic()
        calendar_updated = False
        articles = await self.news_service.fetch_latest_news()

        # Update calendar every 30 ticks
        self._calendar_tick_count += 1
        if self.calendar_service and self._calendar_tick_count >= 30:
            try:
                events = await self.calendar_service.get_upcoming_events()
                log.info("News worker: updated economic calendar ({n} events)", n=len(events))
                calendar_updated = True
            except Exception as e:
                log.warning("Calendar update failed: {err}", err=str(e))
            self._calendar_tick_count = 0

        el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"NEWS_TICK_SUMMARY | new={len(articles)} "
            f"calendar_updated={'Y' if calendar_updated else 'N'} "
            f"el={el_ms:.0f}ms | {ctx()}"
        )
        # Legacy lines preserved for grep compatibility.
        log.info(f"NEWS_FETCH | total={len(articles)} | {ctx()}")
        log.info("News worker: fetched {n} new articles", n=len(articles))
