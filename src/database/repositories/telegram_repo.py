"""Telegram repository: CRUD for price alerts, journal entries, scheduled reports."""

import json

from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.telegram.models.telegram_types import JournalEntry, PriceAlert, ScheduledReport

log = get_logger("telegram")


class TelegramRepository:
    """CRUD for interactive Telegram bot data."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # --- Price Alerts ---

    async def save_alert(self, alert: PriceAlert) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO price_alerts "
            "(id, chat_id, symbol, condition, target_price, current_price_at_set, "
            "indicator, triggered) VALUES (?,?,?,?,?,?,?,?)",
            (alert.id, alert.chat_id, alert.symbol, alert.condition,
             alert.target_price, alert.current_price_at_set, alert.indicator,
             1 if alert.triggered else 0),
        )

    async def get_active_alerts(self, chat_id: int | None = None) -> list[dict]:
        query = "SELECT * FROM price_alerts WHERE triggered = 0"
        params: tuple = ()
        if chat_id:
            query += " AND chat_id = ?"
            params = (chat_id,)
        rows = await self._db.fetch_all(query, params)
        return [dict(r) for r in rows] if rows else []

    async def trigger_alert(self, alert_id: str) -> None:
        await self._db.execute(
            "UPDATE price_alerts SET triggered = 1, triggered_at = datetime('now') WHERE id = ?",
            (alert_id,),
        )

    async def delete_alert(self, alert_id: str) -> None:
        await self._db.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))

    # --- Journal ---

    async def save_journal_entry(self, entry: JournalEntry) -> None:
        await self._db.execute(
            "INSERT INTO trade_journal "
            "(id, chat_id, trade_id, symbol, entry_type, content, mood) "
            "VALUES (?,?,?,?,?,?,?)",
            (entry.id, entry.chat_id, entry.trade_id, entry.symbol,
             entry.entry_type, entry.content, entry.mood),
        )

    async def get_journal_entries(self, chat_id: int, limit: int = 10) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM trade_journal WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )
        return [dict(r) for r in rows] if rows else []

    # --- Scheduled Reports ---

    async def save_report_config(self, report: ScheduledReport) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO scheduled_reports "
            "(id, chat_id, report_type, schedule, enabled) VALUES (?,?,?,?,?)",
            (report.id, report.chat_id, report.report_type, report.schedule,
             1 if report.enabled else 0),
        )

    async def get_active_reports(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM scheduled_reports WHERE enabled = 1",
        )
        return [dict(r) for r in rows] if rows else []

    # --- Conversation Log ---

    async def log_message(self, chat_id: int, role: str, message: str, intent: str = "") -> None:
        await self._db.execute(
            "INSERT INTO conversation_log (chat_id, role, message, intent) VALUES (?,?,?,?)",
            (chat_id, role, message[:2000], intent),
        )
