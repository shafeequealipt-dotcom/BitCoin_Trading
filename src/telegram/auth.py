"""Telegram authentication: verifies only authorized users can interact."""

from src.config.settings import Settings
from src.core.logging import get_logger

log = get_logger("telegram")


class TelegramAuth:
    """Verifies only authorized chat IDs can interact with the bot.

    Args:
        settings: Application settings with chat_id.
    """

    def __init__(self, settings: Settings) -> None:
        self.authorized_chat_ids: set[int] = set()
        chat_id = settings.alerts.chat_id
        if chat_id:
            try:
                self.authorized_chat_ids.add(int(chat_id))
            except (ValueError, TypeError):
                pass

    def is_authorized(self, chat_id: int) -> bool:
        """Check if a chat ID is authorized."""
        if not self.authorized_chat_ids:
            return True  # No restriction if no IDs configured
        return chat_id in self.authorized_chat_ids

    def add_authorized(self, chat_id: int) -> None:
        """Add a chat ID to the authorized set."""
        self.authorized_chat_ids.add(chat_id)
