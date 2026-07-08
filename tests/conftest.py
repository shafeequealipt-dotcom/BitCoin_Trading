"""Shared test fixtures for the Trading Intelligence MCP test suite."""

import os
import tempfile

import pytest

from src.config.settings import Settings


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield d


@pytest.fixture
def sample_config_toml(tmp_dir):
    """Write a minimal valid config.toml to a temp directory and return its path."""
    config_path = os.path.join(tmp_dir, "config.toml")
    content = """
[general]
mode = "paper"
log_level = "DEBUG"
log_dir = "{log_dir}"

[bybit]
testnet = true
default_symbols = ["BTCUSDT", "ETHUSDT"]

[finnhub]
enabled = false

[reddit]
enabled = false

[altdata]
enabled = true

[database]
path = "{db_path}"

[workers]
enabled = false

[brain]
enabled = false

[risk]
max_leverage = 3
mandatory_stop_loss = true
default_stop_loss_pct = 2.0
default_take_profit_pct = 4.0
max_position_size_pct = 10.0
max_open_positions = 5
daily_loss_limit_pct = 5.0
max_total_exposure_pct = 50.0
max_drawdown_pct = 15.0
min_order_value_usdt = 10.0
loss_cooldown_seconds = 300

[alerts]
telegram_enabled = false

[mcp]
transport = "stdio"
""".format(
        log_dir=os.path.join(tmp_dir, "logs").replace("\\", "/"),
        db_path=os.path.join(tmp_dir, "test.db").replace("\\", "/"),
    )
    with open(config_path, "w") as f:
        f.write(content)
    return config_path


@pytest.fixture
def sample_env_file(tmp_dir):
    """Write a minimal .env file to a temp directory and return its path."""
    env_path = os.path.join(tmp_dir, ".env")
    with open(env_path, "w") as f:
        f.write("BYBIT_API_KEY=test_key_123\n")
        f.write("BYBIT_API_SECRET=test_secret_456\n")
    return env_path


@pytest.fixture
def sample_settings(sample_config_toml, sample_env_file):
    """Load a Settings instance from sample config + env files."""
    Settings.reset()
    settings = Settings._load_fresh(sample_config_toml, sample_env_file)
    yield settings
    Settings.reset()


@pytest.fixture(autouse=True)
def reset_settings_singleton():
    """Ensure Settings singleton is reset between tests."""
    Settings.reset()
    yield
    Settings.reset()
