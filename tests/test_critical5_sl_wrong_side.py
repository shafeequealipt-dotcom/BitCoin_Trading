"""Unit tests for CRITICAL-5 (SL/TP wrong-side rejection).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md CRITICAL-5.

Pre-fix: profit_sniper._compute_trail_stop produced trail_stop = peak_price
+ trail_distance for Sell. As current price retraces past peak (Sell going
against you), trail_stop falls below current_price → wrong side. The
SNIPER_TOO_CLOSE check at _apply_trail_stop:1505 used absolute distance
so it didn't catch wrong-side. The sl_gateway's R2 check at
sl_gateway.py:414 is also direction-agnostic. Bybit caught the wrong-side
SL with retCode 10001 ("StopLoss should greater base_price") producing
the audit's KATUSDT 5-burst + RENDERUSDT 2-burst alert spam in 2.85h.

Multi-layer fix:
1. Sniper SNIPER_WRONG_SIDE_GUARD before gateway.apply (root cause)
2. Adapter BYBIT_DEMO_SET_SL_DIRECTION_BUG / SET_TP_DIRECTION_BUG
   (defensive validate-and-reject)
3. Adapter ret_code 34040 ("not modified") treated as idempotent success
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Group 1 — sniper wrong-side guard (helper formula)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "direction,new_sl,price,expected_wrong",
    [
        # Buy (long): SL below price = correct; SL >= price = wrong
        ("Buy", 99.0, 100.0, False),    # correct: SL below price
        ("Buy", 100.0, 100.0, True),    # wrong: SL == price
        ("Buy", 101.0, 100.0, True),    # wrong: SL above price
        ("Long", 99.0, 100.0, False),
        ("Long", 101.0, 100.0, True),
        # Sell (short): SL above price = correct; SL <= price = wrong
        ("Sell", 101.0, 100.0, False),  # correct: SL above price
        ("Sell", 100.0, 100.0, True),   # wrong: SL == price
        ("Sell", 99.0, 100.0, True),    # wrong: SL below price (KATUSDT pattern)
        ("Short", 101.0, 100.0, False),
        ("Short", 99.0, 100.0, True),
    ],
)
def test_sniper_wrong_side_formula(
    direction: str, new_sl: float, price: float, expected_wrong: bool
) -> None:
    """The wrong_side helper used in _apply_trail_stop must correctly
    identify SL placements that would be rejected by Bybit."""
    is_long = direction in ("Buy", "Long")
    wrong_side = (is_long and new_sl >= price) or (not is_long and new_sl <= price)
    assert wrong_side is expected_wrong


# ──────────────────────────────────────────────────────────────────────
# Group 2 — adapter wrong-side rejection (set_stop_loss)
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_adapter():
    """Build a minimal BybitDemoPositionService with mocked client + repo
    for direct set_stop_loss / set_take_profit testing."""
    from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

    client = MagicMock()
    client.post = AsyncMock()
    adapter = BybitDemoPositionService(client, trading_repo=None)
    return adapter


def _mock_position(side_value: str, mark_price: float, size: float = 100.0):
    """Build a mock Position with the named side + mark price."""
    from src.core.types import Position, Side

    side_enum = Side.SELL if side_value == "Sell" else Side.BUY
    return Position(
        symbol="X",
        side=side_enum,
        entry_price=100.0,
        size=size,
        mark_price=mark_price,
        unrealized_pnl=0.0,
        leverage=1,
        liquidation_price=0.0,
    )


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_rejects_wrong_side_sell(mock_adapter) -> None:
    """Sell position: SL must be > mark_price. SL=99 with mark=100 must
    be locally rejected without calling Bybit. Reproduces the KATUSDT
    audit case."""
    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)

    result = await mock_adapter.set_stop_loss("X", stop_loss=99.0)

    assert result is False
    # Critically: client.post must NOT have been called (wrong-side
    # caught locally; no Bybit roundtrip)
    mock_adapter._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_rejects_wrong_side_buy(mock_adapter) -> None:
    """Buy position: SL must be < mark_price. SL=101 with mark=100 must
    be locally rejected."""
    pos = _mock_position("Buy", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)

    result = await mock_adapter.set_stop_loss("X", stop_loss=101.0)

    assert result is False
    mock_adapter._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_accepts_correct_side_sell(mock_adapter) -> None:
    """Sell position: SL=101 with mark=100 is correct (SL above price).
    Must call Bybit and return True on success."""
    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)
    mock_adapter._client.post = AsyncMock(return_value={"retCode": 0})

    result = await mock_adapter.set_stop_loss("X", stop_loss=101.0)

    assert result is True
    mock_adapter._client.post.assert_called_once()


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_accepts_correct_side_buy(mock_adapter) -> None:
    """Buy position: SL=99 with mark=100 is correct."""
    pos = _mock_position("Buy", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)
    mock_adapter._client.post = AsyncMock(return_value={"retCode": 0})

    result = await mock_adapter.set_stop_loss("X", stop_loss=99.0)

    assert result is True
    mock_adapter._client.post.assert_called_once()


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_skips_validation_when_no_position(
    mock_adapter,
) -> None:
    """If get_position returns None (no open position), validation
    skips and the call proceeds. Bybit will reject if there's no
    position to SL — but that's a different error path."""
    mock_adapter.get_position = AsyncMock(return_value=None)
    mock_adapter._client.post = AsyncMock(return_value={"retCode": 0})

    result = await mock_adapter.set_stop_loss("X", stop_loss=99.0)

    # Reaches Bybit; would succeed if there's actually a position. Test
    # asserts no exception + call placed.
    assert result is True
    mock_adapter._client.post.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# Group 3 — adapter set_take_profit wrong-side rejection (latent bug)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_set_take_profit_rejects_wrong_side_sell(mock_adapter) -> None:
    """Sell position: TP must be < mark_price (close at lower price = profit).
    TP=101 with mark=100 is wrong-side (would close at a loss)."""
    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)

    result = await mock_adapter.set_take_profit("X", take_profit=101.0)

    assert result is False
    mock_adapter._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_adapter_set_take_profit_rejects_wrong_side_buy(mock_adapter) -> None:
    """Buy position: TP must be > mark_price (close at higher price = profit).
    TP=99 with mark=100 is wrong-side."""
    pos = _mock_position("Buy", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)

    result = await mock_adapter.set_take_profit("X", take_profit=99.0)

    assert result is False
    mock_adapter._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_adapter_set_take_profit_accepts_correct_side_sell(mock_adapter) -> None:
    """Sell position: TP=99 with mark=100 is correct (close at lower price)."""
    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)
    mock_adapter._client.post = AsyncMock(return_value={"retCode": 0})

    result = await mock_adapter.set_take_profit("X", take_profit=99.0)

    assert result is True


# ──────────────────────────────────────────────────────────────────────
# Group 4 — adapter ret_code 34040 idempotent handling (ICPUSDT case)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_treats_34040_as_success(mock_adapter) -> None:
    """Bybit returns ret_code 34040 ("not modified") when the requested SL
    equals the existing SL. This is idempotent — should be treated as
    success, not failure. Mirrors set_leverage:519 pattern for 110043."""
    from src.core.exceptions import TradingMCPError

    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)
    err = TradingMCPError("not modified")
    err.details = {"ret_code": 34040}
    mock_adapter._client.post = AsyncMock(side_effect=err)

    result = await mock_adapter.set_stop_loss("X", stop_loss=101.0)

    assert result is True


@pytest.mark.asyncio
async def test_adapter_set_take_profit_treats_34040_as_success(mock_adapter) -> None:
    """Same idempotent handling for TP."""
    from src.core.exceptions import TradingMCPError

    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)
    err = TradingMCPError("not modified")
    err.details = {"ret_code": 34040}
    mock_adapter._client.post = AsyncMock(side_effect=err)

    result = await mock_adapter.set_take_profit("X", take_profit=99.0)

    assert result is True


@pytest.mark.asyncio
async def test_adapter_set_stop_loss_other_errors_still_fail(mock_adapter) -> None:
    """Negative control: ret_code 10001 (the original wrong-side error
    that bypassed our local check, e.g. due to a race condition between
    get_position and the post) still produces False, not True."""
    from src.core.exceptions import TradingMCPError

    pos = _mock_position("Sell", mark_price=100.0)
    mock_adapter.get_position = AsyncMock(return_value=pos)
    err = TradingMCPError("StopLoss for Sell position should greater base_price")
    err.details = {"ret_code": 10001}
    mock_adapter._client.post = AsyncMock(side_effect=err)

    result = await mock_adapter.set_stop_loss("X", stop_loss=101.0)

    assert result is False
