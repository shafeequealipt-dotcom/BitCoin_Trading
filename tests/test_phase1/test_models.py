"""Tests for database model mapping helpers."""

from src.database.models import (
    row_to_fear_greed, row_to_funding_rate, row_to_news_article,
    row_to_ohlcv, row_to_order, row_to_position, row_to_reddit_post,
    row_to_signal, row_to_ticker, row_to_trade_record,
)
from src.core.types import (
    OHLCV, FearGreedData, FundingRate, NewsArticle, Order, OrderStatus,
    OrderType, Position, RedditPost, Side, Signal, SignalType, Ticker,
    TimeFrame, TradeRecord,
)


class TestRowToOHLCV:
    def test_basic(self):
        row = {"symbol": "BTCUSDT", "timeframe": "15", "timestamp": "2024-01-01T00:00:00",
               "open": 70000, "high": 71000, "low": 69000, "close": 70500,
               "volume": 100, "turnover": 7000000}
        result = row_to_ohlcv(row)
        assert isinstance(result, OHLCV)
        assert result.symbol == "BTCUSDT"
        assert result.timeframe == TimeFrame.M15
        assert result.close == 70500

    def test_missing_turnover(self):
        row = {"symbol": "BTCUSDT", "timeframe": "60", "timestamp": "2024-01-01",
               "open": 100, "high": 110, "low": 90, "close": 105, "volume": 50}
        result = row_to_ohlcv(row)
        assert result.turnover == 0


class TestRowToTicker:
    def test_basic(self):
        row = {"symbol": "ETHUSDT", "last_price": 3500, "bid": 3499,
               "ask": 3501, "high_24h": 3600, "low_24h": 3400,
               "volume_24h": 1000, "change_24h_pct": 2.5, "updated_at": "2024-01-01"}
        result = row_to_ticker(row)
        assert isinstance(result, Ticker)
        assert result.last_price == 3500


class TestRowToOrder:
    def test_basic(self):
        row = {"order_id": "ord_1", "symbol": "BTCUSDT", "side": "Buy",
               "order_type": "Market", "price": 70000, "qty": 0.01,
               "status": "New", "filled_qty": 0, "avg_fill_price": 0,
               "stop_loss": 68000, "take_profit": None,
               "created_at": "2024-01-01", "updated_at": "2024-01-01"}
        result = row_to_order(row)
        assert isinstance(result, Order)
        assert result.side == Side.BUY
        assert result.status == OrderStatus.NEW


class TestRowToPosition:
    def test_basic(self):
        row = {"symbol": "BTCUSDT", "side": "Sell", "size": 0.05,
               "entry_price": 71000, "mark_price": 70000,
               "unrealized_pnl": 50, "realized_pnl": 0, "leverage": 3,
               "liquidation_price": 80000, "stop_loss": None, "take_profit": None,
               "updated_at": "2024-01-01"}
        result = row_to_position(row)
        assert isinstance(result, Position)
        assert result.side == Side.SELL


class TestRowToNewsArticle:
    def test_with_json_symbols(self):
        row = {"id": "n1", "headline": "BTC Up", "source": "X", "url": "",
               "summary": "", "sentiment_score": 0.5,
               "symbols": '["BTCUSDT"]', "category": "crypto",
               "published_at": "2024-01-01", "fetched_at": "2024-01-01"}
        result = row_to_news_article(row)
        assert result.symbols == ["BTCUSDT"]

    def test_null_symbols(self):
        row = {"id": "n2", "headline": "News", "source": "Y", "url": "",
               "summary": "", "sentiment_score": 0, "symbols": None,
               "symbols_json": None, "category": "",
               "published_at": None, "fetched_at": None}
        result = row_to_news_article(row)
        assert result.symbols == []


class TestRowToSignal:
    def test_basic(self):
        row = {"symbol": "BTCUSDT", "signal_type": "strong_buy",
               "confidence": 0.85, "source": "ta", "components": '{"rsi": 28}',
               "reasoning": "Oversold", "created_at": "2024-01-01"}
        result = row_to_signal(row)
        assert isinstance(result, Signal)
        assert result.signal_type == SignalType.STRONG_BUY
        assert result.components == {"rsi": 28}
