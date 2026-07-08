"""Data models for the interactive Telegram bot."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TelegramUser:
    """Authorized Telegram user."""
    chat_id: int
    username: str = ""
    is_admin: bool = True
    language: str = "en"
    timezone: str = "Asia/Kolkata"
    daily_briefing_time: str = "08:00"
    created_at: Optional[datetime] = None


@dataclass
class ConversationState:
    """Tracks conversation context for multi-turn interactions."""
    chat_id: int
    last_symbol: str = ""
    last_intent: str = ""
    last_message_time: Optional[datetime] = None
    pending_action: str = ""
    pending_data: dict = field(default_factory=dict)
    message_history: list[dict] = field(default_factory=list)

    def add_message(self, role: str, text: str) -> None:
        self.message_history.append({
            "role": role, "text": text[:500],
            "time": datetime.now(timezone.utc).isoformat(),
        })
        if len(self.message_history) > 10:
            self.message_history = self.message_history[-10:]

    def clear_pending(self) -> None:
        self.pending_action = ""
        self.pending_data = {}


@dataclass
class PriceAlert:
    """Custom price alert set by user."""
    id: str
    chat_id: int
    symbol: str
    condition: str  # above, below
    target_price: float
    current_price_at_set: float = 0.0
    indicator: str = "price"
    triggered: bool = False
    triggered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    def check(self, current_value: float) -> bool:
        if self.triggered:
            return False
        if self.condition == "above" and current_value >= self.target_price:
            return True
        if self.condition == "below" and current_value <= self.target_price:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "symbol": self.symbol,
            "condition": self.condition, "target": self.target_price,
            "triggered": self.triggered,
        }


@dataclass
class ScheduledReport:
    """User-configured scheduled report."""
    id: str
    chat_id: int
    report_type: str  # daily_briefing, position_check, weekly_summary
    schedule: str  # "daily 08:00", "every 2h", "weekly sunday 21:00"
    enabled: bool = True
    last_sent: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class JournalEntry:
    """Trade journal entry."""
    id: str
    chat_id: int
    trade_id: str = ""
    symbol: str = ""
    entry_type: str = ""  # trade_note, market_thought, lesson, strategy_idea
    content: str = ""
    mood: str = ""  # confident, nervous, frustrated, excited, calm
    created_at: Optional[datetime] = None
