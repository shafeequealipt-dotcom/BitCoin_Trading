"""Tests for TradeValidator — highest coverage required."""

import pytest
from src.core.types import Side
from src.risk.validators import TradeValidator


class TestValidOrder:
    def test_valid_buy(self, risk_settings, sample_account, sample_instrument_btc):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, 68000, 73000, 2,
            sample_account, [], sample_instrument_btc,
        )
        assert valid is True
        assert issues == []


class TestRejections:
    def test_missing_stop_loss(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, None, 73000, 1,
            sample_account, [],
        )
        assert valid is False
        assert any("stop-loss" in i.lower() or "Stop-loss" in i for i in issues)

    def test_leverage_too_high(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, 68000, None, 5,
            sample_account, [],
        )
        assert valid is False
        assert any("leverage" in i.lower() for i in issues)

    def test_position_too_large(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.5, 70000, 68000, None, 1,
            sample_account, [],  # 0.5 * 70000 = $35000 = 350% of $10K
        )
        assert valid is False
        assert any("position size" in i.lower() or "Position size" in i for i in issues)

    def test_max_positions_reached(self, risk_settings, sample_account, sample_positions_full):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "ADAUSDT", Side.BUY, 100, 0.5, 0.45, None, 1,
            sample_account, sample_positions_full,
        )
        assert valid is False
        assert any("max" in i.lower() and "position" in i.lower() for i in issues)

    def test_sl_wrong_side_buy(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, 72000, None, 1,  # SL above entry for BUY
            sample_account, [],
        )
        assert valid is False
        assert any("below" in i.lower() for i in issues)

    def test_sl_wrong_side_sell(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.SELL, 0.01, 70000, 68000, None, 1,  # SL below entry for SELL
            sample_account, [],
        )
        assert valid is False
        assert any("above" in i.lower() for i in issues)

    def test_sl_too_close(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, 69995, None, 1,  # <0.01% SL
            sample_account, [],
        )
        assert valid is False
        assert any("too close" in i.lower() for i in issues)

    def test_sl_too_far(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, 50000, None, 1,  # 28.5% SL
            sample_account, [],
        )
        assert valid is False
        assert any("too far" in i.lower() for i in issues)

    def test_insufficient_balance(self, risk_settings, sample_account_low):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.1, 70000, 68000, None, 1,  # $7000 margin needed
            sample_account_low, [],
        )
        assert valid is False
        assert any("insufficient" in i.lower() or "balance" in i.lower() for i in issues)

    def test_duplicate_position(self, risk_settings, sample_account, sample_positions_safe):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.01, 70000, 68000, None, 1,
            sample_account, sample_positions_safe,
        )
        assert any("already" in i.lower() for i in issues)

    def test_unsupported_symbol(self, risk_settings, sample_account):
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "FAKECOIN", Side.BUY, 1, 100, 90, None, 1,
            sample_account, [],
        )
        assert valid is False
        assert any("unsupported" in i.lower() for i in issues)

    def test_hard_leverage_limit(self, risk_settings, sample_account):
        risk_settings.risk.max_leverage = 50  # Config says 50, but hard limit is 10
        v = TradeValidator(risk_settings)
        valid, issues = v.validate_order(
            "BTCUSDT", Side.BUY, 0.001, 70000, 68000, None, 15,
            sample_account, [],
        )
        assert valid is False
        assert any("10" in i for i in issues)


class TestRiskParamValidation:
    def test_sane_settings_pass(self, risk_settings):
        v = TradeValidator(risk_settings)
        issues = v.validate_risk_params(risk_settings)
        assert issues == []

    def test_insane_leverage(self, risk_settings):
        risk_settings.risk.max_leverage = 100
        v = TradeValidator(risk_settings)
        issues = v.validate_risk_params(risk_settings)
        assert any("max_leverage" in i for i in issues)


class TestCalculations:
    def test_required_margin(self, risk_settings):
        v = TradeValidator(risk_settings)
        margin = v.calculate_required_margin(0.1, 70000, 2)
        assert margin == 3500  # 0.1 * 70000 / 2

    def test_risk_reward(self, risk_settings):
        v = TradeValidator(risk_settings)
        rr = v.calculate_risk_reward(70000, 68000, 74000, Side.BUY)
        assert rr == 2.0  # 4000/2000
