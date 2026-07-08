"""P5 — close_thesis re-targets zombie rows.

Surgical test: WHERE clause matches zombie-signature rows
(status=closed AND pnl_usd=0 AND close_reason=zombie_reconciler) so the
watchdog's authoritative-PnL UPDATE overwrites them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.thesis_manager import ThesisManager


@pytest.mark.asyncio
async def test_close_thesis_with_order_id_widens_to_zombie_signature() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    tm = ThesisManager(db)

    await tm.close_thesis(
        symbol="PLUMEUSDT",
        close_price=0.01341,
        actual_pnl_pct=-1.6063,
        actual_pnl_usd=-28.91,
        close_reason="bybit_demo_sl_tp",
        lesson="External close",
        order_id="ABCDEF12",
    )

    sql, params = db.execute.call_args.args
    # The UPDATE WHERE must include the zombie signature OR.
    assert "status = 'open'" in sql
    assert "status = 'closed'" in sql
    assert "actual_pnl_usd = 0" in sql
    assert "close_reason = 'zombie_reconciler'" in sql
    # Order_id-scoped path
    assert "order_id = ?" in sql
    assert params[5] == "PLUMEUSDT"
    assert params[6] == "ABCDEF12"


@pytest.mark.asyncio
async def test_close_thesis_without_order_id_widens_to_zombie_signature() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    tm = ThesisManager(db)

    await tm.close_thesis(
        symbol="ETHUSDT",
        close_price=2500.0,
        actual_pnl_pct=1.0,
        actual_pnl_usd=10.0,
        close_reason="strategic_review",
        lesson="",
        # No order_id — legacy caller path
    )

    sql, _params = db.execute.call_args.args
    assert "status = 'open'" in sql
    assert "status = 'closed'" in sql
    assert "actual_pnl_usd = 0" in sql
    assert "close_reason = 'zombie_reconciler'" in sql
    # Without order_id the symbol-only path
    assert "order_id" not in sql
