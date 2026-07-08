"""Fixtures for Phase 9 risk tests."""

import pytest
import pytest_asyncio

from src.config.settings import (
    AltDataSettings, AlertSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
    RedditSettings, RiskSettings, Settings, WorkerSettings,
)
from src.core.types import AccountInfo, Position, Side
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.trading.models.instrument import InstrumentInfo


@pytest.fixture
def risk_settings(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s"),
        finnhub=FinnhubSettings(enabled=False), reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(), database=DatabaseSettings(path=str(tmp_path / "risk.db")),
        workers=WorkerSettings(), brain=BrainSettings(),
        risk=RiskSettings(
            max_leverage=3, mandatory_stop_loss=True, default_stop_loss_pct=2.0,
            default_take_profit_pct=4.0, max_position_size_pct=10.0,
            max_open_positions=5, daily_loss_limit_pct=5.0,
            max_total_exposure_pct=50.0, max_drawdown_pct=15.0,
            min_order_value_usdt=10.0, loss_cooldown_seconds=300,
        ),
        alerts=AlertSettings(), mcp=MCPSettings(),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    db = DatabaseManager(str(tmp_path / "risk_test.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def sample_account():
    return AccountInfo(total_equity=10000, available_balance=8000, used_margin=2000, unrealized_pnl=100)


@pytest.fixture
def sample_account_low():
    return AccountInfo(total_equity=500, available_balance=400, used_margin=100, unrealized_pnl=0)


@pytest.fixture
def sample_positions_safe():
    return [
        Position(symbol="BTCUSDT", side=Side.BUY, size=0.01, entry_price=69000, mark_price=70000,
                 unrealized_pnl=10, leverage=2),
        Position(symbol="ETHUSDT", side=Side.BUY, size=0.5, entry_price=3500, mark_price=3600,
                 unrealized_pnl=50, leverage=1),
    ]


@pytest.fixture
def sample_positions_full():
    return [
        Position(symbol="BTCUSDT", side=Side.BUY, size=0.01, entry_price=70000, mark_price=70000, leverage=1),
        Position(symbol="ETHUSDT", side=Side.BUY, size=0.5, entry_price=3500, mark_price=3500, leverage=1),
        Position(symbol="SOLUSDT", side=Side.BUY, size=5, entry_price=150, mark_price=150, leverage=1),
        Position(symbol="XRPUSDT", side=Side.BUY, size=1000, entry_price=0.6, mark_price=0.6, leverage=1),
        Position(symbol="DOGEUSDT", side=Side.SELL, size=5000, entry_price=0.1, mark_price=0.1, leverage=1),
    ]


@pytest.fixture
def sample_instrument_btc():
    return InstrumentInfo(
        symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT", status="Trading",
        min_qty=0.001, max_qty=100, qty_step=0.001, min_price=0.5,
        max_price=999999, price_tick=0.5, min_leverage=1, max_leverage=100,
        leverage_step=0.01, min_notional=5,
    )
