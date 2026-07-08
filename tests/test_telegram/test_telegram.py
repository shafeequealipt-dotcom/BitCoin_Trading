"""Tests for Interactive Telegram Bot components."""

import pytest

from src.telegram.auth import TelegramAuth
from src.telegram.conversation import ConversationManager
from src.telegram.router import MessageRouter
from src.telegram.models.telegram_types import (
    ConversationState, PriceAlert, JournalEntry, ScheduledReport,
)


# =============================================================================
# Auth Tests
# =============================================================================

class TestAuth:
    def test_authorized_chat_id(self):
        from unittest.mock import MagicMock
        settings = MagicMock()
        settings.alerts.chat_id = "12345"
        auth = TelegramAuth(settings)
        assert auth.is_authorized(12345) is True
        assert auth.is_authorized(99999) is False

    def test_no_restriction_when_empty(self):
        from unittest.mock import MagicMock
        settings = MagicMock()
        settings.alerts.chat_id = ""
        auth = TelegramAuth(settings)
        assert auth.is_authorized(99999) is True

    def test_add_authorized(self):
        from unittest.mock import MagicMock
        settings = MagicMock()
        settings.alerts.chat_id = "12345"
        auth = TelegramAuth(settings)
        auth.add_authorized(67890)
        assert auth.is_authorized(67890) is True


# =============================================================================
# Router Tests
# =============================================================================

class TestRouter:
    def test_classify_buy_command(self):
        router = MessageRouter()
        intent = router.classify("buy BTC 100")
        assert intent["type"] == "trade_command"
        assert intent["action"] == "buy"
        assert intent["symbol"] == "BTCUSDT"

    def test_classify_sell_command(self):
        router = MessageRouter()
        intent = router.classify("sell ETH 50 3x")
        assert intent["type"] == "trade_command"
        assert intent["action"] == "sell"
        assert intent["symbol"] == "ETHUSDT"

    def test_classify_close_command(self):
        router = MessageRouter()
        intent = router.classify("close BTC")
        assert intent["type"] == "trade_command"
        assert intent["action"] == "close"

    def test_classify_emergency(self):
        router = MessageRouter()
        assert router.classify("emergency")["type"] == "emergency"
        assert router.classify("close all now")["type"] == "emergency"
        assert router.classify("911")["type"] == "emergency"

    def test_classify_quick_query(self):
        router = MessageRouter()
        assert router.classify("portfolio")["type"] == "quick_query"
        assert router.classify("how am i doing")["type"] == "quick_query"
        assert router.classify("my positions")["type"] == "quick_query"
        assert router.classify("pnl today")["type"] == "quick_query"

    def test_classify_ai_question(self):
        router = MessageRouter()
        intent = router.classify("what do you think about the market?")
        assert intent["type"] == "ai_question"

    def test_extract_symbol_from_name(self):
        router = MessageRouter()
        assert router._extract_symbol("analyze bitcoin now") == "BTCUSDT"
        assert router._extract_symbol("analyze eth") == "ETHUSDT"
        assert router._extract_symbol("what about solana") == "SOLUSDT"

    def test_extract_symbol_from_pair(self):
        router = MessageRouter()
        assert router._extract_symbol("check BTCUSDT") == "BTCUSDT"

    def test_extract_no_symbol(self):
        router = MessageRouter()
        assert router._extract_symbol("how is the market") is None

    def test_normalize_symbol(self):
        assert MessageRouter._normalize_symbol("btc") == "BTCUSDT"
        assert MessageRouter._normalize_symbol("eth") == "ETHUSDT"
        assert MessageRouter._normalize_symbol("SOLUSDT") == "SOLUSDT"

    def test_context_carries_symbol(self):
        router = MessageRouter()
        state = ConversationState(chat_id=1, last_symbol="BTCUSDT")
        intent = router.classify("and the funding?", state)
        assert intent.get("symbol") == "BTCUSDT"


# =============================================================================
# Conversation Tests
# =============================================================================

class TestConversation:
    def test_get_state_creates_new(self):
        mgr = ConversationManager()
        state = mgr.get_state(12345)
        assert state.chat_id == 12345
        assert state.last_symbol == ""

    def test_set_pending(self):
        mgr = ConversationManager()
        mgr.set_pending(12345, "buy_confirm", {"symbol": "BTCUSDT"})
        state = mgr.get_state(12345)
        assert state.pending_action == "buy_confirm"
        assert state.pending_data["symbol"] == "BTCUSDT"

    def test_clear_pending(self):
        mgr = ConversationManager()
        mgr.set_pending(12345, "buy_confirm", {"symbol": "BTCUSDT"})
        mgr.clear_pending(12345)
        state = mgr.get_state(12345)
        assert state.pending_action == ""

    def test_context_for_ai(self):
        mgr = ConversationManager()
        state = mgr.get_state(12345)
        state.add_message("user", "analyze BTC")
        state.add_message("assistant", "BTC is at $70,000")
        state.last_symbol = "BTCUSDT"
        context = mgr.get_context_for_ai(12345)
        assert "BTC" in context
        assert "BTCUSDT" in context

    def test_message_history_limit(self):
        mgr = ConversationManager()
        state = mgr.get_state(12345)
        for i in range(15):
            state.add_message("user", f"message {i}")
        assert len(state.message_history) == 10

    def test_update_context(self):
        mgr = ConversationManager()
        mgr.update_context(12345, symbol="ETHUSDT", intent="analyze")
        state = mgr.get_state(12345)
        assert state.last_symbol == "ETHUSDT"
        assert state.last_intent == "analyze"


# =============================================================================
# Price Alert Tests
# =============================================================================

class TestPriceAlert:
    def test_check_above_triggered(self):
        alert = PriceAlert(
            id="pa_1", chat_id=1, symbol="BTCUSDT",
            condition="above", target_price=70000,
        )
        assert alert.check(70001) is True
        assert alert.check(69999) is False

    def test_check_below_triggered(self):
        alert = PriceAlert(
            id="pa_1", chat_id=1, symbol="BTCUSDT",
            condition="below", target_price=69000,
        )
        assert alert.check(68999) is True
        assert alert.check(69001) is False

    def test_already_triggered(self):
        alert = PriceAlert(
            id="pa_1", chat_id=1, symbol="BTCUSDT",
            condition="above", target_price=70000,
            triggered=True,
        )
        assert alert.check(80000) is False

    def test_to_dict(self):
        alert = PriceAlert(
            id="pa_1", chat_id=1, symbol="BTCUSDT",
            condition="above", target_price=70000,
        )
        d = alert.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["target"] == 70000


# =============================================================================
# Config Tests
# =============================================================================

class TestConfig:
    def test_telegram_interactive_settings(self):
        from src.config.settings import Settings
        Settings.reset()
        s = Settings._load_fresh()
        assert hasattr(s, "telegram_interactive")
        assert s.telegram_interactive.enabled is True
        assert s.telegram_interactive.max_ai_calls_per_hour == 20
        Settings.reset()
