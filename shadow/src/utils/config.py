"""Configuration loader: reads config.toml + .env and maps to typed dataclasses.

Follows the same pattern as trading-intelligence-mcp's settings.py:
dataclass per section, builder functions with .get() defaults, singleton via load_config().
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# tomli is in stdlib as tomllib from Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# =============================================================================
# Project root — resolved from this file's location (src/utils/config.py → shadow/)
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# =============================================================================
# Dataclasses — one per config section
# =============================================================================

@dataclass
class GeneralConfig:
    """Top-level project settings."""
    project_name: str = "Shadow"
    log_level: str = "INFO"
    log_dir: str = "logs"


@dataclass
class BybitConfig:
    """Bybit mainnet connection settings (public data only)."""
    base_url: str = "https://api.bybit.com"
    ws_url: str = "wss://stream.bybit.com/v5/public/linear"


@dataclass
class CollectorConfig:
    """Data collector settings."""
    coin_count: int = 100
    coin_refresh_interval: int = 86400
    kline_interval: str = "1"
    ticker_snapshot_interval: int = 60
    funding_rate_interval: int = 28800
    open_interest_interval: int = 300


@dataclass
class ExchangeConfig:
    """Virtual exchange settings."""
    starting_balance: float = 10000.0
    taker_fee_rate: float = 0.00055
    maker_fee_rate: float = 0.00020
    slippage_pct: float = 0.03
    slippage_mode: str = "fixed"
    slippage_min: float = 0.01
    slippage_max: float = 0.05
    position_monitor_interval: int = 1


@dataclass
class DatabaseConfig:
    """SQLite database settings."""
    path: str = "data/shadow.db"
    wal_mode: bool = True
    ticker_retention_days: int = 30
    oi_retention_days: int = 90
    wallet_snapshot_retention_days: int = 30


@dataclass
class ApiConfig:
    """HTTP API server settings."""
    host: str = "127.0.0.1"
    port: int = 9090


@dataclass
class TelegramConfig:
    """Telegram bot settings."""
    chat_id: int = 0
    send_trade_alerts: bool = True
    send_daily_summary: bool = True
    send_health_alerts: bool = True
    bot_token: str = ""


@dataclass
class ShadowConfig:
    """Top-level configuration container holding all sub-configs."""
    general: GeneralConfig = field(default_factory=GeneralConfig)
    bybit: BybitConfig = field(default_factory=BybitConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    _instance: "ShadowConfig | None" = field(default=None, init=False, repr=False)

    @classmethod
    def reset(cls) -> None:
        """Clear the singleton instance (for testing)."""
        cls._instance = None


# =============================================================================
# Builder functions — TOML dict → dataclass
# =============================================================================

def _build_general(data: dict[str, Any], project_root: Path) -> GeneralConfig:
    log_dir = data.get("log_dir", "logs")
    if not os.path.isabs(log_dir):
        log_dir = str(project_root / log_dir)
    return GeneralConfig(
        project_name=data.get("project_name", "Shadow"),
        log_level=data.get("log_level", "INFO"),
        log_dir=log_dir,
    )


def _build_bybit(data: dict[str, Any]) -> BybitConfig:
    return BybitConfig(
        base_url=data.get("base_url", "https://api.bybit.com"),
        ws_url=data.get("ws_url", "wss://stream.bybit.com/v5/public/linear"),
    )


def _build_collector(data: dict[str, Any]) -> CollectorConfig:
    return CollectorConfig(
        coin_count=data.get("coin_count", 100),
        coin_refresh_interval=data.get("coin_refresh_interval", 86400),
        kline_interval=data.get("kline_interval", "1"),
        ticker_snapshot_interval=data.get("ticker_snapshot_interval", 60),
        funding_rate_interval=data.get("funding_rate_interval", 28800),
        open_interest_interval=data.get("open_interest_interval", 300),
    )


def _build_exchange(data: dict[str, Any]) -> ExchangeConfig:
    return ExchangeConfig(
        starting_balance=data.get("starting_balance", 10000.0),
        taker_fee_rate=data.get("taker_fee_rate", 0.00055),
        maker_fee_rate=data.get("maker_fee_rate", 0.00020),
        slippage_pct=data.get("slippage_pct", 0.03),
        slippage_mode=data.get("slippage_mode", "fixed"),
        slippage_min=data.get("slippage_min", 0.01),
        slippage_max=data.get("slippage_max", 0.05),
        position_monitor_interval=data.get("position_monitor_interval", 1),
    )


def _build_database(data: dict[str, Any], project_root: Path) -> DatabaseConfig:
    db_path = data.get("path", "data/shadow.db")
    if not os.path.isabs(db_path):
        db_path = str(project_root / db_path)
    return DatabaseConfig(
        path=db_path,
        wal_mode=data.get("wal_mode", True),
        ticker_retention_days=data.get("ticker_retention_days", 30),
        oi_retention_days=data.get("oi_retention_days", 90),
        wallet_snapshot_retention_days=data.get("wallet_snapshot_retention_days", 30),
    )


def _build_api(data: dict[str, Any]) -> ApiConfig:
    return ApiConfig(
        host=data.get("host", "127.0.0.1"),
        port=data.get("port", 9090),
    )


def _build_telegram(data: dict[str, Any]) -> TelegramConfig:
    return TelegramConfig(
        chat_id=data.get("chat_id", 0),
        send_trade_alerts=data.get("send_trade_alerts", True),
        send_daily_summary=data.get("send_daily_summary", True),
        send_health_alerts=data.get("send_health_alerts", True),
        bot_token=os.environ.get("SHADOW_TELEGRAM_BOT_TOKEN", ""),
    )


# =============================================================================
# Public API — load_config()
# =============================================================================

def load_config(config_path: str | None = None) -> ShadowConfig:
    """Load configuration from config.toml and .env, returning a singleton.

    Args:
        config_path: Path to TOML config file. If None, uses config.toml
                     in the project root.

    Returns:
        Fully populated ShadowConfig instance.

    Raises:
        FileNotFoundError: If config.toml does not exist.
        ValueError: If config.toml is malformed.
    """
    if ShadowConfig._instance is not None:
        return ShadowConfig._instance

    instance = _load_fresh(config_path)
    ShadowConfig._instance = instance
    return instance


def _load_fresh(config_path: str | None = None) -> ShadowConfig:
    """Load config without caching (useful for testing).

    Args:
        config_path: Path to TOML config file.

    Returns:
        New ShadowConfig instance.
    """
    project_root = PROJECT_ROOT

    # Resolve config file path
    if config_path is None:
        config_file = project_root / "config.toml"
    else:
        config_file = Path(config_path)
        if not config_file.is_absolute():
            config_file = project_root / config_file

    # Load .env (optional — missing .env is fine for Phase 1)
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(str(env_path), override=True)

    # Load TOML config
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    try:
        with open(config_file, "rb") as f:
            toml_data = tomllib.load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse {config_file}: {e}") from e

    # Build each section
    general = _build_general(toml_data.get("general", {}), project_root)
    bybit = _build_bybit(toml_data.get("bybit", {}))
    collector = _build_collector(toml_data.get("collector", {}))
    exchange = _build_exchange(toml_data.get("exchange", {}))
    database = _build_database(toml_data.get("database", {}), project_root)
    api = _build_api(toml_data.get("api", {}))
    telegram = _build_telegram(toml_data.get("telegram", {}))

    return ShadowConfig(
        general=general,
        bybit=bybit,
        collector=collector,
        exchange=exchange,
        database=database,
        api=api,
        telegram=telegram,
        project_root=project_root,
    )
