"""Tests for AccountService: balance, equity, margin operations."""

import pytest

from src.core.types import AccountInfo
from src.trading.services.account_service import AccountService


class TestAccountService:
    @pytest.mark.asyncio
    async def test_get_wallet_balance(self, mock_client, test_db):
        svc = AccountService(mock_client, test_db)
        info = await svc.get_wallet_balance()

        assert isinstance(info, AccountInfo)
        assert info.total_equity == 10000.0
        assert info.available_balance == 8000.0
        assert info.used_margin == 2000.0
        assert info.unrealized_pnl == 150.5

    @pytest.mark.asyncio
    async def test_get_available_balance(self, mock_client, test_db):
        svc = AccountService(mock_client, test_db)
        balance = await svc.get_available_balance()
        assert balance == 8000.0

    @pytest.mark.asyncio
    async def test_get_equity(self, mock_client, test_db):
        svc = AccountService(mock_client, test_db)
        equity = await svc.get_equity()
        assert equity == 10000.0

    @pytest.mark.asyncio
    async def test_get_margin_usage(self, mock_client, test_db):
        svc = AccountService(mock_client, test_db)
        margin = await svc.get_margin_usage()

        assert margin["used_margin"] == 2000.0
        assert margin["free_margin"] == 8000.0
        assert margin["total_equity"] == 10000.0
        assert margin["unrealized_pnl"] == 150.5

    @pytest.mark.asyncio
    async def test_wallet_balance_persisted(self, mock_client, test_db):
        """Account snapshot should be saved to database."""
        svc = AccountService(mock_client, test_db)
        await svc.get_wallet_balance()

        rows = await test_db.fetch_all("SELECT * FROM account_snapshots")
        assert len(rows) == 1
        assert rows[0]["total_equity"] == 10000.0

    @pytest.mark.asyncio
    async def test_empty_account(self, mock_client, test_db, mock_bybit_session):
        """Handle empty account response gracefully."""
        mock_bybit_session.get_wallet_balance.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"list": []},
        }
        svc = AccountService(mock_client, test_db)
        info = await svc.get_wallet_balance()
        assert info.total_equity == 0.0
