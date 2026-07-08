"""T3-1 / F-4 bybit_demo safety gates smoke tests (six-tier-fixes 2026-05-11).

Pure-function tests for the four new gate functions in
src/trading/services/order_guards.py.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─────────────────────── Gate 1: mandatory SL ───────────────────────


def test_mandatory_sl_rejects_none_stop_loss():
    from src.trading.services.order_guards import check_mandatory_sl_for_bybit_demo
    allowed, reason = check_mandatory_sl_for_bybit_demo(
        stop_loss=None, purpose="layer3_entry",
    )
    assert allowed is False
    assert reason == "mandatory_sl_missing"


def test_mandatory_sl_rejects_zero_stop_loss():
    from src.trading.services.order_guards import check_mandatory_sl_for_bybit_demo
    allowed, _ = check_mandatory_sl_for_bybit_demo(
        stop_loss=0.0, purpose="layer3_entry",
    )
    assert allowed is False


def test_mandatory_sl_allows_positive_stop_loss():
    from src.trading.services.order_guards import check_mandatory_sl_for_bybit_demo
    allowed, _ = check_mandatory_sl_for_bybit_demo(
        stop_loss=1.234, purpose="layer3_entry",
    )
    assert allowed is True


def test_mandatory_sl_bypasses_for_layer4_close():
    """layer4_close legitimately closes positions — no SL required."""
    from src.trading.services.order_guards import check_mandatory_sl_for_bybit_demo
    allowed, _ = check_mandatory_sl_for_bybit_demo(
        stop_loss=None, purpose="layer4_close",
    )
    assert allowed is True


# ─────────────────────── Gate 2: leverage cap ───────────────────────


def test_leverage_cap_rejects_over_max():
    from src.trading.services.order_guards import check_leverage_cap_for_bybit_demo
    allowed, _ = check_leverage_cap_for_bybit_demo(leverage=50, max_leverage=25)
    assert allowed is False


def test_leverage_cap_allows_at_max():
    from src.trading.services.order_guards import check_leverage_cap_for_bybit_demo
    allowed, _ = check_leverage_cap_for_bybit_demo(leverage=25, max_leverage=25)
    assert allowed is True


def test_leverage_cap_skips_none_leverage():
    from src.trading.services.order_guards import check_leverage_cap_for_bybit_demo
    allowed, _ = check_leverage_cap_for_bybit_demo(leverage=None, max_leverage=25)
    assert allowed is True


# ────── Gate 3 + 4: position-size cap + per-trade max-loss ──────


class _StubAccountService:
    """Minimal stub for account_service.get_wallet_balance."""

    def __init__(self, equity: float):
        self._equity = equity

    async def get_wallet_balance(self):
        return SimpleNamespace(total_equity=self._equity)


class _StubMarketService:
    def __init__(self, price: float):
        self._price = price

    async def get_ticker(self, symbol):
        return SimpleNamespace(last_price=self._price)


@pytest.mark.asyncio
async def test_position_size_cap_rejects_oversized_notional():
    from src.trading.services.order_guards import (
        check_position_size_and_max_loss_for_bybit_demo,
    )

    services = {
        "account_service": _StubAccountService(equity=10000.0),
        "market_service": _StubMarketService(price=100.0),
    }
    settings = SimpleNamespace(risk=SimpleNamespace(max_position_size_pct=5.0))
    # 100 qty * $100 price = $10,000 notional vs $500 max (5% of $10k).
    allowed, reason, tel = await check_position_size_and_max_loss_for_bybit_demo(
        services=services, settings=settings,
        symbol="TESTUSDT", qty=100.0,
        stop_loss=99.0, leverage=1, price=100.0,
    )
    assert allowed is False
    assert reason == "position_size_cap_exceeded"
    assert tel["notional"] == 10000.0
    assert tel["max_usd"] == 500.0


@pytest.mark.asyncio
async def test_per_trade_max_loss_rejects_over_2pct_equity():
    from src.trading.services.order_guards import (
        check_position_size_and_max_loss_for_bybit_demo,
    )

    services = {
        "account_service": _StubAccountService(equity=10000.0),
        "market_service": _StubMarketService(price=100.0),
    }
    settings = SimpleNamespace(risk=SimpleNamespace(max_position_size_pct=100.0))
    # SL distance $5 * qty 100 * lev 1 = $500 potential loss vs $200 max (2% of $10k).
    allowed, reason, tel = await check_position_size_and_max_loss_for_bybit_demo(
        services=services, settings=settings,
        symbol="TESTUSDT", qty=100.0,
        stop_loss=95.0, leverage=1, price=100.0,
    )
    assert allowed is False
    assert reason == "per_trade_max_loss_exceeded"


@pytest.mark.asyncio
async def test_safety_gates_fail_open_on_missing_account_service():
    """No account_service => fail open (do not halt all trading on infra hiccup)."""
    from src.trading.services.order_guards import (
        check_position_size_and_max_loss_for_bybit_demo,
    )
    settings = SimpleNamespace(risk=SimpleNamespace(max_position_size_pct=5.0))
    allowed, reason, tel = await check_position_size_and_max_loss_for_bybit_demo(
        services={}, settings=settings,
        symbol="TESTUSDT", qty=10.0,
        stop_loss=99.0, leverage=1, price=100.0,
    )
    assert allowed is True
    assert "warn" in tel


@pytest.mark.asyncio
async def test_safety_gates_accept_transformer_short_keys():
    """Gates resolve services from short keys ("account") AND long keys.

    Transformer._active_services keys with the short form; tests and
    WorkerManager use the long form. The gate must look up both so the
    same helper is callable from either context.
    """
    from src.trading.services.order_guards import (
        check_position_size_and_max_loss_for_bybit_demo,
    )
    services_short = {
        "account": _StubAccountService(equity=10000.0),
        "market": _StubMarketService(price=100.0),
    }
    settings = SimpleNamespace(risk=SimpleNamespace(max_position_size_pct=5.0))
    allowed, reason, tel = await check_position_size_and_max_loss_for_bybit_demo(
        services=services_short, settings=settings,
        symbol="TESTUSDT", qty=100.0,
        stop_loss=99.0, leverage=1, price=100.0,
    )
    # 100 * 100 = $10k notional vs $500 max (5%) — rejected.
    assert allowed is False
    assert reason == "position_size_cap_exceeded"


@pytest.mark.asyncio
async def test_post_place_sl_verify_accepts_short_position_key():
    """verify_post_place_sl_for_bybit_demo resolves "position" short key too."""
    from src.trading.services.order_guards import verify_post_place_sl_for_bybit_demo
    services_short = {"position": _StubPositionServiceWithSL(attached_sl=100.0)}
    ok, reason, tel = await verify_post_place_sl_for_bybit_demo(
        services=services_short, symbol="TESTUSDT", expected_sl=100.0,
    )
    assert ok is True


# ─────────────────── Gate 6: post-place SL verify ───────────────────


class _StubPositionServiceWithSL:
    def __init__(self, attached_sl: float | None):
        self._sl = attached_sl

    async def get_position(self, symbol):
        return SimpleNamespace(stop_loss=self._sl)


class _StubPositionServiceNoPosition:
    async def get_position(self, symbol):
        return None


@pytest.mark.asyncio
async def test_post_place_sl_verify_passes_when_sl_attached():
    from src.trading.services.order_guards import verify_post_place_sl_for_bybit_demo
    services = {"position_service": _StubPositionServiceWithSL(attached_sl=100.0)}
    ok, reason, tel = await verify_post_place_sl_for_bybit_demo(
        services=services, symbol="TESTUSDT", expected_sl=100.0,
    )
    assert ok is True
    assert tel["drift_pct"] < 0.001


@pytest.mark.asyncio
async def test_post_place_sl_verify_fails_when_sl_missing():
    from src.trading.services.order_guards import verify_post_place_sl_for_bybit_demo
    services = {"position_service": _StubPositionServiceWithSL(attached_sl=None)}
    ok, reason, _ = await verify_post_place_sl_for_bybit_demo(
        services=services, symbol="TESTUSDT", expected_sl=100.0,
    )
    assert ok is False
    assert reason == "stop_loss_not_attached"


@pytest.mark.asyncio
async def test_post_place_sl_verify_fails_on_drift_beyond_tolerance():
    from src.trading.services.order_guards import verify_post_place_sl_for_bybit_demo
    services = {"position_service": _StubPositionServiceWithSL(attached_sl=80.0)}
    # 80 vs expected 100 = 20% drift; tolerance default 5%.
    ok, reason, tel = await verify_post_place_sl_for_bybit_demo(
        services=services, symbol="TESTUSDT", expected_sl=100.0,
    )
    assert ok is False
    assert reason == "stop_loss_drift"
    assert tel["drift_pct"] >= 5.0


@pytest.mark.asyncio
async def test_post_place_sl_verify_skips_when_no_expected_sl():
    from src.trading.services.order_guards import verify_post_place_sl_for_bybit_demo
    services = {"position_service": _StubPositionServiceWithSL(attached_sl=100.0)}
    ok, reason, _ = await verify_post_place_sl_for_bybit_demo(
        services=services, symbol="TESTUSDT", expected_sl=None,
    )
    assert ok is True
    assert reason == "no_expected_sl"
