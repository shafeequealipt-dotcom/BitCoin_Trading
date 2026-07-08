"""Focused tests for the sniper partial-close disable (2026-05-26, operator).

When ``mode4.sniper_partial_close_enabled`` is False, ProfitSniper must
NEVER reduce a position: the ``partial_close`` action is redirected to a
trailing-stop tighten (winner protection) and ``_execute_partial_close``
(the only reduce_position caller in the sniper) is never invoked. When the
flag is True, the legacy partial-close path runs unchanged.

Exercises ``ProfitSniper._execute_action`` directly with a minimal mock
``self`` so the gate is tested without constructing the full worker (which
needs ~15 service dependencies). Pairs with
IMPLEMENT_PNL_TRUTH_AND_DISABLE_OVERTIGHTENING.md, Issue 2.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.workers.profit_sniper import ProfitSniper


def _mock_self(partial_enabled: bool) -> MagicMock:
    s = MagicMock()
    s.settings.mode4.sniper_partial_close_enabled = partial_enabled
    s.settings.mode4.partial_close_pct = 50
    # Switching guard: getattr(self,'_transformer',None) or getattr(self,'transformer',None)
    s._transformer = None
    s.transformer = None
    s._last_action_time = {}
    s._last_action_type = {}
    s._apply_trail_stop = AsyncMock(return_value=True)
    s._execute_partial_close = AsyncMock(return_value=True)
    # These tests exercise the LEGACY _execute_action redirect path, which is
    # only active when the Profit-Fetching Exit System is off. When it is on,
    # the spine (_pf_apply_spine, in the tick loop) owns all stop-raising, so
    # _execute_action no longer redirects to _apply_trail_stop — that case is
    # covered by test_pf_enabled_skips_legacy_redirect below.
    s._pf = types.SimpleNamespace(enabled=False)
    return s


def _action() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        action="partial_close",
        source="score",
        score_value=42.0,
        current_pnl=1.5,
        greed_rule_triggered="",
    )


@pytest.mark.asyncio
async def test_disabled_redirects_partial_to_tighten_and_never_reduces() -> None:
    s = _mock_self(partial_enabled=False)
    trail = types.SimpleNamespace(should_apply=True)

    await ProfitSniper._execute_action(s, "BTCUSDT", _action(), trail, MagicMock(), 50000.0)

    # The reduce_position path must NOT run.
    s._execute_partial_close.assert_not_awaited()
    # Redirected to the winner-protecting trail instead.
    s._apply_trail_stop.assert_awaited_once()
    assert s._last_action_type["BTCUSDT"] == "tighten"


@pytest.mark.asyncio
async def test_enabled_still_runs_partial_close() -> None:
    s = _mock_self(partial_enabled=True)
    trail = types.SimpleNamespace(should_apply=True)
    pos = MagicMock()

    await ProfitSniper._execute_action(s, "ETHUSDT", _action(), trail, pos, 3000.0)

    # Legacy behaviour preserved: the partial reduce runs.
    s._execute_partial_close.assert_awaited_once()
    assert s._last_action_type["ETHUSDT"] == "partial_close"


@pytest.mark.asyncio
async def test_pf_enabled_skips_legacy_redirect() -> None:
    """When the Profit-Fetching Exit System is enabled, the spine owns all
    per-tick stop-raising, so the partial-disabled branch of _execute_action
    must NOT redirect to _apply_trail_stop (single stop-writer) and must still
    never reduce the position."""
    s = _mock_self(partial_enabled=False)
    s._pf = types.SimpleNamespace(enabled=True)
    trail = types.SimpleNamespace(should_apply=True)

    await ProfitSniper._execute_action(s, "BTCUSDT", _action(), trail, MagicMock(), 50000.0)

    # The reduce path still must not run.
    s._execute_partial_close.assert_not_awaited()
    # The legacy redirect to _apply_trail_stop is suppressed — the spine owns it.
    s._apply_trail_stop.assert_not_awaited()
