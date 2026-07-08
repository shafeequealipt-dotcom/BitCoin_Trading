"""Bybit REST API client wrapper.

Central client that wraps pybit.unified_trading.HTTP. All trading services
use this client for API access. Handles response validation, rate limiting,
error translation, and safety assertions.
"""

import asyncio
from typing import Any

from pybit.unified_trading import HTTP

from src.config.settings import Settings
from src.core.decorators import rate_limit, retry, timed
from src.core.exceptions import (
    AuthenticationError,
    BybitAPIError,
    DuplicateOrderLinkIdError,
    InsufficientBalanceError,
    InvalidOrderError,
    PositionError,
    RateLimitError,
)
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.trading.auth import BybitAuth

log = get_logger("trading")

# Bybit retCode constants
RC_OK = 0
RC_RATE_LIMIT = 10006
RC_INVALID_API_KEY = 10003
RC_INVALID_SIGN = 10004
RC_DUPLICATE_ORDER_LINK_ID = 110072

# Extended error code mapping for specific exception types.
#
# Bybit V5 error code reference:
#   10001 — Request parameter error (request-shape problem, NOT a
#           balance issue). Falls through to the generic BybitAPIError;
#           caller treats it as a programmer / config bug, not a
#           transient runtime condition. The pre-2026-04 mapping
#           (10001 → InsufficientBalanceError) was wrong: callers
#           handling InsufficientBalanceError would back off / retry
#           assuming a wallet-balance issue while the real root cause
#           was a malformed parameter.
#   10003 / 10004 — Auth errors.
#   10006 — Rate limited.
#   110xxx — Order- and position-specific errors per V5 unified API.
BYBIT_ERROR_MAP: dict[int, type[Exception]] = {
    10003: AuthenticationError,         # Invalid API key
    10004: AuthenticationError,         # Invalid signature
    10006: RateLimitError,              # Rate limited
    110001: InvalidOrderError,          # Order not found
    110003: InvalidOrderError,          # Quantity not valid
    110007: PositionError,               # Position not exists
    110012: InsufficientBalanceError,   # Insufficient balance for order
    110043: InsufficientBalanceError,   # Insufficient available balance
    110044: InvalidOrderError,          # Insufficient balance after SL
    110045: InvalidOrderError,          # Leverage not modified
    110072: DuplicateOrderLinkIdError,  # OrderLinkID is duplicate (idempotency hit)
}


class BybitClient:
    """Bybit REST API client with safety guards and rate limiting.

    Args:
        settings: Application settings.
        db: Database manager for persistence.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self._settings = settings
        self._db = db
        self._session: HTTP | None = None
        self._auth: BybitAuth | None = None
        self._connected = False

        # Safety assertion: prevent accidental live trading
        # Allow mainnet data in "shadow" mode — Transformer routes orders to Shadow (paper)
        # Only block if mode is explicitly "paper" (old testnet mode)
        if not settings.bybit.testnet and settings.general.mode == "paper":
            raise RuntimeError(
                "SAFETY: bybit.testnet is False but mode is 'paper'. "
                "Set general.mode = 'shadow' or 'live' to use mainnet data."
            )

    @property
    def session(self) -> HTTP:
        """Return the active pybit HTTP session.

        Raises:
            RuntimeError: If not connected.
        """
        if self._session is None:
            raise RuntimeError("BybitClient not connected. Call connect() first.")
        return self._session

    @property
    def is_testnet(self) -> bool:
        """Whether the client is connected to testnet."""
        return self._settings.bybit.testnet

    @property
    def is_connected(self) -> bool:
        """Whether the client has an active connection."""
        return self._connected

    async def connect(self) -> None:
        """Initialize the pybit session and validate credentials.

        Creates the HTTP session and verifies API keys are valid
        by making a lightweight API call.

        Raises:
            AuthenticationError: If credentials are invalid or empty.
        """
        bybit = self._settings.bybit

        self._auth = BybitAuth(bybit.api_key, bybit.api_secret)

        self._session = HTTP(
            testnet=bybit.testnet,
            api_key=bybit.api_key,
            api_secret=bybit.api_secret,
            recv_window=bybit.recv_window,
        )

        log.info(
            "Connecting to Bybit {env}...",
            env="testnet" if bybit.testnet else "MAINNET",
        )

        # Validate credentials (non-fatal in shadow mode — public data still works)
        try:
            await self._auth.validate_credentials(self._session)
        except Exception as e:
            if self._settings.general.mode == "shadow":
                log.warning(
                    "Bybit credential validation failed (OK for shadow mode — public data works): {err}",
                    err=str(e)[:200],
                )
            else:
                raise
        self._connected = True

        log.info(
            "Bybit client connected ({env})",
            env="testnet" if bybit.testnet else "MAINNET",
        )

    async def disconnect(self) -> None:
        """Clean up the client session."""
        self._session = None
        self._connected = False
        log.info("Bybit client disconnected")

    @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,))
    @rate_limit(calls_per_second=10.0)
    @timed
    async def call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        """Execute a pybit API method with rate limiting, retry, and error handling.

        This is the central method all services should use for API calls.
        It runs the synchronous pybit method in a thread to avoid blocking
        the event loop.

        Args:
            method: Name of the pybit HTTP method (e.g. "get_tickers").
            **kwargs: Arguments to pass to the method.

        Returns:
            The "result" dict from the Bybit response.

        Raises:
            BybitAPIError: If the API returns a non-zero retCode.
            RateLimitError: If rate limited (retCode 10006).
            AuthenticationError: If auth fails.
        """
        session = self.session
        func = getattr(session, method, None)
        if func is None:
            raise BybitAPIError(
                f"Unknown pybit method: {method}",
                details={"method": method},
            )

        response = await asyncio.to_thread(func, **kwargs)
        return self._handle_response(response, method)

    def _handle_response(self, response: dict[str, Any], operation: str) -> dict[str, Any]:
        """Validate a Bybit API response and return the result payload.

        Args:
            response: Raw pybit response dict.
            operation: Name of the operation for logging.

        Returns:
            The "result" portion of the response.

        Raises:
            BybitAPIError: On non-zero retCode.
            RateLimitError: On rate limit (10006).
            AuthenticationError: On auth errors (10003, 10004).
        """
        ret_code = response.get("retCode", -1)
        ret_msg = response.get("retMsg", "Unknown")
        result = response.get("result", {})

        if ret_code == RC_OK:
            log.debug("{op} succeeded", op=operation)
            return result

        details = {
            "retCode": ret_code,
            "retMsg": ret_msg,
            "operation": operation,
        }

        # Map Bybit error code to specific exception type
        error_class = BYBIT_ERROR_MAP.get(ret_code, BybitAPIError)
        raise error_class(
            f"Bybit error on {operation}: [{ret_code}] {ret_msg}",
            details=details,
        )
