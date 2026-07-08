"""Shared fixtures for Phase 3 intelligence tests.

All external APIs are mocked — no real Finnhub, Reddit, CoinGecko calls.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from src.config.settings import (
    AltDataSettings, AlertSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
    RedditSettings, RiskSettings, Settings, UniverseSettings,
    WorkerSettings,
)
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.intelligence.sentiment.scorer import SentimentScorer


@pytest.fixture
def test_settings(tmp_path):
    """Settings with all intelligence configs populated.

    The universe is constructed with a small but production-shaped
    ``coin_aliases`` map so that ``extract_symbols`` matches full names
    (e.g. "Bitcoin", "Ethereum") in mock article copy — mirroring the
    real ``[universe.coin_aliases]`` config rather than relying on the
    legacy hard-coded map.
    """
    return Settings(
        general=GeneralSettings(mode="paper", log_level="DEBUG", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s"),
        finnhub=FinnhubSettings(enabled=True, api_key="test_finnhub_key", max_articles_per_fetch=50),
        reddit=RedditSettings(
            enabled=True, client_id="cid", client_secret="cs",
            username="u", password="p",
            subreddits=["cryptocurrency", "bitcoin"],
            max_posts_per_sub=10,
        ),
        altdata=AltDataSettings(enabled=True, fear_greed_interval=3600),
        database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(enabled=False),
        brain=BrainSettings(enabled=False),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        mcp=MCPSettings(transport="stdio"),
        universe=UniverseSettings(
            coin_aliases={
                "BTCUSDT": ["bitcoin"],
                "ETHUSDT": ["ethereum", "ether"],
                "SOLUSDT": ["solana"],
                "XRPUSDT": ["ripple"],
                "DOGEUSDT": ["dogecoin"],
            },
        ),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    """Temporary SQLite database with all migrations."""
    db_path = str(tmp_path / "test_intel.db")
    db = DatabaseManager(db_path, wal_mode=True)
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def sample_scorer():
    """Pre-initialized SentimentScorer."""
    return SentimentScorer()


@pytest.fixture
def mock_finnhub_news_response():
    """Realistic Finnhub news JSON."""
    now_ts = int(time.time())
    return [
        {
            "category": "crypto",
            "datetime": now_ts - 100,
            "headline": "Bitcoin rallies past $70,000 as ETF inflows surge",
            "id": 1001,
            "image": "",
            "related": "BTC",
            "source": "CoinDesk",
            "summary": "Bitcoin price has rallied above $70,000 driven by massive institutional ETF buying and bullish market sentiment.",
            "url": "https://example.com/1",
        },
        {
            "category": "crypto",
            "datetime": now_ts - 200,
            "headline": "Ethereum crashes 10% amid SEC regulation fears",
            "id": 1002,
            "image": "",
            "related": "ETH",
            "source": "CryptoNews",
            "summary": "Ethereum dropped sharply as SEC announced new regulation investigation causing panic selling.",
            "url": "https://example.com/2",
        },
        {
            "category": "crypto",
            "datetime": now_ts - 300,
            "headline": "Solana ecosystem growing with new DeFi protocols",
            "id": 1003,
            "image": "",
            "related": "SOL",
            "source": "TheBlock",
            "summary": "Solana continues adoption growth with multiple new DeFi projects launching.",
            "url": "https://example.com/3",
        },
        {
            "category": "crypto",
            "datetime": now_ts - 86500,  # older than 24h
            "headline": "Old stale article should be filtered",
            "id": 1004,
            "image": "",
            "related": "",
            "source": "OldSource",
            "summary": "This is old.",
            "url": "https://example.com/4",
        },
    ]


@pytest.fixture
def mock_finnhub_calendar_response():
    """Realistic economic calendar response."""
    return [
        {
            "event": "FOMC Interest Rate Decision",
            "country": "US",
            "impact": "high",
            "actual": "5.50",
            "estimate": "5.50",
            "prev": "5.25",
            "time": "2026-03-25 18:00:00",
        },
        {
            "event": "CPI Year-over-Year",
            "country": "US",
            "impact": "high",
            "actual": "3.1",
            "estimate": "3.2",
            "prev": "3.4",
            "time": "2026-03-22 13:30:00",
        },
        {
            "event": "Minor Data Release",
            "country": "US",
            "impact": "low",
            "actual": "",
            "estimate": "",
            "prev": "",
            "time": "2026-03-23 10:00:00",
        },
    ]


@pytest.fixture
def mock_reddit_posts():
    """Realistic Reddit post dicts."""
    now_ts = time.time()
    return [
        {
            "id": "post001",
            "title": "Bitcoin to the moon! Bullish breakout incoming!",
            "score": 1500,
            "num_comments": 300,
            "upvote_ratio": 0.92,
            "permalink": "/r/cryptocurrency/comments/post001",
            "created_utc": now_ts - 3600,
            "subreddit": "cryptocurrency",
        },
        {
            "id": "post002",
            "title": "ETH crash incoming? Bearish death cross pattern forming",
            "score": 800,
            "num_comments": 200,
            "upvote_ratio": 0.78,
            "permalink": "/r/cryptocurrency/comments/post002",
            "created_utc": now_ts - 7200,
            "subreddit": "cryptocurrency",
        },
        {
            "id": "post003",
            "title": "Solana growing fast, adoption recovery looking good",
            "score": 500,
            "num_comments": 100,
            "upvote_ratio": 0.85,
            "permalink": "/r/cryptocurrency/comments/post003",
            "created_utc": now_ts - 1800,
            "subreddit": "cryptocurrency",
        },
    ]


@pytest.fixture
def mock_fear_greed_response():
    """Realistic Alternative.me Fear & Greed response."""
    return {
        "name": "Fear and Greed Index",
        "data": [{
            "value": "25",
            "value_classification": "Extreme Fear",
            "timestamp": str(int(time.time())),
        }],
    }


@pytest.fixture
def mock_coingecko_global():
    """Realistic CoinGecko global metrics."""
    return {
        "data": {
            "total_market_cap": {"usd": 2500000000000},
            "market_cap_percentage": {"btc": 52.3, "eth": 17.1},
            "active_cryptocurrencies": 12000,
            "market_cap_change_percentage_24h_usd": 1.5,
        }
    }
