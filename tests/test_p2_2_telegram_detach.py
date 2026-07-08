"""P2-2 (2026-05-13) — Telegram fire-and-forget for INFO alerts.

Verifies that ``AlertManager._send`` no longer blocks the critical
trade path on Telegram round-trip latency for INFO-level alerts,
while CRITICAL and WARNING alerts still await delivery for
guaranteed operator notification.

The fix lives entirely inside ``alert_manager.py``:

- ``_send`` gates by priority. INFO -> ``asyncio.create_task``;
  CRITICAL/WARNING -> awaited.
- ``_deliver_and_log`` is the shared transport+log helper.
- ``_pending_info_tasks`` set + ``flush_pending_info`` coroutine
  allow tests and shutdown to drain in-flight info sends.

These tests use a fake ``bot`` whose ``send_message`` is an
``AsyncMock`` we can throttle with a small ``asyncio.sleep`` to
prove the non-blocking path.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from src.alerts.alert_manager import AlertManager
from src.core.types import AlertLevel


def _build_settings() -> SimpleNamespace:
    """Minimal Settings stand-in covering every attribute AlertManager +
    its TelegramBot dependency read at construction time."""
    return SimpleNamespace(
        alerts=SimpleNamespace(
            telegram_enabled=True,
            max_alerts_per_minute=10,
            trade_alerts=True,
            signal_alerts=True,
            error_alerts=True,
            bot_token="test-token",
            chat_id="-1001234567890",
        ),
    )


def _build_am(*, slow_send_seconds: float = 0.0) -> AlertManager:
    """Build an AlertManager whose Telegram bot is a controlled AsyncMock."""
    settings = _build_settings()
    db = MagicMock()
    am = AlertManager(settings, db)
    am.enabled = True
    if slow_send_seconds > 0:
        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(slow_send_seconds)
            return True
        am.bot.send_message = AsyncMock(side_effect=_slow_send)
    else:
        am.bot.send_message = AsyncMock(return_value=True)
    return am


@pytest.mark.asyncio
async def test_info_alert_returns_immediately_when_telegram_is_slow() -> None:
    """The INFO path must return in microseconds even if the bot takes seconds."""
    am = _build_am(slow_send_seconds=0.5)
    t0 = time.monotonic()
    result = await am.send_custom("entry @ 100, sl @ 95", AlertLevel.INFO)
    elapsed = time.monotonic() - t0
    assert result is True, "send_custom should return True optimistically on INFO"
    assert elapsed < 0.1, (
        f"INFO send_custom blocked for {elapsed:.3f}s — fire-and-forget didn't fire"
    )
    # Bot has not yet been called (task is still sleeping).
    assert am.bot.send_message.await_count == 0
    # Drain the task — now the bot must have been called exactly once.
    await am.flush_pending_info()
    assert am.bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_critical_alert_blocks_until_telegram_returns() -> None:
    """The CRITICAL path must keep awaited semantics for delivery guarantee."""
    am = _build_am(slow_send_seconds=0.2)
    t0 = time.monotonic()
    result = await am.send_custom("EMERGENCY HALT", AlertLevel.CRITICAL)
    elapsed = time.monotonic() - t0
    assert result is True
    # The bot must have already been called by the time send_custom returns.
    assert am.bot.send_message.await_count == 1
    assert elapsed >= 0.18, (
        f"CRITICAL send_custom returned in {elapsed:.3f}s — should have awaited"
    )
    assert not am._pending_info_tasks


@pytest.mark.asyncio
async def test_info_emits_fire_and_forget_tag() -> None:
    """New ALERT_FIRE_AND_FORGET tag fires on the INFO path."""
    am = _build_am()
    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="INFO",
    )
    try:
        await am.send_custom("an info message", AlertLevel.INFO)
        await am.flush_pending_info()
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    assert "ALERT_FIRE_AND_FORGET | kind=info bypass=Y" in joined, (
        f"missing new tag: {joined[:400]}"
    )


@pytest.mark.asyncio
async def test_critical_emits_awaited_tag() -> None:
    """ALERT_AWAITED tag fires on the CRITICAL/WARNING path."""
    am = _build_am()
    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="INFO",
    )
    try:
        await am.send_custom("HALT! risk!", AlertLevel.CRITICAL)
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    assert "ALERT_AWAITED | kind=critical" in joined, (
        f"missing tag: {joined[:400]}"
    )
    # And the fire-and-forget tag must NOT appear on the critical path.
    assert "ALERT_FIRE_AND_FORGET" not in joined


@pytest.mark.asyncio
async def test_info_failure_still_emits_alert_fail() -> None:
    """Fire-and-forget does not silently lose delivery failures."""
    am = _build_am()
    am.bot.send_message = AsyncMock(return_value=False)  # simulate Telegram fail
    am._reposition_dashboard = AsyncMock()  # avoid unrelated side-effects
    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="ERROR",
    )
    try:
        await am.send_custom("an info that will fail", AlertLevel.INFO)
        await am.flush_pending_info()
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    assert "ALERT_FAIL | level=info" in joined, (
        f"fire-and-forget failure must still surface ALERT_FAIL: {joined[:500]}"
    )


@pytest.mark.asyncio
async def test_info_dedup_preserved_under_fire_and_forget() -> None:
    """Identical INFO sends back-to-back trigger dedup despite fire-and-forget."""
    am = _build_am()
    msg = "trade_executed_BTCUSDT_Buy_50000_qty=0.01"
    r1 = await am.send_custom(msg, AlertLevel.INFO)
    r2 = await am.send_custom(msg, AlertLevel.INFO)
    await am.flush_pending_info()
    assert r1 is True
    assert r2 is False  # dedup blocks the second
    assert am.bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_disabled_alert_manager_doesnt_schedule_task() -> None:
    """If AlertManager is disabled, no fire-and-forget task is created."""
    am = _build_am()
    am.enabled = False
    result = await am.send_custom("x", AlertLevel.INFO)
    assert result is False
    assert not am._pending_info_tasks
    assert am.bot.send_message.await_count == 0


@pytest.mark.asyncio
async def test_flush_pending_info_is_noop_when_empty() -> None:
    """``flush_pending_info`` doesn't crash with nothing in flight."""
    am = _build_am()
    await am.flush_pending_info()  # must not raise
    assert not am._pending_info_tasks


@pytest.mark.asyncio
async def test_done_callback_captures_unexpected_exception() -> None:
    """Uncaught exceptions in the fire-and-forget task emit FIRE_AND_FORGET_TASK_FAIL."""
    am = _build_am()

    async def _explode(*_a, **_kw):
        raise RuntimeError("simulated unexpected crash")

    am.bot.send_message = AsyncMock(side_effect=_explode)
    am._reposition_dashboard = AsyncMock()
    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="ERROR",
    )
    try:
        await am.send_custom("info that crashes", AlertLevel.INFO)
        await am.flush_pending_info()
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    # The exception is caught inside _deliver_and_log first (it
    # wraps bot.send_message in try/except) so ALERT_FAIL fires; if a
    # truly uncaught exception slipped past, the done-callback would
    # also fire. We accept either signal.
    assert ("ALERT_FAIL | level=info" in joined) or (
        "ALERT_FIRE_AND_FORGET_TASK_FAIL" in joined
    ), f"failure path produced no observable error log: {joined[:500]}"
