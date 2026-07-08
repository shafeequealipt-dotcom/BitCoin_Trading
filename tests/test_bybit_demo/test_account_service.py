"""Smoke test for BybitDemoAccountService — V5 wallet translation."""

from __future__ import annotations

from typing import Any

import pytest

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoAccountService,
    _build_account_info_from_v5,
    _empty_account_info,
)
from src.core.exceptions import BybitAPIError


class _FakeClient:
    def __init__(self) -> None:
        self.gets: list[tuple[str, dict[str, Any] | None, str]] = []
        self.wallet_response: dict[str, Any] = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "accountType": "UNIFIED",
                        "totalEquity": "10000.50",
                        "totalAvailableBalance": "9500.0",
                        "totalInitialMargin": "500.50",
                        "totalPerpUPL": "150.0",
                        # Equity-phantom fix (2026-05-26): the builder now
                        # reads the USDT SETTLEMENT COIN, not the account-level
                        # totals. USDT equity (9850.50) deliberately differs
                        # from the unified totalEquity (10000.50) to prove the
                        # basis is the coin, not the all-coin total.
                        "coin": [
                            {
                                "coin": "USDT",
                                "equity": "9850.50",
                                "walletBalance": "9700.0",
                                "availableToWithdraw": "9500.0",
                                "unrealisedPnl": "150.0",
                                "totalPositionIM": "500.50",
                            }
                        ],
                    }
                ]
            },
        }
        self.get_raises: Exception | None = None

    async def get(self, path: str, params: dict[str, Any] | None = None, *, op: str = "") -> dict[str, Any]:
        self.gets.append((path, params, op))
        if self.get_raises is not None:
            raise self.get_raises
        return self.wallet_response

    async def post(self, *args: Any, **kw: Any) -> dict[str, Any]:
        return {"retCode": 0, "result": {}}

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_get_wallet_balance_translates_v5() -> None:
    client = _FakeClient()
    svc = BybitDemoAccountService(client)  # type: ignore[arg-type]

    info = await svc.get_wallet_balance()
    # Equity-phantom fix (2026-05-26): equity/available come from the USDT
    # settlement coin, NOT the unified all-coin totals. USDT equity is
    # 9850.50; the unified totalEquity 10000.50 must NOT be used.
    assert info.total_equity == 9850.50        # USDT coin equity (not 10000.50)
    assert info.available_balance == 9500.0    # USDT availableToWithdraw
    assert info.used_margin == 500.50          # USDT totalPositionIM
    assert info.unrealized_pnl == 150.0        # USDT unrealisedPnl
    assert info.margin_level_pct == 0.0  # Bybit doesn't expose a single ratio


@pytest.mark.asyncio
async def test_get_wallet_balance_returns_empty_on_api_error() -> None:
    client = _FakeClient()
    client.get_raises = BybitAPIError(
        "API down", details={"ret_code": -1, "ret_msg": "down", "op": "balance"}
    )
    svc = BybitDemoAccountService(client)  # type: ignore[arg-type]

    info = await svc.get_wallet_balance()
    # Sentinel — never raises, returns zeroed AccountInfo.
    assert info.total_equity == 0.0
    assert info.available_balance == 0.0
    assert info.used_margin == 0.0
    assert info.unrealized_pnl == 0.0


@pytest.mark.asyncio
async def test_get_wallet_balance_logs_warning_on_api_error() -> None:
    """Sentinel return must NOT be silent — emit BYBIT_DEMO_WALLET_FAIL.

    Without this, a credential or network outage zeroes equity readings
    that flow into boot validation, watchdog, and sizing without leaving
    a single log line tying the zero back to the cause.
    """
    from loguru import logger

    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="WARNING")
    try:
        client = _FakeClient()
        client.get_raises = BybitAPIError(
            "auth failed (10003): API key invalid",
            details={"ret_code": 10003, "ret_msg": "API key invalid", "op": "balance"},
        )
        svc = BybitDemoAccountService(client)  # type: ignore[arg-type]
        await svc.get_wallet_balance()
    finally:
        logger.remove(sink_id)

    assert any("BYBIT_DEMO_WALLET_FAIL" in line for line in captured), (
        "Expected BYBIT_DEMO_WALLET_FAIL warning when wallet probe fails; "
        f"captured={captured}"
    )


@pytest.mark.asyncio
async def test_wrapper_methods_route_through_get_wallet_balance() -> None:
    client = _FakeClient()
    svc = BybitDemoAccountService(client)  # type: ignore[arg-type]

    avail = await svc.get_available_balance()
    equity = await svc.get_equity()
    margin = await svc.get_margin_usage()

    assert avail == 9500.0
    assert equity == 9850.50               # USDT coin equity, not unified 10000.50
    assert margin["total_equity"] == 9850.50
    assert margin["used_margin"] == 500.50
    assert margin["free_margin"] == 9500.0


def test_helpers_account_info() -> None:
    empty = _empty_account_info()
    assert empty.total_equity == 0.0
    assert empty.available_balance == 0.0

    built = _build_account_info_from_v5({
        "totalEquity": "100",
        "totalAvailableBalance": "80",
        "totalInitialMargin": "20",
        "totalPerpUPL": "5",
    })
    assert built.total_equity == 100.0
    assert built.available_balance == 80.0
    assert built.used_margin == 20.0
    assert built.unrealized_pnl == 5.0
