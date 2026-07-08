"""Tests for shared types: enums and dataclasses."""

from datetime import datetime, timezone

from src.core.types import (
    OHLCV,
    AccountInfo,
    AlertLevel,
    BrainDecision,
    FearGreedData,
    FundingRate,
    NewsArticle,
    Order,
    OrderStatus,
    OrderType,
    Position,
    RedditPost,
    SentimentLevel,
    Side,
    Signal,
    SignalType,
    Ticker,
    TimeFrame,
    TradeRecord,
    TradingMode,
    WorkerStatus,
)


class TestEnums:
    def test_side_values(self):
        assert Side.BUY == "Buy"
        assert Side.SELL == "Sell"

    def test_order_type_values(self):
        assert OrderType.MARKET == "Market"
        assert OrderType.LIMIT == "Limit"
        assert OrderType.STOP_MARKET == "StopMarket"

    def test_order_status_values(self):
        assert OrderStatus.NEW == "New"
        assert OrderStatus.FILLED == "Filled"
        assert OrderStatus.CANCELLED == "Cancelled"

    def test_timeframe_values(self):
        assert TimeFrame.M1 == "1"
        assert TimeFrame.H1 == "60"
        assert TimeFrame.D1 == "D"

    def test_signal_type_values(self):
        assert SignalType.STRONG_BUY == "strong_buy"
        assert SignalType.NEUTRAL == "neutral"

    def test_sentiment_level_values(self):
        assert SentimentLevel.VERY_BULLISH == "very_bullish"
        assert SentimentLevel.BEARISH == "bearish"

    def test_trading_mode_values(self):
        assert TradingMode.PAPER == "paper"
        assert TradingMode.LIVE == "live"

    def test_worker_status_values(self):
        assert WorkerStatus.RUNNING == "running"
        assert WorkerStatus.ERROR == "error"

    def test_alert_level_values(self):
        assert AlertLevel.INFO == "info"
        assert AlertLevel.CRITICAL == "critical"

    def test_enums_are_strings(self):
        assert isinstance(Side.BUY, str)
        assert isinstance(OrderType.MARKET, str)
        assert isinstance(TimeFrame.H4, str)

    def test_enum_json_serializable(self):
        import json
        data = {"side": Side.BUY, "type": OrderType.LIMIT}
        result = json.dumps(data)
        assert '"Buy"' in result
        assert '"Limit"' in result


class TestOHLCV:
    def test_create(self):
        ts = datetime.now(timezone.utc)
        bar = OHLCV(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=ts,
            open=50000.0,
            high=51000.0,
            low=49000.0,
            close=50500.0,
            volume=1234.5,
        )
        assert bar.symbol == "BTCUSDT"
        assert bar.turnover == 0.0

    def test_to_dict_from_dict_roundtrip(self):
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bar = OHLCV(
            symbol="ETHUSDT",
            timeframe=TimeFrame.M5,
            timestamp=ts,
            open=2000.0,
            high=2100.0,
            low=1900.0,
            close=2050.0,
            volume=500.0,
            turnover=1000000.0,
        )
        d = bar.to_dict()
        assert d["symbol"] == "ETHUSDT"
        assert d["timeframe"] == "5"
        assert isinstance(d["timestamp"], str)

        restored = OHLCV.from_dict(d)
        assert restored.symbol == bar.symbol
        assert restored.close == bar.close
        assert restored.volume == bar.volume


class TestOrder:
    def test_create_with_defaults(self):
        order = Order(
            order_id="ord_123",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            price=50000.0,
            qty=0.01,
        )
        assert order.status == OrderStatus.NEW
        assert order.filled_qty == 0.0
        assert order.stop_loss is None

    def test_roundtrip(self):
        order = Order(
            order_id="ord_456",
            symbol="ETHUSDT",
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            price=2000.0,
            qty=1.0,
            stop_loss=1900.0,
        )
        d = order.to_dict()
        restored = Order.from_dict(d)
        assert restored.order_id == "ord_456"
        assert restored.side == Side.SELL
        assert restored.stop_loss == 1900.0


class TestPosition:
    def test_create(self):
        pos = Position(
            symbol="SOLUSDT",
            side=Side.BUY,
            size=10.0,
            entry_price=100.0,
            mark_price=105.0,
            leverage=2,
        )
        assert pos.unrealized_pnl == 0.0
        assert pos.leverage == 2


class TestSignal:
    def test_create(self):
        sig = Signal(
            symbol="BTCUSDT",
            signal_type=SignalType.STRONG_BUY,
            confidence=0.85,
            source="technical",
            reasoning="RSI oversold",
        )
        assert sig.confidence == 0.85
        assert sig.components == {}


class TestTradeRecord:
    def test_roundtrip(self):
        tr = TradeRecord(
            trade_id="tr_001",
            symbol="BTCUSDT",
            side=Side.BUY,
            entry_price=50000.0,
            exit_price=52000.0,
            qty=0.1,
            pnl=200.0,
            pnl_pct=4.0,
            strategy="momentum",
        )
        d = tr.to_dict()
        restored = TradeRecord.from_dict(d)
        assert restored.pnl == 200.0
        assert restored.strategy == "momentum"


class TestOtherDataclasses:
    def test_account_info(self):
        ai = AccountInfo(
            total_equity=10000.0,
            available_balance=8000.0,
            used_margin=2000.0,
            unrealized_pnl=100.0,
        )
        assert ai.margin_level_pct == 0.0

    def test_fear_greed(self):
        fg = FearGreedData(value=25, classification="Extreme Fear")
        d = fg.to_dict()
        assert d["value"] == 25

    def test_funding_rate(self):
        ts = datetime.now(timezone.utc)
        fr = FundingRate(
            symbol="BTCUSDT",
            funding_rate=0.0001,
            next_funding_time=ts,
        )
        assert fr.predicted_rate == 0.0

    def test_brain_decision(self):
        bd = BrainDecision(
            id="bd_001",
            action="buy",
            symbol="ETHUSDT",
            confidence=0.8,
            reasoning="bullish divergence",
        )
        d = bd.to_dict()
        restored = BrainDecision.from_dict(d)
        assert restored.action == "buy"

    def test_news_article(self):
        na = NewsArticle(
            id="n_001",
            headline="BTC surges",
            source="CoinDesk",
            url="https://example.com",
            summary="Bitcoin price rallied.",
            sentiment_score=0.7,
            symbols=["BTCUSDT"],
        )
        assert na.symbols == ["BTCUSDT"]
        d = na.to_dict()
        assert d["sentiment_score"] == 0.7

    def test_reddit_post(self):
        rp = RedditPost(
            id="r_001",
            subreddit="bitcoin",
            title="Moon soon",
            score=500,
            num_comments=100,
            upvote_ratio=0.95,
            sentiment_score=0.5,
            symbols_mentioned=["BTCUSDT"],
        )
        d = rp.to_dict()
        assert d["score"] == 500

    def test_ticker(self):
        t = Ticker(
            symbol="BTCUSDT",
            last_price=50000.0,
            bid=49999.0,
            ask=50001.0,
            high_24h=52000.0,
            low_24h=48000.0,
            volume_24h=5000.0,
            change_24h_pct=2.5,
        )
        assert t.change_24h_pct == 2.5
