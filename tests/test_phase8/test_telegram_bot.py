"""Tests for TelegramBot."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.alerts.telegram_bot import TelegramBot


class TestTelegramBot:
    @pytest.mark.asyncio
    async def test_connect_no_token(self, alert_settings_disabled):
        bot = TelegramBot(alert_settings_disabled)
        result = await bot.connect()
        assert result is False
        assert bot.enabled is False

    @pytest.mark.asyncio
    async def test_connect_success(self, alert_settings_enabled):
        with patch("telegram.Bot") as MockBot:
            mock_instance = MagicMock()
            mock_instance.get_me = AsyncMock(return_value=MagicMock(username="test_bot"))
            MockBot.return_value = mock_instance

            bot = TelegramBot(alert_settings_enabled)
            result = await bot.connect()
            assert result is True
            assert bot.enabled is True

    @pytest.mark.asyncio
    async def test_send_returns_false_when_disabled(self, alert_settings_disabled):
        bot = TelegramBot(alert_settings_disabled)
        result = await bot.send_message("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_success(self, alert_settings_enabled):
        bot = TelegramBot(alert_settings_enabled)
        bot.enabled = True
        mock_tg = MagicMock()
        mock_tg.send_message = AsyncMock()
        bot.bot = mock_tg

        result = await bot.send_message("Hello!")
        assert result is True
        assert bot.total_sent == 1

    @pytest.mark.asyncio
    async def test_send_error_returns_false(self, alert_settings_enabled):
        bot = TelegramBot(alert_settings_enabled)
        bot.enabled = True
        mock_tg = MagicMock()
        mock_tg.send_message = AsyncMock(side_effect=Exception("Network error"))
        bot.bot = mock_tg

        result = await bot.send_message("Hello!")
        assert result is False
        assert bot.total_errors == 1

    @pytest.mark.asyncio
    async def test_truncation(self, alert_settings_enabled):
        bot = TelegramBot(alert_settings_enabled)
        bot.enabled = True
        mock_tg = MagicMock()
        mock_tg.send_message = AsyncMock()
        bot.bot = mock_tg

        long_msg = "x" * 5000
        await bot.send_message(long_msg)
        sent_text = mock_tg.send_message.call_args[1]["text"]
        assert len(sent_text) <= 4096

    def test_stats(self, alert_settings_enabled):
        bot = TelegramBot(alert_settings_enabled)
        bot.total_sent = 10
        bot.total_errors = 2
        stats = bot.get_stats()
        assert stats["total_sent"] == 10
        assert stats["success_rate_pct"] > 0
