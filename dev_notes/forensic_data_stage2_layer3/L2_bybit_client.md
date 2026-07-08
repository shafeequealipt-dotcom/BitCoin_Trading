# L2 — Bybit Client Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/client.py`
Measured line count: **227** lines.

WebSocket source: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/websocket.py`

---

## 1. Class & Public Methods

Class: `BybitClient` (client.py:66)

Constructor (client.py:74-88):
```
def __init__(self, settings: Settings, db: DatabaseManager) -> None
```
Stored attrs: `_settings`, `_db`, `_session: HTTP | None`, `_auth: BybitAuth | None`, `_connected: bool`.

Safety assertion (client.py:84-88): if `not settings.bybit.testnet` AND `settings.general.mode == "paper"`, raise `RuntimeError("SAFETY: bybit.testnet is False but mode is 'paper'...")`. Mainnet data is allowed when mode is `shadow` or `live`.

Public methods/properties:
| Member | File:Line | Notes |
|---|---|---|
| `session` (property) | 90-99 | Returns `_session`, raises `RuntimeError` if not connected |
| `is_testnet` (property) | 101-104 | `_settings.bybit.testnet` |
| `is_connected` (property) | 106-109 | `_connected` |
| `connect` | 111-152 | Build pybit `HTTP` session, validate creds (non-fatal in shadow mode) |
| `disconnect` | 154-158 | Clears session |
| `call` | 163-191 | Central RPC dispatcher, decorated `@retry @rate_limit @timed` |

Private: `_handle_response` (193-227) maps retCode -> exception.

---

## 2. pybit Method Mapping

`BybitClient.call(method, **kwargs)` resolves method by name on the pybit `HTTP` session (`func = getattr(session, method, None)`, client.py:183) and dispatches via `asyncio.to_thread(func, **kwargs)` (client.py:190).

All pybit methods used by the codebase, with caller file:line and purpose:

| pybit method | Caller (file:line) | Purpose |
|---|---|---|
| `place_order` | order_service.py:723 | Place new order |
| `amend_order` | order_service.py:895 | Modify open order qty/price |
| `cancel_order` | order_service.py:921-925 | Cancel single order |
| `cancel_all_orders` | order_service.py:953 | Cancel all (optional symbol filter) |
| `get_open_orders` | order_service.py:790, 980, 1069 | Open orders / dedup recovery / single-order lookup |
| `get_order_history` | order_service.py:812, 1010, 1078 | Filled / cancelled history; dedup recovery; single-order lookup |
| `set_leverage` | order_service.py:1052-1057 | Set buy/sell leverage |
| `get_tickers` | order_service.py:556 | Last-price lookup for position-cap calc |
| `get_wallet_balance` | account_service.py (5 sites; via `BybitClient.call`) | Wallet/equity reads |
| `get_positions` | position_service.py | Position queries |
| `get_kline` | market_service.py | OHLC fetch |
| `get_instruments_info` | instrument_service.py | Symbol metadata |

(WebSocket streams handled separately, see section 4.)

Retry policy (single source): `@retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,))` decorates `BybitClient.call` (client.py:160). This applies UNIVERSALLY to every method dispatched through `call`.

Per-call additional retries are layered ON TOP at the service level (see L1 section 5 for the place_order scoped retry).

---

## 3. Rate Limiter (Token Bucket)

Decorator declaration: `@rate_limit(calls_per_second=10.0)` at `BybitClient.call` (client.py:161).

Settings reference: `rate_limit_per_second: int = 10` defined at `src/config/settings.py:54` (in the `BybitConfig` dataclass), but the decorator hardcodes `10.0` — the settings field is NOT wired into the decorator argument. GAP — the value is duplicated rather than read from settings.

Implementation: `src/core/decorators.py:108-167`.

`_TokenBucket` class (decorators.py:106-133):
```python
class _TokenBucket:
    def __init__(self, calls_per_second: float) -> None:
        self.rate = calls_per_second
        self.max_tokens = calls_per_second
        self.tokens = calls_per_second
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1.0
```

Bucket cache (decorators.py:136): `_buckets: dict[str, _TokenBucket]` keyed on `f"{func.__module__}.{func.__qualname__}:{calls_per_second}"`. Each decorated function gets one shared bucket process-wide.

Capacity: `max_tokens = calls_per_second = 10.0`, refill rate 10 tokens/sec. 11th call within a single second waits `(1.0 - tokens) / rate` seconds.

### Rate-limit hits in last 24h

GAP — searched logs `data/logs/workers.*.log` for `10006|RC_RATE_LIMIT|RateLimitError` between 2026-05-01 and 2026-05-02. The only match is a heartbeat line containing "10006" as part of a worker ID, NOT an actual rate-limit event. **0 actual `RateLimitError` events found in the last 24h.**

The token bucket is process-local; rate limiting is enforced client-side BEFORE the request reaches Bybit. No log line is emitted by the bucket itself — only `loguru.debug` from `@retry` if a `BybitAPIError` happens to bubble.

---

## 4. WebSocket

File: `src/trading/websocket.py`. Class: `BybitWebSocket` (websocket.py:17).

Constructor (websocket.py:29-39):
- `_public_ws`, `_private_ws` — `pybit.unified_trading.WebSocket` instances
- `_callbacks: dict[str, list[Callable]]`
- `_running: bool`, `_reconnect_attempts: int = 0`, `_max_reconnect_attempts: int = 10`
- `_lock: asyncio.Lock`

Subscriptions (all via pybit's WebSocket class):
| Method | File:Line | pybit call | Channel |
|---|---|---|---|
| `subscribe_ticker` | 88 | `_public_ws.ticker_stream(symbol, callback)` | linear public |
| `subscribe_kline` | 104 | `_public_ws.kline_stream(interval, symbol, callback)` | linear public |
| `subscribe_orderbook` | 121 | `_public_ws.orderbook_stream(depth, symbol, callback)` | linear public |
| `subscribe_orders` | 138 | `_private_ws.order_stream(callback)` | private |
| `subscribe_positions` | 151 | `_private_ws.position_stream(callback)` | private |

Connection setup:
- `connect_public` (websocket.py:46-63): `WebSocket(testnet=settings.bybit.testnet, channel_type="linear")` (websocket.py:54-57).
- `connect_private` (websocket.py:65-86): `WebSocket(testnet=, channel_type="private", api_key=, api_secret=)` (websocket.py:74-79).

Reconnect (`reconnect`, websocket.py:183-216):
```python
base_delay = self._settings.bybit.ws_reconnect_delay
while self._reconnect_attempts < self._max_reconnect_attempts:
    self._reconnect_attempts += 1
    delay = base_delay * (2 ** (self._reconnect_attempts - 1))
    delay = min(delay, 300)  # cap at 5 minutes
    log.warning("WebSocket reconnect attempt {n}/{max} in {d}s", ...)
    await asyncio.sleep(delay)
    try:
        await self.disconnect()
        await self.connect_public()
        self._reconnect_attempts = 0
        return
    except Exception as e:
        log.error("Reconnect attempt failed: {err}", err=str(e))
raise MarketDataError(f"WebSocket reconnection failed after {self._max_reconnect_attempts} attempts")
```

Heartbeat: GAP — there is no explicit `heartbeat` / `ping` method in `BybitWebSocket`. pybit's `WebSocket` class manages its own ping internally; this codebase has no override. Searched: `grep -n "heartbeat\|ping" src/trading/websocket.py`.

Callback wrapping: `_wrap_callback(stream_type, callback)` (websocket.py:218+) wraps user callbacks with try/except logging.

---

## 5. `@retry` Decorator on Methods

Definition: `src/core/decorators.py:17-99`.

```python
def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0,
          exceptions: tuple[type[BaseException], ...] = (Exception,)) -> ...
```

Async branch (decorators.py:36-68):
- For each attempt 1..max_attempts: `await func(...)`; on caught `exceptions`, if last attempt -> log "Retry exhausted for {func} after {n} attempts" at WARNING and re-raise; else log "Retry {attempt}/{max} for {func}" at DEBUG, `await asyncio.sleep(current_delay)`, `current_delay *= backoff`.

Decorator usage in `src/trading/`:
| File:Line | Decorated method | Args |
|---|---|---|
| client.py:160 | `BybitClient.call` | `max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,)` |
| order_service.py:850 | `OrderService.modify_order` | `max_attempts=2, delay=0.5, exceptions=(BybitAPIError, OSError, RuntimeError)` |
| order_service.py:909 | `OrderService.cancel_order` | `max_attempts=2, delay=0.5` (default exceptions=Exception) |
| order_service.py:938 | `OrderService.cancel_all_orders` | `max_attempts=2, delay=0.5` |
| order_service.py:965 | `OrderService.get_open_orders` | `max_attempts=3, delay=1.0` |
| order_service.py:990 | `OrderService.get_order_history` | `max_attempts=3, delay=1.0` |
| account_service.py:27,76,87,98 | account methods | `max_attempts=3, delay=1.0` |
| position_service.py:52,82,98,225,345,383,405 | position methods | mixes of 2/0.5 and 3/1.0 |
| market_service.py:70,155,175,231,270,300 | market methods | `max_attempts=3, delay=1.0` |
| instrument_service.py:38,80 | instrument methods | 3/1.0 and 2/2.0 |

The exact decorator that matches the prompt's `@retry(max_attempts=3, delay=1.0, backoff=2.0)` is **`BybitClient.call` only** (client.py:160). Service-level retries use shorter `max_attempts=2, delay=0.5` for cancel/amend (idempotent operations).

### 5 Retry Events from Logs

GAP — searched logs `data/logs/workers.*.log` for `Retry exhausted|Retry [0-9]+/`. No matches in last 24h or any recent file. Reason: retries happen at DEBUG level (decorators.py:54-60); workers run at INFO level by default so `Retry {attempt}/{max}` lines are dropped. Only "Retry exhausted" (WARNING) would appear, and there are 0 in the last 24h — implying no retried calls reached final exhaustion.

The `ORDER_RETRY`/`ORDER_RETRY_OK`/`ORDER_RETRY_EXHAUSTED` log lines are emitted by the OrderService scoped retry, NOT by the `@retry` decorator. They were also not observed in the last 24h.

---

## 6. retCode Constants

Defined at `client.py:31-35`:
```python
RC_OK = 0
RC_RATE_LIMIT = 10006
RC_INVALID_API_KEY = 10003
RC_INVALID_SIGN = 10004
RC_DUPLICATE_ORDER_LINK_ID = 110072
```

Full error map: `BYBIT_ERROR_MAP` (client.py:51-63), 11 entries — see L1 section 8.

`_handle_response` (client.py:193-227): if `retCode == RC_OK` return `result`, else look up exception via `BYBIT_ERROR_MAP.get(ret_code, BybitAPIError)` and raise with details `{retCode, retMsg, operation}`.
