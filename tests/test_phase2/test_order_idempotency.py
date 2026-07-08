"""Phase 5 — ORDER_START idempotency + scoped retry safety guarantees.

These tests lock in the post-Layer-1 fix for the duplicate ORDER_START
pattern (see ``dev_notes/phase0_issue_duplicate_orders.md`` and
``dev_notes/phase5_order_start_duplicates_report.md``).

The contract under test:
    1. Every ``place_order`` call generates a fresh ``orderLinkId`` and
       passes it to Bybit. No two concurrent calls collide.
    2. Validation/business errors (``InvalidOrderError``,
       ``RateLimitError``, etc.) propagate WITHOUT a retry — re-trying
       cannot help and risks side effects.
    3. A transient (non-Bybit-mapped) exception triggers exactly ONE
       retry, reusing the same ``orderLinkId`` so Bybit can dedup.
    4. ``DuplicateOrderLinkIdError`` (Bybit retCode 110072) is treated as
       evidence the order already exists — recovered via
       ``get_open_orders`` / ``get_order_history`` rather than re-placed.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import (
    BybitAPIError,
    DuplicateOrderLinkIdError,
    InvalidOrderError,
    RateLimitError,
)
from src.core.types import OrderType, Side
from src.trading.services.order_service import (
    OrderService,
    _new_order_link_id,
)


# ---------------------------------------------------------------------------
# Idempotency key generation
# ---------------------------------------------------------------------------

class TestOrderLinkIdGeneration:
    def test_link_id_format(self) -> None:
        """Format is ``ti-<24-hex>`` (27 chars total, fits Bybit V5 36-char limit)."""
        lid = _new_order_link_id()
        assert lid.startswith("ti-")
        body = lid[3:]
        assert len(body) == 24
        assert all(c in "0123456789abcdef" for c in body)
        # Bybit V5 allows up to 36 chars
        assert len(lid) <= 36

    def test_link_id_uniqueness(self) -> None:
        """1000 generated link_ids must all be distinct."""
        ids = {_new_order_link_id() for _ in range(1000)}
        assert len(ids) == 1000


# ---------------------------------------------------------------------------
# orderLinkId is passed to Bybit
# ---------------------------------------------------------------------------

class TestOrderLinkIdInjection:
    @pytest.mark.asyncio
    async def test_link_id_passed_to_bybit(
        self, mock_client, test_db, test_settings
    ) -> None:
        """The Bybit ``place_order`` call receives ``orderLinkId``."""
        svc = OrderService(mock_client, test_db, test_settings)
        with patch.object(
            mock_client, "call", wraps=mock_client.call
        ) as spy:
            await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                stop_loss=68000.0,
            )

        # Find the place_order invocation among any preceding calls
        # (set_leverage, get_tickers for the position-cap check, etc.).
        place_calls = [
            c for c in spy.call_args_list if c.args and c.args[0] == "place_order"
        ]
        assert len(place_calls) == 1
        kwargs = place_calls[0].kwargs
        assert "orderLinkId" in kwargs
        assert kwargs["orderLinkId"].startswith("ti-")
        assert len(kwargs["orderLinkId"]) == 27

    @pytest.mark.asyncio
    async def test_each_call_has_unique_link_id(
        self, mock_client, test_db, test_settings
    ) -> None:
        """Successive calls produce distinct ``orderLinkId``s."""
        svc = OrderService(mock_client, test_db, test_settings)
        with patch.object(
            mock_client, "call", wraps=mock_client.call
        ) as spy:
            for _ in range(3):
                await svc.place_order(
                    symbol="BTCUSDT",
                    side=Side.BUY,
                    order_type=OrderType.MARKET,
                    qty=0.01,
                    stop_loss=68000.0,
                )
        place_calls = [
            c for c in spy.call_args_list if c.args and c.args[0] == "place_order"
        ]
        assert len(place_calls) == 3
        link_ids = {c.kwargs["orderLinkId"] for c in place_calls}
        assert len(link_ids) == 3


# ---------------------------------------------------------------------------
# Validation/business errors propagate without retry
# ---------------------------------------------------------------------------

class TestValidationErrorsNoRetry:
    @pytest.mark.asyncio
    async def test_invalid_order_error_propagates(
        self, mock_client, test_db, test_settings
    ) -> None:
        """``InvalidOrderError`` from Bybit propagates with one Bybit attempt only."""
        svc = OrderService(mock_client, test_db, test_settings)

        attempt_counter = {"n": 0}

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                attempt_counter["n"] += 1
                raise InvalidOrderError(
                    "Bybit error on place_order: [110003] Quantity not valid",
                    details={"retCode": 110003},
                )
            # Other methods (set_leverage, get_tickers, get_instruments_info)
            # delegate to the original mock.
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            with pytest.raises(InvalidOrderError):
                await svc.place_order(
                    symbol="BTCUSDT",
                    side=Side.BUY,
                    order_type=OrderType.MARKET,
                    qty=0.01,
                    stop_loss=68000.0,
                )

        # Exactly one place_order attempt — no retry on Bybit business errors.
        assert attempt_counter["n"] == 1

    @pytest.mark.asyncio
    async def test_rate_limit_error_propagates(
        self, mock_client, test_db, test_settings
    ) -> None:
        """``RateLimitError`` propagates immediately without retry."""
        svc = OrderService(mock_client, test_db, test_settings)
        attempt_counter = {"n": 0}

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                attempt_counter["n"] += 1
                raise RateLimitError(
                    "Bybit error on place_order: [10006] rate limit",
                    details={"retCode": 10006},
                )
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            with pytest.raises(RateLimitError):
                await svc.place_order(
                    symbol="BTCUSDT",
                    side=Side.BUY,
                    order_type=OrderType.MARKET,
                    qty=0.01,
                    stop_loss=68000.0,
                )

        assert attempt_counter["n"] == 1


# ---------------------------------------------------------------------------
# Transient errors retry exactly once with same link_id
# ---------------------------------------------------------------------------

class TestTransientErrorRetry:
    @pytest.mark.asyncio
    async def test_transient_error_retried_once_with_same_link_id(
        self, mock_client, test_db, test_settings
    ) -> None:
        """A non-Bybit exception triggers one retry; the second attempt
        reuses the same ``orderLinkId``.
        """
        svc = OrderService(mock_client, test_db, test_settings)
        attempts = []

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                attempts.append(kwargs.get("orderLinkId"))
                if len(attempts) == 1:
                    # First attempt: simulate a network blip (NOT BybitAPIError).
                    raise OSError("Connection reset by peer")
                # Second attempt: succeed.
                return {"orderId": "retried-order-id", "orderLinkId": kwargs["orderLinkId"]}
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            order = await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                stop_loss=68000.0,
            )

        assert len(attempts) == 2
        assert attempts[0] is not None
        assert attempts[0] == attempts[1], "retry must reuse the original orderLinkId"
        assert order.order_id == "retried-order-id"

    @pytest.mark.asyncio
    async def test_transient_error_retry_budget_exhausted(
        self, mock_client, test_db, test_settings
    ) -> None:
        """Two consecutive transient failures raise — no third attempt."""
        svc = OrderService(mock_client, test_db, test_settings)
        attempts = []

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                attempts.append(kwargs.get("orderLinkId"))
                raise OSError("Connection reset by peer")
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            with pytest.raises(OSError):
                await svc.place_order(
                    symbol="BTCUSDT",
                    side=Side.BUY,
                    order_type=OrderType.MARKET,
                    qty=0.01,
                    stop_loss=68000.0,
                )

        assert len(attempts) == 2  # initial + 1 retry, no third attempt
        assert attempts[0] == attempts[1]


# ---------------------------------------------------------------------------
# Duplicate-link-id recovery (the safety guarantee)
# ---------------------------------------------------------------------------

class TestDuplicateLinkIdRecovery:
    @pytest.mark.asyncio
    async def test_duplicate_link_id_recovers_via_open_orders(
        self, mock_client, test_db, test_settings
    ) -> None:
        """When Bybit returns ``DuplicateOrderLinkIdError``, the canonical
        order is recovered via ``get_open_orders`` and the call returns
        successfully — NO second order is placed.
        """
        svc = OrderService(mock_client, test_db, test_settings)
        place_attempts = []
        recovery_called = {"open": False, "history": False}

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                place_attempts.append(kwargs.get("orderLinkId"))
                # Simulate: prior submission of this link_id already won.
                raise DuplicateOrderLinkIdError(
                    "Bybit error on place_order: [110072] OrderLinkID is duplicate",
                    details={"retCode": 110072},
                )
            if method == "get_open_orders":
                recovery_called["open"] = True
                return {
                    "list": [
                        {
                            "orderId": "canonical-bybit-id",
                            "orderLinkId": kwargs.get("orderLinkId", ""),
                        }
                    ]
                }
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            order = await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                stop_loss=68000.0,
            )

        # Exactly ONE place_order attempt (no retry on dedup).
        assert len(place_attempts) == 1
        assert recovery_called["open"] is True
        assert order.order_id == "canonical-bybit-id"

    @pytest.mark.asyncio
    async def test_duplicate_link_id_falls_through_to_history(
        self, mock_client, test_db, test_settings
    ) -> None:
        """If open-orders lookup is empty (filled market), recovery falls
        through to ``get_order_history`` and still returns successfully.
        """
        svc = OrderService(mock_client, test_db, test_settings)
        recovery_called = {"open": False, "history": False}

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                raise DuplicateOrderLinkIdError(
                    "Bybit error on place_order: [110072] OrderLinkID is duplicate",
                    details={"retCode": 110072},
                )
            if method == "get_open_orders":
                recovery_called["open"] = True
                return {"list": []}  # already filled — not in active book
            if method == "get_order_history":
                recovery_called["history"] = True
                return {
                    "list": [
                        {
                            "orderId": "filled-history-id",
                            "orderLinkId": kwargs.get("orderLinkId", ""),
                        }
                    ]
                }
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            order = await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                stop_loss=68000.0,
            )

        assert recovery_called["open"] is True
        assert recovery_called["history"] is True
        assert order.order_id == "filled-history-id"

    @pytest.mark.asyncio
    async def test_duplicate_link_id_with_no_recovery_returns_synthetic(
        self, mock_client, test_db, test_settings
    ) -> None:
        """Lookup failures produce a synthetic ``DEDUP-<link_id>`` order_id
        rather than re-placing the order. Position reconciliation will
        recover the real id on the next sync.
        """
        svc = OrderService(mock_client, test_db, test_settings)

        async def _fake_call(method: str, **kwargs):
            if method == "place_order":
                raise DuplicateOrderLinkIdError(
                    "Bybit error on place_order: [110072] OrderLinkID is duplicate",
                    details={"retCode": 110072},
                )
            if method == "get_open_orders":
                return {"list": []}
            if method == "get_order_history":
                return {"list": []}
            return await type(mock_client).call(mock_client, method, **kwargs)

        with patch.object(mock_client, "call", side_effect=_fake_call):
            order = await svc.place_order(
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=0.01,
                stop_loss=68000.0,
            )

        assert order.order_id.startswith("DEDUP-ti-")
