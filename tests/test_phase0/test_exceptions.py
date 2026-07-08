"""Tests for the exception hierarchy."""

from datetime import datetime, timezone

from src.core.exceptions import (
    AuthenticationError,
    BybitAPIError,
    ClaudeAPIError,
    ConfigError,
    DailyLossLimitError,
    DatabaseError,
    InsufficientBalanceError,
    MaxDrawdownError,
    OrderError,
    RateLimitError,
    RiskLimitExceededError,
    TradingError,
    TradingMCPError,
    WorkerCrashError,
)


class TestTradingMCPError:
    def test_base_exception_message(self):
        e = TradingMCPError("something broke")
        assert e.message == "something broke"
        assert e.details == {}
        assert isinstance(e.timestamp, datetime)
        assert e.timestamp.tzinfo == timezone.utc

    def test_base_exception_with_details(self):
        e = TradingMCPError("fail", details={"code": 42})
        assert e.details == {"code": 42}
        assert "code" in str(e)
        assert "42" in str(e)

    def test_str_format(self):
        e = TradingMCPError("test error")
        s = str(e)
        assert "TradingMCPError" in s
        assert "test error" in s

    def test_is_exception(self):
        e = TradingMCPError("x")
        assert isinstance(e, Exception)


class TestHierarchy:
    def test_config_error_inherits(self):
        e = ConfigError("bad config")
        assert isinstance(e, TradingMCPError)

    def test_trading_error_chain(self):
        e = InsufficientBalanceError("not enough", details={"balance": 0})
        assert isinstance(e, OrderError)
        assert isinstance(e, TradingError)
        assert isinstance(e, TradingMCPError)

    def test_api_error_chain(self):
        e = BybitAPIError("timeout")
        assert isinstance(e, TradingMCPError)

    def test_brain_error_chain(self):
        e = ClaudeAPIError("rate limited")
        assert isinstance(e, TradingMCPError)

    def test_risk_error_chain(self):
        e = DailyLossLimitError("limit hit", details={"loss": 5.2})
        assert isinstance(e, TradingMCPError)
        assert e.details["loss"] == 5.2

    def test_rate_limit_error(self):
        e = RateLimitError("too fast")
        assert isinstance(e, TradingError)

    def test_database_error(self):
        e = DatabaseError("connection lost")
        assert isinstance(e, TradingMCPError)

    def test_worker_crash(self):
        e = WorkerCrashError("OOM")
        assert isinstance(e, TradingMCPError)

    def test_max_drawdown(self):
        e = MaxDrawdownError("15% drawdown")
        assert isinstance(e, TradingMCPError)

    def test_risk_limit_exceeded(self):
        e = RiskLimitExceededError("position too large")
        assert isinstance(e, TradingMCPError)

    def test_auth_error(self):
        e = AuthenticationError("invalid key")
        assert isinstance(e, TradingMCPError)


class TestExceptionCatching:
    def test_catch_base_catches_all(self):
        exceptions = [
            ConfigError("a"),
            InsufficientBalanceError("b"),
            BybitAPIError("c"),
            ClaudeAPIError("d"),
            DailyLossLimitError("e"),
        ]
        for exc in exceptions:
            try:
                raise exc
            except TradingMCPError as caught:
                assert caught.message in ("a", "b", "c", "d", "e")
