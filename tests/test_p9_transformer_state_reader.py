"""P9 — TransformerStateSnapshot + MCPTransformerAdapter tests.

Surgical tests:
- Snapshot caches DB read for ~5s; serves stale on read failure.
- Adapter routes get_current_equity to the correct mode's account svc.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.transformer_state_reader import (
    MCPTransformerAdapter,
    TransformerStateSnapshot,
)


@pytest.mark.asyncio
async def test_snapshot_caches_mode_within_ttl() -> None:
    db = MagicMock()
    db.fetch_one = AsyncMock(return_value={
        "current_mode": "bybit_demo",
        "is_switching": 0,
        "last_switched_at": "2026-05-09T01:00:00Z",
    })

    snap = TransformerStateSnapshot(db)
    await snap.refresh_if_stale()
    assert snap.current_mode == "bybit_demo"
    assert snap.is_switching is False

    # Second call within 5s window — should NOT re-query DB.
    await snap.refresh_if_stale()
    assert db.fetch_one.call_count == 1


@pytest.mark.asyncio
async def test_snapshot_falls_back_to_cached_on_db_failure() -> None:
    db = MagicMock()
    # First call succeeds; second raises.
    db.fetch_one = AsyncMock(side_effect=[
        {"current_mode": "bybit_demo", "is_switching": 0, "last_switched_at": None},
        Exception("DB unavailable"),
    ])

    snap = TransformerStateSnapshot(db)
    await snap.refresh_if_stale()
    assert snap.current_mode == "bybit_demo"

    # Force cache invalidation
    import time
    snap._cached_at = time.monotonic() - 999.0

    # Second call: DB raises, snapshot keeps the prior value.
    await snap.refresh_if_stale()
    assert snap.current_mode == "bybit_demo"


@pytest.mark.asyncio
async def test_adapter_routes_equity_to_mode_account_svc() -> None:
    db = MagicMock()
    db.fetch_one = AsyncMock(return_value={
        "current_mode": "bybit_demo",
        "is_switching": 0,
        "last_switched_at": None,
    })

    bd_acc = MagicMock()
    bd_bal = MagicMock(total_equity=12345.67, available_balance=10000.0)
    bd_acc.get_wallet_balance = AsyncMock(return_value=bd_bal)

    bybit_acc = MagicMock()  # not called when mode=bybit_demo
    bybit_acc.get_wallet_balance = AsyncMock(return_value=MagicMock(total_equity=999, available_balance=999))

    adapter = MCPTransformerAdapter(
        db=db,
        services_per_mode={
            "bybit": {"account": bybit_acc},
            "bybit_demo": {"account": bd_acc},
        },
    )

    eq = await adapter.get_current_equity()
    assert eq["mode"] == "bybit_demo"
    assert eq["equity"] == 12345.67
    bd_acc.get_wallet_balance.assert_called_once()
    bybit_acc.get_wallet_balance.assert_not_called()


def test_adapter_mode_label_dictionary() -> None:
    db = MagicMock()
    db.fetch_one = AsyncMock(return_value={"current_mode": "bybit_demo", "is_switching": 0, "last_switched_at": None})
    adapter = MCPTransformerAdapter(db=db, services_per_mode={})
    # Cache is cold; mode_label uses default "shadow"
    assert adapter.mode_label == "Shadow"
    # Set cache directly
    adapter._snapshot._cached_mode = "bybit_demo"
    adapter._snapshot._cached_is_switching = False
    assert adapter.mode_label == "Bybit Demo"
    adapter._snapshot._cached_is_switching = True
    assert "switching" in adapter.mode_label
