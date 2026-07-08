"""HTTP-layer tag emissions in BybitDemoClient.

When Bybit's edge proxy rejects a request before it reaches the V5
backend (garbage key, revoked key, missing endpoint permission), it
returns HTTP 401/403 with no retCode envelope. The retCode-specific
AUTH_FAIL tag would never fire on those paths. The client must emit
both BYBIT_DEMO_HTTP_FAIL (always) AND BYBIT_DEMO_AUTH_FAIL (specifically
on 401/403) so the alert relay catches auth issues uniformly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from src.bybit_demo.bybit_demo_client import BybitDemoClient
from src.core.exceptions import BybitAPIError


class _FakeResponse:
    """Minimal aiohttp.ClientResponse stand-in for the request loop."""

    def __init__(self, status: int, body_text: str = "") -> None:
        self.status = status
        self._body = body_text
        self.headers: dict[str, str] = {}
        self.request_info = MagicMock()
        self.history = ()

    async def text(self) -> str:
        return self._body

    async def json(self) -> dict[str, Any]:
        return {}


class _FakeSession:
    """Minimal aiohttp.ClientSession stand-in: returns a configured response."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def request(self, *args: Any, **kw: Any):  # noqa: D401
        @asynccontextmanager
        async def _ctx():
            yield self._response

        return _ctx()


def _capture_warnings_and_errors() -> tuple[list[str], int]:
    captured: list[str] = []
    sink_id = logger.add(
        lambda m: captured.append(str(m)),
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "bybit_demo",
    )
    return captured, sink_id


@pytest.mark.asyncio
async def test_http_401_emits_both_http_fail_and_auth_fail() -> None:
    """HTTP 401 from Bybit edge → both HTTP_FAIL and AUTH_FAIL tags fire."""
    session = _FakeSession(_FakeResponse(status=401, body_text=""))
    client = BybitDemoClient(
        session=session,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="garbage_key_18char",
        api_secret="garbage_secret_40chars_filler__________",
        retry_attempts=1,
    )

    captured, sink_id = _capture_warnings_and_errors()
    try:
        with pytest.raises(BybitAPIError):
            await client.get("/v5/account/wallet-balance", op="balance")
    finally:
        logger.remove(sink_id)

    assert any("BYBIT_DEMO_HTTP_FAIL" in line and "status=401" in line for line in captured), (
        f"Expected BYBIT_DEMO_HTTP_FAIL with status=401; captured={captured}"
    )
    assert any("BYBIT_DEMO_AUTH_FAIL" in line and "code=http_401" in line for line in captured), (
        f"Expected BYBIT_DEMO_AUTH_FAIL with code=http_401; captured={captured}"
    )


@pytest.mark.asyncio
async def test_http_403_emits_both_http_fail_and_auth_fail() -> None:
    """HTTP 403 (permission denied) likewise emits both tags."""
    session = _FakeSession(_FakeResponse(status=403, body_text="forbidden"))
    client = BybitDemoClient(
        session=session,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="key",
        api_secret="secret",
        retry_attempts=1,
    )

    captured, sink_id = _capture_warnings_and_errors()
    try:
        with pytest.raises(BybitAPIError):
            await client.get("/v5/order/create", op="place_order")
    finally:
        logger.remove(sink_id)

    assert any("BYBIT_DEMO_HTTP_FAIL" in line and "status=403" in line for line in captured)
    assert any("BYBIT_DEMO_AUTH_FAIL" in line and "code=http_403" in line for line in captured)


@pytest.mark.asyncio
async def test_http_400_emits_only_http_fail_not_auth_fail() -> None:
    """Generic 4xx (e.g. 400 bad request) must NOT emit AUTH_FAIL.

    AUTH_FAIL is reserved for credential / permission failures so the
    relay's CRITICAL alert doesn't fire for routine 400s.
    """
    session = _FakeSession(_FakeResponse(status=400, body_text='{"retMsg":"bad request"}'))
    client = BybitDemoClient(
        session=session,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="key",
        api_secret="secret",
        retry_attempts=1,
    )

    captured, sink_id = _capture_warnings_and_errors()
    try:
        with pytest.raises(BybitAPIError):
            await client.get("/v5/order/create", op="place_order")
    finally:
        logger.remove(sink_id)

    assert any("BYBIT_DEMO_HTTP_FAIL" in line and "status=400" in line for line in captured)
    assert not any("BYBIT_DEMO_AUTH_FAIL" in line for line in captured), (
        f"AUTH_FAIL must NOT fire on generic 400; captured={captured}"
    )
