"""Unit tests for TradeCoordinator.resolve_authoritative_pnl.

Covers Phase 1 of the price-source divergence fix
(``IMPLEMENT_PRICE_SOURCE_DEFINITIVE_FIX_INDEPTH.md``). The helper extends
the existing external-detection path's authoritative-reconciliation
pattern at ``position_watchdog.py:2569-2578`` to the 11 self-initiated
close sites in ``position_watchdog.py`` and ``profit_sniper.py``.

Test cases:
    1. Shadow returns well-formed dict with net_pnl_usd / net_pnl_pct /
       exit_price → helper returns shadow_authoritative tuple.
    2. Shadow returns None (Bybit live mode or no closed row) → helper
       returns local_fallback tuple, INFO log emitted.
    3. Shadow raises an exception (transport failure) → helper returns
       local_fallback tuple, WARNING log emitted.
    4. Shadow returns dict with missing fields → helper returns
       local_fallback tuple, WARNING log emitted.
    5. position_service has no get_last_close attribute (test mock /
       non-standard service) → helper returns local_fallback silently.
    6. exit_price field present but malformed → helper still returns
       authoritative pnl values, exit_price=None.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trade_coordinator import TradeCoordinator


@pytest.fixture
def coordinator() -> TradeCoordinator:
    return TradeCoordinator()


@pytest.fixture
def position_service_mock() -> MagicMock:
    svc = MagicMock()
    svc.get_last_close = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_shadow_authoritative_full_dict(coordinator, position_service_mock):
    """Shadow returns a complete close record → use Shadow's net_pnl_usd."""
    position_service_mock.get_last_close.return_value = {
        "symbol": "ONDOUSDT",
        "side": "Buy",
        "entry_price": 0.270081,
        "exit_price": 0.26971906,
        "quantity": 1025.0,
        "net_pnl_usd": -0.5232,
        "net_pnl_pct": -0.189,
        "close_trigger": "manual",
    }
    pnl_usd, pnl_pct, src, exit_price = (
        await coordinator.resolve_authoritative_pnl(
            symbol="ONDOUSDT",
            position_service=position_service_mock,
            fallback_pnl_usd=-0.288,  # main project's locally-computed (wrong) value
            fallback_pnl_pct=-0.104,
        )
    )
    assert pnl_usd == pytest.approx(-0.5232)
    assert pnl_pct == pytest.approx(-0.189)
    assert src == "exchange_authoritative"
    assert exit_price == pytest.approx(0.26971906)
    position_service_mock.get_last_close.assert_awaited_once_with("ONDOUSDT")


@pytest.mark.asyncio
async def test_shadow_returns_none_falls_back_to_local(
    coordinator, position_service_mock
):
    """Shadow's get_last_close returns None (Bybit mode or no closed row)
    → helper falls back to caller-supplied local values."""
    position_service_mock.get_last_close.return_value = None
    pnl_usd, pnl_pct, src, exit_price = (
        await coordinator.resolve_authoritative_pnl(
            symbol="BTCUSDT",
            position_service=position_service_mock,
            fallback_pnl_usd=12.34,
            fallback_pnl_pct=0.5,
            fallback_exit_price=None,
        )
    )
    assert pnl_usd == pytest.approx(12.34)
    assert pnl_pct == pytest.approx(0.5)
    assert src == "local_fallback"
    assert exit_price is None


@pytest.mark.asyncio
async def test_shadow_raises_falls_back_to_local(coordinator, position_service_mock):
    """Shadow's get_last_close raises (transport failure)
    → helper falls back, WARNING log emitted."""
    position_service_mock.get_last_close.side_effect = ConnectionError("network blip")
    pnl_usd, pnl_pct, src, exit_price = (
        await coordinator.resolve_authoritative_pnl(
            symbol="ETHUSDT",
            position_service=position_service_mock,
            fallback_pnl_usd=-2.5,
            fallback_pnl_pct=-1.2,
            fallback_exit_price=2350.0,
        )
    )
    assert pnl_usd == pytest.approx(-2.5)
    assert pnl_pct == pytest.approx(-1.2)
    assert src == "local_fallback"
    assert exit_price == pytest.approx(2350.0)


@pytest.mark.asyncio
async def test_shadow_returns_dict_with_missing_fields(
    coordinator, position_service_mock
):
    """Shadow returns dict missing net_pnl_usd or net_pnl_pct
    → helper falls back, WARNING log emitted."""
    position_service_mock.get_last_close.return_value = {
        "symbol": "DOGEUSDT",
        "side": "Sell",
        "exit_price": 0.107562,
        # net_pnl_usd / net_pnl_pct missing
    }
    pnl_usd, pnl_pct, src, exit_price = (
        await coordinator.resolve_authoritative_pnl(
            symbol="DOGEUSDT",
            position_service=position_service_mock,
            fallback_pnl_usd=-0.601,
            fallback_pnl_pct=-0.134,
        )
    )
    assert pnl_usd == pytest.approx(-0.601)
    assert pnl_pct == pytest.approx(-0.134)
    assert src == "local_fallback"


@pytest.mark.asyncio
async def test_position_service_without_get_last_close(coordinator):
    """A position_service that doesn't have get_last_close at all
    (e.g. unit test mock) → helper falls back silently."""
    plain_mock = MagicMock(spec=[])  # no attributes at all
    pnl_usd, pnl_pct, src, exit_price = (
        await coordinator.resolve_authoritative_pnl(
            symbol="SOLUSDT",
            position_service=plain_mock,
            fallback_pnl_usd=5.0,
            fallback_pnl_pct=2.5,
        )
    )
    assert pnl_usd == pytest.approx(5.0)
    assert pnl_pct == pytest.approx(2.5)
    assert src == "local_fallback"


@pytest.mark.asyncio
async def test_shadow_authoritative_with_malformed_exit_price(
    coordinator, position_service_mock
):
    """Shadow returns net_pnl_usd / net_pnl_pct cleanly but exit_price
    is malformed → helper still returns authoritative pnl values, but
    exit_price falls back to the caller-supplied fallback (or None)."""
    position_service_mock.get_last_close.return_value = {
        "net_pnl_usd": -0.42,
        "net_pnl_pct": -0.15,
        "exit_price": "not_a_float",  # malformed
    }
    pnl_usd, pnl_pct, src, exit_price = (
        await coordinator.resolve_authoritative_pnl(
            symbol="MANAUSDT",
            position_service=position_service_mock,
            fallback_pnl_usd=-0.144,
            fallback_pnl_pct=-0.052,
        )
    )
    # Authoritative pnl values still used; exit_price degrades to None.
    assert pnl_usd == pytest.approx(-0.42)
    assert pnl_pct == pytest.approx(-0.15)
    assert src == "exchange_authoritative"
    assert exit_price is None
