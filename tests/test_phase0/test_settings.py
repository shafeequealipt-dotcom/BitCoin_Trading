"""Tests for settings loading: config.toml + .env → typed dataclasses."""

import os

import pytest

from src.config.settings import Settings
from src.core.exceptions import ConfigError


class TestSettingsLoad:
    def test_loads_from_config(self, sample_config_toml, sample_env_file):
        settings = Settings._load_fresh(sample_config_toml, sample_env_file)
        assert settings.general.mode == "paper"
        assert settings.bybit.testnet is True
        assert settings.brain.enabled is False

    def test_default_symbols(self, sample_config_toml, sample_env_file):
        settings = Settings._load_fresh(sample_config_toml, sample_env_file)
        assert "BTCUSDT" in settings.bybit.default_symbols
        assert "ETHUSDT" in settings.bybit.default_symbols

    def test_env_overrides_config(self, sample_config_toml, sample_env_file):
        settings = Settings._load_fresh(sample_config_toml, sample_env_file)
        assert settings.bybit.api_key == "test_key_123"
        assert settings.bybit.api_secret == "test_secret_456"

    def test_missing_config_uses_defaults(self, tmp_dir):
        """When config.toml doesn't exist, defaults should be used."""
        fake_path = os.path.join(tmp_dir, "nonexistent.toml")
        env_path = os.path.join(tmp_dir, ".env")
        with open(env_path, "w") as f:
            f.write("")
        settings = Settings._load_fresh(fake_path, env_path)
        assert settings.general.mode == "paper"
        assert settings.risk.mandatory_stop_loss is True

    def test_invalid_toml_raises(self, tmp_dir):
        """Malformed TOML should raise ConfigError."""
        bad_toml = os.path.join(tmp_dir, "bad.toml")
        with open(bad_toml, "w") as f:
            f.write("[invalid\nthis is not valid toml!!!")
        env_path = os.path.join(tmp_dir, ".env")
        with open(env_path, "w") as f:
            f.write("")
        with pytest.raises(ConfigError):
            Settings._load_fresh(bad_toml, env_path)


class TestBybitSettings:
    def test_testnet_urls(self, sample_settings):
        assert "testnet" in sample_settings.bybit.base_url
        assert "testnet" in sample_settings.bybit.ws_url

    def test_mainnet_urls(self, sample_config_toml, sample_env_file):
        settings = Settings._load_fresh(sample_config_toml, sample_env_file)
        settings.bybit.testnet = False
        assert "testnet" not in settings.bybit.base_url
        assert "testnet" not in settings.bybit.ws_url
        assert "api.bybit.com" in settings.bybit.base_url


class TestSingleton:
    def test_singleton_returns_same(self, sample_config_toml, sample_env_file):
        s1 = Settings.load(sample_config_toml, sample_env_file)
        s2 = Settings.load(sample_config_toml, sample_env_file)
        assert s1 is s2

    def test_reset_clears_singleton(self, sample_config_toml, sample_env_file):
        s1 = Settings.load(sample_config_toml, sample_env_file)
        Settings.reset()
        s2 = Settings.load(sample_config_toml, sample_env_file)
        assert s1 is not s2


class TestRiskDefaults:
    def test_risk_defaults(self, sample_settings):
        assert sample_settings.risk.max_leverage == 3
        assert sample_settings.risk.mandatory_stop_loss is True
        assert sample_settings.risk.daily_loss_limit_pct == 5.0

    def test_brain_defaults(self, sample_settings):
        assert sample_settings.brain.enabled is False
        assert sample_settings.brain.temperature == 0.3
