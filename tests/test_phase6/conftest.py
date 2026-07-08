"""Shared fixtures for Phase 6 MCP tool tests. All services mocked."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config.settings import (
    AltDataSettings, AlertSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
    RedditSettings, RiskSettings, Settings, WorkerSettings,
)
from src.core.types import (
    AccountInfo, FearGreedData, FundingRate, Order, OrderStatus, OrderType,
    Position, Side, Signal, SignalType, Ticker, TradeRecord,
)
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations


@pytest.fixture
def mock_settings(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s"),
        finnhub=FinnhubSettings(enabled=True, api_key="fk"),
        reddit=RedditSettings(enabled=True, client_id="c", client_secret="s", username="u", password="p"),
        altdata=AltDataSettings(), database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(), brain=BrainSettings(), risk=RiskSettings(),
        alerts=AlertSettings(), mcp=MCPSettings(),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    db = DatabaseManager(str(tmp_path / "mcp_test.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def mock_services():
    """Dict of mocked services matching MCPServer._services."""
    return {
        "account": _mock_account(),
        "market": _mock_market(),
        "order": _mock_order(),
        "position": _mock_position(),
        "instrument": MagicMock(),
        "bybit": MagicMock(is_connected=True, is_testnet=True),
        "news": _mock_news(),
        "calendar": _mock_calendar(),
        "reddit": _mock_reddit(),
        "aggregator": _mock_aggregator(),
        "fear_greed": _mock_fg(),
        "funding": _mock_funding(),
        "oi": _mock_oi(),
        "onchain": _mock_onchain(),
        "signal_gen": _mock_signal_gen(),
        "ta": _mock_ta(),
    }


def _mock_account():
    m = MagicMock()
    m.get_wallet_balance = AsyncMock(return_value=AccountInfo(
        total_equity=10000, available_balance=8000, used_margin=2000, unrealized_pnl=150,
    ))
    m.get_available_balance = AsyncMock(return_value=8000.0)
    return m


def _mock_market():
    m = MagicMock()
    m.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTCUSDT", last_price=70000, bid=69999, ask=70001,
        high_24h=71000, low_24h=69000, volume_24h=5000, change_24h_pct=1.5,
    ))
    m.get_tickers = AsyncMock(return_value=[
        Ticker(symbol="BTCUSDT", last_price=70000, bid=69999, ask=70001,
               high_24h=71000, low_24h=69000, volume_24h=5000, change_24h_pct=1.5),
    ])
    m.get_klines = AsyncMock(return_value=[])
    m.get_orderbook = AsyncMock(return_value={"bids": [[69999, 1]], "asks": [[70001, 1]]})
    return m


def _mock_order():
    m = MagicMock()
    m.place_order = AsyncMock(return_value=Order(
        order_id="ord_123", symbol="BTCUSDT", side=Side.BUY, order_type=OrderType.MARKET,
        price=70000, qty=0.01, status=OrderStatus.NEW,
    ))
    m.cancel_order = AsyncMock(return_value=True)
    m.cancel_all_orders = AsyncMock(return_value=2)
    m.get_open_orders = AsyncMock(return_value=[])
    m.modify_order = AsyncMock(return_value=Order(
        order_id="ord_123", symbol="BTCUSDT", side=Side.BUY, order_type=OrderType.LIMIT,
        price=69000, qty=0.01,
    ))
    return m


def _mock_position():
    m = MagicMock()
    sample_pos = Position(
        symbol="BTCUSDT", side=Side.BUY, size=0.01, entry_price=69000, mark_price=70000,
        unrealized_pnl=10, leverage=2,
    )
    m.get_positions = AsyncMock(return_value=[sample_pos])
    # ``close_position`` MCP handler calls ``await position.get_position(symbol)``
    # before ``await position.close_position(symbol)`` so it can capture the
    # entry_price / unrealized_pnl for the post-close alert. A bare
    # MagicMock attribute returns another MagicMock and ``await mock()``
    # raises "MagicMock can't be used in 'await' expression". Wire it as
    # AsyncMock returning the same sample position.
    m.get_position = AsyncMock(return_value=sample_pos)
    m.close_position = AsyncMock(return_value=Order(
        order_id="close_123", symbol="BTCUSDT", side=Side.SELL, order_type=OrderType.MARKET,
        price=0, qty=0.01,
    ))
    m.get_pnl_summary = AsyncMock(return_value={
        "total_unrealized_pnl": 10, "total_realized_pnl": 50, "position_count": 1,
    })
    return m


def _mock_news():
    m = MagicMock()
    m.fetch_latest_news = AsyncMock(return_value=[])
    m.get_news_for_symbol = AsyncMock(return_value=[])
    m.search_news = AsyncMock(return_value=[])
    return m


def _mock_calendar():
    m = MagicMock()
    m.get_upcoming_events = AsyncMock(return_value=[])
    return m


def _mock_reddit():
    m = MagicMock()
    m.get_symbol_buzz = AsyncMock(return_value={
        "symbol": "BTCUSDT", "mention_count_12h": 15, "mention_count_24h": 30,
        "avg_sentiment": 0.3, "trend": "increasing", "sentiment_direction": "improving",
    })
    m.get_subreddit_mood = AsyncMock(return_value={
        "subreddit": "crypto", "avg_sentiment": 0.2, "post_count": 20,
        "top_post": "BTC bullish", "dominant_mood": "bullish",
    })
    m.get_most_mentioned = AsyncMock(return_value=[
        {"symbol": "BTCUSDT", "mention_count": 50, "avg_sentiment": 0.3, "sample_titles": []},
    ])
    return m


def _mock_aggregator():
    m = MagicMock()
    m.aggregate_for_symbol = AsyncMock(return_value={
        "symbol": "BTCUSDT", "overall_score": 0.3, "level": "bullish",
        "news_score": 0.4, "news_count": 5, "reddit_score": 0.2, "reddit_count": 10,
        "fear_greed_value": 45, "fear_greed_classification": "Fear", "momentum": 0.1,
    })
    m.get_sentiment_shift = AsyncMock(return_value={
        "symbol": "BTCUSDT", "current_score": 0.3, "previous_score": 0.1,
        "shift": 0.2, "direction": "improving",
    })
    return m


def _mock_fg():
    m = MagicMock()
    m.get_latest = AsyncMock(return_value=FearGreedData(value=45, classification="Fear", timestamp=now_utc()))
    m.get_history = AsyncMock(return_value=[])
    m.fetch_current = AsyncMock(return_value=FearGreedData(value=45, classification="Fear", timestamp=now_utc()))
    return m


def _mock_funding():
    m = MagicMock()
    m.fetch_current_rates = AsyncMock(return_value=[
        FundingRate(symbol="BTCUSDT", funding_rate=0.0003, next_funding_time=now_utc(), fetched_at=now_utc()),
    ])
    m.get_rate_history = AsyncMock(return_value=[])
    return m


def _mock_oi():
    m = MagicMock()
    m.fetch_current = AsyncMock(return_value=[{"symbol": "BTCUSDT", "open_interest": 15000, "change_24h_pct": 3.5}])
    return m


def _mock_onchain():
    m = MagicMock()
    m.get_global_metrics = AsyncMock(return_value={"total_market_cap_usd": 2.5e12, "btc_dominance": 52.3})
    return m


def _mock_signal_gen():
    m = MagicMock()
    m.generate_signal = AsyncMock(return_value=Signal(
        symbol="BTCUSDT", signal_type=SignalType.BUY, confidence=0.7,
        source="test", reasoning="Bullish sentiment", components={"score": 0.3},
    ))
    return m


def _mock_ta():
    m = MagicMock()
    m.analyze = AsyncMock(return_value={
        "symbol": "BTCUSDT", "timeframe": "15", "candles_analyzed": 200,
        "current_price": 70000, "timestamp": now_utc().isoformat(),
        "trend": {"sma_20": 69800, "sma_50": 69000, "trend_summary": "BULLISH",
                  "macd": {"histogram": 150}, "adx": {"adx": 30}},
        "momentum": {"rsi_14": 62, "stochastic": {"k": 75}, "momentum_summary": "BULLISH"},
        "volatility": {"atr_14": 450, "bollinger": {"bandwidth": 4.8}, "volatility_summary": "MODERATE"},
        "volume": {"volume_sma_ratio": 1.3, "volume_summary": "ABOVE_AVERAGE"},
        "patterns": {"candlestick": [], "chart": []},
        "support_resistance": {"support_levels": [69000], "resistance_levels": [72000], "current_price": 70000},
        "overall": {"signal": "BUY", "score": 0.35, "confidence": 0.72,
                    "bullish_indicators": 8, "bearish_indicators": 3, "neutral_indicators": 2,
                    "key_reasons": ["RSI at 62 (bullish)", "Price above SMA 50"]},
    })
    m.get_indicator = AsyncMock(return_value={"name": "rsi", "value": 62.5})
    return m
