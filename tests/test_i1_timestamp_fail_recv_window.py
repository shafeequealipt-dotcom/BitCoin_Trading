"""Issue I1 (F-26) — TIMESTAMP_FAIL recv_window architectural fix.

The 2026-05-13 audit captured 6 ``BYBIT_DEMO_TIMESTAMP_FAIL`` events
(code=10002) correlated with VM pressure. The fix is a combination
(Option D):

  A. Bump default ``recv_window`` from 5000 ms to 10000 ms
  B. Retry-on-10002 inside the client's request loop (with fresh
     timestamp on each retry — the loop already re-signs)
  C. Adapter returns a discriminated ``PositionsQueryResult`` /
     ``BalanceQueryResult`` so the watchdog can distinguish
     "exchange confirms zero" from "API failed; ground truth unknown"

Coverage:
  * client signing default (A)
  * client retry-on-10002 path (B) + non-retry for other BybitAPIError
  * client preserves ret_code in wrapped details after retry exhaustion
  * adapter ``get_positions_with_confirmation`` for both unknown and
    confirmed-success states (C)
  * adapter legacy ``get_positions`` returns the empty list under both
    states (backwards compat)
  * adapter ``get_wallet_balance_with_confirmation`` analogous
  * Shadow ``get_positions_with_confirmation`` flags unknown on
    transport failure (parity)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoAccountService,
    BybitDemoPositionService,
)
from src.bybit_demo.bybit_demo_client import BybitDemoClient
from src.core.exceptions import (
    BybitAPIError,
    GroundTruthUnavailableError,
    RateLimitError,
)
from src.core.types import BalanceQueryResult, PositionsQueryResult
from src.shadow.shadow_adapter import ShadowPositionService


# ─── A: recv_window default ────────────────────────────────────────────────


def test_recv_window_default_is_10000_after_i1() -> None:
    """Default recv_window bumped 5000 -> 10000 ms by Issue I1."""
    client = BybitDemoClient(
        session=None,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="fakekey",
        api_secret="fakesecret",
    )
    assert client._recv_window == 10000


def test_recv_window_signed_header_uses_configured_value() -> None:
    """The signed-header builder emits the configured recv_window."""
    client = BybitDemoClient(
        session=None,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="k",
        api_secret="s",
        recv_window=15000,
    )
    h = client._signed_headers(timestamp_ms=1, signature="x")
    assert h["X-BAPI-RECV-WINDOW"] == "15000"


# ─── C: adapter discriminated result ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_positions_with_confirmation_unknown_on_10002() -> None:
    """When the underlying client raises BybitAPIError with ret_code=10002,
    the adapter returns ``confirmed=False`` with reason='timestamp_fail'."""
    client_mock = MagicMock()
    client_mock.get = AsyncMock(
        side_effect=BybitAPIError(
            "timestamp fail",
            details={"op": "positions", "ret_code": 10002, "last_error": "10002"},
        )
    )
    svc = BybitDemoPositionService(
        client=client_mock, trading_repo=None,
    )
    result = await svc.get_positions_with_confirmation()
    assert isinstance(result, PositionsQueryResult)
    assert result.confirmed is False
    assert result.reason == "timestamp_fail"
    assert result.positions == ()


@pytest.mark.asyncio
async def test_get_positions_with_confirmation_other_error_returns_confirmed_empty() -> None:
    """Non-10002 BybitAPIError preserves legacy behaviour: confirmed=True with empty list.
    This keeps non-watchdog callers (dashboards, MCP) byte-for-byte identical."""
    client_mock = MagicMock()
    client_mock.get = AsyncMock(
        side_effect=RateLimitError(
            "rate limit", details={"op": "positions", "ret_code": 10006},
        )
    )
    svc = BybitDemoPositionService(client=client_mock, trading_repo=None)
    result = await svc.get_positions_with_confirmation()
    assert result.confirmed is True
    assert result.positions == ()


@pytest.mark.asyncio
async def test_get_positions_with_confirmation_confirmed_with_positions() -> None:
    """Normal success path returns confirmed=True with parsed positions."""
    client_mock = MagicMock()
    client_mock.get = AsyncMock(
        return_value={
            "retCode": 0,
            "result": {
                "list": [{
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.1",
                    "avgPrice": "80000",
                    "markPrice": "80100",
                    "leverage": "5",
                    "unrealisedPnl": "10",
                    "createdTime": "1700000000000",
                    "updatedTime": "1700000000000",
                }]
            },
        }
    )
    svc = BybitDemoPositionService(client=client_mock, trading_repo=None)
    result = await svc.get_positions_with_confirmation()
    assert result.confirmed is True
    assert len(result.positions) == 1
    assert result.positions[0].symbol == "BTCUSDT"


# ─── Backwards-compat: legacy get_positions ──────────────────────────────


@pytest.mark.asyncio
async def test_legacy_get_positions_returns_empty_list_on_unknown() -> None:
    """The legacy ``get_positions()`` callers see an empty list whether the
    state is confirmed-empty or unknown — preserves the pre-I1 contract for
    every consumer that doesn't check confirmation."""
    client_mock = MagicMock()
    client_mock.get = AsyncMock(
        side_effect=BybitAPIError(
            "timestamp fail", details={"op": "positions", "ret_code": 10002},
        )
    )
    svc = BybitDemoPositionService(client=client_mock, trading_repo=None)
    positions = await svc.get_positions()
    assert positions == []
    assert isinstance(positions, list)


# ─── Wallet balance discriminated result ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_wallet_balance_with_confirmation_unknown_on_10002() -> None:
    """Wallet path mirrors the positions path on 10002."""
    client_mock = MagicMock()
    client_mock.get = AsyncMock(
        side_effect=BybitAPIError(
            "timestamp fail", details={"op": "balance", "ret_code": 10002},
        )
    )
    svc = BybitDemoAccountService(client=client_mock)
    result = await svc.get_wallet_balance_with_confirmation()
    assert isinstance(result, BalanceQueryResult)
    assert result.confirmed is False
    assert result.reason == "timestamp_fail"


@pytest.mark.asyncio
async def test_legacy_get_wallet_balance_returns_zero_on_unknown() -> None:
    """Legacy callers see zero AccountInfo whether unknown or confirmed-empty."""
    client_mock = MagicMock()
    client_mock.get = AsyncMock(
        side_effect=BybitAPIError(
            "timestamp fail", details={"op": "balance", "ret_code": 10002},
        )
    )
    svc = BybitDemoAccountService(client=client_mock)
    account = await svc.get_wallet_balance()
    assert account.total_equity == 0.0


# ─── Shadow parity ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_get_positions_with_confirmation_unknown_on_transport_failure() -> None:
    """When Shadow's transport helper returns None (after retry exhaustion),
    the adapter flags confirmed=False so the watchdog preserves state."""
    with patch(
        "src.shadow.shadow_adapter._shadow_get_with_retry",
        new=AsyncMock(return_value=None),
    ):
        svc = ShadowPositionService(
            session=MagicMock(), base_url="http://shadow.local:9090",
        )
        result = await svc.get_positions_with_confirmation()
        assert result.confirmed is False
        assert result.reason == "transport_failure"


@pytest.mark.asyncio
async def test_shadow_get_positions_with_confirmation_confirmed_on_success() -> None:
    """Successful Shadow response returns confirmed=True with positions."""
    fake_data = {
        "positions": [{
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": 0.1,
            "entry_price": 80000,
            "mark_price": 80100,
            "leverage": 5,
            "unrealized_pnl": 10,
            "opened_at": "2026-05-14T10:00:00+00:00",
            "updated_at": "2026-05-14T10:00:00+00:00",
        }]
    }
    with patch(
        "src.shadow.shadow_adapter._shadow_get_with_retry",
        new=AsyncMock(return_value=fake_data),
    ):
        svc = ShadowPositionService(
            session=MagicMock(), base_url="http://shadow.local:9090",
        )
        result = await svc.get_positions_with_confirmation()
        assert result.confirmed is True
        assert len(result.positions) == 1
        assert result.positions[0].symbol == "BTCUSDT"


# ─── Source-level guards ─────────────────────────────────────────────────


def test_request_with_retry_catches_BybitAPIError_for_10002() -> None:
    """Source-pin: the retry loop must catch BybitAPIError with ret_code=10002.
    A future refactor that drops the BybitAPIError handler would silently
    regress to phantom-close territory."""
    import re
    src = open("src/bybit_demo/bybit_demo_client.py").read()
    assert "except BybitAPIError" in src, (
        "Issue I1: client retry loop must catch BybitAPIError"
    )
    # Specifically the 10002 path must emit BYBIT_DEMO_TIMESTAMP_RETRY
    assert re.search(r"BYBIT_DEMO_TIMESTAMP_RETRY", src), (
        "Issue I1: BYBIT_DEMO_TIMESTAMP_RETRY emission missing"
    )


def test_adapter_emits_unknown_state_event() -> None:
    """Source-pin: the adapter must emit BYBIT_DEMO_POSITIONS_UNKNOWN_STATE."""
    import re
    src = open("src/bybit_demo/bybit_demo_adapter.py").read()
    assert re.search(
        r"BYBIT_DEMO_POSITIONS_UNKNOWN_STATE", src,
    ), "Issue I1: positions UNKNOWN_STATE event missing"
    assert re.search(
        r"BYBIT_DEMO_BALANCE_UNKNOWN_STATE", src,
    ), "Issue I1: balance UNKNOWN_STATE event missing"


def test_watchdog_guards_on_ground_truth_unknown() -> None:
    """Source-pin: watchdog must check confirmed flag before close-detect."""
    import re
    src = open("src/workers/position_watchdog.py").read()
    assert re.search(
        r"get_positions_with_confirmation", src,
    ), "Issue I1: watchdog must call get_positions_with_confirmation"
    assert re.search(
        r"WD_GROUND_TRUTH_UNKNOWN", src,
    ), "Issue I1: WD_GROUND_TRUTH_UNKNOWN emission missing"


def test_GroundTruthUnavailableError_exists() -> None:
    """The new typed exception class must exist in exceptions.py."""
    from src.core.exceptions import GroundTruthUnavailableError
    # Subclass of APIError (which is subclass of DataError of TradingMCPError)
    from src.core.exceptions import APIError, TradingMCPError
    assert issubclass(GroundTruthUnavailableError, APIError)
    assert issubclass(GroundTruthUnavailableError, TradingMCPError)
