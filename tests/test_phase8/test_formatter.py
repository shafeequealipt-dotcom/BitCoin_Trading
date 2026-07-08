"""Tests for AlertFormatter."""

from src.core.types import Side, SignalType, SentimentLevel
from src.alerts.formatter import AlertFormatter as F


class TestFormatPrice:
    def test_btc_price(self):
        assert "$70,000" in F.format_price(70000)

    def test_small_price(self):
        assert "$0.15" in F.format_price(0.15) or "0.1500" in F.format_price(0.15)

    def test_medium_price(self):
        assert "$3,500" in F.format_price(3500) or "$3500" in F.format_price(3500)


class TestFormatPnl:
    def test_positive(self):
        result = F.format_pnl(142.50, 1.8)
        assert "+$142.50" in result
        assert "+1.8%" in result

    def test_negative(self):
        result = F.format_pnl(-85.30, -1.2)
        assert "$85.30" in result
        assert "-1.2%" in result


class TestFormatSignal:
    def test_all_types(self):
        assert "STRONG BUY" in F.format_signal(SignalType.STRONG_BUY)
        assert "BUY" in F.format_signal(SignalType.BUY)
        assert "NEUTRAL" in F.format_signal(SignalType.NEUTRAL)
        assert "SELL" in F.format_signal(SignalType.SELL)
        assert "STRONG SELL" in F.format_signal(SignalType.STRONG_SELL)


class TestFormatConfidence:
    def test_high_confidence(self):
        result = F.format_confidence(0.85)
        assert "85%" in result
        assert "\u2588" in result  # Filled blocks

    def test_low_confidence(self):
        result = F.format_confidence(0.2)
        assert "20%" in result


class TestFormatFearGreed:
    def test_extreme_fear(self):
        result = F.format_fear_greed(15, "Extreme Fear")
        assert "15" in result

    def test_greed(self):
        result = F.format_fear_greed(70, "Greed")
        assert "70" in result


class TestFormatSide:
    def test_buy(self):
        assert "LONG" in F.format_side(Side.BUY)

    def test_sell(self):
        assert "SHORT" in F.format_side(Side.SELL)


class TestTruncate:
    def test_short_text(self):
        assert F.truncate("short", 100) == "short"

    def test_long_text(self):
        result = F.truncate("x" * 200, 50)
        assert len(result) == 50
        assert result.endswith("...")
