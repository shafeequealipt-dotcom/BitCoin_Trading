"""WebSocket connection manager for Bybit mainnet public streams.

Maintains persistent connections, handles auto-reconnect with exponential
backoff, dispatches ticker/kline messages to registered callbacks, and
keeps an in-memory price cache for instant lookups.
"""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("collector.ws")

# Bybit limits ~200 subscriptions per connection
MAX_SUBS_PER_CONNECTION = 200
PING_INTERVAL = 20  # seconds
HEALTH_LOG_INTERVAL = 300  # 5 minutes


class WebSocketManager:
    """Manages WebSocket connections to Bybit's public linear stream.

    Splits ticker and kline subscriptions across separate connections
    to stay within Bybit's subscription limits. Maintains an in-memory
    price cache updated on every tick.

    Args:
        config: Shadow configuration.
    """

    def __init__(self, config: ShadowConfig) -> None:
        self._ws_url = config.bybit.ws_url
        self._ticker_callbacks: list[Callable] = []
        self._kline_callbacks: list[Callable] = []
        self._latest_tickers: dict[str, dict[str, Any]] = {}
        self._ticker_timestamps: dict[str, float] = {}
        self._symbols: list[str] = []

        # Connection state
        self._ticker_ws: ClientConnection | None = None
        self._kline_ws: ClientConnection | None = None
        self._running = False

        # Health metrics
        self._total_messages = 0
        self._ticker_messages = 0
        self._kline_messages = 0
        self._reconnect_count = 0
        self._start_time = 0.0
        self._last_message_time = 0.0

    def set_symbols(self, symbols: list[str]) -> None:
        """Set the list of symbols to subscribe to."""
        self._symbols = symbols

    def on_ticker(self, callback: Callable) -> None:
        """Register a callback for ticker messages."""
        self._ticker_callbacks.append(callback)

    def on_kline(self, callback: Callable) -> None:
        """Register a callback for kline messages."""
        self._kline_callbacks.append(callback)

    def get_latest_price(self, symbol: str) -> float | None:
        """Get the last known price for a symbol from memory.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT").

        Returns:
            Last price as float, or None if no data yet.
        """
        ticker = self._latest_tickers.get(symbol)
        if ticker and "lastPrice" in ticker:
            return float(ticker["lastPrice"])
        return None

    def get_latest_ticker(self, symbol: str) -> dict[str, Any] | None:
        """Get the full latest ticker data for a symbol from memory."""
        return self._latest_tickers.get(symbol)

    def get_ticker_age(self, symbol: str) -> float | None:
        """Get seconds since last tick for a symbol."""
        ts = self._ticker_timestamps.get(symbol)
        if ts is None:
            return None
        return time.time() - ts

    def get_health(self) -> dict[str, Any]:
        """Return health metrics."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "total_messages": self._total_messages,
            "ticker_messages": self._ticker_messages,
            "kline_messages": self._kline_messages,
            "reconnect_count": self._reconnect_count,
            "uptime_seconds": uptime,
            "last_message_age": time.time() - self._last_message_time if self._last_message_time else 0,
            "coins_with_data": len(self._latest_tickers),
        }

    async def run(self) -> None:
        """Main loop — runs ticker and kline connections concurrently."""
        self._running = True
        self._start_time = time.time()

        ticker_topics = [f"tickers.{s}" for s in self._symbols]
        kline_topics = [f"kline.1.{s}" for s in self._symbols]

        log.info(
            "Starting WebSocket streams: {nt} ticker + {nk} kline topics",
            nt=len(ticker_topics),
            nk=len(kline_topics),
        )

        # Run both connections + health logger concurrently
        await asyncio.gather(
            self._run_connection("ticker", ticker_topics, self._handle_ticker_message),
            self._run_connection("kline", kline_topics, self._handle_kline_message),
            self._health_logger(),
        )

    async def disconnect(self) -> None:
        """Signal stop and close connections."""
        self._running = False
        for ws in (self._ticker_ws, self._kline_ws):
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        log.info("WebSocket connections closed")

    # ─── Internal connection loop ───────────────────────────────────────

    async def _run_connection(
        self,
        name: str,
        topics: list[str],
        handler: Callable,
    ) -> None:
        """Run a single WebSocket connection with auto-reconnect.

        Args:
            name: Connection name for logging ("ticker" or "kline").
            topics: List of subscription topics.
            handler: Message handler function.
        """
        backoff = 1

        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=None,  # We handle our own pings
                    close_timeout=5,
                ) as ws:
                    if name == "ticker":
                        self._ticker_ws = ws
                    else:
                        self._kline_ws = ws

                    backoff = 1  # Reset on successful connect
                    log.info("WebSocket [{name}] connected to {url}", name=name, url=self._ws_url)

                    # Subscribe in batches (Bybit accepts arrays)
                    await self._subscribe(ws, name, topics)

                    # Start ping task alongside message reader
                    ping_task = asyncio.create_task(self._ping_loop(ws, name))
                    try:
                        await self._read_loop(ws, name, handler)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except asyncio.CancelledError:
                log.info("WebSocket [{name}] task cancelled", name=name)
                return
            except Exception as e:
                if not self._running:
                    return
                self._reconnect_count += 1
                log.warning(
                    "WebSocket [{name}] disconnected: {err}. Reconnecting in {sec}s...",
                    name=name,
                    err=str(e)[:100],
                    sec=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _subscribe(
        self, ws: ClientConnection, name: str, topics: list[str]
    ) -> None:
        """Send subscription messages to the WebSocket.

        Splits into batches of 10 topics per message to avoid overloading.
        """
        batch_size = 10
        for i in range(0, len(topics), batch_size):
            batch = topics[i : i + batch_size]
            msg = json.dumps({"op": "subscribe", "args": batch})
            await ws.send(msg)
            # Small delay between subscription batches
            await asyncio.sleep(0.1)

        log.info(
            "WebSocket [{name}] subscribed to {n} topics",
            name=name,
            n=len(topics),
        )

    async def _ping_loop(self, ws: ClientConnection, name: str) -> None:
        """Send pings every PING_INTERVAL seconds."""
        while self._running:
            try:
                await asyncio.sleep(PING_INTERVAL)
                await ws.send(json.dumps({"op": "ping"}))
            except asyncio.CancelledError:
                return
            except Exception:
                return  # Connection lost, outer loop will reconnect

    async def _read_loop(
        self, ws: ClientConnection, name: str, handler: Callable
    ) -> None:
        """Read messages from WebSocket and dispatch to handler."""
        async for raw_msg in ws:
            if not self._running:
                return

            self._total_messages += 1
            self._last_message_time = time.time()

            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            # Skip pong responses and subscription confirmations
            if msg.get("op") in ("pong", "subscribe"):
                continue
            if "success" in msg:
                continue

            # Dispatch to handler
            topic = msg.get("topic", "")
            data = msg.get("data")
            if topic and data is not None:
                try:
                    handler(topic, data, msg.get("type", ""))
                except Exception as e:
                    log.warning(
                        "Handler error [{name}] {topic}: {err}",
                        name=name,
                        topic=topic,
                        err=str(e)[:100],
                    )

    # ─── Message handlers ───────────────────────────────────────────────

    def _handle_ticker_message(
        self, topic: str, data: dict[str, Any], msg_type: str
    ) -> None:
        """Process a ticker update — update cache and fire callbacks."""
        # topic format: "tickers.BTCUSDT"
        symbol = topic.split(".", 1)[1] if "." in topic else ""
        if not symbol:
            return

        self._ticker_messages += 1

        # Update in-memory cache (merge delta into existing data)
        existing = self._latest_tickers.get(symbol, {})
        existing.update(data)
        self._latest_tickers[symbol] = existing
        self._ticker_timestamps[symbol] = time.time()

        # Fire registered callbacks
        for cb in self._ticker_callbacks:
            try:
                cb(symbol, existing)
            except Exception:
                pass

    def _handle_kline_message(
        self, topic: str, data: Any, msg_type: str
    ) -> None:
        """Process a kline update — fire callbacks."""
        # topic format: "kline.1.BTCUSDT"
        parts = topic.split(".")
        symbol = parts[2] if len(parts) >= 3 else ""
        if not symbol:
            return

        self._kline_messages += 1

        # data is a list of kline objects
        klines = data if isinstance(data, list) else [data]
        for kline in klines:
            for cb in self._kline_callbacks:
                try:
                    cb(symbol, kline)
                except Exception:
                    pass

    # ─── Health logging ─────────────────────────────────────────────────

    async def _health_logger(self) -> None:
        """Log health summary every 5 minutes."""
        while self._running:
            await asyncio.sleep(HEALTH_LOG_INTERVAL)
            if not self._running:
                return

            health = self.get_health()
            uptime_min = health["uptime_seconds"] / 60
            msg_rate = (
                self._total_messages / health["uptime_seconds"]
                if health["uptime_seconds"] > 0
                else 0
            )

            log.info(
                "WS health: {uptime:.0f}m uptime, {total:,} msgs ({rate:.0f}/s), "
                "{coins} coins, {recon} reconnects",
                uptime=uptime_min,
                total=health["total_messages"],
                rate=msg_rate,
                coins=health["coins_with_data"],
                recon=health["reconnect_count"],
            )
