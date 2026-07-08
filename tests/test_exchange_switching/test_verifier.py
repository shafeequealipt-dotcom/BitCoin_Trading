"""Smoke test for verify_post_switch — sentinel detection + cleanup."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.exchanges.switching.post_switch_verifier import (
    POST_SWITCH_SENTINEL_PATH,
    verify_post_switch,
)


class _FakeTransformer:
    def __init__(self, mode: str) -> None:
        self.current_mode = mode

        class _Acc:
            async def get_wallet_balance(self) -> Any:
                class _B:
                    total_equity = 1234.56
                return _B()
        self.active_account_service = _Acc()

        class _Pos:
            async def get_positions(self) -> list:
                return []
        self.active_position_service = _Pos()


class _FakeAlerts:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_custom(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_no_sentinel_is_noop() -> None:
    """When no sentinel file exists, verifier returns False silently."""
    if POST_SWITCH_SENTINEL_PATH.exists():
        POST_SWITCH_SENTINEL_PATH.unlink()

    t = _FakeTransformer("shadow")
    alerts = _FakeAlerts()
    ran = await verify_post_switch(t, alerts)
    assert ran is False
    assert alerts.messages == []


@pytest.mark.asyncio
async def test_sentinel_drives_verification_and_alert() -> None:
    """Sentinel present → verifier reads it, probes adapter, sends alert, deletes file."""
    POST_SWITCH_SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    POST_SWITCH_SENTINEL_PATH.write_text(
        json.dumps({
            "from_mode": "shadow",
            "to_mode": "bybit_demo",
            "positions_closed": 3,
            "reason": "telegram_button",
        })
    )

    t = _FakeTransformer("bybit_demo")
    alerts = _FakeAlerts()
    ran = await verify_post_switch(t, alerts)

    assert ran is True
    assert len(alerts.messages) == 1
    msg = alerts.messages[0]
    assert "bybit_demo" in msg
    assert "1,234.56" in msg or "1234.56" in msg
    assert "0" in msg  # positions: 0
    # Sentinel deleted after verification.
    assert not POST_SWITCH_SENTINEL_PATH.exists()
