"""Issue 1 of cascade-fix series — FearGreedClient.get_history clamps
``days`` to [1, 365] and ``limit`` to [1, 10000] before delegating to
the repo. Defensive layer so any UI/MCP caller cannot accidentally
trigger a wide scan even if the repo signature is later loosened.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.intelligence.altdata.fear_greed import FearGreedClient


class _StubSettings:
    class _Alt:
        fear_greed_interval = 3600  # one hour

    altdata = _Alt()


class _StubDB:
    """The client only uses self._db indirectly through the repo —
    the repo itself is mocked so the DB never sees a query."""


@pytest.mark.asyncio
async def test_get_history_clamps_days_high() -> None:
    client = FearGreedClient(_StubSettings(), _StubDB())
    client._repo = AsyncMock()
    client._repo.get_fear_greed_history = AsyncMock(return_value=[])

    await client.get_history(days=99_999)
    client._repo.get_fear_greed_history.assert_awaited_once_with(
        days=365, limit=10_000,
    )


@pytest.mark.asyncio
async def test_get_history_clamps_days_low() -> None:
    client = FearGreedClient(_StubSettings(), _StubDB())
    client._repo = AsyncMock()
    client._repo.get_fear_greed_history = AsyncMock(return_value=[])

    await client.get_history(days=0)
    # min clamp is 1 day.
    client._repo.get_fear_greed_history.assert_awaited_once_with(
        days=1, limit=10_000,
    )


@pytest.mark.asyncio
async def test_get_history_clamps_limit_high() -> None:
    client = FearGreedClient(_StubSettings(), _StubDB())
    client._repo = AsyncMock()
    client._repo.get_fear_greed_history = AsyncMock(return_value=[])

    await client.get_history(days=7, limit=99_999)
    client._repo.get_fear_greed_history.assert_awaited_once_with(
        days=7, limit=10_000,
    )


@pytest.mark.asyncio
async def test_get_history_passes_through_normal_values() -> None:
    client = FearGreedClient(_StubSettings(), _StubDB())
    client._repo = AsyncMock()
    client._repo.get_fear_greed_history = AsyncMock(return_value=[])

    await client.get_history(days=7, limit=200)
    client._repo.get_fear_greed_history.assert_awaited_once_with(
        days=7, limit=200,
    )
