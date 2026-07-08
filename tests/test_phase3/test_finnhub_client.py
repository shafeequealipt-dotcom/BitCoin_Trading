"""Tests for FinnhubClient: async wrapping, error handling."""

import pytest
from unittest.mock import MagicMock, patch

from src.core.exceptions import FinnhubError
from src.intelligence.news.finnhub_client import FinnhubClient


class TestFinnhubClient:
    @pytest.mark.asyncio
    async def test_get_general_news(self, test_settings, mock_finnhub_news_response):
        with patch("src.intelligence.news.finnhub_client.finnhub") as mock_fh:
            mock_client = MagicMock()
            mock_client.general_news.return_value = mock_finnhub_news_response
            mock_fh.Client.return_value = mock_client

            client = FinnhubClient(test_settings)
            client._client = mock_client
            news = await client.get_general_news("crypto")

            assert len(news) == 4
            assert news[0]["headline"] == "Bitcoin rallies past $70,000 as ETF inflows surge"

    @pytest.mark.asyncio
    async def test_get_crypto_news(self, test_settings, mock_finnhub_news_response):
        with patch("src.intelligence.news.finnhub_client.finnhub") as mock_fh:
            mock_client = MagicMock()
            mock_client.general_news.return_value = mock_finnhub_news_response
            mock_fh.Client.return_value = mock_client

            client = FinnhubClient(test_settings)
            client._client = mock_client
            news = await client.get_crypto_news()
            assert len(news) > 0

    @pytest.mark.asyncio
    async def test_get_economic_calendar(self, test_settings, mock_finnhub_calendar_response):
        with patch("src.intelligence.news.finnhub_client.finnhub") as mock_fh:
            mock_client = MagicMock()
            mock_client.economic_calendar.return_value = {
                "economicCalendar": mock_finnhub_calendar_response
            }
            mock_fh.Client.return_value = mock_client

            client = FinnhubClient(test_settings)
            client._client = mock_client
            events = await client.get_economic_calendar("2026-03-01", "2026-03-31")
            assert len(events) == 3

    @pytest.mark.asyncio
    async def test_api_error_wrapped(self, test_settings):
        import finnhub as fh_module
        with patch("src.intelligence.news.finnhub_client.finnhub") as mock_fh:
            mock_client = MagicMock()
            # FinnhubAPIException expects a response object; use a mock
            mock_response = MagicMock()
            mock_response.json.return_value = {"error": "Unauthorized"}
            mock_response.status_code = 401
            mock_client.general_news.side_effect = fh_module.FinnhubAPIException(mock_response)
            mock_fh.Client.return_value = mock_client
            mock_fh.FinnhubAPIException = fh_module.FinnhubAPIException
            mock_fh.FinnhubRequestException = fh_module.FinnhubRequestException

            client = FinnhubClient(test_settings)
            client._client = mock_client
            with pytest.raises(FinnhubError):
                await client.get_general_news()
