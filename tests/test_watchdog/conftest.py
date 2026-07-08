"""Fixtures for Position Watchdog tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brain.cost_tracker import CostTracker
from src.brain.decision_parser import DecisionParser
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
    WatchdogSettings,
    WorkerSettings,
)
from src.core.types import (
    AccountInfo,
    Order,
    OrderType,
    Position,
    Side,
    Ticker,
)
from src.core.utils import now_utc


@pytest.fixture
def watchdog_settings(tmp_path):
    """Settings tuned for watchdog testing with short intervals."""
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(
            testnet=True, api_key="k", api_secret="s",
            default_symbols=["BTCUSDT", "ETHUSDT"],
        ),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=str(tmp_path / "wd_test.db")),
        workers=WorkerSettings(max_consecutive_failures=3, restart_delay=1),
        brain=BrainSettings(enabled=True, api_key="sk-test"),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        watchdog=WatchdogSettings(
            enabled=True,
            check_interval_seconds=1,
            loss_warning_pct=1.0,
            trailing_loss_pct=0.5,
            sl_proximity_pct=30.0,
            rapid_move_pct=0.5,
            brain_trigger_loss_pct=1.5,
            brain_cooldown_seconds=5,
            partial_close_pct=50.0,
            max_brain_calls_per_hour=10,
        ),
        mcp=MCPSettings(),
    )


@pytest.fixture
def cost_tracker():
    return CostTracker(daily_budget_usd=1.00)


@pytest.fixture
def decision_parser():
    return DecisionParser()


@pytest.fixture
def mock_position_service():
    svc = MagicMock()
    svc.get_positions = AsyncMock(return_value=[])
    svc.close_position = AsyncMock(return_value=Order(
        order_id="close_001", symbol="BTCUSDT", side=Side.SELL,
        order_type=OrderType.MARKET, price=0, qty=0.01,
    ))
    svc.set_stop_loss = AsyncMock(return_value=True)
    svc.reduce_position = AsyncMock(return_value=Order(
        order_id="reduce_001", symbol="BTCUSDT", side=Side.SELL,
        order_type=OrderType.MARKET, price=0, qty=0.005,
    ))
    return svc


@pytest.fixture
def mock_market_service():
    svc = MagicMock()
    svc.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTCUSDT", last_price=70000, bid=69999, ask=70001,
        high_24h=71000, low_24h=69000, volume_24h=5000, change_24h_pct=1.5,
    ))
    return svc


@pytest.fixture
def mock_order_service():
    svc = MagicMock()
    svc.place_order = AsyncMock(return_value=Order(
        order_id="partial_001", symbol="BTCUSDT", side=Side.SELL,
        order_type=OrderType.MARKET, price=0, qty=0.005,
    ))
    return svc


@pytest.fixture
def mock_account_service():
    svc = MagicMock()
    svc.get_wallet_balance = AsyncMock(return_value=AccountInfo(
        total_equity=10000, available_balance=8000,
        used_margin=2000, unrealized_pnl=-150,
    ))
    return svc


@pytest.fixture
def mock_claude_client():
    client = MagicMock()
    client.send_message = AsyncMock(return_value={
        "text": '{"action": "hold", "symbol": "BTCUSDT", "confidence": 0.6, '
                '"reasoning": "temporary dip, technicals still bullish", "risk_notes": "low urgency"}',
        "cost_usd": 0.005,
    })
    # PositionWatchdog._determine_mode reads these as integers / floats and
    # compares them with ``>=``. A bare MagicMock attribute returns another
    # MagicMock, breaking the comparison with TypeError. Pre-set the
    # heartbeat / failure-counter surface to the production "everything's
    # healthy" defaults so tests don't accidentally trip the safety_net or
    # emergency mode.
    client._consecutive_failures = 0
    client._last_call_attempt_time = 0.0
    client._last_response_time = 0.0
    client._last_call_time = 0.0
    return client


@pytest.fixture
def mock_alert_manager():
    mgr = MagicMock()
    mgr.send_watchdog_alert = AsyncMock()
    mgr.send_watchdog_decision = AsyncMock()
    mgr.send_error_alert = AsyncMock()
    mgr.send_trade_alert = AsyncMock()
    return mgr


@pytest.fixture
def mock_risk_manager():
    mgr = MagicMock()
    mgr.on_trade_closed = AsyncMock()
    return mgr


@pytest.fixture
def mock_ta_engine():
    engine = MagicMock()
    engine.analyze = AsyncMock(return_value={
        "overall": {
            "signal": "SELL",
            "confidence": 0.65,
            "key_reasons": ["RSI overbought at 72", "MACD histogram negative"],
        },
    })
    return engine


@pytest.fixture
def sample_long_position():
    """BTCUSDT LONG losing position."""
    return Position(
        symbol="BTCUSDT", side=Side.BUY, size=0.01,
        entry_price=70000, mark_price=69000,
        unrealized_pnl=-10, leverage=2,
        liquidation_price=35000, stop_loss=68000, take_profit=73000,
    )


@pytest.fixture
def sample_short_position():
    """ETHUSDT SHORT losing position."""
    return Position(
        symbol="ETHUSDT", side=Side.SELL, size=0.1,
        entry_price=3500, mark_price=3600,
        unrealized_pnl=-10, leverage=2,
        liquidation_price=7000, stop_loss=3700, take_profit=3200,
    )


@pytest.fixture
def sample_profitable_position():
    """BTCUSDT LONG profitable position."""
    return Position(
        symbol="BTCUSDT", side=Side.BUY, size=0.01,
        entry_price=70000, mark_price=71000,
        unrealized_pnl=10, leverage=2,
        liquidation_price=35000, stop_loss=68000, take_profit=73000,
    )
