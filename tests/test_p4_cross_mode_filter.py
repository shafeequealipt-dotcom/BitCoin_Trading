"""P4 — cross-mode SQL filter on thesis_manager.get_open_theses.

Surgical test: filter respects current_mode + falls back to unfiltered
when transformer not wired.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.thesis_manager import ThesisManager


@pytest.mark.asyncio
async def test_get_open_theses_filters_by_current_mode_when_transformer_wired() -> None:
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    tm = ThesisManager(db)
    tm.attach_transformer(SimpleNamespace(current_mode="bybit_demo"))

    await tm.get_open_theses()

    sql, params = db.fetch_all.call_args.args
    assert "exchange_mode = ?" in sql
    assert params == ("bybit_demo",)


@pytest.mark.asyncio
async def test_get_open_theses_unfiltered_when_no_transformer() -> None:
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    tm = ThesisManager(db)
    # No attach_transformer call — early-boot edge case.

    await tm.get_open_theses()

    # call_args.args contains (sql,) tuple — single positional, no params.
    call_args = db.fetch_all.call_args
    assert "exchange_mode = ?" not in call_args.args[0]
