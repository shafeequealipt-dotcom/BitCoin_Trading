"""Pass-3 audit fix (Phase 1D) — ThesisManager.update_outcome_by_order_id.

Proves the indexer-lag reconcile correction actually LANDS on an already-closed
trade_thesis row. The original Phase-1D wiring re-fired close_thesis on the
reconcile channel, but close_thesis gates its UPDATE to status='open' (or a
zero-pnl zombie row), so the correction silently no-op'd on a normally-closed
row. This drives the REAL ThesisManager against a REAL migrated sqlite DB.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from src.core.thesis_manager import ThesisManager


@pytest.fixture
async def real_db():
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as d:
        db = DatabaseManager(os.path.join(d, "recon.db"))
        await db.connect()
        await run_migrations(db)
        try:
            yield db
        finally:
            await db.disconnect()


async def _pnl(db, order_id):
    rows = await db.fetch_all(
        "SELECT status, actual_pnl_usd, actual_pnl_pct, close_price, close_reason "
        "FROM trade_thesis WHERE order_id = ?",
        (order_id,),
    )
    return rows[0]


async def _open_then_close(mgr, *, symbol, order_id, prov_usd, prov_pct):
    await mgr.save_thesis(
        symbol=symbol, direction="Buy", entry_price=100.0, stop_loss_price=98.0,
        take_profit_price=104.0, size_usd=1000.0, leverage=3, max_hold_minutes=60,
        trailing_activation_pct=1.0, thesis="t", order_id=order_id,
    )
    await mgr.close_thesis(
        symbol=symbol, close_price=100.5, actual_pnl_pct=prov_pct,
        actual_pnl_usd=prov_usd, close_reason="loss_spike_force", order_id=order_id,
    )


@pytest.mark.asyncio
async def test_reconcile_correction_lands_on_closed_row(real_db):
    mgr = ThesisManager(real_db)
    await _open_then_close(mgr, symbol="DOGEUSDT", order_id="oid-1",
                           prov_usd=5.0, prov_pct=0.06)

    row = await _pnl(real_db, "oid-1")
    assert row["status"] == "closed" and float(row["actual_pnl_usd"]) == 5.0

    # The OLD path (re-firing close_thesis on a closed, non-zombie row) is a
    # NO-OP — this documents exactly the bug Pass-3 caught.
    await mgr.close_thesis(
        symbol="DOGEUSDT", close_price=100.5, actual_pnl_pct=-0.0616,
        actual_pnl_usd=-4.36, close_reason="loss_spike_force", order_id="oid-1",
    )
    assert float((await _pnl(real_db, "oid-1"))["actual_pnl_usd"]) == 5.0  # NOT corrected

    # The NEW reconcile method DOES land the exchange-authoritative correction.
    await mgr.update_outcome_by_order_id(
        "DOGEUSDT", "oid-1", actual_pnl_usd=-4.36, actual_pnl_pct=-0.0616,
        close_price=100.6,
    )
    row = await _pnl(real_db, "oid-1")
    assert float(row["actual_pnl_usd"]) == -4.36       # corrected to exchange net
    assert float(row["close_price"]) == 100.6          # corrected exit
    assert row["status"] == "closed"                   # status untouched
    assert row["close_reason"] == "loss_spike_force"   # reason untouched


@pytest.mark.asyncio
async def test_reconcile_keeps_close_price_when_not_supplied(real_db):
    mgr = ThesisManager(real_db)
    await _open_then_close(mgr, symbol="ETHUSDT", order_id="oid-2",
                           prov_usd=3.0, prov_pct=0.04)
    await mgr.update_outcome_by_order_id(
        "ETHUSDT", "oid-2", actual_pnl_usd=-2.0, actual_pnl_pct=-0.03,
        close_price=0.0,  # not supplied -> keep existing 100.5
    )
    row = await _pnl(real_db, "oid-2")
    assert float(row["actual_pnl_usd"]) == -2.0
    assert float(row["close_price"]) == 100.5          # preserved, not clobbered to 0


@pytest.mark.asyncio
async def test_reconcile_requires_order_id_is_safe_noop(real_db):
    mgr = ThesisManager(real_db)
    # missing order_id -> logged skip, no exception, nothing updated
    await mgr.update_outcome_by_order_id(
        "ETHUSDT", "", actual_pnl_usd=-2.0, actual_pnl_pct=-0.03,
    )
