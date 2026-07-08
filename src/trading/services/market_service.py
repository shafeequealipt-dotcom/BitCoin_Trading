"""Market data service: prices, klines, orderbook, tickers.

Fetches data from Bybit, maps to existing dataclasses, and persists to database.
"""

import time
from datetime import datetime, timezone

from src.core.decorators import retry, timed
from src.core.logging import get_logger
from src.core.types import OHLCV, Ticker, TimeFrame
from src.core.utils import now_utc, pct_change, timestamp_to_datetime
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.trading.client import BybitClient

log = get_logger("trading")


class MarketService:
    """Service for market data retrieval and persistence.

    Args:
        client: Connected BybitClient.
        db: Database manager.
        kline_save_chunk_size: Per-chunk row count forwarded to the
            internal :class:`MarketRepository` for chunked kline saves
            (Phase 1 D-3 fix). Defaults preserve backward compatibility
            for legacy construction sites that have no ``Settings``
            reference.
    """

    def __init__(
        self,
        client: BybitClient,
        db: DatabaseManager,
        kline_save_chunk_size: int = 500,
    ) -> None:
        self._client = client
        self._db = db
        self._market_repo = MarketRepository(
            db, kline_save_chunk_size=kline_save_chunk_size
        )
        # In-memory ticker cache with 5-second TTL (reduces duplicate API calls)
        self._ticker_cache: dict[str, tuple[float, Ticker]] = {}
        self._CACHE_TTL = 5.0

    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for a symbol.

        Uses a 5-second in-memory cache to prevent duplicate API calls
        when multiple workers request the same ticker simultaneously.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").

        Returns:
            Ticker with current price, bid/ask, 24h stats.
        """
        cached = self._ticker_cache.get(symbol)
        if cached:
            cache_time, ticker = cached
            if time.time() - cache_time < self._CACHE_TTL:
                return ticker

        ticker = await self._fetch_ticker(symbol)
        self._ticker_cache[symbol] = (time.time(), ticker)
        return ticker

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def _fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch ticker from exchange API and save to DB."""
        result = await self._client.call(
            "get_tickers",
            category="linear",
            symbol=symbol,
        )

        items = result.get("list", [])
        if not items:
            from src.core.exceptions import MarketDataError
            raise MarketDataError(
                f"No ticker data for {symbol}",
                details={"symbol": symbol},
            )

        data = items[0]
        ticker = Ticker(
            symbol=data["symbol"],
            last_price=float(data.get("lastPrice", "0")),
            bid=float(data.get("bid1Price", "0")),
            ask=float(data.get("ask1Price", "0")),
            high_24h=float(data.get("highPrice24h", "0")),
            low_24h=float(data.get("lowPrice24h", "0")),
            volume_24h=float(data.get("volume24h", "0")),
            change_24h_pct=float(data.get("price24hPcnt", "0")) * 100,
            timestamp=now_utc(),
        )

        await self._market_repo.save_ticker(ticker)
        log.debug(
            "Ticker {s}: {p:.2f} ({c:+.2f}%)",
            s=symbol,
            p=ticker.last_price,
            c=ticker.change_24h_pct,
        )
        return ticker

    async def get_all_linear_tickers(self) -> list[Ticker]:
        """Bulk-fetch ALL USDT perp tickers in a single API call.

        Used by the scanner for coin discovery. Returns ~300 tickers from
        Bybit's ``/v5/market/tickers?category=linear`` endpoint. Results are
        cached for 30 seconds to prevent redundant calls within the same cycle.
        """
        # 30-second cache
        cache_key = "_all_linear"
        cached = self._ticker_cache.get(cache_key)
        if cached:
            cache_time, tickers = cached
            if time.time() - cache_time < 30.0:
                return tickers

        result = await self._client.call("get_tickers", category="linear")
        items = result.get("list", [])

        tickers = []
        for data in items:
            symbol = data.get("symbol", "")
            # Only USDT-quoted linear perps
            if not symbol.endswith("USDT"):
                continue
            try:
                ticker = Ticker(
                    symbol=symbol,
                    last_price=float(data.get("lastPrice", "0")),
                    bid=float(data.get("bid1Price", "0")),
                    ask=float(data.get("ask1Price", "0")),
                    high_24h=float(data.get("highPrice24h", "0")),
                    low_24h=float(data.get("lowPrice24h", "0")),
                    volume_24h=float(data.get("turnover24h", "0")),
                    change_24h_pct=float(data.get("price24hPcnt", "0")) * 100,
                    timestamp=now_utc(),
                )
                if ticker.last_price > 0:
                    tickers.append(ticker)
            except (ValueError, TypeError):
                continue

        self._ticker_cache[cache_key] = (time.time(), tickers)
        log.info("Bulk ticker fetch: {n} USDT perps", n=len(tickers))
        return tickers

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        """Get tickers for multiple symbols.

        Args:
            symbols: List of trading pairs. Defaults to configured symbols.

        Returns:
            List of Ticker dataclasses.
        """
        if symbols is None:
            symbols = self._client._settings.bybit.default_symbols

        tickers = []
        for symbol in symbols:
            ticker = await self.get_ticker(symbol)
            tickers.append(ticker)
        return tickers

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_klines(
        self,
        symbol: str,
        interval: TimeFrame,
        limit: int = 200,
    ) -> list[OHLCV]:
        """Fetch kline (candlestick) data from Bybit.

        Maps to OHLCV dataclasses and saves to the database.

        Args:
            symbol: Trading pair.
            interval: Candlestick timeframe.
            limit: Number of candles to fetch (max 200).

        Returns:
            List of OHLCV sorted by timestamp ascending.
        """
        result = await self._client.call(
            "get_kline",
            category="linear",
            symbol=symbol,
            interval=interval.value,
            limit=limit,
        )

        raw_list = result.get("list", [])
        klines = []
        for item in raw_list:
            # Bybit returns [timestamp_ms, open, high, low, close, volume, turnover]
            klines.append(OHLCV(
                symbol=symbol,
                timeframe=interval,
                timestamp=timestamp_to_datetime(int(item[0])),
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5]),
                turnover=float(item[6]) if len(item) > 6 else 0.0,
            ))

        # Bybit returns newest first; reverse to chronological order
        klines.reverse()

        await self._market_repo.save_klines(klines)
        log.debug(
            "Fetched {n} klines for {s} @ {tf}",
            n=len(klines),
            s=symbol,
            tf=interval.value,
        )
        return klines

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_orderbook(self, symbol: str, depth: int = 50) -> dict:
        """Fetch the orderbook for a symbol.

        Saves a snapshot to the database and returns structured data.

        Args:
            symbol: Trading pair.
            depth: Orderbook depth (25 or 50).

        Returns:
            Dict with "bids", "asks" (each list of [price, qty]),
            "symbol", and "timestamp".
        """
        result = await self._client.call(
            "get_orderbook",
            category="linear",
            symbol=symbol,
        )

        bids = [[float(b[0]), float(b[1])] for b in result.get("b", [])][:depth]
        asks = [[float(a[0]), float(a[1])] for a in result.get("a", [])][:depth]

        await self._market_repo.save_orderbook(symbol, bids, asks)

        log.debug(
            "Orderbook {s}: {nb} bids, {na} asks",
            s=symbol,
            nb=len(bids),
            na=len(asks),
        )
        return {
            "symbol": symbol,
            "bids": bids,
            "asks": asks,
            "timestamp": now_utc().isoformat(),
        }

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Fetch recent public trades for a symbol.

        Args:
            symbol: Trading pair.
            limit: Number of trades to fetch.

        Returns:
            List of trade dicts with price, qty, side, time.
        """
        result = await self._client.call(
            "get_public_trade_history",
            category="linear",
            symbol=symbol,
            limit=limit,
        )

        trades = []
        for t in result.get("list", []):
            trades.append({
                "price": float(t.get("price", "0")),
                "qty": float(t.get("size", "0")),
                "side": t.get("side", ""),
                "time": t.get("time", ""),
                "is_block_trade": t.get("isBlockTrade", False),
            })
        return trades

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_24h_stats(self, symbol: str) -> dict:
        """Get 24-hour statistics for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            Dict with high, low, volume, turnover, change percentage.
        """
        ticker = await self.get_ticker(symbol)
        return {
            "symbol": symbol,
            "high_24h": ticker.high_24h,
            "low_24h": ticker.low_24h,
            "volume_24h": ticker.volume_24h,
            "change_24h_pct": ticker.change_24h_pct,
            "last_price": ticker.last_price,
            "bid": ticker.bid,
            "ask": ticker.ask,
        }
