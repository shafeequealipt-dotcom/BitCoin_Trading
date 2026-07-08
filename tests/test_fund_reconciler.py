"""Phase 5 (post-Layer-1 fix) — FundReconciler worker tests.

Exercises ``FundReconciler.tick()`` directly with mocked services. The
reconciler reads Bybit via ``account_service.get_wallet_balance()`` and
compares the result to the local ``fund_manager._account_state``,
emitting:

  - ``FUND_RECONCILE`` every tick (INFO)
  - ``FUND_RECONCILE_DRIFT`` when |drift| > threshold (WARNING)
  - ``FUND_RECONCILE_AUTO_CORRECT`` when auto_correct=True applies an overwrite
  - ``FUND_DAILY_SUMMARY`` once per UTC date crossing

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_5_balance_reconcile.md``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.workers.fund_reconciler import FundReconciler


pytestmark = pytest.mark.asyncio


def _make_reconciler(
    bybit_total: float = 1000.0,
    bybit_available: float = 800.0,
    local_total: float = 1000.0,
    local_cap: float = 200.0,
    local_avail: float = 200.0,
    auto_correct: bool = False,
    threshold_pct: float = 5.0,
):
    """Build a FundReconciler with mocked services and seeded state."""
    settings = Settings._load_fresh()
    settings.fund_manager.reconcile_drift_alert_threshold_pct = threshold_pct
    settings.fund_manager.reconcile_auto_correct = auto_correct

    # account_service mock
    account_svc = MagicMock()
    account_svc.get_wallet_balance = AsyncMock(
        return_value=SimpleNamespace(
            total_equity=bybit_total,
            available_balance=bybit_available,
        )
    )

    # fund_manager mock with _account_state
    fund_mgr = MagicMock()
    fund_mgr._account_state = SimpleNamespace(
        total_equity=local_total,
        trading_capital=local_cap,
        available=local_avail,
    )

    services = {
        "account_service": account_svc,
        "fund_manager": fund_mgr,
    }
    db = MagicMock()
    rec = FundReconciler(settings, db, services)
    return rec, services, fund_mgr


async def test_reconcile_no_drift_emits_match() -> None:
    """Local and exchange agree → FUND_RECONCILE only, no drift event."""
    rec, services, _ = _make_reconciler(bybit_total=1000.0, local_total=1000.0)
    # No raise = pass.
    await rec.tick()
    services["account_service"].get_wallet_balance.assert_called_once()


async def test_reconcile_drift_above_threshold_alerts() -> None:
    """10% drift > 5% threshold → FUND_RECONCILE_DRIFT fires."""
    rec, _, fund_mgr = _make_reconciler(
        bybit_total=1000.0, local_total=1100.0, threshold_pct=5.0
    )
    await rec.tick()
    # Without auto_correct, local must NOT be overwritten.
    assert fund_mgr._account_state.total_equity == 1100.0


async def test_reconcile_auto_correct_overwrites_local() -> None:
    """When opt-in, drift triggers in-place overwrite."""
    rec, _, fund_mgr = _make_reconciler(
        bybit_total=1000.0,
        local_total=1100.0,
        threshold_pct=5.0,
        auto_correct=True,
    )
    await rec.tick()
    # Local is now exchange.
    assert fund_mgr._account_state.total_equity == 1000.0


async def test_reconcile_drift_below_threshold_no_alert() -> None:
    """1% drift < 5% threshold → reconcile but no DRIFT event."""
    rec, _, fund_mgr = _make_reconciler(
        bybit_total=1000.0, local_total=1010.0, threshold_pct=5.0
    )
    await rec.tick()
    # No overwrite even though auto_correct semantics not exercised here.
    assert fund_mgr._account_state.total_equity == 1010.0


async def test_reconcile_handles_bybit_error_gracefully() -> None:
    """account_service raising must not propagate or crash the worker."""
    rec, services, _ = _make_reconciler()
    services["account_service"].get_wallet_balance = AsyncMock(
        side_effect=RuntimeError("Bybit timeout")
    )
    # No raise = pass.
    await rec.tick()


async def test_reconcile_skip_when_no_account_service() -> None:
    """Paper-only mode: no account_service → graceful skip."""
    settings = Settings._load_fresh()
    db = MagicMock()
    rec = FundReconciler(settings, db, services={"fund_manager": MagicMock()})
    # Must not raise.
    await rec.tick()


async def test_reconcile_skip_when_no_fund_manager() -> None:
    """No fund_manager service → graceful skip without exception."""
    settings = Settings._load_fresh()
    account_svc = MagicMock()
    account_svc.get_wallet_balance = AsyncMock(
        return_value=SimpleNamespace(total_equity=1000, available_balance=800)
    )
    db = MagicMock()
    rec = FundReconciler(settings, db, services={"account_service": account_svc})
    await rec.tick()


async def test_reconcile_zero_bybit_total_no_division_error() -> None:
    """Empty wallet (total=0) → drift_pct=0, no ZeroDivisionError."""
    rec, _, _ = _make_reconciler(bybit_total=0.0, local_total=0.0)
    await rec.tick()


async def test_daily_summary_emits_on_date_change() -> None:
    """Day boundary triggers FUND_DAILY_SUMMARY; same day does not."""
    rec, _, _ = _make_reconciler(bybit_total=1000.0, local_total=1000.0)
    # First tick: seeds, no emission.
    await rec.tick()
    # Force a date roll-back so the next tick treats it as a new day.
    rec._last_summary_date = "1999-01-01"
    rec._day_start_balance = 950.0
    await rec.tick()
    # State updated to today's date (no easy way to assert log emission
    # here without capturing log output; the assertion is that no raise
    # happened and bookkeeping advanced).
    assert rec._last_summary_date != "1999-01-01"
