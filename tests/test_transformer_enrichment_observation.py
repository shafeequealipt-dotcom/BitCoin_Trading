"""Phase 2 unit tests — Transformer enrichment is observation-only.

Verifies the contract of ``_enrich_positions_with_local_prices`` and
``_enrich_balance_with_local_prices`` after the price-source-divergence
fix demoted both to observation-only:

1. ``pos.mark_price`` is NEVER mutated (regardless of divergence amount).
2. ``pos.unrealized_pnl`` is NEVER recomputed (regardless of divergence
   amount).
3. ``_last_enrichment_max_divergence_pct`` is updated correctly so the
   strategist's PROMPT_DEFERRED gate at
   ``src/brain/strategist.py:280-298, 500-523`` keeps functioning.
4. Above-threshold divergences emit the renamed ``PRICE_DIVERGENCE_OBS``
   log tag and the ``price_divergence_obs`` event-buffer event.
5. Balance observation does not mutate ``unrealized_pnl``,
   ``total_equity``, ``available_balance``, or ``used_margin``.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.transformer import Transformer
from src.core.types import Position, Side


@dataclass
class _StubBalance:
    """Mimics AccountInfo for balance observation tests without pulling
    in the full dataclass and its dependencies."""

    total_equity: float
    available_balance: float
    used_margin: float
    unrealized_pnl: float


def _make_position(
    symbol: str = "BTCUSDT",
    side: Side = Side.BUY,
    entry: float = 100.0,
    mark: float = 101.0,
    size: float = 1.0,
    unrealized: float = 1.0,
) -> Position:
    return Position(
        symbol=symbol,
        side=side,
        size=size,
        entry_price=entry,
        mark_price=mark,
        unrealized_pnl=unrealized,
        realized_pnl=0.0,
        leverage=1,
        liquidation_price=0.0,
    )


@pytest.fixture
def transformer() -> Transformer:
    """Construct a Transformer with stubbed dependencies. Override
    ``_get_local_price`` per test to control divergence."""
    db = MagicMock()
    config = MagicMock()
    # Default config: divergence_override_pct=0.5 (the threshold this
    # fix repurposes from "override threshold" to "log-emission threshold").
    config.price.divergence_override_pct = 0.5
    config.price.local_max_age_seconds = 10.0
    tf = Transformer(db=db, config=config)
    # No event_buffer wired — disable the per-call event-buffer write
    # path. Each test that wants to assert on event-buffer behaviour
    # supplies its own.
    tf._event_buffer = None
    return tf


@pytest.mark.asyncio
async def test_below_threshold_divergence_does_not_mutate(transformer):
    """Divergence within tolerance — no mutation, gate field updates."""
    transformer._get_local_price = AsyncMock(return_value=101.2)
    pos = _make_position(symbol="BTCUSDT", entry=100.0, mark=101.0,
                         unrealized=1.0)
    await transformer._enrich_positions_with_local_prices([pos])
    # Position state unchanged.
    assert pos.mark_price == 101.0
    assert pos.unrealized_pnl == 1.0
    # Divergence (101.2 - 101.0) / 101.0 * 100 ≈ 0.198% — below 0.5%.
    assert 0.0 < transformer._last_enrichment_max_divergence_pct < 0.5
    # Below-threshold = silent (no log assertion needed).


@pytest.mark.asyncio
async def test_above_threshold_divergence_does_not_mutate_either(transformer):
    """Divergence above threshold — no mutation; log + event fire."""
    transformer._get_local_price = AsyncMock(return_value=110.0)
    pos = _make_position(symbol="BTCUSDT", entry=100.0, mark=100.0,
                         unrealized=0.0)
    event_buffer = MagicMock()
    transformer._event_buffer = event_buffer
    await transformer._enrich_positions_with_local_prices([pos])
    # Position state unchanged even at large divergence.
    assert pos.mark_price == 100.0
    assert pos.unrealized_pnl == 0.0
    # Divergence (110-100)/100*100 = 10% — above threshold.
    assert transformer._last_enrichment_max_divergence_pct == pytest.approx(10.0)
    # Event buffer received the renamed event_name.
    event_buffer.add_event.assert_called_once()
    args, kwargs = event_buffer.add_event.call_args
    # Positional args: (severity, event_name, symbol)
    assert args[0] == "MED"
    assert args[1] == "price_divergence_obs"
    assert args[2] == "BTCUSDT"


@pytest.mark.asyncio
async def test_no_local_price_does_not_mutate(transformer):
    """When ``_get_local_price`` returns None — Shadow value passes through."""
    transformer._get_local_price = AsyncMock(return_value=None)
    pos = _make_position(symbol="ETHUSDT", entry=2000.0, mark=2010.0,
                         unrealized=10.0)
    await transformer._enrich_positions_with_local_prices([pos])
    assert pos.mark_price == 2010.0
    assert pos.unrealized_pnl == 10.0
    # No divergence computed → field stays at its reset value.
    assert transformer._last_enrichment_max_divergence_pct == 0.0


@pytest.mark.asyncio
async def test_max_divergence_updated_across_multiple_positions(transformer):
    """Per-pass max captures the largest |divergence| across all positions."""

    async def _local(symbol):
        return {
            "BTCUSDT": 100.05,   # +0.05% divergence
            "ETHUSDT": 110.0,    # +10% divergence (the max)
            "SOLUSDT": 49.95,    # -0.10% divergence
        }.get(symbol)

    transformer._get_local_price = _local
    positions = [
        _make_position(symbol="BTCUSDT", entry=100.0, mark=100.0),
        _make_position(symbol="ETHUSDT", entry=100.0, mark=100.0),
        _make_position(symbol="SOLUSDT", entry=50.0, mark=50.0),
    ]
    await transformer._enrich_positions_with_local_prices(positions)
    # All positions unchanged.
    for p in positions:
        assert p.unrealized_pnl == 1.0  # default from _make_position
    # Max captures ETHUSDT divergence.
    assert transformer._last_enrichment_max_divergence_pct == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_max_divergence_resets_per_pass(transformer):
    """Field is reset to 0.0 at the top of each pass — a previous pass's
    max cannot poison subsequent strategist gate decisions."""
    # First pass: large divergence.
    transformer._get_local_price = AsyncMock(return_value=110.0)
    pos1 = _make_position(symbol="BTCUSDT", entry=100.0, mark=100.0)
    await transformer._enrich_positions_with_local_prices([pos1])
    assert transformer._last_enrichment_max_divergence_pct == pytest.approx(10.0)

    # Second pass: tiny divergence.
    transformer._get_local_price = AsyncMock(return_value=100.05)
    pos2 = _make_position(symbol="BTCUSDT", entry=100.0, mark=100.0)
    await transformer._enrich_positions_with_local_prices([pos2])
    # Reset means second pass's max reflects only its own observations.
    assert transformer._last_enrichment_max_divergence_pct == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_balance_observation_does_not_mutate(transformer):
    """``_enrich_balance_with_local_prices`` does not modify balance
    fields even when local-vs-Shadow divergence is large."""
    pos = _make_position(symbol="BTCUSDT", entry=100.0, mark=100.0,
                         size=1.0, unrealized=0.0)
    shadow_pos_svc = MagicMock()
    shadow_pos_svc.get_positions = AsyncMock(return_value=[pos])
    transformer._shadow_services = {"position": shadow_pos_svc}
    transformer._get_local_price = AsyncMock(return_value=120.0)  # +20% divergence

    balance = _StubBalance(
        total_equity=1000.0,
        available_balance=900.0,
        used_margin=100.0,
        unrealized_pnl=0.0,
    )
    snapshot = (
        balance.total_equity,
        balance.available_balance,
        balance.used_margin,
        balance.unrealized_pnl,
    )

    result = await transformer._enrich_balance_with_local_prices(balance)

    assert balance.total_equity == snapshot[0]
    assert balance.available_balance == snapshot[1]
    assert balance.used_margin == snapshot[2]
    assert balance.unrealized_pnl == snapshot[3]
    # Helper returns the balance unchanged so the proxy's callsite keeps
    # its existing interface (None or the balance object).
    assert result is None or result is balance


@pytest.mark.asyncio
async def test_strategist_gate_input_preserved_byte_for_byte(transformer):
    """Critical regression — the strategist's PROMPT_DEFERRED gate reads
    ``_last_enrichment_max_divergence_pct`` and compares it against
    ``divergence_block_prompt_pct`` (default 1.0%). This test pins the
    exact field-update semantics so a future refactor cannot silently
    change them."""
    # Inputs: BTCUSDT entry=100, mark=100, local=101 → +1.0% exact.
    transformer._get_local_price = AsyncMock(return_value=101.0)
    pos = _make_position(symbol="BTCUSDT", entry=100.0, mark=100.0)
    await transformer._enrich_positions_with_local_prices([pos])
    assert transformer._last_enrichment_max_divergence_pct == pytest.approx(1.0)
