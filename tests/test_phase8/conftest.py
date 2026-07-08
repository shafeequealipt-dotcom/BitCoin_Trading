"""Fixtures for Phase 8 alert tests. Telegram Bot fully mocked."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config.settings import (
    AlertSettings, AltDataSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
    RedditSettings, RiskSettings, Settings, WorkerSettings,
)
from src.core.types import (
    BrainDecision, Order, OrderStatus, OrderType, Side, Signal, SignalType,
)
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations


@pytest.fixture
def alert_settings_enabled(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s"),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(),
        brain=BrainSettings(),
        risk=RiskSettings(),
        alerts=AlertSettings(
            telegram_enabled=True, bot_token="test_token", chat_id="123456",
            trade_alerts=True, signal_alerts=True, error_alerts=True,
            max_alerts_per_minute=10,
        ),
        mcp=MCPSettings(),
    )


@pytest.fixture
def alert_settings_disabled(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s"),
        finnhub=FinnhubSettings(enabled=False), reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(), database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(), brain=BrainSettings(), risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        mcp=MCPSettings(),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    db = DatabaseManager(str(tmp_path / "alert_test.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=True)
    bot.send_message_chunked = AsyncMock(return_value=True)
    bot.connect = AsyncMock(return_value=True)
    bot.disconnect = AsyncMock()
    bot.enabled = True
    bot.total_sent = 0
    bot.total_errors = 0
    bot.get_stats.return_value = {"enabled": True, "total_sent": 0, "total_errors": 0, "success_rate_pct": 100}
    return bot


@pytest.fixture
def sample_order():
    return Order(
        order_id="ord_test_001", symbol="BTCUSDT", side=Side.BUY,
        order_type=OrderType.MARKET, price=70000, qty=0.05,
        status=OrderStatus.NEW, stop_loss=68000, take_profit=73000,
    )


@pytest.fixture
def sample_signal():
    return Signal(
        symbol="BTCUSDT", signal_type=SignalType.STRONG_BUY,
        confidence=0.85, source="intelligence",
        components={"ta": "bullish", "news": "+0.72", "reddit": "very bullish"},
        reasoning="RSI oversold with bullish divergence",
    )


@pytest.fixture
def sample_brain_buy():
    return BrainDecision(
        id="brain_001", action="buy", symbol="BTCUSDT",
        confidence=0.82, reasoning="RSI oversold during extreme fear",
        risk_notes="FOMC in 6 hours",
    )


@pytest.fixture
def sample_brain_hold():
    return BrainDecision(
        id="brain_002", action="hold", symbol="BTCUSDT",
        confidence=0.4, reasoning="Mixed signals, waiting for clarity",
    )
