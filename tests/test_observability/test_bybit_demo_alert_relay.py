"""Tests for src.observability.bybit_demo_alert_relay.BybitDemoAlertRelay."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from loguru import logger as loguru_logger

from src.core.logging import get_logger
from src.core.types import AlertLevel
from src.observability.bybit_demo_alert_relay import (
    BybitDemoAlertRelay,
    _extract_tag,
)


class _MockAlertManager:
    """Minimal AlertManager stand-in that records every call."""

    def __init__(self) -> None:
        self.error_calls: list[tuple[str, str, AlertLevel]] = []
        self.risk_calls: list[tuple[str, dict]] = []

    async def send_error_alert(
        self,
        component: str,
        error_message: str,
        severity: AlertLevel = AlertLevel.WARNING,
    ) -> None:
        self.error_calls.append((component, error_message, severity))

    async def send_risk_warning(self, warning_type: str, details: dict) -> None:
        self.risk_calls.append((warning_type, details))


# ---------------------------------------------------------------------- #
# _extract_tag                                                            #
# ---------------------------------------------------------------------- #


def test_extract_tag_returns_first_token() -> None:
    assert _extract_tag("BYBIT_DEMO_AUTH_FAIL | code=10003 | did=") == "BYBIT_DEMO_AUTH_FAIL"
    assert _extract_tag("EXCHANGE_SWITCH_RESTART_FAIL | err=x") == "EXCHANGE_SWITCH_RESTART_FAIL"


def test_extract_tag_handles_edge_cases() -> None:
    assert _extract_tag("") is None
    assert _extract_tag("| just-pipes") is None
    assert _extract_tag("free-text with no tag") == "free-text with no tag" or _extract_tag("free-text with no tag") is None  # not whitelisted upstream


# ---------------------------------------------------------------------- #
# Relay dispatch — full integration via loguru                            #
# ---------------------------------------------------------------------- #


async def _wait_for(predicate, timeout: float = 1.0, step: float = 0.02) -> bool:
    """Poll until predicate returns truthy, up to timeout. Returns True if hit."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(step)
        elapsed += step
    return False


@pytest.mark.asyncio
async def test_relay_routes_auth_fail_to_critical_error_alert() -> None:
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        bd_log = get_logger("bybit_demo")
        bd_log.error("BYBIT_DEMO_AUTH_FAIL | code=10003 op=balance | did=d-1")
        # Loguru with enqueue=True and the relay's run_coroutine_threadsafe
        # both add latency — wait for the dispatch.
        ok = await _wait_for(lambda: len(am.error_calls) >= 1, timeout=2.0)
    finally:
        relay.unregister()

    assert ok, "alert_manager.send_error_alert was not called within timeout"
    component, msg, level = am.error_calls[0]
    assert component == "bybit_demo"
    assert "BYBIT_DEMO_AUTH_FAIL" in msg
    assert level == AlertLevel.CRITICAL


@pytest.mark.asyncio
async def test_relay_routes_boot_fail_to_risk_warning() -> None:
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        bd_log = get_logger("bybit_demo")
        bd_log.error(
            "BYBIT_DEMO_BOOT_FAIL | step=health_check url=https://api-demo.bybit.com"
        )
        ok = await _wait_for(lambda: len(am.risk_calls) >= 1, timeout=2.0)
    finally:
        relay.unregister()

    assert ok, "alert_manager.send_risk_warning was not called within timeout"
    warning_type, details = am.risk_calls[0]
    assert warning_type == "bybit_demo_boot"
    assert "BYBIT_DEMO_BOOT_FAIL" in details["log_line"]


@pytest.mark.asyncio
async def test_relay_routes_rate_limit_to_warning() -> None:
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        bd_log = get_logger("bybit_demo")
        bd_log.warning("BYBIT_DEMO_RATE_LIMIT_HIT | code=10006 op=place_order")
        ok = await _wait_for(lambda: len(am.error_calls) >= 1, timeout=2.0)
    finally:
        relay.unregister()

    assert ok
    component, msg, level = am.error_calls[0]
    assert component == "bybit_demo_rate_limit"
    assert level == AlertLevel.WARNING


@pytest.mark.asyncio
async def test_relay_routes_exchange_switch_restart_fail_to_risk_warning() -> None:
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        # Switcher uses the worker logger — confirm the relay accepts it.
        worker_log = get_logger("worker")
        worker_log.error("EXCHANGE_SWITCH_RESTART_FAIL | err=systemctl_unavailable")
        ok = await _wait_for(lambda: len(am.risk_calls) >= 1, timeout=2.0)
    finally:
        relay.unregister()

    assert ok
    warning_type, details = am.risk_calls[0]
    assert warning_type == "exchange_switch_restart"


@pytest.mark.asyncio
async def test_relay_ignores_non_trigger_tags() -> None:
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        bd_log = get_logger("bybit_demo")
        # ORD_SEND, POSITION_CLOSE, ORD_RESP are normal operational
        # events — must NOT fire alerts.
        # P10 of P1-P10 added BYBIT_DEMO_ORDER_REJECT to triggers
        # (the original test used it as a "won't fire" example).
        # We now use BYBIT_DEMO_ORD_RESP — a normal operational receipt
        # that still belongs to the never-trigger set.
        bd_log.info("BYBIT_DEMO_ORD_SEND | sym=BTCUSDT side=Buy qty=0.001")
        bd_log.info("BYBIT_DEMO_POSITION_CLOSE | sym=BTCUSDT purpose=layer4_close")
        bd_log.warning("BYBIT_DEMO_ORD_RESP | sym=BTCUSDT oid=abc fill=80000")
        # Wait a moment to be sure no dispatch is queued.
        await asyncio.sleep(0.3)
    finally:
        relay.unregister()

    assert am.error_calls == []
    assert am.risk_calls == []


@pytest.mark.asyncio
async def test_relay_ignores_other_components() -> None:
    """Records from non-observed components must be filtered out."""
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        # The dashboard / brain / strategy components should never
        # trigger the bybit_demo relay even if they happen to log a
        # string starting with one of the trigger tags.
        dash_log = get_logger("dashboard")
        dash_log.error("BYBIT_DEMO_AUTH_FAIL | spoofed from dashboard")
        await asyncio.sleep(0.3)
    finally:
        relay.unregister()

    assert am.error_calls == []
    assert am.risk_calls == []


@pytest.mark.asyncio
async def test_relay_register_is_idempotent_and_unregister_clean() -> None:
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    sink_id_1 = relay.register()
    sink_id_2 = relay.register()
    assert sink_id_1 == sink_id_2  # idempotent
    relay.unregister()
    relay.unregister()  # safe to call twice


@pytest.mark.asyncio
async def test_relay_sink_does_not_raise_on_invalid_record() -> None:
    """Sink resilience — bad records must not propagate exceptions."""
    am = _MockAlertManager()
    loop = asyncio.get_running_loop()
    relay = BybitDemoAlertRelay(am, loop=loop)
    relay.register()
    try:
        # Valid component + tag prefix but no further fields. Sink
        # should run cleanly and dispatch.
        bd_log = get_logger("bybit_demo")
        bd_log.error("BYBIT_DEMO_AUTH_FAIL")  # no pipe, no fields
        ok = await _wait_for(lambda: len(am.error_calls) >= 1, timeout=2.0)
    finally:
        relay.unregister()

    assert ok
