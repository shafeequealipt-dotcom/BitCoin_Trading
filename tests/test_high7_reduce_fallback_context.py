"""Unit tests for HIGH-7 (REDUCE_FALLBACK swallows context).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md HIGH-7.

Pre-fix: bybit_demo_adapter.reduce_position emitted REDUCE_FALLBACK
with `err='{str(e)[:160]}'` — the str(e) of TradingMCPError truncated
at 160 chars. This cut off mid-detail (e.g., 'op': 'redu' instead of
'op': 'reduce_position'). The Telegram alert routed by alert_relay
inherited the truncated message — operators couldn't see WHY the
partial reduce failed.

Fix: extract ret_code, ret_msg, op explicitly from e.details (which is
a dict on TradingMCPError) and emit them as structured key=val fields.
Also added a REDUCE_FALLBACK log for the qty-exceeds-size silent
degrade case (pre-fix had no log line, making it indistinguishable
from voluntary full closes).

T1-4 (2026-05-12): the adapter now floor-quantizes qty BEFORE the
Bybit POST when an InstrumentService is wired. To keep these tests
exercising the bybit_reject path (the HIGH-7 logging contract), the
fixture now injects a fake InstrumentService that returns a healthy
qty_step. Without one, the path would short-circuit to a
BYBIT_DEMO_QTY_QUANTIZE_UNAVAILABLE full-close fallback BEFORE
hitting the bybit_reject arm — the safer T1-4 behaviour.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeInstrumentService:
    """Returns a healthy InstrumentInfo so reduce_position proceeds to
    the POST and reaches the bybit_reject path under test."""

    def __init__(self, *, qty_step: float = 0.001, min_qty: float = 0.001) -> None:
        from src.trading.models.instrument import InstrumentInfo
        self._info = InstrumentInfo(
            symbol="X", base_coin="X", quote_coin="USDT",
            status="Trading", min_qty=min_qty, max_qty=10000.0,
            qty_step=qty_step, min_price=0.0, max_price=0.0,
            price_tick=0.0, min_leverage=1, max_leverage=100,
            leverage_step=0.01, min_notional=0.0,
        )

    async def get_instrument_info(self, symbol: str) -> Any:
        return self._info


@pytest.fixture
def adapter():
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService
    return BybitDemoPositionService(
        MagicMock(),
        trading_repo=None,
        instrument_service=_FakeInstrumentService(),
    )


def _mock_position(side_value: str, mark_price: float = 100.0, size: float = 100.0):
    from src.core.types import Position, Side
    side_enum = Side.SELL if side_value == "Sell" else Side.BUY
    return Position(
        symbol="X", side=side_enum, entry_price=100.0, size=size,
        mark_price=mark_price, unrealized_pnl=0.0, leverage=1,
        liquidation_price=0.0,
    )


# ──────────────────────────────────────────────────────────────────────
# bybit_reject path: structured ret_code/ret_msg/op now emitted
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reduce_fallback_emits_structured_fields_on_bybit_reject(adapter, caplog) -> None:
    """When Bybit rejects the partial reduce, the REDUCE_FALLBACK log
    line carries ret_code, ret_msg, op as structured key=val fields
    (not just buried in a truncated err string)."""
    from src.core.exceptions import TradingMCPError

    pos = _mock_position("Buy", size=100.0)
    adapter.get_position = AsyncMock(return_value=pos)
    adapter.close_position = AsyncMock(return_value=MagicMock())

    err = TradingMCPError("Bybit demo: API error (10001: Qty invalid)")
    err.details = {
        "ret_code": 10001,
        "ret_msg": "Qty invalid",
        "op": "reduce_position",
    }
    adapter._client.post = AsyncMock(side_effect=err)

    # Loguru captures via the root logger; intercept via caplog won't
    # work with loguru directly. Instead spy on adapter._log.warning.
    log_calls: list[str] = []
    adapter._log.warning = lambda msg, *a, **kw: log_calls.append(msg)

    await adapter.reduce_position("X", qty=50.0)

    # The REDUCE_FALLBACK message must include the structured fields
    assert any("REDUCE_FALLBACK" in m for m in log_calls)
    fallback_msg = next(m for m in log_calls if "REDUCE_FALLBACK" in m)
    assert "ret_code=10001" in fallback_msg
    assert "ret_msg='Qty invalid'" in fallback_msg
    assert "op=reduce_position" in fallback_msg
    # And falls back to close_position
    adapter.close_position.assert_awaited_once()


@pytest.mark.asyncio
async def test_reduce_fallback_handles_missing_details(adapter) -> None:
    """If TradingMCPError has no .details attribute, the structured
    fields fall back to empty/default values without raising."""
    from src.core.exceptions import TradingMCPError

    pos = _mock_position("Buy", size=100.0)
    adapter.get_position = AsyncMock(return_value=pos)
    adapter.close_position = AsyncMock(return_value=MagicMock())

    err = TradingMCPError("opaque transport error")
    # No details attribute — getattr returns {}
    adapter._client.post = AsyncMock(side_effect=err)

    log_calls: list[str] = []
    adapter._log.warning = lambda msg, *a, **kw: log_calls.append(msg)

    await adapter.reduce_position("X", qty=50.0)

    fallback_msg = next(m for m in log_calls if "REDUCE_FALLBACK" in m)
    assert "ret_code=" in fallback_msg
    assert "ret_msg=''" in fallback_msg


# ──────────────────────────────────────────────────────────────────────
# qty-exceeds-size path: now emits REDUCE_FALLBACK (was silent)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_qty_exceeds_size_emits_reduce_fallback(adapter) -> None:
    """The silent-degrade case (qty >= pos.size) now emits a
    REDUCE_FALLBACK log line with reason=qty_exceeds_size so operators
    can distinguish forced-full-close from voluntary close in audit
    history."""
    pos = _mock_position("Buy", size=100.0)
    adapter.get_position = AsyncMock(return_value=pos)
    adapter.close_position = AsyncMock(return_value=MagicMock())

    log_calls: list[str] = []
    adapter._log.warning = lambda msg, *a, **kw: log_calls.append(msg)

    # Request more than position size → qty_exceeds_size path
    await adapter.reduce_position("X", qty=150.0)

    fallback_msg = next(m for m in log_calls if "REDUCE_FALLBACK" in m)
    assert "reason=qty_exceeds_size" in fallback_msg
    assert "pos_size=100.0" in fallback_msg
    adapter.close_position.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────
# no_position path unchanged (no err context to report)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_position_path_unchanged(adapter) -> None:
    """When no position exists, the no_position log line stays as-is
    (no err context to enrich; nothing to lose)."""
    adapter.get_position = AsyncMock(return_value=None)

    log_calls: list[str] = []
    adapter._log.warning = lambda msg, *a, **kw: log_calls.append(msg)

    result = await adapter.reduce_position("X", qty=50.0)

    fallback_msg = next(m for m in log_calls if "REDUCE_FALLBACK" in m)
    assert "reason=no_position" in fallback_msg
    # Returns rejected order (not falling back to close_position when no position)
    assert result is not None
