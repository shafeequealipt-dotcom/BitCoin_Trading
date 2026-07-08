"""Shared fixtures for Phase 5 worker tests. All services are mocked."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config.settings import (
    AltDataSettings, AlertSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
    RedditSettings, RiskSettings, Settings, WorkerSettings,
)
from src.core.types import FearGreedData, FundingRate, Signal, SignalType, Ticker, WorkerStatus
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations


@pytest.fixture
def mock_settings(tmp_path):
    """Settings with short intervals for fast tests."""
    return Settings(
        general=GeneralSettings(mode="paper", log_level="DEBUG", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s", default_symbols=["BTCUSDT", "ETHUSDT"]),
        finnhub=FinnhubSettings(enabled=True, api_key="fk"),
        reddit=RedditSettings(enabled=True, client_id="c", client_secret="s",
                              username="u", password="p", subreddits=["crypto"]),
        altdata=AltDataSettings(enabled=True),
        database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(
            enabled=True, market_data_interval=1, news_interval=1,
            reddit_interval=1, altdata_interval=1, health_check_interval=1,
            max_consecutive_failures=3, restart_delay=1,
        ),
        brain=BrainSettings(enabled=False),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        mcp=MCPSettings(transport="stdio"),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    db = DatabaseManager(str(tmp_path / "workers_test.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def mock_market_service():
    svc = MagicMock()
    svc.get_klines = AsyncMock(return_value=[])
    svc.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTCUSDT", last_price=70000, bid=69999, ask=70001,
        high_24h=71000, low_24h=69000, volume_24h=5000, change_24h_pct=1.5,
    ))
    return svc


@pytest.fixture
def mock_news_service():
    svc = MagicMock()
    svc.fetch_latest_news = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def mock_calendar_service():
    svc = MagicMock()
    svc.get_upcoming_events = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def mock_reddit_service():
    svc = MagicMock()
    svc.scan_subreddits = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def mock_fear_greed():
    client = MagicMock()
    client.fetch_current = AsyncMock(return_value=FearGreedData(
        value=45, classification="Fear", timestamp=now_utc(),
    ))
    return client


@pytest.fixture
def mock_funding_tracker():
    tracker = MagicMock()
    tracker.fetch_current_rates = AsyncMock(return_value=[
        FundingRate(symbol="BTCUSDT", funding_rate=0.0001,
                    next_funding_time=now_utc(), fetched_at=now_utc()),
    ])
    return tracker


@pytest.fixture
def mock_oi_tracker():
    tracker = MagicMock()
    tracker.fetch_current = AsyncMock(return_value=[
        {"symbol": "BTCUSDT", "open_interest": 15000},
    ])
    return tracker


@pytest.fixture
def mock_onchain():
    client = MagicMock()
    client.get_global_metrics = AsyncMock(return_value={
        "total_market_cap_usd": 2500000000000,
        "btc_dominance": 52.3,
    })
    return client


@pytest.fixture
def mock_ta_engine():
    engine = MagicMock()
    engine.analyze = AsyncMock(return_value={
        "overall": {"signal": "BUY", "score": 0.3, "confidence": 0.7},
    })
    return engine


@pytest.fixture
def mock_aggregator():
    agg = MagicMock()
    agg.aggregate_for_symbol = AsyncMock(return_value={
        "symbol": "BTCUSDT", "overall_score": 0.3, "level": "bullish",
        "news_count": 5, "reddit_count": 10,
    })
    return agg


@pytest.fixture
def mock_signal_generator():
    gen = MagicMock()
    gen.generate_signal = AsyncMock(return_value=Signal(
        symbol="BTCUSDT", signal_type=SignalType.BUY, confidence=0.7,
        source="test", reasoning="test signal",
    ))
    return gen


@pytest.fixture
def mock_bybit_ws():
    ws = MagicMock()
    ws.connect_public = AsyncMock()
    ws.subscribe_ticker = MagicMock()
    ws.disconnect = AsyncMock()
    ws.is_running = True
    return ws


@pytest.fixture
def mock_scanner():
    """MarketScanner stand-in returning the test universe.

    The seven-workers universe-integration engagement made the scanner
    the SOLE source of truth for active coins (HR-1). Workers now
    early-return on empty/missing scanner (HR-3 gates), so any test
    that exercises the per-coin path must inject a scanner returning
    a non-empty universe.
    """
    scanner = MagicMock()
    scanner.get_active_universe = AsyncMock(return_value=["BTCUSDT", "ETHUSDT"])
    scanner.subscribe = MagicMock()
    return scanner
