"""Tests for BybitClient: connection, response handling, safety assertions."""

import pytest
from unittest.mock import MagicMock, patch

from src.config.settings import BybitSettings, GeneralSettings, Settings
from src.core.exceptions import AuthenticationError, BybitAPIError, RateLimitError
from src.trading.client import BybitClient


class TestBybitClientInit:
    def test_safety_assertion_blocks_mainnet_in_paper(self, test_settings, test_db):
        """Mainnet with paper mode should raise RuntimeError."""
        test_settings.bybit.testnet = False
        test_settings.general.mode = "paper"
        with pytest.raises(RuntimeError, match="SAFETY"):
            BybitClient(test_settings, test_db)

    def test_testnet_paper_allowed(self, test_settings, test_db):
        """Testnet with paper mode should work."""
        client = BybitClient(test_settings, test_db)
        assert client.is_testnet is True
        assert client.is_connected is False

    def test_mainnet_live_allowed(self, test_settings, test_db):
        """Mainnet with live mode should be allowed."""
        test_settings.bybit.testnet = False
        test_settings.general.mode = "live"
        client = BybitClient(test_settings, test_db)
        assert client.is_testnet is False


class TestBybitClientConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, mock_client):
        assert mock_client.is_connected is True
        assert mock_client.session is not None

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_client):
        await mock_client.disconnect()
        assert mock_client.is_connected is False

    @pytest.mark.asyncio
    async def test_session_raises_before_connect(self, test_settings, test_db):
        client = BybitClient(test_settings, test_db)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = client.session


class TestBybitClientCall:
    @pytest.mark.asyncio
    async def test_call_success(self, mock_client, mock_bybit_session):
        result = await mock_client.call("get_tickers", category="linear", symbol="BTCUSDT")
        assert "list" in result

    @pytest.mark.asyncio
    async def test_call_api_error(self, mock_client, mock_bybit_session):
        mock_bybit_session.get_tickers.return_value = {
            "retCode": 10001,
            "retMsg": "Request parameter error",
            "result": {},
        }
        with pytest.raises(BybitAPIError, match="10001"):
            await mock_client.call("get_tickers", category="linear")

    @pytest.mark.asyncio
    async def test_call_rate_limit_error(self, mock_client, mock_bybit_session):
        mock_bybit_session.get_tickers.return_value = {
            "retCode": 10006,
            "retMsg": "Too many requests",
            "result": {},
        }
        with pytest.raises(RateLimitError):
            await mock_client.call("get_tickers", category="linear")

    @pytest.mark.asyncio
    async def test_call_auth_error(self, mock_client, mock_bybit_session):
        mock_bybit_session.get_tickers.return_value = {
            "retCode": 10003,
            "retMsg": "Invalid Api-Key",
            "result": {},
        }
        with pytest.raises(AuthenticationError):
            await mock_client.call("get_tickers", category="linear")

    @pytest.mark.asyncio
    async def test_call_passes_kwargs(self, mock_client, mock_bybit_session):
        """Verify kwargs are forwarded to the pybit method."""
        await mock_client.call("get_tickers", category="linear", symbol="ETHUSDT")
        mock_bybit_session.get_tickers.assert_called_with(category="linear", symbol="ETHUSDT")
