"""Bybit WebSocket connection manager for real-time data streaming.

Manages public (tickers, klines, orderbook) and private (orders, positions)
WebSocket connections with auto-reconnect and heartbeat support.
"""

import asyncio
from typing import Any, Callable

from src.config.settings import Settings
from src.core.exceptions import MarketDataError
from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("trading")


class BybitWebSocket:
    """WebSocket manager for Bybit real-time streams.

    Uses pybit's WebSocket class for connection management.
    Adds monitoring, callback routing, and error propagation.

    Args:
        settings: Application settings.
        db: Database manager for persistence.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self._settings = settings
        self._db = db
        self._public_ws: Any | None = None
        self._private_ws: Any | None = None
        self._callbacks: dict[str, list[Callable]] = {}
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Whether the WebSocket connections are active."""
        return self._running

    async def connect_public(self) -> None:
        """Connect to the public WebSocket stream (tickers, klines, orderbook).

        Uses pybit.unified_trading.WebSocket internally.
        """
        from pybit.unified_trading import WebSocket

        try:
            self._public_ws = WebSocket(
                testnet=self._settings.bybit.testnet,
                channel_type="linear",
            )
            self._running = True
            self._reconnect_attempts = 0
            log.info("Public WebSocket connected")
        except Exception as e:
            raise MarketDataError(
                f"Failed to connect public WebSocket: {e}",
                details={"error": str(e)},
            )

    async def connect_private(self, *, demo: bool = False) -> None:
        """Connect to the private WebSocket stream (orders, executions, positions).

        Requires API key and secret for authentication. When ``demo=True``,
        the connection targets Bybit's demo cluster
        (``wss://stream-demo.bybit.com/v5/private``) using
        ``settings.bybit_demo.api_key`` / ``api_secret`` — these are distinct
        from the live Bybit credentials and the demo cluster does not accept
        live keys. When ``demo=False`` (default), live or testnet is selected
        based on ``settings.bybit.testnet`` using ``settings.bybit.api_key``.

        Args:
            demo: When True, connect to Bybit demo cluster using bybit_demo
                  credentials. Default False uses live/testnet bybit creds.

        Raises:
            MarketDataError: If pybit's WebSocket constructor fails or the
                target credential set is not configured.
        """
        from pybit.unified_trading import WebSocket

        if demo:
            bd = getattr(self._settings, "bybit_demo", None)
            if bd is None:
                raise MarketDataError(
                    "connect_private(demo=True) requires settings.bybit_demo",
                    details={"demo": True},
                )
            api_key = bd.api_key
            api_secret = bd.api_secret
            # Bybit demo is the mainnet variant (api-demo / stream-demo).
            # The audit's planning risk #1 — pybit URL support for demo —
            # is resolved by passing demo=True; pybit then builds the
            # stream-demo subdomain natively (see
            # pybit/_websocket_stream.py:135-139).
            testnet = False
            cluster = "stream-demo"
        else:
            bybit = self._settings.bybit
            api_key = bybit.api_key
            api_secret = bybit.api_secret
            testnet = bybit.testnet
            cluster = "stream-testnet" if testnet else "stream"

        if not api_key or not api_secret:
            raise MarketDataError(
                "Private WebSocket requires api_key and api_secret",
                details={"demo": demo, "cluster": cluster},
            )

        try:
            self._private_ws = WebSocket(
                testnet=testnet,
                channel_type="private",
                api_key=api_key,
                api_secret=api_secret,
                demo=demo,
            )
            log.info(
                "Private WebSocket connected | cluster={c} demo={d}",
                c=cluster, d=demo,
            )
        except Exception as e:
            raise MarketDataError(
                f"Failed to connect private WebSocket: {e}",
                details={"error": str(e), "demo": demo, "cluster": cluster},
            )

    def subscribe_ticker(self, symbols: list[str], callback: Callable) -> None:
        """Subscribe to real-time ticker updates for given symbols.

        Args:
            symbols: List of trading pairs.
            callback: Function called with ticker data on each update.
        """
        if self._public_ws is None:
            raise MarketDataError("Public WebSocket not connected")
        for symbol in symbols:
            self._public_ws.ticker_stream(
                symbol=symbol,
                callback=self._wrap_callback("ticker", callback),
            )
            log.debug("Subscribed to ticker: {s}", s=symbol)

    def subscribe_kline(self, symbol: str, interval: int, callback: Callable) -> None:
        """Subscribe to real-time kline (candlestick) updates.

        Args:
            symbol: Trading pair.
            interval: Kline interval in minutes (1, 5, 15, etc.).
            callback: Function called with kline data on each update.
        """
        if self._public_ws is None:
            raise MarketDataError("Public WebSocket not connected")
        self._public_ws.kline_stream(
            interval=interval,
            symbol=symbol,
            callback=self._wrap_callback("kline", callback),
        )
        log.debug("Subscribed to kline: {s} @ {i}m", s=symbol, i=interval)

    def subscribe_orderbook(self, symbol: str, depth: int, callback: Callable) -> None:
        """Subscribe to real-time orderbook updates.

        Args:
            symbol: Trading pair.
            depth: Orderbook depth (25 or 50).
            callback: Function called with orderbook data on each update.
        """
        if self._public_ws is None:
            raise MarketDataError("Public WebSocket not connected")
        self._public_ws.orderbook_stream(
            depth=depth,
            symbol=symbol,
            callback=self._wrap_callback("orderbook", callback),
        )
        log.debug("Subscribed to orderbook: {s} depth={d}", s=symbol, d=depth)

    def subscribe_orders(self, callback: Callable) -> None:
        """Subscribe to private order execution updates.

        Args:
            callback: Function called with order data on each update.
        """
        if self._private_ws is None:
            raise MarketDataError("Private WebSocket not connected")
        self._private_ws.order_stream(
            callback=self._wrap_callback("order", callback),
        )
        log.debug("Subscribed to order updates")

    def subscribe_positions(self, callback: Callable) -> None:
        """Subscribe to private position updates.

        Args:
            callback: Function called with position data on each update.
        """
        if self._private_ws is None:
            raise MarketDataError("Private WebSocket not connected")
        self._private_ws.position_stream(
            callback=self._wrap_callback("position", callback),
        )
        log.debug("Subscribed to position updates")

    def subscribe_executions(self, callback: Callable) -> None:
        """Subscribe to private execution (fill) events.

        Routes to pybit's ``execution_stream``. Each event carries
        ``execPrice``, ``execQty``, ``execFee``, ``closedSize``,
        ``orderId``, ``symbol``, ``side`` and ``execTime`` — the
        authoritative post-fee data the watchdog's REST get_last_close
        path races for. ``closedSize > 0`` indicates a close-side fill
        (stop-loss / take-profit / manual close hit).

        Args:
            callback: Function called with execution data on each fill event.
        """
        if self._private_ws is None:
            raise MarketDataError("Private WebSocket not connected")
        self._private_ws.execution_stream(
            callback=self._wrap_callback("execution", callback),
        )
        log.debug("Subscribed to execution updates")

    async def disconnect(self) -> None:
        """Close all WebSocket connections gracefully."""
        self._running = False
        if self._public_ws is not None:
            try:
                self._public_ws.exit()
            except Exception as e:
                log.warning("Error closing public WS: {err}", err=str(e))
            self._public_ws = None

        if self._private_ws is not None:
            try:
                self._private_ws.exit()
            except Exception as e:
                log.warning("Error closing private WS: {err}", err=str(e))
            self._private_ws = None

        log.info("WebSocket connections closed")

    async def reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff.

        Raises:
            MarketDataError: If max reconnection attempts exceeded.
        """
        base_delay = self._settings.bybit.ws_reconnect_delay

        while self._reconnect_attempts < self._max_reconnect_attempts:
            self._reconnect_attempts += 1
            delay = base_delay * (2 ** (self._reconnect_attempts - 1))
            delay = min(delay, 300)  # Cap at 5 minutes

            log.warning(
                "WebSocket reconnect attempt {n}/{max} in {d}s",
                n=self._reconnect_attempts,
                max=self._max_reconnect_attempts,
                d=delay,
            )
            await asyncio.sleep(delay)

            try:
                await self.disconnect()
                await self.connect_public()
                self._reconnect_attempts = 0
                log.info("WebSocket reconnected successfully")
                return
            except Exception as e:
                log.error("Reconnect attempt failed: {err}", err=str(e))

        raise MarketDataError(
            f"WebSocket reconnection failed after {self._max_reconnect_attempts} attempts"
        )

    def _wrap_callback(self, stream_type: str, callback: Callable) -> Callable:
        """Wrap a user callback with error handling and logging.

        Args:
            stream_type: Type of stream for logging (e.g. "ticker").
            callback: Original user callback.

        Returns:
            Wrapped callback function.
        """
        def wrapped(message: Any) -> None:
            try:
                callback(message)
            except Exception as e:
                log.error(
                    "Error in {type} callback: {err}",
                    type=stream_type,
                    err=str(e),
                )
        return wrapped
