"""Telegram Bot Worker: runs the interactive bot as a background task."""

import asyncio

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.bot import InteractiveTelegramBot
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class TelegramBotWorker(BaseWorker):
    """Runs the interactive Telegram bot.

    The bot runs as a long-lived background asyncio task.
    tick() launches it once and then monitors health.
    """

    def __init__(
        self, settings: Settings, db: DatabaseManager, bot: InteractiveTelegramBot,
    ) -> None:
        super().__init__(
            name="telegram_bot_worker",
            interval_seconds=60,
            settings=settings,
            db=db,
        )
        self.bot = bot
        self._bot_task: asyncio.Task | None = None

    async def tick(self) -> None:
        """Launch bot on first tick, monitor on subsequent ticks."""
        if self._bot_task is None:
            log.info("Telegram bot worker: launching interactive bot in background")
            self._bot_task = asyncio.create_task(self._run_bot())
        elif self._bot_task.done():
            exc = self._bot_task.exception() if not self._bot_task.cancelled() else None
            if exc:
                log.error("Telegram bot crashed: {err}, restarting...", err=str(exc))
            else:
                log.warning("Telegram bot stopped, restarting...")
            self._bot_task = asyncio.create_task(self._run_bot())
        # If task is still running, tick does nothing (bot is healthy)

    async def _run_bot(self) -> None:
        """Run the bot — blocks until stopped."""
        try:
            await self.bot.start()
        except Exception as e:
            log.error("Telegram bot error: {err}", err=str(e))

    async def cleanup(self) -> None:
        """Stop the bot on worker shutdown."""
        await self.bot.stop()
        if self._bot_task and not self._bot_task.done():
            self._bot_task.cancel()
