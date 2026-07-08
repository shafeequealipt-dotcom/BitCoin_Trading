"""Shared fixtures for Phase 7 Brain tests. Anthropic client fully mocked."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config.settings import (
    AltDataSettings, AlertSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
    RedditSettings, RiskSettings, Settings, WorkerSettings,
)
from src.core.types import (
    AccountInfo, BrainDecision, FearGreedData, FundingRate, Order, OrderStatus,
    OrderType, Position, Side, Signal, SignalType, Ticker,
)
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations
from src.brain.cost_tracker import CostTracker


@pytest.fixture
def brain_settings(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s", default_symbols=["BTCUSDT", "ETHUSDT"]),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=str(tmp_path / "brain_test.db")),
        workers=WorkerSettings(),
        brain=BrainSettings(enabled=True, api_key="sk-ant-test", model="claude-sonnet-4-20250514",
                            max_tokens=500, min_signal_confidence=0.7, analysis_interval=60),
        risk=RiskSettings(mandatory_stop_loss=True, max_leverage=3, max_open_positions=5),
        alerts=AlertSettings(),
        mcp=MCPSettings(),
    )


@pytest_asyncio.fixture
async def test_db(tmp_path):
    db = DatabaseManager(str(tmp_path / "brain_test.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()


@pytest.fixture
def cost_tracker():
    return CostTracker(daily_budget_usd=1.00)


@pytest.fixture
def mock_anthropic_response():
    """Valid JSON decision from Claude."""
    return json.dumps({
        "action": "buy", "symbol": "BTCUSDT", "confidence": 0.85,
        "order_type": "market", "limit_price": None, "qty_pct": 5.0,
        "stop_loss": 68000, "take_profit": 73000, "leverage": 2,
        "reasoning": "RSI oversold with bullish divergence during extreme fear.",
        "risk_notes": "Position size conservative at 5%.",
    })


@pytest.fixture
def mock_anthropic_hold_response():
    return json.dumps({
        "action": "hold", "symbol": "BTCUSDT", "confidence": 0.4,
        "order_type": "market", "qty_pct": 0, "stop_loss": None,
        "take_profit": None, "leverage": 1,
        "reasoning": "Market is uncertain, no clear edge.", "risk_notes": "",
    })


@pytest.fixture
def mock_anthropic_fenced_response():
    return '```json\n{"action": "buy", "symbol": "ETHUSDT", "confidence": 0.9, "order_type": "market", "qty_pct": 3, "stop_loss": 3500, "take_profit": 4200, "leverage": 1, "reasoning": "Strong momentum.", "risk_notes": ""}\n```'


@pytest.fixture
def mock_services():
    return {
        "account": _mock_account(),
        "market": _mock_market(),
        "order": _mock_order(),
        "position": _mock_position(),
        "news": MagicMock(get_news_summary=AsyncMock(return_value={"total_articles": 5, "avg_sentiment": 0.3})),
        "aggregator": MagicMock(aggregate_for_symbol=AsyncMock(return_value={"symbol": "BTCUSDT", "overall_score": 0.3, "level": "bullish"})),
        "fear_greed": MagicMock(get_latest=AsyncMock(return_value=FearGreedData(value=30, classification="Fear", timestamp=now_utc()))),
        "funding": MagicMock(fetch_current_rates=AsyncMock(return_value=[])),
        "ta": MagicMock(analyze=AsyncMock(return_value={
            "overall": {"signal": "BUY", "score": 0.3, "confidence": 0.7, "bullish_indicators": 5, "bearish_indicators": 2, "neutral_indicators": 1, "key_reasons": []},
            "trend": {"trend_summary": "BULLISH", "macd": {"histogram": 100}},
            "momentum": {"momentum_summary": "BULLISH", "rsi_14": 42},
            "volatility": {"volatility_summary": "MODERATE"},
            "volume": {"volume_summary": "AVERAGE"},
            "patterns": {"candlestick": [], "chart": []},
            "support_resistance": {"support_levels": [68000], "resistance_levels": [73000], "current_price": 70000},
        })),
    }


def _mock_account():
    m = MagicMock()
    m.get_wallet_balance = AsyncMock(return_value=AccountInfo(
        total_equity=10000, available_balance=8000, used_margin=2000, unrealized_pnl=150,
    ))
    return m


def _mock_market():
    m = MagicMock()
    m.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTCUSDT", last_price=70000, bid=69999, ask=70001,
        high_24h=71000, low_24h=69000, volume_24h=5000, change_24h_pct=1.5,
    ))
    return m


def _mock_order():
    m = MagicMock()
    m.place_order = AsyncMock(return_value=Order(
        order_id="brain_ord_001", symbol="BTCUSDT", side=Side.BUY,
        order_type=OrderType.MARKET, price=70000, qty=0.005,
    ))
    return m


def _mock_position():
    m = MagicMock()
    m.get_positions = AsyncMock(return_value=[])
    m.close_position = AsyncMock(return_value=Order(
        order_id="close_001", symbol="BTCUSDT", side=Side.SELL,
        order_type=OrderType.MARKET, price=0, qty=0.01,
    ))
    return m
