# P1 Phase 1 — websocket.py anatomy + pybit demo capability

## File scope

`src/trading/websocket.py` — 236 lines. Single class `BybitWebSocket`.

## Class contract

| Aspect | Detail |
|--------|--------|
| Constructor | `__init__(settings: Settings, db: DatabaseManager)` (line 29) |
| State | `_public_ws`, `_private_ws` (both `Any | None`); `_callbacks: dict[str, list[Callable]]`; `_running: bool`; `_reconnect_attempts: int`; `_max_reconnect_attempts = 10`; `_lock: asyncio.Lock` |
| Public connection | `async connect_public()` (line 45) — pybit `WebSocket(testnet=settings.bybit.testnet, channel_type="linear")` |
| Private connection | `async connect_private()` (line 66) — pybit `WebSocket(testnet=bybit.testnet, channel_type="private", api_key=bybit.api_key, api_secret=bybit.api_secret)` |
| Public subscriptions | `subscribe_ticker(symbols, cb)` (line 88), `subscribe_kline(symbol, interval, cb)` (line 104), `subscribe_orderbook(symbol, depth, cb)` (line 121) |
| Private subscriptions | `subscribe_orders(cb)` (line 138) → pybit `order_stream`; `subscribe_positions(cb)` (line 151) → pybit `position_stream` |
| `subscribe_executions` | **DOES NOT EXIST** — would route to pybit `execution_stream(callback)` |
| Lifecycle | `disconnect()` (line 164) — calls pybit `.exit()` on both; `reconnect()` (line 183) — exponential backoff capped at 5 min, max 10 attempts |
| Callback wrapping | `_wrap_callback(stream_type, callback)` (line 217) — try/except, log error, swallow exception (intentional — must not propagate into pybit thread) |

## pybit library — demo support

pybit version: no `__version__` attribute exposed; module at `/home/inshadaliqbal786/.local/lib/python3.10/site-packages/pybit/`.

**Critical finding (resolves P1 plan risk #1):** pybit's `WebSocket(channel_type, **kwargs)` accepts `demo: bool = False` as a kwarg. The `_WebSocketManager.__init__` at `pybit/_websocket_stream.py:33` and the URL-build at `_connect()` lines 135-139 produce:

| `testnet` | `demo` | Subdomain | Final URL (private) |
|-----------|--------|-----------|---------------------|
| False | False | stream | `wss://stream.bybit.com/v5/private` (mainnet live) |
| True | False | stream-testnet | `wss://stream-testnet.bybit.com/v5/private` (testnet) |
| False | **True** | **stream-demo** | **`wss://stream-demo.bybit.com/v5/private`** (demo) |
| True | True | stream-demo-testnet | `wss://stream-demo-testnet.bybit.com/v5/private` (demo testnet) |

URL constants: `pybit.unified_trading.PRIVATE_WSS = 'wss://{SUBDOMAIN}.{DOMAIN}.{TLD}/v5/private'` and `PUBLIC_WSS = 'wss://{SUBDOMAIN}.{DOMAIN}.com/v5/public/{CHANNEL_TYPE}'`. The format-string substitution drives the per-mode URL.

So **wiring P1 does not require subclassing or raw `websockets`**. Three changes to `BybitWebSocket.connect_private()` cover the demo path:
1. Accept an optional `demo: bool = False` parameter (or read from settings).
2. Pass through to pybit constructor.
3. Use `settings.bybit_demo.api_key` / `api_secret` when `demo=True`, NOT `settings.bybit.api_key`.

## pybit private streams (for callback signatures)

From `/home/inshadaliqbal786/.local/lib/python3.10/site-packages/pybit/unified_trading.py:122-185`:

| Method | Topic | Push freq | Bybit doc |
|--------|-------|-----------|-----------|
| `position_stream(callback)` (line 122) | `position` | real-time | https://bybit-exchange.github.io/docs/v5/websocket/private/position |
| `order_stream(callback)` (line 134) | `order` | real-time | https://bybit-exchange.github.io/docs/v5/websocket/private/order |
| `execution_stream(callback)` (line 146) | `execution` | real-time | https://bybit-exchange.github.io/docs/v5/websocket/private/execution |
| `fast_execution_stream(callback)` (line 158) | `execution.fast` | real-time, fewer fields | https://bybit-exchange.github.io/docs/v5/websocket/private/fast-execution |
| `wallet_stream(callback)` (line 175) | `wallet` | real-time | https://bybit-exchange.github.io/docs/v5/websocket/private/wallet |

For P1, **execution + position** are the load-bearing streams:

- **execution**: emits one event per fill with `symbol, side, execPrice, execQty, execFee, closedSize, orderId, orderType, execTime`. `closedSize > 0` indicates a close-side fill (stop-loss / take-profit / manual close hit). This carries authoritative post-fee data needed for TIAS / data lake / strategist.
- **position**: emits when position state changes; `size = 0` means flat. Useful for confirming the position is fully closed (a partial-close execution alone does not flat the position).

`order` stream is supplementary — it tells us the order lifecycle (`New → Filled / Cancelled / Rejected`) but not authoritative PnL.

## Authentication (private streams)

pybit handles HMAC auth internally. The `_V5WebSocketManager.__init__` requires `api_key` + `api_secret` for `channel_type="private"` (raises `UnauthorizedExceptionError` otherwise — `unified_trading.py:113`). The handshake sends an `op: auth` frame within `private_auth_expire` seconds (default 1) of connect. Pybit handles re-auth on reconnect.

For P1 demo wiring, we must use **bybit_demo** credentials (`settings.bybit_demo.api_key`, `settings.bybit_demo.api_secret`) — the bybit_demo cluster does not accept main-account keys.

## Heartbeat / reconnection

| Aspect | Detail |
|--------|--------|
| Ping interval | pybit default 20s (`ping_interval=20`) |
| Ping timeout | pybit default 10s (`ping_timeout=10`) |
| Custom ping payload | `{"op": "ping"}` (`_websocket_stream.py:151`) |
| `restart_on_error` | pybit default `True` — pybit auto-reconnects on transport errors and resubscribes |
| Project-side reconnect | `BybitWebSocket.reconnect()` (line 183) — only handles `connect_public`, NOT `connect_private`. P1 must add private-side reconnect handling. |
| Resubscribe on reconnect | pybit's `_connect()` calls `resubscribe_to_topics()` (uses `self.subscriptions` registry) so subscriptions survive transport reconnects without project intervention. |

## Current consumers

`BybitWebSocket` is instantiated exactly once at `src/workers/manager.py:106`:
```python
ws = BybitWebSocket(settings, db)
self._services["ws"] = ws
```

Only consumer: `src/workers/price_worker.py:144-147` calls `await ws.connect_public()` and `ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)`.

`connect_private`, `subscribe_orders`, `subscribe_positions` have **zero call sites** across the codebase (verified by grep). Dead code — exactly as the audit noted.

## What P1 must add (anatomy preview, NOT implementation)

| Item | Where | Notes |
|------|-------|-------|
| Accept `demo: bool` in `connect_private` | `websocket.py:66` (extend signature) | Routes to pybit `demo=True` |
| Resolve credentials from `settings.bybit_demo` when `demo=True` | `websocket.py:73-80` | Use bd creds, not bybit creds |
| New method `subscribe_executions(callback)` | `websocket.py:163` (after `subscribe_positions`) | Routes to pybit `execution_stream` |
| Private-side reconnect path | `websocket.py:183-215` (extend) | Handle `_private_ws` separately from `_public_ws` |
| New consumer class | NEW `src/bybit_demo/bybit_demo_websocket_subscriber.py` | Constructs `BybitWebSocket(settings, db)`, calls `connect_private(demo=True)`, subscribes to position + execution + order, owns the message handler that calls `coordinator.on_trade_closed` |
| Boot wiring | `manager.py:300-378` (after `validate_boot`) | Construct subscriber, attach to coordinator, start subscription |

These are documented for Phase 2 discussion. No code change in Phase 1.
