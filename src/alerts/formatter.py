"""Data formatting helpers for Telegram alert messages."""

from datetime import datetime

from src.core.types import SentimentLevel, Side, SignalType
from src.core.utils import format_price as _core_format_price


class AlertFormatter:
    """Static methods for formatting trading data into readable Telegram text."""

    @staticmethod
    def format_price(price: float, symbol: str = "") -> str:
        """Format price with magnitude-aware precision (delegates to core).

        The old fixed-tier ``<$1 -> .4f`` mangled sub-cent coins (0.0001584
        rendered "$0.0002"); this now uses the canonical
        ``src.core.utils.format_price`` ($-prefixed, grouped, trailing zeros
        stripped). The ``symbol`` arg is kept for signature compatibility;
        exact per-symbol tick precision is applied by callers holding a
        ``PriceFormatter`` (the alert templates, post C5).
        """
        return f"${_core_format_price(price, grouped=True, strip_zeros=True)}"

    @staticmethod
    def format_pnl(pnl: float, pnl_pct: float = 0.0) -> str:
        """Format PnL with colored emoji."""
        emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"  # green/red circle
        sign = "+" if pnl >= 0 else ""
        pct_str = f" ({sign}{pnl_pct:.1f}%)" if pnl_pct != 0 else ""
        return f"{emoji} {sign}${abs(pnl):,.2f}{pct_str}"

    @staticmethod
    def format_signal(signal_type: SignalType) -> str:
        """Format signal type with emojis."""
        mapping = {
            SignalType.STRONG_BUY: "\U0001f7e2\U0001f7e2 STRONG BUY",
            SignalType.BUY: "\U0001f7e2 BUY",
            SignalType.NEUTRAL: "\U0001f7e1 NEUTRAL",
            SignalType.SELL: "\U0001f534 SELL",
            SignalType.STRONG_SELL: "\U0001f534\U0001f534 STRONG SELL",
        }
        return mapping.get(signal_type, str(signal_type.value))

    @staticmethod
    def format_sentiment(level: SentimentLevel) -> str:
        """Format sentiment level with emoji."""
        mapping = {
            SentimentLevel.VERY_BULLISH: "\U0001f680 Very Bullish",
            SentimentLevel.BULLISH: "\U0001f4c8 Bullish",
            SentimentLevel.NEUTRAL: "\U0001f610 Neutral",
            SentimentLevel.BEARISH: "\U0001f4c9 Bearish",
            SentimentLevel.VERY_BEARISH: "\U0001f480 Very Bearish",
        }
        return mapping.get(level, str(level.value))

    @staticmethod
    def format_fear_greed(value: int, classification: str) -> str:
        """Format Fear & Greed with emoji."""
        if value < 25:
            emoji = "\U0001f631"
        elif value < 40:
            emoji = "\U0001f628"
        elif value < 60:
            emoji = "\U0001f610"
        elif value < 75:
            emoji = "\U0001f60f"
        else:
            emoji = "\U0001f911"
        return f"{emoji} {value} — {classification}"

    @staticmethod
    def format_confidence(confidence: float) -> str:
        """Format confidence as percentage with visual bar."""
        pct = int(confidence * 100)
        filled = int(confidence * 10)
        bar = "\u2588" * filled + "\u2591" * (10 - filled)
        return f"{pct}% {bar}"

    @staticmethod
    def format_side(side: Side) -> str:
        """Format order side."""
        if side == Side.BUY:
            return "\U0001f7e2 LONG"
        return "\U0001f534 SHORT"

    @staticmethod
    def format_timestamp(dt: datetime | None = None) -> str:
        """Format datetime for display."""
        if dt is None:
            from src.core.utils import now_utc
            dt = now_utc()
        return dt.strftime("%b %d, %H:%M UTC")

    @staticmethod
    def format_currency(amount: float) -> str:
        """Format currency amount."""
        if abs(amount) >= 1000:
            return f"${amount:,.2f}"
        return f"${amount:.2f}"

    @staticmethod
    def truncate(text: str, max_length: int = 100) -> str:
        """Truncate text with ellipsis."""
        if len(text) <= max_length:
            return text
        return text[:max_length - 3] + "..."
