# A1 — External APIs Inventory

**Collection started (UTC):** 2026-04-27T22:57:39Z
**Collector:** Module A.1 (external API inventory)
**Scope:** Verbatim inventory of every external service the Layer 1 → Stage 2 pipeline talks to, with file:line evidence and live log samples.

> Hard rules followed: verbatim over paraphrase; measurements over estimates; file:line evidence; document gaps explicitly. **No fix proposals.**

---

## Live log file in scope

- `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
  - size: 845183 bytes, line count: 4701 (read at 2026-04-27T22:57Z)
  - earliest line timestamp observed: `2026-04-27 22:06:01`
  - latest line timestamp observed: `2026-04-27 22:56:54`

The "last 24h" window for grep is therefore the live-process window from
`22:05` to `22:56` UTC plus rotated files
(`workers.2026-04-27_01-31-00_169356.log` and earlier rotations are present
in `data/logs/` but not re-read here unless cited).

---

## A.1.1 — Bybit REST API

### Base URL / version

`src/config/settings.py:61-66`:
```
@property
def base_url(self) -> str:
    """REST API base URL based on testnet flag."""
    if self.testnet:
        return "https://api-testnet.bybit.com"
    return "https://api.bybit.com"
```

`config.toml:20-22`:
```
[bybit]
# Bybit mainnet for REAL market data. Orders routed via Transformer to Shadow (paper).
testnet = false
```

The active base URL at run time is therefore `https://api.bybit.com`.
Version path is **V5 unified** (the project routes everything through
`pybit.unified_trading.HTTP`; see Auth below).

### Authentication

- Driver: `pybit.unified_trading.HTTP` (third-party SDK), wrapped by
  `src/trading/client.py`.
- Credentials sourced from environment (`/home/inshadaliqbal786/trading-intelligence-mcp/.env`):
  - `BYBIT_API_KEY=<REDACTED>`
  - `BYBIT_API_SECRET=<REDACTED>`
- Validation entry point: `src/trading/client.py:122-129`:
  ```
  self._auth = BybitAuth(bybit.api_key, bybit.api_secret)

  self._session = HTTP(
      testnet=bybit.testnet,
      api_key=bybit.api_key,
      api_secret=bybit.api_secret,
      recv_window=bybit.recv_window,
  )
  ```
- `recv_window` = 5000 ms (`config.toml:38` — `recv_window = 5000`).
- Validation in `BybitAuth.validate_credentials` is called from
  `src/trading/client.py:138`. In `shadow` mode credential validation
  failures are downgraded to a WARNING and the client continues
  (file:line `src/trading/client.py:140-146`).

### Rate-limit configuration

- `config.toml:31-32`:
  ```
  # Rate limit: max requests per second to Bybit REST API
  rate_limit_per_second = 10
  ```
- Decorator-enforced cap on every REST call, set at the wrapper:
  `src/trading/client.py:160-163`:
  ```
  @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,))
  @rate_limit(calls_per_second=10.0)
  @timed
  async def call(self, method: str, **kwargs: Any) -> dict[str, Any]:
  ```
- Token bucket implementation: `src/core/decorators.py:105-129` (one
  bucket per `(func.__module__.__qualname__, calls_per_second)` —
  see `bucket_key` at `src/core/decorators.py:147`).
- Retry: `max_attempts=3`, base `delay=1.0s`, `backoff=2.0` (so 1s,
  2s, 4s) restricted to `BybitAPIError` (subclasses of
  `BybitAPIError` in `src/core/exceptions.py`). Decorator body:
  `src/core/decorators.py:35-66`.

### Error / retCode mapping

`src/trading/client.py:30-63`:
```
RC_OK = 0
RC_RATE_LIMIT = 10006
RC_INVALID_API_KEY = 10003
RC_INVALID_SIGN = 10004
RC_DUPLICATE_ORDER_LINK_ID = 110072
...
BYBIT_ERROR_MAP: dict[int, type[Exception]] = {
    10003: AuthenticationError,         # Invalid API key
    10004: AuthenticationError,         # Invalid signature
    10006: RateLimitError,              # Rate limited
    110001: InvalidOrderError,          # Order not found
    110003: InvalidOrderError,          # Quantity not valid
    110007: PositionError,              # Position not exists
    110012: InsufficientBalanceError,   # Insufficient balance for order
    110043: InsufficientBalanceError,   # Insufficient available balance
    110044: InvalidOrderError,          # Insufficient balance after SL
    110045: InvalidOrderError,          # Leverage not modified
    110072: DuplicateOrderLinkIdError,  # OrderLinkID is duplicate (idempotency hit)
}
```

### Endpoints (pybit method names) and call sites

The wrapper accepts a string `method` that maps to a pybit `HTTP`
method; the underlying HTTP path is therefore implicit in pybit's V5
mapping. Below are all call sites discovered in the Layer-1→Stage-2
scope (and immediately adjacent services) with file:line.

| pybit method | HTTP path (V5) | Call site (file:line) | Retry / rate-limit | Purpose |
| --- | --- | --- | --- | --- |
| `get_tickers` | `GET /v5/market/tickers` | `src/trading/services/market_service.py:74-78` (`_fetch_ticker`) | `@retry(3, 1.0)` + `@rate_limit(10/s)` (via `client.call`) | Single-symbol ticker (used by `MarketService.get_ticker`) |
| `get_tickers` | `GET /v5/market/tickers` | `src/trading/services/market_service.py:125` (`get_all_linear_tickers`) | same | Bulk pull of all linear tickers (~543 USDT perps observed) |
| `get_tickers` | `GET /v5/market/tickers` | `src/intelligence/altdata/funding_rates.py:48-50` (`fetch_current_rates`) | `@retry(3, 1.0)` on tracker + `@rate_limit(10/s)` on client | Per-symbol `fundingRate`, `nextFundingTime` |
| `get_tickers` | `GET /v5/market/tickers` | `src/brain/brain_v2.py:442-444` (Brain v2 last-price lookup before order) | client-level | Last price for SL/TP sizing |
| `get_tickers` | `GET /v5/market/tickers` | `src/strategies/scanner.py:392` (`market_service.get_tickers`) | client-level | Strategy scanner price lookup |
| `get_tickers` | `GET /v5/market/tickers` | `src/trading/services/order_service.py:556` (mid-order price refresh) | client-level | Refresh price right before placement |
| `get_kline` | `GET /v5/market/kline` | `src/trading/services/market_service.py:195-201` (`get_klines`) | `@retry(3, 1.0)` + client-level | Historical OHLCV; per-symbol per-timeframe |
| `get_kline` (called via `market_service.get_klines`) | same | `src/workers/kline_worker.py:200` | inherits client-level | KlineWorker's only fetch path; M5/H1/H4/D1 |
| `get_orderbook` | `GET /v5/market/orderbook` | `src/trading/services/market_service.py:246-250` | client-level | Orderbook depth (default 50) |
| `get_public_trade_history` | `GET /v5/market/recent-trade` | `src/trading/services/market_service.py:282-286` | client-level | Recent trades |
| `get_open_interest` | `GET /v5/market/open-interest` | `src/intelligence/altdata/open_interest.py:43-49` | `@retry(3, 1.0)` + client-level | OI history (`intervalTime=1h`, `limit=2`) |
| `get_instruments_info` | `GET /v5/market/instruments-info` | `src/trading/services/instrument_service.py:54-58` (single-symbol) | client-level | Instrument tick / lot size |
| `get_instruments_info` | same | `src/trading/services/instrument_service.py:88-91` (bulk) | client-level | Bulk instrument refresh |
| `get_wallet_balance` | `GET /v5/account/wallet-balance` | `src/trading/services/account_service.py:37-40` | `@retry(3, 1.0)` + client-level | UNIFIED account balance |
| `get_positions` | `GET /v5/position/list` | `src/trading/services/position_service.py:67` | `@retry(3, 1.0)` + client-level | Open positions with `settleCoin=USDT` |
| `place_order` | `POST /v5/order/create` | `src/trading/services/position_service.py:160` (close), `:271` (reduce) | `@retry(2, 0.5)` (pos service) + client-level | Close / reduce existing positions (`reduceOnly=True`) |
| `place_order` | same | `src/trading/services/order_service.py:723` (entry path), retry helper at `_place_order_with_retry` lines 720-771 | application-level retry up to `_ORDER_PLACE_MAX_ATTEMPTS` + client-level | Entry orders (with `orderLinkId` idempotency) |
| `set_leverage` | `POST /v5/position/set-leverage` | `src/trading/services/position_service.py:368` (and `order_service.py:1052`) | `@retry(2, 0.5)` + client-level | Set per-symbol leverage |
| `set_trading_stop` | `POST /v5/position/trading-stop` | `src/trading/services/position_service.py:395` (SL), `:417` (TP) | `@retry(2, 0.5)` + client-level | Server-side SL / TP |
| `amend_order` | `POST /v5/order/amend` | `src/trading/services/order_service.py:895` | client-level | Modify existing order |
| `cancel_order` | `POST /v5/order/cancel` | `src/trading/services/order_service.py:921` | `@retry(2, 0.5)` + client-level | Cancel single order |
| `cancel_all_orders` | `POST /v5/order/cancel-all` | `src/trading/services/order_service.py:953` | `@retry(2, 0.5)` + client-level | Cancel-all (optionally filtered) |
| `get_open_orders` | `GET /v5/order/realtime` | `src/trading/services/order_service.py:980`, `:790` (recovery), `:1069` (single) | `@retry(3, 1.0)` + client-level | Open-orders listing / dedup recovery |
| `get_order_history` | `GET /v5/order/history` | `src/trading/services/order_service.py:1010`, `:1078` (fallback) | `@retry(3, 1.0)` + client-level | Closed order recovery |

> "HTTP path (V5)" is given per the V5 unified-API mapping built into
> `pybit`. The system never constructs the URL directly; it always
> calls `BybitClient.call(method=...)`.

### Latency observations (live, last hour, UTC)

Bybit REST is exercised heaviest by KlineWorker and AltDataWorker.
Per-tick latencies (verbatim from `data/logs/workers.log`):

KlineWorker (`get_kline` × 50 symbols × 2-4 timeframes):
```
2026-04-27 22:20:41.401 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11399ms | no_ctx
2026-04-27 22:25:51.364 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=21363ms | no_ctx
2026-04-27 22:30:41.445 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11444ms | no_ctx
2026-04-27 22:35:44.682 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=14680ms | no_ctx
2026-04-27 22:40:46.369 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=16367ms | no_ctx
2026-04-27 22:45:40.434 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=10433ms | no_ctx
2026-04-27 22:55:51.231 | INFO     | src.workers.kline_worker:tick:284 | KLINE_FETCH | klines=39539 expected=40000 symbols=50 quality=ok errors=0 el=21230ms | no_ctx
```

AltData REST (funding + OI):
```
2026-04-27 22:06:54.356 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=9353 oi_ms=8953 fg_ms=0 onchain_ms=2675 total_ms=9353 ran=[funding,oi,onchain] | no_ctx
2026-04-27 22:11:50.347 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=5344 oi_ms=0 fg_ms=0 onchain_ms=2667 total_ms=5344 ran=[funding,onchain] | no_ctx
2026-04-27 22:16:54.555 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=9539 oi_ms=9437 fg_ms=0 onchain_ms=2674 total_ms=9539 ran=[funding,oi,onchain] | no_ctx
2026-04-27 22:21:52.290 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=7269 oi_ms=0 fg_ms=0 onchain_ms=2763 total_ms=7273 ran=[funding,onchain] | no_ctx
2026-04-27 22:26:55.156 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=10137 oi_ms=9940 fg_ms=0 onchain_ms=2661 total_ms=10137 ran=[funding,oi,onchain] | no_ctx
2026-04-27 22:31:50.192 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=5190 oi_ms=0 fg_ms=0 onchain_ms=2670 total_ms=5190 ran=[funding,onchain] | no_ctx
2026-04-27 22:36:54.159 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=8444 oi_ms=9155 fg_ms=0 onchain_ms=2678 total_ms=9156 ran=[funding,oi,onchain] | no_ctx
2026-04-27 22:41:50.061 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=5059 oi_ms=0 fg_ms=0 onchain_ms=2637 total_ms=5059 ran=[funding,onchain] | no_ctx
2026-04-27 22:56:54.192 | INFO     | src.workers.altdata_worker:tick:247 | ALTDATA_TICK_DONE | funding_ms=9020 oi_ms=9190 fg_ms=976 onchain_ms=2669 total_ms=9190 ran=[funding,oi,fear_greed,onchain] | no_ctx
```

Bulk linear-ticker fetch (`get_tickers` no-symbol):
```
2026-04-27 22:08:48.457 | INFO     | src.trading.services.market_service:get_all_linear_tickers:152 | Bulk ticker fetch: 543 USDT perps
2026-04-27 22:17:04.056 | INFO     | src.trading.services.market_service:get_all_linear_tickers:152 | Bulk ticker fetch: 543 USDT perps
2026-04-27 22:23:23.286 | INFO     | src.trading.services.market_service:get_all_linear_tickers:152 | Bulk ticker fetch: 543 USDT perps
2026-04-27 22:53:35.604 | INFO     | src.trading.services.market_service:get_all_linear_tickers:152 | Bulk ticker fetch: 543 USDT perps
```

### Latest observed REST response sample

NOT FOUND verbatim — searched `data/logs/workers.log` and `data/logs/general.log`
for raw Bybit ticker JSON / kline JSON; the system logs aggregated metrics
(`KLINE_FETCH`, `ALTDATA_TICK_DONE`, `Bulk ticker fetch: …`) rather than
raw response bodies. The closest field-by-field reconstruction available
is the post-mapping `Ticker` log emitted at `src/trading/services/market_service.py:103-107`:
```
log.debug(
    "Ticker {s}: {p:.2f} ({c:+.2f}%)",
    s=symbol,
    p=ticker.last_price,
    c=ticker.change_24h_pct,
)
```
which is suppressed at INFO level (current root level, see
`src/core/logging.py`).

The known fields the code consumes from each `get_tickers` row are
verbatim from `src/trading/services/market_service.py:88-99`:
```
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
```
…and for funding from `src/intelligence/altdata/funding_rates.py:55-65`:
```
data = items[0]
funding_rate_str = data.get("fundingRate", "0")
next_time_str = data.get("nextFundingTime", "0")

fr = FundingRate(
    symbol=symbol,
    funding_rate=float(funding_rate_str),
    next_funding_time=timestamp_to_datetime(int(next_time_str)) if next_time_str != "0" else now_utc(),
    predicted_rate=0.0,
    fetched_at=now_utc(),
)
```

### Error patterns observed (last 24h, REST)

Grep of `data/logs/workers.log` for failure tags:

- `FUNDING_FETCH_FAIL`: 0 occurrences (last hour). Greppable patterns
  produced no output:
  - `grep -E "FUNDING_FETCH_FAIL|FEAR_GREED_FETCH_FAIL|FEAR_GREED_FALLBACK|ALTDATA_SOURCE_FAIL"` → 0 matches besides the success `ALTDATA_TICK_DONE` lines pasted above.
- `RateLimitError` / retCode `10006` / HTTP 429: 0 matches in
  `workers.log`.
- `Bybit error`: 0 matches in `workers.log`.
- `KLINE_FETCH … errors=0` is reported on every observed tick; the
  `errors=0` field is sourced from per-symbol exception count in
  `src/workers/kline_worker.py` (the explicit `errors=` slot).
- ERROR-level events in the active session (relevant to REST):
  ```
  2026-04-27 22:16:38.601 | ERROR    | src.workers.strategy_worker:tick:460 | STRAT_PREFETCH_CRITICAL | el=8571ms db=1087ms h1_db=774ms coins=50 | sid=s-1777328190019
  2026-04-27 22:26:39.056 | ERROR    | src.workers.strategy_worker:tick:460 | STRAT_PREFETCH_CRITICAL | el=8870ms db=833ms h1_db=2010ms coins=50 | sid=s-1777328790019
  ```
  These are DB-side, not Bybit — included for completeness because
  they are the only ERROR rows in the live session.

OBSERVED ANOMALY: occasional KLINE_FETCH shortfall: `klines=29997 expected=30000` (lines `22:25:51`, `22:35:44`, `22:40:46`, `22:55:51`) and `klines=39539 expected=40000` (line `22:55:51`). The kline_worker reports `quality=ok errors=0` regardless. (No fix proposed; this is in-scope of B.2.)

---

## A.1.2 — Bybit WebSocket

### Base URL

`src/config/settings.py:68-73`:
```
@property
def ws_url(self) -> str:
    """WebSocket URL based on testnet flag."""
    if self.testnet:
        return "wss://stream-testnet.bybit.com"
    return "wss://stream.bybit.com"
```

Active URL: `wss://stream.bybit.com`. (`config.toml:22` testnet=false.)

### Driver

`pybit.unified_trading.WebSocket` — see
`src/trading/websocket.py:50-56`:
```
from pybit.unified_trading import WebSocket
...
self._public_ws = WebSocket(
    testnet=self._settings.bybit.testnet,
    channel_type="linear",
)
```

Private channel uses `channel_type="private"` and supplies
`api_key`/`api_secret` (`src/trading/websocket.py:71-80`).

`config.toml:34-38` — operational tuning:
```
# WebSocket ping interval in seconds
ws_ping_interval = 20
# Reconnect delay on WS disconnect (seconds)
ws_reconnect_delay = 5
# Order receive window in milliseconds
recv_window = 5000
```

### Subscriptions actually used (Layer 1A — PriceWorker)

Public channel (`channel_type="linear"`):
- `ticker_stream(symbol=…)` — one subscription per coin; PriceWorker
  subscribes to all 50 coins in `settings.universe.watch_list`.
  Subscription call:
  - `src/trading/websocket.py:88-102` (`subscribe_ticker`):
    ```
    def subscribe_ticker(self, symbols: list[str], callback: Callable) -> None:
        ...
        for symbol in symbols:
            self._public_ws.ticker_stream(
                symbol=symbol,
                callback=self._wrap_callback("ticker", callback),
            )
            log.debug("Subscribed to ticker: {s}", s=symbol)
    ```
  - Invoked from `src/workers/price_worker.py:111`:
    ```
    self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)
    ```

Public stream — defined but unused in the current Layer-1 hot path:
- `kline_stream(interval, symbol, callback)` — `src/trading/websocket.py:104-119`. NOT FOUND being subscribed by PriceWorker / KlineWorker / any Layer-1 worker. KlineWorker uses REST `get_kline` (`src/workers/kline_worker.py:200`).
- `orderbook_stream(depth, symbol, callback)` — `src/trading/websocket.py:121-136`. NOT FOUND in any active Layer-1 worker subscription path.

Private channel — defined but unused at runtime in the current
`src/workers/manager.py` wire-up:
- `order_stream(callback)` — `src/trading/websocket.py:138-149`.
- `position_stream(callback)` — `src/trading/websocket.py:151-162`.
- NOT FOUND being called by any worker; private WS plumbing exists
  but no Layer-1 subscriber.

### Message types received (Layer 1A only)

PriceWorker callback `_handle_ticker_update` parses these fields out
of each ticker push (`src/workers/price_worker.py:185-211`):
```
last_price = _sf(tick_data.get("lastPrice"))
...
ticker = Ticker(
    symbol=symbol,
    last_price=last_price,
    bid=_sf(tick_data.get("bid1Price")),
    ask=_sf(tick_data.get("ask1Price")),
    high_24h=_sf(tick_data.get("highPrice24h")),
    low_24h=_sf(tick_data.get("lowPrice24h")),
    volume_24h=_sf(tick_data.get("volume24h")),
    change_24h_pct=_sf(tick_data.get("price24hPcnt")) * 100,
    timestamp=now_utc(),
)
```
Top-level pybit envelope is unwrapped at `src/workers/price_worker.py:168-170`:
```
tick_data = data.get("data", data)
if isinstance(tick_data, list):
    tick_data = tick_data[0] if tick_data else {}
```

### Reconnect policy

`src/trading/websocket.py:36-37`:
```
self._reconnect_attempts = 0
self._max_reconnect_attempts = 10
```

Backoff loop (`src/trading/websocket.py:183-215`):
```
async def reconnect(self) -> None:
    base_delay = self._settings.bybit.ws_reconnect_delay  # 5

    while self._reconnect_attempts < self._max_reconnect_attempts:  # 10
        self._reconnect_attempts += 1
        delay = base_delay * (2 ** (self._reconnect_attempts - 1))
        delay = min(delay, 300)  # Cap at 5 minutes
        ...
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
```

PriceWorker's tick-level health check is in
`src/workers/price_worker.py:130-137`:
```
# Connection health check — if ws dropped, reconnect next tick.
...
if not self.ws.is_running:
    log.warning(f"PRICE_WS_DISC | rsn=ws_not_running | {ctx()}")
    log.warning("Price worker: WebSocket disconnected, will reconnect")
    self._connected = False
```

### Live throughput observations (PRICE_WS_HEALTH heartbeat)

Heartbeat every `interval_seconds` (default 45 s) at
`src/workers/price_worker.py:149-157`. Verbatim sample (last 20):
```
2026-04-27 22:06:29.525 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=8308 msgs_in_window=6231 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:07:14.528 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6717 msgs_in_window=5038 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:07:59.529 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6664 msgs_in_window=4998 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:08:44.531 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6774 msgs_in_window=5081 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:09:29.533 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=7401 msgs_in_window=5551 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:10:14.535 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6578 msgs_in_window=4934 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:10:59.538 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6765 msgs_in_window=5074 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:11:44.540 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=7346 msgs_in_window=5510 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:12:29.543 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6614 msgs_in_window=4961 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:13:14.547 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6645 msgs_in_window=4984 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:13:59.552 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5725 msgs_in_window=4294 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:14:44.555 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6192 msgs_in_window=4644 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:15:29.557 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=7032 msgs_in_window=5274 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:16:14.558 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6585 msgs_in_window=4939 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:16:59.561 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5102 msgs_in_window=3827 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:17:44.563 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6076 msgs_in_window=4557 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:18:29.565 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6306 msgs_in_window=4730 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:19:14.568 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5453 msgs_in_window=4090 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:19:59.570 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5720 msgs_in_window=4290 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:20:44.572 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5214 msgs_in_window=3911 window_s=45.0 subscribed=50 quotes_cached=50
```

Observed throughput range (last hour): 5013 – 10229 msg/min,
`subscribed=50 quotes_cached=50` consistently, `status=connected`
on every heartbeat in the captured window.

### WS error patterns (last 24h)

Grep of `data/logs/workers.log`:
- `PRICE_WS_DISC` — 0 occurrences in the captured window.
- `PRICE_WS_TICK_FAIL` — 0 occurrences.
- `WebSocket reconnect attempt` — 0 occurrences.
- `Reconnect attempt failed` — 0 occurrences.

NOT FOUND any WS failure event in the 22:05 → 22:56 window.

---

## A.1.3 — Finnhub API

### Driver / endpoints

Synchronous SDK `finnhub.Client` wrapped in `asyncio.to_thread`. File
`src/intelligence/news/finnhub_client.py:8-30`:
```
import finnhub
...
class FinnhubClient:
    def __init__(self, settings: Settings) -> None:
        api_key = settings.finnhub.api_key
        if not api_key:
            log.warning("Finnhub API key not set — news features will not work")
        self._client = finnhub.Client(api_key=api_key)
        self._settings = settings
```

Endpoints (per `finnhub-python` SDK, mapped to the documented REST URL):

| SDK method | REST URL | File:line | Decorators |
| --- | --- | --- | --- |
| `general_news` | `GET https://finnhub.io/api/v1/news?category=<cat>&minId=<id>` | `src/intelligence/news/finnhub_client.py:50-52` | `@retry(3, 2.0)` + `@rate_limit(1.0/s)` + `@timed` |
| `economic_calendar` | `GET https://finnhub.io/api/v1/calendar/economic?from=<d>&to=<d>` | `src/intelligence/news/finnhub_client.py:99-103` | `@retry(3, 2.0)` + `@rate_limit(1.0/s)` + `@timed` |

Decorator stack (file:line `src/intelligence/news/finnhub_client.py:33-35`):
```
@retry(max_attempts=3, delay=2.0, exceptions=(FinnhubError, Exception))
@rate_limit(calls_per_second=1.0)
@timed
async def get_general_news(self, category: str = "crypto", min_id: int = 0) -> list[dict]:
```
The crypto-news convenience method (line 73-80) calls the same
`get_general_news(category="crypto")`.

### Rate limit (configured)

`config.toml:40-48`:
```
[finnhub]
# Enable Finnhub news + calendar integration
enabled = true
# Rate limit: Finnhub free tier allows 60 calls/min
rate_limit_per_minute = 60
# Categories to fetch: general, forex, crypto, merger
news_categories = ["crypto", "general"]
# Max articles to fetch per poll
max_articles_per_fetch = 50
```

> The token bucket is parameterised at `calls_per_second=1.0` in code
> (1/s = 60/min) at `src/intelligence/news/finnhub_client.py:34`. The
> `rate_limit_per_minute=60` config key is read into
> `FinnhubSettings` (`src/config/settings.py:80`) but is NOT
> currently consumed by the rate-limit decorator — the decorator value
> is hard-coded `1.0`.

### Authentication

API key from `.env`: `FINNHUB_API_KEY=<REDACTED>`. Passed to
`finnhub.Client(api_key=...)`. No signed request — Finnhub uses query-
parameter `token=<key>` per SDK; the SDK handles header/param
internally.

### Call sites (workers)

- `src/workers/news_worker.py:54`:
  ```
  articles = await self.news_service.fetch_latest_news()
  ```
- which calls `src/intelligence/news/news_service.py:67`:
  ```
  raw_articles = await self._finnhub.get_general_news(category=category)
  ```
- Calendar: `src/intelligence/news/calendar_service.py:42`:
  ```
  raw_events = await self._finnhub.get_economic_calendar(from_date, to_date)
  ```

### Latency observations (live)

NOT FOUND a per-call elapsed_ms log line for Finnhub (`@timed`
emits at DEBUG level, suppressed in production logs). The aggregate
funnel-stage line `FINNHUB_COVERAGE` is emitted once per news_worker
tick.

Verbatim sample (last 8 emissions):
```
2026-04-27 22:08:04.142 | INFO     | src.intelligence.news.news_service:fetch_latest_news:118 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47 | no_ctx
2026-04-27 22:13:07.801 | INFO     | src.intelligence.news.news_service:fetch_latest_news:118 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47 | no_ctx
2026-04-27 22:18:08.879 | INFO     | src.intelligence.news.news_service:fetch_latest_news:118 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47 | no_ctx
2026-04-27 22:23:10.885 | INFO     | src.intelligence.news.news_service:fetch_latest_news:118 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47 | no_ctx
2026-04-27 22:28:12.282 | INFO     | src.intelligence.news.news_service:fetch_latest_news:118 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=1 skipped_old=2 skipped_no_headline=0 skipped_dedup=47 | no_ctx
2026-04-27 22:33:13.351 | INFO     | src.intelligence.news.news_service:fetch_latest_news:118 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47 | no_ctx
```

Cadence ≈ once every 5 minutes. Returned-article count is consistently
96 (the Finnhub free-tier crypto feed page size). After the
`@news_repo.headline_exists` dedup gate, only 0 or 1 new article
typically lands in the DB per cycle.

### Latest observed Finnhub response sample

NOT FOUND a verbatim raw response payload — the system logs only the
funnel summary above. The per-article fields the code reads are
verbatim from `src/intelligence/news/news_service.py:75-113`:
```
ts = raw.get("datetime", 0)
...
headline = raw.get("headline", "")
...
summary = raw.get("summary", "")
...
article = NewsArticle(
    id=str(raw.get("id", generate_id("news"))),
    headline=headline,
    source=raw.get("source", ""),
    url=raw.get("url", ""),
    summary=summary[:500],
    sentiment_score=sentiment,
    symbols=symbols,
    category=raw.get("category", category),
    published_at=published,
    fetched_at=now_utc(),
)
```

### Error patterns (last 24h)

Grep of `data/logs/workers.log` for `Finnhub` / `finnhub`:
- 0 ERROR-level matches.
- 0 occurrences of "Retry exhausted" emitted from
  `core.decorators` for Finnhub.
- All `FINNHUB_COVERAGE` lines in window report `returned=96`
  (no upstream truncation observed).

---

## A.1.4 — Alternative.me Fear & Greed

### Endpoint

`src/intelligence/altdata/fear_greed.py:18`:
```
FEAR_GREED_URL = "https://api.alternative.me/fng/"
```
(Fixed URL; no path parameters; no per-call params attached.)

### Fetch path

`src/intelligence/altdata/fear_greed.py:57-78`:
```
async with aiohttp.ClientSession() as session:
    async with session.get(FEAR_GREED_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            ...
            log.warning(
                f"FEAR_GREED_FETCH_FAIL | url={FEAR_GREED_URL} "
                f"status={resp.status} body='{_body}'"
            )
            raise APIError(
                f"Fear & Greed API returned status {resp.status}",
                ...
            )
        data = await resp.json()
```
Decorators (line 37-38):
```
@retry(max_attempts=3, delay=2.0, exceptions=(APIError, aiohttp.ClientError, Exception))
@timed
```
No `@rate_limit` on this path. Per-process in-memory cache:
`_cache_ttl = 3600.0` seconds (line 35) — 1 h cache hit short-circuit
at line 50-55.

### Cadence

`config.toml:62-66`:
```
[altdata]
# Enable alternative data collection (Fear & Greed, funding rates, etc.)
enabled = true
# Fear & Greed index poll interval in seconds (API updates ~daily)
fear_greed_interval = 3600
```

Worker-level cadence (`src/workers/altdata_worker.py:85-87`):
```
self._fg_interval_s: float = float(
    settings.workers.sweet_spots.altdata.fear_greed_minutes * 60
)
```
Default `fear_greed_minutes = 60` (per settings dataclass). The
worker schedules an internal monotonic deadline `_next_fg_mono`
(line 81); F&G fires only when `t0 >= self._next_fg_mono`
(line 113). After firing, deadline advances by `_fg_interval_s`
regardless of success (line 205-206).

Live evidence — F&G fired only on the `22:56:54` AltData tick
within the captured window (`fg_ms=976`, `value=47`):
```
2026-04-27 22:56:54.192 | INFO     | src.workers.altdata_worker:tick:222 | ALTDATA_FG_TICK | value=47 el=976ms next_in_s=3600 | no_ctx
2026-04-27 22:56:45.977 | INFO     | src.intelligence.altdata.fear_greed:fetch_current:96 | Fear & Greed Index: 47 (Neutral)
```
All earlier AltData ticks in the captured window logged `fg_ms=0`
(F&G skipped due to `_next_fg_mono` not yet elapsed). This matches
`fg_interval_s=3600`.

### Authentication

None. The endpoint is open / unauthenticated.

### Latest observed response sample

The system logs only:
```
2026-04-27 22:56:45.977 | INFO     | src.intelligence.altdata.fear_greed:fetch_current:96 | Fear & Greed Index: 47 (Neutral)
```
Verbatim post-mapping fields stored
(`src/intelligence/altdata/fear_greed.py:84-91`):
```
item = items[0]
fg = FearGreedData(
    value=int(item.get("value", "50")),
    classification=item.get("value_classification", "Neutral"),
    timestamp=datetime.fromtimestamp(
        int(item.get("timestamp", "0")), tz=timezone.utc
    ),
)
```
Raw JSON not captured to logs — NOT FOUND verbatim body.

### Error patterns (last 24h)

- `FEAR_GREED_FETCH_FAIL`: 0 occurrences in window.
- `FEAR_GREED_FALLBACK`: 0 occurrences in window.
- `Failed to fetch Fear & Greed`: 0 occurrences.

NOT FOUND any F&G failure event in the captured window. The single
fetch in the window was successful.

---

## A.1.5 — CoinGecko (on-chain / global metrics)

### Base URL / endpoints

`src/intelligence/altdata/onchain.py:14`:
```
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
```

Endpoints actually called:

| Method | URL | File:line |
| --- | --- | --- |
| `get_global_metrics` | `GET https://api.coingecko.com/api/v3/global` | `src/intelligence/altdata/onchain.py:40` |
| `get_coin_metrics(coin_id)` | `GET https://api.coingecko.com/api/v3/coins/<coin_id>?localization=false&tickers=false&community_data=true&developer_data=false` | `src/intelligence/altdata/onchain.py:67-71` |
| `get_market_dominance` | (delegates to `get_global_metrics`) | `src/intelligence/altdata/onchain.py:101` |

Active hot path: AltDataWorker only calls `get_global_metrics`
(`src/workers/altdata_worker.py:273-274`):
```
async def _fetch_onchain(self):
    return await self.onchain.get_global_metrics()
```

`get_coin_metrics` is defined and decorated (`src/intelligence/altdata/onchain.py:54-90`)
but NOT FOUND being invoked from any worker — it has no live caller in
`src/workers/`.

### Decorators / rate limit / retry

`src/intelligence/altdata/onchain.py:29-31`, `:54-56`, `:92-94`:
```
@retry(max_attempts=2, delay=5.0, exceptions=(APIError, aiohttp.ClientError, Exception))
@rate_limit(calls_per_second=0.3)
@timed
```
Token bucket cap is 0.3 calls/s = 18/min, below the configured
`coingecko_rate_limit_per_minute = 10` ceiling.
`config.toml:71-72`:
```
# CoinGecko rate limit (free tier: 10-30 calls/min)
coingecko_rate_limit_per_minute = 10
```
> The 0.3/s decorator value (18/min) is **higher** than the 10/min
> config; the config key is read into `AltDataSettings`
> (`src/config/settings.py:109`) but is NOT consumed by the
> decorator — the rate value is hard-coded.

### HTTP client

`src/intelligence/altdata/onchain.py:107-129`:
```
async def _get(self, url: str, params: dict | None = None) -> dict:
    ...
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 429:
                raise APIError("CoinGecko rate limit exceeded", details={"status": 429})
            if resp.status != 200:
                raise APIError(
                    f"CoinGecko returned status {resp.status}",
                    details={"status": resp.status, "url": url},
                )
            return await resp.json()
```
Per-call timeout: 15 s.

### Authentication

None. Public free tier.

### Cadence

`AltDataWorker.tick()` calls `_fetch_onchain` every tick when the
funding cadence fires (`src/workers/altdata_worker.py:111-114, 138-139`):
```
fire_funding = self.funding is not None
fire_oi = ...
fire_fg = ...
fire_onchain = self.onchain is not None  # cheap; piggybacks funding cadence
```
Funding fires every `altdata` sweet-spot wake (every 5 min by
default). Live evidence — `onchain_ms` reported every tick:
```
funding_ms=9353 oi_ms=8953 fg_ms=0 onchain_ms=2675 ...
funding_ms=5344 oi_ms=0 fg_ms=0 onchain_ms=2667 ...
funding_ms=9539 oi_ms=9437 fg_ms=0 onchain_ms=2674 ...
funding_ms=7269 oi_ms=0 fg_ms=0 onchain_ms=2763 ...
funding_ms=10137 oi_ms=9940 fg_ms=0 onchain_ms=2661 ...
funding_ms=5190 oi_ms=0 fg_ms=0 onchain_ms=2670 ...
funding_ms=8444 oi_ms=9155 fg_ms=0 onchain_ms=2678 ...
funding_ms=5059 oi_ms=0 fg_ms=0 onchain_ms=2637 ...
funding_ms=9020 oi_ms=9190 fg_ms=976 onchain_ms=2669 ...
```
Latency range: 2637 ms – 2763 ms.

### Latest observed response sample

NOT FOUND a verbatim CoinGecko JSON body in workers.log — the system
logs only `onchain_ms` elapsed. Field mapping the code consumes
(`src/intelligence/altdata/onchain.py:41-48`):
```
gd = data.get("data", {})
return {
    "total_market_cap_usd": gd.get("total_market_cap", {}).get("usd", 0),
    "btc_dominance": gd.get("market_cap_percentage", {}).get("btc", 0),
    "eth_dominance": gd.get("market_cap_percentage", {}).get("eth", 0),
    "active_cryptocurrencies": gd.get("active_cryptocurrencies", 0),
    "market_cap_change_24h_pct": gd.get("market_cap_change_percentage_24h_usd", 0),
}
```

### Error patterns (last 24h)

- `CoinGecko rate limit exceeded`: 0 occurrences.
- `CoinGecko returned status`: 0 occurrences.
- `CoinGecko global metrics error`: 0 occurrences.
- `ALTDATA_SOURCE_FAIL src=onchain`: 0 occurrences.

NOT FOUND any CoinGecko failure in the captured window.

### CoinGecko symbol map

`src/intelligence/signals/signal_models.py:62-69`:
```
COINGECKO_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
}
```
Only 5 coins are mapped — `get_coin_metrics()` is callable for these
five only without producing an `coin_id.upper()+"USDT"` fallback.

---

## A.1.6 — Other external services

The following audit was performed:

```
grep -rn "requests\.\|aiohttp\|httpx\|urllib\|websockets" --include="*.py" src/
```
across the full `src/` tree. Findings, by destination:

| Service | Driver | URL / base | File:line | In Layer 1→Stage 2 hot path? |
| --- | --- | --- | --- | --- |
| Bybit REST | `pybit.unified_trading.HTTP` | `https://api.bybit.com` | (covered in A.1.1) | YES |
| Bybit WS | `pybit.unified_trading.WebSocket` | `wss://stream.bybit.com` | (covered in A.1.2) | YES (PriceWorker) |
| Finnhub | `finnhub.Client` (sync, wrapped in `asyncio.to_thread`) | (SDK-internal) `https://finnhub.io/api/v1/...` | `src/intelligence/news/finnhub_client.py:30, 50, 99` | YES (NewsWorker) |
| Alternative.me | `aiohttp.ClientSession` (per-call) | `https://api.alternative.me/fng/` | `src/intelligence/altdata/fear_greed.py:18, 58-59` | YES (AltDataWorker) |
| CoinGecko | `aiohttp.ClientSession` (per-call) | `https://api.coingecko.com/api/v3` | `src/intelligence/altdata/onchain.py:14, 120-121` | YES (AltDataWorker) |
| Anthropic Claude API (Brain credentials) | `urllib.request` (single-attempt 30s) | (token refresh endpoint, not pasted in code in scope) | `src/brain/claude_code_client.py:30-31, 755-766` | NO — Stage 2 onwards |
| DeepSeek (TIAS Stage 2 verifier) | `aiohttp.ClientSession` (lazy persistent) | (env-driven; URL not hard-coded in this file) | `src/tias/deepseek_client.py:19, 88, 135` | NO — Stage 2 onwards |
| Qwen / DashScope (APEX Stage 2) | `aiohttp.ClientSession` (lazy persistent) | (env-driven) | `src/apex/qwen_client.py:24, 81, 134` | NO — Stage 2 onwards |
| Shadow paper exchange | `aiohttp.ClientSession` (shared from manager) | local Shadow base URL (constructed by `transformer`) | `src/shadow/shadow_adapter.py:21, 60, 144, 397, 585` | NO — execution side |

> Reddit (`praw`) is referenced in `src/config/settings.py:86-99` but
> `config.toml` has `[reddit] enabled = false`. The reddit_worker
> module exists at `src/workers/reddit_worker.py` but per the
> operator's notes is currently inactive (see B.5.SPECIAL).

Static-analysis whitelist (`src/factory/validator.py:14-15`)
explicitly tracks `requests`, `aiohttp`, `urllib`, `socket`, `http`
imports as networking tokens — used by the validator to flag any
new external-IO addition. Confirms no other production module
introduces a hidden external dependency outside the table above.

---

## Cross-cutting evidence: ERRORs in the active session

Full list of ERROR / CRITICAL rows in `data/logs/workers.log` for the
captured window (none of which originate from the external APIs in
scope):

```
2026-04-27 22:16:38.601 | ERROR    | src.workers.strategy_worker:tick:460 | STRAT_PREFETCH_CRITICAL | el=8571ms db=1087ms h1_db=774ms coins=50 | sid=s-1777328190019
2026-04-27 22:26:39.056 | ERROR    | src.workers.strategy_worker:tick:460 | STRAT_PREFETCH_CRITICAL | el=8870ms db=833ms h1_db=2010ms coins=50 | sid=s-1777328790019
2026-04-27 22:45:52.782 | CRITICAL | workers:_sync_emit | WORKER_SHUTDOWN | reason=atexit | clean exit recorded
2026-04-27 22:45:52.781 | CRITICAL | __main__:_atexit_log:82 | WORKER_SHUTDOWN | reason=atexit | clean exit recorded
```

`STRAT_PREFETCH_CRITICAL` is a DB read, not a Bybit/Finnhub/CoinGecko
event. `WORKER_SHUTDOWN | reason=atexit` corresponds to the operator
restarting the worker process at 22:45:52 (the live process was then
re-started at 22:53:26 per `Shadow API is not reachable at startup`
warning — service confirmed live again from 22:55:51 KLINE_FETCH).

---

## Gaps documented

1. **NOT FOUND** verbatim Bybit ticker/kline raw JSON body in any
   log file under `data/logs/`. Only post-mapped values and aggregate
   counters are emitted. Searched: `data/logs/workers.log`,
   `data/logs/general.log`.
2. **NOT FOUND** verbatim Finnhub article body. Only the
   `FINNHUB_COVERAGE` aggregator line is emitted at INFO; per-article
   content is written to `news_articles` DB but not to the log
   stream.
3. **NOT FOUND** verbatim Alternative.me / CoinGecko JSON body. Same
   reason — only post-mapped values are logged.
4. **NOT FOUND** any Bybit retCode 10006 / 429 / `Bybit error` row
   in the captured window. The captured window is ~50 minutes of
   live runtime (22:05–22:56 UTC); rotated logs were not re-grepped.
5. **NOT FOUND** any active subscription to Bybit WS `kline_stream` or
   `orderbook_stream`. They are defined in `src/trading/websocket.py`
   but never invoked from any Layer-1 worker.
6. **NOT FOUND** `FinnhubSettings.rate_limit_per_minute` being
   consumed at runtime — the value (60) sits in config but the actual
   rate-limit decorator hard-codes `calls_per_second=1.0`.
7. **NOT FOUND** `AltDataSettings.coingecko_rate_limit_per_minute`
   being consumed at runtime — the value (10) sits in config but the
   decorator hard-codes `calls_per_second=0.3` (18/min).
8. **NOT FOUND** `BybitSettings.ws_ping_interval` being read in
   application code — the value (`20`) is in `config.toml` and
   `BybitSettings` (file `src/config/settings.py:55`) but is not
   referenced in `src/trading/websocket.py` (pybit handles WS pings
   internally).
9. **NOT FOUND** any active call to `get_coin_metrics` (CoinGecko
   per-coin endpoint) — the method exists in `onchain.py` but no
   worker invokes it.

---

## End of A1
