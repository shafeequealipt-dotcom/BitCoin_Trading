"""Trade Journal: CRUD with mood tracking and AI insights."""

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.telegram_repo import TelegramRepository
from src.telegram.models.telegram_types import JournalEntry

log = get_logger("telegram")


class TradeJournal:
    def __init__(self, db: DatabaseManager) -> None:
        self.repo = TelegramRepository(db)

    async def add_entry(self, chat_id: int, content: str, entry_type: str = "market_thought",
                        symbol: str = "", mood: str = "", trade_id: str = "") -> JournalEntry:
        entry = JournalEntry(
            id=generate_id("jrn"), chat_id=chat_id,
            trade_id=trade_id, symbol=symbol,
            entry_type=entry_type, content=content,
            mood=mood, created_at=now_utc(),
        )
        await self.repo.save_journal_entry(entry)
        return entry

    async def get_entries(self, chat_id: int, limit: int = 10) -> list[dict]:
        return await self.repo.get_journal_entries(chat_id, limit)
