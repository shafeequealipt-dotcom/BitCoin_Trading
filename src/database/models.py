"""Database row to dataclass mapping helpers.

Handles JSON parsing, NULL values, enum conversion, and timestamp parsing.
"""

import json
from datetime import datetime, timezone
from typing import Any

from src.core.types import (
    OHLCV, AccountInfo, FearGreedData, FundingRate, NewsArticle, Order,
    OrderStatus, OrderType, Position, RedditPost, Side, Signal, SignalType,
    Ticker, TimeFrame, TradeRecord,
)


def _parse_json(val: Any, default: Any = None) -> Any:
    """Safely parse a JSON string."""
    if val is None:
        return default if default is not None else {}
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _parse_dt(val: Any) -> datetime:
    """Parse an ISO timestamp string to datetime."""
    if val is None:
        return datetime.now(timezone.utc)
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def row_to_ohlcv(row: dict) -> OHLCV:
    """Map a klines row to OHLCV dataclass."""
    return OHLCV(
        symbol=row["symbol"],
        timeframe=TimeFrame(row["timeframe"]),
        timestamp=_parse_dt(row["timestamp"]),
        open=_safe_float(row["open"]),
        high=_safe_float(row["high"]),
        low=_safe_float(row["low"]),
        close=_safe_float(row["close"]),
        volume=_safe_float(row["volume"]),
        turnover=_safe_float(row.get("turnover", 0)),
    )


def row_to_ticker(row: dict) -> Ticker:
    """Map a ticker_cache row to Ticker dataclass."""
    return Ticker(
        symbol=row["symbol"],
        last_price=_safe_float(row["last_price"]),
        bid=_safe_float(row.get("bid", 0)),
        ask=_safe_float(row.get("ask", 0)),
        high_24h=_safe_float(row.get("high_24h", 0)),
        low_24h=_safe_float(row.get("low_24h", 0)),
        volume_24h=_safe_float(row.get("volume_24h", 0)),
        change_24h_pct=_safe_float(row.get("change_24h_pct", 0)),
        timestamp=_parse_dt(row.get("updated_at")),
    )


def row_to_order(row: dict) -> Order:
    """Map an orders row to Order dataclass."""
    return Order(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=Side(row["side"]),
        order_type=OrderType(row["order_type"]),
        price=_safe_float(row.get("price", 0)),
        qty=_safe_float(row["qty"]),
        status=OrderStatus(row.get("status", "New")),
        filled_qty=_safe_float(row.get("filled_qty", 0)),
        avg_fill_price=_safe_float(row.get("avg_fill_price", 0)),
        stop_loss=row.get("stop_loss"),
        take_profit=row.get("take_profit"),
        created_at=_parse_dt(row.get("created_at")),
        updated_at=_parse_dt(row.get("updated_at")),
    )


def row_to_position(row: dict) -> Position:
    """Map a positions row to Position dataclass."""
    return Position(
        symbol=row["symbol"],
        side=Side(row["side"]),
        size=_safe_float(row["size"]),
        entry_price=_safe_float(row["entry_price"]),
        mark_price=_safe_float(row.get("mark_price", 0)),
        unrealized_pnl=_safe_float(row.get("unrealized_pnl", 0)),
        realized_pnl=_safe_float(row.get("realized_pnl", 0)),
        leverage=_safe_int(row.get("leverage", 1)),
        liquidation_price=_safe_float(row.get("liquidation_price", 0)),
        stop_loss=row.get("stop_loss"),
        take_profit=row.get("take_profit"),
        updated_at=_parse_dt(row.get("updated_at")),
    )


def row_to_news_article(row: dict) -> NewsArticle:
    """Map a news_articles row to NewsArticle dataclass."""
    symbols_field = row.get("symbols") or row.get("symbols_json", "[]")
    return NewsArticle(
        id=row["id"],
        headline=row.get("headline", ""),
        source=row.get("source", ""),
        url=row.get("url", ""),
        summary=row.get("summary", ""),
        sentiment_score=_safe_float(row.get("sentiment_score", 0)),
        symbols=_parse_json(symbols_field, []),
        category=row.get("category", ""),
        published_at=_parse_dt(row.get("published_at")),
        fetched_at=_parse_dt(row.get("fetched_at")),
    )


def row_to_reddit_post(row: dict) -> RedditPost:
    """Map a reddit_posts row to RedditPost dataclass."""
    symbols_field = row.get("symbols_mentioned") or row.get("symbols_json", "[]")
    return RedditPost(
        id=row["id"],
        subreddit=row.get("subreddit", ""),
        title=row.get("title", ""),
        score=_safe_int(row.get("score", 0)),
        num_comments=_safe_int(row.get("num_comments", 0)),
        upvote_ratio=_safe_float(row.get("upvote_ratio", 0)),
        sentiment_score=_safe_float(row.get("sentiment_score", 0)),
        symbols_mentioned=_parse_json(symbols_field, []),
        permalink=row.get("permalink", ""),
        created_at=_parse_dt(row.get("created_at")),
        fetched_at=_parse_dt(row.get("fetched_at")),
    )


def row_to_trade_record(row: dict) -> TradeRecord:
    """Map a trade_history row to TradeRecord dataclass."""
    return TradeRecord(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        side=Side(row["side"]),
        entry_price=_safe_float(row["entry_price"]),
        exit_price=_safe_float(row.get("exit_price", 0)),
        qty=_safe_float(row["qty"]),
        pnl=_safe_float(row.get("pnl", 0)),
        pnl_pct=_safe_float(row.get("pnl_pct", 0)),
        strategy=row.get("strategy", ""),
        signal_confidence=_safe_float(row.get("signal_confidence", 0)),
        notes=row.get("notes", ""),
        entry_time=_parse_dt(row.get("entry_time")),
        exit_time=_parse_dt(row.get("exit_time")),
    )


def row_to_fear_greed(row: dict) -> FearGreedData:
    """Map a fear_greed_index row to FearGreedData dataclass."""
    return FearGreedData(
        value=_safe_int(row["value"]),
        classification=row.get("classification", ""),
        timestamp=_parse_dt(row.get("timestamp")),
    )


def row_to_funding_rate(row: dict) -> FundingRate:
    """Map a funding_rates row to FundingRate dataclass."""
    return FundingRate(
        symbol=row["symbol"],
        funding_rate=_safe_float(row["funding_rate"]),
        next_funding_time=_parse_dt(row.get("next_funding_time")),
        predicted_rate=_safe_float(row.get("predicted_rate", 0)),
        fetched_at=_parse_dt(row.get("fetched_at")),
    )


def row_to_signal(row: dict) -> Signal:
    """Map a signals row to Signal dataclass."""
    return Signal(
        symbol=row["symbol"],
        signal_type=SignalType(row["signal_type"]),
        confidence=_safe_float(row.get("confidence", 0)),
        source=row.get("source", ""),
        components=_parse_json(row.get("components", "{}"), {}),
        reasoning=row.get("reasoning", ""),
        created_at=_parse_dt(row.get("created_at")),
    )
