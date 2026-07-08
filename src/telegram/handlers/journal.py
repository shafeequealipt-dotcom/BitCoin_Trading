"""Journal command handlers: /journal, /note."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.telegram_repo import TelegramRepository
from src.telegram.models.telegram_types import JournalEntry

log = get_logger("telegram")


class JournalHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.repo = TelegramRepository(db)

    async def show(self, update, context) -> None:
        entries = await self.repo.get_journal_entries(update.effective_chat.id, limit=5)
        if not entries:
            await update.message.reply_text("\U0001f4d3 Journal empty. Add a note with /note [text]")
            return
        msg = "\U0001f4d3 <b>TRADE JOURNAL</b>\n\n"
        for e in entries:
            mood = f" [{e.get('mood', '')}]" if e.get("mood") else ""
            msg += f"\u2022 {e['content'][:100]}{mood}\n  <i>{e.get('created_at', '')[:16]}</i>\n\n"
        await update.message.reply_text(msg, parse_mode="HTML")

    async def add_note(self, update, context) -> None:
        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /note Your thought here")
            return
        content = " ".join(args)
        entry = JournalEntry(
            id=generate_id("jrn"),
            chat_id=update.effective_chat.id,
            entry_type="market_thought",
            content=content,
            created_at=now_utc(),
        )
        await self.repo.save_journal_entry(entry)
        await update.message.reply_text(f"\u2705 Note saved: {content[:50]}...")
