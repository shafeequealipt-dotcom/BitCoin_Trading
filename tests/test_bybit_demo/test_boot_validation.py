"""Tests for bybit_demo_boot.validate_boot — three failure modes + success."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from loguru import logger

from src.bybit_demo.bybit_demo_boot import validate_boot
from src.core.exceptions import BybitAPIError


def _capture_logs(level: str = "INFO"):
    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level=level)
    return captured, sink_id


@pytest.mark.asyncio
async def test_validate_boot_success_emits_start_and_validated() -> None:
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value={
        "retCode": 0,
        "result": {"list": [{"totalEquity": "10000.50"}]},
    })

    captured, sink_id = _capture_logs("INFO")
    try:
        result = await validate_boot(
            client,
            base_url="https://api-demo.bybit.com",
            api_key_len=64,
            recv_window=5000,
        )
    finally:
        logger.remove(sink_id)

    assert result["ok"] is True
    assert result["equity"] == 10000.50
    assert any("BYBIT_DEMO_BOOT_START" in l for l in captured)
    assert any("BYBIT_DEMO_BOOT_VALIDATED" in l for l in captured)
    assert any("equity=10000.50" in l for l in captured)
    assert not any("BYBIT_DEMO_BOOT_FAIL" in l for l in captured)


@pytest.mark.asyncio
async def test_validate_boot_no_creds_short_circuits() -> None:
    """api_key_len == 0 must fail BEFORE hitting the network."""
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=True)
    client.get = AsyncMock()

    captured, sink_id = _capture_logs("INFO")
    try:
        result = await validate_boot(
            client,
            base_url="https://api-demo.bybit.com",
            api_key_len=0,
            recv_window=5000,
        )
    finally:
        logger.remove(sink_id)

    assert result["ok"] is False
    assert result["step"] == "no_creds"
    # Confirms we did NOT touch the client when creds are missing.
    client.health_check.assert_not_called()
    client.get.assert_not_called()
    assert any("BYBIT_DEMO_BOOT_START" in l for l in captured)
    assert any("BYBIT_DEMO_BOOT_FAIL" in l and "step=no_creds" in l for l in captured)


@pytest.mark.asyncio
async def test_validate_boot_health_check_fail() -> None:
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=False)
    client.get = AsyncMock()

    captured, sink_id = _capture_logs("INFO")
    try:
        result = await validate_boot(
            client,
            base_url="https://api-demo.bybit.com",
            api_key_len=64,
            recv_window=5000,
        )
    finally:
        logger.remove(sink_id)

    assert result["ok"] is False
    assert result["step"] == "health_check"
    # Wallet probe is skipped when reachability fails.
    client.get.assert_not_called()
    assert any("BYBIT_DEMO_BOOT_FAIL" in l and "step=health_check" in l for l in captured)


@pytest.mark.asyncio
async def test_validate_boot_wallet_probe_fail() -> None:
    """Auth failure (10003) on the wallet probe must surface as BOOT_FAIL step=wallet."""
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=True)
    client.get = AsyncMock(side_effect=BybitAPIError(
        "Bybit demo: API error (10003: API key invalid)",
        details={"ret_code": 10003, "ret_msg": "API key invalid", "op": "boot_validate"},
    ))

    captured, sink_id = _capture_logs("INFO")
    try:
        result = await validate_boot(
            client,
            base_url="https://api-demo.bybit.com",
            api_key_len=64,
            recv_window=5000,
        )
    finally:
        logger.remove(sink_id)

    assert result["ok"] is False
    assert result["step"] == "wallet"
    assert "10003" in result["err"]
    assert any("BYBIT_DEMO_BOOT_FAIL" in l and "step=wallet" in l for l in captured)


@pytest.mark.asyncio
async def test_validate_boot_handles_empty_account_list() -> None:
    """Bybit returning an empty result list must not crash; equity = 0."""
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value={"retCode": 0, "result": {"list": []}})

    result = await validate_boot(
        client,
        base_url="https://api-demo.bybit.com",
        api_key_len=64,
        recv_window=5000,
    )

    assert result["ok"] is True
    assert result["equity"] == 0.0
