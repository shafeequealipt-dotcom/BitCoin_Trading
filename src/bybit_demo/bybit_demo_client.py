"""Bybit demo HTTP client — V5 REST wrapper with signing, retry, rate-limit.

Talks to ``https://api-demo.bybit.com``. Re-implements HMAC-SHA256
signing per the Bybit V5 spec rather than reusing pybit because pybit
does not accept a custom base URL parameter (its ``testnet`` flag points
at ``api-testnet.bybit.com``, not the demo environment).

The retry / boot-grace pattern mirrors
``src/shadow/shadow_adapter.py:_shadow_get_with_retry`` — same defaults
(5 attempts, ``0.2 * 2^(n-1)`` backoff, 30 s boot-grace window). This
is intentional: the project's house style for adapter HTTP clients.

Bybit V5 response envelope:
    ``{"retCode": int, "retMsg": str, "result": dict, ...}``
``retCode == 0`` means success. Non-zero is a domain error; the client
translates the most common codes to project exception types
(:class:`InvalidOrderError`, :class:`InsufficientBalanceError`,
:class:`OrderRejectedError`, :class:`RateLimitError`,
:class:`BybitAPIError`).

The split of responsibility is deliberate: the CLIENT raises typed
exceptions, the ADAPTER catches and returns sentinels. That preserves
Shadow's "never raises" contract at the public adapter surface while
still giving the lower layer rich error semantics for telemetry and
future MCP tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from src.core.exceptions import (
    BybitAPIError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderRejectedError,
    RateLimitError,
)
from src.core.log_context import ctx
from src.core.logging import get_logger

# Boot-grace window: during the first ``_BOOT_GRACE_SECONDS`` of the
# process, exhausted retry chains log at DEBUG instead of ERROR. Without
# this, a normal restart races the trading-workers process startup
# against the first wallet/position query and floods the log with
# spurious failures.
_PROCESS_START_MONOTONIC = time.monotonic()
_BOOT_GRACE_SECONDS = 30.0


def _in_boot_grace() -> bool:
    """Return True during the first ``_BOOT_GRACE_SECONDS`` of the process."""
    return (time.monotonic() - _PROCESS_START_MONOTONIC) < _BOOT_GRACE_SECONDS


# --- Bybit V5 error-code → project exception mapping -----------------------
#
# Sourced from https://bybit-exchange.github.io/docs/v5/error . Codes
# below 110000 are auth / system; 110xxx are order / position. We map
# the most common ones to specific exception types and fall back to
# ``OrderRejectedError`` for any other 110xxx and ``BybitAPIError``
# for anything else.

_INVALID_ORDER_CODES = frozenset({
    110001,  # Order does not exist
    110003,  # Order price out of range
    110004,  # Wallet balance is insufficient (handled below)
    110009,  # The number of stop orders exceeds the limit
    110013,  # Order qty exceeds open limit
    110017,  # Order price out of permissible range
    110026,  # Cannot set leverage from the cross margin mode
    110043,  # Set leverage has not been modified
})

_INSUFFICIENT_BALANCE_CODES = frozenset({
    110007,  # Insufficient available balance
    110045,  # Balance insufficient for order
})

_RATE_LIMIT_CODES = frozenset({
    10006,   # Too many visits
    10018,   # IP rate limit exceeded
})


def _translate_ret_code(
    ret_code: int,
    ret_msg: str,
    *,
    op: str,
) -> Exception:
    """Translate a Bybit V5 ``retCode`` to the appropriate project exception.

    The adapter catches whatever this returns and converts to a sentinel
    Order/AccountInfo. The structured exception type lets future MCP
    tools or telemetry distinguish error categories without re-parsing
    the message.
    """
    details = {"ret_code": ret_code, "ret_msg": ret_msg, "op": op}

    if ret_code in _INSUFFICIENT_BALANCE_CODES:
        return InsufficientBalanceError(
            f"Bybit demo: insufficient balance ({ret_code}: {ret_msg})",
            details=details,
        )
    if ret_code in _INVALID_ORDER_CODES:
        return InvalidOrderError(
            f"Bybit demo: invalid order ({ret_code}: {ret_msg})",
            details=details,
        )
    if ret_code in _RATE_LIMIT_CODES:
        return RateLimitError(
            f"Bybit demo: rate limit ({ret_code}: {ret_msg})",
            details=details,
        )
    if 110000 <= ret_code < 120000:
        return OrderRejectedError(
            f"Bybit demo: order rejected ({ret_code}: {ret_msg})",
            details=details,
        )
    return BybitAPIError(
        f"Bybit demo: API error ({ret_code}: {ret_msg})",
        details=details,
    )


# --- Auth / signing / quota retCode buckets for log routing ----------------
#
# Used by the structured-tag emission path in the request loop. Kept
# here next to the _translate_ret_code mapping so adding a new code is
# a single-file edit. These do NOT overlap with _INVALID_ORDER_CODES /
# _INSUFFICIENT_BALANCE_CODES / _RATE_LIMIT_CODES — the auth bucket is
# 1000x system codes that translate to BybitAPIError by default but
# warrant a more specific structured log tag for forensics + alerting.

_AUTH_FAIL_CODES = frozenset({
    10003,  # API key invalid
    10004,  # Sign verification failed (HMAC mismatch)
    10005,  # Permission denied / API key has no permission for endpoint
})

_TIMESTAMP_FAIL_CODES = frozenset({
    10002,  # Request expired (timestamp outside recv_window)
})


def _log_ret_code(
    log: Any,
    ret_code: int,
    ret_msg: str,
    *,
    op: str,
) -> None:
    """Emit a retCode-specific structured tag before the typed exception is raised.

    Routes to the most specific tag the project's alerting layer can
    distinguish. Generic 110xxx OrderRejectedError codes intentionally
    do NOT log here — the adapter layer already emits
    ``BYBIT_DEMO_ORDER_REJECT`` with full request context (symbol, side,
    qty) which is more useful than this layer's bare retCode.
    """
    msg_short = ret_msg[:120]
    if ret_code in _TIMESTAMP_FAIL_CODES:
        log.error(
            f"BYBIT_DEMO_TIMESTAMP_FAIL | code={ret_code} op={op} "
            f"msg='{msg_short}' | {ctx()}"
        )
    elif ret_code in _AUTH_FAIL_CODES:
        log.error(
            f"BYBIT_DEMO_AUTH_FAIL | code={ret_code} op={op} "
            f"msg='{msg_short}' | {ctx()}"
        )
    elif ret_code in _RATE_LIMIT_CODES:
        log.warning(
            f"BYBIT_DEMO_RATE_LIMIT_HIT | code={ret_code} op={op} "
            f"msg='{msg_short}' | {ctx()}"
        )
    elif ret_code in _INSUFFICIENT_BALANCE_CODES:
        log.warning(
            f"BYBIT_DEMO_INSUFFICIENT_BALANCE | code={ret_code} op={op} "
            f"msg='{msg_short}' | {ctx()}"
        )
    # Other 110xxx (OrderRejectedError) and generic BybitAPIError —
    # not tagged here by design. Adapter layer logs ORDER_REJECT with
    # symbol/side/qty context which is richer than retCode alone.


class BybitDemoClient:
    """HTTP client for Bybit demo V5 API.

    Holds an ``aiohttp.ClientSession`` (passed in by the caller — the
    boot wiring in :mod:`src.workers.manager` owns the session lifecycle
    so all adapters share one connection pool). Provides signed and
    unsigned ``GET``/``POST`` methods, with retry + rate-limit awareness.

    Args:
        session: Shared ``aiohttp.ClientSession`` from the boot wiring.
        base_url: Demo API base URL. Default ``https://api-demo.bybit.com``.
        api_key: Bybit demo API key. Read from env in :class:`BybitDemoSettings`.
        api_secret: Bybit demo API secret. Read from env.
        recv_window: Bybit recv-window in milliseconds. Default 10000.
            Issue I1 (F-26, 2026-05-14) raised this from 5000 ms after
            the 2026-05-13 audit captured 4 op=positions + 2 op=balance
            TIMESTAMP_FAIL events correlated with VM pressure. Bybit
            accepts up to 300000 ms; 10000 ms preserves replay-attack
            resistance while absorbing pressure-correlated jitter. The
            retry loop's per-attempt re-sign (see ``_request_with_retry``)
            now also catches 10002 specifically and retries with a fresh
            timestamp, so a transient hit no longer triggers the
            phantom-close cascade.
        timeout_seconds: Per-request timeout. Default 10.
        retry_attempts: Max attempts for transient failures. Default 5.
        retry_base_delay_seconds: Base for exponential backoff. Default 0.2.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        recv_window: int = 10000,
        timeout_seconds: float = 10.0,
        retry_attempts: int = 5,
        retry_base_delay_seconds: float = 0.2,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window = recv_window
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay_seconds
        self._log = get_logger("bybit_demo")
        # Edge-detect state for BYBIT_DEMO_RATE_LIMIT_RECOVERED. Flipped
        # True when a response header reports remaining < 3, flipped back
        # to False (and emits RECOVERED) on the first subsequent response
        # showing remaining >= 3.
        self._rate_limit_active: bool = False

    # ------------------------------------------------------------------ #
    # Signing                                                             #
    # ------------------------------------------------------------------ #

    def _sign(
        self,
        timestamp_ms: int,
        payload: str,
    ) -> str:
        """Compute HMAC-SHA256 signature for a Bybit V5 request.

        Sign payload format: ``timestamp + api_key + recv_window + payload``
        where ``payload`` is the request body JSON for POST or the
        sorted query string for GET. Returns lowercase hex digest.

        Phase 12.5 (lifecycle-logging-audit Gap 5.5-G1): wrap in
        try/except to surface signing failures via structured tag.
        Defensive — hmac.new can raise on malformed key bytes; exposing
        this loudly catches API-key rotation/encoding regressions
        before they cascade into HTTP 401s downstream.
        """
        try:
            sign_payload = (
                f"{timestamp_ms}{self._api_key}{self._recv_window}{payload}"
            )
            return hmac.new(
                self._api_secret.encode("utf-8"),
                sign_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        except Exception as e:
            # Re-raise after structured log so callers get the original
            # exception (don't swallow — sign failure must hard-fail).
            try:
                from src.core.log_context import ctx as _ctx
                from src.core.logging import get_logger
                _log = get_logger("bybit_demo")
                _log.error(
                    f"BYBIT_DEMO_HMAC_FAIL | err='{str(e)[:120]}' "
                    f"reason=sign_payload_or_key_invalid | {_ctx()}"
                )
            except Exception:
                pass  # never block raise on logging failure
            raise

    def _signed_headers(self, timestamp_ms: int, signature: str) -> dict[str, str]:
        """Build the signed-request header dict."""
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": str(timestamp_ms),
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": str(self._recv_window),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _query_string(params: dict[str, Any] | None) -> str:
        """Build a deterministic query string for sign-stable requests."""
        if not params:
            return ""
        # Filter out None values (Bybit rejects them) and stringify.
        clean = {k: str(v) for k, v in params.items() if v is not None}
        # Sort keys to match Bybit's canonical signing order.
        return urlencode(sorted(clean.items()))

    # ------------------------------------------------------------------ #
    # Request helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool,
        op: str,
    ) -> dict[str, Any]:
        """Core request loop. Returns the parsed Bybit response envelope.

        Raises :class:`BybitAPIError` (or a subclass) on non-zero retCode
        or after exhausted retries on transport failures. Callers MUST
        catch and translate to sentinels at the adapter layer.

        Retry policy: 4xx (except 429) → no retry; 429/5xx/network →
        retry up to ``_retry_attempts`` with exponential backoff.
        """
        url = f"{self._base_url}{path}"
        last_err: Exception | None = None

        # Stable across retries — body and query string don't change
        # per-attempt. Only timestamp + signature (which depend on the
        # timestamp) need to be recomputed inside the loop because Bybit
        # validates recv_window against a fresh wall-clock timestamp.
        qs = self._query_string(params)
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        sig_payload = qs if method == "GET" else body_str
        request_url = f"{url}?{qs}" if (method == "GET" and qs) else url

        for attempt in range(1, self._retry_attempts + 1):
            try:
                timestamp_ms = int(time.time() * 1000)
                if signed:
                    signature = self._sign(timestamp_ms, sig_payload)
                    headers = self._signed_headers(timestamp_ms, signature)
                else:
                    headers = {"Content-Type": "application/json"}

                async with self._session.request(
                    method,
                    request_url,
                    headers=headers,
                    data=body_str if method != "GET" else None,
                    timeout=self._timeout,
                ) as resp:
                    # Inspect rate-limit headers for observability.
                    limit_status = resp.headers.get("X-Bapi-Limit-Status")
                    if limit_status is not None:
                        try:
                            remaining = int(limit_status)
                            if remaining < 3:
                                self._rate_limit_active = True
                                self._log.warning(
                                    f"BYBIT_DEMO_RATE_LIMIT | op={op} "
                                    f"remaining={remaining} | {ctx()}"
                                )
                            elif self._rate_limit_active:
                                # Edge: just exited the rate-limit window.
                                self._rate_limit_active = False
                                self._log.info(
                                    f"BYBIT_DEMO_RATE_LIMIT_RECOVERED | op={op} "
                                    f"remaining={remaining} | {ctx()}"
                                )
                        except ValueError:
                            pass

                    # 4xx (non-429) → permanent client error; do NOT retry.
                    if 400 <= resp.status < 500 and resp.status != 429:
                        text = await resp.text()
                        self._log.warning(
                            f"BYBIT_DEMO_HTTP_FAIL | op={op} "
                            f"status={resp.status} body={text[:160]} "
                            f"| {ctx()}"
                        )
                        # When Bybit's edge layer rejects the request
                        # before it reaches the V5 backend (garbage key,
                        # revoked key, missing permission), it returns
                        # HTTP 401/403 with no retCode envelope — the
                        # retCode-specific AUTH_FAIL tag would never
                        # fire. Emit it explicitly here so the alert
                        # relay catches the same trigger uniformly.
                        if resp.status in (401, 403):
                            self._log.error(
                                f"BYBIT_DEMO_AUTH_FAIL | code=http_{resp.status} "
                                f"op={op} msg='{text[:120]}' | {ctx()}"
                            )
                        raise BybitAPIError(
                            f"Bybit demo: HTTP {resp.status} on {op}",
                            details={"status": resp.status, "body": text[:240]},
                        )

                    # 5xx and 429 → transient; raise to retry path.
                    if resp.status >= 500 or resp.status == 429:
                        last_err = aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message=f"HTTP {resp.status}",
                        )
                    else:
                        # 2xx — parse envelope.
                        envelope = await resp.json()
                        ret_code = int(envelope.get("retCode", -1))
                        ret_msg = str(envelope.get("retMsg", ""))

                        if ret_code == 0:
                            return envelope

                        # Non-zero retCode → emit a specific structured
                        # tag (auth / timestamp / rate-limit / balance)
                        # then translate and raise. Logging BEFORE the
                        # raise ensures every retCode is observable even
                        # when the caller silently swallows the typed
                        # exception (e.g., AccountService sentinel path).
                        _log_ret_code(self._log, ret_code, ret_msg, op=op)
                        raise _translate_ret_code(ret_code, ret_msg, op=op)

            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
                last_err = e
            except BybitAPIError as e:
                # Issue I1 (F-26, 2026-05-14) — retry-on-10002 with
                # fresh timestamp. The audited TIMESTAMP_FAIL events
                # were correlated with VM pressure causing the gap
                # between L344 (timestamp_ms = int(time.time() * 1000))
                # and L351 (request send) to exceed recv_window.
                # The next loop iteration re-signs with a fresh
                # timestamp so a transient hit clears on retry.
                #
                # Only retCode 10002 (TIMESTAMP_FAIL) is retryable.
                # Other BybitAPIError subclasses
                # (InsufficientBalanceError, InvalidOrderError,
                # RateLimitError, OrderRejectedError) are persistent
                # and propagate immediately as before. Auth failures
                # (10003/10004/10005) are persistent and propagate.
                _ret_code = (
                    e.details.get("ret_code") if isinstance(e.details, dict) else None
                )
                if _ret_code == 10002:
                    last_err = e
                    self._log.info(
                        f"BYBIT_DEMO_TIMESTAMP_RETRY | op={op} "
                        f"attempt={attempt} max={self._retry_attempts} "
                        f"recv_window_ms={self._recv_window} "
                        f"err='{str(e)[:120]}' | {ctx()}"
                    )
                else:
                    # Non-retryable BybitAPIError — propagate.
                    raise

            # Permanent application errors (raised inside the with-block)
            # propagate out of this except chain because they're not
            # caught by the (ClientError, OSError, TimeoutError) tuple
            # above. Only transient transport / 5xx / 429 fall through
            # here for retry.

            if attempt < self._retry_attempts:
                wait_seconds = self._retry_base_delay * (2 ** (attempt - 1))
                self._log.debug(
                    f"BYBIT_DEMO_RETRY | op={op} attempt={attempt} "
                    f"wait_ms={int(wait_seconds * 1000)} "
                    f"err={str(last_err)[:160]} | {ctx()}"
                )
                await asyncio.sleep(wait_seconds)
                continue

            # Final attempt exhausted on transient failure.
            level = self._log.debug if _in_boot_grace() else self._log.error
            level(
                f"BYBIT_DEMO_CALL_FAIL | op={op} "
                f"attempts={self._retry_attempts} "
                f"err={str(last_err)[:160]} boot_grace={_in_boot_grace()} "
                f"| {ctx()}"
            )
            # Issue I1 (F-26, 2026-05-14) — preserve the original
            # ret_code in the wrapped exception details so the adapter
            # can distinguish exhausted-retry-on-10002 (which must
            # raise GroundTruthUnavailableError per architectural fix)
            # from exhausted-retry-on-network-failure (which still
            # collapses to the sentinel). The wrapping is kept so the
            # exception chain message remains operator-readable.
            _last_err_details = (
                last_err.details if isinstance(last_err, BybitAPIError)
                and isinstance(last_err.details, dict) else {}
            )
            _last_err_ret_code = _last_err_details.get("ret_code")
            raise BybitAPIError(
                f"Bybit demo: {op} failed after {self._retry_attempts} attempts: {last_err}",
                details={
                    "op": op,
                    "last_error": str(last_err)[:240],
                    "ret_code": _last_err_ret_code,
                },
            )

        # Unreachable — the loop either returns or raises on every path.
        raise BybitAPIError(f"Bybit demo: {op} fell out of retry loop")

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = True,
        op: str = "",
    ) -> dict[str, Any]:
        """Signed or unsigned GET. Returns the Bybit response envelope.

        Raises :class:`BybitAPIError` (or subclass) on retCode != 0 or
        exhausted retries. Adapters catch and translate to sentinels.
        """
        return await self._request_with_retry(
            "GET",
            path,
            params=params,
            signed=signed,
            op=op or path,
        )

    async def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        signed: bool = True,
        op: str = "",
    ) -> dict[str, Any]:
        """Signed POST. Returns the Bybit response envelope.

        Raises :class:`BybitAPIError` (or subclass) on retCode != 0 or
        exhausted retries. Adapters catch and translate to sentinels.
        """
        return await self._request_with_retry(
            "POST",
            path,
            body=body,
            signed=signed,
            op=op or path,
        )

    async def health_check(self) -> bool:
        """Probe Bybit demo reachability via /v5/market/time (unsigned).

        Used by the Transformer's startup health check to decide whether
        to surface the "Bybit demo not reachable" warning in worker logs.
        """
        try:
            envelope = await self._request_with_retry(
                "GET",
                "/v5/market/time",
                signed=False,
                op="health_check",
            )
            return int(envelope.get("retCode", -1)) == 0
        except Exception:
            # Health check NEVER raises — boot probe must be silent on
            # failure (same contract Shadow's health_check has).
            return False
