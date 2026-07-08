"""P8 — data_lake.write_trade exchange_mode tagging.

Surgical test: write_trade with exchange_mode kwarg writes the value
into the trade_log column (not the column DEFAULT).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.data_lake import DataLakeWriter


@pytest.mark.asyncio
async def test_write_trade_with_exchange_mode_passes_to_sql() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    writer = DataLakeWriter(db)

    await writer.write_trade(
        trade_id="t-X",
        symbol="BTCUSDT",
        direction="Buy",
        entry_price=80000,
        exit_price=80500,
        pnl_pct=0.625,
        pnl_usd=5.0,
        exchange_mode="bybit_demo",
    )

    db.execute.assert_called_once()
    sql, params = db.execute.call_args.args
    assert "exchange_mode" in sql
    # The 16th positional param is exchange_mode in the explicit-mode path.
    assert params[-1] == "bybit_demo"


@pytest.mark.asyncio
async def test_write_trade_without_exchange_mode_emits_warning() -> None:
    """Backward-compat path: caller doesn't pass exchange_mode → falls
    through to column DEFAULT 'shadow' BUT emits a WARNING so the gap
    is observable in logs.
    """
    db = MagicMock()
    db.execute = AsyncMock()
    writer = DataLakeWriter(db)

    # Capture loguru output. The warning text contains
    # 'caller_did_not_pass_exchange_mode'.
    from loguru import logger
    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="WARNING")
    try:
        await writer.write_trade(
            trade_id="t-X",
            symbol="BTCUSDT",
            direction="Buy",
            entry_price=80000,
            exit_price=80500,
            pnl_pct=0.625,
            pnl_usd=5.0,
            # exchange_mode intentionally omitted
        )
    finally:
        logger.remove(sink_id)

    assert any("DL_TRADE_NO_MODE" in line for line in captured)
    # Backward-compat SQL has 15 params (no exchange_mode)
    sql, params = db.execute.call_args.args
    assert "exchange_mode" not in sql
