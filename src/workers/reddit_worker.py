"""Reddit worker: scans configured subreddits for sentiment data."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.intelligence.sentiment.reddit_service import RedditService
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class RedditWorker(BaseWorker):
    """Scans Reddit subreddits for crypto sentiment.

    Args:
        settings: Application settings.
        db: Database manager.
        reddit_service: RedditService for scanning and scoring.
    """

    def __init__(self, settings: Settings, db: DatabaseManager, reddit_service: RedditService) -> None:
        super().__init__(
            name="reddit_worker",
            interval_seconds=float(settings.workers.reddit_interval),
            settings=settings,
            db=db,
        )
        self.reddit_service = reddit_service

    async def tick(self) -> None:
        """Scan all configured subreddits."""
        posts = await self.reddit_service.scan_subreddits()
        log.info(
            "Reddit worker: processed {n} new posts from {s} subreddits",
            n=len(posts), s=len(self.settings.reddit.subreddits),
        )
