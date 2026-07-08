"""Phase 1 — shadow_adapter boot-grace retry helper tests.

Verifies the boot-grace-aware retry helper added in
``src/shadow/shadow_adapter.py``. The helper exists so a normal restart
sequence (workers + shadow racing on systemd start) doesn't produce a
burst of false-alarm ``Cannot connect to host 127.0.0.1:9090`` ERROR
lines and a fictitious zero-balance state in the fund manager.

See ``dev_notes/phase0_issue_startup_ordering.md`` and
``dev_notes/phase1_boot_ordering_report.md``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from src.shadow import shadow_adapter as sa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal aiohttp-style async context manager response."""

    def __init__(self, status: int, payload: Any | None = None) -> None:
        self.status = status
        self._payload = payload or {}
        self.request_info = MagicMock()
        self.history = ()

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Stub aiohttp.ClientSession that returns a queue of responses or raises."""

    def __init__(self, sequence: list[Any]) -> None:
        # Each item is either a _FakeResponse or an Exception subclass to raise.
        self._sequence = list(sequence)
        self.calls = 0

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls += 1
        item = self._sequence.pop(0) if self._sequence else _FakeResponse(200, {"ok": True})
        if isinstance(item, BaseException):
            raise item
        return item


# ---------------------------------------------------------------------------
# Boot-grace window
# ---------------------------------------------------------------------------


class TestBootGrace:
    def test_grace_active_within_window(self) -> None:
        """At import time, the grace window is fresh — should be active."""
        with patch.object(sa, "_PROCESS_START_MONOTONIC", sa.time.monotonic()):
            assert sa._in_boot_grace() is True

    def test_grace_expires_after_window(self) -> None:
        """After the grace seconds elapse, the helper returns False."""
        # Pretend the process started 2× the grace window ago.
        fake_start = sa.time.monotonic() - (sa._BOOT_GRACE_SECONDS * 2)
        with patch.object(sa, "_PROCESS_START_MONOTONIC", fake_start):
            assert sa._in_boot_grace() is False


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


class TestShadowGetWithRetry:
    @pytest.mark.asyncio
    async def test_first_attempt_success(self) -> None:
        """A 200 on the first try short-circuits — no retry, no sleep."""
        log = MagicMock()
        session = _FakeSession([_FakeResponse(200, {"hello": "world"})])
        result = await sa._shadow_get_with_retry(
            session, "http://x/y", log=log, op="test", attempts=5, base_delay=0.001
        )
        assert result == {"hello": "world"}
        assert session.calls == 1
        log.error.assert_not_called()
        log.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self) -> None:
        """Connection errors retry up to ``attempts`` times before giving up."""
        log = MagicMock()
        session = _FakeSession([
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
            _FakeResponse(200, {"recovered": True}),
        ])
        result = await sa._shadow_get_with_retry(
            session, "http://x/y", log=log, op="balance",
            attempts=5, base_delay=0.001,
        )
        assert result == {"recovered": True}
        assert session.calls == 3

    @pytest.mark.asyncio
    async def test_exhausted_in_grace_window_logs_debug(self) -> None:
        """Boot-grace active + retries exhausted -> DEBUG (not ERROR)."""
        log = MagicMock()
        session = _FakeSession([
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
        ])
        with patch.object(sa, "_PROCESS_START_MONOTONIC", sa.time.monotonic()):
            result = await sa._shadow_get_with_retry(
                session, "http://x/y", log=log, op="balance",
                attempts=3, base_delay=0.001,
            )
        assert result is None
        assert session.calls == 3
        log.debug.assert_called_once()
        log.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_exhausted_after_grace_logs_error(self) -> None:
        """After grace expires, exhausted retries log ERROR."""
        log = MagicMock()
        session = _FakeSession([
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
        ])
        # Pretend process started long ago → grace window expired.
        fake_start = sa.time.monotonic() - (sa._BOOT_GRACE_SECONDS * 2)
        with patch.object(sa, "_PROCESS_START_MONOTONIC", fake_start):
            result = await sa._shadow_get_with_retry(
                session, "http://x/y", log=log, op="balance",
                attempts=2, base_delay=0.001,
            )
        assert result is None
        log.error.assert_called_once()
        log.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_4xx_returns_none_without_retry(self) -> None:
        """4xx (client errors, except 429) are not transient; no retry."""
        log = MagicMock()
        session = _FakeSession([_FakeResponse(404)])
        result = await sa._shadow_get_with_retry(
            session, "http://x/y", log=log, op="position",
            attempts=5, base_delay=0.001,
        )
        assert result is None
        assert session.calls == 1  # No retry on 404
        log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_429_retries(self) -> None:
        """429 (rate-limit) IS transient — retried."""
        log = MagicMock()
        session = _FakeSession([
            _FakeResponse(429),
            _FakeResponse(200, {"ok": True}),
        ])
        result = await sa._shadow_get_with_retry(
            session, "http://x/y", log=log, op="balance",
            attempts=5, base_delay=0.001,
        )
        assert result == {"ok": True}
        assert session.calls == 2

    @pytest.mark.asyncio
    async def test_get_wallet_balance_uses_helper(self) -> None:
        """``ShadowAccountService.get_wallet_balance`` returns empty AccountInfo on exhausted retries."""
        from src.shadow.shadow_adapter import ShadowAccountService
        session = _FakeSession([
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
            aiohttp.ClientConnectionError("refused"),
        ])
        svc = ShadowAccountService(session, "http://x")  # type: ignore[arg-type]
        with patch.object(sa, "_PROCESS_START_MONOTONIC", sa.time.monotonic()):
            info = await svc.get_wallet_balance()
        # Empty AccountInfo: total_equity == 0
        assert info.total_equity == 0.0
        assert session.calls == 5
