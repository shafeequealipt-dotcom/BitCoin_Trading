"""Tests for FearGreedClient: fetch, parse, caching, persistence."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.core.types import FearGreedData
from src.core.utils import now_utc
from src.database.repositories.altdata_repo import AltDataRepository
from src.intelligence.altdata.fear_greed import FearGreedClient


class TestFearGreedClient:
    @pytest.mark.asyncio
    async def test_fetch_current(self, test_db, test_settings, mock_fear_greed_response):
        client = FearGreedClient(test_settings, test_db)

        # Patch the aiohttp session at a lower level
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_fear_greed_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("src.intelligence.altdata.fear_greed.aiohttp.ClientSession", return_value=mock_session):
            fg = await client.fetch_current()

        assert isinstance(fg, FearGreedData)
        assert fg.value == 25
        assert fg.classification == "Extreme Fear"

    @pytest.mark.asyncio
    async def test_persisted_to_db(self, test_db, test_settings, mock_fear_greed_response):
        client = FearGreedClient(test_settings, test_db)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_fear_greed_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("src.intelligence.altdata.fear_greed.aiohttp.ClientSession", return_value=mock_session):
            await client.fetch_current()

        rows = await test_db.fetch_all("SELECT * FROM fear_greed_index")
        assert len(rows) == 1
        assert rows[0]["value"] == 25

    @pytest.mark.asyncio
    async def test_get_history(self, test_db, test_settings):
        repo = AltDataRepository(test_db)
        await repo.save_fear_greed(FearGreedData(value=30, classification="Fear", timestamp=now_utc()))
        await repo.save_fear_greed(FearGreedData(value=50, classification="Neutral", timestamp=now_utc()))

        client = FearGreedClient(test_settings, test_db)
        history = await client.get_history(days=1)
        assert len(history) == 2
