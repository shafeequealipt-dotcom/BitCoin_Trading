"""Smoke test for Bybit retCode → project exception translation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.bybit_demo.bybit_demo_client import _log_ret_code, _translate_ret_code
from src.core.exceptions import (
    BybitAPIError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderRejectedError,
    RateLimitError,
)


@pytest.mark.parametrize(
    "ret_code,expected_type",
    [
        (110001, InvalidOrderError),     # Order does not exist
        (110007, InsufficientBalanceError),  # Insufficient balance
        (110045, InsufficientBalanceError),  # Balance insufficient for order
        (110099, OrderRejectedError),    # generic 110xxx → fallback
        (10006, RateLimitError),         # Too many visits
        (10018, RateLimitError),         # IP rate limit
        (10001, BybitAPIError),          # other → generic
        (-1, BybitAPIError),             # malformed → generic
    ],
)
def test_translate_ret_code(ret_code: int, expected_type: type[Exception]) -> None:
    exc = _translate_ret_code(ret_code, "test message", op="test_op")
    assert isinstance(exc, expected_type), (
        f"retCode {ret_code} → {type(exc).__name__}, expected {expected_type.__name__}"
    )
    # Every translated exception carries the ret_code in details for
    # downstream telemetry / MCP tools to inspect.
    assert exc.details["ret_code"] == ret_code  # type: ignore[attr-defined]
    assert exc.details["op"] == "test_op"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "ret_code,expected_tag,expected_method",
    [
        (10002, "BYBIT_DEMO_TIMESTAMP_FAIL", "error"),
        (10003, "BYBIT_DEMO_AUTH_FAIL", "error"),
        (10004, "BYBIT_DEMO_AUTH_FAIL", "error"),
        (10005, "BYBIT_DEMO_AUTH_FAIL", "error"),
        (10006, "BYBIT_DEMO_RATE_LIMIT_HIT", "warning"),
        (10018, "BYBIT_DEMO_RATE_LIMIT_HIT", "warning"),
        (110007, "BYBIT_DEMO_INSUFFICIENT_BALANCE", "warning"),
        (110045, "BYBIT_DEMO_INSUFFICIENT_BALANCE", "warning"),
    ],
)
def test_log_ret_code_emits_specific_tag(
    ret_code: int, expected_tag: str, expected_method: str
) -> None:
    """Verify _log_ret_code routes each bucket to the right severity + tag."""
    mock_log = MagicMock()
    _log_ret_code(mock_log, ret_code, "demo error message", op="some_op")
    method = getattr(mock_log, expected_method)
    assert method.called, (
        f"retCode {ret_code} did not call log.{expected_method} "
        f"(expected tag {expected_tag})"
    )
    emitted = method.call_args[0][0]
    assert expected_tag in emitted
    assert f"code={ret_code}" in emitted
    assert "op=some_op" in emitted


def test_log_ret_code_skips_generic_codes() -> None:
    """Generic 110xxx OrderRejected and other BybitAPIError codes don't tag here.

    Adapter layer emits BYBIT_DEMO_ORDER_REJECT with full request context
    so the client layer skips them to avoid duplicate noise.
    """
    mock_log = MagicMock()
    _log_ret_code(mock_log, 110099, "generic order reject", op="o")
    _log_ret_code(mock_log, 10001, "generic system error", op="o")
    _log_ret_code(mock_log, -1, "malformed", op="o")
    assert not mock_log.error.called
    assert not mock_log.warning.called
    assert not mock_log.info.called
