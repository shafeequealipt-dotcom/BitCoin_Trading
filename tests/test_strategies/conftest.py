"""Shared fixtures for strategy infrastructure tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import (
    AlertSettings, AltDataSettings, BrainSettings, BybitSettings,
    DatabaseSettings, FinnhubSettings, GeneralSettings, LeverageSettings,
    MCPSettings, OptimizerSettings, PnLTargetSettings, RedditSettings,
    RegimeSettings, RiskSettings, ScannerSettings, Settings,
    StrategyEngineSettings, WatchdogSettings, WorkerSettings,
)
from src.core.types import OHLCV, Side, Ticker, TimeFrame, AccountInfo
from src.core.utils import now_utc
from src.strategies.models.regime_types import MarketRegime, RegimeState
from src.strategies.models.signal_types import (
    EnsembleResult, EnsembleVote, RawSignal, ScoredSetup, StrategyPerformance,
)


@pytest.fixture
def strategy_settings(tmp_path):
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s",
                            default_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=str(tmp_path / "test.db")),
        workers=WorkerSettings(max_consecutive_failures=3, restart_delay=1),
        brain=BrainSettings(enabled=True, api_key="sk-test"),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        watchdog=WatchdogSettings(enabled=False),
        scanner=ScannerSettings(enabled=True, max_coins=5, min_volume_24h=1000),
        regime=RegimeSettings(),
        strategy_engine=StrategyEngineSettings(min_score_threshold=60),
        pnl_targets=PnLTargetSettings(),
        leverage=LeverageSettings(),
        optimizer=OptimizerSettings(),
        mcp=MCPSettings(),
    )


@pytest.fixture
def sample_regime():
    return RegimeState(
        regime=MarketRegime.TRENDING_UP,
        confidence=0.75,
        adx=30, atr_percentile=110, choppiness=35,
        volume_ratio=1.3, trend_direction=1,
        active_strategy_categories=["scalping", "momentum", "advanced"],
    )


@pytest.fixture
def sample_raw_signal():
    return RawSignal(
        strategy_name="A1_rsi_reversal",
        strategy_category="scalping",
        symbol="BTCUSDT",
        direction=Side.BUY,
        entry_price=70000,
        suggested_stop_loss=69000,
        suggested_take_profit=72000,
        timeframe="5",
        conditions_met={"rsi_oversold": True, "support_bounce": True},
        conditions_strength={"rsi_oversold": 0.85, "support_bounce": 0.7},
    )


@pytest.fixture
def sample_ta_data():
    return {
        "trend": {
            "trend_summary": "BULLISH",
            "adx": {"adx": 30, "plus_di": 25, "minus_di": 15},
        },
        "momentum": {"momentum_summary": "BULLISH", "rsi_14": 45},
        "volatility": {
            "volatility_summary": "MODERATE",
            "atr_14": 500, "natr_14": 1.2,
            "choppiness_index": 35,
        },
        "volume": {"volume_summary": "ABOVE_AVERAGE", "volume_sma_ratio": 1.5},
        "overall": {
            "signal": "BUY", "confidence": 0.7,
            "key_reasons": ["RSI at 45", "Supertrend bullish"],
        },
        "support_resistance": {
            "current_price": 70000,
            "support_levels": [69500, 69000],
            "resistance_levels": [71000, 72000],
        },
    }


@pytest.fixture
def sample_ticker():
    return Ticker(
        symbol="BTCUSDT", last_price=70000, bid=69990, ask=70010,
        high_24h=71000, low_24h=68000, volume_24h=500_000_000,
        change_24h_pct=2.5,
    )


@pytest.fixture
def sample_tickers():
    return [
        Ticker(symbol="BTCUSDT", last_price=70000, bid=69990, ask=70010,
               high_24h=71000, low_24h=68000, volume_24h=800_000_000, change_24h_pct=2.5),
        Ticker(symbol="ETHUSDT", last_price=3500, bid=3498, ask=3502,
               high_24h=3600, low_24h=3400, volume_24h=300_000_000, change_24h_pct=1.8),
        Ticker(symbol="SOLUSDT", last_price=140, bid=139.9, ask=140.1,
               high_24h=145, low_24h=135, volume_24h=150_000_000, change_24h_pct=3.2),
    ]
