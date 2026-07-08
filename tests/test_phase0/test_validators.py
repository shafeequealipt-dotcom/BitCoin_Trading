"""Tests for configuration validation."""

import pytest

from src.config.settings import (
    AlertSettings,
    BrainSettings,
    BybitSettings,
    GeneralSettings,
    MCPSettings,
    RiskSettings,
    Settings,
)
from src.config.validators import validate_config
from src.core.exceptions import ConfigError


def _make_settings(**overrides) -> Settings:
    """Build a Settings with defaults, applying overrides to sub-settings."""
    s = Settings()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


class TestValidMode:
    def test_paper_mode_passes(self):
        s = _make_settings(general=GeneralSettings(mode="paper"))
        warnings = validate_config(s)
        # No ConfigError raised
        assert isinstance(warnings, list)

    def test_live_mode_warns(self):
        s = _make_settings(general=GeneralSettings(mode="live"))
        warnings = validate_config(s)
        assert any("LIVE" in w for w in warnings)

    def test_invalid_mode_raises(self):
        s = _make_settings(general=GeneralSettings(mode="yolo"))
        with pytest.raises(ConfigError, match="Invalid trading mode"):
            validate_config(s)

    def test_live_with_testnet_warns(self):
        s = _make_settings(
            general=GeneralSettings(mode="live"),
            bybit=BybitSettings(testnet=True),
        )
        warnings = validate_config(s)
        assert any("contradictory" in w.lower() for w in warnings)


class TestRiskValidation:
    def test_valid_risk_passes(self):
        s = _make_settings()
        warnings = validate_config(s)
        # Default risk settings should be valid
        assert isinstance(warnings, list)

    def test_leverage_too_high(self):
        s = _make_settings(risk=RiskSettings(max_leverage=200))
        with pytest.raises(ConfigError, match="max_leverage"):
            validate_config(s)

    def test_leverage_zero(self):
        s = _make_settings(risk=RiskSettings(max_leverage=0))
        with pytest.raises(ConfigError, match="max_leverage"):
            validate_config(s)

    def test_mandatory_stop_loss_disabled(self):
        s = _make_settings(risk=RiskSettings(mandatory_stop_loss=False))
        with pytest.raises(ConfigError, match="mandatory_stop_loss"):
            validate_config(s)

    def test_daily_loss_limit_invalid(self):
        s = _make_settings(risk=RiskSettings(daily_loss_limit_pct=-5.0))
        with pytest.raises(ConfigError):
            validate_config(s)

    def test_max_drawdown_invalid(self):
        s = _make_settings(risk=RiskSettings(max_drawdown_pct=0))
        with pytest.raises(ConfigError):
            validate_config(s)

    def test_extreme_stop_loss_warns(self):
        s = _make_settings(risk=RiskSettings(default_stop_loss_pct=15.0))
        warnings = validate_config(s)
        assert any("stop_loss" in w.lower() for w in warnings)


class TestAPIKeyValidation:
    def test_missing_bybit_keys_warns(self):
        s = _make_settings(bybit=BybitSettings(api_key="", api_secret=""))
        warnings = validate_config(s)
        assert any("bybit" in w.lower() for w in warnings)

    def test_present_bybit_keys_no_warn(self):
        s = _make_settings(bybit=BybitSettings(api_key="key", api_secret="secret"))
        warnings = validate_config(s)
        assert not any("bybit" in w.lower() for w in warnings)

    def test_brain_enabled_no_key_warns(self):
        s = _make_settings(brain=BrainSettings(enabled=True, api_key=""))
        warnings = validate_config(s)
        assert any("anthropic" in w.lower() or "brain" in w.lower() for w in warnings)

    def test_telegram_enabled_no_token_warns(self):
        s = _make_settings(
            alerts=AlertSettings(telegram_enabled=True, bot_token="", chat_id=""),
        )
        warnings = validate_config(s)
        assert any("telegram" in w.lower() for w in warnings)


class TestMCPValidation:
    def test_valid_stdio(self):
        s = _make_settings(mcp=MCPSettings(transport="stdio"))
        warnings = validate_config(s)
        assert isinstance(warnings, list)

    def test_invalid_transport(self):
        s = _make_settings(mcp=MCPSettings(transport="grpc"))
        with pytest.raises(ConfigError, match="transport"):
            validate_config(s)

    def test_sse_invalid_port(self):
        s = _make_settings(mcp=MCPSettings(transport="sse", sse_port=99999))
        with pytest.raises(ConfigError, match="port"):
            validate_config(s)

    def test_sse_no_auth_token_warns(self):
        s = _make_settings(
            mcp=MCPSettings(transport="sse", sse_auth_required=True, auth_token=""),
        )
        warnings = validate_config(s)
        assert any("auth" in w.lower() for w in warnings)
