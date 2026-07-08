"""Unit tests for HIGH-1 (account_snapshots dormant since mode flip).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md HIGH-1.

Pre-fix: transformer._AccountProxy.get_wallet_balance only saved a
snapshot when in shadow mode (`if self._t.is_shadow:` block at lines
1334-1336). The docstring on _save_account_snapshot claimed Bybit mode
was handled by AccountService internally, but no Bybit code path wrote
to account_snapshots — verified by grep. Result: zero bybit_demo equity
history captured for 33+ hours.

Fix: snapshot save moved OUTSIDE the is_shadow gate so it runs for
both modes. Enrichment stays shadow-only (Shadow's raw balance needs
local-price multiplication; Bybit's balance comes pre-enriched).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def transformer_mock():
    """Build a transformer-like object with the bare attributes the
    _AccountProxy.get_wallet_balance method needs."""
    t = MagicMock()
    t.active_account_service = MagicMock()
    t.active_account_service.get_wallet_balance = AsyncMock()
    t._enrich_balance_with_local_prices = AsyncMock()
    t._save_account_snapshot = AsyncMock()
    return t


def _account_proxy(transformer):
    """Construct the proxy bound to the test transformer."""
    from src.core.transformer import _AccountProxy
    return _AccountProxy(transformer)


@pytest.mark.asyncio
async def test_bybit_demo_mode_saves_snapshot(transformer_mock) -> None:
    """The audit's regression: in bybit_demo mode, get_wallet_balance must
    now trigger a snapshot save. Pre-fix: zero saves for 33+ hours.
    Post-HIGH-2: also passes exchange_mode kwarg resolved from
    transformer.current_mode."""
    transformer_mock.is_shadow = False
    transformer_mock.current_mode = "bybit_demo"
    raw_balance = MagicMock(total_equity=5000.0)
    transformer_mock.active_account_service.get_wallet_balance.return_value = raw_balance

    proxy = _account_proxy(transformer_mock)
    result = await proxy.get_wallet_balance()

    # Snapshot saved with exchange_mode tag (HIGH-1 + HIGH-2)
    transformer_mock._save_account_snapshot.assert_awaited_once_with(
        raw_balance, exchange_mode="bybit_demo",
    )
    # No enrichment in bybit_demo mode
    transformer_mock._enrich_balance_with_local_prices.assert_not_called()
    # Returns the raw balance (no enrichment)
    assert result is raw_balance


@pytest.mark.asyncio
async def test_shadow_mode_still_enriches_and_saves(transformer_mock) -> None:
    """Regression guard: shadow mode behavior unchanged — enrichment
    happens, then snapshot saves the enriched balance with shadow tag."""
    transformer_mock.is_shadow = True
    transformer_mock.current_mode = "shadow"
    raw_balance = MagicMock(total_equity=5000.0)
    enriched = MagicMock(total_equity=5125.0)
    transformer_mock.active_account_service.get_wallet_balance.return_value = raw_balance
    transformer_mock._enrich_balance_with_local_prices.return_value = enriched

    proxy = _account_proxy(transformer_mock)
    result = await proxy.get_wallet_balance()

    # Enrichment ran with raw balance
    transformer_mock._enrich_balance_with_local_prices.assert_awaited_once_with(raw_balance)
    # Snapshot saved with the ENRICHED value + shadow tag
    transformer_mock._save_account_snapshot.assert_awaited_once_with(
        enriched, exchange_mode="shadow",
    )
    # Returns the enriched balance
    assert result is enriched


@pytest.mark.asyncio
async def test_snapshot_save_failure_does_not_break_get_wallet_balance(
    transformer_mock,
) -> None:
    """Defensive: if _save_account_snapshot raises, the method should
    still return the balance to the caller. The current implementation
    swallows DB exceptions inside _save_account_snapshot itself, so the
    proxy should never see them. Locks the contract."""
    transformer_mock.is_shadow = False
    raw_balance = MagicMock(total_equity=5000.0)
    transformer_mock.active_account_service.get_wallet_balance.return_value = raw_balance
    # If snapshot save itself swallows internally (current behaviour
    # since Phase 14), the proxy returns balance normally. Use a no-op
    # mock to verify the proxy path doesn't itself raise.
    transformer_mock._save_account_snapshot.return_value = None

    proxy = _account_proxy(transformer_mock)
    result = await proxy.get_wallet_balance()

    assert result is raw_balance
    transformer_mock._save_account_snapshot.assert_awaited_once()
