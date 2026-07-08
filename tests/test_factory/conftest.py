"""Fixtures for Strategy Factory tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import (
    AlertSettings, AltDataSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FactorySettings, FinnhubSettings, GeneralSettings,
    MCPSettings, RedditSettings, RiskSettings, Settings, WorkerSettings,
    WatchdogSettings, ScannerSettings, RegimeSettings, StrategyEngineSettings,
    PnLTargetSettings, LeverageSettings, OptimizerSettings,
)
from src.core.utils import generate_id, now_utc
from src.factory.models.factory_types import DiscoveredPattern, GeneratedStrategy


@pytest.fixture
def factory_settings(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s",
                            default_symbols=["BTCUSDT", "ETHUSDT"]),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(max_consecutive_failures=3, restart_delay=1),
        brain=BrainSettings(enabled=True, api_key="sk-test"),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        watchdog=WatchdogSettings(enabled=False),
        scanner=ScannerSettings(),
        regime=RegimeSettings(),
        strategy_engine=StrategyEngineSettings(),
        pnl_targets=PnLTargetSettings(),
        leverage=LeverageSettings(),
        optimizer=OptimizerSettings(),
        factory=FactorySettings(
            enabled=True,
            min_pattern_occurrences=5,  # Lower for tests
            min_win_rate=0.55,
            min_profit_factor=1.0,
            max_strategies_per_batch=2,
        ),
        mcp=MCPSettings(),
    )


@pytest.fixture
def sample_pattern():
    return DiscoveredPattern(
        id="pat_test123",
        pattern_type="single_var",
        description="RSI < 20 on BTCUSDT 5-min → bounce 72%",
        conditions={"rsi_below": 20},
        symbols=["BTCUSDT"],
        timeframe="5",
        direction="long",
        occurrences=45,
        wins=32,
        losses=13,
        win_rate=0.72,
        avg_profit_pct=0.42,
        avg_loss_pct=0.28,
        profit_factor=2.57,
        is_valid=True,
        discovered_at=now_utc(),
    )


@pytest.fixture
def valid_strategy_code():
    return '''
"""Generated strategy for testing."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class GenTest(BaseStrategy):

    @property
    def name(self) -> str: return "GEN_test"
    @property
    def category(self) -> str: return "ai_generated"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M5

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        if not candles or len(candles) < 20:
            return None
        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None or rsi > 20:
            return None
        price = ticker.last_price if ticker else candles[-1].close
        return RawSignal(
            strategy_name=self.name, strategy_category=self.category,
            symbol=symbol, direction=Side.BUY, entry_price=price,
            suggested_stop_loss=price * 0.99, suggested_take_profit=price * 1.01,
            timeframe=self.timeframe.value,
            conditions_met={"rsi_oversold": rsi},
            conditions_strength={"rsi_oversold": 0.8},
            created_at=now_utc(),
        )

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        return ("NEUTRAL", 0.3, "Generated strategy")
'''


@pytest.fixture
def invalid_strategy_code():
    return '''
import os
import subprocess

class BadStrategy:
    def scan(self):
        os.system("rm -rf /")
        print("hello")
'''
