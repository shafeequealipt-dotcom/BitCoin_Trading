"""Shared fixtures for Phase 2 trading tests.

Provides mocked Bybit sessions, temporary databases, and realistic
API response data. NO real API calls are made in tests.
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.config.settings import (
    AlertSettings,
    AltDataSettings,
    BrainSettings,
    BybitSettings,
    DatabaseSettings,
    FinnhubSettings,
    GeneralSettings,
    MCPSettings,
    RedditSettings,
    RiskSettings,
    Settings,
    WorkerSettings,
)
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.trading.client import BybitClient


@pytest.fixture
def test_settings(tmp_path):
    """Settings configured for paper/testnet trading."""
    return Settings(
        general=GeneralSettings(mode="paper", log_level="DEBUG", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(
            testnet=True,
            api_key="test_api_key_123",
            api_secret="test_api_secret_456",
            default_symbols=["BTCUSDT", "ETHUSDT"],
            rate_limit_per_second=10,
            recv_window=5000,
        ),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(enabled=False),
        database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(enabled=False),
        brain=BrainSettings(enabled=False),
        risk=RiskSettings(
            max_leverage=3,
            mandatory_stop_loss=True,
            default_stop_loss_pct=2.0,
            default_take_profit_pct=4.0,
            max_position_size_pct=10.0,
            max_open_positions=5,
            daily_loss_limit_pct=5.0,
            max_total_exposure_pct=50.0,
            max_drawdown_pct=15.0,
            min_order_value_usdt=10.0,
        ),
        alerts=AlertSettings(telegram_enabled=False),
        mcp=MCPSettings(transport="stdio"),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    """Temporary SQLite database with migrations applied."""
    db_path = str(tmp_path / "test_trading.db")
    db = DatabaseManager(db_path, wal_mode=True)
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def mock_bybit_session():
    """Mocked pybit HTTP session with realistic response data."""
    session = MagicMock()

    # Default: all methods return success
    session.get_wallet_balance.return_value = sample_wallet_response()
    session.get_tickers.return_value = sample_ticker_response()
    session.get_kline.return_value = sample_kline_response()
    session.get_orderbook.return_value = sample_orderbook_response()
    session.get_instruments_info.return_value = sample_instrument_response()
    session.place_order.return_value = sample_place_order_response()
    session.amend_order.return_value = sample_amend_order_response()
    session.cancel_order.return_value = sample_cancel_order_response()
    session.cancel_all_orders.return_value = sample_cancel_all_response()
    session.get_open_orders.return_value = sample_open_orders_response()
    session.get_order_history.return_value = sample_order_history_response()
    session.get_positions.return_value = sample_positions_response()
    session.set_leverage.return_value = {"retCode": 0, "retMsg": "OK", "result": {}}
    session.set_trading_stop.return_value = {"retCode": 0, "retMsg": "OK", "result": {}}
    session.get_public_trade_history.return_value = sample_public_trades_response()

    return session


@pytest_asyncio.fixture
async def mock_client(test_settings, test_db, mock_bybit_session):
    """BybitClient with mocked pybit session (no real API calls)."""
    with patch("src.trading.client.HTTP", return_value=mock_bybit_session):
        with patch("src.trading.auth.BybitAuth.validate_credentials", return_value=True):
            client = BybitClient(test_settings, test_db)
            await client.connect()
            yield client
            await client.disconnect()


# =============================================================================
# Sample API response factories
# =============================================================================

def sample_wallet_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [{
                "totalEquity": "10000.00",
                "totalAvailableBalance": "8000.00",
                "totalInitialMargin": "2000.00",
                "totalPerpUPL": "150.50",
                "accountType": "UNIFIED",
            }]
        },
    }


def sample_ticker_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [{
                "symbol": "BTCUSDT",
                "lastPrice": "70000.00",
                "bid1Price": "69999.50",
                "ask1Price": "70000.50",
                "highPrice24h": "71000.00",
                "lowPrice24h": "69000.00",
                "volume24h": "12345.67",
                "price24hPcnt": "0.0150",
            }]
        },
    }


def sample_kline_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                # Bybit returns newest first
                ["1704110400000", "70100", "70200", "69900", "70000", "100.5", "7035050"],
                ["1704106800000", "69800", "70150", "69700", "70100", "95.2", "6674920"],
                ["1704103200000", "69500", "69900", "69400", "69800", "88.1", "6150580"],
            ]
        },
    }


def sample_orderbook_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "s": "BTCUSDT",
            "b": [["69999.50", "1.5"], ["69999.00", "2.0"], ["69998.50", "0.8"]],
            "a": [["70000.50", "1.2"], ["70001.00", "1.8"], ["70001.50", "0.5"]],
            "ts": 1704110400000,
            "u": 12345,
        },
    }


def sample_instrument_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [{
                "symbol": "BTCUSDT",
                "baseCoin": "BTC",
                "quoteCoin": "USDT",
                "status": "Trading",
                "lotSizeFilter": {
                    "minOrderQty": "0.001",
                    "maxOrderQty": "100",
                    "qtyStep": "0.001",
                    "minNotionalValue": "5",
                },
                "priceFilter": {
                    "minPrice": "0.10",
                    "maxPrice": "999999.00",
                    "tickSize": "0.10",
                },
                "leverageFilter": {
                    "minLeverage": "1",
                    "maxLeverage": "100",
                    "leverageStep": "0.01",
                },
            }]
        },
    }


def sample_place_order_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "orderId": "test-order-id-001",
            "orderLinkId": "",
        },
    }


def sample_amend_order_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "orderId": "test-order-id-001",
            "orderLinkId": "",
        },
    }


def sample_cancel_order_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "orderId": "test-order-id-001",
        },
    }


def sample_cancel_all_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {"orderId": "test-order-id-001"},
                {"orderId": "test-order-id-002"},
            ]
        },
    }


def sample_open_orders_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [{
                "orderId": "test-order-id-001",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "orderType": "Limit",
                "price": "69500.00",
                "qty": "0.01",
                "orderStatus": "New",
                "cumExecQty": "0",
                "avgPrice": "0",
                "stopLoss": "68000",
                "takeProfit": "72000",
                "createdTime": "1704110400000",
                "updatedTime": "1704110400000",
            }]
        },
    }


def sample_order_history_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [{
                "orderId": "test-order-filled-001",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "orderType": "Market",
                "price": "70000.00",
                "qty": "0.01",
                "orderStatus": "Filled",
                "cumExecQty": "0.01",
                "avgPrice": "70000.00",
                "stopLoss": "68000",
                "takeProfit": "73000",
                "createdTime": "1704100000000",
                "updatedTime": "1704100001000",
            }]
        },
    }


def sample_positions_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [{
                "symbol": "BTCUSDT",
                "side": "Buy",
                "size": "0.01",
                "avgPrice": "69000.00",
                "markPrice": "70000.00",
                "unrealisedPnl": "10.00",
                "cumRealisedPnl": "50.00",
                "leverage": "2",
                "liqPrice": "35000.00",
                "stopLoss": "68000",
                "takeProfit": "73000",
                "updatedTime": "1704110400000",
                "positionIdx": 0,
            }]
        },
    }


def sample_public_trades_response() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {"price": "70000.00", "size": "0.1", "side": "Buy", "time": "1704110400000", "isBlockTrade": False},
                {"price": "69999.50", "size": "0.05", "side": "Sell", "time": "1704110399000", "isBlockTrade": False},
            ]
        },
    }
