"""H3 (2026-05-16) — FUND_INUSE_DRIFT root cause tests.

Phase 1 evidence: ``state.in_use = sum(abs(p.size * p.entry_price)
for p in positions)`` omitted the leverage divisor; Bybit's
``totalInitialMargin`` is ``sum(notional / leverage)`` per position.
Result: local OVER-counts in_use by an average-leverage factor → drift
grew from $-7k to $-17k over a 5h window.

The H3 fix sources ``state.in_use`` from Bybit's wallet
(``account.used_margin``, the ``totalInitialMargin`` field) directly,
making it canonical by construction. A leverage-aware position-derived
fallback is computed for diagnostic and for the cases when the wallet
read fails.

These tests assert the priority order and the new observability shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.fund_manager.manager import IntelligentFundManager
from src.fund_manager.models.fund_types import AccountState


@dataclass
class _FakePos:
    symbol: str
    size: float
    entry_price: float
    leverage: int = 1


def _make_mgr_with_services(
    *,
    wallet_total_equity: float = 10_000.0,
    wallet_used_margin: float | None = 0.0,
    wallet_raises: Exception | None = None,
    positions: list[_FakePos] | None = None,
    positions_raise: Exception | None = None,
) -> IntelligentFundManager:
    """Construct an IntelligentFundManager with stubbed services."""
    account_svc = MagicMock()
    if wallet_raises is not None:
        account_svc.get_wallet_balance = AsyncMock(side_effect=wallet_raises)
    else:
        wallet_obj = MagicMock()
        wallet_obj.total_equity = wallet_total_equity
        wallet_obj.used_margin = wallet_used_margin if wallet_used_margin is not None else 0.0
        wallet_obj.available_balance = wallet_total_equity - (wallet_used_margin or 0.0)
        account_svc.get_wallet_balance = AsyncMock(return_value=wallet_obj)

    pos_svc = MagicMock()
    if positions_raise is not None:
        pos_svc.get_positions = AsyncMock(side_effect=positions_raise)
    else:
        pos_svc.get_positions = AsyncMock(return_value=positions or [])

    # Bare settings stub — fund_manager.IntelligentFundManager.__init__
    # accepts a settings + services dict.
    settings_stub = MagicMock()
    services = {
        "account_service": account_svc,
        "position_service": pos_svc,
    }
    db_stub = MagicMock()
    mgr = IntelligentFundManager(settings=settings_stub, db=db_stub, services=services)
    # Bypass initialize() and seed account_state directly so update_state
    # has a state to mutate.
    mgr._account_state = AccountState(
        total_equity=wallet_total_equity,
        starting_balance=10_000.0,
        unlock_pct=100.0,
    )
    return mgr


@pytest.mark.asyncio
async def test_in_use_takes_bybit_value_when_account_available() -> None:
    """When the wallet read succeeds, state.in_use must equal the
    Bybit-reported totalInitialMargin (the ``used_margin`` field).
    Position-derived computation runs for diagnostics but does not
    override.
    """
    bybit_margin = 150.0
    positions = [
        _FakePos("BTCUSDT", 10, 45_000, leverage=3),  # notional=$450k margin=$150k
    ]
    mgr = _make_mgr_with_services(
        wallet_total_equity=200_000.0,
        wallet_used_margin=bybit_margin,
        positions=positions,
    )
    await mgr.update_state()
    assert mgr._account_state.in_use == bybit_margin
    # Naive notional is still tracked for callers that want it
    assert mgr._account_state.in_use_notional == pytest.approx(450_000.0, abs=1.0)


@pytest.mark.asyncio
async def test_in_use_falls_back_to_leverage_aware_sum_when_wallet_fails() -> None:
    """When the wallet read raises, state.in_use uses the leverage-
    aware position-derived value (NOT the broken naive notional).
    """
    positions = [
        _FakePos("BTCUSDT", 10, 45_000, leverage=3),
        _FakePos("ETHUSDT", 4, 2_500, leverage=2),
    ]
    expected_leverage_aware = (10 * 45_000) / 3 + (4 * 2_500) / 2  # 150k + 5k = 155k
    mgr = _make_mgr_with_services(
        wallet_total_equity=200_000.0,
        wallet_raises=RuntimeError("wallet read failed"),
        positions=positions,
    )
    await mgr.update_state()
    assert mgr._account_state.in_use == pytest.approx(expected_leverage_aware, abs=1.0)
    # Naive notional separately tracked
    expected_notional = (10 * 45_000) + (4 * 2_500)  # 450k + 10k = 460k
    assert mgr._account_state.in_use_notional == pytest.approx(expected_notional, abs=1.0)


@pytest.mark.asyncio
async def test_leverage_zero_falls_back_to_one() -> None:
    """Defensive: a position with leverage=0 (or missing) does not
    divide-by-zero — the formula uses max(1, leverage).
    """
    positions = [
        _FakePos("FOO", 5, 100, leverage=0),  # leverage=0 → use 1
    ]
    mgr = _make_mgr_with_services(
        wallet_total_equity=10_000.0,
        wallet_raises=RuntimeError("force fallback"),
        positions=positions,
    )
    await mgr.update_state()
    # 5 * 100 / max(1, 0) = 500
    assert mgr._account_state.in_use == pytest.approx(500.0, abs=0.01)


@pytest.mark.asyncio
async def test_empty_positions_zero_in_use() -> None:
    """No open positions on either side → in_use = 0 (whether sourced
    from Bybit or fallback).
    """
    mgr = _make_mgr_with_services(
        wallet_total_equity=10_000.0,
        wallet_used_margin=0.0,
        positions=[],
    )
    await mgr.update_state()
    assert mgr._account_state.in_use == 0.0
    assert mgr._account_state.in_use_notional == 0.0


@pytest.mark.asyncio
async def test_available_capacity_correct_with_leveraged_position() -> None:
    """The downstream consumer of in_use is state.available, which gates
    new trade sizing. With a leveraged position, available must reflect
    the margin actually used (not the leveraged notional).
    """
    bybit_margin = 150.0  # $150 margin
    positions = [_FakePos("BTCUSDT", 1, 45_000, leverage=3)]
    mgr = _make_mgr_with_services(
        wallet_total_equity=10_000.0,
        wallet_used_margin=bybit_margin,
        positions=positions,
    )
    # unlock_pct=100 in the helper, so trading_capital = total_equity = 10k
    await mgr.update_state()
    state = mgr._account_state
    # available = max(0, trading_capital - in_use) = max(0, 10000 - 150) = 9850
    # Pre-H3 the naive notional would have been $45,000 → available clamped to 0
    # → no new trades possible. Aim violation.
    assert state.available == pytest.approx(9_850.0, abs=1.0)
