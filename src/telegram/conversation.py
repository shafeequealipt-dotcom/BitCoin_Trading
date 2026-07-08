"""Conversation Manager: tracks multi-turn conversation context."""

from datetime import datetime, timezone

from src.core.logging import get_logger
from src.telegram.models.telegram_types import ConversationState

log = get_logger("telegram")


class ConversationManager:
    """Manages conversation state per user for natural multi-turn interactions.

    Remembers: last coin discussed, pending actions, recent messages.
    """

    def __init__(self) -> None:
        self._states: dict[int, ConversationState] = {}

    def get_state(self, chat_id: int) -> ConversationState:
        """Get or create conversation state for a chat."""
        if chat_id not in self._states:
            self._states[chat_id] = ConversationState(chat_id=chat_id)
        return self._states[chat_id]

    def set_pending(self, chat_id: int, action: str, data: dict) -> None:
        """Set a pending action awaiting user input."""
        state = self.get_state(chat_id)
        state.pending_action = action
        state.pending_data = data

    def clear_pending(self, chat_id: int) -> None:
        """Clear any pending action."""
        state = self.get_state(chat_id)
        state.clear_pending()

    def update_context(self, chat_id: int, symbol: str | None = None, intent: str = "") -> None:
        """Update conversation context after a message."""
        state = self.get_state(chat_id)
        if symbol:
            state.last_symbol = symbol
        if intent:
            state.last_intent = intent
        state.last_message_time = datetime.now(timezone.utc)

    def get_context_for_ai(self, chat_id: int) -> str:
        """Build conversation context string for Claude."""
        state = self.get_state(chat_id)
        if not state.message_history:
            return ""
        context = "Recent conversation:\n"
        for msg in state.message_history[-5:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            context += f"{role}: {msg['text']}\n"
        if state.last_symbol:
            context += f"\nCurrently discussing: {state.last_symbol}\n"
        return context
