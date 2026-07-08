"""Schedule command handlers: manage scheduled reports."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.repositories.telegram_repo import TelegramRepository

log = get_logger("telegram")


class ScheduleHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.repo = TelegramRepository(db)

    async def manage(self, update, context) -> None:
        reports = await self.repo.get_active_reports()
        msg = "\U0001f4c5 <b>SCHEDULED REPORTS</b>\n\n"
        if not reports:
            msg += "No scheduled reports.\n"
            msg += "Morning briefing runs daily at configured time."
        else:
            for r in reports:
                msg += f"\u2022 {r['report_type']} — {r['schedule']}\n"
        await update.message.reply_text(msg, parse_mode="HTML")
