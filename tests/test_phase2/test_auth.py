"""Tests for Bybit authentication helper."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.core.exceptions import AuthenticationError
from src.trading.auth import BybitAuth


class TestBybitAuth:
    def test_init_with_valid_keys(self):
        auth = BybitAuth("key123", "secret456")
        assert auth.api_key == "key123"
        assert auth.api_secret == "secret456"

    def test_init_raises_on_empty_key(self):
        with pytest.raises(AuthenticationError, match="required"):
            BybitAuth("", "secret")

    def test_init_raises_on_empty_secret(self):
        with pytest.raises(AuthenticationError, match="required"):
            BybitAuth("key", "")

    def test_generate_signature(self):
        auth = BybitAuth("mykey", "mysecret")
        sig = auth.generate_signature(1704110400000, 5000, "symbol=BTCUSDT")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest

    def test_signature_changes_with_params(self):
        auth = BybitAuth("mykey", "mysecret")
        sig1 = auth.generate_signature(1704110400000, 5000, "a=1")
        sig2 = auth.generate_signature(1704110400000, 5000, "a=2")
        assert sig1 != sig2

    def test_signature_changes_with_timestamp(self):
        auth = BybitAuth("mykey", "mysecret")
        sig1 = auth.generate_signature(1000, 5000, "")
        sig2 = auth.generate_signature(2000, 5000, "")
        assert sig1 != sig2

    @pytest.mark.asyncio
    async def test_validate_credentials_success(self):
        auth = BybitAuth("key", "secret")
        session = MagicMock()
        session.get_wallet_balance.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"list": []},
        }
        result = await auth.validate_credentials(session)
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_credentials_invalid_key(self):
        auth = BybitAuth("bad_key", "bad_secret")
        session = MagicMock()
        session.get_wallet_balance.return_value = {
            "retCode": 10003,
            "retMsg": "Invalid Api-Key",
            "result": {},
        }
        with pytest.raises(AuthenticationError, match="Invalid"):
            await auth.validate_credentials(session)

    @pytest.mark.asyncio
    async def test_validate_credentials_network_error(self):
        auth = BybitAuth("key", "secret")
        session = MagicMock()
        session.get_wallet_balance.side_effect = ConnectionError("timeout")
        with pytest.raises(AuthenticationError, match="Failed"):
            await auth.validate_credentials(session)
