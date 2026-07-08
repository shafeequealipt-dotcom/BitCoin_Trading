"""P2-2 follow-up (2026-05-13) — ensure send_custom callers pass AlertLevel enum.

`AlertManager.send_custom(priority: AlertLevel = AlertLevel.INFO)` expects a
typed enum. Several caller sites had historically passed raw uppercase
strings like ``"INFO"`` or ``"CRITICAL"``. Pre-P2-2 those sites' alerts
were silently broken at the ALERT_SENT log emit (raw str has no
``.value`` attribute) — but the message had already reached Telegram
before the crash, so the bug was latent. Post-P2-2, the
``ALERT_AWAITED | kind={priority.value.lower()}`` emit runs BEFORE
``bot.send_message``, so the same crash now prevents the alert from
ever being sent. The follow-up commit repaired all caller sites to
pass the proper ``AlertLevel`` enum.

These tests lock in the contract so a future regression does not
silently break alert delivery:

1. ``test_no_raw_string_priority_in_known_caller_files`` — static grep
   over every known ``send_custom`` caller file in the project,
   refusing any pattern like ``send_custom(..., "INFO")`` or
   ``priority="CRITICAL"``.

2. ``test_send_custom_with_alertlevel_critical_does_not_crash`` —
   constructs a minimal AlertManager + AsyncMock bot, calls
   ``send_custom(msg, AlertLevel.CRITICAL)``, and verifies:
     - ``priority.value.lower() == "critical"`` field appears in the
       ALERT_AWAITED log.
     - ``bot.send_message`` is awaited exactly once (CRITICAL is
       still synchronous per P2-2).
     - The call returns True.

3. ``test_send_custom_with_alertlevel_info_uses_fire_and_forget`` —
   same harness but with AlertLevel.INFO, verifying the fire-and-
   forget path emits ``ALERT_FIRE_AND_FORGET`` and runs the bot send
   in the background task.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from src.alerts.alert_manager import AlertManager
from src.core.types import AlertLevel


# Caller files known to use send_custom. If a new caller file appears,
# add it here.
_CALLER_FILES: tuple[str, ...] = (
    "src/workers/position_watchdog.py",
    "src/workers/profit_sniper.py",
    "src/workers/strategy_worker.py",
    "src/core/layer_manager.py",
    "src/exchanges/switching/exchange_switcher.py",
    "src/exchanges/switching/post_switch_verifier.py",
)


# Pattern: send_custom(... "INFO" ...) or priority="INFO" or "CRITICAL" /
# "WARNING" — UPPERCASE string literals as priority. Matches the buggy
# pre-fix form, ignoring lowercase usages.
_BAD_PRIORITY_RE = re.compile(
    r'send_custom\([^)]*?(["\'](?:INFO|WARNING|CRITICAL)["\'])',
    re.MULTILINE | re.DOTALL,
)
_BAD_KWARG_RE = re.compile(
    r'\bpriority\s*=\s*["\'](?:INFO|WARNING|CRITICAL)["\']',
)


def test_no_raw_string_priority_in_known_caller_files() -> None:
    """Each caller file must pass AlertLevel enum, not a raw string."""
    project = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for rel in _CALLER_FILES:
        path = project / rel
        if not path.exists():
            continue
        text = path.read_text()
        for match in _BAD_PRIORITY_RE.finditer(text):
            offenders.append(
                f"{rel}: positional priority is raw string: {match.group(0)[:120]!r}"
            )
        for match in _BAD_KWARG_RE.finditer(text):
            offenders.append(
                f"{rel}: priority=<str> kwarg is raw string: {match.group(0)[:120]!r}"
            )
    assert not offenders, (
        "send_custom callers must pass AlertLevel enum, not raw strings. "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


def _build_minimal_am() -> AlertManager:
    """Construct AlertManager with mocked bot for tests."""
    settings = SimpleNamespace(
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
    am = AlertManager(settings, MagicMock())
    am.enabled = True
    am.bot.send_message = AsyncMock(return_value=True)
    return am


@pytest.mark.asyncio
async def test_send_custom_with_alertlevel_critical_does_not_crash() -> None:
    """AlertLevel.CRITICAL must drive the awaited path AND emit ALERT_AWAITED with kind=critical."""
    am = _build_minimal_am()
    captured: list[str] = []
    sink = logger.add(lambda m: captured.append(str(m)), level="INFO")
    try:
        result = await am.send_custom("EMERGENCY test", AlertLevel.CRITICAL)
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    assert "ALERT_AWAITED | kind=critical" in joined, (
        f"CRITICAL path must emit ALERT_AWAITED kind=critical: {joined[:400]}"
    )
    assert result is True, "CRITICAL with healthy bot should return True"
    assert am.bot.send_message.await_count == 1, (
        "CRITICAL path must call bot.send_message exactly once (awaited)"
    )


@pytest.mark.asyncio
async def test_send_custom_with_alertlevel_info_uses_fire_and_forget() -> None:
    """AlertLevel.INFO must take the fire-and-forget path and emit ALERT_FIRE_AND_FORGET."""
    am = _build_minimal_am()
    captured: list[str] = []
    sink = logger.add(lambda m: captured.append(str(m)), level="INFO")
    try:
        result = await am.send_custom("trade entry test", AlertLevel.INFO)
        await am.flush_pending_info()
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    assert "ALERT_FIRE_AND_FORGET | kind=info bypass=Y" in joined, (
        f"INFO path must emit ALERT_FIRE_AND_FORGET: {joined[:400]}"
    )
    assert result is True
    assert am.bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_send_custom_with_alertlevel_warning_emits_awaited_warning_kind() -> None:
    """AlertLevel.WARNING takes the awaited path and emits ALERT_AWAITED kind=warning."""
    am = _build_minimal_am()
    captured: list[str] = []
    sink = logger.add(lambda m: captured.append(str(m)), level="INFO")
    try:
        result = await am.send_custom("price spike test", AlertLevel.WARNING)
    finally:
        logger.remove(sink)
    joined = "\n".join(captured)
    assert "ALERT_AWAITED | kind=warning" in joined, (
        f"WARNING path must emit ALERT_AWAITED kind=warning: {joined[:400]}"
    )
    assert result is True
