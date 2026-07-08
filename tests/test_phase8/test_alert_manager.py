"""Tests for AlertManager."""

import pytest

from src.core.types import AlertLevel, SignalType
from src.alerts.alert_manager import AlertManager


class TestAlertManagerInit:
    @pytest.mark.asyncio
    async def test_disabled_in_config(self, alert_settings_disabled, test_db):
        am = AlertManager(alert_settings_disabled, test_db)
        await am.initialize()
        assert am.enabled is False

    @pytest.mark.asyncio
    async def test_enabled_with_mock_bot(self, alert_settings_enabled, test_db, mock_bot):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        assert am.enabled is True


class TestAlertManagerSending:
    @pytest.mark.asyncio
    async def test_trade_alert_sends(self, alert_settings_enabled, test_db, mock_bot, sample_order):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_trade_alert(sample_order, 5000.0)
        # P2-2 (2026-05-13): trade alerts are now INFO -> fire-and-forget.
        # Wait for the background send task to actually call the bot.
        await am.flush_pending_info()
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_trade_alert_disabled(self, alert_settings_disabled, test_db, sample_order):
        am = AlertManager(alert_settings_disabled, test_db)
        await am.send_trade_alert(sample_order)
        # Should not crash, just silently do nothing

    @pytest.mark.asyncio
    async def test_signal_alert_high_conf(self, alert_settings_enabled, test_db, mock_bot, sample_signal):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_signal_alert(sample_signal)
        # P2-2 (2026-05-13): signal alerts are INFO -> fire-and-forget.
        await am.flush_pending_info()
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_alert_low_conf_filtered(self, alert_settings_enabled, test_db, mock_bot):
        from src.core.types import Signal
        low_signal = Signal(symbol="BTCUSDT", signal_type=SignalType.BUY, confidence=0.3, source="test")
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_signal_alert(low_signal)
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_brain_hold_emits(self, alert_settings_enabled, test_db, mock_bot, sample_brain_hold):
        """Brain HOLD decisions ARE alerted (operator visibility, not filtered).

        ``AlertManager.send_brain_decision_alert`` docstring (alert_manager.py:71)
        explicitly says "Send alert for every Brain decision (including
        holds)." The original test asserted holds were filtered — that
        was the pre-Phase-8 contract. Operators wanted ALL brain
        decisions visible (including HOLD) so an unexplained absence of
        trade activity is traceable to a documented hold reason rather
        than a silent failure. The tightened ``send_signal_alert``
        path (low-confidence signals filtered, see
        ``test_signal_alert_low_conf_filtered``) does NOT apply to
        brain decisions.
        """
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_brain_decision_alert(sample_brain_hold, "scheduled", 0.007)
        # P2-2 (2026-05-13): brain decision alerts are INFO -> fire-and-forget.
        await am.flush_pending_info()
        mock_bot.send_message.assert_called_once()
        # Assert the rendered message reflects the HOLD action — guards
        # against a future template regression silently dropping
        # action context.
        sent_text = mock_bot.send_message.call_args.args[0]
        assert "HOLD" in sent_text.upper()

    @pytest.mark.asyncio
    async def test_brain_buy_sends(self, alert_settings_enabled, test_db, mock_bot, sample_brain_buy):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_brain_decision_alert(sample_brain_buy, "scheduled", 0.0085)
        # P2-2 (2026-05-13): brain decision alerts are INFO -> fire-and-forget.
        await am.flush_pending_info()
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_critical_bypasses_throttle(self, alert_settings_enabled, test_db, mock_bot):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        am.throttle.max_per_hour = 0  # Fully throttled
        await am.send_error_alert("brain", "crash!", AlertLevel.CRITICAL)
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_risk_warning_always_sends(self, alert_settings_enabled, test_db, mock_bot):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        am.throttle.max_per_hour = 0
        await am.send_risk_warning("Loss limit", {"pnl": "-$245"})
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_prevents_repeat(self, alert_settings_enabled, test_db, mock_bot, sample_order):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_trade_alert(sample_order)
        await am.send_trade_alert(sample_order)  # Same alert
        # P2-2 (2026-05-13): trade alerts are now INFO -> fire-and-forget.
        # The dedup is enforced via pre-recording the content hash in _send
        # BEFORE the task is scheduled, so the second call still sees a
        # duplicate. Wait for the first task to actually call the bot
        # before asserting on call_count.
        await am.flush_pending_info()
        # Only first should send (dedup)
        assert mock_bot.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_test_message(self, alert_settings_enabled, test_db, mock_bot):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        result = await am.send_test_message()
        assert result is True

    @pytest.mark.asyncio
    async def test_system_startup(self, alert_settings_enabled, test_db, mock_bot):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        am.enabled = True
        await am.send_system_startup("paper", ["BTCUSDT"], 7)
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_stats(self, alert_settings_enabled, test_db, mock_bot):
        am = AlertManager(alert_settings_enabled, test_db)
        am.bot = mock_bot
        stats = am.get_stats()
        assert "enabled" in stats
        assert "bot" in stats
        assert "throttle" in stats
