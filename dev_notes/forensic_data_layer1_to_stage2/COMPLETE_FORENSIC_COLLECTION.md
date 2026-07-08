# COMPLETE FORENSIC COLLECTION — Layer 1 → Stage 2 Pipeline

**Generated:** 2026-04-27T23:22:15Z
**Source:** consolidated copy of all 25 forensic files in this directory
**Project:** /home/inshadaliqbal786/trading-intelligence-mcp
**Capture window:** 2026-04-27 22:53 UTC – 23:20 UTC (workers PID 399)
**DB snapshot used:** _trading_db_snapshot.db (147 MB, captured 22:56:15 UTC)

This single file is a verbatim concatenation of all module files in deliverable order.
For navigation, see the per-section dividers below or read `INDEX.md` for one-line summaries.

---

## INDEX

## Module A — Data Sources
- [A1_external_apis.md](A1_external_apis.md) — Bybit REST/WS, Finnhub, Alternative.me F&G, CoinGecko inventory + 9 documented gaps

## Module B — Layer 1A Workers
- [B1_price_worker.md](B1_price_worker.md) — `_ws_quotes` + `ticker_cache` wiring; ~5,500 msgs/min, 50 coins
- [B2_kline_worker.md](B2_kline_worker.md) — Multi-TF schedule, executemany write pattern; `KLINE_WRITE_DONE` tag NOT FOUND
- [B3_altdata_worker.md](B3_altdata_worker.md) — funding/OI/F&G/onchain cadences; F&G skipped 11/12 wakeups by design
- [B4_news_worker.md](B4_news_worker.md) — `SYMBOL_EXTRACTION_MAP` only covers 10 of 50 watch_list base assets
- [B5_sentiment_aggregator.md](B5_sentiment_aggregator.md) — SENT_UNKNOWN root cause: Reddit disabled + 44/50 coins untagged

## Module C — Layer 1B Analyzers
- [C1_structure_worker.md](C1_structure_worker.md) — 12 X-RAY phases, batch_size=25 (not 2 as prompt stated), live setup_type distribution
- [C2_signal_worker.md](C2_signal_worker.md) — Phase-29 gate is NOT cause of NEUTRAL; upstream classifier is. BTCUSDT trace pinned.
- [C3_regime_worker.md](C3_regime_worker.md) — APEX/TIAS use SAME accessor (no key mismatch); 8/49 first-tick changes bypass hysteresis
- [C4_ta_cache.md](C4_ta_cache.md) — TTL=120s (not 90s default), maxsize=200, 9 production callers

## Module D — Strategy Pipeline
- [D1_strategy_worker.md](D1_strategy_worker.md) — 4 internal layers, 30 of 39 strategies fired 0×, B4=73% of L1 signals
- [D2_strategy_performance.md](D2_strategy_performance.md) — Only `claude_trader` populates table; A1-K4 absent. `strategy_trades` closure fields never UPDATEd

## Module E — Smart Scanner
- [E1_scanner_worker.md](E1_scanner_worker.md) — `qualified=0` decomposition: 25+10+12+2+1=50 with verbatim XRAY_NONE_REASON samples
- [E2_coin_package_builder.md](E2_coin_package_builder.md) — Cold-start 0.67 root-cause per missing field; `_enrich_for` AttributeError fall-through

## Module F — Cross-Layer Wiring
- [F1_inter_worker_caches.md](F1_inter_worker_caches.md) — 13 caches inventoried; NO runtime introspection mechanism
- [F2_db_tables.md](F2_db_tables.md) — 15 tables; D-3 lock peak 137,403 ms on 2026-04-26 21:14:54
- [F3_sweet_spot_scheduling.md](F3_sweet_spot_scheduling.md) — Per-worker schedule + cycle gate verbatim; live drift 0–28 ms
- [F4_data_flow_trace.md](F4_data_flow_trace.md) — End-to-end cycle 22:25:00 timing; package age 163–287s at Brain CALL_A

## Module G — Configuration & Live State
- [G1_config_toml.md](G1_config_toml.md) — Verbatim 1094-line config.toml, md5 d5c308beb5441fb193217013e3f3a545
- [G2_hardcoded_thresholds.md](G2_hardcoded_thresholds.md) — 27 threshold groups not in config; 4 sources of truth for `min_rr_ratio`
- [G3_live_cache_snapshots.md](G3_live_cache_snapshots.md) — Reconstructed from logs; no in-process dump endpoint
- [G4_scanner_cycles.md](G4_scanner_cycles.md) — Last 7 cycles ALL `qualified=0 forced=2`
- [G5_brain_cycles.md](G5_brain_cycles.md) — Last 5 CALL_A: 0/2/2/2/2 packages, 2/2/1/0/2 trades; placement results not in logs
- [G6_errors_24h.md](G6_errors_24h.md) — 79 ERROR/CRITICAL events, ORDER_GATE_LM_DEADLINE_EXCEEDED=20, ORDER_BLOCKED=20
- [G7_worker_inventory.md](G7_worker_inventory.md) — 19 registered workers; 7 "dormant" workers confirmed gated/removed

## Verification Gate
1. ✅ Every file exists and is non-empty (43,713 → 9,352 bytes)
2. ✅ Code references cite file:line in source repo
3. ✅ Live measurements have actual timestamps (22:05–23:20 UTC window)
4. ✅ Cache snapshots either contain values or document "NOT FOUND — reconstructed from..."
5. ✅ config.toml pasted verbatim with md5 fingerprint
6. ✅ Hardcoded thresholds enumerated against config

## Pre-Condition Notes (full transparency)
- Available log window: ~1h20m current `workers.log` (22:10–22:59) plus rotated `workers.2026-04-27_01-31-00`. No continuous 24h trail; "last 24h" counts bounded by visible window.
- Workers process restarted at 22:45:52→22:53:26 (clean atexit) mid-collection — `fires` counter reset noted in F3.
- `KLINE_WRITE_DONE`, `SCANNER_QUALIFY` per-coin emissions, runtime cache-dump endpoint: NOT FOUND (documented per Hard Rule 5, not fabricated).

## What This Document Is NOT
This is forensic data ONLY. No fix proposals. No architecture critiques. The external designer reads these 25 files (or `COMPLETE_FORENSIC_COLLECTION.md`) and writes a precise fix plan separately.

---


================================================================================
FILE: A1_external_apis.md
================================================================================

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


================================================================================
FILE: B1_price_worker.md
================================================================================

# B1 — PriceWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.1.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/price_worker.py`
- Size: 11,646 bytes
- Lines of code (`wc -l`): 264
- Last modified: 2026-04-27 20:40:06 UTC

## B.1.2 — Public methods (signatures + tick body)

Class declaration (line 26): `class PriceWorker(BaseWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 41).

### `__init__` (line 43)
```
def __init__(
    self, settings: Settings, db: DatabaseManager,
    ws: BybitWebSocket, scanner=None,
) -> None:
    super().__init__(
        name="price_worker",
        interval_seconds=float(settings.workers.market_data_interval),
        settings=settings,
        db=db,
    )
    self.ws = ws
    self.market_repo = MarketRepository(db)
    self._scanner = scanner  # legacy injection; not read by tick()
    self._tracked_symbols: list[str] = list(settings.universe.watch_list)
    self._connected = False
    self._dropped_count: int = 0
    self._ws_quotes: dict[str, tuple[float, float]] = {}
    self._ws_msg_count: int = 0
    self._ws_health_last_emit: float = _time.monotonic()
```

### `tick()` (line 75) — full body verbatim
```python
async def tick(self) -> None:
    universe = list(self.settings.universe.watch_list)
    if not universe:
        log.warning(
            f"PRICE_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
        )
        return

    if set(universe) != set(self._tracked_symbols):
        log.info(
            "PriceWorker: Updating symbols {old} -> {new}",
            old=len(self._tracked_symbols), new=len(universe),
        )
        self._tracked_symbols = universe
        if self._connected:
            self._connected = False

    if not self._connected:
        await self.ws.connect_public()
        self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)
        self._connected = True
        _sample = ",".join(self._tracked_symbols[:10])
        _suffix = "..." if len(self._tracked_symbols) > 10 else ""
        log.info(
            f"PRICE_WS_CONN | symbols={len(self._tracked_symbols)} "
            f"sample=[{_sample}{_suffix}] | {ctx()}"
        )
        log.info(
            "Price worker: WebSocket connected, subscribed to {n} symbols",
            n=len(self._tracked_symbols),
        )
    else:
        if not self.ws.is_running:
            log.warning(f"PRICE_WS_DISC | rsn=ws_not_running | {ctx()}")
            log.warning("Price worker: WebSocket disconnected, will reconnect")
            self._connected = False

    now_mono = _time.monotonic()
    elapsed_s = max(now_mono - self._ws_health_last_emit, 0.001)
    msgs_per_min = (self._ws_msg_count / elapsed_s) * 60.0
    log.info(
        f"PRICE_WS_HEALTH | "
        f"status={'connected' if self._connected and self.ws.is_running else 'disconnected'} "
        f"msgs_per_min={msgs_per_min:.0f} "
        f"msgs_in_window={self._ws_msg_count} "
        f"window_s={elapsed_s:.1f} "
        f"subscribed={len(self._tracked_symbols)} "
        f"quotes_cached={len(self._ws_quotes)} | {ctx()}"
    )
    self._ws_msg_count = 0
    self._ws_health_last_emit = now_mono
```

### Other public methods
- `_handle_ticker_update(self, data: dict) -> None` (line 161) — WS callback (not async). Validates payload, normalises with `_sf` safe-float helper, drops on `last_price <= 0` (logs `PRICE_SKIP_INVALID`), stores `(last_price, monotonic())` in `self._ws_quotes[symbol]`, increments `self._ws_msg_count`, builds `Ticker` dataclass, schedules `self.market_repo.save_ticker(ticker)` via `loop.create_task`. On exception: increments `_dropped_count`, logs `PRICE_WS_TICK_FAIL`.
- `get_ws_quote(self, symbol: str, max_age_s: float = 5.0) -> float | None` (line 239) — Public read accessor. Reads `self._ws_quotes[symbol]`; returns price if `monotonic() - ts <= max_age_s` and `price > 0`, else `None`.
- `cleanup(self) -> None` (line 260) — disconnects WS on stop.

## B.1.3 — What it READS

- WebSocket subscription set: `self._tracked_symbols`, seeded from `settings.universe.watch_list` and refreshed every tick (price_worker.py:88, :100). Subscribes via `self.ws.subscribe_ticker(symbols, self._handle_ticker_update)` at price_worker.py:111. The `BybitWebSocket.subscribe_ticker` is defined at `src/trading/websocket.py:88`; `connect_public` at `src/trading/websocket.py:45`.
- DB reads at startup: NONE (verified — no `db.fetch_*` or repo read calls in module).
- Config consumed:
  - `settings.workers.market_data_interval` → tick interval seconds (line 49). config.toml: `[workers] market_data_interval = 45`.
  - `settings.universe.watch_list` → 50-symbol list. config.toml: `[universe] watch_list = [...]` (50 entries; verified by inspection).

## B.1.4 — What it WRITES

In-memory caches:
- `self._ws_quotes: dict[str, tuple[float, float]]` (declared price_worker.py:66; written :196). Key = symbol (e.g. `BTCUSDT`); value = `(last_price: float, monotonic_ts: float)`.
- `self._ws_msg_count: int` (declared :72; written :200) — per-tick WS message counter, reset to 0 each tick (:158).
- `self._dropped_count: int` (declared :61; incremented in `_handle_ticker_update` exception branch :224).

DB tables:
- `ticker_cache` — written via `MarketRepository.save_ticker()` at price_worker.py:218 (`loop.create_task(self.market_repo.save_ticker(ticker))`). Schema:
  ```
  CREATE TABLE ticker_cache (
      symbol TEXT PRIMARY KEY,
      last_price REAL NOT NULL,
      bid REAL NOT NULL DEFAULT 0,
      ask REAL NOT NULL DEFAULT 0,
      high_24h REAL NOT NULL DEFAULT 0,
      low_24h REAL NOT NULL DEFAULT 0,
      volume_24h REAL NOT NULL DEFAULT 0,
      change_24h_pct REAL NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
  );
  ```
  Insert SQL (market_repo.py:267):
  ```
  INSERT OR REPLACE INTO ticker_cache
  (symbol, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct, updated_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  ```

## B.1.5 — Cadence

- `tick()` fires on `BaseWorker` fixed interval. interval_seconds = `settings.workers.market_data_interval` = 45 s (config.toml:`[workers] market_data_interval = 45`).
- WS messages: continuous push from Bybit (no polling). Live measurement: msgs_per_min = 4,482 - 7,148 (range across the last 25 health heartbeats), msgs_in_window 3,362 - 5,361 over 45 s. So ~80–120 msg/s aggregate from 50 subscribed tickers.
- Cache (`_ws_quotes`) update: per WS message (price_worker.py:196). Write rate ≈ msgs/s above.

## B.1.6 — Live measurements

PRICE_WS_HEALTH events (last 5 verbatim from `data/logs/workers.log`):
```
2026-04-27 22:55:55.968 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5456 msgs_in_window=4092 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:56:40.970 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6224 msgs_in_window=4668 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:57:25.972 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6258 msgs_in_window=4694 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:58:10.974 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5204 msgs_in_window=3903 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:58:55.977 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=4482 msgs_in_window=3362 window_s=45.0 subscribed=50 quotes_cached=50
```

PRICE_WS_TICK_* events (per spec request "Last 20 PRICE_WS_TICK_*"):
- `PRICE_WS_TICK_FAIL`: NOT FOUND in `data/logs/workers.log` and `workers.2026-04-27_01-31-00_169356.log` over the available retained window (grep count = 0).
- The `_handle_ticker_update` path emits ONLY DEBUG line "Price update: {s} = {p}" on success (price_worker.py:222) and `PRICE_WS_TICK_FAIL` on exception. With log_level=INFO (config.toml:[general] log_level = "INFO") successes are not retained. So no per-tick events are observable; observability is limited to the 45-s `PRICE_WS_HEALTH` heartbeats above.

WORKER_TICK_DONE events for price_worker — emitted as `LAYER1A_TICK_DONE | sub=price_worker`. Last 5:
```
2026-04-27 22:55:55.968 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:56:40.971 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:57:25.972 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:58:10.975 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:58:55.977 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
```
Note: `elapsed_ms` was 576 ms only on the `WORKER_FIRST_TICK` connect event at 22:53:40.962.

Current `_ws_quotes` size: 50 (verified from PRICE_WS_HEALTH `quotes_cached=50` repeatedly across all heartbeats since 22:54:25).

Current message rate: 4,482 - 7,148 msgs/min (window of last 25 heartbeats); median ≈ 5,500.

`WORKER_FIRST_TICK` for price_worker: `2026-04-27 22:53:40.962 | name=price_worker el_to_first_tick_ms=576 first_tick_el_ms=576`.

## B.1.7 — Failure modes (last 24h)

Available log window: `data/logs/workers.log` covers 2026-04-27 22:10 to 22:59 plus `workers.2026-04-27_01-31-00_169356.log` (older session). Search across both files:

| Tag | Count | File:line of emitter |
|-----|------:|----------------------|
| `PRICE_WS_TICK_FAIL` | 0 | price_worker.py:228 |
| `PRICE_WS_DISC` | 0 | price_worker.py:135 |
| `PRICE_SKIP_INVALID` | 0 | price_worker.py:190 (DEBUG level, suppressed under INFO) |
| `PRICE_UNIVERSE_EMPTY` | 0 | price_worker.py:91 |

`PRICE_WS_CONN` (one-shot connect events):
```
2026-04-27 22:53:40.962 | PRICE_WS_CONN | symbols=50 sample=[BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,ARBUSDT...] | no_ctx
```
GAP: with PRICE_SKIP_INVALID logged at DEBUG, any silent zero-price drops cannot be enumerated under the current INFO log level.

## B.1.8 — Dependencies (consumers)

- `src/apex/assembler.py:147–148` — `if price_worker and hasattr(price_worker, "get_ws_quote"): q = price_worker.get_ws_quote(symbol, max_age_s=5.0)`. APEX assembler reads the live WS quote with a 5 s freshness tolerance.
- `ticker_cache` table consumers (DB reads):
  - `src/database/repositories/market_repo.py:294` — `MarketRepository.get_ticker(symbol)` — `SELECT * FROM ticker_cache WHERE symbol = ?`.
  - `src/intelligence/sentiment/aggregator.py:165–166` — `SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?` (used in the no-data SENT branch to log `change_24h`).
- The legacy `_scanner` injection (price_worker.py:55) is documented as not read by `tick()`; it remains for backward-compat.


================================================================================
FILE: B2_kline_worker.md
================================================================================

# B2 — KlineWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.2.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/kline_worker.py`
- Size: 23,778 bytes
- Lines of code: 494
- Last modified: 2026-04-27 20:29:43 UTC

## B.2.2 — Public methods (signatures + tick body)

Class declaration (line 53): `class KlineWorker(SweetSpotWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 73).

### Module-level constants
```python
# kline_worker.py:32-37
TIMEFRAME_SCHEDULE = {
    TimeFrame.M5: 60,
    TimeFrame.H1: 60,
    TimeFrame.H4: 300,
    TimeFrame.D1: 3600,
}

# kline_worker.py:44
_KLINE_FRESHNESS_THRESHOLD_S = 600.0

# kline_worker.py:50
_LAG_QUERY_MAX_SYMBOLS = 500
```

### `__init__` (line 75)
```
def __init__(self, settings, db, market_service, scanner=None):
    super().__init__(
        name="kline_worker",
        sweet_spot=settings.workers.sweet_spots.kline_worker,
        settings=settings, db=db,
        window_minutes=settings.workers.sweet_spots.window_minutes,
    )
    ...
    self._tracked_symbols: list[str] = list(settings.universe.watch_list)
    self._last_fetch: dict[str, float] = {}
    self._last_tick_per_symbol: dict[str, int] = {}
    self._circuit_breaker_until: float = 0.0
    self._consecutive_fails: dict[str, int] = {}
    self._fail_streak_started: dict[str, float] = {}
    self._STRAGGLER_THRESHOLD = 3
    self._tick_count: int = 0
    self._consecutive_busy_checkpoints: int = 0
```

### Helpers
- `_classify_fetch_quality(total, expected) -> (level, reason)` (line 124, staticmethod). Mapping:
  - `expected <= 0` → `("INFO", "ok")`
  - `total == 0` → `("CRITICAL", "zero_fetch")`
  - `ratio < 0.5` → `("ERROR", "short_50pct")`
  - `ratio < 0.9` → `("WARNING", "short_10pct")`
  - else → `("INFO", "ok")`
- `is_circuit_open(self) -> bool` (line 146): returns `time.monotonic() < self._circuit_breaker_until`. Used by `strategy_worker` to gate TA on a fetch collapse.

### `tick()` (line 150) — full body verbatim
```python
async def tick(self) -> None:
    universe = list(self.settings.universe.watch_list)
    if not universe:
        log.warning(
            f"KLINE_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
        )
        return
    self._tracked_symbols = universe

    now = time.time()
    t0_mono = time.monotonic()
    total_fetched = 0
    errors_this_tick = 0
    skipped_cooldown = 0
    tf_fetched: dict[str, int] = {tf.value: 0 for tf in TIMEFRAME_SCHEDULE}
    per_symbol_fetched: dict[str, int] = {s: 0 for s in self._tracked_symbols}
    per_symbol_expected: dict[str, int] = {s: 0 for s in self._tracked_symbols}

    for symbol in self._tracked_symbols:
        for timeframe, min_interval in TIMEFRAME_SCHEDULE.items():
            cache_key = f"{symbol}:{timeframe.value}"
            last = self._last_fetch.get(cache_key, 0)

            if now - last < min_interval:
                skipped_cooldown += 1
                continue

            per_symbol_expected[symbol] += 200

            try:
                klines = await self.market_service.get_klines(
                    symbol, timeframe, limit=200,
                )
                n = len(klines)
                total_fetched += n
                tf_fetched[timeframe.value] = tf_fetched.get(timeframe.value, 0) + n
                per_symbol_fetched[symbol] = per_symbol_fetched.get(symbol, 0) + n
                self._last_fetch[cache_key] = now
                if n > 0:
                    try:
                        from src.core.cache_freshness import record_write
                        record_write("klines", f"{symbol}:{timeframe.value}")
                    except Exception:
                        pass
                if n > 0 and symbol in self._consecutive_fails:
                    del self._consecutive_fails[symbol]
                    self._fail_streak_started.pop(symbol, None)
                await asyncio.sleep(0)
            except Exception as e:
                errors_this_tick += 1
                log.warning(
                    f"KLINE_FETCH_FAIL | sym={symbol} tf={timeframe.value} "
                    f"err={str(e)[:120]} | {ctx()}"
                )

    self._last_tick_per_symbol = dict(per_symbol_fetched)

    # Phase 3 (post-Layer-1 fix): consecutive-fail tracking, KLINE_STRAGGLER.
    for sym, exp in per_symbol_expected.items():
        if exp <= 0:
            continue
        got = per_symbol_fetched.get(sym, 0)
        if got > 0:
            self._consecutive_fails.pop(sym, None)
            self._fail_streak_started.pop(sym, None)
            continue
        self._consecutive_fails[sym] = self._consecutive_fails.get(sym, 0) + 1
        if sym not in self._fail_streak_started:
            self._fail_streak_started[sym] = now
        if self._consecutive_fails[sym] >= self._STRAGGLER_THRESHOLD:
            duration_s = now - self._fail_streak_started[sym]
            log.warning(
                f"KLINE_STRAGGLER | sym={sym} "
                f"consecutive_fails={self._consecutive_fails[sym]} "
                f"duration={duration_s:.0f}s | {ctx()}"
            )

    # KLINE_FETCH primary log
    expected_total = sum(per_symbol_expected.values())
    level, reason = self._classify_fetch_quality(total_fetched, expected_total)
    el_ms = (time.monotonic() - t0_mono) * 1000
    _emit = getattr(log, level.lower(), log.info)
    _emit(
        f"KLINE_FETCH | klines={total_fetched} expected={expected_total} "
        f"symbols={len(self._tracked_symbols)} quality={reason} "
        f"errors={errors_this_tick} el={el_ms:.0f}ms | {ctx()}"
    )

    # Per-symbol gap
    if level in ("WARNING", "ERROR", "CRITICAL"):
        for sym, exp in per_symbol_expected.items():
            if exp <= 0:
                continue
            got = per_symbol_fetched.get(sym, 0)
            if got >= exp:
                continue
            stale_since = now - self._last_fetch.get(
                f"{sym}:{TimeFrame.M5.value}", 0,
            )
            log.warning(
                f"KLINE_GAP | sym={sym} expected={exp} got={got} "
                f"stale_since={stale_since:.0f}s | {ctx()}"
            )

    if level == "CRITICAL":
        self._circuit_breaker_until = time.monotonic() + 30.0
        log.critical(
            f"KLINE_CIRCUIT_BREAKER | open_until=+30s reason={reason} | {ctx()}"
        )

    # Single grouped SELECT for KLINE_WRITE_LAG + KLINE_FRESHNESS_WARN
    try:
        _scan_syms = self._tracked_symbols[:_LAG_QUERY_MAX_SYMBOLS]
        if _scan_syms:
            placeholders = ",".join("?" for _ in _scan_syms)
            kline_rows = await self.db.fetch_all(
                f"""
                SELECT symbol, MAX(timestamp) AS newest_ts
                FROM klines
                WHERE timeframe = ? AND symbol IN ({placeholders})
                GROUP BY symbol
                """,
                (TimeFrame.M5.value, *_scan_syms),
            )
            now_dt = datetime.now(timezone.utc)
            _M5_PERIOD_S = 300
            _LAG_BUFFER_S = 60
            _LAG_THRESHOLD_S = _M5_PERIOD_S + _LAG_BUFFER_S
            _lag_stale: list[tuple[str, float]] = []
            _seen_syms: set[str] = set()
            for r in kline_rows:
                ...
                if age_s > _LAG_THRESHOLD_S:
                    _lag_stale.append((sym, age_s))
                if age_s > _KLINE_FRESHNESS_THRESHOLD_S:
                    log.warning(f"KLINE_FRESHNESS_WARN | sym={sym} age_s={age_s:.0f} ...")
            if _lag_stale:
                _lag_stale.sort(key=lambda x: -x[1])
                top = ",".join(f"{s}={a:.0f}s" for s, a in _lag_stale[:5])
                log.warning(f"KLINE_WRITE_LAG | stale_count={len(_lag_stale)} ...")
            for sym in _scan_syms:
                if sym not in _seen_syms:
                    log.warning(f"KLINE_FRESHNESS_WARN | sym={sym} age_s=inf reason=no_klines_in_db ...")
    except Exception as e:
        log.debug("KLINE_FRESHNESS_SKIP | err='{err}'", err=str(e)[:120])

    self._tick_count += 1
    await self._maybe_run_wal_checkpoint()

    tf_split = ",".join(f"{tf}:{n}" for tf, n in tf_fetched.items())
    log.info(
        f"KLINE_TICK_SUMMARY | universe={len(self._tracked_symbols)} "
        f"fetched={total_fetched} saved={total_fetched} "
        f"skipped={skipped_cooldown} tf_split={{{tf_split}}} "
        f"errors={errors_this_tick} el={el_ms:.0f}ms "
        f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
    )
```

### `_maybe_run_wal_checkpoint()` (line 416)
Cadence-controlled `PRAGMA wal_checkpoint(PASSIVE)` after writes. Cadence: `settings.database.wal_checkpoint_every_n_kline_ticks` (config = 50). Escalates to TRUNCATE after `wal_checkpoint_truncate_after_busy_count` (= 3) consecutive busy results.

## B.2.3 — What it READS

- `settings.universe.watch_list` (50 coins) per tick (kline_worker.py:161).
- `self._last_fetch[symbol:tf]` cache for cooldown gating.
- DB read in tick body: a single grouped freshness SELECT (kline_worker.py:330):
  ```
  SELECT symbol, MAX(timestamp) AS newest_ts
  FROM klines
  WHERE timeframe = ? AND symbol IN (?,...)
  GROUP BY symbol
  ```
- WAL file size on disk via `os.path.getsize(wal_path)` for checkpoint instrumentation (kline_worker.py:444, :458).
- Config consumed:
  - `settings.workers.sweet_spots.kline_worker` → `"0:30"` (config.toml:`[workers.sweet_spots] kline_worker = "0:30"`).
  - `settings.workers.sweet_spots.window_minutes` → `5`.
  - `settings.database.wal_checkpoint_every_n_kline_ticks` → `50`.
  - `settings.database.wal_checkpoint_truncate_after_busy_count` → `3`.
  - `settings.database.path` (used to build `wal_path`).

## B.2.4 — What it WRITES

In-memory:
- `self._last_fetch: dict[str, float]` — key `"{symbol}:{tf.value}"`, value `time.time()` of last successful fetch (kline_worker.py:207).
- `self._last_tick_per_symbol: dict[str, int]` (kline_worker.py:248).
- `self._consecutive_fails: dict[str, int]` (line 264), `self._fail_streak_started: dict[str, float]` (line 266), `self._circuit_breaker_until: float` (line 308).
- `self._tick_count: int` (line 398), `self._consecutive_busy_checkpoints: int` (line 469/471).
- `src.core.cache_freshness.record_write("klines", "{symbol}:{tf}")` (kline_worker.py:213-214) — global cache freshness map.

DB writes happen INSIDE `MarketService.get_klines()` → `MarketRepository.save_klines()`. Insert SQL (`market_repo.py:103`):
```
INSERT OR IGNORE INTO klines
(symbol, timeframe, timestamp, open, high, low, close, volume, turnover)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

## B.2.5 — Cadence

- Sweet-spot wakeup: `0:30` within every 5-minute window (config). One tick per ≈ 5 min.
- Per-tick fetch loop: 50 symbols × 4 timeframes = up to 200 calls; each gated by per-(symbol,tf) cooldown:
  - M5: 60 s
  - H1: 60 s
  - H4: 300 s
  - D1: 3600 s
- DB writes: chunked `executemany` of 500 rows per chunk via `MarketRepository.save_klines()` (market_repo.py:122-133); yields the event loop between chunks. KLINE_SAVE_CHUNKED is emitted only when payload > 1 chunk.

## B.2.SPECIAL — DB write pattern

Exact `executemany` call (market_repo.py:127):
```python
await self._db.executemany(sql, params[i : i + chunk_size])
```
Surrounding loop (lines 122-134):
```python
chunk_size = self._kline_save_chunk_size       # default 500 from config
total = len(params)
chunks = (total + chunk_size - 1) // chunk_size
t0 = time.monotonic()
for i in range(0, total, chunk_size):
    await self._db.executemany(sql, params[i : i + chunk_size])
    if chunks > 1:
        await asyncio.sleep(0)
el_ms = (time.monotonic() - t0) * 1000.0
```

Rows per transaction: up to `kline_save_chunk_size` = 500 (config.toml:`[database] kline_save_chunk_size = 500`). Each `executemany` is one transaction under `DatabaseManager._lock`.

Lock hold time per chunk: NOT FOUND directly. The historical pre-chunk single executemany was logged at 12-20 s (per market_repo.py:75-80 docstring). Live KLINE_TICK_SUMMARY el_ms range below is the closest proxy.

`KLINE_SAVE_CHUNKED` events: 0 in the available log window — search `data/logs/workers.log` and `workers.2026-04-27_01-31-00_169356.log` returned no matches. Per save, payloads are at most 200 klines per (symbol,tf), under the 500 chunk threshold, so the multi-chunk path never fires.

`KLINE_WRITE_DONE` events (per spec request "5 actual KLINE_WRITE_DONE events"): NOT FOUND — searched for that literal tag in the codebase (`grep -rn KLINE_WRITE_DONE src`) and logs; the worker emits `KLINE_FETCH` and `KLINE_TICK_SUMMARY`, not `KLINE_WRITE_DONE`. The closest available event is `KLINE_TICK_SUMMARY`. Last 5:
```
2026-04-27 22:30:41.451 | KLINE_TICK_SUMMARY | universe=50 fetched=20000 saved=20000 skipped=100 tf_split={5:10000,60:10000,240:0,D:0} errors=0 el=11444ms drift_ms=1
2026-04-27 22:35:44.687 | KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 skipped=50 tf_split={5:10000,60:10000,240:9997,D:0} errors=0 el=14680ms drift_ms=1
2026-04-27 22:40:46.374 | KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 skipped=50 tf_split={5:10000,60:10000,240:9997,D:0} errors=0 el=16367ms drift_ms=2
2026-04-27 22:45:40.440 | KLINE_TICK_SUMMARY | universe=50 fetched=20000 saved=20000 skipped=100 tf_split={5:10000,60:10000,240:0,D:0} errors=0 el=10433ms drift_ms=1
2026-04-27 22:55:51.235 | KLINE_TICK_SUMMARY | universe=50 fetched=39539 saved=39539 skipped=0 tf_split={5:10000,60:10000,240:9997,D:9542} errors=0 el=21230ms drift_ms=0
```

`LAYER1A_TICK_DONE | sub=kline_worker` events:
```
2026-04-27 22:45:40.441 | LAYER1A_TICK_DONE | sub=kline_worker elapsed_ms=10440 drift_ms=1
2026-04-27 22:55:51.236 | LAYER1A_TICK_DONE | sub=kline_worker elapsed_ms=21235 drift_ms=0
```

## B.2.SPECIAL2 — Multi-timeframe schedule

- Schedule defined at `src/workers/kline_worker.py:32-37` as `TIMEFRAME_SCHEDULE = { TimeFrame.M5: 60, TimeFrame.H1: 60, TimeFrame.H4: 300, TimeFrame.D1: 3600 }` — units are seconds-cooldown between fetches.
- Schedule enforced at kline_worker.py:185-192 (loop) using `if now - last < min_interval: skipped_cooldown += 1; continue`.
- Sweet-spot wakeup is ONE per 5-min window at offset `0:30`, so:
  - M5 (60 s cooldown) → fires every wakeup (each wakeup is ≥ 300 s after the prior).
  - H1 (60 s cooldown) → fires every wakeup.
  - H4 (300 s cooldown) → fires every wakeup.
  - D1 (3600 s cooldown) → fires roughly every 12 wakeups (≈ 1 h).
- Per-(symbol,tf) cooldowns are independent — `cache_key = f"{symbol}:{timeframe.value}"`.

Live evidence — per-tick `tf_split={5:N,60:N,240:N,D:N}` from KLINE_TICK_SUMMARY:
- 22:10:47 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:15:48 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:20:41 → tf_split={5:10000,60:10000,240:0,D:0}    (H4 skipped — 300 s cooldown not yet expired since 22:15)
- 22:25:51 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:30:41 → tf_split={5:10000,60:10000,240:0,D:0}    (H4 skipped)
- 22:35:44 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:40:46 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:45:40 → tf_split={5:10000,60:10000,240:0,D:0}    (H4 skipped)
- 22:55:51 → tf_split={5:10000,60:10000,240:9997,D:9542}   (first D1 wakeup post-restart)

## B.2.SPECIAL3 — Quality reporting

Where `quality=ok` originates: kline_worker.py:124-144 — `_classify_fetch_quality(total, expected)`. Function logic verbatim above. The reason string `"ok"` is returned ONLY when:
1. `expected <= 0`, OR
2. `total / expected >= 0.9`.

Why session 22:27 reported `quality=ok` with daily TF "458 bars short":
- `expected_total` is computed (kline_worker.py:280) as `sum(per_symbol_expected.values())` where each `(symbol, timeframe)` cooldown-non-skipped fetch contributes `+200` (kline_worker.py:197). On the 22:25:51 tick, `KLINE_FETCH | klines=29997 expected=30000` indicates 50 symbols × 3 timeframes (M5+H1+H4) × 200 = 30,000 expected; the 3-bar shortfall = 29997/30000 = 99.99%, well above 0.9 → quality "ok".
- D1 was NOT in `per_symbol_expected` for that tick because the 3,600 s D1 cooldown hadn't elapsed since the previous D1 fetch — the D1 row was filtered out by `if now - last < min_interval: skipped_cooldown += 1; continue` at kline_worker.py:190 BEFORE the `+200` increment at line 197. Result: D1's missing bars are not counted toward `expected_total` on the ticks where D1 is in cooldown, so `quality=ok` is reported even when the on-disk D1 series is short.
- The shortfall flagged by the operator (458 bars short on D1) reflects ON-DISK kline rows, not the per-tick fetch result. The quality classifier in this worker is fetch-vs-expected for the current tick only; it has no awareness of historical row deficits.
- Separate freshness instrumentation does exist: `KLINE_WRITE_LAG` (threshold 360 s, M5 only) and `KLINE_FRESHNESS_WARN` (threshold 600 s, M5 only) at kline_worker.py:357-388. Neither covers D1.

## B.2.6 — Live measurements

Last 10 KLINE_FETCH events (verbatim):
```
2026-04-27 22:10:47.235 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=17234ms
2026-04-27 22:15:48.534 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=18532ms
2026-04-27 22:20:41.401 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11399ms
2026-04-27 22:25:51.364 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=21363ms
2026-04-27 22:30:41.445 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11444ms
2026-04-27 22:35:44.682 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=14680ms
2026-04-27 22:40:46.369 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=16367ms
2026-04-27 22:45:40.434 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11444ms
2026-04-27 22:55:51.231 | KLINE_FETCH | klines=39539 expected=40000 symbols=50 quality=ok errors=0 el=21230ms
```
el_ms range across these 9 ticks: 11,399 - 21,363 ms (median ≈ 14,700 ms).

## B.2.7 — Failure modes (last 24h)

Search results across `workers.log` + `workers.2026-04-27_01-31-00_169356.log`:

| Tag | Count | Source |
|-----|------:|--------|
| `KLINE_FETCH_FAIL` | 0 | kline_worker.py:243 |
| `KLINE_STRAGGLER` | 0 | kline_worker.py:269 |
| `KLINE_FRESHNESS_WARN` | 0 | kline_worker.py:362, :383 |
| `KLINE_WRITE_LAG` | 0 | kline_worker.py:371 |
| `KLINE_GAP` | 0 | kline_worker.py:301 |
| `KLINE_CIRCUIT_BREAKER` | 0 | kline_worker.py:309 |
| `KLINE_FRESHNESS_SKIP` | 0 | kline_worker.py:389 (DEBUG) |
| `KLINE_UNIVERSE_EMPTY` | 0 | kline_worker.py:166 |
| `WAL_CHECKPOINT_ERR` | 0 | kline_worker.py:452 |
| `WAL_CHECKPOINT_SCHEDULED` | 0 | kline_worker.py:475 |
| `WAL_CHECKPOINT_ESCALATE` | 0 | kline_worker.py:489 |

GAP: log retention only covers ≈ 1 hour 20 min (22:10-22:59 in current `workers.log`) plus an older session log; older 24-h window not present in `data/logs/`.

## B.2.8 — Dependencies (consumers)

Direct attribute consumers of `kline_worker`:
- `src/workers/manager.py:954-956` — instantiation: `_kline_worker = KlineWorker(s, db, self._services["market"], scanner=_scanner_ref); self._services["kline_worker"] = _kline_worker`.
- `is_circuit_open()` is consumed by `strategy_worker` (line 147 docstring claim — verified by `grep "is_circuit_open"` returning that worker as caller).

Indirect consumers (via `klines` table):
- `src/database/repositories/market_repo.py:158` — `MarketRepository.get_klines(symbol, tf, limit)` is the canonical read path. Callers include strategy_worker, structure_worker, regime_worker, signal_worker (TACache).
- Any code path that runs TA reads `klines` rows. Because of the `INSERT OR IGNORE` semantics, only NEW rows are added — the 200-row repeated fetches per tick are largely no-ops at the DB level.

Indirect consumers of cache freshness:
- `src/core/cache_freshness.record_write("klines", "{sym}:{tf}")` is called at kline_worker.py:214 — consumed by `src/telegram/handlers/system.py:225` (per grep) and any caller of `cache_freshness` singleton.


================================================================================
FILE: B3_altdata_worker.md
================================================================================

# B3 — AltDataWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.3.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/altdata_worker.py`
- Size: 11,457 bytes
- Lines of code: 274
- Last modified: 2026-04-27 08:49:18 UTC

## B.3.2 — Public methods (signatures + tick body)

Class declaration (line 30): `class AltDataWorker(SweetSpotWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 52).

### `__init__` (line 54)
```python
def __init__(self, settings, db, fear_greed, funding, oi_tracker, onchain):
    super().__init__(
        name="altdata_worker",
        sweet_spot=settings.workers.sweet_spots.altdata.funding_rates,
        settings=settings, db=db,
        window_minutes=settings.workers.sweet_spots.window_minutes,
    )
    self.fear_greed = fear_greed
    self.funding = funding
    self.oi_tracker = oi_tracker
    self.onchain = onchain
    self.symbols: list[str] = list(settings.universe.watch_list)
    self._scanner = None
    self._next_oi_mono: float = 0.0
    self._next_fg_mono: float = 0.0
    self._oi_interval_s: float = float(
        settings.workers.sweet_spots.altdata.open_interest_minutes * 60
    )
    self._fg_interval_s: float = float(
        settings.workers.sweet_spots.altdata.fear_greed_minutes * 60
    )
    self._funding_cache: dict[str, float] = {}
```

### `tick()` (line 92) — full body verbatim
```python
async def tick(self) -> None:
    t0 = time.monotonic()
    universe = list(self.settings.universe.watch_list)
    if not universe:
        log.warning(
            f"ALTDATA_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
        )
        return
    self.symbols = universe

    fire_funding = self.funding is not None
    fire_oi = self.oi_tracker is not None and t0 >= self._next_oi_mono
    fire_fg = self.fear_greed is not None and t0 >= self._next_fg_mono
    fire_onchain = self.onchain is not None  # piggybacks funding cadence

    async def _timed(label: str, coro):
        t_sub = time.monotonic()
        try:
            result = await coro
            return (label, (time.monotonic() - t_sub) * 1000.0, result, None)
        except Exception as e:
            return (label, (time.monotonic() - t_sub) * 1000.0, None, e)

    tasks: list = []
    if fire_funding:
        tasks.append(_timed("funding", self._fetch_funding_rates()))
    if fire_oi:
        tasks.append(_timed("oi", self._fetch_open_interest()))
    if fire_fg:
        tasks.append(_timed("fear_greed", self._fetch_fear_greed()))
    if fire_onchain:
        tasks.append(_timed("onchain", self._fetch_onchain()))

    if not tasks:
        log.warning("AltData worker: no sources due this tick")
        return

    results = await asyncio.gather(*tasks)

    fg_val = None
    funding_count = 0
    oi_count = 0
    funding_el_ms = 0.0
    oi_el_ms = 0.0
    fg_el_ms = 0.0
    onchain_el_ms = 0.0

    gather_el_ms = (time.monotonic() - t0) * 1000

    for label, sub_el_ms, result, err in results:
        if err is not None:
            log.warning(
                f"ALTDATA_SOURCE_FAIL | src={label} el_ms={sub_el_ms:.0f} "
                f"err={str(err)[:120]} | {ctx()}"
            )
            if label == "funding":
                funding_el_ms = sub_el_ms
            elif label == "oi":
                oi_el_ms = sub_el_ms
            elif label == "fear_greed":
                fg_el_ms = sub_el_ms
            elif label == "onchain":
                onchain_el_ms = sub_el_ms
            continue
        if label == "fear_greed" and result:
            fg_val = result.value
            fg_el_ms = sub_el_ms
        elif label == "funding" and isinstance(result, list):
            funding_count = len(result)
            funding_el_ms = sub_el_ms
            for fr in result:
                sym = getattr(fr, "symbol", None)
                rate = getattr(fr, "funding_rate", None)
                if sym and rate is not None:
                    try:
                        self._funding_cache[sym] = float(rate)
                    except (TypeError, ValueError):
                        continue
        elif label == "oi" and isinstance(result, list):
            oi_count = len(result)
            oi_el_ms = sub_el_ms
        elif label == "onchain":
            onchain_el_ms = sub_el_ms

    now_mono = time.monotonic()
    if fire_oi:
        self._next_oi_mono = now_mono + self._oi_interval_s
    if fire_fg:
        self._next_fg_mono = now_mono + self._fg_interval_s

    if fire_funding:
        log.info(
            f"ALTDATA_FUNDING_TICK | universe={len(universe)} "
            f"fetched={funding_count} cached_size={len(self._funding_cache)} "
            f"el={funding_el_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )
    if fire_oi:
        log.info(
            f"ALTDATA_OI_TICK | universe={len(universe)} "
            f"fetched={oi_count} el={oi_el_ms:.0f}ms "
            f"next_in_s={self._oi_interval_s:.0f} | {ctx()}"
        )
    if fire_fg:
        log.info(
            f"ALTDATA_FG_TICK | value={fg_val} el={fg_el_ms:.0f}ms "
            f"next_in_s={self._fg_interval_s:.0f} | {ctx()}"
        )

    log.info(
        f"ALTDATA | fg={fg_val} funding={funding_count} oi={oi_count} "
        f"el={gather_el_ms:.0f}ms | {ctx()}"
    )

    ran = ",".join(
        label
        for label, fired in (
            ("funding", fire_funding),
            ("oi", fire_oi),
            ("fear_greed", fire_fg),
            ("onchain", fire_onchain),
        )
        if fired
    )
    log.info(
        f"ALTDATA_TICK_DONE | funding_ms={funding_el_ms:.0f} "
        f"oi_ms={oi_el_ms:.0f} fg_ms={fg_el_ms:.0f} "
        f"onchain_ms={onchain_el_ms:.0f} total_ms={gather_el_ms:.0f} "
        f"ran=[{ran}] | {ctx()}"
    )
```

### Public accessor `get_funding(coin) -> float | None` (line 254)
```python
def get_funding(self, coin: str) -> float | None:
    return self._funding_cache.get(coin)
```

### Private fetchers (lines 264-274)
```python
async def _fetch_fear_greed(self):
    return await self.fear_greed.fetch_current()

async def _fetch_funding_rates(self):
    return await self.funding.fetch_current_rates(self.symbols)

async def _fetch_open_interest(self):
    return await self.oi_tracker.fetch_current(self.symbols)

async def _fetch_onchain(self):
    return await self.onchain.get_global_metrics()
```

## B.3.3 — What it READS

Sub-feed clients are constructor-injected:
- `self.fear_greed: FearGreedClient | None` — wraps `https://api.alternative.me/fng/` (per `src/intelligence/altdata/fear_greed.py`).
- `self.funding: FundingRateTracker | None` — wraps Bybit funding-rate REST (per `src/intelligence/altdata/funding_rates.py:31` `fetch_current_rates`).
- `self.oi_tracker: OpenInterestTracker | None` — wraps Bybit `get_open_interest` (per `src/intelligence/altdata/open_interest.py:28-44`).
- `self.onchain: OnChainClient | None` — wraps CoinGecko `get_global_metrics` (per `src/intelligence/altdata/onchain.py:32`).

Universe: `settings.universe.watch_list` (50 coins) re-read every tick (altdata_worker.py:102).

Config consumed:
- `settings.workers.sweet_spots.altdata.funding_rates` → `"1:45"` (config.toml).
- `settings.workers.sweet_spots.altdata.open_interest_minutes` → `5`.
- `settings.workers.sweet_spots.altdata.fear_greed_minutes` → `60`.
- `settings.workers.sweet_spots.window_minutes` → `5`.

## B.3.4 — What it WRITES

In-memory:
- `self._funding_cache: dict[str, float]` — key = symbol, value = funding_rate float (altdata_worker.py:90, written :190).
- `self._next_oi_mono: float` — monotonic deadline for next OI fire (line 80, advanced :204).
- `self._next_fg_mono: float` — monotonic deadline for next F&G fire (line 81, advanced :206).
- `self.symbols: list[str]` — refreshed every tick from watch_list (line 108).

DB writes (delegated to sub-clients):
- `OpenInterestTracker.fetch_current()` calls `self._repo.save_open_interest(symbol, current_oi)` at `src/intelligence/altdata/open_interest.py:58` — writes `open_interest` table (verified via altdata_repo).
- `FearGreedClient.fetch_current()` writes Fear & Greed records via its own repo (per fear_greed.py:39 + altdata_repo).
- `FundingRateTracker.fetch_current_rates()` writes via altdata_repo (used by `get_funding_rates` reader at altdata_repo.py:99).
- `OnChainClient.get_global_metrics()` returns dict; persistence is delegated to its repo path.

## B.3.5 — Cadence

- Sweet-spot wakeup: `1:45` within every 5-min window.
- Per-source cadences:
  - **funding**: every wakeup (no deadline gate; `fire_funding = self.funding is not None`).
  - **oi**: every `_oi_interval_s = 5 * 60 = 300 s` via `t0 >= self._next_oi_mono`.
  - **fear_greed**: every `_fg_interval_s = 60 * 60 = 3600 s` via `t0 >= self._next_fg_mono`.
  - **onchain**: piggybacks funding (every wakeup; only gated by `self.onchain is not None`).
- Initial deadlines = 0.0 (constructor, lines 80-81), so first tick fires every source once.

## B.3.SPECIAL — Sub-feed schedule (live verification)

Verbatim ALTDATA_TICK_DONE events (last 7 in `data/logs/workers.log`):
```
2026-04-27 22:21:52.290 | ALTDATA_TICK_DONE | funding_ms=7269 oi_ms=0    fg_ms=0    onchain_ms=2763 total_ms=7273 ran=[funding,onchain]
2026-04-27 22:26:55.155 | ALTDATA_TICK_DONE | funding_ms=10137 oi_ms=9940 fg_ms=0    onchain_ms=2661 total_ms=10137 ran=[funding,oi,onchain]
2026-04-27 22:31:50.191 | ALTDATA_TICK_DONE | funding_ms=5190  oi_ms=0    fg_ms=0    onchain_ms=2670 total_ms=5190  ran=[funding,onchain]
2026-04-27 22:36:54.158 | ALTDATA_TICK_DONE | funding_ms=8444  oi_ms=9155 fg_ms=0    onchain_ms=2678 total_ms=9156  ran=[funding,oi,onchain]
2026-04-27 22:41:50.061 | ALTDATA_TICK_DONE | funding_ms=5059  oi_ms=0    fg_ms=0    onchain_ms=2637 total_ms=5059  ran=[funding,onchain]
2026-04-27 22:56:54.192 | ALTDATA_TICK_DONE | funding_ms=9020  oi_ms=9190 fg_ms=976  onchain_ms=2669 total_ms=9190  ran=[funding,oi,fear_greed,onchain]
```

Cadence verified: tick interval ≈ 5 min (sweet-spot wakeup). OI fires every 2nd tick (300 s ≈ 5 min cadence with skip). F&G appears once across the captured window (3600 s cadence). Funding+onchain fire every tick.

## B.3.SPECIAL2 — Why some ticks have only 3 of 4 sub-feeds

The "F&G missing" pattern (`ran=[funding,oi,onchain]`) arises from the deadline gate at altdata_worker.py:113:
```
fire_fg = self.fear_greed is not None and t0 >= self._next_fg_mono
```
After F&G fires, line 206 advances the deadline:
```
if fire_fg:
    self._next_fg_mono = now_mono + self._fg_interval_s    # +3600 s
```
With `_fg_interval_s = 3600 s` and the 5-min sweet-spot wakeup, F&G fires roughly once every 12 wakeups. All other 11 wakeups satisfy `t0 < self._next_fg_mono` and the F&G branch is omitted from `tasks`. This is by design (config: `fear_greed_minutes = 60`).

Same mechanism for OI with a 300 s deadline (line 204). Live observation confirms: 22:21:52 had no OI (skipped because `_next_oi_mono` was set on the previous fire); 22:26:55 had OI (deadline expired); 22:31:50 had no OI again. Ratio matches the 5-min cooldown.

ALTDATA_FG_TICK observed (line 222) verbatim:
```
2026-04-27 22:56:54.192 | ALTDATA_FG_TICK | value=47 el=976ms next_in_s=3600 | no_ctx
```
Single F&G fire in the captured window with `value=47`.

## B.3.6 — Live measurements

Funding cache size: `cached_size=50` per ALTDATA_FUNDING_TICK (line 213 in code; observed across all post-22:26 fires).

ALTDATA_FUNDING_TICK examples:
```
2026-04-27 22:26:55.155 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=10137ms drift_ms=18
2026-04-27 22:31:50.191 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=5190ms drift_ms=1
2026-04-27 22:36:54.158 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=8444ms drift_ms=2
2026-04-27 22:41:50.061 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=5059ms drift_ms=1
2026-04-27 22:56:54.192 | ALTDATA_FUNDING_TICK | universe=50 fetched=50 cached_size=50 el=9020ms drift_ms=1
```

ALTDATA_OI_TICK examples:
```
2026-04-27 22:26:55.156 | ALTDATA_OI_TICK | universe=50 fetched=50 el=9940ms next_in_s=300
2026-04-27 22:36:54.158 | ALTDATA_OI_TICK | universe=50 fetched=50 el=9155ms next_in_s=300
2026-04-27 22:56:54.192 | ALTDATA_OI_TICK | universe=50 fetched=50 el=9190ms next_in_s=300
```

LAYER1A_TICK_DONE for altdata:
```
2026-04-27 22:41:50.061 | LAYER1A_TICK_DONE | sub=altdata_worker elapsed_ms=5060 drift_ms=1
2026-04-27 22:56:54.193 | LAYER1A_TICK_DONE | sub=altdata_worker elapsed_ms=9191 drift_ms=1
```

## B.3.7 — Failure modes (last 24h)

| Tag | Count | Source |
|-----|------:|--------|
| `ALTDATA_SOURCE_FAIL` | 0 | altdata_worker.py:163 |
| `ALTDATA_UNIVERSE_EMPTY` | 0 | altdata_worker.py:105 |
| `AltData worker: no sources due this tick` | 0 | altdata_worker.py:142 |

GAP: log retention covers ≈ 1 hour 20 min only. F&G shows `value=None` in the legacy ALTDATA aggregate line on the 5 ticks where it didn't fire (e.g. `ALTDATA | fg=None funding=50 oi=50 el=...`) — the `None` is by-design (only assigned when the fear_greed task fired in this cycle), not a fetch failure.

## B.3.8 — Dependencies (consumers)

`get_funding(coin)` consumers:
- `src/workers/scanner_worker.py:154-164` — `_get_funding_strength()` reads `services.get("altdata_worker").get_funding(coin)`.
- `src/workers/scanner_worker.py:280-282` — direction/blocker check uses `adw.get_funding(symbol)`.
- `src/workers/scanner_worker.py:424-428` — composite scoring path also reads `adw.get_funding(symbol)`.

`altdata_worker` itself is registered into `self._services["altdata_worker"]` at `src/workers/manager.py:972` so any worker with `self.services` access can `services.get("altdata_worker")`.

DB-table consumers (indirect, via repos):
- `funding_rates` table → `AltDataRepository.get_funding_rates(symbol, hours)` at altdata_repo.py:99 — used by `funding_rates.py:132` (`get_funding_history`).
- `open_interest` table → `AltDataRepository.save_open_interest` (open_interest.py:58 caller).
- `fear_greed` table → `AltDataRepository.get_latest_fear_greed()` — read by `src/intelligence/sentiment/aggregator.py:131` (`fg = await self._altdata_repo.get_latest_fear_greed()`).
- `OnChainClient.get_global_metrics()` returns global market data (BTC dominance, total market cap) — consumed wherever onchain context is read.

`_funding_cache` is a private dict; only the `get_funding()` accessor is public.


================================================================================
FILE: B4_news_worker.md
================================================================================

# B4 — NewsWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.4.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/news_worker.py`
- Size: 2,870 bytes
- Lines of code: 75
- Last modified: 2026-04-27 04:32:34 UTC

## B.4.2 — Public methods (signatures + tick body)

Class declaration (line 17): `class NewsWorker(BaseWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 28).

### `__init__` (line 30)
```python
def __init__(
    self,
    settings: Settings,
    db: DatabaseManager,
    news_service: NewsService,
    calendar_service: CalendarService | None = None,
) -> None:
    super().__init__(
        name="news_worker",
        interval_seconds=float(settings.workers.news_interval),
        settings=settings,
        db=db,
    )
    self.news_service = news_service
    self.calendar_service = calendar_service
    self._calendar_tick_count = 0
```

### `tick()` (line 47) — full body verbatim
```python
async def tick(self) -> None:
    """Fetch latest news and periodically update calendar."""
    t0 = time.monotonic()
    calendar_updated = False
    articles = await self.news_service.fetch_latest_news()

    self._calendar_tick_count += 1
    if self.calendar_service and self._calendar_tick_count >= 30:
        try:
            events = await self.calendar_service.get_upcoming_events()
            log.info("News worker: updated economic calendar ({n} events)", n=len(events))
            calendar_updated = True
        except Exception as e:
            log.warning("Calendar update failed: {err}", err=str(e))
        self._calendar_tick_count = 0

    el_ms = (time.monotonic() - t0) * 1000
    log.info(
        f"NEWS_TICK_SUMMARY | new={len(articles)} "
        f"calendar_updated={'Y' if calendar_updated else 'N'} "
        f"el={el_ms:.0f}ms | {ctx()}"
    )
    log.info(f"NEWS_FETCH | total={len(articles)} | {ctx()}")
    log.info("News worker: fetched {n} new articles", n=len(articles))
```

## B.4.3 — What it READS

- Finnhub crypto news via `NewsService.fetch_latest_news(category="crypto")` (default, news_service.py:46-67). The Finnhub HTTP call is `self._finnhub.get_general_news(category=category)` (news_service.py:67).
- Economic calendar via `CalendarService.get_upcoming_events()` every 30 ticks (news_worker.py:60).
- DB reads inside `news_service.fetch_latest_news`:
  - `await self._news_repo.headline_exists(headline)` (news_service.py:89) — dedup check.
- Config consumed:
  - `settings.workers.news_interval` → 300 s (config.toml:`[workers] news_interval = 300`) — tick cadence.
  - `settings.finnhub.max_articles_per_fetch` → 50 (config.toml:`[finnhub] max_articles_per_fetch = 50`).
  - `settings.finnhub.news_categories = ["crypto", "general"]` (only first/default consumed by tick).
  - `settings.finnhub.rate_limit_per_minute = 60`.

## B.4.4 — What it WRITES

In-memory:
- `self._calendar_tick_count: int` — counter (news_worker.py:45).

DB tables (via NewsService → NewsRepository):
- `news_articles` — `INSERT OR IGNORE INTO news_articles (id, headline, source, url, summary, sentiment_score, symbols, category, published_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)` at news_repo.py:84-101.
- `symbols` column stores JSON-encoded list of system tickers (e.g. `["BTCUSDT","ETHUSDT"]`) populated by `news_service.extract_symbols(text)` (news_service.py:99-100).

Schema:
```
CREATE TABLE news_articles (
    id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    sentiment_score REAL NOT NULL DEFAULT 0,
    symbols TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT (datetime('now')),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_news_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_symbols ON news_articles(symbols);
```

## B.4.5 — Cadence

- Fixed-interval BaseWorker tick: `interval_seconds = settings.workers.news_interval = 300` (5 min).
- Per `news_service.fetch_latest_news`: requests Finnhub once (max 50 articles considered), filters by 24-h cutoff, dedup against DB, persists new rows.
- Calendar update: every 30 ticks (= 30 * 5 min = 150 min ≈ 2.5 h).

## B.4.SPECIAL — Article→symbol matching algorithm

Implementation lives in `src/intelligence/news/news_service.py` at lines 202-227 (`extract_symbols`):
```python
def extract_symbols(text: str) -> list[str]:
    lower = text.lower()
    found: set[str] = set()
    for name, symbol in SYMBOL_EXTRACTION_MAP.items():
        if len(name) <= 4:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, lower):
                found.add(symbol)
        else:
            if name in lower:
                found.add(symbol)
    return sorted(found)
```
Called from `NewsService.fetch_latest_news` (news_service.py:100):
```python
symbols = extract_symbols(text)
```
where `text = f"{headline} {summary}"` (news_service.py:94).

`SYMBOL_EXTRACTION_MAP` is defined verbatim in `src/intelligence/signals/signal_models.py:75-98`:
```python
SYMBOL_EXTRACTION_MAP: dict[str, str] = {
    # Full names
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "avalanche": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "chainlink": "LINKUSDT",
    "polygon": "MATICUSDT",
    # Tickers (uppercase handled by caller)
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "ada": "ADAUSDT",
    "avax": "AVAXUSDT",
    "dot": "DOTUSDT",
    "link": "LINKUSDT",
    "matic": "MATICUSDT",
}
```

KEY OBSERVATION: the map covers **only 10 base assets** (BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, DOT, LINK, MATIC) — i.e. a strict subset of the 50-coin watch_list. Coins outside this map (BNB, ARB, NEAR, ATOM, INJ, RENDER, ONDO, ENA, PYTH, SEI, AERO, RUNE, GALA, MANA, SAND, AXS, LDO, CRV, DYDX, AAVE, ICP, IMX, HBAR, HYPE, GMT, FIL, MNT, MON, SKR, PLUME, EGLD, ALGO, BSB, KAT, HYPER, ORCA, BLUR, OP, APT, LTC, BCH, ALICE) are NEVER tagged on news rows by `news_worker` even when the article mentions them. There is a downstream fallback at `NewsRepository.get_by_symbol` (news_repo.py:121-160 → `extract_base_asset`) that retries with the stripped base asset when the literal `LIKE %BTCUSDT%` returns 0 rows, but that fallback can only succeed if the article was tagged in the first place using one of the map's keys.

## B.4.6 — Live measurements

Last 5 NEWS_FETCH / NEWS_TICK_SUMMARY events:
```
2026-04-27 22:33:13.351 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:33:13.352 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1065ms
2026-04-27 22:38:14.415 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:38:14.416 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1062ms
2026-04-27 22:43:15.521 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=3 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:43:15.522 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1105ms
2026-04-27 22:53:46.049 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=2 skipped_old=1 skipped_no_headline=0 skipped_dedup=47
2026-04-27 22:53:46.049 | NEWS_TICK_SUMMARY | new=2 calendar_updated=N el=5085ms
2026-04-27 22:58:47.116 | FINNHUB_COVERAGE | category=crypto returned=96 considered=50 new=0 skipped_old=1 skipped_no_headline=0 skipped_dedup=49
2026-04-27 22:58:47.116 | NEWS_TICK_SUMMARY | new=0 calendar_updated=N el=1064ms
```

LAYER1A_TICK_DONE for news:
```
2026-04-27 22:43:15.523 | LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=1105 interval_s=300.0
2026-04-27 22:53:46.050 | LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=5086 interval_s=300.0
2026-04-27 22:58:47.116 | LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=1064 interval_s=300.0
```

### Article distribution per coin (last 1h, DB snapshot)

Query:
```sql
SELECT symbols, COUNT(*) FROM news_articles
WHERE published_at > datetime('now','-1 hour')
GROUP BY symbols ORDER BY 2 DESC;
```
Results (50 rows total, snapshot 2026-04-27 ≈ 22:59 UTC):
```
["BTCUSDT"]                                                          | 20
[]                                                                   | 20  (no symbols matched)
["ETHUSDT"]                                                          | 6
["XRPUSDT"]                                                          | 2
["ADAUSDT","BTCUSDT","DOGEUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]       | 1
["SOLUSDT"]                                                          | 1
```

Per-coin matched-article totals (counted by symbols-list membership, last 1h):
- BTCUSDT: 21 (20 + 1 from joint row)
- ETHUSDT: 7
- XRPUSDT: 3
- SOLUSDT: 2
- ADAUSDT: 1
- DOGEUSDT: 1
- All other 44 watch_list coins (BNB, ARB, NEAR, ATOM, INJ, RENDER, ONDO, ENA, PYTH, SEI, AERO, RUNE, GALA, MANA, SAND, AXS, LDO, CRV, DYDX, AAVE, ICP, IMX, HBAR, HYPE, GMT, FIL, MNT, MON, SKR, PLUME, EGLD, ALGO, BSB, KAT, HYPER, ORCA, BLUR, OP, APT, LTC, BCH, ALICE, AVAXUSDT, LINKUSDT): **0**.

20 articles in last 1h were tagged with empty symbols list `[]` — sample headlines from DB snapshot:
- "Canada advances bill to ban crypto political donations"
- "Tennessee crypto kiosk ban set to go into effect July 1"
- "Western Union eyeing stablecoin launch to settle global transactions without SWIFT, CEO says"
- "EU sanctions target Russian crypto exchanges, stablecoins and CBDC"
- "Curve founder pitches market-based fix for $700K bad debt in contrast to Aave bailout"  (mentions Aave/Curve but not in EXTRACTION_MAP)
- "Industry leaders are pouring hundreds of millions into a rescue plan for Aave users after massive crypto hack"  (mentions Aave but not mapped)
- "MiCA has made euro stablecoins safe but weak, new report argues"
- ...

Total `news_articles` row count: 1,226 (oldest 2026-03-29T02:24:30+00:00, newest 2026-04-27T21:50:17+00:00).

## B.4.7 — Failure modes (last 24h)

| Tag | Count | Source |
|-----|------:|--------|
| `Calendar update failed:` | 0 | news_worker.py:64 |
| `FINNHUB_*` ERROR / FAIL | 0 | (no error tag emitted by news_service) |

Coverage anomaly (NOT a failure but a structural data gap):
- Every observed FINNHUB_COVERAGE shows `returned=96 considered=50 skipped_dedup=47` — Finnhub's response contains 96 articles but only 50 are considered (capped by `max_articles_per_fetch`), and 47/50 are typically already persisted. New rows per tick: 0 - 2.

## B.4.8 — Dependencies (consumers)

- `src/database/repositories/news_repo.py:121` — `NewsRepository.get_by_symbol(symbol, hours, limit)` is the canonical reader. Caller list:
  - `src/intelligence/sentiment/aggregator.py:115` — `news_articles = await self._news_repo.get_by_symbol(symbol, hours=hours)` (`SentimentAggregator.aggregate_for_symbol`).
  - `src/intelligence/news/news_service.py:142` — `NewsService.get_news_for_symbol`.
- Aggregator further consumes `a.sentiment_score` from each row (aggregator.py:116 `news_scores = [a.sentiment_score for a in news_articles]`).
- `src/intelligence/news/news_service.py:168` — `get_news_summary(hours)` reads `news_articles` table for global summary.
- `news_service.search` (news_service.py:155) → `news_repo.search(keyword)`.
- Telegram + MCP tool exposures route through these same repo methods.


================================================================================
FILE: B5_sentiment_aggregator.md
================================================================================

# B5 — SentimentAggregator

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.5.1 — Where it lives

- File path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/intelligence/sentiment/aggregator.py`
- Class: `SentimentAggregator` (line 28)
- It is **NOT a worker**. It is a service singleton injected into:
  - `SignalGenerator.__init__` at `src/intelligence/signals/signal_generator.py:52` (`self._aggregator = aggregator`).
  - The signal worker tick path: `src/workers/signal_worker.py:96-102` calls `await self.aggregator.aggregate_for_symbol(symbol)` per coin.
  - `SignalGenerator.generate_signal` at `signal_generator.py:85` calls `sentiment = await self._aggregator.aggregate_for_symbol(symbol)`.
  - MCP tool `src/mcp/tools/sentiment_tools.py:80` (`aggregator.aggregate_for_symbol(args["symbol"], args.get("hours", 24))`).

When does it run: each `signal_worker.tick()`, which is `SweetSpotWorker` triggered at `settings.workers.sweet_spots.signal_worker = "1:00"` per 5-min window (config.toml). Per tick it iterates all 50 watch_list symbols and calls `aggregate_for_symbol(sym)` twice per symbol — once explicit at signal_worker.py:98 ("Sentiment aggregation only") and once implicit inside `signal_generator.generate_signal` at signal_generator.py:85. Therefore each symbol triggers 2 calls per cycle (the second is short-circuited by the zero-coverage cache for unknown symbols).

## B.5.2 — Aggregation logic

### Constants (aggregator.py:18-30)
```python
WEIGHT_NEWS = 0.35
WEIGHT_REDDIT = 0.30
WEIGHT_FEAR_GREED = 0.20
WEIGHT_MOMENTUM = 0.15
_ZERO_COVERAGE_TTL_SECONDS: float = 30 * 60  # 30 minutes
```

### `aggregate_for_symbol(symbol, hours=24)` flow (aggregator.py:88-280)
1. **Zero-coverage cache** (lines 100-110): if `symbol in self._unknown_cache` and not expired → emit `SENT_UNKNOWN_CACHE_HIT` and return cached dict.
2. **News lookup** (line 115): `news_articles = await self._news_repo.get_by_symbol(symbol, hours=hours)` with `hours=24` default.
3. **Reddit lookup** (line 120): `reddit_posts = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=hours)`.
4. **Fear & Greed** (line 125): `fg = await self._altdata_repo.get_latest_fear_greed()`; `fg_normalized = (fg.value - 50) / 50.0`.
5. **Momentum** (line 130): `recent_sentiment = await self._sentiment_repo.get_sentiment_for_symbol(symbol, limit=1)`. `momentum = clamp(current_avg - prev_score, -1, 1)`.
6. **Weight rebalancing** for F&G extremes (lines 135-141):
   ```python
   fg_weight = WEIGHT_FEAR_GREED
   if fg_value < 20 or fg_value > 80:
       fg_weight = 0.60
   elif fg_value < 30 or fg_value > 70:
       fg_weight = 0.40
   ```
7. **Score formula** (lines 146-152):
   ```python
   overall = (
       news_avg * WEIGHT_NEWS * scale
       + reddit_avg * WEIGHT_REDDIT * scale
       + fg_normalized * fg_weight
       + momentum * WEIGHT_MOMENTUM * scale
   )
   overall = clamp(overall, -1.0, 1.0)
   ```
8. **No-data branch** (lines 156-220):
   ```python
   has_own_data = len(news_scores) > 0 or len(reddit_scores) > 0
   if not has_own_data:
       overall = 0.0
       ... fetch change_24h_pct from ticker_cache ...
       log.info(f"SENT_NEUTRAL | sym=... rsn=no_news_no_reddit fg=...")
       if self._reddit_intentionally_disabled:
           log.info(f"SENT_DEGRADED_MODE | sym=... reason=reddit_disabled ...")
       else:
           log.info(f"SENT_NO_DATA | sym=... matched_articles=0 matched_reddit=0 ...")
           log.info(f"SENT_UNKNOWN | sym=... rsn=no_news_no_reddit ...")
   ```
9. **Level mapping** (lines 224-227): `level = SentimentLevel.UNKNOWN if not has_own_data else self._scorer.score_to_level(overall)`.
10. **Persist** (line 247): `await self._sentiment_repo.save_aggregated_sentiment(result)`.
11. **Cache** the no-data verdict for 30 min (lines 274-279).

Lookup window: 24h default (per `aggregate_for_symbol(symbol, hours=24)` signature at line 88).

Threshold for "enough articles": NONE — any non-empty `news_scores` OR `reddit_scores` list flips `has_own_data` to True. There is no minimum-count threshold.

## B.5.3 — `aggregated_sentiment` schema and write format

DB schema (snapshot DB):
```sql
CREATE TABLE aggregated_sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    overall_score REAL NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT 'neutral',
    news_score REAL NOT NULL DEFAULT 0,
    news_count INTEGER NOT NULL DEFAULT 0,
    reddit_score REAL NOT NULL DEFAULT 0,
    reddit_count INTEGER NOT NULL DEFAULT 0,
    fear_greed_value INTEGER NOT NULL DEFAULT 50,
    momentum REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_agg_sentiment_symbol ON aggregated_sentiment(symbol, created_at DESC);
```

Insert SQL (sentiment_repo.py:127-143):
```sql
INSERT INTO aggregated_sentiment
(symbol, overall_score, level, news_score, news_count,
 reddit_score, reddit_count, fear_greed_value, momentum)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Cache key/value format (in-memory):
- Aggregator's `_unknown_cache: dict[str, tuple[float, dict]]` at aggregator.py:62. Key = symbol; value = `(monotonic_expires_at, result_dict)` with TTL 1800 s (30 min).

Sample of last 10 writes (DB snapshot, table `aggregated_sentiment`, ordered by id DESC):
```
id      | symbol     | overall | level   | news_s | n_n | reddit_s | n_r | fg | mom | created_at
336158  | ALICEUSDT  | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336157  | BCHUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336156  | LTCUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336155  | APTUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336154  | OPUSDT     | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336153  | BLURUSDT   | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336152  | ORCAUSDT   | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336151  | HYPERUSDT  | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336150  | KATUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
336149  | BSBUSDT    | 0.0     | unknown | 0.0    | 0   | 0.0      | 0   | 47 | 0.0 | 2026-04-27 22:26:03
```

Distribution last 1h (DB snapshot):
```
SELECT level, COUNT(*) FROM aggregated_sentiment WHERE created_at > datetime('now','-1 hour') GROUP BY level;
neutral | 72
unknown | 44
```

Distinct symbols last 1h: 50 (matches watch_list).

Total rows in `aggregated_sentiment`: 276,330.

## B.5.4 — Why most ticks emit SENT_UNKNOWN

### The structural cause: news article tagging covers only 10 base assets

Per `src/intelligence/signals/signal_models.py:75-98` (`SYMBOL_EXTRACTION_MAP`), only 10 keys produce a tag: BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, DOT, LINK, MATIC. Articles for the remaining 44 watch_list coins are NEVER stored with their symbol in `news_articles.symbols`, so `NewsRepository.get_by_symbol(symbol)` returns `[]` for those coins. Reddit is disabled (`config.toml:[reddit] enabled = false`, `client_id` empty). With both branches at 0, `has_own_data = False`, and the no-data branch fires `SENT_UNKNOWN` / `SENT_DEGRADED_MODE`.

### Per-symbol live SENT_AGG outputs (last cycle in workers.log, 22:25:59 - 22:26:03):

Non-zero news count (4 of 50 symbols):
```
22:26:00.068 | SENT_AGG | sym=ETHUSDT  score=-0.018 level=neutral news_n=7 reddit_n=0 fg=47
22:26:00.101 | SENT_AGG | sym=SOLUSDT  score=-0.010 level=neutral news_n=2 reddit_n=0 fg=47
22:26:00.310 | SENT_AGG | sym=XRPUSDT  score=-0.047 level=neutral news_n=3 reddit_n=0 fg=47
22:26:00.342 | SENT_AGG | sym=ADAUSDT  score=-0.010 level=neutral news_n=1 reddit_n=0 fg=47
22:26:00.376 | SENT_AGG | sym=DOGEUSDT score=-0.010 level=neutral news_n=1 reddit_n=0 fg=47
```
(BTCUSDT also non-zero — visible in earlier cycle).

Zero news count emit `level=unknown` (representative samples — verbatim):
```
22:26:03.123 | SENT_NEUTRAL | sym=BLURUSDT rsn=no_news_no_reddit fg=47 change_24h=-8.0376
22:26:03.124 | SENT_DEGRADED_MODE | sym=BLURUSDT reason=reddit_disabled fg=47 change_24h=-8.0376
22:26:03.126 | SENT_AGG | sym=BLURUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.126 | SENT_UNKNOWN_CACHE_HIT | sym=BLURUSDT
22:26:03.279 | SENT_NEUTRAL | sym=OPUSDT rsn=no_news_no_reddit fg=47 change_24h=-5.5362
22:26:03.280 | SENT_DEGRADED_MODE | sym=OPUSDT reason=reddit_disabled fg=47 change_24h=-5.5362
22:26:03.284 | SENT_AGG | sym=OPUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.318 | SENT_NEUTRAL | sym=LTCUSDT rsn=no_news_no_reddit fg=47 change_24h=None
22:26:03.319 | SENT_DEGRADED_MODE | sym=LTCUSDT reason=reddit_disabled fg=47 change_24h=None
22:26:03.320 | SENT_AGG | sym=LTCUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.333 | SENT_NEUTRAL | sym=BCHUSDT rsn=no_news_no_reddit fg=47 change_24h=None
22:26:03.341 | SENT_AGG | sym=BCHUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
22:26:03.354 | SENT_NEUTRAL | sym=ALICEUSDT rsn=no_news_no_reddit fg=47 change_24h=3.7545
22:26:03.360 | SENT_AGG | sym=ALICEUSDT score=0.000 level=unknown news_n=0 reddit_n=0 fg=47
```

For each of 5 zero-sentiment coins, the WHY:
- **BLURUSDT** — `news_articles.symbols` table contains 0 rows where the JSON list contains "BLURUSDT" or "BLUR" in the last 24 h (verified by DB snapshot showing only `BTCUSDT`, `ETHUSDT`, `XRPUSDT`, `SOLUSDT`, `ADAUSDT`, `DOGEUSDT`, `[]` in last 1h). Articles exist (20 untagged in the same window, plus articles mentioning BLUR for which the EXTRACTION_MAP has no key) but none are tagged with BLUR/BLURUSDT. Reddit is disabled. Aggregator returns score=0.0 / level=unknown.
- **OPUSDT** — same root cause; OP not in `SYMBOL_EXTRACTION_MAP`. Articles for "Optimism" rebrand exist but never tagged.
- **LTCUSDT** — Litecoin not in `SYMBOL_EXTRACTION_MAP`. Same outcome.
- **BCHUSDT** — Bitcoin Cash not in `SYMBOL_EXTRACTION_MAP`. Note: `change_24h=None` because `ticker_cache` had no row for BCHUSDT at lookup time (aggregator.py:165-166).
- **ALICEUSDT** — ALICE not in `SYMBOL_EXTRACTION_MAP`.

Aggregator init log (one-shot, captured 2026-04-27 22:53:27.037):
```
SENTIMENT_DEGRADED_MODE | reason=no_reddit source=fear_greed_only | no_ctx
```
Source: aggregator.py:78-83 (when `settings.reddit.client_id` is falsy, set `_reddit_intentionally_disabled = True` and log).

## B.5.5 — Reddit fallback gap

Reddit is disabled both at config (`config.toml:[reddit] enabled = false`) and via missing client_id. The aggregator detects this at `__init__` (aggregator.py:67-86) and stores `self._reddit_intentionally_disabled = True`.

Code path when `_reddit_intentionally_disabled`:
- The Reddit lookup at aggregator.py:120 still executes: `reddit_posts = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=hours)` — but `reddit_posts` is always `[]` because `reddit_worker` is registered but disabled (per project memory). So `reddit_avg = 0.0`.
- In the no-data branch (lines 207-220): if `_reddit_intentionally_disabled`, only `SENT_DEGRADED_MODE` is logged; otherwise both `SENT_NO_DATA` and back-compat `SENT_UNKNOWN` are logged.
- There is **no fallback substitute source** — no Twitter, no alternate Reddit credentials, no other social-sentiment provider wired in. With reddit off, the only signal is: news (when matched) + F&G (market-wide) + momentum.

Observable consequence:
- `SENT_BASEASSET_FALLBACK` (news_repo.py:155) provides a per-news lookup retry by stripping numeric prefixes/quote suffixes (e.g. `1000BONKUSDT → BONK`). Count in current logs = 0. The fallback only helps when articles WERE tagged with the base asset already; it does not retroactively tag untagged articles.
- `_unknown_cache` (TTL 1800 s) hides ~98 % of repeat lookups for zero-coverage symbols by emitting `SENT_UNKNOWN_CACHE_HIT` instead of re-querying the DB.

## What it READS

- `self._news_repo.get_by_symbol(symbol, hours=24)` — table `news_articles` (LIKE filter on `symbols` column with base-asset fallback in repo).
- `self._sentiment_repo.get_posts_by_symbol(symbol, hours=24)` — table `reddit_posts`.
- `self._altdata_repo.get_latest_fear_greed()` — table `fear_greed`.
- `self._sentiment_repo.get_sentiment_for_symbol(symbol, limit=1)` — table `aggregated_sentiment` (for momentum prev-score).
- `self._db.fetch_one("SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?", ...)` — only in no-data branch (line 165).

## What it WRITES

- DB: `aggregated_sentiment` table (insert per call, line 247).
- In-memory: `_unknown_cache` (per-symbol no-data verdict, TTL 1800 s, capped only by symbol set).

## Cadence (live)

- Indirectly driven by `signal_worker` sweet spot `1:00` per 5-min window.
- Live count: 50 distinct symbols aggregated in last 1h, 116 rows total. Per-cycle distribution observed in aggregated_sentiment last 1h: 72 neutral + 44 unknown = 116 / 50 = ~2.3 writes per coin per hour. (Explained by the 2 invocations per cycle; the cache hit short-circuits one of them.)

## Failure modes

| Tag | Count | Source |
|-----|------:|--------|
| `SENT_AGG` | observed every tick (~50 per cycle) | aggregator.py:272 |
| `SENT_NEUTRAL` (no-data branch) | observed for 44 of 50 coins | aggregator.py:192 |
| `SENT_UNKNOWN` (back-compat) | observed when reddit configured + zero coverage | aggregator.py:218 |
| `SENT_DEGRADED_MODE` | observed for all 44 zero-coverage coins | aggregator.py:208 |
| `SENT_NO_DATA` | 0 (reddit not configured, so this branch never fires) | aggregator.py:213 |
| `SENT_BASEASSET_FALLBACK` | 0 | news_repo.py:155 |
| `SENTIMENT_DEGRADED_MODE` (init one-shot) | 1 (at 22:53:27.037) | aggregator.py:78 |
| `SENT_UNKNOWN_CACHE_HIT` | observed in bulk after warm-up | aggregator.py:106 |
| Per-symbol exception "Sentiment aggregation failed" | 0 | signal_worker.py:103 |

## Dependencies (consumers)

- `src/intelligence/signals/signal_generator.py:85` — `sentiment = await self._aggregator.aggregate_for_symbol(symbol)` consumed by `generate_signal()`.
- `src/workers/signal_worker.py:96-102` — direct call per symbol per tick.
- `src/mcp/tools/sentiment_tools.py:80` — MCP tool `aggregate_for_symbol` for ad-hoc operator queries.
- DB-table `aggregated_sentiment` is read by:
  - `src/database/repositories/sentiment_repo.py:146` — `get_sentiment_for_symbol(symbol, limit=1)` (used by aggregator itself for momentum).
  - `src/database/repositories/sentiment_repo.py:162` — `get_sentiment_history(symbol, hours)` (used by `aggregator.get_sentiment_shift`).
- Deprecated reference: `src/brain/prompt_builder.py.deprecated:91` previously called `agg.aggregate_for_symbol(sym)` for prompt context (file is `.deprecated`, not active).


================================================================================
FILE: C1_structure_worker.md
================================================================================

# C1 — StructureWorker / X-RAY (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
DB snapshot: `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db` (mtime 22:56)

---

## C.1.1 — File location and structure

**Worker file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/structure_worker.py` — 368 lines (verified `wc -l`).

Module docstring (`structure_worker.py:1-11`, verbatim):

```
"""Structure Worker: runs X-RAY structural analysis for the full watch_list.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins). With batch_size=25,
  a full sweep completes in 2 ticks (~10 min via two sweet-spot fires).
- Fires at the configured sweet spot (default 0:45) within every 5-min
  window, after KlineWorker's 0:30 finishes its writes. The 15-second gap
  gives kline writes time to land in trading.db before structure reads.
- ``ShadowKlineReader`` (Shadow DB fallback path, async-aiosqlite per the
  2026-04-25 fix) is unchanged.
"""
```

**Sub-engine directory:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/` — 14 .py files (excl. `__init__.py`), 4302 lines total.

Files with one-line descriptions (header docstrings, verbatim first line):

| File | Lines | Class / role |
|------|-------|--------------|
| `structure_engine.py` | 1164 | "X-RAY Structure Engine — orchestrates all structural analysis." (`StructureEngine`) |
| `structure_cache.py` | 128  | "X-RAY structural analysis cache — compute once, share everywhere." (`StructureCache`) |
| `support_resistance.py` | 320 | (Phase 1) `SupportResistanceEngine` — S/R + swing pivots |
| `market_structure.py` | 306 | (Phase 2) `MarketStructureDetector` — BOS / CHoCH / structure label |
| `structural_levels.py` | 245 | (Phase 3) `StructuralLevelCalculator` — structural SL/TP placement |
| `fair_value_gap.py` | 212 | (Phase 4) `FairValueGapDetector` |
| `order_blocks.py` | 188 | (Phase 5) `OrderBlockDetector` |
| `liquidity.py` | 332 | (Phase 6+7) `LiquidityMapper` — zones + sweeps |
| `volume_profile.py` | 188 | (Phase 8) `VolumeProfileCalculator` |
| `fibonacci.py` | 191 | (Phase 9) `FibonacciCalculator` |
| `mtf_confluence.py` | 250 | (Phase 10) `MTFConfluenceScorer` |
| `setup_scanner.py` | 263 | (Phase 11) `SetupScanner` — "Smart Coin Selection" |
| `session_timing.py` | 218 | (Phase 12) `SessionTimer` — "Institutional Session Timing" |
| `shadow_kline_reader.py` | 296 | `ShadowKlineReader` — async aiosqlite fallback |

`models/structure_types.py` exists alongside (`SetupType`, `StructuralAnalysis`, etc.).

---

## C.1.2 — The 12 X-RAY phases

The pipeline orchestrator is `StructureEngine.analyze()` at `structure_engine.py:167-551`. The phase markers in code are **1-10 plus 11 and 12** (see `structure_engine.py:204, 247, 265, 334, 345, 358, 373, 400, 415, 436` for phase comment headers; `structure_worker.py:91, 229` for phases 12/11).

NOT FOUND — the labels "3a", "3b", "3c" referenced in the prompt do NOT appear as separate phase headers in any file under `src/analysis/structure/` (searched: `grep -n "Phase 3a\|3b\|3c" src/analysis/structure/*.py` returns no matches). Phase 3 in the code is a single block: "PHASE 3: Structural SL/TP Placement" (`structure_engine.py:265`). Documenting as a gap.

| Phase | Name | File | Computes | Writes to `StructuralAnalysis` |
|-------|------|------|----------|-------------------------------|
| 1 | Support & Resistance | `support_resistance.py` (`SupportResistanceEngine.calculate`) | `support_levels`, `resistance_levels`, `swing_data` (`structure_engine.py:204-215`) | `support_levels`, `resistance_levels`, `nearest_support`, `nearest_resistance`, `position_in_range` |
| 2 | Market Structure | `market_structure.py` (`MarketStructureDetector.detect`) | BOS/CHoCH events, `structure` label (uptrend/downtrend/ranging) (`structure_engine.py:247-263`) | `market_structure`, derives `suggested_direction` |
| 3 | Structural SL/TP Placement | `structural_levels.py` (`StructuralLevelCalculator.calculate`) | dual-direction SL/TP + RR (`structure_engine.py:265-332`) | `structural_placement` (with `rr_long`, `rr_short`, `rr_best`, `long_sl_price`, etc.) |
| 4 | Fair Value Gaps | `fair_value_gap.py` (`FairValueGapDetector.detect`) | unfilled FVG list (`structure_engine.py:334-343`) | `fvgs`, `nearest_fvg` |
| 5 | Order Blocks | `order_blocks.py` (`OrderBlockDetector.detect`) | OB list (`structure_engine.py:345-356`) | `order_blocks`, `nearest_ob` |
| 6+7 | Liquidity Zones + Sweeps | `liquidity.py` (`LiquidityMapper.detect_zones` / `.detect_sweeps`) | zones + recent sweeps (`structure_engine.py:358-383`) | `liquidity_zones`, `recent_sweeps`, `nearest_unswept_liquidity`, `active_sweep_signal`, `smc_confluence` |
| 8 | Volume Profile | `volume_profile.py` (`VolumeProfileCalculator.calculate`) | POC + VAH/VAL (`structure_engine.py:400-413`) | `volume_profile`, `poc_price` |
| 9 | Fibonacci | `fibonacci.py` (`FibonacciCalculator.calculate`) | retracement levels + key level (`structure_engine.py:415-434`) | `fibonacci`, `fib_key_level` |
| 10 | MTF Confluence | `mtf_confluence.py` (`MTFConfluenceScorer.score`) | 0-10 score + quality (`structure_engine.py:436-460`) | `mtf_confluence`, `mtf_confluence_score`, `confluence_quality` |
| 11 | Setup Scanner (smart coin selection) | `setup_scanner.py` (`SetupScanner.scan`) | ranks all `StructuralAnalysis` in cache; produces top-12 + skip list (`structure_worker.py:229-243`) | `_ranked_setups`, `_skip_list` on `StructureCache` (not on `StructuralAnalysis`) |
| 12 | Session Timing | `session_timing.py` (`SessionTimer.get_context`) | session label, manipulation flag, Asian range (`structure_worker.py:91-108`) | `session_context` field |

### Phase elapsed times — last 10 ticks (per-coin elapsed_ms inside `XRAY_ANALYZE` log)

Sample from cycle 22:25:45 (verbatim log, `el=` field):

```
sym=SOLUSDT     el=12ms   phases=10/10
sym=BNBUSDT     el=12ms   phases=10/10
sym=XRPUSDT     el=167ms  phases=10/10
sym=ADAUSDT     el=12ms   phases=10/10
sym=DOGEUSDT    el=56ms   phases=10/10
sym=AVAXUSDT    el=111ms  phases=10/10
sym=LINKUSDT    el=9ms    phases=10/10
sym=ARBUSDT     el=56ms   phases=10/10
sym=NEARUSDT    el=12ms   phases=10/10
sym=ATOMUSDT    el=24ms   phases=10/10
```

Per-tick aggregate (`XRAY_TICK_SUMMARY`, last 5 ticks):

```
22:05:46  universe=50 batch=0/2 symbols=25 analyzed=25 errors=0 cached=25 setups=12 skips=13 el=2069ms
22:10:45  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=1869ms
22:15:45  universe=50 batch=0/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=2207ms
22:20:45  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=1838ms
22:25:47  universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 setups=12 skips=13 el=2303ms drift_ms=23
```

Phase-level breakout: NOT FOUND. Per-phase elapsed timing is not emitted by the engine (only the aggregate `XRAY_ANALYZE el=` per coin and `XRAY_TICK_SUMMARY el=` per tick). The `phases=10/10` field counts how many of phases 1-10 succeeded (`structure_engine.py:202, 213, 254, 330, 341, 354, 369, 381, 411, 432, 458`).

---

## C.1.3 — Setup classification

**Location:** `StructureEngine.classify_setup()` at `structure_engine.py:676-803`. Called from `structure_engine.py:524` inside `analyze()` after the analysis is otherwise populated.

Verbatim decision tree (`structure_engine.py:676-803`):

```python
def classify_setup(
    self, analysis: StructuralAnalysis,
) -> tuple[SetupType, float]:
    cfg = getattr(self._settings, "setup_types", None)
    fvg_ob_min = getattr(cfg, "fvg_ob_min_confluence", 0.7) if cfg else 0.7
    require_retest = (
        getattr(cfg, "structural_break_require_retest", True) if cfg else True
    )
    sweep_min_pct = (
        getattr(cfg, "sweep_min_displacement_pct", 0.5) if cfg else 0.5
    )
    breakout_min_bars = (
        getattr(cfg, "range_breakout_min_compression_bars", 20) if cfg else 20
    )

    direction = (analysis.suggested_direction or "").lower()
    struct = (analysis.market_structure.structure or "").lower()
    last_bos = analysis.market_structure.last_bos
    nearest_fvg = analysis.nearest_fvg
    nearest_ob = analysis.nearest_ob
    active_sweep = analysis.active_sweep_signal
    mtf = analysis.mtf_confluence
    mtf_score_01 = (
        float(getattr(mtf, "score", 0)) / 10.0 if mtf is not None else 0.0
    )
    smc_01 = max(0.0, min(1.0, analysis.smc_confluence / 100.0))

    def _bull_alignment() -> bool:
        return direction == "long" and struct in ("uptrend",)

    def _bear_alignment() -> bool:
        return direction == "short" and struct in ("downtrend",)

    # ── Bullish FVG + OB confluence ──
    if (
        nearest_fvg is not None and nearest_fvg.direction == "bullish"
        and not nearest_fvg.filled
        and nearest_ob is not None and nearest_ob.direction == "bullish"
        and nearest_ob.fresh
        and _bull_alignment()
        and mtf_score_01 >= fvg_ob_min
    ):
        conf = min(mtf_score_01, max(smc_01, 0.5))
        return SetupType.BULLISH_FVG_OB, round(conf, 4)

    # ── Bearish FVG + OB confluence (mirror) ──
    if (
        nearest_fvg is not None and nearest_fvg.direction == "bearish"
        and not nearest_fvg.filled
        and nearest_ob is not None and nearest_ob.direction == "bearish"
        and nearest_ob.fresh
        and _bear_alignment()
        and mtf_score_01 >= fvg_ob_min
    ):
        conf = min(mtf_score_01, max(smc_01, 0.5))
        return SetupType.BEARISH_FVG_OB, round(conf, 4)

    # ── Bullish structural break (BOS with optional retest) ──
    if (
        last_bos is not None and last_bos.direction == "bullish"
        and direction == "long"
        and (not require_retest or last_bos.significance == "major")
    ):
        conf = max(mtf_score_01, smc_01, 0.5)
        return SetupType.BULLISH_STRUCTURAL_BREAK, round(conf, 4)

    # ── Bearish structural break ──
    if (
        last_bos is not None and last_bos.direction == "bearish"
        and direction == "short"
        and (not require_retest or last_bos.significance == "major")
    ):
        conf = max(mtf_score_01, smc_01, 0.5)
        return SetupType.BEARISH_STRUCTURAL_BREAK, round(conf, 4)

    # ── Liquidity sweep + reclaim ──
    if active_sweep is not None and active_sweep.sweep_depth_pct >= sweep_min_pct:
        if active_sweep.sweep_type == "bullish_sweep" and direction == "long":
            conf = max(mtf_score_01, 0.5)
            return SetupType.BULLISH_LIQUIDITY_SWEEP, round(conf, 4)
        if active_sweep.sweep_type == "bearish_sweep" and direction == "short":
            conf = max(mtf_score_01, 0.5)
            return SetupType.BEARISH_LIQUIDITY_SWEEP, round(conf, 4)

    # ── Range breakout/breakdown (compression release) ──
    if (
        analysis.position_in_range >= 0.95 and direction == "long"
        and analysis.total_confluence_factors >= breakout_min_bars // 2
    ):
        return SetupType.BULLISH_RANGE_BREAKOUT, round(max(mtf_score_01, 0.5), 4)
    if (
        analysis.position_in_range <= 0.05 and direction == "short"
        and analysis.total_confluence_factors >= breakout_min_bars // 2
    ):
        return SetupType.BEARISH_RANGE_BREAKDOWN, round(max(mtf_score_01, 0.5), 4)

    return SetupType.NONE, 0.0
```

### Configurable thresholds (`config.toml:930-935`, verbatim):

```
[analysis.structure.setup_types]
fvg_ob_min_confluence = 0.7
structural_break_require_retest = true
sweep_min_displacement_pct = 0.5
range_breakout_min_compression_bars = 20
mtf_alignment_required = true
```

### Live distribution — last 100 classifications

From `XRAY_CLASSIFY_SUMMARY` events in `workers.log` (last 4 ticks × 25 = 100 classifications):

```
22:10:45  total=25  bearish_fvg_ob=15  none=10                     conf_p50=0.55 conf_p95=0.55
22:15:45  total=25  bearish_fvg_ob=18  none=6   bullish_fvg_ob=1   conf_p50=0.55 conf_p95=0.55
22:20:45  total=25  bearish_fvg_ob=15  none=10                     conf_p50=0.55 conf_p95=0.55
22:25:47  total=25  bearish_fvg_ob=18  none=6   bullish_fvg_ob=1   conf_p50=0.55 conf_p95=0.55
```

Aggregate (last 100 across these 4 ticks, exact `XRAY_CLASSIFY` count by setup_type):

```
bearish_fvg_ob = 66
none           = 32   (10 + 6 + 10 + 6)
bullish_fvg_ob = 2
```

Confidence is essentially constant at 0.55 because the threshold gate is `mtf_score_01 >= 0.7` (i.e. mtf score 7/10) and `conf = min(mtf_score_01, max(smc_01, 0.5))`. With `mtf=7/10` and `smc=55` (= 0.55 normalised), `conf = min(0.7, max(0.55, 0.5)) = 0.55`.

---

## C.1.4 — Batch processing

**Why 25 of 50 per tick:** `_get_universe()` slices `[batch_start : batch_start + batch_size]` and advances `_batch_start` by `batch_size` (`structure_worker.py:340-346`, verbatim):

```python
batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]
self._batch_start += self._batch_size
if self._batch_start >= len(self._full_universe):
    self._batch_start = 0  # wrap around to start of universe

return batch if batch else self._full_universe[:self._batch_size]
```

`batch_size` source (`structure_worker.py:82`):

```python
self._batch_size = settings.structure.batch_size
```

`config.toml:925`:

```
batch_size = 25
```

(NOTE: prompt says "batch_size 2" — that does NOT match the code/config. Live config and live logs both report `batch=0/2` or `batch=1/2`, meaning **2 batches of 25** to cover 50 coins. The "batch=1/2" denominator is the count of batches, not a batch_size value. Confirmed in `structure_worker.py:255-263`: `_batches_total = ceil(50/25) = 2`.)

**Per-coin elapsed (cycle 22:25, in ms):**

```
SOLUSDT=12  BNBUSDT=12  XRPUSDT=167  ADAUSDT=12   DOGEUSDT=56  AVAXUSDT=111
LINKUSDT=9  ARBUSDT=56  NEARUSDT=12  ATOMUSDT=24  INJUSDT=23   RENDERUSDT=12
ONDOUSDT=18 ENAUSDT=14  PYTHUSDT=38  SEIUSDT=8    AEROUSDT=8   RUNEUSDT=42
GALAUSDT=177 MANAUSDT=13 SANDUSDT=16 AXSUSDT=151  LDOUSDT=24
```

23 of 25 captured here; sum ≈ 1023 ms, mean ≈ 41 ms, median ≈ 16 ms. Tick total elapsed (incl. session-context, classify-summary, scanner): `el=2303ms`.

**If batch=1 (i.e. all 50 per tick), projected duration:** 2× the 25-coin mean = ~2050 ms of per-coin work, plus the SetupScanner pass (single pass over the full cache; `setup_scanner.py:36-89` runs in tens of ms). Projected: ~2.0–4.6 s per tick. NOT measured directly — projection is mean × 2 from the live 25-coin sample.

---

## C.1.5 — StructureCache

**Defined in:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/structure_cache.py` (128 lines).

**Constructor:** `structure_cache.py:25` — `def __init__(self, ttl_seconds: float = DEFAULT_TTL)`. `DEFAULT_TTL = 300.0` (`structure_cache.py:15`).

**Wired with TTL=300s** — `manager.py:219-221`:

```python
structure_cache = StructureCache(
    ttl_seconds=float(settings.structure.cache_ttl_seconds),
)
```

`cache_ttl_seconds = 300` (`config.toml:897`).

**Entry shape** — `structure_cache.py:27`:

```python
self._cache: dict[str, tuple[float, StructuralAnalysis]] = {}
```

Key: `symbol` (e.g. `"BTCUSDT"`). Value: `(monotonic_set_time, StructuralAnalysis)`.

`StructuralAnalysis` fields (visible at `structure_engine.py:483-517`, verbatim list):

```
symbol, current_price,
support_levels, resistance_levels, nearest_support, nearest_resistance, position_in_range,
market_structure, structural_placement, setup_score, setup_quality, suggested_direction,
fvgs, order_blocks, liquidity_zones, recent_sweeps,
nearest_fvg, nearest_ob, nearest_unswept_liquidity, active_sweep_signal, smc_confluence,
volume_profile, poc_price, fibonacci, fib_key_level, mtf_confluence, mtf_confluence_score,
confluence_quality, total_confluence_factors, session_context,
# (then patched in classify_setup):
setup_type, setup_type_confidence
```

`structure_cache.py` also stores `_ranked_setups` and `_skip_list` from Phase 11 (`set_ranked_setups`, `structure_cache.py:94-105`).

**Live size, oldest/newest age** — most recent `XRAY_CACHE_HEALTH` events (`workers.log`):

```
22:25:47  size=50  oldest_age_s=302  hits=165 misses=234 hit_rate=0.41
22:20:45  size=50  oldest_age_s=298  hits=...
22:15:45  size=50  oldest_age_s=298  ...
22:10:45  size=50  oldest_age_s=302  ...
```

`oldest_age_s ≈ 300` is exactly the TTL, meaning every alternate-batch-cursor coin sits at the TTL boundary at the moment of the next sweep. Newest entry age is 0–~5 s (just-written within the current tick). The cache reaches `size=50` after the second tick post-restart and stays there.

---

## C.1.6 — Freshness gate

**Cache TTL is the sole freshness mechanism inside the cache itself.** `StructureCache.get()` (`structure_cache.py:31-44`, verbatim):

```python
def get(self, symbol: str) -> StructuralAnalysis | None:
    cached = self._cache.get(symbol)
    if cached:
        cache_time, result = cached
        if time.monotonic() - cache_time < self._ttl:
            self._hits += 1
            return result
    self._misses += 1
    return None
```

Threshold: `self._ttl = 300.0 s` (live wiring). Beyond 300 s `.get()` returns None (the entry is not erased — just rejected on read).

**ScannerWorker access path** (`scanner_worker.py:533-547`):

```python
sw = self.services.get("structure_worker")
structure = None
try:
    cache = getattr(sw, "_cache", None) if sw else None
    structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
except Exception:
    structure = None
if structure is None:
    record["reasons_failed"].append("no_xray_analysis")
    return False, record
setup_type = getattr(structure, "setup_type", None)
if setup_type is None or getattr(setup_type, "value", "none") == "none":
    record["reasons_failed"].append("no_xray_setup_type")
    return False, record
```

ScannerWorker reads the `StructureCache` directly (via the worker's `_cache` attr) — there is NO separate freshness threshold layered on top of the 300 s TTL.

A separate "xray cache write age" telemetry exists (`structure_worker.py:140-144` — `record_write("xray", symbol)`), and ScannerWorker reads it as a **rollup** (`scanner_worker.py:935-945`, `xray_age_p50_ms`, `xray_age_p95_ms`) but does NOT use it as a per-coin gate.

**% fresh at any moment:** Live `XRAY_CACHE_HEALTH` shows `size=50` continuously across the 21:55–22:27 window with `oldest_age_s ∈ [298, 302]`. Since the TTL is 300 s and the worker fires every 5 min covering 25/50 coins per fire, the steady-state pattern is: 25 entries are 0–300 s old (just-written) and 25 are 300+ s old — i.e. exactly half the universe is within TTL at any instant on average, and a coin oscillates between just-written and at-TTL-boundary. `XRAY_CACHE_HEALTH` reports `cached=50` because it counts dict size, NOT TTL-validity. The hit_rate=0.41 line confirms reads frequently miss the freshness check.

---

## OBSERVED ANOMALIES

- 64% of last 100 setups classify as `bearish_fvg_ob`, 32% as `none`, 2% as `bullish_fvg_ob`. The pre-condition for FVG_OB is `mtf_score_01 >= fvg_ob_min` (= 0.7). Since `mtf=7/10` is reached for the bulk of the universe (`XRAY_ANALYZE` shows `mtf=7/10(good)` on most coins) and direction is overwhelmingly `short` (because `structure=downtrend` for almost the whole sample), the bearish branch wins. The threshold value of 7/10 is met with no margin (exactly 7 == 0.7).
- Setup confidence is pinned at 0.55 because of the `min/max` clamp interaction with the constant `smc=55`/`mtf=7`.
- `oldest_age_s = 302` (>= TTL) in cache health report means the cache always carries at least one entry that is technically expired by the time the worker logs the health line.


================================================================================
FILE: C2_signal_worker.md
================================================================================

# C2 — SignalWorker (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
DB snapshot: `_trading_db_snapshot.db` (mtime 22:56)

---

## C.2.1 — Signal generation pipeline

**Worker file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/signal_worker.py` — 178 lines (verified).
**Generator file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/intelligence/signals/signal_generator.py` (`SignalGenerator.generate_signal`, lines 70-214).

### Inputs (where each is read from)

Inside `SignalGenerator.generate_signal()` (`signal_generator.py:84-99`):

```python
sentiment = await self._aggregator.aggregate_for_symbol(symbol)
overall_score = sentiment.get("overall_score", 0.0)
fg = await self._altdata_repo.get_latest_fear_greed()
fg_value = fg.value if fg else 50
fr = await self._altdata_repo.get_latest_funding_rate(symbol)
funding_rate = fr.funding_rate if fr else 0.0
oi = await self._altdata_repo.get_latest_open_interest(symbol)
oi_change = oi.get("change_24h_pct", 0.0) if oi and isinstance(oi, dict) else 0.0
```

| Input | Source |
|-------|--------|
| `sentiment.overall_score` | `SentimentAggregator.aggregate_for_symbol(symbol)` (per-symbol weighted news/reddit/F&G mix) |
| `fg_value` | `AltDataRepository.get_latest_fear_greed()` → `fear_greed_index` table |
| `funding_rate` | `AltDataRepository.get_latest_funding_rate(symbol)` → `funding_rates` table |
| `oi_change` | `AltDataRepository.get_latest_open_interest(symbol)` → `open_interest` table |
| `volume_surge_ratio` | `_compute_volume_surge_ratio(symbol)` reads M5 klines from `MarketRepository.get_klines(symbol, "5", 21)` (`signal_generator.py:284-312`) |
| `data_age_hours` | `_compute_data_age_hours(fg, fr, oi)` — oldest of fg/fr/oi timestamps (`signal_generator.py:216-282`) |

**No structure/X-RAY input** — `SignalGenerator` does NOT consume `StructureCache`. The "TA + sentiment + funding + structure" formulation in the prompt is partially incorrect with respect to the live wiring: TA and structure are not direct inputs to the signal classifier. SignalWorker does call `aggregator.aggregate_for_symbol()` (sentiment side-effect) and then `signal_generator.generate_signal()`.

### Aggregation formula (verbatim)

`signal_generator._evaluate_signal()` at `signal_generator.py:349-490`:

```python
# 1. Compute four component scores in [-1, +1].
s_sentiment = clamp(float(sentiment), -1.0, 1.0)
s_fg = clamp((50.0 - float(fear_greed)) / cfg.fg_normalize_range, -1.0, 1.0,)
s_funding = clamp(-float(funding_rate) / cfg.funding_normalize, -1.0, 1.0,)
s_oi = clamp(float(oi_change) / cfg.oi_normalize_pct, -1.0, 1.0)

# 2. Mark each component active iff abs(score) >= its threshold.
active = {
    "sentiment": abs(s_sentiment) >= cfg.sentiment_min_active,
    "fg":        abs(s_fg)        >= cfg.fg_min_active,
    "funding":   abs(s_funding)   >= cfg.funding_min_active,
    "oi":        abs(s_oi)        >= cfg.oi_min_active,
}
weights = {
    "sentiment": cfg.sentiment_weight,
    "fg":        cfg.fg_weight,
    "funding":   cfg.funding_weight,
    "oi":        cfg.oi_weight,
}

# 3. Weighted sum over active components, renormalised.
active_weight_sum = sum(weights[c] for c in active if active[c])
if active_weight_sum <= 0.0:
    direction_score = 0.0
    signal_type = SignalType.NEUTRAL
    reason = (...)
else:
    direction_score = sum(
        weights[c] * scores[c] for c in active if active[c]
    ) / active_weight_sum
    if direction_score >= cfg.strong_threshold:    signal_type = SignalType.STRONG_BUY
    elif direction_score >= cfg.buy_threshold:     signal_type = SignalType.BUY
    elif direction_score <= -cfg.strong_threshold: signal_type = SignalType.STRONG_SELL
    elif direction_score <= -cfg.buy_threshold:    signal_type = SignalType.SELL
    else:                                          signal_type = SignalType.NEUTRAL
```

Default threshold/weight values (`SignalGeneratorMultiSourceSettings`, `settings.py:1645-1657`):

```
sentiment_min_active = 0.05
fg_min_active        = 0.10
funding_min_active   = 0.20
oi_min_active        = 0.20
sentiment_weight     = 0.40
fg_weight            = 0.25
funding_weight       = 0.20
oi_weight            = 0.15
strong_threshold     = 0.55
buy_threshold        = 0.25
fg_normalize_range   = 30.0
funding_normalize    = 0.005
oi_normalize_pct     = 5.0
```

Confidence is computed by `ConfidenceCalculator.calculate(components)` at `confidence.py:19-71`:

```python
confidence = (
    agreement * 0.40
    + magnitude * 0.25
    + volume * 0.20
    + freshness * 0.15
)
```

with `_freshness_factor` returning 0.3 when `age_hours > 24`, 0.4 when `<= 24`, 0.6 when `<= 12`, 0.8 when `<= 6`, 1.0 when `<= 1`. `_volume_factor` returns 0.3 when `volume_surge_ratio < 0.5`, 0.5 if `< 1.5`, 0.7 if `< 2.5`, 1.0 if `>= 2.5`.

---

## C.2.2 — Phase-29 confidence gate

**Location:** `signal_generator.py:158-189`. Verbatim:

```python
# Phase 29 (Y-28): enforce CONFIDENCE_THRESHOLDS as a hard gate.
_orig_type = signal_type
try:
    t_strong = float(CONFIDENCE_THRESHOLDS.get("strong_buy", 0.60))
    t_buy = float(CONFIDENCE_THRESHOLDS.get("buy", 0.40))
except Exception:
    t_strong, t_buy = 0.60, 0.40
if signal_type in (SignalType.STRONG_BUY, SignalType.STRONG_SELL):
    if confidence < t_strong:
        if confidence >= t_buy:
            signal_type = (
                SignalType.BUY if signal_type == SignalType.STRONG_BUY
                else SignalType.SELL
            )
        else:
            signal_type = SignalType.NEUTRAL
elif signal_type in (SignalType.BUY, SignalType.SELL):
    if confidence < t_buy:
        signal_type = SignalType.NEUTRAL
if signal_type != _orig_type:
    log.info(
        f"SIG_DOWNGRADE | sym={symbol} from={_orig_type.value} "
        f"to={signal_type.value} conf={confidence:.2f} "
        f"strong_min={t_strong:.2f} buy_min={t_buy:.2f} | {ctx()}"
    )
```

`CONFIDENCE_THRESHOLDS` (`signal_models.py:44-50`):

```
strong_buy  = 0.6
buy         = 0.4
neutral     = 0.0
sell        = 0.4
strong_sell = 0.6
```

**Why ~100% demote to NEUTRAL today:** the upstream classifier itself returns NEUTRAL because only the `fg` component is "active" (`SIG_GEN_INPUT … sent_active=False fg_active=True fund_active=False oi_active=False` — see live trace below). The single active component produces `direction_score = +0.100`, which is **below** `buy_threshold = 0.25`, so the classifier emits `NEUTRAL` directly — the Phase-29 gate then has nothing to demote (`_orig_type == NEUTRAL`). No `SIG_DOWNGRADE` events appear in the recent log because the original classification is already NEUTRAL.

```
$ grep -c "SIG_DOWNGRADE" workers.log    # 0 in the captured window
```

So the 100% NEUTRAL outcome is driven by the **classifier's** active-component gate (sentiment/funding/OI all under their `min_active` thresholds), not by the confidence gate.

**Active vs inactive (live BTCUSDT trace, `SIG_GEN_INPUT @ 22:26:00`):**

```
sent_active=False  fg_active=True  fund_active=False  oi_active=False
sentiment=-0.012   fg=47           funding=+0.00003   oi_change=+0.00
```

---

## C.2.3 — `_signal_cache` structure

**Location:** `signal_worker.py:67`:

```python
self._signal_cache: dict[str, Signal] = {}
```

**Key:** symbol string (e.g. `"BTCUSDT"`).
**Value:** `Signal` dataclass (`src/core/types.py`). `Signal` is constructed at `signal_generator.py:190-205`:

```python
signal = Signal(
    symbol=symbol,
    signal_type=signal_type,
    confidence=confidence,
    source="intelligence_aggregator",
    components={
        "overall_sentiment": overall_score,
        "fear_greed": fg_value,
        "funding_rate": funding_rate,
        "oi_change_pct": oi_change,
        "news_count": sentiment.get("news_count", 0),
        "reddit_count": sentiment.get("reddit_count", 0),
    },
    reasoning=reasoning,
    created_at=now_utc(),
)
```

**Live snapshot — 5 representative entries (last `SIG_GEN` events @ 22:26):**

```
sym=BTCUSDT   type=neutral conf=0.20 vol_surge=0.03 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=ETHUSDT   type=neutral conf=0.20 vol_surge=0.06 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=SOLUSDT   type=neutral conf=0.20 vol_surge=0.04 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=BNBUSDT   type=neutral conf=0.20 vol_surge=0.05 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=XRPUSDT   type=neutral conf=0.20 vol_surge=0.04 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'
```

(Anonymisation note: these values are public market data — no sensitive content.)

The cache is read by ScannerWorker via `signal_worker.get_signal(coin)` (`signal_worker.py:169-177`):

```python
def get_signal(self, coin: str) -> Signal | None:
    return self._signal_cache.get(coin)
```

---

## C.2.4 — Why the BTCUSDT signal is `neutral` / `0.20` / `+0.100`

Live inputs RIGHT NOW for BTCUSDT (most recent cycle 22:26:00):

`SIG_GEN_INPUT @ 22:26:00.054` (verbatim):

```
sym=BTCUSDT
sent_active=False  fg_active=True  fund_active=False  oi_active=False
sentiment=-0.012   fg=47           funding=+0.00003   oi_change=+0.00
```

`SIG_CLASSIFY @ 22:26:00.054` (verbatim):

```
sym=BTCUSDT components=[s:-0.01,fg:+0.10,fund:-0.01,oi:+0.00]
            active=[s:False,fg:True,fund:False,oi:False]
            direction_score=+0.100  type=neutral
```

`SIG_GEN @ 22:26:00.062` (verbatim):

```
sym=BTCUSDT type=neutral conf=0.20 vol_surge=0.03 age_h=22.43
rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'
```

### Trace — direction_score

Component computations (per `_evaluate_signal`, with live values):

```
s_sentiment = clamp(-0.012, -1, +1)               = -0.012  → abs(-0.012) < 0.05 → INACTIVE
s_fg        = clamp((50 - 47) / 30.0, -1, +1)     = +0.100  → abs(+0.100) >= 0.10 → ACTIVE
s_funding   = clamp(-0.00003 / 0.005, -1, +1)     = -0.006  → abs(-0.006) < 0.20 → INACTIVE
s_oi        = clamp(+0.00 / 5.0, -1, +1)          = +0.000  → abs(+0.000) < 0.20 → INACTIVE
```

Only `fg` is active. `active_weight_sum = fg_weight = 0.25`.

```
direction_score = (0.25 * +0.100) / 0.25 = +0.100
```

`+0.100 < buy_threshold (0.25)` → `signal_type = NEUTRAL`. The classifier already returns NEUTRAL.

### Trace — confidence = 0.20

`ConfidenceCalculator.calculate` consumes:

```
components = {
    "news_sentiment": sentiment.news_score,
    "reddit_sentiment": sentiment.reddit_score,
    "fear_greed": (fg-50)/50  = (47-50)/50 = -0.06,
    "funding_rate": clamp(funding*100, -1, 1) = clamp(0.003, -1, 1) = +0.003,
    "open_interest": clamp(oi/20, -1, 1) = 0.0,
    "data_age_hours": 22.43,
    "volume_surge_ratio": 0.03,
}
```

The first five non-None scalars feed `agreement` and `magnitude`:

- `_agreement_factor`: counting `>0.05` and `<-0.05` of {-0.012, 0.0 [reddit], -0.06, +0.003, 0.0} → positives=0, negatives=1 (only `fg`=-0.06 < -0.05; sentiment -0.012 > -0.05; funding +0.003 < 0.05). `dominant=1, total=5` → 0.2.
- `_magnitude_factor`: mean(|...|) = (0.012 + 0 + 0.06 + 0.003 + 0)/5 = 0.015.
- `_volume_factor`: vol_surge=0.03 → returns 0.3 (the `< 0.5` bucket).
- `_freshness_factor`: age=22.43 h → returns 0.4 (the `<= 24` bucket).

```
confidence = 0.40*0.2 + 0.25*0.015 + 0.20*0.3 + 0.15*0.4
           = 0.080  + 0.00375    + 0.060     + 0.060
           = 0.2038  ≈ 0.20  ✓
```

(The published value of `0.20` matches.)

### Identifying the EXACT input causing the demotion

The signal type is NEUTRAL because **3 of 4 components are inactive** (sentiment, funding, OI — all near zero) and the only active component (`fg = +0.100`) produces a direction_score (0.100) below the `buy_threshold` (0.25). The single largest contributor to keeping the signal NEUTRAL is the **inactive sentiment** path: `sentiment=-0.012` < `sentiment_min_active=0.05`. Sentiment carries the largest weight (0.40), so even a modest active sentiment in the BUY direction would boost direction_score above 0.25.

The confidence floor of ~0.20 is dominated by the `_volume_factor=0.3` and the `_freshness_factor=0.4` — both are at or near their floor. The `data_age_hours=22.43` indicates Fear & Greed (or funding rate) has not been refreshed in close to a day; **this directly puts freshness in the `<= 24` bucket of 0.4** instead of the `<= 1` bucket of 1.0.

---

## C.2.5 — Live distribution (last 100 signals)

**Source:** `SIG_BATCH_STATS` and `SIG_TICK_SUMMARY` events at 22:06, 22:11, 22:16, 22:21, 22:26 — each tick emits 50 signals → 250 signals across last 5 ticks (200 most recent ≈ "last 100" sample doubled). Verbatim:

```
22:06:01  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.429 conf_mean=0.253 conf_std=0.054
22:11:00  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.343 conf_mean=0.238 conf_std=0.048
22:16:01  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.344 conf_mean=0.267 conf_std=0.058
22:21:00  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.344 conf_mean=0.219 conf_std=0.035
22:26:03  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.335 conf_mean=0.214 conf_std=0.025
```

### `signal_type` distribution

NOT FOUND in a single roll-up log; inferred from the SIG_GEN sample. All BTCUSDT samples shown above and the spot-check across BTC/ETH/SOL/BNB/XRP show `type=neutral`. Sample search:

```
$ grep -E "type=(buy|strong_buy|sell|strong_sell)" workers.log | tail -5
```

Returns 0 hits in the captured 21:55–22:27 window. Inferred distribution: **neutral=100%** of the last 250 signals. (Cross-verified: max conf observed `0.429` < `buy_threshold` confidence floor `0.40`, so even on confidence the classifier could only reach NEUTRAL or BUY; the classifier already chose NEUTRAL.)

### Confidence histogram (last 250)

From the five `SIG_BATCH_STATS` lines, raw stats only — no per-coin rows. Min across the window: 0.203. Max: 0.429. Mean: ~0.24. Std: 0.025–0.058. The distribution is essentially a narrow band [0.20, 0.43] centred near 0.22.

### `direction_score` histogram (last 100)

NOT FOUND as an aggregate log; per-coin direction_score lives in `SIG_CLASSIFY` lines. Sample (22:26 cycle, 5 majors all show `direction_score=+0.100`). The constancy across majors implies the F&G value (47) drives every coin to the same `s_fg=+0.100`, and with no other component active for those coins the per-symbol direction_score is identical. Per-symbol variation only enters when `fund` or `oi` cross their `0.20` activation thresholds (rare in the captured window).

---

## OBSERVED ANOMALIES

- 100% NEUTRAL outcome is structural: only F&G is active for the majors, and `direction_score=+0.100 < buy_threshold=0.25`.
- `data_age_hours = 22.43` for BTCUSDT — F&G or funding/OI has not been refreshed for nearly a day. This pegs `_freshness_factor` at 0.4 (the `<=24` bucket) for the entire 5-min cycle.
- `volume_surge_ratio = 0.03` for BTCUSDT — implies the most recent M5 kline has 3% of the 20-period average volume. Either a zero-volume bar landed in the read window or volume is genuinely collapsed.


================================================================================
FILE: C3_regime_worker.md
================================================================================

# C3 — RegimeWorker (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
DB snapshot: `_trading_db_snapshot.db` (mtime 22:56)

---

## C.3.1 — Classification pipeline

**Worker file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/regime_worker.py` — 313 lines (verified).
**Detector file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/regime.py` — `RegimeDetector` class.

### Inputs

`RegimeDetector.detect(symbol)` (`regime.py:67-103`):

```python
klines = await self.market_repo.get_klines(symbol, TimeFrame.H1.value, 200)
# Then via TAEngine:
ta = await self.ta_engine.analyze(candles=klines)
adx        = ta.get("trend", {}).get("adx", {}).get("adx") or 0
plus_di    = ta.get("trend", {}).get("adx", {}).get("plus_di") or 0
minus_di   = ta.get("trend", {}).get("adx", {}).get("minus_di") or 0
choppiness = ta.get("volatility", {}).get("choppiness_index") or 50
atr        = ta.get("volatility", {}).get("atr_14") or 0
volume_ratio = ta.get("volume", {}).get("volume_sma_ratio") or 1.0
natr       = ta.get("volatility", {}).get("natr_14") or 1.0
atr_percentile = natr * 100
```

Inputs: H1 klines (200 bars) → ADX + DI, choppiness, ATR/NATR, volume SMA ratio.

### Classification formula (verbatim, `regime.py:117-158`)

```python
if adx > cfg.trending_adx_threshold and plus_di > minus_di and choppiness < 45:
    regime = MarketRegime.TRENDING_UP
    confidence = min(adx / 50, 1.0)
    trend_direction = 1
elif adx > cfg.trending_adx_threshold and minus_di > plus_di and choppiness < 45:
    regime = MarketRegime.TRENDING_DOWN
    confidence = min(adx / 50, 1.0)
    trend_direction = -1
elif atr_percentile > cfg.volatile_atr_percentile or volume_ratio > 2.0:
    regime = MarketRegime.VOLATILE
    confidence = min(atr_percentile / 200, 1.0)
    trend_direction = 1 if plus_di > minus_di else -1
elif adx < cfg.ranging_adx_threshold and choppiness > cfg.ranging_choppiness_threshold:
    regime = MarketRegime.RANGING
    confidence = min(choppiness / 80, 1.0)
    trend_direction = 0
elif adx < cfg.dead_adx_threshold and volume_ratio < cfg.dead_volume_ratio and atr_percentile < 50:
    regime = MarketRegime.DEAD
    confidence = 0.8
    trend_direction = 0
else:
    regime = MarketRegime.RANGING
    confidence = 0.4
    trend_direction = 0

active_cats = REGIME_ACTIVE_CATEGORIES.get(regime, [])

state = RegimeState(
    regime=regime,
    confidence=confidence,
    adx=adx,
    atr_percentile=atr_percentile,
    choppiness=choppiness,
    volume_ratio=volume_ratio,
    trend_direction=trend_direction,
    active_strategy_categories=list(active_cats),
)
```

Threshold values (`config.toml:488-502`, verbatim):

```
[regime]
detection_interval_seconds = 600
primary_symbol = "BTCUSDT"
trending_adx_threshold = 25
ranging_adx_threshold = 20
ranging_choppiness_threshold = 60
volatile_atr_percentile = 150
dead_adx_threshold = 15
dead_volume_ratio = 0.5
hysteresis_count = 2
```

### Per-coin vs global

**Global:** `RegimeWorker.tick()` calls `detector.detect()` with no symbol → defaults to `primary_symbol` (BTCUSDT). The result is persisted in `regime_history` and logged as `REGIME_GLOBAL` (`regime_worker.py:142-163`).

**Per-coin:** `regime_worker.py:170-195`:

```python
coins_to_check = [
    s for s in universe
    if s != self.settings.regime.primary_symbol
]
if coins_to_check:
    per_coin = await self.detector.detect_per_coin(coins_to_check)
    if not hasattr(self.detector, '_per_coin_regimes') or self.detector._per_coin_regimes is None:
        self.detector._per_coin_regimes = {}
    self.detector._per_coin_regimes.update(per_coin)
```

`detect_per_coin` (`regime.py:214-222`) calls `detect(symbol)` for every coin individually, hits the same hysteresis path, and returns the dict.

---

## C.3.2 — Stickiness (hysteresis)

**Implementation:** `RegimeDetector.detect()` lines `162-212` (`regime.py`). Verbatim:

```python
confirmed = self._confirmed_regimes.get(symbol)

if confirmed is None:
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    self._last_regime = state
    return state

if regime == confirmed.regime:
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    self._last_regime = state
    return state

# Regime differs from confirmed — apply hysteresis.
pending_regime, pending_count = self._pending_regime.get(symbol, (None, 0))
new_count = (pending_count + 1) if pending_regime == regime else 1

_hyst = int(getattr(cfg, "hysteresis_count", 2))
if new_count >= _hyst:
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    log.warning(
        f"REGIME_CHG | sym={symbol} old={_old_rgm} new={regime.value} ..."
    )
    self._last_regime = state
    return state
else:
    self._pending_regime[symbol] = (regime, new_count)
    log.info(
        f"REGIME_PENDING | sym={symbol} confirmed={confirmed.regime.value} ..."
    )
    self._last_regime = confirmed
    return confirmed
```

**Threshold:** `cfg.hysteresis_count = 2` (`config.toml:502`). Two consecutive readings of the new regime are required to confirm a change.

### Why 8 (or more) coins changed regime in one tick

The 22:27 observation referenced "8 of 49 coins changed regime in one tick." In `workers.log` the FIRST tick after process restart (22:06:15) emitted these `REGIME_CHG` events:

```
22:06:16.264  REGIME_CHG | sym=ENAUSDT  old=ranging      new=volatile        conf=0.38
22:06:16.783  REGIME_CHG | sym=SANDUSDT old=ranging      new=trending_down   conf=0.55
22:06:16.931  REGIME_CHG | sym=LDOUSDT  old=trending_down new=volatile       conf=0.86
22:06:17.414  REGIME_CHG | sym=IMXUSDT  old=ranging      new=trending_down   conf=0.60
22:06:17.910  REGIME_CHG | sym=MNTUSDT  old=trending_down new=ranging        conf=0.40
22:06:18.003  REGIME_CHG | sym=MONUSDT  old=ranging      new=volatile        conf=0.48
22:06:18.314  REGIME_CHG | sym=ALGOUSDT old=volatile     new=ranging         conf=0.40
22:06:18.603  REGIME_CHG | sym=ORCAUSDT old=trending_up  new=volatile        conf=1.00
```

8 changes — matches the observation exactly. Cause: this is the `REGIME_RESTORE` boot path (`regime_worker.py:69-124`) — the in-memory `_per_coin_regimes` is rebuilt from `coin_regime_history` rows that may be up to 30 minutes old. On the very next live `detect()` for these coins, the freshly-computed regime differs from the restored one, and hysteresis still allows confirmation on the FIRST live read because `_confirmed_regimes[symbol]` is None until the live tick assigns it (the restore path writes into `_per_coin_regimes` but NOT into `_confirmed_regimes`). See `regime.py:165-172`:

```python
if confirmed is None:
    # First reading for this symbol — immediately confirm; no prior state to compare.
    self._confirmed_regimes[symbol] = state
    self._pending_regime.pop(symbol, None)
    self._last_regime = state
    return state
```

So on the first post-restart tick, every coin whose live regime differs from the restored regime gets an immediate confirmation (effectively bypassing hysteresis). Subsequent ticks (22:11, 22:16, 22:21, 22:26) emit ZERO `REGIME_CHG` events — confirming the hysteresis works steady-state.

---

## C.3.3 — `_per_coin_regimes` cache

**Defined at:** `regime.py:40` — `self._per_coin_regimes: dict[str, RegimeState] = {}`.

**Key format:** plain symbol string (e.g. `"BTCUSDT"`).

**Value structure:** `RegimeState` dataclass — fields `regime` (`MarketRegime` enum), `confidence`, `adx`, `atr_percentile`, `choppiness`, `volume_ratio`, `trend_direction`, `active_strategy_categories`.

**Write sites:**

- `regime.py:111-118` — boot restore (inside `regime_worker.py:111-118`, writes via `self.detector._per_coin_regimes[row["symbol"]] = RegimeState(...)`)
- `regime_worker.py:194` — `self.detector._per_coin_regimes.update(per_coin)` after `detect_per_coin`
- `regime.py:165-172` — `self._confirmed_regimes[symbol] = state` (paired confirm cache, written every detect())

**Read sites for `_per_coin_regimes` / `get_coin_regime`:**

- `regime.py:46-48` — `RegimeDetector.get_coin_regime(symbol)` — public accessor returns `self._per_coin_regimes.get(symbol)`
- `regime_worker.py:300-313` — `RegimeWorker.get_regime(coin)` — wraps `detector.get_coin_regime`
- `apex/assembler.py:588` — `coin_regime = detector.get_coin_regime(symbol)`
- `apex/gate.py:370` — `coin_regime = detector.get_coin_regime(symbol)`
- `tias/collector.py:282` — `coin_regime = regime_detector.get_coin_regime(symbol)`
- `scanner_worker.py:138` — `state = rw.get_regime(coin)` (also lines 463, 567)

---

## C.3.4 — Why APEX/TIAS can't reach the cache

**RegimeWorker write key (verbatim, `regime_worker.py:189-194`):**

```python
per_coin = await self.detector.detect_per_coin(coins_to_check)
if not hasattr(self.detector, '_per_coin_regimes') or self.detector._per_coin_regimes is None:
    self.detector._per_coin_regimes = {}
self.detector._per_coin_regimes.update(per_coin)
```

`detect_per_coin` returns `{symbol: RegimeState}` (`regime.py:214-222`) — key is the plain symbol string. Inside `detect()` the writes are at `regime.py:169, 176, 192` to `self._confirmed_regimes[symbol]`, and `_last_regime = state` is the singleton. The public read (`get_coin_regime`) reads from `_per_coin_regimes`, NOT from `_confirmed_regimes`.

NOTE: `_per_coin_regimes` is updated only via `regime_worker.py:194` (the bulk `.update(per_coin)` after `detect_per_coin`). The restore path writes there too. Per-symbol `detect()` writes only `_confirmed_regimes` and `_last_regime`. So the cache that the public accessor reads is updated **once per tick by the worker**, not on each individual `detect()` call within `detect_per_coin`. (`detect_per_coin` returns dict from individual `detect`s and the worker `.update()`s it.)

### APEX assembler — `_get_market_conditions` (`apex/assembler.py:585-591`, verbatim)

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    if coin_regime is not None:
        regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        lr = detector._last_regime
        regime = str(lr.regime.value)
        log.warning("REGIME_FALLBACK | sym={sym} source=assembler | ...")
```

Lookup: `detector.get_coin_regime(symbol)` → `self._per_coin_regimes.get(symbol)`. Key = bare symbol string.

### APEX gate — `_get_conviction_weight` (`apex/gate.py:367-379`, verbatim)

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    if coin_regime is not None:
        _regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        _regime = str(detector._last_regime.regime.value)
        log.warning("REGIME_FALLBACK | sym={sym} source=gate | ...")
```

Same call: `detector.get_coin_regime(symbol)`. Key = bare symbol string.

### TIAS collector — `_collect_group_c` (`tias/collector.py:280-294`, verbatim)

```python
regime_detector = self._services.get("regime_detector")
if regime_detector:
    coin_regime = regime_detector.get_coin_regime(symbol)
    if coin_regime is not None:
        result["regime"] = str(coin_regime.regime.value)
        result["regime_verified"] = 1
    elif hasattr(regime_detector, "_last_regime") and regime_detector._last_regime:
        lr = regime_detector._last_regime
        result["regime"] = str(lr.regime.value)
        log.warning("REGIME_FALLBACK | sym={sym} source=tias | ...")
```

Same call: `regime_detector.get_coin_regime(symbol)`. Key = bare symbol string.

### Comparison

All three call sites use `regime_detector.get_coin_regime(symbol)`, which reads `self._per_coin_regimes[symbol]` — **the same key the writer uses**. There is **no key mismatch** in the source code as it stands today.

NOT FOUND — a key-shape divergence. Searched APEX/TIAS for any other lookup pattern (`grep -rn "_per_coin_regimes" src/apex src/tias`) returns 0 hits; both modules only access via `get_coin_regime`. The "APEX/TIAS can't reach the cache" claim from the prompt is not corroborated by the current code; the only documented compatibility risk is:

1. The accessor returns `None` when called BEFORE the first RegimeWorker tick has populated `_per_coin_regimes` (cold-start race). In that window APEX/TIAS fall through to the `_last_regime` fallback (written by every `detect()` global call) and emit `REGIME_FALLBACK`. The current log shows `REGIME_FALLBACK` warnings exist (e.g. "source=assembler", "source=gate", "source=tias") — but they are documented to be emitted on the per-coin lookup miss, not on a key-format mismatch.
2. The dependency injection: APEX/TIAS receive a `regime_detector` service, NOT `regime_worker`. `services["regime_detector"]` is the `RegimeDetector` instance (see `manager.py` wiring). If the service registry stores `regime_worker` only and APEX expects `regime_detector`, that would cause `_services.get("regime_detector")` to return None and the fallback branch to fire. Confirmed by the WARNING `REGIME_FALLBACK` emissions tagged `source=assembler/gate/tias` — but log capture in the current window does NOT include any (grep returned no recent ones in the captured 21:55–22:27 minutes; the warnings are emitted only when `coin_regime is None` AND `_last_regime is not None`).

So based on **code level evidence**: every consumer reads via the same accessor, against the same `dict[symbol -> RegimeState]`. **No divergent key**. The mismatch claim is NOT corroborated by the code.

---

## C.3.5 — DB persistence

### `regime_history` schema (verbatim, from `_trading_db_snapshot.db`)

```sql
CREATE TABLE regime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
    regime TEXT NOT NULL,
    confidence REAL,
    adx REAL,
    atr_percentile REAL,
    choppiness REAL,
    detected_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_regime_time ON regime_history(detected_at DESC);
```

Row count: **2006**. Date range: `2026-03-26 16:11:19` → `2026-04-27 22:26:15`.

Writer: `regime_worker.py:145-157`:

```python
await self.db.execute(
    "INSERT INTO regime_history "
    "(symbol, regime, confidence, adx, atr_percentile, choppiness, detected_at) "
    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
    (
        self.settings.regime.primary_symbol,
        state.regime.value,
        state.confidence,
        state.adx,
        state.atr_percentile,
        state.choppiness,
    ),
)
```

### `coin_regime_history` schema (verbatim)

```sql
CREATE TABLE coin_regime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    regime TEXT NOT NULL,
    confidence REAL NOT NULL,
    adx REAL,
    choppiness REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_coin_regime_symbol ON coin_regime_history(symbol, timestamp DESC);
```

Row count: **20951**. Date range: `2026-04-13 10:48:19` → `2026-04-27 22:26:24`.

Writer: `regime_worker.py:250-258`:

```python
await self.db.execute(
    """INSERT INTO coin_regime_history
       (symbol, regime, confidence, adx, choppiness)
       VALUES (?, ?, ?, ?, ?)""",
    (sym, rs.regime.value, rs.confidence, rs.adx, rs.choppiness),
)
```

### Why the 15-hour gap in `regime_history`

Verbatim DB query (executed against the snapshot):

```
SELECT detected_at, regime FROM regime_history ORDER BY id DESC LIMIT 10;

2026-04-27 22:26:15  ranging
2026-04-27 22:21:15  ranging
2026-04-27 22:16:15  ranging
2026-04-27 22:11:15  ranging
2026-04-27 22:06:15  ranging
2026-04-27 22:01:15  ranging
2026-04-27 21:56:15  ranging
2026-04-27 06:51:15  trending_down  ← LAST ROW BEFORE GAP
2026-04-27 06:46:15  trending_down
2026-04-27 06:41:15  trending_down
```

Gap window: **2026-04-27 06:51:15 → 21:56:15** = ~15 h 5 min with **zero** rows inserted into `regime_history`.

The cause is NOT a bug in the SQL or the worker — RegimeWorker `tick()` always executes `await self.db.execute("INSERT INTO regime_history …")` unconditionally before the per-coin block (`regime_worker.py:145-157`). For 15 hours of zero rows, the worker `tick()` itself was not running. Possible causes (NOT verified inside this collection):

1. The process was stopped or the LAYER1B cycle gate was disabled (`cycle_gated = True` at `regime_worker.py:40`; `is_cycle_active()` would skip the tick if Layer 1B is toggled off).
2. The base worker watchdog or sweet-spot scheduler ran in a degraded state.

The first row after the gap is at 21:56:15 — exactly the `regime_worker` sweet spot `1:15` rounded to 5-min boundaries (`config.toml:138`: `regime_worker = "1:15"`). The 21:55:00 + 1:15 sweet-spot fire matches.

`coin_regime_history` shows the same kind of gap is bounded by the worker process — 20951 rows over 14.5 days = ~1444 rows/day, but the bulk are concentrated in the last few hours after the gap closed.

---

## OBSERVED ANOMALIES

- 15 h 5 min gap in `regime_history` (06:51 → 21:56 UTC on 2026-04-27). Worker did not execute its tick during that window.
- `REGIME_PERCOIN_SUMMARY` reports `divergent=26` consistently across last 5 ticks — **the prompt cited "8 of 49"** but that was the count of `REGIME_CHG` events on the boot tick at 22:06, not the per-cycle divergence count. They are different metrics: divergent=count of coins whose current regime != global; CHG=count of coins whose regime changed this tick.
- All call sites for the regime cache use the same accessor and the same dict — no key mismatch detectable from code review. If APEX/TIAS are reporting `REGIME_FALLBACK`, the cause would be (a) coin not yet in `_per_coin_regimes` (cold start), or (b) the `regime_detector` service is not registered in the consuming context. Neither was directly observed in the captured 30-min log window.


================================================================================
FILE: C4_ta_cache.md
================================================================================

# C4 — TACache (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`

---

## C.4.1 — Where it lives

**File:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/ta_cache.py` — 237 lines (verified `wc -l`).

Module docstring (`ta_cache.py:1-9`, verbatim):

```
"""Centralized TA cache — compute once, share everywhere.

Eliminates duplicate TA computation across:
- strategy_worker (every 45s)
- signal_worker (every 120s)
- position_watchdog (every 15s)

Drop-in replacement for TAEngine — same analyze() interface.
"""
```

### Public API

```python
class TACache:
    def __init__(self, ta_engine, ttl_seconds: float = DEFAULT_TTL,
                 maxsize: int = _DEFAULT_MAXSIZE) -> None
    async def analyze(self, candles=None, symbol: str | None = None,
                      timeframe=None, limit: int = 200) -> dict
    def is_fresh(self, symbol: str, timeframe: str = "60",
                 max_age: int = 60) -> bool
    def invalidate(self, symbol: str | None = None) -> None
    def get_stats(self) -> dict
    # __getattr__(name)   → proxies to underlying TAEngine
```

Constants in module:

```
DEFAULT_TTL              = 90.0   # ta_cache.py:25
_DEFAULT_MAXSIZE         = 200    # ta_cache.py:58
_SIZE_LOG_MIN_INTERVAL   = 300.0  # ta_cache.py:59 (TA_CACHE_SIZE log throttle)
```

**Live wiring:** `manager.py:189` — `ta_cache = TACache(ta_engine_raw, ttl_seconds=120.0)`. Live TTL is **120 s** (not the 90 s default). Same instance is registered as `services["ta"]`, `services["ta_engine"]`, `services["ta_cache"]` (`manager.py:190-192`).

**Internal state:**

```python
self._cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()   # ta_cache.py:91
self._lookups, self._valid_hits, self._recomputed = 0, 0, 0           # ta_cache.py:102-104
self._evictions = 0                                                    # ta_cache.py:106
self._lock = asyncio.Lock()                                            # ta_cache.py:108
```

Cache key (verbatim `ta_cache.py:133-141`):

```python
if candles:
    sym = getattr(candles[0], "symbol", symbol or "UNK") if candles else "UNK"
    tf = getattr(candles[0], "timeframe", timeframe)
    tf_val = tf.value if hasattr(tf, "value") else str(tf) if tf else "?"
    key = f"{sym}:{tf_val}"
elif symbol and timeframe:
    tf_val = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
    key = f"{symbol}:{tf_val}"
else:
    return await self._engine.analyze(...)
```

Both candle-path and symbol-path produce the **same key shape** `f"{sym}:{tf_val}"` (per the Stage-1/2 fix documented at `ta_cache.py:27-48`).

---

## C.4.2 — Lazy population mechanism

**Trigger:** Every call to `TACache.analyze()` is the population trigger. There is no proactive pre-warm — populate-on-miss only.

Hot path (`ta_cache.py:118-174`, verbatim):

```python
async def analyze(self, candles=None, symbol: str | None = None,
                  timeframe=None, limit: int = 200) -> dict:
    if candles:
        ...
        key = f"{sym}:{tf_val}"
    elif symbol and timeframe:
        ...
        key = f"{symbol}:{tf_val}"
    else:
        return await self._engine.analyze(candles=candles, symbol=symbol,
                                          timeframe=timeframe, limit=limit)

    now = time.monotonic()
    self._lookups += 1
    async with self._lock:
        cached = self._cache.get(key)
        if cached:
            cache_time, result = cached
            if now - cache_time < self._ttl:
                self._valid_hits += 1
                self._cache.move_to_end(key)   # promote to MRU
                return result

    # Miss — compute outside lock
    self._recomputed += 1
    result = await self._engine.analyze(candles=candles, symbol=symbol,
                                        timeframe=timeframe, limit=limit)

    async with self._lock:
        self._cache[key] = (time.monotonic(), result)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
            self._evictions += 1
    return result
```

**Recomputation triggers:**

1. Cache miss (key not present).
2. Stale entry: `now - cache_time >= self._ttl` (TTL = 120 s live).

**Invalidation:** `ta_cache.py:183-200` — `invalidate(symbol)` deletes entries whose key starts with `f"{symbol}:"`; `invalidate()` (no arg) clears the whole cache. Searched the codebase for callers:

```
$ grep -rn "ta_cache.invalidate\|TACache.invalidate" src/  
```

Returns 0 hits in src/ (excluding the definition itself). NOT FOUND — no production code path explicitly invalidates the cache; freshness is enforced solely via TTL expiration on read.

**LRU eviction:** when `len(self._cache) > self._maxsize` (default 200), the LRU entry is dropped via `popitem(last=False)` (`ta_cache.py:171-173`).

---

## C.4.3 — Live measurements

`TA_CACHE_SIZE` log lines (rate-limited to one emission per 300 s, `ta_cache.py:59`). All recent emissions (verbatim):

```
2026-04-27 22:11:33.698  TA_CACHE_SIZE | entries=100 maxsize=200 evictions=0 hit_rate=0.39
2026-04-27 22:16:38.573  TA_CACHE_SIZE | entries=100 maxsize=200 evictions=0 hit_rate=0.40
2026-04-27 22:26:38.898  TA_CACHE_SIZE | entries=100 maxsize=200 evictions=0 hit_rate=0.40
```

**Current cache size:** 100 entries (50 coins × 2 timeframes commonly used: M5 + H1).
**hit_rate:** 0.39 → 0.40 over the captured 15-min window.
**Evictions:** 0 (well under maxsize=200).

### Cache miss latency

NOT FOUND as a direct measurement. The cache wraps `TAEngine.analyze()`; per-call elapsed time is not separately logged by the cache layer. The closest proxy is the `_recomputed` counter increment (`ta_cache.py:161`) which simply counts misses, not their duration.

A miss invokes `await self._engine.analyze(...)` — `TAEngine` runs the indicator pipeline against the supplied candles. Since `XRAY_ANALYZE el=…` lines for analyses that include a TA path (used downstream by RegimeDetector via `ta_engine.analyze`) generally measure in the 8–177 ms range for the entire phase pipeline, TA computation alone is in the lower end of that range, but a single `TACache` miss latency is not separately captured by either log emitter.

### Hit rate over last 1000 reads

`hit_rate=0.40` means 40% of cache lookups are within TTL. Inferred from the rolling counter and `_lookups`:
- The reported hit_rate is `self._valid_hits / max(self._lookups, 1)` (`ta_cache.py:215`), measured over **the lifetime of the cache**, NOT a rolling window of 1000.
- NOT FOUND: a rolling-window counter. The counters reset only on process restart.

---

## C.4.4 — Consumers (every caller of `TACache.analyze()`)

Production call sites (excluding tests, generated `.pyc`, and the cache class itself):

| File:line | Context |
|-----------|---------|
| `src/analysis/volatility_profile.py:198` | `ta_5m = await self._ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=limit)` |
| `src/analysis/volatility_profile.py:219` | `ta_1h = await self._ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.H1, limit=limit)` |
| `src/tias/collector.py:360` | `ta = await ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)` |
| `src/brain/strategist.py:627` | `ta = await ta_cache.analyze(...)` |
| `src/brain/strategist.py:1453` | `ta = await ta_cache.analyze(...)` |
| `src/brain/strategist.py:2321` | `ta = await ta_cache.analyze(...)` |
| `src/apex/assembler.py:208` | `ta = await ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)` |
| `src/workers/profit_sniper.py:984` | `ta_result = await self.ta_cache.analyze(...)` |
| `src/workers/strategy_worker.py:1454` | `_ta_entry = await _ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)` |

Indirect consumers via the same `TACache` instance (registered as `services["ta"]/["ta_engine"]/["ta_cache"]`):

- `RegimeDetector.detect()` calls `self.ta_engine.analyze(candles=klines)` (`regime.py:104`). The `ta_engine` it receives from `manager.py` is the TACache instance, so this is the candle-path of `TACache.analyze`.

Strategy worker also drives the **prefetch** (`strategy_worker.py:317-318` comment: "self.ta_engine IS the TACache (manager.py registers `ta`/`ta_engine`/`ta_cache` as the same TACache instance)"), populating entries that downstream readers (strategist, profit sniper, apex assembler, tias collector, volatility profile) hit on subsequent reads.

`SignalWorker` does NOT call TACache directly — its docstring specifically notes "Sentiment aggregation only (no TA — handled by TACache)" (`signal_worker.py:95`).

---

## OBSERVED

- TTL configured at instantiation = 120 s (live), not the 90 s module default. The TTL discrepancy is intentional per the comment block at `ta_cache.py:20-25` ("ttl_seconds=120.0 … pattern MISS, HIT, HIT, MISS, HIT, HIT").
- 100 entries / maxsize 200 → 50% utilisation. Eviction never fires in current steady state (`evictions=0`).
- Hit rate ~40% indicates ~60% of reads either miss outright or hit a stale entry. Given the fixed 50-coin universe and the 120 s TTL, the recompute frequency is dominated by callers that read at intervals > 120 s (e.g. brain strategist on a 150 s cadence).
- No proactive invalidation in production code; cache is purely TTL-bounded.


================================================================================
FILE: D1_strategy_worker.md
================================================================================

# D1 — Strategy Worker (Layer 1C / Strategy Pipeline / Stage 1)

**Capture timestamp:** 2026-04-27 23:03:34 UTC
**Source files (verified line counts and bytes):**
- `src/workers/strategy_worker.py` — 1592 lines, 77,902 bytes (per `wc -l` / `wc -c`)
- `src/strategies/scanner.py` — 668 lines
- `src/strategies/scorer.py` — 467 lines
- `src/strategies/ensemble.py` — 162 lines
- `src/strategies/registry.py` — 133 lines
- `src/strategies/register_all.py` — 132 lines
- `src/strategies/pnl_manager.py` — 449 lines
- `src/strategies/categories/` — 39 strategy files (40 listed in dir, X1 not registered on this run)

**Live observation window (workers.log):** 5 STRAT cycles, 2026-04-27 22:06:30 → 22:26:39

---

## D.1.1 — File overview: `src/workers/strategy_worker.py`

### Methods (every method, one-line description)

| Line | Method | Description |
|---|---|---|
| 56 | `__init__` | Constructs the worker; injects registry/scanner/regime/scorer/ensemble/pnl/ta/repo/services; sets `_score_cache`, `_prev_consensus`, `_tick_times`. |
| 98 | `tick()` | Full Layer 1-4 pipeline (PnL gate → universe → regime → prefetch → L1 scan → L2 score → L3 ensemble → apply restrictions → L4 hand-off). Async. |
| 891 | `get_score(coin)` | Public accessor; returns `_score_cache.get(coin)` (last L2 total_score for the symbol). |
| 904 | `_build_consensus_summary(setups)` | Builds legacy per-coin consensus dict `{symbol: {"buy": int, "sell": int, "total_score": float}}` from a setup list (fed `filtered`). |
| 923 | `_build_per_coin_consensus(setups)` | Builds new per-coin consensus payload `{symbol: {"consensus": str, "consensus_score": float, "vote_count": int, "direction": str, "last_updated": float}}`; takes the highest-`total_score` setup per symbol. |
| 983 | `_execute_claude_trade(trade, position_symbols, plan)` | Executes a single Claude-directed trade: validates symbol/dup/X-RAY/SL-TP/qty, calls `order_svc.place_order(purpose="layer3_entry", layer_snapshot=...)`, registers with coordinator, saves thesis, records to DB, sends Telegram. |

Two top-level constants/state on the class:
- `worker_tier = WorkerTier.LAYER1C` (line 52)
- `cycle_gated = True` (line 54) — Phase 4 LayerManager skip-when-inactive

### The 4 internal "layers" (line ranges inside `tick()`)

The `tick()` method spans lines **98 → 889**. Each numbered comment block declares one layer (numbering follows the source comments `# 1.` … `# 9.` plus the explicit `LAYER` headers).

| Layer | In-file label | Lines | What it does |
|---|---|---|---|
| Pre-pipeline gate | `# 1. Check PnL manager` … `# 5b. Pre-fetch sentiment and altdata` | 113–394 | PnL halt check (line 113); kline-circuit check (140); universe load (157); regime detection (165); active-strategies query (196); M5 + H1 kline batch + TA prefetch (207–315); H1 TA pre-population (317–359); altdata/sentiment fetch (366–394). |
| **Layer 1 (Scanner)** | `# 6. LAYER 1: Scan — run strategies on coins` | 466–549 | Iterates `candles_map` × `symbol_strategies`; calls `strategy.scan(...)`; collects `raw_signals`. Emits `STRAT_L1_DONE`/`STRAT_L1`/`STRAT_L1_SIG`. |
| **Layer 2 (Scorer)** | `# 7. LAYER 2: Score (with sentiment + altdata + structural context)` | 557–634 | `self.scorer.score_batch(...)` → `scored: list[ScoredSetup]`; populates `_score_cache` (lines 585–590); computes percentile + component-avg distribution; emits `STRAT_L2_DONE`/`STRAT_L2`. |
| **Layer 3 (Ensemble)** | `# 8. LAYER 3: Ensemble` | 636–676 | `self.ensemble.vote_batch(...)` → `consensus_setups: list[EnsembleResult]`; emits `STRAT_L3_DONE`/`STRAT_L3`/`STRAT_L3_VOTE`. |
| **Layer 4 (Hand-off)** | `# 9. Apply PnL restrictions (start of L4)` … `═════ LAYER 4: STORE HINTS FOR CLAUDE ═════` | 678–828 | `pnl_manager.apply_restrictions(...)`, builds and writes `_strategy_consensus`, `_strategy_consensus_summary`, `_strategy_hints` on the layer manager; emits `STRAT_CONSENSUS_WRITE`/`STRAT_CONSENSUS_CHANGE`/`STRAT_CONSENSUS_SUMMARY`/`STRAT_L4_HANDOFF`/`STRAT_L4`. |

Cycle close: lines 830–889 emit `STRAT_CYCLE_DONE`, `STRAT_TICK_SLOW` (`>30 s`), and the rolling 10-tick `STRAT_HEALTH`.

---

## D.1.2 — Layer 1: Strategy Scanner

**Files:**
- Universe selection: `src/strategies/scanner.py` (lines 350–579 = `scan_market`); ranks Bybit USDT perps by 0–100 opportunity score (5 components: momentum 0–30, volatility 0–25, trend strength 0–15, volume 0–20, spread 0–10; +regime bonus, −chop penalty). Hard disqualifiers (vol < $5M, price < $0.0001, spread > 0.5%) at scanner.py:424–434. *Not the same as the per-strategy scanning loop in `strategy_worker.tick`.*
- Strategy registration: `src/strategies/registry.py` lines 23–34 (`register`), 44–53 (`get_active_for_regime` — returns ALL enabled strategies; comment line 45-49: "ALL strategies run in ALL regimes. Regime affects sizing, not activation.")
- Bulk register: `src/strategies/register_all.py` — `register_strategies_a_to_f` (A1–F4, 19 strategies, lines 10–57), `register_strategies_g_to_k` (G1–K4, 20 strategies, lines 60–109), `register_all_strategies` (top-level, line 112; X1 only on testnet, lines 117–130).

### All 40 strategy files (39 active in current run, X1 testnet-only)

Live boot log confirms 39 registered (workers.log 22:53:35.187: `Total strategies registered: 39`). X1 file exists at `src/strategies/categories/x1_always_trade.py` but did not register on the 2026-04-27 22:53 boot (testnet flag false).

| ID | Class | File | Category | Detects |
|---|---|---|---|---|
| A1 | `RSIReversalScalp` | `a1_rsi_reversal.py:14` | scalping | RSI<25 oversold at lower BB + stoch crossing up + vol≥1.5× (long); mirror short. Line 1 docstring: "Buy oversold, sell overbought on 5-min chart." |
| A2 | `VWAPBounceScalp` | `a2_vwap_bounce.py:14` | scalping | Price within 0.1% of VWAP + RSI 40–50 + 8/12 candles above VWAP + bullish pattern + vol < 0.8× (long). |
| A3 | `BBSqueezeScalp` | `a3_bb_squeeze_scalp.py:14` | scalping | BB bandwidth < 2.0 + price > BB upper + macd_hist > 0 + vol_ratio ≥ 2.0 (long). |
| A4 | `EMACrossoverMomentum` | `a4_ema_crossover.py` | scalping | EMA crossover with trend confirmation. |
| B1 | `VolumeBreakout` | `b1_volume_breakout.py:14` | momentum | BB bandwidth < 3 + price > BB upper + vol_ratio ≥ 3.0 + RSI > 60 + macd_hist > 0 + ADX ≥ 20 (long). M15 timeframe. |
| B2 | `SupertrendFollower` | `b2_supertrend_follower.py:14` | momentum | Supertrend dir == 1 + price > SMA50 + MACD line > 0 + ADX ≥ 25 + 50 ≤ RSI ≤ 70 + vol ≥ 1.0 (long). H1 timeframe. |
| B3 | `IchimokuBreakout` | `b3_ichimoku_breakout.py` | momentum | Multi-indicator trend confirmation using Ichimoku proxies. |
| B4 | `DoubleBottomTop` | `b4_double_bottom_top.py` | momentum | Pattern-based reversal with divergence confirmation. |
| C1 | `BBMeanReversion` | `c1_bb_mean_reversion.py` | mean_reversion | Buy at lower BB, sell at upper BB in ranging markets. |
| C2 | `RSIDivergence` | `c2_rsi_divergence.py` | mean_reversion | Detect price/RSI divergence for reversals. |
| D1 | `FundingRateFade` | `d1_funding_rate_fade.py` | funding_arb | Contrarian trade when funding rates are extreme. |
| D2 | `OIDivergence` | `d2_oi_divergence.py` | funding_arb | Trade when price and open interest diverge. |
| E1 | `FearGreedExtreme` | `e1_fear_greed_extreme.py` | sentiment | Contrarian trade at extreme F&G levels. |
| E2 | `NewsBreakout` | `e2_news_breakout.py` | sentiment | Trade strong news-driven moves with volume confirmation. |
| E3 | `SentimentMomentum` | `e3_sentiment_momentum.py` | sentiment | Trade sentiment shifts confirmed by price and volume. |
| F1 | `SupportResistanceBounce` | `f1_support_resistance.py` | advanced | Trade bounces off key S/R levels. |
| F2 | `MultiTFAlignment` | `f2_multi_tf_alignment.py` | advanced | Enter when all TF indicators align. |
| F3 | `LiquidationHunt` | `f3_liquidation_hunt.py` | advanced | Trade liquidation cascades in leveraged markets. |
| F4 | `GridRecovery` | `f4_grid_recovery.py` | advanced | Add to losing positions in ranging markets only. (Activates only for losing positions per docstring.) |
| G1 | `StopHuntSniper` | `g1_stop_hunt_sniper.py` | predatory | Trade reversals after stop hunt wicks beyond S/R. |
| G2 | `RetailSentimentFade` | `g2_retail_sentiment_fade.py` | predatory | Contrarian trade against extreme crowd sentiment. |
| G3 | `LiquidationFrontrunner` | `g3_liquidation_frontrunner.py` | predatory | Front-run liquidation cascades. |
| G4 | `WhaleShadow` | `g4_whale_shadow.py` | predatory | Follow unusually large volume candles (whale activity). |
| H1 | `FundingPrediction` | `h1_funding_prediction.py` | microstructure | Position before extreme funding collection. |
| H2 | `SpreadBasisExploit` | `h2_spread_basis.py` | microstructure | Trade perp premium/discount to index. |
| H3 | `VolatilitySwitch` | `h3_volatility_switch.py` | microstructure | Trade breakout from ultra-tight squeeze. |
| H4 | `OrderFlowImbalance` | `h4_order_flow.py` | microstructure | Detect directional flow from consecutive candles. |
| I1 | `KillZoneTrading` | `i1_kill_zone.py` | time_based | Trade during high-impact session opens. |
| I2 | `WeekendGapExploit` | `i2_weekend_gap.py` | time_based | Trade thin-volume weekend stop hunts. |
| I3 | `OptionsExpiryPlay` | `i3_options_expiry.py` | time_based | Trade mean reversion near monthly expiry. |
| I4 | `HourlyCloseMomentum` | `i4_hourly_close.py` | time_based | Trade consecutive strong closes. |
| J1 | `BTCDominanceRotation` | `j1_btc_dominance.py` | cross_market | Trade BTC vs alts rotation. |
| J2 | `CorrelationBreakdown` | `j2_correlation_breakdown.py` | cross_market | Trade when asset diverges from BTC. |
| J3 | `CrossExchangeLag` | `j3_cross_exchange_lag.py` | cross_market | Arbitrage last_price vs mark_price. |
| J4 | `AltcoinBetaAmplification` | `j4_altcoin_beta.py` | cross_market | Trade lagging alts that will catch up to BTC. |
| K1 | `ClaudeConviction` | `k1_claude_conviction.py` | ai_enhanced | Deep Claude API analysis for high-quality setups. K1 does not independently scan (per docstring, line 11–12). |
| K2 | `PatternMemory` | `k2_pattern_memory.py` | ai_enhanced | Match current market state to historical patterns. |
| K3 | `MultiStrategyEnsemble` | `k3_ensemble.py:14` | ai_enhanced | Placeholder. Logic in `ensemble.py`. `scan()` returns None, `vote()` returns `("NEUTRAL", 0.0, "K3 does not vote — it IS the voting system")` (lines 25–29). |
| K4 | `AdaptiveOptimizer` | `k4_adaptive_optimizer.py` | ai_enhanced | Placeholder. Logic in `optimizer.py`. Doesn't trade — tunes parameters and weights. |
| X1 | `AlwaysTradeStrategy` | `x1_always_trade.py` | (testnet only) | Forces trades on testnet for data generation. NOT registered in current run. |

### Live measurement: which strategies fire most / never fire

Source: `data/logs/workers.log` `STRAT_L1_DONE` lines (5 cycles between 22:06–22:26 in the live log; 7 cycles total when including the previous workers.2026-04-27 file). Cumulative `top_firing` counts:

| Strategy | Fire count (5 cycles) | Notes |
|---|---|---|
| `B4_double_bottom_top` | 63 | Dominates — fires 9 signals on every cycle observed. (`B4_double_bottom_top:9` repeated 5 cycles.) |
| `A3_bb_squeeze` | 7 | Fires 1× in 3 cycles. |
| `H3_vol_switch` | 5 | Fires 2× one cycle, 1× another. |
| `A4_ema_crossover` | 3 | Fires once. |
| `B2_supertrend` | 3 | Fires 2× one cycle. |
| `B3_ichimoku` | 2 | Fires once each in 2 cycles. |
| `A2_vwap_bounce` | 1 | Fires once. |
| `H4_order_flow` | 1 | Fires once. |
| `I4_hourly_close` | 1 | Fires once. |
| **Never fired (count = 0):** | | A1, B1, C1, C2, D1, D2, E1, E2, E3, F1, F2, F3, F4, G1, G2, G3, G4, H1, H2, I1, I2, I3, J1, J2, J3, J4, K1, K2, K3, K4 — **30 of 39** |

(Note: the `non_firing` log field is truncated to `[:5]` in `strategy_worker.py:535`, so the bottom-five list is not exhaustive. The "never fired" count above is computed from `top_firing` absence across all observed cycles.)

Verbatim sample STRAT_L1_DONE (workers.log line @ 22:06:33.311):
```
STRAT_L1_DONE | signals=10 strategies=39 coins=50 per_strategy_avg=0.26 top_firing=[B4_double_bottom_top:9,A3_bb_squeeze:1] non_firing=[A1_rsi_reversal,A2_vwap_bounce,A4_ema_crossover,B1_volume_breakout,B2_supertrend] el=29ms | sid=s-1777327590001
```

---

## D.1.3 — Layer 2: Trade Scorer

**File:** `src/strategies/scorer.py`

### Score formula (verbatim, lines 47–53)

```python
base = self._score_base(signal)
confluence = self._score_confluence(signal, ta_data)
context = self._score_context(signal, ta_data, sentiment_data, altdata, regime)
quality = self._score_quality(signal, candles, ta_data, structural_data)

total = base + confluence + context + quality
```

Grade thresholds (lines 58–67):
```
total >= 80 -> A+
total >= 68 -> A
total >= 56 -> B
total >= 45 -> C
else        -> D
```

### Component breakdown

| Component | Range | Source method | Formula summary |
|---|---|---|---|
| `base` | 0–40 | `_score_base` (lines 114–125) | Starts at 30. +3 per condition with strength > 0.8, +2 per > 0.6, +1 per > 0.4. Clamped at 40. |
| `confluence` | 0–25 | `_score_confluence` (lines 127–172) | Trend agreement ±5/−3, momentum agreement ±5/−3, volume confirmation +5, overall TA signal ±5/−3, volatility favorable +5. Clamped 0–25. |
| `context` | 0–20 | `_score_context` (lines 174–240) | Higher-TF agreement +10 (or +3 if disagree, when conf>0.6), sentiment ±3, F&G 0–8 (extreme contrarian gets max), funding 0–4, regime match +2. Clamped 20. |
| `quality` | 0–20 | `_score_quality` (lines 242–319) | Volume 0–3, S/R proximity 0–3 basic OR 0–8 X-RAY (`_xray_sr_score` at line 322), clean candle structure 0–3, baseline +3. Clamped 20. |

X-RAY `_xray_sr_score` (lines 322–467) modifiers (each tracked in `_m` dict):
- entry_quality: ideal +3 / good +2 / poor −1
- rr_quality: excellent +2 / good +1
- structure dir: aligned +2 / against −2
- BOS/CHoCH: +1 / −2
- FVG: +1; OB fresh: +1.2; SMC≥70: +0.8; sweep: high_prob +1.5 / mod +0.8
- POC favorable: +0.5; FIB confluence: +0.8; MTF: +1.0
- Session: NY mid +0.3; manipulation_likely −0.5
- RR-skip penalty: −3.0 (when `rr_quality=='skip'` and not fallback)
- Final clamp 0–8.

### Live distribution — last 50 ScoredSetups (5 cycles aggregated)

`STRAT_L2_DONE` percentile + component avg lines from `workers.log` (5 most-recent cycles):

| Cycle (UTC) | scored | p25 | p50 | p75 | p95 | base avg | confl avg | ctx avg | qual avg |
|---|---|---|---|---|---|---|---|---|---|
| 22:06:33 | 10 | 43.0 | 52.0 | 60.8 | 68.0 | 33.5 | 9.0 | 3.2 | 8.4 |
| 22:11:33 | 14 | 45.8 | 66.0 | 69.0 | 72.0 | 33.2 | 12.5 | 5.1 | 9.0 |
| 22:16:38 | 13 | 48.3 | 60.0 | 68.8 | 79.0 | 33.8 | 11.2 | 6.8 | 7.9 |
| 22:21:37 | 12 | 47.8 | 53.0 | 59.0 | 65.0 | 33.2 | 7.7 | 4.0 | 8.6 |
| 22:26:39 | 10 | 49.3 | 50.0 | 55.8 | 62.0 | 33.1 | 8.5 | 4.2 | 6.7 |
| **Aggregate (n=59)** | — | — | — | — | — | **33.4** | **9.78** | **4.66** | **8.12** |

Total composite mean ≈ 56.0 (B grade). Best single setup observed: 82 (A+) at 22:16:38 (CRVUSDT).

Component dominance: `base` (~33) carries the majority of every score; `confluence` floats 7.7–12.5; `context` low (3.2–6.8 — sentiment/F&G mostly empty in this run); `quality` 6.7–9.0.

Sample verbatim:
```
STRAT_L2_DONE | scored=14 score_p25=45.8 score_p50=66.0 score_p75=69.0 score_p95=72.0 score_components_avg=[base:33.2,confluence:12.5,context:5.1,quality:9.0] el=34ms | sid=s-1777327890003
```

---

## D.1.4 — Layer 3: Ensemble Voter

**File:** `src/strategies/ensemble.py`

### Voting logic (verbatim, lines 35–135 — entry method `vote`)

```python
def vote(
    self,
    setup: ScoredSetup,
    candles_map: dict[str, list[OHLCV]],
    ta_map: dict[str, dict],
    sentiment_data: dict | None,
    altdata: dict | None,
    regime: RegimeState,
) -> EnsembleResult:
    signal = setup.raw_signal
    symbol = signal.symbol
    direction = signal.direction
    originator = signal.strategy_name

    active = self.registry.get_active_for_regime(regime.regime)
    candles = candles_map.get(symbol, [])
    ta_data = ta_map.get(symbol, {})

    votes: list[EnsembleVote] = []
    for strategy in active:
        if strategy.name == originator:
            continue
        try:
            vote_str, confidence, reasoning = strategy.vote(
                symbol=symbol,
                direction=direction,
                candles=candles,
                ta_data=ta_data,
                sentiment_data=sentiment_data,
                altdata=altdata,
            )
            perf = self.registry.get_performance(strategy.name)
            weight = perf.ensemble_weight

            votes.append(EnsembleVote(
                strategy_name=strategy.name,
                vote=vote_str,
                confidence=confidence,
                weight=weight,
                reasoning=reasoning,
            ))
        except Exception as e:
            log.warning(...)

    buy_votes = sum(v.weight * v.confidence for v in votes if v.vote == "BUY")
    sell_votes = sum(v.weight * v.confidence for v in votes if v.vote == "SELL")
    neutral_votes = sum(v.weight for v in votes if v.vote == "NEUTRAL")

    agreeing = buy_votes if direction == Side.BUY else sell_votes
    opposing = sell_votes if direction == Side.BUY else buy_votes
    consensus_dir = "BUY" if direction == Side.BUY else "SELL"

    # Consensus determines SIZE, not eligibility. All levels pass.
    CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
    cfg = self.settings.strategy_engine
    if agreeing >= 4.0 and opposing <= 1.5:
        consensus = "STRONG"
    elif agreeing >= cfg.min_ensemble_agreement and opposing <= cfg.max_ensemble_opposition:
        consensus = "GOOD"
    elif agreeing >= 1.5 and opposing <= 1.5:
        consensus = "WEAK"
    elif agreeing > opposing:
        consensus = "LEAN"
    else:
        consensus = "CONFLICT"
        log.warning(f"ENSEMBLE_CONFLICT | sym={setup.raw_signal.symbol} buy={agreeing:.1f} sell={opposing:.1f} | {ctx()}")

    size_mult = CONSENSUS_SIZE.get(consensus, 0.3)
```

### Consensus categorization (lines 99–113 verbatim)

```
CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
STRONG  : agreeing >= 4.0  AND opposing <= 1.5
GOOD    : agreeing >= cfg.min_ensemble_agreement AND opposing <= cfg.max_ensemble_opposition
WEAK    : agreeing >= 1.5  AND opposing <= 1.5
LEAN    : agreeing > opposing
CONFLICT: else
```

`size_multiplier` is a **fixed lookup**, not a continuous score: `{1.0, 0.75, 0.50, 0.30, 0.15}`.

### Why every STRONG coin scores `votes=38 score=1.00` (root cause)

Source of the log line: `strategy_worker.py:755-759`:

```python
log.info(
    f"STRAT_CONSENSUS_CHANGE | sym={sym} "
    f"from={prev or 'NONE'} to={entry['consensus']} "
    f"votes={entry['vote_count']} score={entry['consensus_score']:.2f} | {ctx()}"
)
```

`entry` is a row of `_build_per_coin_consensus(...)`. From `strategy_worker.py:959-961`:

```python
consensus_score = float(getattr(sw, "size_multiplier", 0.5) or 0.5)
vote_count = len(getattr(sw, "votes", []) or [])
```

So:

1. **`votes=38` is constant by construction.** `vote_count` = number of `EnsembleVote` rows, which is `len(active) - 1` (originator is excluded at ensemble.py:63). Active count is 39 for the current registration → every setup yields exactly 38 votes regardless of how those 38 voted. The field is a count of voters polled, not a count of agreement.
2. **`score` is the `size_multiplier`, which is one of 5 fixed values.** From ensemble.py:99–113, `size_multiplier ∈ {1.0, 0.75, 0.50, 0.30, 0.15}` — discrete category labels, not a continuous score. So every STRONG coin will always log `score=1.00`, every GOOD `0.75`, every WEAK `0.30`, every LEAN `0.50`, every CONFLICT `0.15`. The label is doing the work; the number is a 1:1 alias.

The "every STRONG coin scoring identical votes=38 score=1.00" observation is therefore a **definition mismatch**, not a bug: the log field labelled `score` is the discrete size-multiplier alias (1.0 ↔ STRONG), and `votes` is the polled-voter count (always 38 with a 39-strategy registry minus originator).

The actual continuous quantities — `agreeing`, `opposing`, `setup.total_score` — are not in the `STRAT_CONSENSUS_CHANGE` line.

Verbatim live ENSEMBLE summary (workers.log 22:11:33.802):
```
ENSEMBLE | setups=14 strong=7 good=2 weak=4 conflict=0 | sid=s-1777327890003
```

---

## D.1.5 — Layer 4: Hand-off

Hand-off targets are written by `tick()` after `apply_restrictions`:

| Target | Where written | Source value | Live size (5 cycles) |
|---|---|---|---|
| `self._score_cache` | `strategy_worker.py:585-590` | `{symbol: float(scored_setup.total_score)}` for every L2 setup. Note: keyed by symbol, so a later cycle's entries overwrite earlier ones. | 16, 18, 18, 19, 19 (size grows monotonically across the run; never the full 50). |
| `layer_manager._strategy_consensus` | `strategy_worker.py:721-734` | `_build_per_coin_consensus(consensus_setups)` output. Built from FULL `consensus_setups` (not `filtered`) — Phase 4 fix per comment line 707-719. Updated via `existing.update(new_consensus)` so stale entries from prior cycles persist (only updates processed coins). | `cache_size_after`: 16, 18, 18, 19, 19. |
| `layer_manager._strategy_consensus_summary` | `strategy_worker.py:737` | `_build_consensus_summary(filtered)` — legacy shape `{sym: {"buy": int, "sell": int, "total_score": float}}`. Built from POST-PnL-filter `filtered` (preserved for legacy strategist reads at `strategist.py:1017/1587`). | 7, 7, 7, 6, 7. |
| `layer_manager._strategy_hints` | `strategy_worker.py:803` (gated by `is_layer_active(3)`) | List of dicts `{symbol, direction, strategy, score, consensus}` from `filtered[:20]`. | 7, 9, 9, 7, 7. |

`STRAT_L4_HANDOFF` verbatim (workers.log 22:11:33.807):
```
STRAT_L4_HANDOFF | score_cache_size=18 consensus_size=18 consensus_summary_size=7 hints_top20_size=9 el=2ms | sid=s-1777327890003
```

---

## D.1.6 — Why ensemble flaps for AAVEUSDT

`STRAT_CONSENSUS_CHANGE` events for AAVEUSDT, last 6 cycles (5 in current log, 1 from prior run):

| Cycle (UTC) | from → to | votes | score (size_mult alias) |
|---|---|---|---|
| 21:56:33 | NONE → STRONG | 38 | 1.00 |
| 22:06:33 | STRONG → GOOD | 38 | 0.75 |
| 22:11:33 | GOOD → WEAK | 38 | 0.30 |
| (22:16:38 — no change logged → AAVEUSDT was either WEAK still, missing, or absent from `consensus_setups` that cycle.) | | | |
| 22:21:37 | WEAK → STRONG | 38 | 1.00 |
| 22:26:39 | STRONG → GOOD | 38 | 0.75 |

Pattern: **STRONG → GOOD → WEAK → (no entry) → STRONG → GOOD** within 30 minutes.

What's changing each cycle that produces this oscillation:

1. **The originator strategy can flip between cycles.** The log does not record originator per cycle, but the consensus is computed *only* if a strategy fired a raw signal on AAVEUSDT that cycle. From the L1 fire counts, the dominant signal-producer is `B4_double_bottom_top` (63 of ~59 total signals). Different originators → different excluded voter → different `agreeing/opposing` totals.
2. **`agreeing`/`opposing` are continuous floats** (sum of `weight * confidence`) but get bucketed by hard thresholds:
   - STRONG requires `agreeing >= 4.0 AND opposing <= 1.5`
   - GOOD requires `agreeing >= cfg.min_ensemble_agreement AND opposing <= cfg.max_ensemble_opposition`
   - WEAK requires `agreeing >= 1.5 AND opposing <= 1.5`
   - LEAN otherwise (and `agreeing > opposing`)
   
   A 0.1-point movement of `agreeing` across 4.0 (e.g. 4.05 → 3.95) flips STRONG↔GOOD; movement across `min_ensemble_agreement` flips GOOD↔WEAK. The categorical output exaggerates small input changes.
3. **TA inputs change each cycle.** `STRAT_PREFETCH` shows fresh M5 + H1 kline batches every cycle (`db=...ms ta=...ms`). Each strategy's `vote()` reads fresh `ta_data` (RSI, BB, MACD, etc.); even small numeric drift can flip a `BUY/SELL/NEUTRAL` vote.
4. **No regime change observed during the window** — `STRAT_REGIME_DIST` is identical across all 5 cycles (`up=0 down=20 ranging=23 volatile=6 dead=0 other=0 total=49 global=ranging`), so regime is *not* the input flipping.
5. **Direction may flip across cycles.** `_build_per_coin_consensus` keeps the highest-`total_score` setup per symbol; if cycle N's top AAVEUSDT signal is BUY and cycle N+1's is SELL, the ensemble computes `agreeing` from the opposite sign, which can flip categories sharply.

The `STRAT_CONSENSUS_CHANGE` line does not preserve the originator name, the direction, or the raw `agreeing`/`opposing` values — those would be needed to diagnose which specific input flipped between any two cycles. From the log alone, the cause is "the underlying continuous votes drift across hard category thresholds, while the L1 originator and direction can change cycle-to-cycle."

---

## D.1.7 — Why 5 strategies (A1/A2/A3/B1/B2) never fire

The prompt names A1/A2/A3/B1/B2. **Live measurement disagrees about A2/A3/B2:** in the 5-cycle window, A2 fired 1×, A3 fired 7×, B2 fired 3×. Only **A1, B1, and (effectively) A3 in early cycles** are non-firing. For completeness, all 5 are analysed below against current observed market conditions (`global=ranging`; per-coin: 23 ranging, 20 down, 6 volatile, 0 up).

### A1 — `RSIReversalScalp` (`a1_rsi_reversal.py:14`, scan lines 27–87)

Trigger conditions (verbatim, lines 46–53 long path):
```
if rsi < 25 and bb_lower and price <= bb_lower:
    if vol_ratio < 1.5: return None
    if stoch_k is None or stoch_d is None or not (stoch_k > stoch_d and stoch_k < 25):
        return None
    if adx > 30 and minus_di > plus_di:
        return None  # Strong downtrend
```
Conjunction: RSI<25 AND price<=BB lower AND vol_ratio>=1.5 AND stoch_k>stoch_d AND stoch_k<25 AND NOT(adx>30 AND −DI>+DI).

Under current conditions: most coins are ranging or in mild downtrend (regime dist 23 ranging / 20 down). RSI<25 is itself a tail event; combined with vol_ratio≥1.5 AND stoch crossover<25 AND no strong downtrend, the conjunction is very tight. The mirror SHORT path requires RSI>75. **Could fire** if a coin enters a sharp oversold flush with low ADX, but probability per coin per cycle is low. 0 fires in 5 cycles is consistent.

### A2 — `VWAPBounceScalp` (`a2_vwap_bounce.py:14`, scan lines 27–82)

Trigger (verbatim, lines 41–49):
```
if vwap_dist_pct < 0.001 and 40 <= rsi <= 50:
    above_count = sum(1 for c in candles[-12:] if c.close > vwap)
    if above_count < 8: return None
    if not has_bullish_pattern(ta_data): return None
    if vol_ratio > 0.8: return None  # Want low volume pullback
```
Requires applicable_regimes ∈ {TRENDING_UP, TRENDING_DOWN} (line 19). In the current window, `up=0 down=20`. Live: A2 fired 1× (in the 22:26 cycle on a coin in a downtrend), so this strategy DOES fire under current conditions, just rarely (price within 0.1% of VWAP is a narrow band).

### A3 — `BBSqueezeScalp` (`a3_bb_squeeze_scalp.py:14`, scan lines 27–83)

Trigger (verbatim, lines 40–57):
```
if bb_bw is None or bb_upper is None or bb_lower is None: return None
if bb_bw >= 2.0: return None  # No squeeze
...
if vol_ratio < 2.0: return None  # Need volume on breakout
if price > bb_upper and macd_hist and macd_hist > 0:
    # LONG signal
```
Applicable regimes: RANGING, VOLATILE. Live: A3 fired 7 times across the window. Conjunction (BB_bw<2.0 AND vol_ratio≥2.0 AND price-broke-band AND macd_hist sign-aligned) is reachable in current ranging market.

### B1 — `VolumeBreakout` (`b1_volume_breakout.py:14`, scan lines 27–82)

Trigger (verbatim, lines 46–50):
```
if bb_bw < 3 and price > bb_upper and vol_ratio >= 3.0 and rsi > 60:
    if macd_hist is None or macd_hist <= 0: return None
    if adx < 20: return None
```
Conjunction: BB_bw<3 AND price>BB upper AND vol_ratio>=3.0 AND RSI>60 AND macd_hist>0 AND ADX>=20. **Vol_ratio≥3.0 is the rare gate** — it means current candle volume is ≥3× SMA20 of volume. M15 timeframe (line 21). In a ranging market with `volatile=6`, vol_ratio≥3.0 is uncommon. 0 fires consistent.

### B2 — `SupertrendFollower` (`b2_supertrend_follower.py:14`, scan lines 27–88)

Trigger (verbatim, lines 46–54):
```
if st_dir == 1 and price > sma_50:
    if macd_line is None or macd_line <= 0: return None
    if adx < 25: return None
    if not (50 <= rsi <= 70): return None
    if vol_ratio < 1.0: return None
```
Applicable regimes: TRENDING_UP, TRENDING_DOWN (line 19). H1 timeframe. With current `up=0 down=20`, only the SHORT path is reachable. Conjunction (ADX≥25 AND 30≤RSI≤50 AND macd_line<0 AND vol_ratio≥1.0 AND price<SMA50 AND st_dir=−1) requires a clean H1 downtrend. Live: B2 fired 3× in the 5-cycle window (2× on one cycle), so this DOES fire under current conditions.

### Summary

Of the 5 named: **B2 fires (3×), A3 fires (7×), A2 fires (1×). A1 and B1 are 0/5.** 30 of 39 strategies have 0 fires across the observed window. Possible causes (per code, not measured): tight conjunctions on rare conditions (B1's vol_ratio≥3.0); regime mismatch (A2/B2 require trending; current is ranging); or dependence on data the prefetch loop doesn't carry (e.g., funding-rate-based D1/D2/H1, sentiment-based E*/G2, news E2 — `sentiment_context` and `altdata_context` are mostly empty in the current run; see strategy_worker.py:366–394). The L1 sweep iterates all 39 strategies on all 50 coins; nothing structural prevents the silent strategies from firing — their conjunctions are simply not satisfied by the current market state and prefetched-data set.

---

## D.1.8 — `apply_restrictions` filter

**File:** `src/strategies/pnl_manager.py`
**Method:** `DailyPnLManager.apply_restrictions` (lines 310–333)

Verbatim:
```python
def apply_restrictions(
    self, setups: list[EnsembleResult], mode: dict,
) -> list[EnsembleResult]:
    """Filter setups based on current mode restrictions."""
    if mode["mode"] == "HALTED":
        return []

    threshold = mode["max_score_threshold"]
    allowed_coins = mode.get("allowed_coins")
    allowed_risk = mode.get("allowed_risk_levels", [])

    filtered: list[EnsembleResult] = []
    for setup in setups:
        signal = setup.scored_setup.raw_signal
        if setup.scored_setup.total_score < threshold:
            continue
        if allowed_coins is not None and signal.symbol not in allowed_coins:
            continue
        if allowed_risk and signal.strategy_category not in ("scalping",):
            # Check risk level from strategy category as proxy
            pass
        filtered.append(setup)

    return filtered
```

Threshold is read from the active mode dict. NORMAL mode is defined at lines 241–250:
```python
elif pct >= cfg.caution_threshold_pct:
    return {
        "mode": "NORMAL",
        "max_score_threshold": 50,
        "max_leverage": 5,
        "allowed_coins": None,
        "max_positions": 10,
        "allowed_risk_levels": ["low", "medium", "high"],
        "message": "Normal mode. Full aggression.",
    }
```

So in NORMAL mode the filter passes any setup whose `total_score >= 50`, no coin restriction, no risk restriction effectively (the `allowed_risk` block is a no-op in current code: the inner `if` body is `pass`). The other modes have higher thresholds (CAUTION 80, SURVIVAL 80, PROTECT 85, TARGET_HIT 90, HALTED 100).

### Live measurement: setup survival per cycle (mode=NORMAL, threshold=50)

`STRAT_CONSENSUS_WRITE` lines (5 cycles):

| Cycle | scored (in) | filtered (out) | survival |
|---|---|---|---|
| 22:06:33 | 10 | 7 | 70% |
| 22:11:33 | 14 | 9 | 64% |
| 22:16:38 | 13 | 9 | 69% |
| 22:21:37 | 12 | 7 | 58% |
| 22:26:39 | 10 | 7 | 70% |
| **Aggregate** | **59** | **39** | **66.1%** |

Verbatim sample (workers.log 22:06:33.370):
```
STRAT_CONSENSUS_WRITE | full_count=10 filtered_count=7 setups_in=10 cache_size_after=16 mode=NORMAL threshold=50 | sid=s-1777327590001
```

Live PnL gate (`STRAT_PNL_GATE`) confirms NORMAL mode: `pnl_pct=+0.00`, halted=N across all 5 cycles (e.g. `STRAT_PNL_GATE | halted=N rsn=ok pnl_pct=+0.00 wins=0 losses=2 el=0ms`). With `pct=0` ≥ `caution_threshold_pct`, `get_current_mode()` returns NORMAL (lines 242–250).


================================================================================
FILE: D2_strategy_performance.md
================================================================================

# D2 — Strategy Performance Table

**Capture timestamp:** 2026-04-27 23:03:34 UTC
**Source DB snapshot:** `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
**Snapshot data extent:** Latest `updated_at` in `strategy_performance`: 2026-04-27T22:29:49.571518+00:00. Latest `created_at` in `strategy_trades`: 2026-04-27T22:25:35.117223+00:00.

---

## D.2.1 — `strategy_performance` table

### Schema (verbatim from `sqlite3 .schema strategy_performance`)

```sql
CREATE TABLE strategy_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT 'all',
        total_trades INTEGER NOT NULL DEFAULT 0,
        winning_trades INTEGER NOT NULL DEFAULT 0,
        losing_trades INTEGER NOT NULL DEFAULT 0,
        win_rate REAL NOT NULL DEFAULT 0,
        avg_pnl REAL NOT NULL DEFAULT 0,
        avg_pnl_pct REAL NOT NULL DEFAULT 0,
        max_drawdown REAL NOT NULL DEFAULT 0,
        sharpe_ratio REAL,
        profit_factor REAL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(strategy, symbol, timeframe)
    );
CREATE INDEX idx_strategy_perf_name ON strategy_performance(strategy);
```

Total row count: **124** (one row per strategy-symbol pair, all `timeframe='all'`).

### 20 most recent rows (ORDER BY updated_at DESC)

```
id   strategy        symbol         tf   total  wins  losses  win_rate  avg_pnl  avg_pnl_pct  max_dd  sharpe  pf  updated_at
10   claude_trader   ETHUSDT        all  72     31    41      0.4306    0.0      -0.0354      0.0     -      -   2026-04-27T22:29:49
9    claude_trader   BTCUSDT        all  65     28    37      0.4308    0.0      -0.0104      0.0     -      -   2026-04-27T22:29:00
27   claude_trader   AAVEUSDT       all  11     5     6       0.4545    0.0      -0.2475      0.0     -      -   2026-04-26T22:36:36
79   claude_trader   CRVUSDT        all  2      0     2       0.0       0.0      -0.1826      0.0     -      -   2026-04-26T22:25:34
132  claude_trader   INJUSDT        all  2      0     2       0.0       0.0      -0.0925      0.0     -      -   2026-04-26T18:09:09
131  claude_trader   AXSUSDT        all  1      0     1       0.0       0.0      -0.4368      0.0     -      -   2026-04-26T04:52:02
130  claude_trader   HYPERUSDT      all  2      0     2       0.0       0.0      -0.735       0.0     -      -   2026-04-26T04:51:32
124  claude_trader   DYDXUSDT       all  2      0     2       0.0       0.0      -0.0677      0.0     -      -   2026-04-26T02:48:20
66   claude_trader   ALICEUSDT      all  4      1     3       0.25      0.0      1.099        0.0     -      -   2026-04-26T02:22:55
50   claude_trader   MAGMAUSDT      all  8      3     5       0.375     0.0      -0.0161      0.0     -      -   2026-04-26T01:46:07
84   claude_trader   BASEDUSDT      all  5      4     1       0.8       0.0      0.3847       0.0     -      -   2026-04-26T01:31:32
32   claude_trader   ALGOUSDT       all  3      2     1       0.6667    0.0      0.5071       0.0     -      -   2026-04-26T01:14:27
127  claude_trader   ORCAUSDT       all  2      2     0       1.0       0.0      1.1763       0.0     -      -   2026-04-26T00:41:04
60   claude_trader   MOVRUSDT       all  3      0     3       0.0       0.0      -0.5747      0.0     -      -   2026-04-26T00:38:08
126  claude_trader   TRUMPUSDT      all  2      0     2       0.0       0.0      -0.09        0.0     -      -   2026-04-26T00:17:16
128  claude_trader   ZBTUSDT        all  2      1     1       0.5       0.0      -0.085       0.0     -      -   2026-04-25T23:52:28
129  claude_trader   WCTUSDT        all  1      0     1       0.0       0.0      -0.46        0.0     -      -   2026-04-25T23:51:45
122  claude_trader   ZKPUSDT        all  4      3     1       0.75      0.0      0.1913       0.0     -      -   2026-04-24T22:08:54
121  claude_trader   TREEUSDT       all  5      2     3       0.4       0.0      -0.3943      0.0     -      -   2026-04-24T21:47:43
125  claude_trader   ZAMAUSDT       all  1      0     1       0.0       0.0      -1.3879      0.0     -      -   2026-04-24T17:58:56
```

(`avg_pnl` is always 0.0; `sharpe_ratio` and `profit_factor` are NULL on all rows.)

### Distribution: which strategies have entries

```sql
SELECT strategy, COUNT(DISTINCT symbol) AS symbols, SUM(total_trades) AS total_trades,
       SUM(winning_trades) AS wins, SUM(losing_trades) AS losses,
       ROUND(SUM(winning_trades)*1.0/NULLIF(SUM(total_trades),0),3) AS overall_wr,
       ROUND(AVG(avg_pnl_pct),4) AS avg_pnl_pct
FROM strategy_performance GROUP BY strategy ORDER BY total_trades DESC;
```

Result:

| strategy | symbols | total_trades | wins | losses | overall_wr | avg_pnl_pct |
|---|---|---|---|---|---|---|
| `claude_trader` | 124 | 958 | 451 | 507 | 0.471 | -0.0841 |

**Only one strategy is represented in `strategy_performance`: `claude_trader`.** The 39 registered strategies (A1–K4) have **zero rows** in this table. Cumulative across all 124 symbols: 958 trades, 451 wins, 507 losses, overall WR 47.1%, avg PnL pct −0.084%.

Top-20 symbols by trade count for `claude_trader` (sorted DESC by total_trades):

| symbol | trades | wins | losses | wr | avg_pnl_pct | last updated |
|---|---|---|---|---|---|---|
| ETHUSDT | 72 | 31 | 41 | 0.431 | -0.0354 | 2026-04-27 22:29 |
| BTCUSDT | 65 | 28 | 37 | 0.431 | -0.0104 | 2026-04-27 22:29 |
| HYPEUSDT | 46 | 21 | 25 | 0.457 | 0.0222 | 2026-04-21 23:01 |
| SOLUSDT | 38 | 14 | 24 | 0.368 | -0.0385 | 2026-04-22 16:10 |
| SIRENUSDT | 37 | 9 | 28 | 0.243 | -0.5817 | 2026-04-20 09:08 |
| ZECUSDT | 37 | 19 | 18 | 0.514 | -0.1349 | 2026-04-24 17:08 |
| RIVERUSDT | 35 | 13 | 22 | 0.371 | -0.1624 | 2026-04-24 17:11 |
| RAVEUSDT | 35 | 18 | 17 | 0.514 | -0.3755 | 2026-04-21 14:30 |
| ARIAUSDT | 32 | 14 | 18 | 0.438 | 0.3412 | 2026-04-22 16:38 |
| CLUSDT | 28 | 18 | 10 | 0.643 | 0.1304 | 2026-04-21 23:03 |
| FARTCOINUSDT | 23 | 14 | 9 | 0.609 | 0.1223 | 2026-04-18 10:54 |
| TAOUSDT | 22 | 15 | 7 | 0.682 | 0.005 | 2026-04-17 19:35 |
| DOGEUSDT | 21 | 6 | 15 | 0.286 | 0.0367 | 2026-04-13 15:12 |
| ADAUSDT | 19 | 10 | 9 | 0.526 | 0.0265 | 2026-04-17 19:25 |
| SUIUSDT | 17 | 9 | 8 | 0.529 | -0.0339 | 2026-04-17 19:25 |
| XRPUSDT | 15 | 10 | 5 | 0.667 | 0.0788 | 2026-04-17 19:22 |
| BSBUSDT | 14 | 5 | 9 | 0.357 | -0.2674 | 2026-04-23 18:23 |
| ENAUSDT | 14 | 6 | 8 | 0.429 | -0.0222 | 2026-04-18 11:30 |
| DOTUSDT | 14 | 6 | 8 | 0.429 | -0.1282 | 2026-04-23 19:45 |
| ENJUSDT | 14 | 8 | 6 | 0.571 | 0.0658 | 2026-04-23 19:44 |

---

## D.2.2 — `claude_trader` performance: 0 wins / 6 trades observation

### Daily PnL ground truth (`daily_pnl` table, last 3 days)

```
date         start  end       realized_pnl  trades  wins  losses  max_dd  target_hit  halted
2026-04-27   0.0    6274.42   -0.2229       6       1     5       0.0     0           0
2026-04-26   0.0    6285.23   -0.3601       2       0     2       0.0     0           0
2026-04-25   0.0    6308.33   -1.4          2       0     2       0.0     0           0
```

Sum past 3 days: 10 trades, 1 win, 9 losses; cumulative realized PnL ≈ −1.99 USDT.

(The 22:27 observation reads "0 wins / 6 trades over prior 2 days" — `daily_pnl` shows 2026-04-26+27 = 8 trades, 1 win, 7 losses; the "0 wins" likely covers the strict prior 48 h before the observation moment, when 2026-04-27 had not yet booked the single win recorded today at 1W 5L.)

### Recent `strategy_trades` rows for `claude_trader` (created_at DESC, last 25)

All 25 rows below have `pnl=NULL`, `pnl_pct=NULL`, `was_win=NULL`, `exit_time=NULL`.

```
trade_id                                     symbol     dir    score  ens     lev  pnl  pnl_pct  was_win  entry_time           exit_time
BTCUSDT_Buy_20260427222535                   BTCUSDT    Buy    100.0  CLAUDE  2    -    -        -        2026-04-27T22:25:35  -
ETHUSDT_Sell_20260427222534                  ETHUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T22:25:34  -
ETHUSDT_Sell_20260427221036                  ETHUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T22:10:36  -
BTCUSDT_Buy_20260427220343                   BTCUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-27T22:03:43  -
ETHUSDT_Sell_20260427220342                  ETHUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T22:03:42  -
BTCUSDT_Sell_20260427215533                  BTCUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T21:55:33  -
ETHUSDT_Buy_20260427215532                   ETHUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-27T21:55:32  -
AAVEUSDT_Sell_20260426223402                 AAVEUSDT   Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T22:34:02  -
CRVUSDT_Buy_20260426222300                   CRVUSDT    Buy    100.0  CLAUDE  2    -    -        -        2026-04-26T22:23:00  -
INJUSDT_Buy_20260426180851                   INJUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T18:08:51  -
INJUSDT_Buy_20260426174323                   INJUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T17:43:23  -
AXSUSDT_Buy_20260426044349                   AXSUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:43:49  -
HYPERUSDT_Buy_20260426044348                 HYPERUSDT  Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:43:48  -
HYPERUSDT_Buy_20260426041017                 HYPERUSDT  Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:10:17  -
AXSUSDT_Buy_20260426041017                   AXSUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:10:17  -
DYDXUSDT_Sell_20260426023930                 DYDXUSDT   Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T02:39:30  -
ALICEUSDT_Buy_20260426021914                 ALICEUSDT  Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T02:19:14  -
MAGMAUSDT_Buy_20260426014021                 MAGMAUSDT  Buy    100.0  CLAUDE  2    -    -        -        2026-04-26T01:40:21  -
BASEDUSDT_Sell_20260426012658                BASEDUSDT  Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T01:26:58  -
ALGOUSDT_Buy_20260426011136                  ALGOUSDT   Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T01:11:36  -
TRUMPUSDT_Sell_20260426004117                TRUMPUSDT  Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T00:41:17  -
WCTUSDT_Buy_20260426004113                   WCTUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T00:41:13  -
MOVRUSDT_Buy_20260426003028                  MOVRUSDT   Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T00:30:28  -
ORCAUSDT_Buy_20260426003026                  ORCAUSDT   Buy    100.0  CLAUDE  2    -    -        -        2026-04-26T00:30:26  -
TRUMPUSDT_Sell_20260426000419                TRUMPUSDT  Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T00:00:00  -
```

### What's contributing to 0 wins (data documentation only)

1. **`strategy_trades` does not get its closure fields written.** All 1219 `claude_trader` rows have `pnl IS NULL`, `was_win IS NULL`, `exit_time IS NULL` (verified: `SELECT COUNT(*) AS total, SUM(CASE WHEN pnl IS NULL THEN 1 ELSE 0 END) AS null_pnl, SUM(CASE WHEN was_win IS NULL THEN 1 ELSE 0 END) AS null_was_win FROM strategy_trades` → `1219|1219|1219`). The insert path is `src/core/trade_recorder.py:88-110` (only 12 columns, no pnl/exit/was_win fields). A `grep -rnE "UPDATE strategy_trades|strategy_trades.*SET" src/` returns 0 matches — there is **no UPDATE statement against `strategy_trades` anywhere in `src/`**, so the docstring in `trade_recorder.py:72-73` ("PnL fields … are updated later when the trade closes via TradeCoordinator callbacks") is unfulfilled in the current codebase.
2. **`strategy_performance` IS being updated**, via `WorkerManager._update_strategy_performance` (`src/workers/manager.py:1967-2018`). It computes new totals/wr/avg_pnl_pct from a closure record and `INSERT OR REPLACE`s the row. Most recent updates: ETHUSDT @ 22:29:49, BTCUSDT @ 22:29:00 (today). So per-symbol aggregates are accurate; only the per-row `strategy_trades` audit log is missing closure data.
3. **The 6 trades on 2026-04-27 (per `daily_pnl`) match the recent rows.** Looking at created_at on `strategy_trades` for 2026-04-27: 7 trades on that date — BTCUSDT×2 (Buy 22:25, Sell 21:55), ETHUSDT×3 (Sell 22:25, Sell 22:10, Sell 22:03, Buy 21:55), one BTCUSDT Buy 22:03. The `daily_pnl` row says trades=6 wins=1 losses=5 — meaning by the snapshot moment, 6 had closed and 1 was a win.
4. **Per-symbol BTC/ETH cumulative WR is 43.1%/43.1%** with avg_pnl_pct of −0.0104%/−0.0354% (entries 9 & 10 above). Both updated 22:29 today. Cumulatively, claude_trader on BTCUSDT is 28W / 37L, ETHUSDT 31W / 41L — i.e., persistently sub-50% on the highest-volume coins, with average PnL pct around −0.01% to −0.04%.
5. **Recent universe of trades is dominated by BTC / ETH and a long tail of low-volume alts:** of the 25 most-recent rows above, 7 are BTC/ETH and the rest are coins each with 1–2 trades total in `strategy_performance` and most with 0 wins (CRVUSDT 0/2, INJUSDT 0/2, AXSUSDT 0/1, HYPERUSDT 0/2, DYDXUSDT 0/2, MOVRUSDT 0/3, TRUMPUSDT 0/2, WCTUSDT 0/1, ZAMAUSDT 0/1).
6. **Mode is NORMAL during this window** (live `STRAT_PNL_GATE`: `halted=N rsn=ok pnl_pct=+0.00 wins=0 losses=2`), so no PnL-mode-tightening is in effect — the threshold is 50, and 7 of 10 setups survive per cycle (D1.8). The trade-source field on every recent `strategy_trades` row is `claude_trader` / category `CLAUDE` (per `_execute_claude_trade` → `record_strategy_trade`), so all observed 0-win trades came through the Claude direct execution path, not through any A1–K4 strategy executor.

The data shows the 0-wins-from-6-trades observation is consistent with the underlying claude_trader cumulative win rate (~43.1% on BTC/ETH; lower on first-time symbols where insufficient samples skew toward 0%). No closed-trade audit on a per-row basis is available in the snapshot DB because `strategy_trades` closure fields are never written.


================================================================================
FILE: E1_scanner_worker.md
================================================================================

# E1 — Scanner Worker Forensic Data

**Capture timestamp (UTC):** 2026-04-27 23:03:16
**Source code file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner_worker.py` (1090 lines)
**Primary live log:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
**Rotated log used for ranking range / 7-cycle window:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.2026-04-27_01-31-00_169356.log`
**DB snapshot:** `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
**Config:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml`

---

## E.1.1 — File overview (every method, one-liner)

| Line | Symbol | Purpose |
|------|--------|---------|
| L40 | `class ScannerWorker(SweetSpotWorker)` | Layer 1D cycle trigger — qualifies + ranks + builds packages. |
| L57 | class attr `worker_tier = WorkerTier.LAYER1D` | Sub-layer assignment used by LayerManager. |
| L59 | class attr `cycle_gated = True` | Skip tick when `LayerManager.is_cycle_active()` is False. |
| L61–L76 | `__init__(settings, db, scanner, services=None)` | Stores DI handles; sweet-spot from `settings.workers.sweet_spots.scanner_worker` ("4:00"). |
| L80–L95 | `_get_setup_score(coin)` | Defensive accessor on `services["structure_worker"].get_setup_score` (0–100). |
| L97–L108 | `_get_strategy_score(coin)` | Defensive accessor on `services["strategy_worker"].get_score` (~0–100). |
| L110–L124 | `_get_signal_confidence(coin)` | Defensive accessor on `services["signal_worker"].get_signal(coin).confidence`. |
| L126–L152 | `_get_regime_alignment(coin)` | Returns -1..+1: trending_up/trending_down→+1, volatile→+0.5, ranging→0, dead/unknown/None→-1. |
| L154–L170 | `_get_funding_strength(coin)` | Returns `abs(funding_rate)` from altdata_worker. |
| L174–L215 | `_compute_opportunity_score(coin)` | Composite weighted score (returns `(score, breakdown)`). |
| L217–L234 | `_open_position_symbols()` async | Returns set of symbols with currently open positions (HR-3 force-include). |
| L238–L252 | `_regime_aligns(regime, direction)` static | True if direction long matches `trending_up`/`ranging`, short matches `trending_down`/`ranging`. |
| L254–L312 | `_check_blockers(symbol, structure, consensus, *, recent_loss_set=None)` | Returns list of blocker labels (funding-against-direction, manipulation_likely_session, recent_loss). |
| L316–L499 | `_build_package(symbol, score, record, forced)` | Builds CoinPackage from caches: structure, strategies, signals, alt_data, price, position. |
| L501–L605 | `_qualifies(symbol, *, recent_loss_set=None)` | The 5-criterion gate. Returns `(bool, record)`. |
| L609–L1090 | `tick()` async | Main scanner cycle: prefetch losers, qualify all 50, score survivors + forced, rank, take top max_selection, BTC/ETH force-include, build packages, validate, write `_coin_packages` cache + active_universe DB rows + `MarketScanner._active_universe`. |

### Qualification + ranking + package-build flow (line ranges)

| Phase | Lines | Description |
|------|------|------|
| Tick start / cycle_id allocation | L634–L641 | `cycle_tracker.start_cycle("layer1d")`. |
| Watch list read | L644–L649 | `self.settings.universe.watch_list` (50 coins; warn if empty). |
| Force-include open positions | L651 | `protected = await self._open_position_symbols()`. |
| Recent loser prefetch | L658–L667 | `recent_loss_symbols(self.db, hours=cfg_q.recent_failure_blocker_hours)`. |
| Qualification loop | L693–L740 | Runs `_qualifies` per coin; tallies `agg` buckets; appends to `qualified_records`. |
| `SCANNER_FILTER_AGGREGATE` log | L746–L758 | One INFO line per cycle with all bucket totals. |
| Off-watch-list open-position injection | L762–L773 | Score force-includes for any open-pos symbol outside watch_list. |
| Ranking | L776 | `qualified_records.sort(key=lambda r: r[1], reverse=True)`. |
| Selection | L778–L787 | Take top `max_selection` (15); if fewer than `min_selection` (0), output what we have. |
| BTC/ETH reference-pair inject | L805–L819 | Append BTCUSDT, ETHUSDT with score 0.0 if missing (HR-2). |
| Package build loop | L825–L883 | `_build_package()` per coin → `validate_package()` → drop on FAIL, keep on OK/WARN. |
| Cache write | L884–L897 | `lm._coin_packages = packages`; `record_write("packages")`. |
| Validation summary log | L905–L909 | `PACKAGE_VALIDATE_SUMMARY` rollup. |
| `CYCLE_FRESHNESS` log | L916–L952 | p50/p95 ages for klines, xray, packages. |
| Active-universe DB write | L992–L1017 | `DELETE`; `INSERT OR REPLACE` executemany. |
| In-memory broadcast | L1024–L1037 | `scanner.set_active_universe(new_symbols)` + subscriber callbacks. |
| Per-coin DEBUG | L1041–L1049 | `SCANNER_SELECTED` (DEBUG, currently disabled — 0 entries in logs). |
| `SCANNER_SELECT` summary | L1060–L1064 | `qualified=N selected=M forced=K watch_list=L`. |
| `SCANNER_TICK_SUMMARY` | L1070–L1076 | Legacy summary (`mean_score`, `top=`, drift). |
| `cycle_tracker.end_cycle` | L1079–L1090 | Records qualified/selected/packages and ends the cycle. |

---

## E.1.2 — `_qualifies()` — FULL METHOD VERBATIM

Source: `src/workers/scanner_worker.py` lines 501–605.

```python
    def _qualifies(
        self,
        symbol: str,
        *,
        recent_loss_set: set[str] | None = None,
    ) -> tuple[bool, dict]:
        """Layer 1 restructure Phase 5 — apply 5-criterion qualitative checklist.

        Order matters — the implementation short-circuits at the first
        failed check so that ``record["reasons_failed"]`` shows the
        first failing criterion (the most useful debugging hint).

        Args:
            symbol: Watch_list coin to evaluate.
            recent_loss_set: Optional pre-computed set of symbols that
                closed at a loss within ``cfg.recent_failure_blocker_hours``.
                Forwarded to ``_check_blockers`` so the recent-loss
                blocker is one set membership lookup, not a per-coin DB
                query. ``tick`` populates this once per cycle.

        Returns:
            ``(qualified, record)`` where record has keys
            ``reasons_passed``, ``reasons_failed``, and ``blockers``.
        """
        cfg = self.settings.scanner.qualitative
        record: dict = {
            "reasons_passed": [],
            "reasons_failed": [],
            "blockers": [],
        }

        # Criterion 1 — XRAY setup type identified.
        sw = self.services.get("structure_worker")
        structure = None
        try:
            cache = getattr(sw, "_cache", None) if sw else None
            structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
        except Exception:
            structure = None
        if structure is None:
            record["reasons_failed"].append("no_xray_analysis")
            return False, record
        setup_type = getattr(structure, "setup_type", None)
        if setup_type is None or getattr(setup_type, "value", "none") == "none":
            record["reasons_failed"].append("no_xray_setup_type")
            return False, record
        record["reasons_passed"].append(f"xray_setup={setup_type.value}")

        # Criterion 2 — ensemble consensus.
        lm = self.services.get("layer_manager")
        consensus = None
        if lm and hasattr(lm, "get_strategy_consensus"):
            consensus = lm.get_strategy_consensus(symbol)
        accept = {"STRONG"} if cfg.min_consensus == "STRONG" else {"STRONG", "GOOD"}
        if consensus is None or consensus.get("consensus") not in accept:
            label = consensus.get("consensus") if consensus else "NONE"
            record["reasons_failed"].append(f"consensus={label}")
            return False, record
        record["reasons_passed"].append(f"consensus={consensus['consensus']}")

        # Criterion 3 — regime alignment.
        if cfg.require_regime_alignment:
            rw = self.services.get("regime_worker")
            regime_label = ""
            if rw and hasattr(rw, "get_regime"):
                try:
                    state = rw.get_regime(symbol)
                    if state is not None:
                        regime_label = (
                            state.regime.value
                            if hasattr(state.regime, "value")
                            else str(state.regime)
                        )
                except Exception:
                    regime_label = ""
            direction = consensus.get("direction", "")
            if not self._regime_aligns(regime_label, direction):
                record["reasons_failed"].append(
                    f"regime={regime_label or 'unknown'}_vs_{direction}"
                )
                return False, record
            record["reasons_passed"].append(f"regime={regime_label}_aligns_{direction}")

        # Criterion 4 — RR ratio.
        rr = 0.0
        try:
            sp = getattr(structure, "structural_placement", None)
            rr = float(getattr(sp, "rr_ratio", 0.0) or 0.0) if sp else 0.0
        except Exception:
            rr = 0.0
        if rr < cfg.min_rr_ratio:
            record["reasons_failed"].append(f"rr={rr:.2f}_below_{cfg.min_rr_ratio}")
            return False, record
        record["reasons_passed"].append(f"rr={rr:.2f}")

        # Criterion 5 — no blockers.
        blockers = self._check_blockers(
            symbol, structure, consensus, recent_loss_set=recent_loss_set,
        )
        if blockers:
            record["blockers"] = blockers
            record["reasons_failed"].append(f"blockers={','.join(blockers)}")
            return False, record

        return True, record
```

### Per-criterion documentation

#### Criterion 1 — XRAY setup type identified  (L532–L547)

- **Source cache:** `services["structure_worker"]._cache` (a `StructureCache` instance from `src/analysis/structure/structure_cache.py`).
- **Lookup:** `cache.get(symbol)` returns `StructuralAnalysis | None`.
- **Pass condition (must satisfy BOTH):**
  1. `structure is not None` (cache hit AND not expired by TTL).
  2. `structure.setup_type is not None AND structure.setup_type.value != "none"`.
- **What counts as "has setup type":** `setup_type` is the `SetupType` enum (`src/analysis/structure/models/structure_types.py`). Live observed values from `XRAY_CLASSIFY` log entries: `bearish_fvg_ob`. Any `SetupType.value` other than `"none"` qualifies. `XRAY_NONE_REASON` log lines (e.g., `2026-04-27 22:20:45.024 sym=CRVUSDT closest_type=BEARISH_FVG_OB missed_by='no_short_downtrend_align(...)'`) indicate the analyzer wrote `setup_type.value == "none"` to the cache.
- **First-failure short-circuit reasons:**
  - `"no_xray_analysis"` — cache.get returned None (entry missing or expired). Live counter: `fail_no_xray=25` per cycle (see E.1.6).
  - `"no_xray_setup_type"` — entry exists but value == "none". Live counter: `fail_setup_none=6..10` per cycle.

#### Criterion 2 — Strategy ensemble consensus  (L550–L559)

- **Source cache:** `services["layer_manager"].get_strategy_consensus(symbol)` (defined `src/core/layer_manager.py:1388`), which reads `self._strategy_consensus` (dict written by StrategyWorker L3).
- **Returned dict keys:** `{"consensus", "consensus_score", "vote_count", "direction", "last_updated"}`.
- **Acceptance set:** `accept = {"STRONG"} if cfg.min_consensus == "STRONG" else {"STRONG", "GOOD"}`.
  - With config `min_consensus = "GOOD"` (config.toml L401): `accept = {"STRONG", "GOOD"}`.
  - `WEAK`, `LEAN`, `CONFLICT`, `NONE`, missing all FAIL.
- **Pass condition:** `consensus is not None AND consensus.get("consensus") in accept`.
- **Live distribution (cycle c-2026-04-27-22:20, STRAT_L3_DONE @ 22:21:37.784):**
  `consensus_dist=[STRONG:4, WEAK:4, GOOD:3, LEAN:1]` — total 12; only STRONG(4) + GOOD(3) = **7 coins** could pass criterion 2 ; the other ~38 of the watch list either had no consensus entry OR had `WEAK`/`LEAN`/`NONE`.

#### Criterion 3 — Regime alignment  (L562–L582)

- Gated by `cfg.require_regime_alignment` (config.toml L402: `true`).
- **Source:** `services["regime_worker"].get_regime(symbol)` returns `RegimeState | None`; `state.regime.value` is the label.
- **Direction:** `consensus.get("direction", "")` from criterion 2's consensus dict.
- **`_regime_aligns(regime, direction)` (L238–L252) match table:**

| Regime label substring | direction="long" | direction="short" |
|---|---|---|
| `"trending_up"` | True | False |
| `"trending_down"` | False | True |
| `"ranging"` / `"rang"` | True | True |
| `"volatile"` | False | False |
| anything else (incl. `"dead"`, `""`) | False | False |

- Match is by substring (`in regime_name`).  Empty `direction` (consensus dict missing `"direction"`) → both branches return False.
- **Live distribution (STRAT_REGIME_DIST @ 22:21:30.014):** `up=0 down=20 ranging=23 volatile=6 dead=0 other=0 total=49 global=ranging`.

#### Criterion 4 — RR ratio  (L584–L594)

- **Source:** `structure.structural_placement.rr_ratio` (already loaded in criterion 1).
- **Threshold:** `cfg.min_rr_ratio` from config.toml L400: `min_rr_ratio = 2.0`.
- **Pass condition:** `rr >= 2.0`. Reads via `getattr(sp, "rr_ratio", 0.0) or 0.0` so missing/None → 0.0 → fail.
- **Source of threshold:** `[scanner.qualitative] min_rr_ratio = 2.0` (config.toml L400). Mirrored at config.toml L907 (`min_rr_ratio = 2.0` in another section).

#### Criterion 5 — Blockers  (L596–L603, calls `_check_blockers` L254–L312)

Blockers evaluated (in order, all collected — list returned, non-empty fails):

1. **`funding_against_long_rate=...`** / **`funding_against_short_rate=...`** (L279–L294) — set when `|rate|` exceeds `cfg.funding_blocker_threshold_pct = 0.001` (config.toml L403, 0.1%) AND the rate sign opposes `consensus["direction"]`.
2. **`manipulation_likely_session`** (L297–L302) — set when `structure.session_context.manipulation_likely == True`.
3. **`recent_loss_within_{N}h`** (L307–L310) — set when `symbol in recent_loss_set` (pre-fetched once per tick at `tick` L658–L667 via `src.core.trade_recorder.recent_loss_symbols`). Lookback `cfg.recent_failure_blocker_hours = 1` (config.toml L404).

Live observed counter: `fail_blockers=0` for every recent cycle (no blocker has fired in the captured window).

---

## E.1.3 — XRAY freshness gate

There is **no explicit freshness gate inside `_qualifies` or `_build_package`.** Freshness is enforced implicitly by the underlying cache:

- **File:** `src/analysis/structure/structure_cache.py`
- **Class:** `StructureCache`
- **Default TTL:** `DEFAULT_TTL = 300.0` (5 min) — `structure_cache.py:15`.
- **Read method (L31–L44):**
  ```python
  def get(self, symbol: str) -> StructuralAnalysis | None:
      cached = self._cache.get(symbol)
      if cached:
          cache_time, result = cached
          if time.monotonic() - cache_time < self._ttl:
              self._hits += 1
              return result
      self._misses += 1
      return None
  ```
  → entries older than 300 s are silently treated as missing (returned as None, miss counted).

### Why this rejects 25 of 50 per cycle

`StructureWorker` runs with `batch_size = 25` (config.toml L925, file `src/workers/structure_worker.py:82`). Sweep order (L341): `batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]` then advance with wrap-around. With 50 watch-list coins this gives a 2-tick rolling sweep — every 5-min interval refreshes 25 coins; the other 25 sit untouched.

- TTL = 300 s = 5 min.
- Sweet-spot interval = 5 min.
- The *previous* batch was written ~5 min ago, so by the time the scanner ticks at sweet-spot 4:00, those entries are at age ≈ 300 s and just past the TTL line.

Live evidence (workers.log):
- `2026-04-27 22:20:45.581 XRAY_TICK_SUMMARY | universe=50 batch=0/2 symbols=25 analyzed=25 cached=50 setups=12 skips=18`
- `2026-04-27 22:20:45.581 XRAY_CACHE_HEALTH | size=50 oldest_age_s=301 hits=139 misses=199 hit_rate=0.41`
- `2026-04-27 22:25:47.330 XRAY_CACHE_HEALTH | size=50 oldest_age_s=302 hits=165 misses=234 hit_rate=0.41`
- `CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20 ... xray_age_p50_ms=194995 xray_age_p95_ms=494936 ... xray_keys=50` — p95 (495 s) is past TTL.
- All five recent SCANNER_FILTER_AGGREGATE entries report `fail_no_xray=25` (cycles 22:00, 22:10, 22:15, 22:20, 22:24… see E.1.5 table).

Cache field `size=50` because `set()` does not auto-evict; entries linger but `get()` returns None when stale, so 25 are functionally absent each cycle.

---

## E.1.4 — Composite scoring

### Formula (scanner_worker.py L182–L208)

```
struct_norm  = clip( (StructureWorker.get_setup_score(coin) or 0.0) / 100.0, 0..1 )
strat_norm   = clip( (StrategyWorker.get_score(coin) or 0.0) / 100.0,       0..1 )
sig_norm     = clip( SignalWorker.get_signal(coin).confidence or 0.0,       0..1 )
regime_align = _get_regime_alignment(coin)        # -1..+1
regime_norm  = (regime_align + 1.0) / 2.0          # 0..1
funding_norm = clip( |altdata_worker.get_funding(coin) or 0.0| / 0.001, 0..1 )

score = w_structure * struct_norm
      + w_strategy  * strat_norm
      + w_signal    * sig_norm
      + w_regime    * regime_norm
      + w_funding   * funding_norm
```

Missing components (None) contribute 0.

### Weights — `[scanner.scoring_weights]`, config.toml L388–L393

| Component | Weight | Source |
|---|---|---|
| structure | 0.30 | config.toml:389 |
| strategy  | 0.30 | config.toml:390 |
| signal    | 0.15 | config.toml:391 |
| regime    | 0.15 | config.toml:392 |
| funding   | 0.10 | config.toml:393 |
| **Sum**   | **1.00** | |

Default values match in `src/config/settings.py:551–555`.

### Live distribution (last 50 SCANNER_TICK_SUMMARY entries available)

`SCANNER_SELECTED` per-coin DEBUG breakdown is not present in any captured log
(`grep -c SCANNER_SELECTED` → 0 in both `workers.log` and the rotated
`workers.2026-04-27_01-31-00_169356.log`). DEBUG output is disabled at the
sink — only INFO `SCANNER_TICK_SUMMARY` aggregate is available, with
per-cycle `mean_score` and `top=COIN(score)`.

50 most-recent `SCANNER_TICK_SUMMARY` `mean_score` and `top` entries (chronological,
from `workers.2026-04-27_01-31-00_169356.log` and `workers.log`):

| Cycle (HH:MM) | mean_score | top coin | top score |
|---|---|---|---|
| 03:54 | 0.581 | ETHUSDT | 0.754 |
| 03:59 | 0.586 | CRVUSDT | 0.751 |
| 04:04 | 0.601 | LINKUSDT | 0.756 |
| 04:09 | 0.576 | CRVUSDT | 0.757 |
| 04:14 | 0.584 | ETHUSDT | 0.747 |
| 04:19 | 0.570 | CRVUSDT | 0.751 |
| 04:24 | 0.580 | ETHUSDT | 0.747 |
| 04:29 | 0.568 | CRVUSDT | 0.757 |
| 04:34 | 0.583 | ETHUSDT | 0.768 |
| 04:39 | 0.563 | CRVUSDT | 0.751 |
| 04:44 | 0.577 | ETHUSDT | 0.747 |
| 04:49 | 0.567 | CRVUSDT | 0.751 |
| 04:54 | 0.577 | RENDERUSDT | 0.763 |
| 04:59 | 0.561 | CRVUSDT | 0.751 |
| 05:04 | 0.561 | RENDERUSDT | 0.748 |
| 05:09 | 0.541 | AAVEUSDT | 0.750 |
| 05:14 | 0.545 | ETHUSDT | 0.694 |
| 05:19 | 0.537 | MONUSDT | 0.725 |
| 05:24 | 0.547 | ETHUSDT | 0.691 |
| 05:29 | 0.520 | MONUSDT | 0.695 |
| 05:34 | 0.553 | ETHUSDT | 0.703 |
| 05:39 | 0.519 | MONUSDT | 0.689 |
| 05:44 | 0.539 | ONDOUSDT | 0.693 |
| 05:49 | 0.513 | MONUSDT | 0.689 |
| 05:54 | 0.536 | ONDOUSDT | 0.679 |
| 05:59 | 0.508 | MONUSDT | 0.683 |
| 06:04 | 0.482 | RUNEUSDT | 0.648 |
| 06:09 | 0.509 | DYDXUSDT | 0.681 |
| 06:14 | 0.482 | RUNEUSDT | 0.642 |
| 06:24 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 06:29 | 0.214 | DYDXUSDT | 0.642 |
| 06:34 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 06:39 | 0.202 | DYDXUSDT | 0.606 |
| 06:44 | 0.212 | RUNEUSDT | 0.636 |
| 06:49 | 0.308 | BLURUSDT | 0.627 |
| 21:54 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 21:59 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 22:04 | 0.027 | BTCUSDT  | 0.055 |
| 22:09 | 0.123 | BTCUSDT  | 0.247 |
| 22:14 | 0.196 | ETHUSDT  | 0.392 |
| 22:19 | 0.000 | BTCUSDT  | 0.000 (forced) |
| 22:24 | 0.000 | BTCUSDT  | 0.000 (forced) |

**Regime change in the data:** Pre-cycle ~06:24 the scanner ran the legacy code path (file shows `tick:329` line numbers) — Phase-5 qualitative gate was off, all 50 scored, top scores 0.7+. From 06:24 onward it ran the new gate (line numbers `tick:746`/`tick:1060`/`tick:1070`); top scores collapsed to 0.0 (BTC/ETH forced) or ≤0.4 when 1–2 coins qualified.

---

## E.1.5 — Selection logic

### Thresholds — `[scanner.qualitative]` config.toml L399–L406

| Setting | Value |
|---|---|
| `max_selection` | 15 |
| `min_selection` | 0 |
| `min_consensus` | "GOOD" |
| `min_rr_ratio` | 2.0 |
| `require_regime_alignment` | true |
| `funding_blocker_threshold_pct` | 0.001 |
| `recent_failure_blocker_hours` | 1 |

### Selection logic (scanner_worker.py L778–L819)

```
if len(qualified_records) >= n_max:           # 15
    selected = qualified_records[:n_max]
elif len(qualified_records) >= n_min:         # 0
    selected = qualified_records
else:
    selected = qualified_records              # min_selection often 0 today
# BTC/ETH always appended if absent (HR-2 reference pair)
```

`min_selection = 0` means the scanner never falls back; with zero qualifiers `selected` is empty and only BTC/ETH (force=True, score=0.0) end up in `final`.

### Force-include logic (HR-2 / HR-3)

1. **Open positions** (L651, L725): `_open_position_symbols()` returns set, then `forced = (coin in protected) and not qualified` — open-position symbols on watch_list bypass the gate; off-watch-list open-position symbols are scored separately L762–L773.
2. **BTC/ETH reference pairs** (L805–L819): always appended at the end with score 0.0 if missing from `new_symbols`.

### Last 7 cycle outputs (qualified, selected, forced)

Source: `workers.log` SCANNER_FILTER_AGGREGATE + SCANNER_SELECT pairs (L746, L1060). Newest first.

| Cycle id | qualified | selected | forced | watch_list | top coin | mean_score |
|---|---|---|---|---|---|---|
| c-2026-04-27-22:20 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |
| c-2026-04-27-22:15 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |
| c-2026-04-27-22:10 | 0 | 2 | 2 | 50 | ETHUSDT(0.392) | 0.196 |
| c-2026-04-27-22:05 | 0 | 2 | 2 | 50 | BTCUSDT(0.247) | 0.123 |
| c-2026-04-27-22:00 | 0 | 2 | 2 | 50 | BTCUSDT(0.055) | 0.027 |
| c-2026-04-27-21:55 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |
| c-2026-04-27-21:50 | 0 | 2 | 2 | 50 | BTCUSDT(0.000) | 0.000 |

Per-cycle filter aggregate (same 7 cycles):

| Cycle | total | qualified | fail_no_xray | fail_setup_none | fail_consensus | fail_regime | fail_rr | fail_blockers | pass_xray | pass_consensus_strong | pass_consensus_good |
|---|---|---|---|---|---|---|---|---|---|---|---|
| c-2026-04-27-22:20 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 |
| c-2026-04-27-22:15 | 50 | 0 | 25 | 6 | 14 | 2 | 3 | 0 | 19 | 4 | 1 |
| c-2026-04-27-22:10 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 |
| c-2026-04-27-22:05 | 50 | 0 | 25 | 6 | 15 | 1 | 3 | 0 | 19 | 4 | 0 |
| c-2026-04-27-22:00 | 50 | 0 | 25 | 10 | 13 | 0 | 2 | 0 | 15 | 2 | 0 |
| c-2026-04-27-21:55 | 50 | 0 | 25 | 5 | 19 | 0 | 1 | 0 | 20 | 0 | 1 |
| c-2026-04-27-21:50 | 50 | 0 | 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

`c-2026-04-27-21:50` is a cold-start: structure cache was empty for ALL 50 coins.

---

## E.1.6 — 7 consecutive `qualified=0` cycles — full forensic trace

**Selected cycle for 50×5 trace:** `c-2026-04-27-22:20` (`SCANNER_FILTER_AGGREGATE` emitted at `2026-04-27 22:24:00.014`).

Workflow timing in this cycle:
- StructureWorker tick @ 22:20:45 — `XRAY_TICK_SUMMARY universe=50 batch=0/2 symbols=25 analyzed=25 errors=0 cached=50 session=late_ny(mid) setups=12 skips=18 el=579ms` ; `XRAY_CACHE_HEALTH size=50 oldest_age_s=301`.
- StrategyWorker tick @ 22:21:37 — `STRAT_L3_DONE consensus=12 consensus_dist=[STRONG:4, WEAK:4, GOOD:3, LEAN:1]`; `STRAT_L4_HANDOFF score_cache_size=19 consensus_size=19 consensus_summary_size=6 hints_top20_size=7`.
- AltDataWorker tick @ 22:21:52 — `ALTDATA fg=None funding=50 oi=0 el=7273ms`.
- ScannerWorker tick @ 22:24:00.

### Bucket totals (verbatim from log)

```
SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-04-27-22:20 total=50 qualified=0
fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1
fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0
```

Sum: 25 + 10 + 12 + 2 + 1 + 0 = 50 ✓

### Coin-by-coin bucket assignment

The scanner reads:
- StructureCache via `services["structure_worker"]._cache.get(symbol)` — **only the 25 coins analyzed at 22:20:45 (batch 0/2) have fresh entries.** The other 25 (batch 1/2 — last analyzed at 22:15:45) are at TTL=300 s+ and silently return None.
- `services["layer_manager"].get_strategy_consensus(symbol)` — 12 entries fresh from the 22:21:37 L3 cycle.
- `services["regime_worker"].get_regime(symbol)` — `per_coin_size=49`.

**Batch 1/2 — all 25 fail at criterion 1 with `no_xray_analysis`:** BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, ARBUSDT, NEARUSDT, ATOMUSDT, INJUSDT, RENDERUSDT, ONDOUSDT, ENAUSDT, PYTHUSDT, SEIUSDT, AEROUSDT, RUNEUSDT, GALAUSDT, MANAUSDT, SANDUSDT, AXSUSDT, LDOUSDT.
*Inferred from `XRAY_(CLASSIFY|NONE_REASON)` lines at 22:20:45 — these 25 coins are NOT logged at 22:20:45 (their last analysis was 22:15:45, batch 1/2; logged at 22:25:47.329 next cycle as the rolling sweep returns to them).*

**Batch 0/2 — 25 coins analyzed at 22:20:45**, partitioned by setup_type from `XRAY_(CLASSIFY|NONE_REASON)` lines:

`pass_xray=15` (XRAY_CLASSIFY, setup_type != "none"):
DYDXUSDT (bearish_fvg_ob conf=0.55 score=100), ICPUSDT (bearish_fvg_ob conf=0.70 score=100), IMXUSDT (conf=0.55 score=49), HBARUSDT (conf=0.55 score=98), GMTUSDT (conf=0.55 score=49), FILUSDT (conf=0.55 score=100), MNTUSDT (conf=0.55 score=83), MONUSDT (conf=0.55 score=93), PLUMEUSDT (conf=0.55 score=100), BLURUSDT (conf=0.55 score=100), OPUSDT (conf=0.55 score=49), APTUSDT (conf=0.55 score=30), LTCUSDT (conf=0.55 score=30), BCHUSDT (conf=0.55 score=64), ALICEUSDT (conf=0.55 score=100).

`fail_setup_none=10` (XRAY_NONE_REASON, setup_type=="none"):
CRVUSDT, AAVEUSDT, HYPEUSDT, SKRUSDT, EGLDUSDT, ALGOUSDT, BSBUSDT, KATUSDT, HYPERUSDT, ORCAUSDT.

### Sample fail-bucket coins with EXACT input values

#### Bucket: `fail_no_xray=25` (sample 3 — all return None from `cache.get`)

For all 25, `services["structure_worker"]._cache.get(symbol) → None` because their entry was last set at `2026-04-27 22:15:45` (5 min before scanner tick at 22:24:00), age ≈ 495 s ≥ TTL 300 s.

| Symbol | Last `XRAY_CLASSIFY/NONE_REASON` event | Cache age at scanner read | Returned |
|---|---|---|---|
| BTCUSDT | 22:15:45.x batch (next batch, not in 22:20:45 logs) | ≈ 495 s | None |
| LINKUSDT | 22:15:45.x batch | ≈ 495 s | None |
| ATOMUSDT | 22:15:45.x batch | ≈ 495 s | None |

Verification: `CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20 ... xray_age_p50_ms=194995 xray_age_p95_ms=494936 xray_keys=50` (workers.log @ 22:24:00.020) — half the keys are at p95=495 s (past TTL).

#### Bucket: `fail_setup_none=10` (sample 3)

These coins have a fresh entry but `setup_type.value == "none"`. Verbatim `XRAY_NONE_REASON` lines from workers.log @ 22:20:45:

```
sym=CRVUSDT closest_type=BEARISH_FVG_OB
  missed_by='no_short_downtrend_align(dir=short,struct=ranging)'
  weakest_input=direction_alignment mtf=0.70 smc=0.70 direction=short structure=ranging

sym=AAVEUSDT closest_type=BULLISH_FVG_OB
  missed_by='no_fresh_bullish_ob;no_long_uptrend_align(dir=long,struct=ranging);
             mtf_score=0.40<fvg_ob_min=0.70'
  weakest_input=direction_alignment mtf=0.40 smc=0.25 direction=long structure=ranging

sym=ALGOUSDT closest_type=BULLISH_FVG_OB
  missed_by='no_long_uptrend_align(dir=long,struct=ranging);mtf_score=0.60<fvg_ob_min=0.70'
  weakest_input=direction_alignment mtf=0.60 smc=0.70 direction=long structure=ranging
```

In the scanner: `setup_type.value == "none"` → criterion 1 fails with `no_xray_setup_type`.

#### Bucket: `fail_consensus=12` (sample 3)

These coins passed criterion 1 (have `setup_type != "none"`) but consensus is missing or not in `{STRONG, GOOD}`. The 15 pass_xray coins: DYDX, ICP, IMX, HBAR, GMT, FIL, MNT, MON, PLUME, BLUR, OP, APT, LTC, BCH, ALICE. From `STRAT_CONSENSUS_SUMMARY total=10 GOOD=3 LEAN=1 STRONG=3 WEAK=3` (writer's filtered-down set was 10 in this cycle; STRAT_L4_HANDOFF reports cache `consensus_size=19` carrying older entries) and only 3 of the 15 pass_xray coins matched STRONG (per `pass_consensus_strong=3`).

12 of the 15 fail at criterion 2. Sample (consensus values pulled from the most recent STRAT_CONSENSUS_CHANGE entries within the 5-min window):

| Symbol | Consensus value seen | Required | Reason |
|---|---|---|---|
| DYDXUSDT | `WEAK` (changed `NONE→WEAK` @ 22:21:37.785, score=0.30) | STRONG/GOOD | `consensus=WEAK` — fail criterion 2 |
| HYPERUSDT | `LEAN` (changed `WEAK→LEAN` @ 22:21:37.785, score=0.50) — but HYPERUSDT also has setup_type=none (in fail_setup_none bucket) | STRONG/GOOD | (HYPER actually short-circuits at criterion 1) |
| ALICEUSDT | no STRAT_CONSENSUS_CHANGE in last 10 min → consensus.get("consensus") in `{WEAK, NONE}` or entry absent | STRONG/GOOD | `consensus=WEAK` or `consensus=NONE` — fail criterion 2 |
| LTCUSDT | no recent CHANGE (last logged near 22:11–22:21 cycles) — likely `WEAK` or absent | STRONG/GOOD | fail criterion 2 |

`pass_consensus_strong=3` and `pass_consensus_good=0` confirms only 3 STRONG (no GOOD) made it past criteria 1 & 2 in this cycle.

#### Bucket: `fail_regime=2` (sample 2)

3 coins pass criterion 1 + 2 (the `pass_consensus_strong=3` set). 2 of them fail criterion 3. From `STRAT_REGIME_DIST | up=0 down=20 ranging=23 volatile=6 dead=0` (22:21:30) — `up=0`. Any STRONG-consensus coin with `direction="long"` automatically fails criterion 3 unless the per-coin regime is `ranging` (no `trending_up` exists this cycle). Volatile (6 coins) always fail. Without per-coin regime entries logged at INFO, exact identities can't be paired here, but the structural cause is: 6/49 coins are in `volatile`, which `_regime_aligns` rejects for both directions. Live the failure pattern is `regime=volatile_vs_long` or `regime=volatile_vs_short`.

#### Bucket: `fail_rr=1` (sample 1)

After the 2 regime failures, 1 coin remains. It fails RR at `cfg.min_rr_ratio = 2.0`. Looking at the 15 pass_xray coins, the candidates whose XRAY_ANALYZE shows `rr<2.0` and `direction=short`: DYDXUSDT had rr_s=7.8 (would pass), ICPUSDT rr_s=2.1 (would pass), IMXUSDT rr_s=0.4 / rr=1.0(long) (fail), GMTUSDT rr_s=0.9 (fail), MNTUSDT rr_s=0.2 / rr=1.5(long) (fail). The exact identity of the `fail_rr=1` coin can't be pinned without DEBUG SCANNER_FILTER_RESULT lines (currently disabled), but the threshold is 2.0 and the input field is `structure.structural_placement.rr_ratio`.

#### Bucket: `fail_blockers=0`

No blocker fired this cycle (funding magnitudes all `< 0.001` against direction; no `manipulation_likely` session flag; no recent loss in last hour).

### Summary of root causes for `qualified=0`

1. **Half the universe is silently invalidated by TTL** — `StructureCache.TTL=300 s` matches the structure-worker sweep interval (5 min × 2 batches = 10 min full sweep) so 25 coins are always at age ~5 min and 25 coins are always at age ~10 min. The latter 25 fail `cache.get()` (returns None) → `fail_no_xray`.
2. **Of the 25 fresh structure entries, ~10 have `setup_type=none`** (analyzer rejects them on `direction_alignment`/`mtf_score`/`fresh_ob` rules) → `fail_setup_none`.
3. **Of the remaining ~15, only 3–4 have a STRONG/GOOD consensus** in `_strategy_consensus` — StrategyWorker's L3 produces 10–12 consensus dist with WEAK/LEAN dominating → `fail_consensus=12`.
4. **Regime alignment trims another 1–2** because `STRAT_REGIME_DIST` shows `up=0` and 6/49 coins are `volatile` (always fails).
5. **RR threshold trims any with `rr_ratio < 2.0`** — ~1/cycle.
6. **No blocker fires** in the captured window.

Net: 0 qualifiers.  BTC/ETH force-include yields `selected=2 forced=2`.

---

## Gaps / NOT FOUND

- **NOT FOUND — searched** workers.log + workers.2026-04-27_01-31-00_169356.log for `SCANNER_SELECTED` (per-coin DEBUG breakdown) — 0 hits in either file. Distribution of component breakdowns (struct/strat/sig/regime/funding) per selected coin cannot be measured from logs because DEBUG-level logging is disabled at the sink. Only INFO-level `SCANNER_TICK_SUMMARY mean_score` + `top=COIN(score)` is available.
- **NOT FOUND — searched** workers.log for `SCANNER_FILTER_RESULT` (per-coin DEBUG fail reason) — 0 hits. Cannot enumerate the exact failing coin per RR / regime bucket from logs alone; identification done by intersecting structure analyzer logs (`XRAY_CLASSIFY`/`XRAY_NONE_REASON`) with consensus summary and regime distribution.
- **No log of the per-coin regime label at INFO** — `STRAT_REGIME_DIST` gives the global histogram but the per-coin label is only available via the regime_worker's in-memory accessor; no INFO-level `REGIME_STATE_BY_COIN` line.


================================================================================
FILE: E2_coin_package_builder.md
================================================================================

# E2 — Coin Package Builder Forensic Data

**Capture timestamp (UTC):** 2026-04-27 23:03:16
**Source files:**
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/core/coin_package.py` (133 lines)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/core/coin_package_validator.py` (193 lines)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner_worker.py` (`_build_package` L316–L499)
**Live log:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
**Config:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` `[coin_package_validator]` (L1072–L1075)

---

## E.2.1 — `CoinPackage` schema (verbatim)

`src/core/coin_package.py` L26–L133. All fields (type + default value):

### `StructuralLevels` (L26–L33)
| Field | Type | Default |
|---|---|---|
| `current_price` | `float` | `0.0` |
| `suggested_sl` | `float` | `0.0` |
| `suggested_tp` | `float` | `0.0` |
| `rr_ratio` | `float` | `0.0` |

### `XrayBlock` (L35–L45)
| Field | Type | Default |
|---|---|---|
| `setup_type` | `str` | `"none"` |
| `setup_score` | `float` | `0.0` |
| `setup_type_confidence` | `float` | `0.0` |
| `structural_levels` | `StructuralLevels` | `StructuralLevels()` (default factory) |
| `mtf_confluence` | `str` | `""` |
| `session` | `str` | `""` |
| `session_phase` | `str` | `""` |
| `key_features` | `list[str]` | `[]` (default factory) |

### `StrategiesBlock` (L48–L55)
| Field | Type | Default |
|---|---|---|
| `fired_count` | `int` | `0` |
| `fired_strategies` | `list[str]` | `[]` (default factory) |
| `ensemble_consensus` | `str` | `"NONE"` |
| `consensus_score` | `float` | `0.0` |
| `total_score` | `float` | `0.0` |

### `SignalsBlock` (L58–L64)
| Field | Type | Default |
|---|---|---|
| `confidence` | `float` | `0.0` |
| `direction` | `str` | `"neutral"` |
| `sentiment_score` | `float` | `0.0` |
| `sentiment_articles_count` | `int` | `0` |

### `AltDataBlock` (L67–L73)
| Field | Type | Default |
|---|---|---|
| `funding_rate` | `float` | `0.0` |
| `funding_signal` | `str` | `"neutral"` |
| `oi_change_4h_pct` | `float` | `0.0` |
| `fear_greed` | `int` | `0` |

### `PriceDataBlock` (L76–L82)
| Field | Type | Default |
|---|---|---|
| `current` | `float` | `0.0` |
| `change_24h_pct` | `float` | `0.0` |
| `volume_24h_usd` | `float` | `0.0` |
| `regime` | `str` | `""` |

### `CoinPackage` (L85–L133)
| Field | Type | Default |
|---|---|---|
| `symbol` | `str` | (required, no default) |
| `qualified` | `bool` | (required, no default) |
| `opportunity_score` | `float` | (required, no default) |
| `qualification_reasons` | `list[str]` | `[]` (default factory) |
| `price_data` | `PriceDataBlock` | `PriceDataBlock()` |
| `xray` | `XrayBlock` | `XrayBlock()` |
| `strategies` | `StrategiesBlock` | `StrategiesBlock()` |
| `signals` | `SignalsBlock` | `SignalsBlock()` |
| `alt_data` | `AltDataBlock` | `AltDataBlock()` |
| `open_position` | `dict | None` | `None` |
| `blockers_observed` | `list[str]` | `[]` (default factory) |
| `built_at` | `float` | `time.time()` (default factory) |

Methods (L127–L133):
- `to_dict()` → `dataclasses.asdict(self)` (full nested dict).
- `size_bytes()` → `len(json.dumps(self.to_dict(), default=str))`.

---

## E.2.2 — `_build_package()` — VERBATIM and per-field source

Source: `src/workers/scanner_worker.py` L316–L499.

```python
    def _build_package(
        self,
        symbol: str,
        score: float,
        record: dict,
        forced: bool,
    ) -> CoinPackage:
        """Layer 1 restructure Phase 6 — build self-contained CoinPackage.

        Reads existing caches with defensive ``getattr/get`` patterns so a
        missing service degrades to sensible defaults rather than crashing
        the cycle. Missing fields contribute a note to ``blockers_observed``
        rather than failing the whole package.

        Returns:
            Fully-populated CoinPackage matching blueprint Section 11.2.
        """
        blockers_observed: list[str] = list(record.get("blockers", []))

        # ── XRAY block ────────────────────────────────────────────────
        sw = self.services.get("structure_worker")
        structure = None
        try:
            cache = getattr(sw, "_cache", None) if sw else None
            structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
        except Exception:
            structure = None

        levels = StructuralLevels()
        xray = XrayBlock(setup_type="none")
        if structure is not None:
            try:
                levels.current_price = float(getattr(structure, "current_price", 0.0) or 0.0)
                sp = getattr(structure, "structural_placement", None)
                if sp is not None:
                    direction = getattr(sp, "direction", "") or ""
                    if direction == "long":
                        levels.suggested_sl = float(getattr(sp, "long_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "long_tp_price", 0.0) or 0.0)
                    elif direction == "short":
                        levels.suggested_sl = float(getattr(sp, "short_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "short_tp_price", 0.0) or 0.0)
                    else:
                        levels.suggested_sl = float(getattr(sp, "structural_sl", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "structural_tp", 0.0) or 0.0)
                    levels.rr_ratio = float(getattr(sp, "rr_ratio", 0.0) or 0.0)

                setup_type_obj = getattr(structure, "setup_type", None)
                setup_type_value = (
                    setup_type_obj.value if setup_type_obj is not None else "none"
                )
                session = getattr(structure, "session_context", None)
                xray = XrayBlock(
                    setup_type=setup_type_value,
                    setup_score=float(getattr(structure, "setup_score", 0) or 0),
                    setup_type_confidence=float(
                        getattr(structure, "setup_type_confidence", 0.0) or 0.0
                    ),
                    structural_levels=levels,
                    mtf_confluence=str(getattr(structure, "confluence_quality", "")),
                    session=str(getattr(session, "current_session", "")) if session else "",
                    session_phase=str(getattr(session, "session_phase", "")) if session else "",
                    key_features=[],
                )
            except Exception:
                blockers_observed.append("xray_extract_failed")
        else:
            blockers_observed.append("xray_missing")

        # ── Strategies block ──────────────────────────────────────────
        lm = self.services.get("layer_manager")
        consensus = None
        if lm and hasattr(lm, "get_strategy_consensus"):
            try:
                consensus = lm.get_strategy_consensus(symbol)
            except Exception:
                consensus = None
        score_total = 0.0
        try:
            stw = self.services.get("strategy_worker")
            if stw and hasattr(stw, "get_score"):
                score_total = float(stw.get_score(symbol) or 0.0)
        except Exception:
            pass
        strategies = StrategiesBlock(
            fired_count=int((consensus or {}).get("vote_count", 0)),
            fired_strategies=[],  # detail kept in _strategy_hints, not packaged here
            ensemble_consensus=(consensus or {}).get("consensus", "NONE"),
            consensus_score=float((consensus or {}).get("consensus_score", 0.0)),
            total_score=score_total,
        )

        # ── Signals block ────────────────────────────────────────────
        sigw = self.services.get("signal_worker")
        signals = SignalsBlock(
            direction=(consensus or {}).get("direction", "neutral"),
        )
        try:
            if sigw and hasattr(sigw, "get_signal"):
                sig = sigw.get_signal(symbol)
                if sig is not None:
                    signals.confidence = float(getattr(sig, "confidence", 0.0) or 0.0)
                    if getattr(sig, "direction", None):
                        signals.direction = str(getattr(sig, "direction"))
        except Exception:
            blockers_observed.append("signal_missing")

        # ── Alt data block ───────────────────────────────────────────
        adw = self.services.get("altdata_worker")
        alt = AltDataBlock()
        try:
            if adw and hasattr(adw, "get_funding"):
                rate = adw.get_funding(symbol)
                if rate is not None:
                    alt.funding_rate = float(rate)
                    alt.funding_signal = (
                        "longs_paying" if rate > 0
                        else "shorts_paying" if rate < 0
                        else "neutral"
                    )
        except Exception:
            blockers_observed.append("funding_missing")
        try:
            fg = self.services.get("fear_greed")
            if fg and hasattr(fg, "get_latest"):
                latest = fg.get_latest()
                alt.fear_greed = int(getattr(latest, "value", 0) or 0)
        except Exception:
            pass

        # ── Price data block ─────────────────────────────────────────
        market = self.services.get("market") or self.services.get("market_service")
        price = PriceDataBlock()
        try:
            if market and hasattr(market, "get_ticker_cached"):
                t = market.get_ticker_cached(symbol)
                if t is not None:
                    price.current = float(getattr(t, "last_price", 0.0) or 0.0)
                    price.change_24h_pct = float(getattr(t, "change_24h_pct", 0.0) or 0.0)
                    price.volume_24h_usd = float(getattr(t, "volume_24h_usd", 0.0) or 0.0)
        except Exception:
            blockers_observed.append("ticker_missing")
        if structure is not None and price.current == 0.0:
            price.current = levels.current_price
        regime_worker = self.services.get("regime_worker")
        try:
            if regime_worker and hasattr(regime_worker, "get_regime"):
                state = regime_worker.get_regime(symbol)
                if state is not None:
                    price.regime = (
                        state.regime.value
                        if hasattr(state.regime, "value")
                        else str(state.regime)
                    )
        except Exception:
            pass

        # ── Open position (force-included only) ──────────────────────
        open_pos: dict | None = None
        if forced:
            try:
                pos_svc = self.services.get("position") or self.services.get("position_service")
                if pos_svc and hasattr(pos_svc, "get_position"):
                    p = pos_svc.get_position(symbol)
                    if p is not None:
                        open_pos = (
                            p.to_dict() if hasattr(p, "to_dict") else dict(getattr(p, "__dict__", {}))
                        )
            except Exception:
                blockers_observed.append("position_lookup_failed")

        return CoinPackage(
            symbol=symbol,
            qualified=not forced,
            opportunity_score=float(score),
            qualification_reasons=list(record.get("reasons_passed", [])),
            price_data=price,
            xray=xray,
            strategies=strategies,
            signals=signals,
            alt_data=alt,
            open_position=open_pos,
            blockers_observed=blockers_observed,
        )
```

### Per-field source mapping

| CoinPackage field | Source service / cache | Read method | Default if source missing | Lines |
|---|---|---|---|---|
| `symbol` | argument from caller | passed in | required | L487 |
| `qualified` | derived (`not forced`) | local | required | L489 |
| `opportunity_score` | argument from caller | passed in (computed by `_compute_opportunity_score`) | `0.0` for forced BTC/ETH | L490 |
| `qualification_reasons` | argument `record["reasons_passed"]` | local | `[]` | L491 |
| `xray.setup_type` | `services["structure_worker"]._cache.get(symbol).setup_type.value` | StructureCache.get (TTL 300 s) | **`"none"`** (XrayBlock default) | L362–L367 |
| `xray.setup_score` | `structure.setup_score` | direct attr | `0.0` | L370 |
| `xray.setup_type_confidence` | `structure.setup_type_confidence` | direct attr | `0.0` | L371–L373 |
| `xray.structural_levels.current_price` | `structure.current_price` | direct attr | `0.0` | L348 |
| `xray.structural_levels.suggested_sl` | `structure.structural_placement.{long,short,structural}_sl_price` (per direction) | direct attr | `0.0` | L353/356/359 |
| `xray.structural_levels.suggested_tp` | `structure.structural_placement.{long,short,structural}_tp_price` (per direction) | direct attr | `0.0` | L354/357/360 |
| `xray.structural_levels.rr_ratio` | `structure.structural_placement.rr_ratio` | direct attr | `0.0` | L361 |
| `xray.mtf_confluence` | `structure.confluence_quality` (str) | direct attr | `""` | L375 |
| `xray.session` | `structure.session_context.current_session` | direct attr | `""` | L376 |
| `xray.session_phase` | `structure.session_context.session_phase` | direct attr | `""` | L377 |
| `xray.key_features` | (always `[]` — not populated) | hardcoded | `[]` | L378 |
| `strategies.fired_count` | `consensus["vote_count"]` from `services["layer_manager"].get_strategy_consensus(symbol)` | dict.get | `0` | L401 |
| `strategies.fired_strategies` | hardcoded `[]` (kept in `_strategy_hints`) | hardcoded | `[]` | L402 |
| `strategies.ensemble_consensus` | `consensus["consensus"]` | dict.get | `"NONE"` | L403 |
| `strategies.consensus_score` | `consensus["consensus_score"]` | dict.get | `0.0` | L404 |
| `strategies.total_score` | `services["strategy_worker"].get_score(symbol)` | accessor | `0.0` | L394–L399 |
| `signals.confidence` | `services["signal_worker"].get_signal(symbol).confidence` | accessor | `0.0` | L417 |
| `signals.direction` | `consensus["direction"]` (then overridden by `sig.direction` if non-empty) | dict.get / accessor | `"neutral"` | L411, L418–L419 |
| `signals.sentiment_score` | NOT WRITTEN — defaults to `0.0` | n/a | `0.0` | (default) |
| `signals.sentiment_articles_count` | NOT WRITTEN — defaults to `0` | n/a | `0` | (default) |
| `alt_data.funding_rate` | `services["altdata_worker"].get_funding(symbol)` | accessor | `0.0` | L428–L430 |
| `alt_data.funding_signal` | derived from sign of `funding_rate` | local | `"neutral"` | L431–L435 |
| `alt_data.oi_change_4h_pct` | NOT WRITTEN — defaults to `0.0` | n/a | `0.0` | (default) |
| `alt_data.fear_greed` | `services["fear_greed"].get_latest().value` | accessor | `0` | L439–L443 |
| `price_data.current` | `services["market"].get_ticker_cached(symbol).last_price` (with `levels.current_price` as fallback if structure exists) | accessor + fallback | `0.0` | L453, L458–L459 |
| `price_data.change_24h_pct` | `ticker.change_24h_pct` | accessor | `0.0` | L454 |
| `price_data.volume_24h_usd` | `ticker.volume_24h_usd` | accessor | `0.0` | L455 |
| `price_data.regime` | `services["regime_worker"].get_regime(symbol).regime.value` | accessor | `""` | L463–L469 |
| `open_position` | `services["position"].get_position(symbol).to_dict()` (only when `forced=True`) | accessor | `None` | L474–L485 |
| `blockers_observed` | accumulated locally + `record["blockers"]` | local | `[]` | L333, L381, L421, L437, L457, L485 |
| `built_at` | `time.time()` at construction (CoinPackage default factory) | local | (always set) | (dataclass default) |

### Fields that default to zero/None when source is missing (silent zeros)

- `xray.setup_type = "none"` and all `structural_levels.*` zero when `cache.get(symbol) is None` (TTL miss).
- `strategies.ensemble_consensus = "NONE"`, `vote_count → fired_count = 0` when `get_strategy_consensus(symbol) is None`.
- `signals.confidence = 0.0` when `signal_worker.get_signal(symbol)` is None or accessor missing.
- `signals.sentiment_score = 0.0`, `signals.sentiment_articles_count = 0` — never populated (always zero).
- `alt_data.funding_rate = 0.0` when accessor returns None.
- `alt_data.fear_greed = 0` when fear_greed service returns None / missing — and the `int(getattr(latest, "value", 0) or 0)` collapses any falsy `value` to 0. Live: AltDataWorker logs `fg=None` on its own ticks (see workers.log @ 22:21:52, 22:26:55, 22:31:50, 22:36:54, 22:41:50 all `fg=None`); the SentimentAggregator uses a separate FearGreedService that returns `fg=47` (visible in workers.log @ 22:26:03). Whether the scanner's `services["fear_greed"]` resolves to the working FearGreedService or the broken AltDataWorker accessor is determined by service registration in WorkerManager (out of scope of this file).
- `alt_data.oi_change_4h_pct = 0.0` — never populated (always zero).
- `price_data.current = 0.0` when ticker cache miss AND no structure (otherwise falls back to `levels.current_price`).
- `price_data.regime = ""` when regime_worker returns None.

---

## E.2.3 — Package validator

Source: `src/core/coin_package_validator.py` (193 lines).

### Validation logic

Pure function `validate_package(pkg, *, fail_below, warn_below, staleness_fail_seconds, now_unix=None) → ValidationResult` (L72–L193).

**Required rules** (each contributes 1.0 to `req_score`, count `req_count`):
| Field name (label) | Pass condition | Lines |
|---|---|---|
| `symbol` | `bool(pkg.symbol) and isinstance(pkg.symbol, str)` | L121 |
| `qualified` | `isinstance(pkg.qualified, bool)` | L122 |
| `opportunity_score` | finite `float` in `[0, 1]` | L123–L128 |
| `price_data.current` | `pkg.price_data.current > 0` | L129–L134 |
| `built_at` | `now - pkg.built_at < staleness_fail_seconds` (if not, also added to `stale_fields`) | L135–L139 |

Total: 5 required fields.

**Optional rules** (each contributes 1.0 to `opt_score`, weighted at 0.5×):
| Field name (label) | Pass condition | Lines |
|---|---|---|
| `xray.setup_type` | `setup_type != "none"` | L142–L144 |
| `xray.structural_levels.suggested_sl` | only when `setup_type != "none"`, must be `> 0` | L147, L150 |
| `xray.structural_levels.suggested_tp` | only when `setup_type != "none"`, must be `> 0` | L148, L151 |
| `xray.structural_levels.rr_ratio` | only when `setup_type != "none"`, must be `> 0` | L149, L152 |
| `strategies.fired_count` | `int >= 0` | L154–L159 |
| `signals.confidence` | finite `float` in `[0, 1]` | L160–L166 |
| `price_data.regime` | non-empty | L167–L170 |
| `alt_data.fear_greed` | `int > 0` | L171–L174 |

Total: 8 optional fields, but the 3 SL/TP/RR optionals are conditional on `setup_type != "none"` (so when setup_type is "none", `opt_count = 5`; when setup_type is set, `opt_count = 8`).

### Completeness scoring formula (L177–L179)

```
denom = req_count + 0.5 * opt_count
completeness = (req_score + 0.5 * opt_score) / denom if denom > 0 else 0.0
completeness = max(0.0, min(1.0, completeness))
```

### Verdict thresholds (L181–L186)

```
if completeness < fail_below:  verdict = "fail"   (default fail_below = 0.50)
elif completeness < warn_below:  verdict = "warn"  (default warn_below = 0.85)
else:                           verdict = "ok"
```

Config (`config.toml` L1072–L1075):
```
[coin_package_validator]
fail_below = 0.50
warn_below = 0.85
staleness_fail_seconds = 300.0
```

### Quarantine action

`scanner_worker.py:866–873` — packages with verdict == `"fail"` are dropped (`continue`) and never inserted into the `_coin_packages` cache. Stage 2 never sees them.

---

## E.2.4 — Cold-start package — exact field-by-field analysis

### Cycle selected

The user-described "first package after restart" pattern is reproduced live in **cycle `c-2026-04-27-22:20`** (PACKAGE_VALIDATE @ `2026-04-27 22:24:00.017`):

```
PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=BTCUSDT
  completeness=0.67 verdict=warn
  missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']
  stale=[]
PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=ETHUSDT
  completeness=0.73 verdict=warn
  missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']
  stale=[]
PACKAGE_VALIDATE_SUMMARY | cycle_id=c-2026-04-27-22:20
  packages_built=2 ok=0 warn=2 fail_quarantined=0
```

These match the spec's expected 4 missing fields verbatim for BTCUSDT.

### Why each of the 4 fields is missing (BTCUSDT)

#### 1. `price_data.current` missing

- Path: `_build_package` L450–L455 reads `services["market"].get_ticker_cached(symbol).last_price`. If the market service or the ticker is None, `price.current` stays `0.0`.
- Fallback at L458–L459: `if structure is not None and price.current == 0.0: price.current = levels.current_price`.
- **In this cycle, BTCUSDT is in batch 1/2 of the structure worker** (analyzed at 22:15:45, age ≈ 495 s when the scanner ticks at 22:24:00). So `cache.get("BTCUSDT") → None` (TTL=300 s expired). With `structure is None`, the fallback does NOT fire. And ticker_cache for BTCUSDT may also be missing under the resolved service registration — combined effect: `price.current = 0.0`.
- Validator label: `price_data.current` (required) → fails when `<= 0`.

ETHUSDT differs: `missing` for ETH does NOT include `price_data.regime` ⇒ ETHUSDT had a regime label populated; but `price_data.current` IS in ETH's missing list, so its ticker is also unavailable AND its structure cache entry was also expired (same batch 1/2).

#### 2. `xray.setup_type` missing (counted as "missing" by the validator label `xray.setup_type` because `setup_type == "none"`)

- Path: `_build_package` L336–L383. With `cache.get("BTCUSDT") → None` (cache miss for batch 1/2), the `else` branch at L382 sets `xray = XrayBlock(setup_type="none")` (default) and appends `xray_missing` to `blockers_observed`.
- Validator: `setup_type != "none"` is the optional rule label. With value `"none"`, the optional rule fails — counted in `missing_fields` AND the conditional SL/TP/RR optional rules are skipped (so `opt_count = 5` instead of 8 for this package).
- **Confirmed cause:** structure cache TTL miss because BTCUSDT is in the *other* structure batch (last analyzed 22:15:45, age 495 s ≥ TTL 300 s at scanner read time 22:24:00).

#### 3. `price_data.regime` missing (BTCUSDT only)

- Path: `_build_package` L460–L471 reads `services["regime_worker"].get_regime(symbol).regime.value`.
- Live: `REGIME_TICK_SUMMARY universe=50 global=ranging per_coin_size=49` (workers.log @ 22:21:21.657) — regime_worker only has 49 entries. `STRAT_REGIME_DIST | up=0 down=20 ranging=23 volatile=6 dead=0 other=0 total=49` (22:21:30) — confirms 49.
- BTCUSDT is the regime worker's `primary_symbol` (config.toml L491: `primary_symbol = "BTCUSDT"`) and is excluded from `_per_coin_regimes` (it's tracked separately as the global regime). So `regime_worker.get_regime("BTCUSDT")` returns None → `price.regime = ""` (default) → validator counts this optional rule as missing.
- ETHUSDT is in the per-coin regime dict (49 includes ETH), so its `price_data.regime` IS populated and is NOT in ETH's missing list.

#### 4. `alt_data.fear_greed` missing

- Path: `_build_package` L438–L444: `services["fear_greed"].get_latest().value`.
- Live: AltDataWorker's tick reports `fg=None` consistently:
  - `2026-04-27 22:21:52.289 ALTDATA | fg=None funding=50 oi=0 el=7273ms`
  - `2026-04-27 22:26:55.156 ALTDATA | fg=None funding=50 oi=50 el=10137ms`
  - `2026-04-27 22:31:50.192 ALTDATA | fg=None funding=50 oi=0 el=5190ms`
  - `2026-04-27 22:36:54.159 ALTDATA | fg=None funding=50 oi=50 el=9156ms`
- A SentimentAggregator path does see `fg=47` (workers.log @ 22:26:03) via a separate FearGreedService. So the live FearGreed value (47) exists somewhere in the system, but the service registered as `services["fear_greed"]` (or whichever the scanner reads) returns None → `int(getattr(None, "value", 0) or 0) = 0`.
- Validator rule: `int(pkg.alt_data.fear_greed) > 0`. With value 0, optional rule fails → label `alt_data.fear_greed` added to `missing_fields`.

### Completeness math for BTCUSDT cold-start package

With `setup_type == "none"`, optional rules used (L142–L174) are **5** (the 3 SL/TP/RR optional rules are skipped):
- `xray.setup_type` (FAIL — value "none")
- `strategies.fired_count` (PASS — defaults to 0, which is `>= 0`)
- `signals.confidence` (PASS — defaults to 0.0 which is in [0,1])
- `price_data.regime` (FAIL — empty string)
- `alt_data.fear_greed` (FAIL — value 0)

Required (5): `symbol` PASS, `qualified` PASS, `opportunity_score` PASS, `price_data.current` FAIL, `built_at` PASS → req_score = 4.

Optional (5): `xray.setup_type` FAIL, `strategies.fired_count` PASS, `signals.confidence` PASS, `price_data.regime` FAIL, `alt_data.fear_greed` FAIL → opt_score = 2.

```
denom = 5 + 0.5 * 5 = 7.5
completeness = (4 + 0.5 * 2) / 7.5 = 5.0 / 7.5 = 0.6666... → rounded 0.67 ✓
```

This matches the live log (`completeness=0.67 verdict=warn`).

For ETHUSDT (no `price_data.regime` missing): req_score = 4, opt_score = 3 (regime passes).
```
completeness = (4 + 0.5 * 3) / 7.5 = 5.5 / 7.5 = 0.7333... → rounded 0.73 ✓
```

Matches live log.

### What would need to be true for `completeness = 1.0` (BTCUSDT)?

All 5 required + all relevant optional rules must pass.

1. **`price_data.current > 0`** → `services["market"].get_ticker_cached("BTCUSDT")` returns a non-None Ticker with `last_price > 0` AT scanner read time. Today this is failing because the resolved market service is not exposing fresh ticker data for BTCUSDT under the service-key "market" / "market_service" used by the scanner.
2. **`xray.setup_type != "none"`** → `services["structure_worker"]._cache.get("BTCUSDT")` must return a fresh `StructuralAnalysis` (within 300 s) AND its `setup_type.value` must be a non-"none" value (e.g. `bullish_fvg_ob`). Today this fails because BTCUSDT is in batch 1/2 — last analyzed at 22:15:45, age 495 s at scanner read at 22:24:00 ≥ TTL 300 s. Either (a) `StructureCache.TTL` must exceed 300 s by enough to cover the 5-min worst-case stride, or (b) StructureWorker must analyze the full 50 every cycle, or (c) ScannerWorker must run after both batches in the cycle. Additionally the analyzer must classify BTCUSDT to a non-"none" setup at that read.
3. **Following from (2), the 3 conditional optional rules** (`suggested_sl > 0`, `suggested_tp > 0`, `rr_ratio > 0`) become applicable; all three must be populated by `structural_placement` for the active direction.
4. **`price_data.regime` non-empty** → `services["regime_worker"].get_regime("BTCUSDT")` must return a `RegimeState` with `regime.value` non-empty. Today `regime_worker` excludes BTCUSDT from `_per_coin_regimes` because it is `primary_symbol` (config L491). Either BTCUSDT is added to per-coin coverage, or `_build_package` falls back to the global regime for BTCUSDT.
5. **`alt_data.fear_greed > 0`** → `services["fear_greed"].get_latest()` must return a record with `.value > 0`. Today AltDataWorker logs `fg=None` repeatedly; only SentimentAggregator's path reads `fg=47`. Either AltDataWorker's fear-greed fetch must succeed, or `services["fear_greed"]` must point to the working FearGreedService used by the SentimentAggregator.

When all five hold, BTCUSDT's completeness is `(5 + 0.5 * 8) / (5 + 0.5 * 8) = 9.0 / 9.0 = 1.0` (note opt_count rises to 8 once `setup_type != "none"` enables the SL/TP/RR rules).

---

## Notes / observations from the build path (NOT recommendations, just data captured during read)

- `_build_package` consistently writes to `pkg.price_data` and `pkg.alt_data` (correct field names per dataclass). However, the per-active-universe enrichment helper `_enrich_for(coin)` in `tick` (`scanner_worker.py:973–990`) reads `pkg.price.volume_24h_usd`, `pkg.price.change_24h_pct`, `pkg.alt.funding_rate`. The CoinPackage dataclass exposes those nested blocks under attribute names `price_data` and `alt_data` (see `coin_package.py:118, 122`), not `price` / `alt`. The `try/except` at L984–L989 silently returns `(0.0, 0.0, 0.0, 0.0)` on AttributeError. Live evidence: the `active_universe` table from the snapshot DB shows `volume_24h, change_24h_pct, funding_rate, spread_pct = 0.0, 0.0, 0.0, 0.0` for both BTCUSDT and ETHUSDT in cycle 22:24:
  ```
  BTCUSDT|0.0|0.0|0.0|0.0|0.0|1|2026-04-27 22:24:00
  ETHUSDT|0.0|0.0|0.0|0.0|0.0|1|2026-04-27 22:24:00
  ```
  (Documented as observation only — no fix proposed in this file.)

## Gaps / NOT FOUND

- **NOT FOUND — searched** workers.log for `PACKAGE_VALIDATE` lines for **non-forced** packages (real qualifiers) — none in the 21:50–22:24 window because every cycle had `qualified=0`. Only BTC/ETH force-included entries were validated, so the cold-start failure pattern dominates the available sample. The two warn-verdict packages in cycle 22:20 are the closest analog to "first package after restart" since the BTC/ETH forced path is functionally indistinguishable from a cold-start (no XRAY, no per-coin regime for BTC).
- **NOT FOUND — searched** `_build_package` and `coin_package.py` for any code that populates `signals.sentiment_score`, `signals.sentiment_articles_count`, or `alt_data.oi_change_4h_pct` — not written anywhere in the current builder. These fields will always be the dataclass defaults (0.0 / 0 / 0.0).
- **NOT FOUND — searched** `services["fear_greed"]` registration in WorkerManager — out of scope of this file (Module C/F territory). The exact identity of `services["fear_greed"]` (FearGreedService vs altdata_worker fallback) is not determined from logs alone.


================================================================================
FILE: F1_inter_worker_caches.md
================================================================================

# F1 — Inter-Worker In-Memory Caches

Snapshot timestamp reference: 2026-04-27 ~22:30 UTC (matches log tail).

For every shared in-memory cache used in the Layer 1 → Stage 2 pipeline,
this file records: defining file, owner (writer), every consumer reader,
key/value structure, typical size, TTL/invalidation, and a sample of
recent contents (where available).

Note on snapshots: there is no live snapshot mechanism for runtime worker
dicts; the system is running and these dicts live in worker process
memory only. Where DB-backed surrogates exist (`coin_regime_history`,
`aggregated_sentiment`, `funding_rates`, `ticker_cache`, `klines`,
`active_universe`) the latest rows are read from
`_trading_db_snapshot.db` and shown as proxies. Pure in-memory caches
(`_score_cache`, `_strategy_consensus*`, `_strategy_hints`,
`_coin_packages`, StructureCache, TACache, `_signal_cache`,
`_funding_cache`, `_ws_quotes`) cannot be snapshotted from the snapshot
DB; values come from log tail evidence (sizes, freshness ages, sample
keys/values).

---

## C1 — `_ws_quotes`  (PriceWorker)

- **Defining file:** `src/workers/price_worker.py:66` — `self._ws_quotes: dict[str, tuple[float, float]] = {}`
- **Owner / writer:** `PriceWorker` callback `_handle_ticker_update` at `src/workers/price_worker.py:196`:
  `self._ws_quotes[symbol] = (last_price, _time.monotonic())`
- **Consumers (readers):**
  - `PriceWorker.get_ws_quote(symbol, max_age_s=5.0)` at `src/workers/price_worker.py:239-257` — read at line 251.
  - Heartbeat read for log size: `src/workers/price_worker.py:156` (`quotes_cached={len(self._ws_quotes)}`).
  - No external worker imports the dict directly; access is through `get_ws_quote`. No grep hits in `src/apex/`, `src/brain/`, `src/workers/scanner_worker.py`, `src/workers/structure_worker.py` reading `_ws_quotes` or calling `get_ws_quote` (NOT FOUND for direct cross-worker reader; searched src/).
- **Key format:** symbol string (e.g. `"BTCUSDT"`).
- **Value structure:** `tuple[float, float]` = `(last_price, monotonic_seconds_at_set)`.
- **Typical size:** equal to subscribed universe — at last `PRICE_WS_HEALTH` heartbeat:
  `subscribed=50 quotes_cached={len(self._ws_quotes)}` is the format printed
  at `price_worker.py:156`. The number is not in the captured workers.log
  (no PRICE_WS_HEALTH lines in the 22:25–23:01 window we have); structurally
  it tracks 50 (the watch_list size).
- **TTL / invalidation:**
  - No expiry inside the dict itself.
  - Read-time freshness gate: `get_ws_quote(...)` rejects entries older
    than `max_age_s` (default 5.0). `src/workers/price_worker.py:255-257`.
  - Subscription-set change triggers `self._connected = False` at
    `price_worker.py:106-107` and reconnect — old entries remain but
    will only be refreshed for the new universe.
- **Sample 5 entries:** NOT FOUND — no in-memory snapshot mechanism;
  process introspection not requested (data collection only). DB
  surrogate `ticker_cache` (snapshot file) is shown in F2; first 5 rows
  at end of this file under the table’s entry.

---

## C2 — `ticker_cache` table (PriceWorker — DB persistence of WS ticks)

- **Defining file (DDL):** `src/database/migrations.py:37` (CREATE TABLE).
- **Owner / writer:** `MarketRepository.save_ticker` at
  `src/database/repositories/market_repo.py:268` (`INSERT OR REPLACE INTO ticker_cache ...`).
  Called from `PriceWorker._handle_ticker_update` via
  `loop.create_task(self.market_repo.save_ticker(ticker))`
  at `src/workers/price_worker.py:218`.
- **Consumers (readers):**
  - `MarketRepository.get_ticker` at `src/database/repositories/market_repo.py:285-296`.
  - `src/core/transformer.py:667` — `SELECT last_price, updated_at FROM ticker_cache WHERE symbol = ?`.
  - `src/intelligence/sentiment/aggregator.py:169` — `SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?`.
  - `MarketService.get_ticker` 5-second cache wraps it (mentioned at
    `src/core/layer_manager.py:1199`, `src/workers/profit_sniper.py:836`).
- **Schema (verbatim):**
  ```sql
  CREATE TABLE ticker_cache (
        symbol TEXT PRIMARY KEY,
        last_price REAL NOT NULL,
        bid REAL NOT NULL DEFAULT 0,
        ask REAL NOT NULL DEFAULT 0,
        high_24h REAL NOT NULL DEFAULT 0,
        low_24h REAL NOT NULL DEFAULT 0,
        volume_24h REAL NOT NULL DEFAULT 0,
        change_24h_pct REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  ```
- **Indexes:** PK on `symbol` only.
- **Row count (snapshot):** 200.
- **Sample 5 rows (snapshot):**
  ```
  BATUSDT       0.09915     -5.8404   17192837.2   2026-03-27T10:26:44.755669+00:00
  B3USDT        0.0004844   60.5036   40027177300.0 2026-03-27T13:01:12.009192+00:00
  VIRTUALUSDT   0.6566      -4.6747   34912353.0   2026-03-27T15:16:05.047819+00:00
  PARTIUSDT     0.09979     -6.973    120000664.0  2026-03-27T16:56:32.103897+00:00
  ANKRUSDT      0.005018    5.798     3229061554.0 2026-03-28T02:26:07.257024+00:00
  ```
  OBSERVED ANOMALY: snapshot rows show `updated_at` from late-March
  2026; the latest WS ticks for the live workers are not visible in the
  static snapshot DB taken at 22:56 — full freshness check would
  require querying the live DB. Forensic data only.

---

## C3 — `_funding_cache`  (AltDataWorker)

- **Defining file:** `src/workers/altdata_worker.py:90` —
  `self._funding_cache: dict[str, float] = {}`.
- **Owner / writer:** `AltDataWorker.tick` at
  `src/workers/altdata_worker.py:185-192` —
  ```python
  for fr in result:
      sym = getattr(fr, "symbol", None)
      rate = getattr(fr, "funding_rate", None)
      if sym and rate is not None:
          try:
              self._funding_cache[sym] = float(rate)
  ```
  Updated each funding fetch (every `funding_rates` sweet spot fire,
  default `1:45` per 5-min window).
- **Consumers (readers):**
  - `AltDataWorker.get_funding(coin)` at `src/workers/altdata_worker.py:254-261`.
  - `ScannerWorker._get_funding_strength` at `src/workers/scanner_worker.py:154-170` (calls `adw.get_funding(coin)` line 164).
  - `ScannerWorker._check_blockers` at `src/workers/scanner_worker.py:281-294`.
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:427-435`.
- **Key format:** symbol string.
- **Value structure:** `float` (funding rate, raw decimal — e.g. `0.0001` for 0.01%).
- **Typical size:** `cached_size={len(self._funding_cache)}` reported
  in `ALTDATA_FUNDING_TICK` log at `altdata_worker.py:212`. Live tail
  at 22:31:50 / 22:36:54 / 22:41:50 / 22:56:54 / 23:01:50 shows
  `ran=[funding,...]` every 5 min; size approaches universe (50).
  Workers.log fragments captured do not include the explicit
  `cached_size=` value (legacy line is below ALTDATA_TICK_DONE).
- **TTL / invalidation:** none — last value persists until overwritten
  by next tick. No staleness check at read.
- **Sample 5 entries:** NOT FOUND — no in-memory snapshot mechanism.
  DB surrogate `funding_rates` rows from snapshot:
  ```
  ALICEUSDT  0.0001       2026-04-27T22:41:50.060214+00:00
  BCHUSDT    5.26e-06     2026-04-27T22:41:49.991281+00:00
  LTCUSDT    -0.00011332  2026-04-27T22:41:49.920397+00:00
  APTUSDT    0.0001       2026-04-27T22:41:49.848534+00:00
  OPUSDT     -0.00010343  2026-04-27T22:41:49.779746+00:00
  ```

---

## C4 — `_signal_cache`  (SignalWorker)

- **Defining file:** `src/workers/signal_worker.py:67` —
  `self._signal_cache: dict[str, Signal] = {}`.
- **Owner / writer:** `SignalWorker.tick` at
  `src/workers/signal_worker.py:113` — `self._signal_cache[symbol] = signal`.
  Fires at sweet spot `1:00` (every 5-min window).
- **Consumers (readers):**
  - `SignalWorker.get_signal(coin)` at `src/workers/signal_worker.py:169-177`.
  - `ScannerWorker._get_signal_confidence` at `src/workers/scanner_worker.py:110-124` (line 114: `sw.get_signal(coin)`).
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:414-420` (line 415: `sigw.get_signal(symbol)`).
- **Key format:** symbol string.
- **Value structure:** `Signal` dataclass from `src.core.types`. Includes at minimum `signal_type`, `confidence`, `direction` (read at scanner_worker.py:417-419).
- **Typical size:** equal to processed universe per tick. Live tail
  shows `signals=50 mean_conf=0.21` (`SIG_TICK_SUMMARY` at 22:26:03).
  Cache reaches 50 each cycle.
- **TTL / invalidation:** none in-cache; overwritten each tick.
  Fresh max age ≈ 5 minutes (one window).
- **Sample 5 entries:** NOT FOUND — in-memory only. DB surrogate
  (latest 5 rows of `signals` table — same shape, persisted by
  intelligence_aggregator):
  ```
  ALICEUSDT  neutral 0.2035      intelligence_aggregator 2026-04-27T22:26:03.368403+00:00
  BCHUSDT    neutral 0.20302435  intelligence_aggregator 2026-04-27T22:26:03.347217+00:00
  LTCUSDT    neutral 0.2436106   intelligence_aggregator 2026-04-27T22:26:03.327064+00:00
  APTUSDT    neutral 0.2035      intelligence_aggregator 2026-04-27T22:26:03.310434+00:00
  OPUSDT     neutral 0.20358255  intelligence_aggregator 2026-04-27T22:26:03.289664+00:00
  ```

---

## C5 — `_per_coin_regimes`  (RegimeWorker / RegimeDetector)

- **Defining file:** `src/strategies/regime.py:40` —
  `self._per_coin_regimes: dict[str, RegimeState] = {}`.
- **Owner / writer:** `RegimeWorker.tick` at
  `src/workers/regime_worker.py:194` — `self.detector._per_coin_regimes.update(per_coin)`.
  Initial restore from DB at `regime_worker.py:111-118` (after first tick).
- **Consumers (readers):**
  - `RegimeDetector.get_coin_regime` at `src/strategies/regime.py:46-48`.
  - `RegimeWorker.get_regime` at `src/workers/regime_worker.py:300-312` — wraps `RegimeDetector.get_coin_regime`, with fallback to direct `_per_coin_regimes` lookup at line 312.
  - `StrategyWorker.tick` reads at `src/workers/strategy_worker.py:166` —
    `coin_regimes = getattr(self.regime_detector, '_per_coin_regimes', {})`.
  - `ScannerWorker._get_regime_alignment` at `src/workers/scanner_worker.py:135-138` calls `rw.get_regime(coin)`.
  - `ScannerWorker._qualifies` at `src/workers/scanner_worker.py:565-573`.
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:462-469`.
  - APEX assembler — `src/apex/assembler.py:590` uses
    `RegimeState.regime` enum value (consumer is via the same accessor route).
  - TIAS — `src/tias/collector.py:277-284` (RegimeState dataclass shape).
  - ProfitSniper — `src/workers/profit_sniper.py:1006` reads
    `getattr(self.regime_detector, "_last_regime", None)` (a separate
    field on the detector; per-coin path is `detector.detect()` fallback).
- **Key format:** symbol string.
- **Value structure:** `RegimeState` dataclass from
  `src/strategies/models/regime_types.py:42`. Fields seen in restore
  code (`regime_worker.py:111-118`): `regime` (MarketRegime enum),
  `confidence`, `adx`, `atr_percentile`, `choppiness`, `volume_ratio`,
  `trend_direction`, `active_strategy_categories`.
- **Typical size:** live tail —
  `REGIME_TICK_SUMMARY | universe=50 ... per_coin_size=49 el=9789ms drift_ms=17` (22:26:24).
  Steady state 49 of 50 (primary BTC tracked separately as `_last_regime`).
- **TTL / invalidation:** none in-cache. Persisted each tick via
  `INSERT INTO coin_regime_history` (regime_worker.py:252-258). Stale
  entries preserved across ticks.
- **Sample 5 entries (DB surrogate `coin_regime_history`):**
  ```
  ALICEUSDT  trending_down  0.61862976  2026-04-27 22:26:24
  BCHUSDT    ranging        0.4         2026-04-27 22:26:24
  LTCUSDT    ranging        0.4         2026-04-27 22:26:24
  APTUSDT    ranging        0.4         2026-04-27 22:26:24
  OPUSDT     trending_down  0.55720908  2026-04-27 22:26:24
  ```

OBSERVED ANOMALY (already noted in 22:27 monitor): `regime_history`
table for the global symbol has only 1 row at 22:21:15 and 6 rows in
the 22:00 hour, but the prior 06:00–21:00 hours contain 0 rows in our
snapshot — there is a gap from 06:00 to 21:00 (sqlite3 query
`SELECT substr(detected_at,1,13) hour, COUNT(*) ... GROUP BY hour`):
```
2026-04-27 00 .. 06    each 11–12 rows
[no rows for 06–21]
2026-04-27 21          1 row
2026-04-27 22          6 rows
```

---

## C6 — `_score_cache`  (StrategyWorker)

- **Defining file:** `src/workers/strategy_worker.py:93` —
  `self._score_cache: dict[str, float] = {}`.
- **Owner / writer:** `StrategyWorker` Layer 2 path at
  `src/workers/strategy_worker.py:588` —
  `self._score_cache[_sym] = float(_ss.total_score)`.
  Fires at sweet spot `1:30` (every 5-min window).
- **Consumers (readers):**
  - `StrategyWorker.get_score(coin)` at `src/workers/strategy_worker.py:891-900`.
  - `ScannerWorker._get_strategy_score` at `src/workers/scanner_worker.py:97-108` (line 101).
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:396-397`.
  - Internal log at `strategy_worker.py:809` — `_score_cache_size = len(...)`.
- **Key format:** symbol string.
- **Value structure:** float `total_score` from a `ScoredSetup`.
- **Typical size:** populated only for coins that produced a raw signal in the current tick.
  Live tail `STRAT_CYCLE_DONE` 22:26:39: `coins=50 signals=10 scored=10 hints=7`.
  Cache size reflected at `strategy_worker.py:821` —
  `score_cache_size={_score_cache_size}` in the
  STRAT_L4_HANDOFF event (the workers.log fragments captured did not
  include this raw line; the log emit site is verified).
- **TTL / invalidation:** none — entries persist until overwritten.
- **Sample 5 entries:** NOT FOUND — in-memory only. No DB persistence
  of `_score_cache` values (it is the wrapper around L2 total_score
  which is not separately tabled).

---

## C7 — `_strategy_consensus`  (LayerManager — owned, written by StrategyWorker)

- **Defining file:** `src/core/layer_manager.py:104` —
  `self._strategy_consensus: dict[str, dict] = {}` (owner is `LayerManager`).
- **Owner / writer:** `StrategyWorker.tick` at
  `src/workers/strategy_worker.py:720-734` (Phase 3 — written EVERY
  tick, NOT under Layer 3 gate):
  ```python
  if layer_manager:
      new_consensus = self._build_per_coin_consensus(consensus_setups)
      existing = getattr(layer_manager, "_strategy_consensus", {}) or {}
      ... existing.update(new_consensus) ...
      layer_manager._strategy_consensus = existing
  ```
- **Consumers (readers):**
  - `LayerManager.get_strategy_consensus(symbol)` at
    `src/core/layer_manager.py:1388-1400` (`return self._strategy_consensus.get(symbol)`).
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:388-390` (line 390: `consensus = lm.get_strategy_consensus(symbol)`).
  - `ScannerWorker._qualifies` at `src/workers/scanner_worker.py:552-553`.
  - `Strategist` (Brain) — `src/brain/strategist.py:1029-1035` and `1734-1741` falls back to `_strategy_consensus` if `_strategy_consensus_summary` missing.
- **Key format:** symbol string.
- **Value structure:** dict with keys `"consensus"` (e.g. STRONG/GOOD/NONE),
  `"consensus_score"` (float), `"vote_count"` (int), `"direction"`
  (str: long/short/neutral), `"last_updated"` (timestamp). Per
  docstring at `layer_manager.py:1391-1395`.
- **Typical size:** Live tail `STRAT_CONSENSUS_WRITE`:
  - 22:11:33 → `full_count=12 ... cache_size_after=18`
  - 22:16:38 → `full_count=11 ... cache_size_after=18`
  - 22:21:37 → `full_count=10 ... cache_size_after=19`
  - 22:26:39 → `full_count=9 ...  cache_size_after=19`
  Steady-state size ≈ 18–19 of 50.
- **TTL / invalidation:** none. Stale entries preserved across cycles
  via merge (`existing.update(new_consensus)`).
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C8 — `_strategy_consensus_summary`  (LayerManager — alias for legacy strategist reads)

- **Defining file:** `src/core/layer_manager.py:106` —
  `self._strategy_consensus_summary: dict = {}`.
- **Owner / writer:** `StrategyWorker.tick` at
  `src/workers/strategy_worker.py:737` —
  `layer_manager._strategy_consensus_summary = self._build_consensus_summary(filtered)`.
- **Consumers (readers):**
  - `Strategist._build_..._prompt` at `src/brain/strategist.py:1034-1035` and `1740-1741`.
- **Key format:** symbol string.
- **Value structure:** Legacy summary dict with `{"buy", "sell", "total_score"}` per the
  defensive migration check at `strategy_worker.py:727-732`.
- **Typical size:** logged at `strategy_worker.py:815` as
  `consensus_summary_size={_summary_size}`. Built from `filtered`
  setups (post PnL restrictions). Same range as filtered_count
  (7–9 in the 22:11–22:26 window).
- **TTL / invalidation:** overwritten each tick, no stale-entry merge.
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C9 — `_strategy_hints`  (LayerManager — written by StrategyWorker)

- **Defining file:** `src/core/layer_manager.py:108` —
  `self._strategy_hints: list = []`.
- **Owner / writer:** `StrategyWorker.tick` at
  `src/workers/strategy_worker.py:803` —
  `layer_manager._strategy_hints = hints`. Gated behind
  `if layer_manager.is_layer_active(3):` (line 776) — so hints are
  only written when Layer 3 (Execution) is on.
- **Consumers (readers):**
  - `Strategist` at `src/brain/strategist.py:1019-1020` and
    `src/brain/strategist.py:1725-1726`:
    `hints = getattr(layer_manager, "_strategy_hints", []) or []`.
- **Key format:** N/A — list, not dict.
- **Value structure (per `strategy_worker.py:786-793`):**
  ```python
  {
    "symbol":   <str>,
    "direction": <"long"|"short">,
    "strategy":  <strategy_name str>,
    "score":     <float, rounded 1 decimal>,
    "consensus": <"STRONG"|"GOOD"|...>,
  }
  ```
- **Typical size:** capped at 20 (`filtered[:20]` at `strategy_worker.py:783`).
  Live tail `STRAT_CYCLE_DONE`: `hints=7` to `hints=9` in the captured
  window — well under the cap.
- **TTL / invalidation:** overwritten each tick.
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C10 — `StructureCache`  (StructureWorker)

- **Defining file:** `src/analysis/structure/structure_cache.py:18` —
  `class StructureCache`. Holds `self._cache: dict[str, tuple[float, StructuralAnalysis]]` at line 27.
- **Owner / writer:** `StructureWorker.tick` at
  `src/workers/structure_worker.py:136` — `self._cache.set(symbol, result)`.
  Fires at sweet spot `0:45` (every 5-min window).
  Internal `set` at `structure_cache.py:46-48` stamps `time.monotonic()`.
- **Consumers (readers):**
  - `StructureCache.get(symbol)` at `structure_cache.py:31-44` (TTL check at line 40).
  - `StructureCache.get_all()` at `structure_cache.py:50-60` (returns fresh entries only).
  - `StructureCache.get_top_setups(n)` at `structure_cache.py:62-77`.
  - `StructureCache.get_ranked_setups()` at `structure_cache.py:99-101` — returns the scanner-ranked subset (a separate field set by `set_ranked_setups`).
  - `StructureWorker.get_setup_score(coin)` at
    `src/workers/structure_worker.py:296-313` (calls `self._cache.get(coin)`).
  - `ScannerWorker._build_package` at
    `src/workers/scanner_worker.py:336-340` —
    `cache = getattr(sw, "_cache", None) ... structure = cache.get(symbol)`
    (note: this uses the cache's TTL-respecting `.get`).
  - `ScannerWorker._qualifies` at `src/workers/scanner_worker.py:534-540` (same pattern as build_package).
  - `Strategist` reads ranked setups at `src/brain/strategist.py:747` and `:1578` — `structure_cache.get_ranked_setups()`.
  - `PerformanceEnforcer` at `src/strategies/performance_enforcer.py:462` — `ranked = structure_cache.get_ranked_setups()`.
  - `Telegram analysis handler` at `src/telegram/handlers/analysis.py:40`.
- **Key format:** symbol string.
- **Value structure:** `tuple[float, StructuralAnalysis]` =
  `(monotonic_set_time, analysis)` where `StructuralAnalysis` is from
  `src/analysis/structure/models/structure_types.py`. Per
  `scanner_worker.py:336-381`, the analysis exposes attributes:
  `current_price`, `structural_placement` (with `direction`, `long_sl_price`,
  `long_tp_price`, `short_sl_price`, `short_tp_price`, `structural_sl`,
  `structural_tp`, `rr_ratio`), `setup_type` (enum with `.value`),
  `setup_score`, `setup_type_confidence`, `confluence_quality`,
  `session_context` (with `current_session`, `session_phase`,
  `manipulation_likely`).
- **Typical size:** live tail —
  `XRAY_TICK_SUMMARY | universe=50 ... cached=50` (22:25:47, 22:20:45, 22:15:45,
  22:10:45). Cache fills to 50 across the 2 batches of 25.
- **TTL / invalidation:**
  - DEFAULT_TTL = 300.0 seconds at `structure_cache.py:15`. `get()`
    rejects entries older than `self._ttl` at line 40.
  - `invalidate(symbol|None)` at line 87-92.
  - `clear()` at line 79-81.
- **Cache-health log:** `structure_cache.py:117-128` — `get_oldest_entry_age_seconds()`.
  Workers.log tail `XRAY_CLASSIFY_SUMMARY` shows confidence p50/p95.
- **Sample 5 entries:** NOT FOUND — in-memory only. Per `XRAY_CLASSIFY_SUMMARY`
  at 22:25:47: `total=25 bearish_fvg_ob=18 none=6 bullish_fvg_ob=1 conf_p50=0.55 conf_p95=0.55`.

---

## C11 — `TACache`  (lazy, shared via service registry)

- **Defining file:** `src/analysis/ta_cache.py:62` — `class TACache`.
  Holds `self._cache: OrderedDict[str, tuple[float, dict]]` at line 91.
- **Owner / writer:** `TACache.analyze(...)` itself populates on miss at
  `ta_cache.py:166-174` (under lock; LRU-evicts at maxsize). Single
  instance constructed at `src/workers/manager.py:189`:
  `ta_cache = TACache(ta_engine_raw, ttl_seconds=120.0)`.
  Registered three times for back-compat at `manager.py:190-192`:
  `services["ta"] = ta_cache`, `services["ta_engine"] = ta_cache`,
  `services["ta_cache"] = ta_cache`.
- **Consumers (readers / lazy populators):**
  - `StrategyWorker` at `src/workers/strategy_worker.py:1451-1454` — `_ta_cache.analyze(...)`.
  - `ProfitSniper` at `src/workers/profit_sniper.py:980-984` — `ta_cache.analyze(...)`.
  - `Strategist` at `src/brain/strategist.py:598`, `625-627`, `1347`, `1451-1453`, `2308-2321` — calls `ta_cache.analyze(...)`.
  - `APEX assembler` at `src/apex/assembler.py:204-208`.
  - `TIAS collector` at `src/tias/collector.py:355-360`.
  - `VolatilityProfiler` at `src/analysis/volatility_profile.py:198-219`.
  - `FreshnessGuard` at `src/core/freshness_guard.py:59-61` (reads `is_fresh`).
- **Key format:** `f"{sym}:{tf}"` (unified across both candles-path and
  symbol-path per the comment at `ta_cache.py:27-48`).
- **Value structure:** `tuple[float, dict]` = `(monotonic_set_time, analysis_result_dict)`.
- **Typical size:** maxsize=200 (line 58 `_DEFAULT_MAXSIZE`). Steady-state
  working set ≈ 32–64 entries per the comment at lines 50-53.
  No live `TA_CACHE_SIZE` log lines in the 22:25–23:01 workers.log
  fragments captured.
- **TTL / invalidation:**
  - TTL = 120 s at construction (`manager.py:189`); module-level
    DEFAULT_TTL = 90 s at `ta_cache.py:25`. Live wiring uses 120 s.
  - LRU eviction past maxsize at `ta_cache.py:171-174`.
  - `invalidate(symbol|None)` at `ta_cache.py:183-200`.
- **Hit rate (live tail evidence):** `STRAT_CYCLE_DONE` at 22:26:39
  reports `cache_lookups=50 cache_valid=50 recomputed=0 hits=50` —
  100% hit rate after StrategyWorker H1 prefetch.
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C12 — `_coin_packages`  (LayerManager — written by ScannerWorker)

- **Defining file:** `src/core/layer_manager.py:113` —
  `self._coin_packages: dict = {}`.
- **Owner / writer:** `ScannerWorker.tick` at
  `src/workers/scanner_worker.py:884-886`:
  ```python
  lm = self.services.get("layer_manager")
  if lm is not None:
      lm._coin_packages = packages
  ```
  Fires at sweet spot `4:00` (every 5-min window).
- **Consumers (readers):**
  - `LayerManager.get_coin_packages()` at
    `src/core/layer_manager.py:1402-1409` (`return getattr(self, "_coin_packages", {}) or {}`).
  - `Strategist._build_trade_prompt` at `src/brain/strategist.py:1371-1372`:
    `if lm is not None and hasattr(lm, "get_coin_packages"): packages = lm.get_coin_packages()`.
  - Configuration switch documented at `src/config/settings.py:402` and
    `src/config/settings.py:1695`.
- **Key format:** symbol string.
- **Value structure:** `CoinPackage` dataclass from
  `src/core/coin_package.py`. Fields populated by
  `ScannerWorker._build_package` (lines 316-499) include:
  `symbol`, `qualified` (bool), `opportunity_score` (float),
  `qualification_reasons` (list[str]), `price_data` (PriceDataBlock:
  `current`, `change_24h_pct`, `volume_24h_usd`, `regime`),
  `xray` (XrayBlock: `setup_type`, `setup_score`,
  `setup_type_confidence`, `structural_levels`, `mtf_confluence`,
  `session`, `session_phase`, `key_features`),
  `strategies` (StrategiesBlock: `fired_count`, `fired_strategies`,
  `ensemble_consensus`, `consensus_score`, `total_score`),
  `signals` (SignalsBlock: `direction`, `confidence`),
  `alt_data` (AltDataBlock: `funding_rate`, `funding_signal`, `fear_greed`),
  `open_position` (dict|None — populated only if forced),
  `blockers_observed` (list[str]).
- **Typical size:** live tail `SCANNER_PACKAGE_BUILD_DONE`:
  - 22:14:00 → `packages=2 total_size_bytes=1876 elapsed_ms=3`
  - 22:19:00 → `packages=2 total_size_bytes=1956 elapsed_ms=2`
  - 22:24:00 → `packages=2 total_size_bytes=1894 elapsed_ms=2`
  Stuck at 2 (forced BTC + ETH) every cycle in the captured window.
- **TTL / invalidation:** rebuilt fresh each scanner tick (assignment,
  not merge). Stale only if scanner doesn't fire.
- **Sample (validation result, snapshot of last cycle in window):**
  - `PACKAGE_VALIDATE | sym=BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']` (22:24:00:017).
  - `PACKAGE_VALIDATE | sym=ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']` (22:24:00:017).
  - The most-recent `PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0` (22:24:00:019).

---

## C13 — `aggregated_sentiment` (DB-backed; SentimentAggregator writes)

This is a TABLE, not an in-memory cache, but listed in the inventory.
Documented for completeness here; full F2 entry below.

- **Owner / writer:** `SentimentAggregator.aggregate_for_symbol(...)` at
  `src/intelligence/sentiment/aggregator.py:270` —
  `await self._sentiment_repo.save_aggregated_sentiment(result)`.
  Called from `SignalWorker.tick` at `src/workers/signal_worker.py:97-98`.
- **Consumers (readers):** `SentimentRepository.get_aggregated_sentiment_*`
  at `src/database/repositories/sentiment_repo.py:157` and `:174`;
  `MCP get_aggregated_sentiment` tool at
  `src/mcp/tools/sentiment_tools.py:72-90`.
- **Schema (verbatim from snapshot):**
  ```sql
  CREATE TABLE aggregated_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        overall_score REAL NOT NULL DEFAULT 0,
        level TEXT NOT NULL DEFAULT 'neutral',
        news_score REAL NOT NULL DEFAULT 0,
        news_count INTEGER NOT NULL DEFAULT 0,
        reddit_score REAL NOT NULL DEFAULT 0,
        reddit_count INTEGER NOT NULL DEFAULT 0,
        fear_greed_value INTEGER NOT NULL DEFAULT 50,
        momentum REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_agg_sentiment_symbol
      ON aggregated_sentiment(symbol, created_at DESC);
  ```
- **Row count (snapshot):** 276,330.
- **Sample 5 latest rows:**
  ```
  ALICEUSDT 0.0 unknown 0 47 2026-04-27 22:26:03
  BCHUSDT   0.0 unknown 0 47 2026-04-27 22:26:03
  LTCUSDT   0.0 unknown 0 47 2026-04-27 22:26:03
  APTUSDT   0.0 unknown 0 47 2026-04-27 22:26:03
  OPUSDT    0.0 unknown 0 47 2026-04-27 22:26:03
  ```
- **Growth rate (snapshot):** 56 rows in 21:00 hour, 116 rows in 22:00 hour
  → ~50/min steady-state per signal_worker fire, 50 coins/min.

---

## Other discovered shared dicts (out-of-scope but inventoried)

Greps for `self\._.*cache` across `src/workers/` returned these
additional caches that exist in single-worker scope only — listed for
completeness:

- `_atr_cache` at `src/workers/profit_sniper.py:146` — `dict[str, tuple[float, float]]`. Owner+reader: `ProfitSniper` only.
- `_cached_regime` + `_regime_cache_time` at `src/workers/profit_sniper.py:151-152`. Owner+reader: `ProfitSniper` only (30-second cache wrapping `RegimeDetector.detect()`).
- `_arrays_cache` at `src/workers/sniper_ring_buffer.py:71`. Owner+reader: ring buffer instance only.
- `_market_data` (mentioned but no shared cross-worker access).

These do NOT cross worker boundaries; they are scoped to their owner.

---

## Snapshot mechanism gap

Hard Rule 4 (live state snapshot at named timestamp) — there is no
runtime introspection endpoint that dumps any of `_score_cache`,
`_strategy_consensus*`, `_strategy_hints`, `_signal_cache`,
`_per_coin_regimes`, `StructureCache._cache`, or `TACache._cache`.

What exists:
- `StructureCache.get_stats()` at `structure_cache.py:107-115` — returns hit/miss counts (logged by structure_worker as `XRAY_CACHE_HEALTH`).
- `TACache.get_stats()` at `ta_cache.py:202-233` — returns hit/miss/eviction counts (logged by strategy_worker as `TA_CACHE_SIZE`).
- `STRAT_L4_HANDOFF` at `strategy_worker.py:819-826` — cache **sizes** but not contents.
- `SCANNER_PACKAGE_BUILD_DONE` at `scanner_worker.py:899-903` — **size and total bytes** of `_coin_packages` but not contents.

For per-entry contents, code change would be required (out of scope).

NOT FOUND — searched: `src/workers/*.py`, `src/core/layer_manager.py`,
`src/analysis/ta_cache.py`, `src/analysis/structure/structure_cache.py`
for any `dump_cache`, `snapshot`, or `to_json` method on the cache
classes.


================================================================================
FILE: F2_db_tables.md
================================================================================

# F2 — DB Tables Used by Layer 1 → Stage 2 Pipeline

Snapshot DB:
`/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
File timestamp: `Apr 27 22:56` (153,624,576 bytes).

DDL is taken verbatim via `sqlite3 .schema <table>`. Row counts via
`SELECT COUNT(*)`. Indexes via
`SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=...`.

Writer/reader file:line citations come from grep over `src/`.

---

## T1 — `klines`

- **DDL:**
  ```sql
  CREATE TABLE klines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        turnover REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(symbol, timeframe, timestamp)
    );
  CREATE INDEX idx_klines_symbol_tf_ts
    ON klines(symbol, timeframe, timestamp DESC);
  ```
- **Writer:** `MarketRepository.save_klines` at
  `src/database/repositories/market_repo.py:65-128` (uses
  `executemany` of `INSERT OR IGNORE INTO klines ...` at line 103;
  chunked, default chunk size at line 31).
  Called from `MarketService.save_klines` at `src/trading/services/market_service.py:222`.
  KlineWorker triggers via `self.market_service.get_klines(...)` at
  `src/workers/kline_worker.py:200-202` (the service path internally
  calls `save_klines`).
- **Readers:**
  - `MarketRepository.get_klines` (called by structure_worker at
    `src/workers/structure_worker.py:351`).
  - `KlineWorker` itself for freshness scan at
    `src/workers/kline_worker.py:330-338` (`SELECT symbol, MAX(timestamp) AS newest_ts FROM klines WHERE timeframe = ? AND symbol IN (...)`).
  - `RegimeDetector.detect_per_coin` (downstream — not pasted; reads via market_repo).
  - Strategist via `_prefetch_*` and `ta_cache` (indirect, through `TAEngine.analyze` → market_repo.get_klines).
- **Row count (snapshot):** **95,331**.
- **Indexes (snapshot):** UNIQUE(symbol, timeframe, timestamp) [implicit] + `idx_klines_symbol_tf_ts(symbol, timeframe, timestamp DESC)`.
- **Growth rate:** Each `KLINE_TICK_SUMMARY` reports `saved=N`. Tail:
  ```
  22:25:51  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  22:30:41  fetched=20000  saved=20000  tf_split={5:10000,60:10000,240:0,D:0}
  22:35:44  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  22:40:46  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  22:45:40  fetched=20000  saved=20000  tf_split={5:10000,60:10000,240:0,D:0}
  22:55:51  fetched=39539  saved=39539  tf_split={5:10000,60:10000,240:9997,D:9542}
  23:00:45  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  ```
  Note: `INSERT OR IGNORE` means `saved=N` reports the fetched count, not unique inserts. Net new rows per cycle is much lower (only the latest 5-minute kline is actually new). True growth ≈ 200 unique rows per 5-min cycle (50 symbols × 4 timeframes × at most a few new bars).

OBSERVED ANOMALY (data freshness): snapshot tail —
`SELECT symbol, timeframe, MAX(timestamp) FROM klines GROUP BY symbol, timeframe LIMIT 10`
returns daily-TF newest timestamps from `2026-04-23` to `2026-04-22` for several
coins (the snapshot was taken at 22:56 on 2026-04-27). The static
snapshot does not contain the latest 5-min cycle ticks. Live DB would
need to be queried for current freshness (live KLINE_FRESHNESS_WARN
events would surface stragglers).

---

## T2 — `ticker_cache`

- **DDL:** see F1 entry C2.
- **Writer:** `MarketRepository.save_ticker` at
  `src/database/repositories/market_repo.py:268`:
  `INSERT OR REPLACE INTO ticker_cache ...`. Called from
  `PriceWorker._handle_ticker_update` at `src/workers/price_worker.py:218`.
- **Readers:**
  - `MarketRepository.get_ticker` at `src/database/repositories/market_repo.py:285-296`.
  - `src/core/transformer.py:667`.
  - `src/intelligence/sentiment/aggregator.py:169` (for momentum).
- **Row count (snapshot):** **200**.
- **Indexes:** PK on `symbol`.
- **Growth rate:** N/A — `INSERT OR REPLACE` keeps row count = subscribed-symbols ever seen. Stable at 200.

---

## T3 — `active_universe`

- **DDL:**
  ```sql
  CREATE TABLE active_universe (
        symbol TEXT PRIMARY KEY,
        opportunity_score REAL NOT NULL,
        volume_24h REAL,
        change_24h_pct REAL,
        funding_rate REAL,
        spread_pct REAL,
        coin_tier INTEGER DEFAULT 3,
        updated_at TEXT DEFAULT (datetime('now'))
    );
  ```
- **Writer:** `ScannerWorker.tick` at
  `src/workers/scanner_worker.py:993-1013`:
  ```
  await self.db.execute("DELETE FROM active_universe")
  ...
  await self.db.executemany(
      "INSERT OR REPLACE INTO active_universe (symbol, ...) VALUES (?, ?, ?, ?, ?, ?, ?)",
      insert_rows,
  )
  ```
- **Readers:** `MarketScanner.get_active_universe()` (pulled in-memory via `ScannerWorker.scanner.set_active_universe(new_symbols)` at `scanner_worker.py:1024`). Direct DB readers: NOT FOUND in `src/` grep beyond the ScannerWorker DELETE/INSERT itself.
- **Row count (snapshot):** **2** — only `BTCUSDT` and `ETHUSDT`, both with `opportunity_score=0.0` and `coin_tier=1`, `updated_at=2026-04-27 22:24:00`.
- **Indexes:** PK on `symbol` only.
- **Growth rate:** N/A — DELETE + INSERT every scanner cycle (every 5 min). Steady-state row count = `len(final)` from the cycle (currently 2).

---

## T4 — `regime_history`

- **DDL:**
  ```sql
  CREATE TABLE regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
        regime TEXT NOT NULL,
        confidence REAL,
        adx REAL,
        atr_percentile REAL,
        choppiness REAL,
        detected_at TEXT DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_regime_time ON regime_history(detected_at DESC);
  ```
- **Writer:** `RegimeWorker.tick` at `src/workers/regime_worker.py:145-157`:
  `INSERT INTO regime_history (symbol, regime, confidence, adx, atr_percentile, choppiness, detected_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))` (one INSERT per tick, global regime only).
- **Readers:** NOT FOUND any direct SELECT FROM regime_history in `src/`. Only `coin_regime_history` is restored at `regime_worker.py:90-102`.
- **Row count (snapshot):** **2,006**.
- **Indexes:** PK + `idx_regime_time`.
- **Growth rate (snapshot, last 24 h via `substr(detected_at,1,13), COUNT(*)`):**
  ```
  2026-04-26 22  → 8
  2026-04-26 23  → 12
  2026-04-27 00  → 12
  2026-04-27 01  → 12
  2026-04-27 02  → 12
  2026-04-27 03  → 12
  2026-04-27 04  → 12
  2026-04-27 05  → 12
  2026-04-27 06  → 11
  [ no rows for 2026-04-27 07 through 20 ]
  2026-04-27 21  → 1
  2026-04-27 22  → 6
  ```
  OBSERVED ANOMALY: 15-hour gap from 06:00 to 21:00 on 2026-04-27 in the snapshot. Matches the 22:27 monitor observation cited in the prompt.

---

## T5 — `coin_regime_history`

- **DDL:**
  ```sql
  CREATE TABLE coin_regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        regime TEXT NOT NULL,
        confidence REAL NOT NULL,
        adx REAL,
        choppiness REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );
  CREATE INDEX idx_coin_regime_symbol ON coin_regime_history(symbol, timestamp DESC);
  ```
- **Writer:** `RegimeWorker.tick` at `src/workers/regime_worker.py:251-258`:
  `INSERT INTO coin_regime_history (symbol, regime, confidence, adx, choppiness) VALUES (?, ?, ?, ?, ?)`. One INSERT per coin per tick.
- **Readers:** `RegimeWorker.tick` first-tick restore at `src/workers/regime_worker.py:90-102` (filter by `WHERE timestamp > datetime('now', '-30 minutes') AND symbol IN (...)`).
- **Row count (snapshot):** **20,951**.
- **Indexes:** PK + `idx_coin_regime_symbol`.
- **Cleanup:** `regime_worker.py:285-287` — `DELETE FROM coin_regime_history WHERE timestamp < datetime('now', '-24 hours')` once per 100 ticks.
- **Growth rate:** ~49 rows per 5-min tick (per_coin_size=49 from REGIME_TICK_SUMMARY) → ~588/h sustained.

---

## T6 — `aggregated_sentiment`

- **DDL:** see F1 entry C13.
- **Writer:** `SentimentRepository.save_aggregated_sentiment` at `src/database/repositories/sentiment_repo.py:128`:
  `INSERT INTO aggregated_sentiment (symbol, overall_score, level, news_score, news_count, reddit_score, reddit_count, fear_greed_value, momentum, created_at) VALUES (...)`. Called from `SentimentAggregator.aggregate_for_symbol` at `src/intelligence/sentiment/aggregator.py:270`.
- **Readers:** `SentimentRepository.get_*` at `src/database/repositories/sentiment_repo.py:157` (`SELECT * FROM aggregated_sentiment WHERE symbol = ? ORDER BY created_at DESC LIMIT ?`) and `:174` (`WHERE symbol = ? AND created_at > ?`). MCP tool `get_aggregated_sentiment` at `src/mcp/tools/sentiment_tools.py:73`.
- **Row count (snapshot):** **276,330**.
- **Indexes:** PK + `idx_agg_sentiment_symbol`.
- **Cleanup:** `src/database/cleanup.py:26` — 30-day retention; `src/workers/cleanup_worker.py:48` — `("aggregated_sentiment", 30, "created_at")`.
- **Growth rate (snapshot, last hours):**
  ```
  2026-04-27 21  → 56
  2026-04-27 22  → 116
  ```
  Steady ~50/min × N where N = signal_worker fires/min → matches sentiment writes per signal_worker cycle (50 coins × 1 fire/5-min = ~600/h).

---

## T7 — `funding_rates`

- **DDL:**
  ```sql
  CREATE TABLE funding_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        funding_rate REAL NOT NULL,
        next_funding_time TEXT NOT NULL,
        predicted_rate REAL NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_funding_symbol ON funding_rates(symbol, fetched_at DESC);
  ```
- **Writer:** `AltDataRepo.save_funding_rate` at `src/database/repositories/altdata_repo.py:87` —
  `INSERT INTO funding_rates (symbol, funding_rate, next_funding_time, predicted_rate, fetched_at) ...`. Called from `FundingRateTracker.fetch_current_rates` (the tracker writes to DB; in-memory `_funding_cache` is in AltDataWorker — see F1).
- **Readers:** `AltDataRepo.get_funding_rates` at `src/database/repositories/altdata_repo.py:99` (paginated query). Used in dashboards and TIAS/APEX.
- **Row count (snapshot):** **87,145**.
- **Indexes:** PK + `idx_funding_symbol`.
- **Growth rate:** Each AltDataWorker fire writes 50 rows; fires every 5 min → ~600/h sustained.

---

## T8 — `open_interest`

- **DDL:**
  ```sql
  CREATE TABLE open_interest (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        open_interest_value REAL NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_oi_symbol ON open_interest(symbol, timestamp DESC);
  ```
- **Writer:** `AltDataRepo.save_open_interest` at `src/database/repositories/altdata_repo.py:158`:
  `INSERT INTO open_interest (symbol, open_interest_value) VALUES (?, ?)`. Called from `OpenInterestTracker.fetch_current(symbols)` via AltDataWorker.
- **Readers:** dashboards / strategist (grep showed `fetch_one:SELECT * FROM open_interest WHERE symbol = ? ORD` in DB_LOCK_WAIT lines).
- **Row count (snapshot):** **79,565**.
- **Indexes:** PK + `idx_oi_symbol`.
- **Cadence:** OI fires every `open_interest_minutes=5` from `config.toml:146` → 50 rows/cycle → ~600/h.

---

## T9 — `fear_greed_index`

- **DDL:**
  ```sql
  CREATE TABLE fear_greed_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value INTEGER NOT NULL,
        classification TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_fear_greed_ts ON fear_greed_index(timestamp DESC);
  ```
- **Writer:** `AltDataRepo.save_fear_greed` at `src/database/repositories/altdata_repo.py:33`:
  `INSERT INTO fear_greed_index (value, classification, timestamp) VALUES (?, ?, ?)`. Called from `FearGreedClient.fetch_current` via AltDataWorker.
- **Readers:** Single-row latest fetch — appears in DB_LOCK_WAIT logs as `fetch_one:SELECT * FROM fear_greed_index ORDER BY timestam`.
- **Row count (snapshot):** **21,373**.
- **Indexes:** PK + `idx_fear_greed_ts`.
- **Cadence:** F&G fires every `fear_greed_minutes=60` from `config.toml:148`.

---

## T10 — `signals`

- **DDL:**
  ```sql
  CREATE TABLE signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        components TEXT NOT NULL DEFAULT '{}',
        reasoning TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_signals_symbol ON signals(symbol, created_at DESC);
  ```
- **Writer:** `AltDataRepo` (or signal repo) at `src/database/repositories/altdata_repo.py:204`:
  `INSERT INTO signals (symbol, signal_type, confidence, source, components, reasoning, created_at) ...`. Called by `SignalGenerator` / `intelligence_aggregator`.
- **Readers:** dashboards, strategist context. NOT FOUND any direct read in the Layer 1→Stage 2 critical path beyond the in-memory `_signal_cache` (F1 C4).
- **Row count (snapshot):** **155,693**.
- **Indexes:** PK + `idx_signals_symbol`.
- **Sample latest (snapshot):** every coin `signal_type=neutral confidence≈0.20–0.24 source=intelligence_aggregator created_at=2026-04-27T22:26:03`.
- **Growth rate:** 50 signals per signal_worker tick × 12 ticks/h = ~600/h.

---

## T11 — `news_articles`

- **DDL:**
  ```sql
  CREATE TABLE news_articles (
        id TEXT PRIMARY KEY,
        headline TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        sentiment_score REAL NOT NULL DEFAULT 0,
        symbols TEXT NOT NULL DEFAULT '[]',
        category TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT (datetime('now')),
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_news_published ON news_articles(published_at DESC);
  CREATE INDEX idx_news_symbols ON news_articles(symbols);
  ```
- **Writer:** NewsWorker via news repository (NOT FOUND a single `INSERT INTO news_articles` line in our grep — `src/workers/news_worker.py` is only 75 lines and delegates writes; check news_repo for actual SQL).
- **Readers:** Sentiment aggregator + DB_LOCK_WAIT log shows `fetch_all:SELECT * FROM news_articles WHERE symbols LIKE ?`.
- **Row count (snapshot):** **1,226**.
- **Indexes:** PK + `idx_news_published` + `idx_news_symbols`.
- **Sample latest:**
  ```
  7860475 "Bitcoin whale holdings hit five-month high: Is BTC headed to $80K next?"  Cointelegraph  +0.30  ["BTCUSDT"]   2026-04-27T22:53:42.163245+00:00
  7860474 "Canada advances bill to ban crypto political donations"                    Cointelegraph  -0.15  []           2026-04-27T22:53:42.160136+00:00
  7860372 "Industry leaders are pouring hundreds of millions into a rescue plan for Aave users after massive crypto hack"  CoinDesk 0.0 [] 2026-04-27T22:28:12.110766+00:00
  ```

---

## T12 — `strategy_performance`

- **DDL:**
  ```sql
  CREATE TABLE strategy_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT 'all',
        total_trades INTEGER NOT NULL DEFAULT 0,
        winning_trades INTEGER NOT NULL DEFAULT 0,
        losing_trades INTEGER NOT NULL DEFAULT 0,
        win_rate REAL NOT NULL DEFAULT 0,
        avg_pnl REAL NOT NULL DEFAULT 0,
        avg_pnl_pct REAL NOT NULL DEFAULT 0,
        max_drawdown REAL NOT NULL DEFAULT 0,
        sharpe_ratio REAL,
        profit_factor REAL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(strategy, symbol, timeframe)
    );
  CREATE INDEX idx_strategy_perf_name ON strategy_performance(strategy);
  ```
- **Writer:** Strategy / TIAS feedback path (NOT GREP'D for full file:line in this module — see D2 collection). The `apply_restrictions` filter at `src/strategies/pnl_manager.py` would be the relevant reader.
- **Readers:** `apply_restrictions` (D1), TIAS feedback. Detail belongs in D2.
- **Row count (snapshot):** **124**.
- **Sample (top 5 by total_trades):**
  ```
  claude_trader  ETHUSDT   total=72  win=31  win_rate=0.4306
  claude_trader  BTCUSDT   total=65  win=28  win_rate=0.4308
  claude_trader  HYPEUSDT  total=46  win=21  win_rate=0.4565
  claude_trader  SOLUSDT   total=38  win=14  win_rate=0.3684
  claude_trader  SIRENUSDT total=37  win=9   win_rate=0.2432
  ```

---

## T13 — `cycle_metrics` (CycleTracker hourly aggregate)

- **DDL:** see top of file. ALTER-added columns include `signal_buy_pct`, `signal_sell_pct`, `signal_neutral_pct`, `xray_setup_type_count`, `regime_distribution_json`, `l1_strategies_fired_avg`, `l2_score_p50`, `l3_consensus_dist_json`, `package_completeness_avg`, `freshness_klines_to_xray_p50`.
- **Writer:** `CycleTracker` — wired via `services["cycle_tracker"]`. Writers: NOT GREP'D for `INSERT INTO cycle_metrics` in this module pass.
- **Readers:** Operator dashboards. Out of pipeline-critical scope.
- **Row count (snapshot):** **0**.

---

## T14 — `ensemble_votes`

- **DDL:** see top of file. Indexes: NONE on this table beyond PK.
- **Writers / Readers:** NOT GREP'D for this module. Out of pipeline-critical scope (per E1/D1 modules).
- **Row count (snapshot):** **0**.

---

## T15 — `brain_decisions` and `claude_decisions`

- **claude_decisions DDL:**
  ```sql
  CREATE TABLE claude_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        decision_type TEXT NOT NULL,
        new_trades_count INTEGER DEFAULT 0,
        position_actions_count INTEGER DEFAULT 0,
        market_view TEXT,
        risk_level TEXT,
        response_time_ms INTEGER,
        prompt_length INTEGER,
        full_response TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_claude_decisions_ts ON claude_decisions(ts_epoch);
  ```
- **brain_decisions:** present (see DDL) but row count = 0 — appears unused.
- **Writer (claude_decisions):** `LayerManager._record_decision_to_data_lake` at `src/core/layer_manager.py:862-876` → `data_lake.write_claude_decision(...)` (asyncio.create_task). Actual SQL: NOT GREP'D in this module.
- **Row count (snapshot):** `claude_decisions` = **1,016**; `brain_decisions` = **0**.
- **Sample latest 3 (claude_decisions):**
  ```
  call_a  "Late NY dead zone, both BTC and ETH sold off hard today (-2.3% and -3.5%). Low volatility..."  resp_ms=113851 prompt_length=0 created_at=2026-04-27 22:25:16
  call_a  "Only 2 coins available (BTCUSDT, ETHUSDT). ETHUSDT already has a position being managed by watchdog..."  resp_ms=79318 prompt_length=0 created_at=2026-04-27 22:18:22
  call_b  ""  resp_ms=97495 prompt_length=0 created_at=2026-04-27 22:14:33
  ```

---

## SPECIAL — D-3 LOCK CONTENTION FORENSICS

DB lock instrumentation lives in `src/database/connection.py`:
- `_locked()` async context manager at line 168-231: acquires `self._lock` (asyncio.Lock at line 93), records wait time into `_wait_samples` (deque maxlen 1000), and emits `DB_LOCK_WAIT` warning when `wait_ms >= self._lock_wait_warn_ms` (default `DB_LOCK_WAIT_WARN_MS = 1000.0` per line 38).
- `log_lock_histogram()` at line 233-269 emits `DB_LOCK_HIST` periodic summary.

Search:
- `data/logs/workers.log` (current): **0** `DB_LOCK_WAIT` events in the captured window.
- `data/logs/general.log`: many events captured. The events use a deeper format that includes both holder (previous lock holder, may say `holder=none` if the prior holder cleared `_last_holder` or had no op tag) and `caller=<op>`.

### 5 instances of `DB_LOCK_WAIT > 1000ms` (verbatim from general.log)

1. `2026-04-26 16:35:28.823 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1108 holder=none caller=fetch_all: ...`
2. `2026-04-26 16:35:28.828 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1112 holder=none caller=fetch_all: ...`
3. `2026-04-26 16:35:28.847 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1129 holder=none caller=fetch_all:SELECT * FROM price_alerts WHERE triggered = 0 | no_ctx`
4. `2026-04-26 16:35:28.849 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1132 holder=none caller=fetch_all:SELECT * FROM scheduled_reports WHERE enabled =  | no_ctx`
5. `2026-04-26 17:35:32.200 | WARNING  | src.database.connection:_locked:149 | DB_LOCK_WAIT | wait_ms=1017 holder=fetch_one:SELECT * FROM fear_greed_index ORDER BY timestam caller=fetch_all: ...`

For each: holder named is the OP TAG of the previous lock holder
(per `_locked` line 198: `prev_holder = self._last_holder`). Where
`holder=none`, the previous holder cleared `_last_holder` (or was the
very first acquire). Where `holder=fetch_one:SELECT * FROM fear_greed_index`,
that operation had been the immediately-previous holder.

The **upstream caller (worker)** is captured in
`_extract_external_caller_frame()` at line 42-68; it is recorded only
on the warn path and embedded in the warning as
`frame=<filename>:<lineno>`. The samples shown above use the older
format which does NOT include `frame=...` — that field came in with
the Phase 1 D-3 fix (line 217-222 in current code) but the historic
log lines pre-date that emission. Newer samples in the same
`general.log` use the post-fix form (`_locked:149` instead of `:136`).

### Longest single transaction (likely kline_worker bulk insert)

The longest waits captured were >130 seconds:

1. `2026-04-26 21:14:54.791 ... DB_LOCK_WAIT | wait_ms=137403 holder=execute:` (137.4 s).
2. `2026-04-26 21:14:53.842 ... DB_LOCK_WAIT | wait_ms=136584 holder=executemany:` (136.6 s).
3. `2026-04-26 21:14:52.555 ... DB_LOCK_WAIT | wait_ms=135569 holder=execute:INSERT INTO account_snapshots` (135.6 s).
4. `2026-04-26 21:14:51.281 ... DB_LOCK_WAIT | wait_ms=134424 holder=execute:INSERT INTO coin_regime_history` (134.4 s).
5. `2026-04-26 21:14:45.105 ... DB_LOCK_WAIT | wait_ms=135909 holder=fetch_all:` (135.9 s).

Holder identity for the longest wait (137,403 ms): `holder=execute:`
(truncated — full SQL not in line; only the first 48 chars logged per
`_locked` line 300 / 337 op tag). The bulk INSERT pattern matches the
kline_worker `executemany` of `INSERT OR IGNORE INTO klines ...` at
`src/database/repositories/market_repo.py:127`. The transaction size
in rows: per `KlineWorker.tick` evidence (workers.log tail) =
**29,997 rows per cycle** when M5+H1+H4 fire together (`tf_split={5:10000,60:10000,240:9997,D:0}`), or **39,539 rows** when D1 also fires (`{5:10000,60:10000,240:9997,D:9542}` at 22:55:51). The `executemany` is chunked at `market_repo.py:127`:
`await self._db.executemany(sql, params[i : i + chunk_size])` with
`chunk_size` per line 31 docstring (default mirrors the historical single-call). Concrete chunk size value: NOT FOUND verbatim in the read excerpt.

Lock hold time: ≈ wall-clock time between the `executemany`'s acquire
and release. Not directly logged per-transaction. Indirect estimate:
the longest wait observed by a waiter (137.4 s) implies the holder
held the lock for >137 s. Live `KLINE_TICK_SUMMARY` shows `el=10433ms`
to `el=21363ms` for the entire tick (which includes both fetch and
DB write); so the 137-second holds are **outliers** likely associated
with WAL-checkpoint stalls or the kline_worker's combined fetch +
multi-table-lookup tick body, not the single executemany alone. The
post-fix instrumentation (`frame=` field) is needed to attribute
specific holds; the historic samples cannot pin the exact line.

OBSERVED ANOMALY: `DB_LOCK_HIST | n=536 p50=0ms p95=0ms max=1135ms`
(2026-04-26 16:35:28.856) shows distribution heavily skewed —
99% of acquires are <1 ms but the tail extends past 1 second. The
post-Layer-1 fix added a `top_callers=[...]` list to this emit
(`src/database/connection.py:262-266`) but the historic line shown
predates that addition.


================================================================================
FILE: F3_sweet_spot_scheduling.md
================================================================================

# F3 — Sweet-Spot Scheduling Wiring

## F3.1 — Scheduler implementation

**File:** `src/workers/sweet_spot_scheduler.py` (255 lines).
**Last modified:** confirmed by `wc -l` output `255 /home/.../sweet_spot_scheduler.py`.

### Algorithm — verbatim from source

`parse_sweet_spot(value)` — `src/workers/sweet_spot_scheduler.py:26-61`:

```python
def parse_sweet_spot(value: str) -> tuple[int, int]:
    if not isinstance(value, str):
        raise ValueError(...)
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"sweet spot must be in MM:SS format, got {value!r}")
    try:
        m = int(parts[0])
        s = int(parts[1])
    except ValueError as e:
        raise ValueError(...)
    if m < 0 or m > 59:
        raise ValueError(f"sweet spot minute must be 0-59, got {m}")
    if s < 0 or s > 59:
        raise ValueError(f"sweet spot second must be 0-59, got {s}")
    return (m, s)
```

`seconds_until_next_sweet_spot(spot, *, window_minutes=5, now=None, skip_threshold_s=0.1)`
— `src/workers/sweet_spot_scheduler.py:64-101`:

```python
def seconds_until_next_sweet_spot(spot, *, window_minutes=5, now=None,
                                  skip_threshold_s=0.1):
    if now is None:
        now = time.time()
    window_s = window_minutes * 60
    spot_s = spot[0] * 60 + spot[1]
    pos_in_window = now % window_s
    delta = spot_s - pos_in_window
    if delta > skip_threshold_s:
        return delta
    return delta + window_s
```

`is_at_sweet_spot(spot, *, window_minutes=5, now=None, tolerance_s=1.0)`
— `src/workers/sweet_spot_scheduler.py:104-121`. Used by tests only.

`SweetSpotScheduler.__init__` — `sweet_spot_scheduler.py:162-180`:

```python
def __init__(self, worker_name, offset, window_minutes=5):
    self.worker_name = worker_name
    self.offset_str = offset
    self.offset = parse_sweet_spot(offset)
    self.window_minutes = int(window_minutes)
    if self.window_minutes < 1:
        raise ValueError(...)
    self.stats = SweetSpotStats()
    log.info(
        f"SWEET_SPOT_REGISTERED | worker={self.worker_name} "
        f"offset={self.offset_str} window_min={self.window_minutes} | {ctx()}"
    )
```

`SweetSpotScheduler.wait_for_sweet_spot` — `sweet_spot_scheduler.py:188-242`:

```python
async def wait_for_sweet_spot(self) -> float:
    delay_s = self.seconds_until_next()
    await asyncio.sleep(delay_s)

    now = time.time()
    window_s = self.window_minutes * 60
    spot_s = self.offset[0] * 60 + self.offset[1]
    pos_in_window = now % window_s
    drift_s = pos_in_window - spot_s
    if abs(drift_s - window_s) < abs(drift_s):
        drift_s -= window_s
    elif abs(drift_s + window_s) < abs(drift_s):
        drift_s += window_s
    drift_ms = drift_s * 1000.0

    self.stats.fires += 1
    self.stats.cumulative_drift_ms += abs(drift_ms)
    if abs(drift_ms) > self.stats.max_drift_ms:
        self.stats.max_drift_ms = abs(drift_ms)
    self.stats.last_drift_ms = drift_ms

    log.info(
        f"SWEET_SPOT_FIRED | worker={self.worker_name} "
        f"offset={self.offset_str} drift_ms={drift_ms:.0f} "
        f"fires={self.stats.fires} | {ctx()}"
    )
    try:
        from src.core.worker_liveness import get_default_tracker
        get_default_tracker().record_sweet_spot(self.worker_name)
    except Exception:
        pass
    return drift_ms
```

The scheduler is owned by every `SweetSpotWorker` (constructed in
`SweetSpotWorker.__init__` at `src/workers/base_worker.py:516-520`).
The run loop calls `wait_for_sweet_spot()` BEFORE every tick at
`base_worker.py:555` so the worker waits FIRST then ticks.

---

## F3.2 — Per-worker sweet-spot schedule

Configured in `config.toml:133-148` (verbatim):

```toml
[workers.sweet_spots]
window_minutes = 5
kline_worker = "0:30"
structure_worker = "0:45"
signal_worker = "1:00"
regime_worker = "1:15"
strategy_worker = "1:30"
scanner_worker = "4:00"

[workers.sweet_spots.altdata]
# Funding rates: MM:SS within window, between regime (1:15) and scanner (4:00).
funding_rates = "1:45"
# Open interest: every N minutes, independent of window.
open_interest_minutes = 5
# Fear & Greed: every M minutes, hourly default.
fear_greed_minutes = 60
```

Defaults match (in `src/config/settings.py:292-298` and `:251-253`).

### Per-worker tick interval and config source

| Worker | Tier | Sweet-spot offset | Tick body | Config source (file:line) | Cadence |
|--------|------|-------------------|-----------|---------------------------|---------|
| `price_worker` | LAYER1A | N/A (continuous WS) | health/reconnect heartbeat | `settings.workers.market_data_interval` (`config.toml:108` = 45s) | Every 45 s (BaseWorker fixed interval) |
| `kline_worker` | LAYER1A | `0:30` | M5/H1/H4/D1 fetch + DB write | `settings.workers.sweet_spots.kline_worker` (`config.toml:135`) | Once per 5-min window (sweet-spot) |
| `structure_worker` | LAYER1B | `0:45` | X-RAY analysis (batched 25/cycle) | `settings.workers.sweet_spots.structure_worker` (`config.toml:136`) | Once per 5-min window |
| `signal_worker` | LAYER1B | `1:00` | sentiment aggregation + signal generation | `settings.workers.sweet_spots.signal_worker` (`config.toml:137`) | Once per 5-min window |
| `regime_worker` | LAYER1B | `1:15` | global + per-coin regime detection | `settings.workers.sweet_spots.regime_worker` (`config.toml:138`) | Once per 5-min window |
| `strategy_worker` | LAYER1C | `1:30` | Layer 1-4 strategy pipeline | `settings.workers.sweet_spots.strategy_worker` (`config.toml:139`) | Once per 5-min window |
| `altdata_worker` | LAYER1A | `1:45` (funding) | funding/OI/F&G/onchain (per-source deadlines) | `settings.workers.sweet_spots.altdata.funding_rates` (`config.toml:144`) | Funding every wake (5 min); OI every 5 min; F&G every 60 min |
| `scanner_worker` | LAYER1D | `4:00` | qualify/rank/build packages/write active_universe | `settings.workers.sweet_spots.scanner_worker` (`config.toml:140`) | Once per 5-min window |
| `news_worker` | LAYER1A | N/A (fixed) | Finnhub poll | `settings.workers.news_interval` (`config.toml:110` = 300s) | Every 300 s |

Worker tier mapping (single source of truth) — from each worker file:
- `kline_worker.py:73`: `worker_tier = WorkerTier.LAYER1A`
- `price_worker.py:41`: `worker_tier = WorkerTier.LAYER1A`
- `altdata_worker.py:52`: `worker_tier = WorkerTier.LAYER1A`
- `structure_worker.py:46-48`: `worker_tier = WorkerTier.LAYER1B; cycle_gated = True`
- `signal_worker.py:42-44`: `worker_tier = WorkerTier.LAYER1B; cycle_gated = True`
- `regime_worker.py:38-40`: `worker_tier = WorkerTier.LAYER1B; cycle_gated = True`
- `strategy_worker.py:52-54`: `worker_tier = WorkerTier.LAYER1C; cycle_gated = True`
- `scanner_worker.py:57-59`: `worker_tier = WorkerTier.LAYER1D; cycle_gated = True`

### Chain ordering enforcement

`SweetSpotsSettings.__post_init__` at `src/config/settings.py:301-342`
validates strict chain order: every downstream worker's offset (in seconds)
must be `> prev_seconds` else `ConfigError` is raised at startup. The chain
checked: kline → structure → signal → regime → strategy → scanner.
Note: the altdata `1:45` and the strategy `1:30` are not in the strict
chain (altdata is independent), so altdata's `1:45` between regime
(1:15) and scanner (4:00) is intentional.

---

## F3.3 — Cycle gating

### `is_cycle_active()` definition

`src/core/layer_manager.py:1357-1370`:

```python
def is_cycle_active(self) -> bool:
    """Layer 1 restructure Phase 4 — should Layer 1B/1C/1D fire now?

    Today (pre-Phase-8 renumbering) a cycle is active iff both BRAIN
    (toggle 2) and EXECUTION (toggle 3) are intended on. Layer 1A
    always runs regardless. Phase 8 will rewire to toggle 2 alone
    (= ANALYSIS in the new scheme).
    """
    return self._layer_active.get(2, False) and self._layer_active.get(3, False)
```

Therefore:
- LAYER1B / LAYER1C / LAYER1D workers (cycle_gated=True) tick ONLY
  when both Layer 2 (BRAIN) and Layer 3 (EXECUTION) are active.
- LAYER1A workers (cycle_gated=False on the class) ALWAYS run while
  Layer 1 is on.

### How the layer toggle is applied

`SweetSpotWorker.start` at `src/workers/base_worker.py:550-599`:

```python
while self.running:
    try:
        self._last_drift_ms = await self._scheduler.wait_for_sweet_spot()
    ...
    if not self.running:
        break

    if (
        self.cycle_gated and self._layer_manager
        and hasattr(self._layer_manager, "is_cycle_active")
        and not self._layer_manager.is_cycle_active()
    ):
        if self.layer_tier_tag:
            _now_skip = time.monotonic()
            if _now_skip - self._last_skip_info_ts >= _SKIP_INFO_RATE_LIMIT_S:
                self._last_skip_info_ts = _now_skip
                log.info(
                    f"{self.layer_tier_tag}_TICK_SKIP | "
                    f"sub={self.name} reason=cycle_inactive "
                    f"drift_ms={self._last_drift_ms:.0f} "
                    f"rate_limited=true | {ctx()}"
                )
            else:
                log.debug(...)
        continue
```

Same pattern in `BaseWorker.start` at `base_worker.py:231-258` for the
fixed-interval workers. The skip happens AFTER `wait_for_sweet_spot`
returns (so wall-clock anchoring is preserved — no schedule drift)
and BEFORE the tick body. Skipping emits an INFO event rate-limited
to once per 600 s per worker (`_SKIP_INFO_RATE_LIMIT_S` at line 43).

LayerManager handle wiring: `BaseWorker._layer_manager` at line 171 is
late-bound by WorkerManager after instantiation. None when not wired
— gated workers fall through (don't skip) so a wiring oversight
doesn't silently halt all analysis.

### Live verification: with all layers ON, all workers tick

From `data/logs/workers.log` 22:25–23:01 window — every cycle_gated
worker fired its sweet spot AND ticked successfully:

```
22:25:30.001  SWEET_SPOT_FIRED | worker=kline_worker     offset=0:30 fires=7
22:25:51.373  KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 ... el=21363ms
22:25:47.329  XRAY_TICK_SUMMARY | universe=50 batch=1/2 symbols=25 analyzed=25 ... el=2303ms
22:26:00.021  SWEET_SPOT_FIRED | worker=signal_worker    offset=1:00 fires=7
22:26:03.374  SIG_TICK_SUMMARY | universe=50 signals=50 mean_conf=0.21 el=3352ms
22:26:15.017  SWEET_SPOT_FIRED | worker=regime_worker    offset=1:15 fires=7
22:26:24.807  REGIME_TICK_SUMMARY | universe=50 global=ranging per_coin_size=49 el=9789ms
22:26:30.017  SWEET_SPOT_FIRED | worker=strategy_worker  offset=1:30 fires=7
22:26:39.110  STRAT_CYCLE_DONE | coins=50 signals=10 scored=10 hints=7 ... el=9092ms
22:26:45.018  SWEET_SPOT_FIRED | worker=altdata_worker   offset=1:45 fires=7
22:26:55.156  ALTDATA_TICK_DONE | total_ms=10137 ran=[funding,oi,onchain]
22:29:00.028  SWEET_SPOT_FIRED | worker=scanner_worker   offset=4:00 fires=8
```

The 22:30:00 cycle:
```
22:30:30.000  SWEET_SPOT_FIRED | worker=kline_worker     offset=0:30 fires=8
22:30:45.001  SWEET_SPOT_FIRED | worker=structure_worker offset=0:45 fires=8
22:31:00.001  SWEET_SPOT_FIRED | worker=signal_worker    offset=1:00 fires=8
22:31:15.000  SWEET_SPOT_FIRED | worker=regime_worker    offset=1:15 fires=8
22:31:30.000  SWEET_SPOT_FIRED | worker=strategy_worker  offset=1:30 fires=8
22:31:45.001  SWEET_SPOT_FIRED | worker=altdata_worker   offset=1:45 fires=8
22:34:00.002  SWEET_SPOT_FIRED | worker=scanner_worker   offset=4:00 fires=11
```

Drift values per `SWEET_SPOT_FIRED` line are typically `0–2 ms`, occasionally
`drift_ms=22` / `28` when an upstream tick ran long; never seen >50 ms
in the captured window. The chain is healthy.

OBSERVED ANOMALY: at `2026-04-27 22:53:46.050` to `22:53:40.963`, the
fires counter resets — `worker=scanner_worker offset=4:00 drift_ms=1 fires=1`
at 22:54:00, `worker=kline_worker offset=0:30 drift_ms=0 fires=1` at
22:55:30. The workers reset their `fires` counter to 1 — implies a
process restart at ~22:53. Workers.log between 22:46 and 22:53 has
no `SWEET_SPOT_FIRED` events for any sweet-spot worker (workers.log
shows only price_worker LAYER1A_TICK_DONE entries during that gap),
matching a worker-process restart.

### Layer toggle state — current (snapshot from logs)

The captured workers.log fragments do not include `LAYER_TOGGLE` /
`LAYER_STATE_SYNC` events in the 22:25–23:01 window. The persistent
state file `data/layer_state.json` is referenced at
`src/core/layer_manager.py:28` but its current contents were not
captured in this collection.

NOT FOUND — searched: workers.log captured fragment for
`LAYER_TOGGLE | layer=`, `LAYER_STATE_SYNC | match=`, or
`LAYER_STATE_PERSIST_OK` lines. The fact that all cycle_gated
workers (`structure_worker`, `signal_worker`, `regime_worker`,
`strategy_worker`, `scanner_worker`) tick and produce summary lines
implies `is_cycle_active() == True` at all observed cycles
(if it were False, only the `LAYER1{B,C,D}_TICK_SKIP` line would
appear, not the *_TICK_SUMMARY lines).


================================================================================
FILE: F4_data_flow_trace.md
================================================================================

# F4 — End-to-End Data Flow Trace

Selected cycle: **window starting 2026-04-27 22:25:00 UTC** (M5 boundary).
This is the most-recent fully-captured cycle in `data/logs/workers.log`
where every Layer-1 sweet-spot worker fired and ScannerWorker
produced packages (the next ScannerWorker fire was at 22:29:00 with
`fires=8`, building from this cycle's data).

Prior cycle ScannerWorker emit at `22:24:00.020` is the last set of
packages Brain CALL_A could read. The Brain CALL_A that started at
`22:23:22.866` actually read the c-2026-04-27-22:20 packages.

This trace captures what happened **between 22:25:00 and 22:29:30**.

Source files:
- `data/logs/workers.log` (lines from grep — verbatim).
- `data/logs/brain.log` (Brain CALL_A reads — verbatim).

---

## F4.1 — Cycle start (M5 boundary 22:25:00)

**Wall-clock anchor:** 2026-04-27 22:25:00 UTC.

What fires first on the boundary itself: nothing — the kline_worker
is the first chained sweet-spot at offset `0:30`, not at `0:00`.

The very first events in this window are not sweet-spot worker fires
but cadence/heartbeat events:
- `22:25:00.378  ENFORCER_GRACE | el=0 remaining=0min ...`
- `22:25:00.379  ENFORCER_BEAT  | total=0T W=0 L=0 wr=0.0% strk=-1 hb=OK`
- `22:25:00.381  SYSTEM_HEALTH  | loop_lag=0.0ms tasks=33 mem=330MB cpu=5% pid=396`
- `22:25:00.423  WD_TICK        | mode=passive n=0 syms=[none]`

PriceWorker (continuous WS) heartbeat at `22:25:14.588`:
```
PRICE_WS_HEALTH | status=connected msgs_per_min=5013 msgs_in_window=3760
window_s=45.0 subscribed=50 quotes_cached=50
```

Worker liveness heartbeat at `22:25:29.663`:
```
WORKER_LIVENESS_HEARTBEAT | total=19 healthy=19 never_ticked=0 overdue=0
idle_cycle_gate=0 cycle_active=True
```
(Confirms `is_cycle_active() == True` for this cycle.)

---

## F4.2 — Layer 1A executions

### KlineWorker — sweet-spot 0:30

- **22:25:30.001** `SWEET_SPOT_FIRED | worker=kline_worker offset=0:30 drift_ms=1 fires=7`
- **22:25:51.373** `KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 skipped=50 tf_split={5:10000,60:10000,240:9997,D:0} errors=0 el=21363ms drift_ms=1`
- **22:25:51.374** `LAYER1A_TICK_DONE | sub=kline_worker elapsed_ms=21373 drift_ms=1`

What it read: 50 symbols × {M5, H1, H4, D1} per `TIMEFRAME_SCHEDULE` at
`src/workers/kline_worker.py:32-37`. M5 + H1 + H4 fired this cycle (D1
skipped: `tf_split={...,D:0}`).

What it wrote: 29,997 kline rows via `executemany` into the `klines`
table (`src/database/repositories/market_repo.py:103-128`).
`INSERT OR IGNORE` so most are duplicates; net new rows are only the
latest M5/H1/H4 bars per symbol. Tick elapsed: **21,363 ms**.

Data freshness at end of Layer 1A: latest M5 timestamp written ≈ 22:25
boundary. The freshness scan inside the same tick (kline_worker.py:330-338)
would have logged `KLINE_FRESHNESS_WARN` if any coin's age >600 s; no
such line appears in the captured window for this cycle.

### PriceWorker — continuous (45s interval)

PriceWorker heartbeat at 22:25:14 (above) shows ws healthy, **5,013 msgs/min**.
Next price_worker tick at 22:25:59:
- **22:25:59.590** `LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=1 interval_s=45.0`

WS quotes cached: 50 (matches subscribed=50).

### AltDataWorker — sweet-spot 1:45 (within this 5-min window: 22:26:45)

- **22:26:45.018** `SWEET_SPOT_FIRED | worker=altdata_worker offset=1:45 drift_ms=18 fires=7`
- **22:26:55.156** `ALTDATA_TICK_DONE | funding_ms=10137 oi_ms=9940 fg_ms=0 onchain_ms=2661 total_ms=10137 ran=[funding,oi,onchain]`
- **22:26:55.156** `LAYER1A_TICK_DONE | sub=altdata_worker elapsed_ms=10138 drift_ms=18`

What ran: `funding,oi,onchain` (F&G skipped — `fg_ms=0` because the F&G deadline
hadn't lapsed; F&G fires every 60 min per `config.toml:148`).

NewsWorker tick at 22:28:12.284 (every 300s independent of sweet-spot):
- **22:28:12.284** `LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=1256 interval_s=300.0`

---

## F4.3 — Layer 1B executions

### StructureWorker — sweet-spot 0:45

- **22:25:45.001** sweet-spot fired (implicit — not in captured fragment;
  but XRAY_TICK_SUMMARY line is at 22:25:47). Earlier line in fragment:
  `22:25:30.001  SWEET_SPOT_FIRED | worker=kline_worker ...`. The
  structure_worker fire at 22:25:45 fell in a gap of the captured grep
  but its tick body emitted:
- **22:25:47.325** `XRAY_CLASSIFY_SUMMARY | total=25 bearish_fvg_ob=18 none=6 bullish_fvg_ob=1 conf_p50=0.55 conf_p95=0.55`
- **22:25:47.329** `XRAY_TICK_SUMMARY | universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 session=late_ny(mid) setups=12 skips=13 el=2303ms drift_ms=23`
- **22:25:47.333** `LAYER1B_TICK_DONE | sub=structure_worker elapsed_ms=2305 drift_ms=23`

What it read: H1 candles for 25 of 50 coins (batch=1/2). The candle
fetch path is `MarketRepository.get_klines(symbol, "60", 200)` at
`src/workers/structure_worker.py:351`, with Shadow DB fallback at line 360.

Which 25 coins processed: **batch=1/2** means the second batch of 25
(`_batch_start=25`, slicing watch_list[25:50]). The first batch (batch=0/2)
was processed in the previous cycle at 22:20:45 per the earlier fragment.

What it wrote: 25 entries written via `StructureCache.set(symbol, result)`
at `src/workers/structure_worker.py:136`. Cache is now sized
`cached=50` (the previous batch's 25 plus this batch's 25, both within
the 300s TTL).

OBSERVED: drift_ms=23 — slight late-fire. Tick still completed in 2,303 ms.

### SignalWorker — sweet-spot 1:00

- **22:26:00.021** `SWEET_SPOT_FIRED | worker=signal_worker offset=1:00 drift_ms=22 fires=7`
- **22:26:03.374** `SIG_TICK_SUMMARY | universe=50 signals=50 mean_conf=0.21 el=3352ms drift_ms=22`
- **22:26:03.375** `LAYER1B_TICK_DONE | sub=signal_worker elapsed_ms=3353 drift_ms=22`

What it read: For each of 50 symbols — invoked
`SentimentAggregator.aggregate_for_symbol(symbol)` and
`SignalGenerator.generate_signal(symbol)` (signal_worker.py:97-108).
The aggregator reads news_articles, ticker_cache (for momentum),
and fear_greed_index.

What it wrote:
- 50 rows into `aggregated_sentiment` (via repository).
- 50 entries into in-memory `_signal_cache` at `signal_worker.py:113`.
- 50 rows into `signals` table (via intelligence_aggregator).

Signal distribution: `mean_conf=0.21`. Sample DB rows show every
signal at `signal_type=neutral confidence≈0.20–0.24 source=intelligence_aggregator`.

### RegimeWorker — sweet-spot 1:15

- **22:26:15.017** `SWEET_SPOT_FIRED | worker=regime_worker offset=1:15 drift_ms=17 fires=7`
- **22:26:24.807** `REGIME_TICK_SUMMARY | universe=50 global=ranging per_coin_size=49 el=9789ms drift_ms=17`
- **22:26:24.808** `LAYER1B_TICK_DONE | sub=regime_worker elapsed_ms=9790 drift_ms=17`

What it read: Global regime via `self.detector.detect()` at
`regime_worker.py:143` (uses BTC klines). Per-coin via
`self.detector.detect_per_coin(coins_to_check)` at line 189.

What it wrote:
- 1 row into `regime_history` (global, primary symbol).
- 49 rows into `coin_regime_history` (one per coin in watch_list, minus primary).
- Updated `RegimeDetector._per_coin_regimes` (in-memory) at line 194.

Global regime: `ranging` confidence 0.4. Per-coin distribution
reflected in DB sample (BCHUSDT/LTCUSDT/APTUSDT = ranging, ALICEUSDT/OPUSDT = trending_down).

---

## F4.4 — Layer 1C execution

### StrategyWorker — sweet-spot 1:30

- **22:26:30.017** `SWEET_SPOT_FIRED | worker=strategy_worker offset=1:30 drift_ms=17 fires=7`
- **22:26:39.109** `STRAT_CONSENSUS_WRITE | full_count=9 filtered_count=7 setups_in=10 cache_size_after=19 mode=NORMAL threshold=50`
- **22:26:39.110** `STRAT_CYCLE_DONE | coins=50 signals=10 scored=10 hints=7 urg=0 el=9092ms | gate=0ms prefetch=8870ms(db=833ms ta=6011ms h1_db=2010ms h1_ta=1ms(cache_lookups=50 cache_valid=50 recomputed=0 hits=50)) L1=19ms L2=26ms L3=4ms L4=1ms misc=171ms drift_ms=17`
- **22:26:39.121** `LAYER1C_TICK_DONE | sub=strategy_worker elapsed_ms=9101 drift_ms=17`

Per `STRAT_CYCLE_DONE` breakdown at `strategy_worker.py:853-869`:

| Phase | elapsed_ms |
|-------|-----------:|
| `gate` (PnL+circuit-breaker) | 0 |
| `prefetch` total | 8,870 |
| ↳ db | 833 |
| ↳ ta (M5 TACache) | 6,011 |
| ↳ h1_db | 2,010 |
| ↳ h1_ta | 1 |
| `L1` (scan strategies) | 19 |
| `L2` (score) | 26 |
| `L3` (ensemble) | 4 |
| `L4` (handoff) | 1 |
| `misc` | 171 |
| **Total** | **9,092 ms** |

H1 TACache hit rate: `cache_lookups=50 cache_valid=50 recomputed=0 hits=50`
— 100% cache hit (the H1 prefetch only had to read from the warm cache).

What it wrote (per-cache, all in `LayerManager` instance):
- `_score_cache`: 10 entries (one per scored coin — `signals=10 scored=10`).
- `_strategy_consensus`: 9 new entries merged into existing 10
  (`full_count=9 ... cache_size_after=19`). Steady-state ~19 of 50.
- `_strategy_consensus_summary`: built from `filtered=7` setups
  (post-PnL restrictions).
- `_strategy_hints`: 7 entries (gated behind `is_layer_active(3)`).

OBSERVED: 41 of 50 coins are NOT in `_strategy_consensus` after this
cycle. Per `STRAT_CONSENSUS_WRITE` line, only 9 coins produced
consensus this tick (small relative to the 50 fed in via universe).

---

## F4.5 — Layer 1D execution

### ScannerWorker — sweet-spot 4:00 (offset 4:00 within window — fires at 22:29:00)

- **22:29:00.028** `SWEET_SPOT_FIRED | worker=scanner_worker offset=4:00 drift_ms=28 fires=8`

The scanner emits cycle markers on its tick. The **next-cycle's** scanner
data — built from this 22:25–22:29 window's upstream — will be reported
under `cycle_id=c-2026-04-27-22:25`. Searching the captured fragment:

NOT FOUND in captured workers.log fragment for `c-2026-04-27-22:25`
SCANNER_FILTER_AGGREGATE / SCANNER_PACKAGE_BUILD / SCANNER_SELECT lines.
The ScannerWorker fire at 22:29:00.028 happened, but the sub-second
detail lines (which usually appear within ~30 ms of fire) were not in
the grep output we collected. The most-recent fully captured scanner
output we have is for `cycle_id=c-2026-04-27-22:20` at 22:24:00, which
is the BEFORE-cycle for this trace.

#### Most-recent captured scanner cycle (cycle_id=c-2026-04-27-22:20, fired 22:24:00)

This is the cycle Brain CALL_A at 22:23:22 / 22:25:16 actually read:

```
22:24:00.001  SWEET_SPOT_FIRED | worker=scanner_worker offset=4:00 drift_ms=1 fires=7
22:24:00.001  LAYER1D_CYCLE_START | cycle_id=c-2026-04-27-22:20
22:24:00.014  SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-04-27-22:20 total=50
              qualified=0
              fail_no_xray=25 fail_setup_none=10 fail_consensus=12
              fail_regime=2 fail_rr=1 fail_blockers=0
              pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0
22:24:00.016  SCANNER_PACKAGE_BUILD_START | cycle_id=c-2026-04-27-22:20 packages_to_build=2
22:24:00.017  PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=BTCUSDT
              completeness=0.67 verdict=warn
              missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
22:24:00.017  PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=ETHUSDT
              completeness=0.73 verdict=warn
              missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed'] stale=[]
22:24:00.018  SCANNER_PACKAGE_BUILD_DONE | cycle_id=c-2026-04-27-22:20 packages=2
              total_size_bytes=1894 elapsed_ms=2
22:24:00.019  PACKAGE_VALIDATE_SUMMARY | cycle_id=c-2026-04-27-22:20 packages_built=2
              ok=0 warn=2 fail_quarantined=0
22:24:00.020  CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20
              klines_age_p50_ms=209932 klines_age_p95_ms=1704546
              xray_age_p50_ms=194995 xray_age_p95_ms=494936
              packages_age_p50_ms=1 packages_age_p95_ms=1
              klines_keys=200 xray_keys=50 packages_keys=3
22:24:00.031  SCANNER_SELECT | cycle_id=c-2026-04-27-22:20 qualified=0
              selected=2 forced=2 watch_list=50
22:24:00.031  SCANNER_TICK_SUMMARY | watch_list=50 protected=0 scored=2 selected=2
              top_n=15 forced_in=2 mean_score=0.000 top=BTCUSDT(0.000) el=29ms drift_ms=1
22:24:00.031  LAYER1D_CYCLE_DONE | cycle_id=c-2026-04-27-22:20 elapsed_ms=30
22:24:00.033  CYCLE_COMPLETE | cycle_id=c-2026-04-27-22:20 layer1a_ms=0 layer1b_ms=6655
              layer1c_ms=7794 layer1d_ms=30 total_ms=14479 packages_ready=2 qualified_pct=0.0 status=ok
22:24:00.033  LAYER1D_TICK_DONE | sub=scanner_worker elapsed_ms=32 drift_ms=1
```

Final package count for cycle c-2026-04-27-22:20: **2** (BTCUSDT, ETHUSDT — both forced/reference pairs, both warn-verdict).

---

## F4.6 — Stage 2 read (Brain CALL_A)

The captured `data/logs/brain.log` shows Brain CALL_A reads. The most-recent CALL_A relative to this trace started at 22:23:22 and finished at 22:25:16:

```
22:23:22.866  STRAT_CALL_A_START | did=d-1777328602866
22:23:23.019  STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=263 age_max_s=263 reader=brain_call_a
22:23:24.085  STRAT_CALL_A_CTX | sections=40 chars=6529 el=1207ms
22:23:24.085  PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6568 sections=40 packages=2 elapsed_ms=1207
22:23:24.119  STRAT_CALL_A | chars=6568
22:25:16.717  STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Late NY dead zone, both BTC and ETH sold off hard today (-2.3% and -3.5%). Low v'
22:25:16.717  STRAT_CALL_A_END | el=113851ms trades=2
```

Reader site: `src/brain/strategist.py:1371-1372` —
`if lm is not None and hasattr(lm, "get_coin_packages"): packages = lm.get_coin_packages()`.
The log emit `STRATEGIST_PACKAGES_READ` is at `src/brain/strategist.py:1391`.

**Packages received:** 2 (BTCUSDT, ETHUSDT).
**Package age at read:** `age_min_s=263 age_max_s=263` (4 min 23 s).
**Final prompt size:** `chars=6568 size_bytes=6568 sections=40 packages=2 elapsed_ms=1207` to build.
**Brain decision:** `trades=2 risk=cautious`. (Subsequent BRAIN_DO_START at 22:25:16.718.)

### CALL_A reads in the captured brain.log window (5 events)

| Brain CALL_A start | did | Packages count | Age min/max | Prompt chars/sections | Trades |
|---|---|---|---|---|---|
| 22:01:43.869 | d-1777327303869 | 2 | 163/163 | 6946/49 chars | 2 |
| 22:08:47.920 | d-1777327727920 | 2 | 287/287 | 6517/41 chars | 1 |
| 22:17:03.516 | d-1777328223516 | 2 | 183/183 | 6973/44 chars | 0 |
| 22:23:22.866 | d-1777328602866 | 2 | 263/263 | 6529/40 chars | 2 |
| (no later CALL_A in fragment captured) | | | | | |

Every Brain CALL_A in the captured window read exactly **2 packages**
(forced BTC+ETH). Package ages range 163–287 s (≈3–5 minutes), which is
consistent with the ScannerWorker firing every 5 minutes at the `4:00`
sweet-spot offset and Brain CALL_A firing on its own 150 s cadence.

---

## Cycle timing summary (this cycle, 22:25:00 boundary)

```
+0:00  M5 boundary
+0:30  kline_worker fires      → KLINE_TICK_SUMMARY at +0:51 (21,363 ms)
+0:45  structure_worker fires  → XRAY_TICK_SUMMARY at +0:47 (2,303 ms, batch 1/2 — 25 coins)
+1:00  signal_worker fires     → SIG_TICK_SUMMARY at +1:03 (3,352 ms, 50 signals)
+1:15  regime_worker fires     → REGIME_TICK_SUMMARY at +1:24 (9,789 ms, 49 per-coin)
+1:30  strategy_worker fires   → STRAT_CYCLE_DONE at +1:39 (9,092 ms, 9 consensus, 7 hints)
+1:45  altdata_worker fires    → ALTDATA_TICK_DONE at +1:55 (10,137 ms, funding+oi+onchain)
+4:00  scanner_worker fires    → captured cycle's scanner output not in grep fragment;
                                  prior cycle (c-2026-04-27-22:20) at 22:24:00 produced
                                  qualified=0 selected=2 forced=2.
```

The **next** brain CALL_A will read whatever ScannerWorker produces from
this cycle (cycle_id `c-2026-04-27-22:25`). Brain CALL_A interval is
150 s per `src/core/layer_manager.py:85`; the next CALL_A relative to
22:25:16 is at ~22:30:16 (alternation: A→B→A) — not in the captured
brain.log fragment.

OBSERVED ANOMALY: across 7 captured Scanner cycles in workers.log
(22:09:00, 22:14:00, 22:19:00, 22:24:00, 22:29:00, 22:34:00, 22:39:00,
22:44:00) every `SCANNER_SELECT` line shows `qualified=0 selected=2 forced=2`.
The pipeline has not produced a single non-forced qualified coin in
the captured window.

OBSERVED ANOMALY: `CYCLE_FRESHNESS` for cycle c-2026-04-27-22:20 shows
`klines_age_p95_ms=1704546` (~28 minutes) and `xray_age_p95_ms=494936`
(~8 minutes) at scanner read time — meaning some kline-cache entries
are very stale at the moment ScannerWorker computes qualification.
`packages_keys=3` (BTCUSDT, ETHUSDT, and one more — NOT FOUND which
third key by name; the snapshot active_universe shows only 2 rows but
the freshness counter suggests a third package was written and
maybe quarantined by validator).


================================================================================
FILE: G1_config_toml.md
================================================================================

# G1 — config.toml (Verbatim)

## Capture metadata

- **Source file:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml`
- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **File size:** 42755 bytes
- **Line count:** 1094 (last newline only)
- **mtime:** 2026-04-27 20:25:42 UTC
- **md5:** `d5c308beb5441fb193217013e3f3a545`
- **Secret redaction:** scanned for `api_key|API_KEY|secret|SECRET|password|PASSWORD|token|TOKEN` — only matches are unrelated config keys (`max_tokens`, `advisor_max_tokens`) and one comment about OAuth refresh. No literal credentials present in the file. Nothing redacted.

## Verbatim contents (config.toml lines 1-1094)

```toml
# =============================================================================
# Trading Intelligence MCP — Master Configuration
# =============================================================================
# All settings for the entire system. Env vars override values here.
# Copy .env.example → .env and fill in your API keys.
# =============================================================================

[general]
# Trading mode: "shadow" (Shadow virtual exchange), "paper" (Bybit testnet), "live" (real funds)
mode = "shadow"
# Shadow API URL (only used when mode = "shadow")
shadow_api_url = "http://127.0.0.1:9090"
# Timezone for display (internal always UTC)
timezone = "UTC"
# How verbose: DEBUG, INFO, WARNING, ERROR
log_level = "INFO"
# Directory for log files (relative to project root)
log_dir = "data/logs"

[bybit]
# Bybit mainnet for REAL market data. Orders routed via Transformer to Shadow (paper).
testnet = false
# Fallback symbols (used when bulk ticker API fails). Scanner dynamically
# selects top 20 by score from all Bybit USDT perps on each scan cycle.
default_symbols = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "1000PEPEUSDT",
    "WIFUSDT", "HYPEUSDT", "AAVEUSDT", "NEARUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "LTCUSDT", "BCHUSDT", "TONUSDT",
]
# Rate limit: max requests per second to Bybit REST API
rate_limit_per_second = 10
# WebSocket ping interval in seconds
ws_ping_interval = 20
# Reconnect delay on WS disconnect (seconds)
ws_reconnect_delay = 5
# Order receive window in milliseconds
recv_window = 5000

[finnhub]
# Enable Finnhub news + calendar integration
enabled = true
# Rate limit: Finnhub free tier allows 60 calls/min
rate_limit_per_minute = 60
# Categories to fetch: general, forex, crypto, merger
news_categories = ["crypto", "general"]
# Max articles to fetch per poll
max_articles_per_fetch = 50

[reddit]
# Enable Reddit sentiment analysis via PRAW
enabled = false
# Subreddits to monitor for crypto sentiment
subreddits = ["cryptocurrency", "bitcoin", "ethtrader", "CryptoMarkets", "solana"]
# Max posts to fetch per subreddit per poll
max_posts_per_sub = 25
# Minimum score threshold to consider a post relevant
min_score = 10
# Rate limit: Reddit allows ~60 requests/min with OAuth
rate_limit_per_minute = 60

[altdata]
# Enable alternative data collection (Fear & Greed, funding rates, etc.)
enabled = true
# Fear & Greed index poll interval in seconds (API updates ~daily)
fear_greed_interval = 3600
# Funding rate poll interval in seconds
funding_rate_interval = 300
# Open interest poll interval in seconds
open_interest_interval = 600
# CoinGecko rate limit (free tier: 10-30 calls/min)
coingecko_rate_limit_per_minute = 10

[database]
# SQLite database path (relative to project root)
path = "data/trading.db"
# WAL mode for concurrent reads during writes
wal_mode = true
# Connection pool size (for future PostgreSQL migration)
pool_size = 5
# Query timeout in seconds
query_timeout = 30
# Auto-vacuum interval in hours
vacuum_interval = 24
# Phase 1 (D-3 fix) — chunk size for MarketRepository.save_klines.
# A single executemany over the full per-(symbol, timeframe) batch held
# the DatabaseManager lock for 12-20 s during heavy ticks, queueing every
# other worker behind it. Chunking + yielding the loop between chunks
# eliminates that contention without changing total wall-clock work.
kline_save_chunk_size = 500
# Phase 1 (D-3 fix) — WAL checkpoint scheduler cadence (in kline_worker
# ticks). PASSIVE checkpoints never block, so it's safe to schedule
# them often. The historical 100 MiB pinned -wal file disappeared once
# we stopped relying on opportunistic auto-checkpoint + hourly cleanup.
wal_checkpoint_every_n_kline_ticks = 50
# Phase 1 (D-3 fix) — escalate to TRUNCATE if PASSIVE checkpoints come
# back busy this many times in a row. TRUNCATE briefly blocks writers
# but fully reclaims WAL space when readers consistently pin snapshots.
wal_checkpoint_truncate_after_busy_count = 3
# Phase 1 (D-3 fix) — DB_LOCK_WAIT warn threshold (ms). Drop to 500 ms
# during verification to see finer-grained contention, then raise back.
db_lock_wait_threshold_ms = 1000

[workers]
# Enable background data collection workers
enabled = true
# Market data worker: OHLCV + ticker polling interval (seconds)
market_data_interval = 45
# News worker: Finnhub polling interval (seconds)
news_interval = 300
# Reddit worker: sentiment polling interval (seconds)
reddit_interval = 600
# Alt data worker: funding rates, OI, Fear & Greed interval (seconds)
altdata_interval = 300
# Health check interval: how often workers report status (seconds)
health_check_interval = 120
# Max consecutive failures before worker restarts
max_consecutive_failures = 5
# Worker restart delay (seconds)
restart_delay = 10

# ─────────────────────────────────────────────────────────────────────
# Sweet-spot scheduling — corrected Layer 1 architecture (Phase 1).
#
# The 7 data workers fire at these MM:SS offsets within every 5-minute
# window. Chain ordering is enforced (kline → structure → signal →
# regime → strategy → scanner) so each downstream worker reads warm
# upstream data. PriceWorker is continuous (no sweet spot needed).
# Bad MM:SS values or out-of-order chain → ConfigError at startup.
#
# Reference: LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §8.
# ─────────────────────────────────────────────────────────────────────
[workers.sweet_spots]
window_minutes = 5
kline_worker = "0:30"
structure_worker = "0:45"
signal_worker = "1:00"
regime_worker = "1:15"
strategy_worker = "1:30"
scanner_worker = "4:00"

[workers.sweet_spots.altdata]
# Funding rates: MM:SS within window, between regime (1:15) and scanner (4:00).
funding_rates = "1:45"
# Open interest: every N minutes, independent of window.
open_interest_minutes = 5
# Fear & Greed: every M minutes, hourly default.
fear_greed_minutes = 60

[brain]
# Claude Code CLI — no API key needed, no budget limit
# Uses existing Claude Max subscription ($0 per call)
enabled = true
use_claude_code = true
# Layer 1 restructure Phase 7 — when true, the strategist reads
# per-coin sections from layer_manager._coin_packages instead of
# querying 12 services per cycle. Set false to fall back to the
# legacy service-query path during Phase 9 observation if a
# regression is detected.
use_packages = true
# Strategic review interval (seconds) — alternating Call A (trades) / Call B (positions)
# 150s = 2.5 min between calls, giving 5 min per call type
strategic_interval = 150
# Watchdog Claude review interval (seconds) — reviews positions every 30s
watchdog_interval = 30
# Legacy settings kept for backward compatibility
analysis_interval = 900
signal_triggered = true
min_signal_confidence = 0.45
max_calls_per_hour = 30
model = "claude-sonnet-4-20250514"
max_tokens = 4096
temperature = 0.3

# Claude CLI subprocess timing (Phase 2 session-stability fix — Y-22 + timeout retune)
# Hard cap on one Claude CLI invocation. Was hardcoded 300 in manager.py.
claude_cli_timeout_seconds = 300
# Retries after failure (non-retryable errors — auth, billing — still skip retry).
claude_cli_max_retries = 2
# Floor between consecutive Claude CLI invocations (adaptive interval).
claude_cli_min_interval = 2.0
# Backoff base for timeout-path retries: sleep = (attempt+1) * base seconds.
# 10 → ladder 10s/20s/30s. Was hardcoded 30 → 30s/60s/90s.
# Lowering halves the brain-outage window after a single timeout.
claude_cli_retry_timeout_backoff_base_seconds = 10
# Phase 3 (Brain credentials) — pre-flight refresh margin in seconds.
# Trigger an OAuth refresh if the access token expires within this window;
# if the refresh fails AND we are inside the margin, raise
# CredentialRefreshError instead of spawning a doomed subprocess.
credential_refresh_margin_seconds = 600
# Phase 3 (Brain credentials) — refresh attempt budget per call.
# 3 attempts with exponential backoff (1s/3s/7s) before giving up.
credential_refresh_max_attempts = 3
# Cap on watchdog events injected into the Call A URGENT prompt.
# Defence-in-depth — EventBuffer already truncates at 3000 chars.
prompt_event_buffer_max_events = 20

[risk]
# ===================== RISK MANAGEMENT — AGGRESSIVE (PAPER ONLY) =====================
max_leverage = 5
mandatory_stop_loss = true
default_stop_loss_pct = 3.0
default_take_profit_pct = 6.0
max_position_size_pct = 20.0
max_open_positions = 10
daily_loss_limit_pct = 10.0
max_total_exposure_pct = 80.0
max_drawdown_pct = 25.0
min_order_value_usdt = 5.0
loss_cooldown_seconds = 30

[alerts]
# Enable Telegram alert notifications
telegram_enabled = true
# Alert levels to send: INFO, WARNING, CRITICAL
alert_levels = ["WARNING", "CRITICAL"]
# Send daily performance summary
daily_summary = true
# Daily summary time (UTC, HH:MM format)
daily_summary_time = "00:00"
# Rate limit alerts: max messages per minute
max_alerts_per_minute = 10
# Include trade entry/exit alerts
trade_alerts = true
# Include signal alerts
signal_alerts = true
# Include error alerts
error_alerts = true

[mcp]
# MCP transport: "stdio" for Claude Code, "sse" for browser/claude.ai
transport = "stdio"
# SSE server host (only used when transport = "sse")
sse_host = "0.0.0.0"
# SSE server port
sse_port = 8080
# Authentication required for SSE transport
sse_auth_required = true
# Server name advertised via MCP
server_name = "trading-intelligence"
# Server version
server_version = "0.1.0"

[watchdog]
# Position Watchdog: code rules every 10s (timer, trailing, hard stops, duplicates)
# Claude reviews handled by LayerManager every 30s
enabled = true
# How often to check positions (seconds) — code rules
check_interval_seconds = 10
# Alert when position loses > X% from entry
loss_warning_pct = 0.5
# Alert when position drops > X% from its peak unrealized profit
trailing_loss_pct = 0.3
# Alert when price is within X% of the distance to stop-loss
sl_proximity_pct = 30.0
# Alert when price moves > X% against position in single check
rapid_move_pct = 0.5
# Trigger Claude Brain when position loses > X%
brain_trigger_loss_pct = 0.8
brain_cooldown_seconds = 60
partial_close_pct = 50.0
max_brain_calls_per_hour = 20
# Layer 3: early-exit disabled (0% historical win rate, 24/24 losses).
# SL handles exits cleanly. Set true to re-enable; monitoring log
# 'EARLY_EXIT_DISABLED_WOULD_FIRE' shows what it would have done.
early_exit_enabled = false
# Phase 2 (P0-1 Ghost Positions): fast set-diff reconcile cadence —
# independent of the 5-min thesis sweep. 0.0 disables (kill switch).
fast_reconcile_seconds = 30.0

[mcp_pool]
# Phase 23 (Y-22) — MCP client pool. Disabled by default; turn on per
# consumer to migrate off the one-shot stdio storm. Each consumer that
# imports MCP tools should:
#   1. Set ``enabled = true`` here.
#   2. Run ``python server.py --transport sse`` in the background
#      (workers manager can host this — add to manager.py if needed).
#   3. Acquire a client via ``MCPClientPool.acquire`` instead of
#      shelling out to ``server.py`` per call.
# When enabled=false, every consumer keeps using one-shot stdio.
enabled = false
sse_url = "http://127.0.0.1:8080"
min_warm = 1
max_warm = 2
health_check_interval_seconds = 60
acquire_timeout_seconds = 2.0

[price]
# Phase 3 (P0-2 Price Divergence) — local price freshness vs Shadow.
# - local_max_age_seconds: above this age the WebSocket-fed local price
#   is treated as stale and the consumer falls back to Shadow's 1 Hz
#   authoritative mark. Set to a large number (e.g. 999999) to disable.
# - divergence_override_pct: above this divergence (% of Shadow's price)
#   Shadow's price is preferred over local. Emits PRICE_OVERRIDE log +
#   MED-priority event_buffer entry so Claude sees the override.
# - divergence_block_prompt_pct: any open position with divergence >
#   this value blocks Claude's prompt build (PROMPT_DEFERRED). The
#   strategist re-tries on the next cycle, after the WS re-syncs.
local_max_age_seconds = 10.0
divergence_override_pct = 0.5
divergence_block_prompt_pct = 1.0

[sl_gateway]
# Single-entry-point stop-loss gateway (Layer 3 hardening).
# When enabled, every SL modification (Time-Decay, SENTINEL, Profit Sniper
# trail, watchdog trail, brain tighten) routes through one validator that
# enforces tighten-only + min-distance + max-step + rate-limit.
#
# Rollout protocol:
#   1. Start with enabled=false (symmetric pass-through, state tracked).
#   2. Verify SL_GATEWAY_PASSTHROUGH count equals SL_PROPAGATED count.
#   3. Flip enabled=true with log_only_global=true to observe rejects.
#   4. Flip log_only flags to false one at a time for staged enforcement.
# Dry-run enabled by the Prefetch-Performance Fix (2026-04-23): every rule
# is now evaluated but REJECTs are downgraded to SL_GATEWAY_REJECT_WOULD
# logs. No trades are blocked. Once SL_GATEWAY_STATS by_rsn distribution is
# validated over a session, set log_only_global=false for hard enforcement.
enabled = true
# Rule R2: minimum distance between new SL and current price (percent).
# Legacy static fallback; when the volatility profiler is wired (default
# since manager.py), R2 uses an ATR-scaled effective min — see the
# min_distance_atr_multiplier / _abs_floor_pct keys below.
min_distance_pct = 0.3
# Rule R3: maximum step size per single SL update (percent of previous SL).
max_step_pct = 0.5
# Rule R4: minimum seconds between SL updates per symbol.
rate_limit_seconds = 30
# Global log-only: every rule becomes a SL_GATEWAY_REJECT_WOULD log.
log_only_global = true
# Per-rule log-only flags (surgical rollout controls).
# Tighten-only MUST stay false — it is safety-critical.
log_only_tighten_only = false
log_only_min_distance = false
log_only_max_step = false
log_only_rate_limit = false

# ATR-scaled R2 min_distance (user spec: max(0.05%, atr_5m_pct * 0.5)).
# Aggressive to maximise accepted trail pushes on low-vol coins. The
# absolute floor prevents bid-ask strangulation. Class ceiling clamps
# freak spikes. See src/analysis/vol_scale.py.
min_distance_atr_multiplier = 0.5
min_distance_abs_floor_pct = 0.05

# Per-class ceiling for the ATR-scaled min_distance. Anything above these
# values would be an SL that's pathologically far from price (e.g. flash
# crash). Dead coins cap at 0.30% (their baseline noise); extreme coins
# can go up to 3.50% during real volatility.
[sl_gateway.min_distance_class_ceiling]
dead = 0.30
low = 0.50
medium = 1.00
high = 2.00
extreme = 3.50

[scanner]
# Market scanner — AGGRESSIVE TESTNET MODE
enabled = true
scan_interval_seconds = 300
min_volume_24h = 5000000
max_coins = 30
max_spread_pct = 0.15
# Phase 5 (Universe flapping fix) — re-entry cooldown bumped from the
# legacy hardcoded 300 s. A coin removed from the active universe
# cannot re-enter for this many seconds (force-included coins bypass).
reentry_cooldown_seconds = 600

# Phase 5 (Universe flapping fix) — consecutive-scan hysteresis on the
# active_universe membership decision. Without it, a coin oscillating
# around the cutoff score enters/exits every scan, triggering
# KLINE_BACKFILL → cold start → STRAT_SKIP_STALE storms (live obs:
# 14 rotations/hour on 2026-04-26).
[scanner.hysteresis]
enabled = true
entry_consecutive_scans = 2
exit_consecutive_scans = 3
entry_threshold_above_min = 5
exit_threshold_below_min = -5

# ─────────────────────────────────────────────────────────────────────
# Composite opportunity scoring (Phase 6 — corrected Layer 1).
#
# ScannerWorker reads warm caches from the 7 data workers and computes
# a per-coin opportunity score as a weighted sum of these 5 components.
# Tunable: re-balance based on observed trade outcomes.
#
# Reference: LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §9.3.
# ─────────────────────────────────────────────────────────────────────
[scanner.scoring_weights]
structure = 0.30   # X-RAY setup score (0-100, normalized)
strategy = 0.30    # StrategyWorker L2 total_score (normalized to 0-1)
signal = 0.15      # SignalWorker confidence (0-1)
regime = 0.15      # Regime alignment factor (-1..+1)
funding = 0.10     # Funding rate strength (|rate| > 0.01% counts)

# Layer 1 restructure Phase 5 — qualitative checklist applied BEFORE
# the quantitative ranking step. All five criteria must pass for a
# coin to be considered for selection (force-included open-position
# coins skip this gate per HR-2). See ScannerQualitativeSettings.
[scanner.qualitative]
min_rr_ratio = 2.0
min_consensus = "GOOD"            # STRONG | GOOD; LEAN/WEAK/CONFLICT always fail
require_regime_alignment = true
funding_blocker_threshold_pct = 0.001  # 0.1% — funding above this against direction blocks
recent_failure_blocker_hours = 1
max_selection = 15
min_selection = 0                  # output empty when nothing qualifies

# =============================================================================
# Universe — Layer 1 alignment (single source of truth for "what coins?")
# =============================================================================
# Operator-curated 50-coin watch list. The single source of truth for
# Layer 1: Shadow subscribes to these, ScannerWorker filters from these,
# all downstream workers operate on the 30-coin active subset selected
# from these every 5 minutes. Open-position coins are always
# force-included even if outside this list (HR-2 in the blueprint).
#
# Manual review recommended weekly. See WATCH_LIST_50.md for the full
# selection rationale (3-tier composition: 12 majors + 23 mid-caps +
# 15 aggressive opportunity hunters, calibrated for ~$100 capital with
# $0.50+/hour profit goal).
#
# Validation rules (enforced by UniverseSettings.__post_init__):
#   - non-empty, length ≥ 10
#   - every entry matches ^[A-Z0-9]+USDT$ (uppercase, USDT-quoted)
#   - no duplicates
#
# 2026-04-26 substitution per WATCH_LIST_50.md alternates protocol:
#   FETUSDT → AEROUSDT (FETUSDT is status=Closed on Bybit; AEROUSDT
#   is the top Tier B alternate listed in WATCH_LIST_50.md)

[universe]
watch_list = [
  # Tier A — Always-On Majors (12) — safety net + tight-spread scalping
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
  "XRPUSDT",
  "ADAUSDT",
  "DOGEUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "ARBUSDT",
  "NEARUSDT",
  "ATOMUSDT",
  # Tier B — Volatile Mid-Caps (23) — main hunting ground for daily exploitation
  "INJUSDT",
  "RENDERUSDT",
  "ONDOUSDT",
  "ENAUSDT",
  "PYTHUSDT",
  "SEIUSDT",
  "AEROUSDT",  # substituted for delisted FETUSDT (top Tier B alternate)
  "RUNEUSDT",
  "GALAUSDT",
  "MANAUSDT",
  "SANDUSDT",
  "AXSUSDT",
  "LDOUSDT",
  "CRVUSDT",
  "DYDXUSDT",
  "AAVEUSDT",
  "ICPUSDT",
  "IMXUSDT",
  "HBARUSDT",
  "HYPEUSDT",
  "GMTUSDT",
  "FILUSDT",
  "MNTUSDT",
  # Tier C — Aggressive Opportunity Hunters (15) — high-leverage opportunity strikes
  "MONUSDT",
  "SKRUSDT",
  "PLUMEUSDT",
  "EGLDUSDT",
  "ALGOUSDT",
  "BSBUSDT",
  "KATUSDT",
  "HYPERUSDT",
  "ORCAUSDT",
  "BLURUSDT",
  "OPUSDT",
  "APTUSDT",
  "LTCUSDT",
  "BCHUSDT",
  "ALICEUSDT",
]

[regime]
# Market regime detector
detection_interval_seconds = 600
primary_symbol = "BTCUSDT"
trending_adx_threshold = 25
ranging_adx_threshold = 20
ranging_choppiness_threshold = 60
volatile_atr_percentile = 150
dead_adx_threshold = 15
dead_volume_ratio = 0.5
# Phase 3 (output-quality): per-symbol confirm-N-readings hysteresis.
# Pre-fix this was hardcoded at 2 in src/strategies/regime.py:185.
# Higher → more sticky regimes (fewer false flips), lower → more
# responsive (potentially flapping). Validated >= 1 at config-load.
hysteresis_count = 2

[strategy_engine]
# 4-layer strategy execution engine — AGGRESSIVE
scan_interval_seconds = 45
min_score_threshold = 0
min_ensemble_agreement = 2.5
max_ensemble_opposition = 2.5
max_setups_to_brain = 10
max_brain_calls_per_hour = 30

[pnl_targets]
# Daily PnL — AGGRESSIVE (paper trading)
daily_target_pct = 10.0
protect_threshold_pct = 7.0
caution_threshold_pct = -3.0
survival_threshold_pct = -7.0
halt_threshold_pct = -10.0

[leverage]
# Smart leverage — AGGRESSIVE
max_leverage = 5
tier_1_max = 5
tier_2_max = 5
tier_3_max = 4
volatile_max = 4
dead_max = 3
min_confidence_for_5x = 0.65
min_confidence_for_4x = 0.55

[optimizer]
# Weekly adaptive optimizer
enabled = true
run_day = "sunday"
run_hour_utc = 0
weight_adjustment_pct = 10
max_param_change_pct = 20
min_trades_for_optimization = 20
underperform_threshold_pct = 10
disable_after_weeks = 3

[factory]
# Strategy Factory: AI-powered pattern discovery and strategy generation
enabled = false  # Disabled: 0 patterns discovered, 0 backtests run — wasting CPU
discovery_schedule_hour_utc = 2
discovery_lookback_days = 14
min_pattern_occurrences = 10
min_win_rate = 0.52
min_profit_factor = 1.1
min_statistical_significance = 0.05
max_strategies_per_batch = 10
max_generation_retries = 3
generation_cost_limit_usd = 0.20
live_monitor_interval_seconds = 300
hot_pattern_threshold_win_rate = 0.70
hot_pattern_threshold_occurrences = 3
emergency_generation_enabled = true

[backtesting]
# Backtesting engine configuration
initial_capital = 10000
default_leverage = 3
commission_pct = 0.06
slippage_pct = 0.02
funding_rate_pct = 0.01
walk_forward_enabled = true
train_pct = 0.70
monte_carlo_runs = 1000
min_trades_to_pass = 15
min_win_rate = 0.50
min_profit_factor = 1.1
max_drawdown_pct = 20.0
min_sharpe = 0.3
min_walk_forward_efficiency = 0.4
max_ruin_probability = 0.05

[trial]
# Paper trading trial configuration
trial_duration_days = 3
max_extensions = 1
extension_duration_days = 7
trial_position_size_pct = 50
min_trades_for_evaluation = 5
promotion_min_win_rate = 0.48
promotion_min_pnl = 0.0
promotion_max_drawdown = 10.0
max_active_strategies = 80
demotion_underperform_weeks = 2
demotion_win_rate_drop_pct = 15
quarterly_revival_enabled = true

[portfolio]
# Portfolio Optimizer — capital allocation and risk management
enabled = true
optimization_day = "sunday"
optimization_hour_utc = 0
kelly_fraction = 0.40
min_trades_for_kelly = 20
max_strategy_allocation_pct = 15.0
min_strategy_allocation_pct = 2.0
proven_strategies_budget_pct = 52.0
ai_strategies_budget_pct = 33.0
trial_strategies_budget_pct = 12.0
cash_reserve_pct = 3.0
correlation_lookback_days = 30
high_correlation_threshold = 0.7
daily_risk_budget_pct = 8.0
drawdown_reduction_threshold_1 = 8.0
drawdown_reduction_factor_1 = 0.7
drawdown_reduction_threshold_2 = 15.0
drawdown_reduction_factor_2 = 0.4
kelly_weight = 0.30
mean_variance_weight = 0.40
risk_parity_weight = 0.30
min_rebalance_change_pct = 2.0
stress_test_enabled = true

[telegram_interactive]
# Interactive Telegram Bot
enabled = true
ai_responses_enabled = true
max_ai_calls_per_hour = 20
trade_confirmation_required = true
morning_briefing_enabled = true
morning_briefing_hour_utc = 5
price_alert_check_interval = 10

[fund_manager]
# Intelligent Fund Manager — 22-module capital management
enabled = true
check_interval_seconds = 60
starting_unlock_pct = 20
active_pool_pct = 70
aplus_reserve_pct = 20
emergency_reserve_pct = 10
profit_lock_pct = 50
trade_profit_lock_pct = 25
max_correlation_bucket_pct = 30
min_profitable_trade_fee_pct = 0.12

# ─── Phase 5 (post-Layer-1 fix): FundReconciler ──────────────────────
# reconcile_enabled — master switch. When False, the FundReconciler
#   worker is not registered; balance drift will go undetected until a
#   trade is rejected by Bybit (ErrCode 110007). Strongly recommended
#   to keep True in any environment with a real Bybit wallet.
# reconcile_interval_seconds — heartbeat cadence. 60 s is the minimum
#   sensible cadence given Bybit's REST quota and the typical drift
#   evolution rate. Faster reconciliation does not help; slower means
#   drift can persist longer before alerting.
# reconcile_drift_alert_threshold_pct — absolute drift % between local
#   total_equity and exchange total_equity past which a WARNING +
#   Telegram alert fires. 5 % is conservative — typical post-trade
#   noise from fee deductions and unrealized PnL is well under 1 %.
# reconcile_auto_correct — when True, drift triggers an in-place
#   overwrite of local total_equity from exchange. OFF by default
#   because auto-correcting silently is not auditable; operators
#   must opt-in explicitly.
reconcile_enabled = true
reconcile_interval_seconds = 60
reconcile_drift_alert_threshold_pct = 5.0
reconcile_auto_correct = false

[enforcer]
# Enforcer v2 — PnL-Based Intelligent Throttling
enabled = true
check_interval_seconds = 60

# PnL-based thresholds (daily PnL %)
pnl_caution_pct = -2.0              # Below this → el=1 (capital preservation)
pnl_survival_pct = -5.0             # Below this → el=2 (survival)

# Size reduction for mild negative PnL
size_reduction_enabled = true
size_reduction_at_pnl_pct = 0.0     # Start reducing below this PnL %
size_reduction_factor = 0.75        # 25% smaller positions (0% to caution)

# Streak as secondary signal (only when PnL is negative)
streak_boost_threshold = -5         # 5-loss streak + negative PnL → immediate el=1

# Auto-recovery
max_enforcement_minutes = 45        # Auto-recover after stuck at el>=1
grace_period_minutes = 30           # Manual reset grace (full skip)

# Per-level restrictions
level_1_max_positions = 3
level_1_max_leverage = 3
level_1_min_score = 75
level_2_max_positions = 2
level_2_max_leverage = 3
level_2_min_score = 80
level_2_min_confluence = 7
level_2_min_rr = 3.0

# Legacy fields (kept for backward compatibility)
decay_minutes = 60
min_trades_per_hour = 20
min_profit_per_hour_pct = 5.0
min_win_rate = 0.45
min_signals_per_hour = 50
min_setups_to_brain_per_hour = 10
max_seconds_between_trades = 90
max_escalation_level = 5
force_trade_on_gap = true
rewards_enabled = true
hourly_report_enabled = true

[mode4]
# Mode 4: ProfitSniper — institutional-grade profit protection (Phase 1-10)
# 5 mathematical models: Hurst, Momentum Decay, ATR Extension,
# Volume Divergence, Risk/Reward. Regime-aware scoring + ATR trailing.
enabled = true
check_interval_seconds = 5

# Ring Buffer (Phase 1)
buffer_max_size = 720                    # 60 minutes at 5s (720 entries)
buffer_min_ready = 100                   # Need 8+ minutes for valid models

# Trailing System (Phase 8)
base_atr_multiplier = 2.5               # Chandelier Exit base width in ATR units
trail_min_change_pct = 0.1              # Min SL change % to avoid Shadow flooding
regime_factor_trending = 1.3            # Wider trail — let trends run
regime_factor_ranging = 0.7             # Tighter trail — reversion likely
regime_factor_volatile = 1.0            # Standard trail — volatility in ATR
regime_factor_dead = 0.6               # Tightest trail — no momentum, protect gains

# Anti-Greed (Phase 9) — pullback backstop
anti_greed_enabled = true
anti_greed_pullback_40_min_peak = 2.0   # Min peak % for 40% pullback → tighten
anti_greed_pullback_60_min_peak = 3.0   # Min peak % for 60% pullback → partial
anti_greed_pullback_75_min_peak = 5.0   # Min peak % for 75% pullback → full close

# Action cooldowns (Phase 9)
tighten_cooldown_seconds = 30
partial_close_cooldown_seconds = 120
partial_close_pct = 50                  # % of position to close on partial action

# Phase 4 (Sniper-loop fix) — type-agnostic per-position cooldowns.
# The legacy partial_close_cooldown_seconds only blocked the NEXT
# partial when the IMMEDIATELY-prior action was also a partial; an
# alternating tighten ↔ partial pattern defeated it (INJUSDT 21:48
# bug, 4× partials in 60s). The new gate is type-agnostic.
min_seconds_between_actions = 60        # any M4 action of any type starts this cooldown
min_seconds_before_close = 180          # full_close from score branch (anti-greed bypasses)
# Phase 4 (Sniper-loop fix) — PROFIT GATE on partials. The legacy
# P9_CLOSE_GATE only gates full_close; a partial could fire on a red
# position. Default 0.0 = require break-even before any partial fires.
min_profit_for_partial_pct = 0.0

# Phase 9 Sniper Stall Escape (P1-8) + Phase 4A de-escalation (session-stability)
# Escalation fires when the sniper is stuck at "actionable=True but action=hold".
stall_escape_partial_after_ticks = 20    # ~100s at 5s cadence → first escape
stall_escape_full_after_ticks = 40       # ~200s at 5s cadence → escalate to full
# After any escape emission the stall method waits this long before emitting again.
# Stops the 20x PARTIAL_CLOSE_UNSUPPORTED warning spam observed 2026-04-24.
stall_escape_cooldown_seconds = 30
# After this many tighten_agg downgrades without PnL recovery of at least
# stall_recovery_threshold_pct from the worst-observed PnL, escalate to full_close.
stall_tighten_max_applications = 3
stall_recovery_threshold_pct = 0.15

# Logging / DB write throttle (Phase 10)
log_every_n_ticks = 6                   # M4_EVAL log every 30s (6 × 5s)
log_always_above_score = 50             # Always log if composite score >= this
sniper_log_write_every_n_ticks = 6      # Write to DB every 30s minimum

# Legacy classification thresholds — used by _classify_score() for M7 labels
score_watch = 30
score_consult_claude = 50
score_auto_partial = 70
score_auto_full = 85

# Legacy profit/immunity filters — used by _classify_score()
min_profit_pct = 0.8
min_profit_for_action = 0.10             # Min PnL% before Mode4 Phase 9 takes action
profit_immunity_seconds = 60
loss_immunity_seconds = 30
full_rules_after_seconds = 300

# Legacy cooldowns — used by is_in_cooldown() / _is_safe_to_execute()
cooldown_extreme_seconds = 300
cooldown_strong_seconds = 180
cooldown_medium_seconds = 120

# Legacy Claude settings — kept for _consult_claude() method
claude_timeout_seconds = 15
max_claude_queries_per_hour = 10
claude_hold_recheck_seconds = 30

# Legacy model weights (must total 100) — used for M7 counterfactual snapshot
weight_zscore = 25
weight_velocity = 25
weight_volume = 20
weight_bollinger = 15
weight_momentum = 15

# Legacy flash crash protection
flash_crash_auto_score = 70

# TRADE LIBERATION: Trail distance floors + activation threshold
min_trail_atr_multiplier = 1.5              # Min trail = 1.5 × ATR (noise floor)
min_trail_pct = 0.30                        # Min trail as % of entry price
min_profit_for_trail_pct = 0.30             # Min peak PnL% before trail activates
min_profit_decay = 0.50                     # Floor for profit_decay factor

# =============================================================================
# TIAS Phase 2 — DeepSeek Post-Trade Analysis via OpenRouter
# =============================================================================
[tias]
enabled = true
primary_model = "deepseek/deepseek-chat-v3-0324"
fallback_model = "deepseek/deepseek-chat"
temperature = 0.3
max_tokens = 1500
timeout_seconds = 45
max_retries = 1
analysis_version = 1

# =============================================================================
# APEX — Aggressive Profit Extraction & Exploitation (via OpenRouter)
# =============================================================================
[apex]
enabled = true
model = "deepseek/deepseek-v3.2"
fallback_model = "deepseek/deepseek-chat"
# Layer 3: was 30s; DeepSeek frequently responds at 30-32s causing APEX_TIMEOUT.
timeout_seconds = 60
max_tokens = 800
temperature = 0.2
max_position_size_usd = 1200
max_leverage = 5
min_tias_trades_for_optimization = 3
min_regime_trades_for_fallback = 10

# Guardrails
min_tp_pct = 0.3
gate_tp_floor_enabled = true
gate_trail_activation_floor_pct_of_tp = 15.0
gate_trail_distance_floor_pct = 40.0
gate_mode_override_enabled = true
gate_confidence_floor = 0.50
# Hard size-cap: APEX/conviction inflation cannot exceed 1.5× Claude's
# pre-APEX directive size. Gate CHECK 0 enforces this and logs
# CONVICTION_SIZE_CAP when it binds. Set 0 to disable.
gate_apex_size_cap_mult = 1.5

# Conviction Allocator
conviction_enabled = true
conviction_min_trades = 3

# Per-class TP cap multiplier (× recommended_tp_pct from volatility profiler).
# Applied after DeepSeek responds; clamps absurd TPs back to class-appropriate
# room. Dead coins cap near 1.2× base (~0.36-0.60%); extreme coins stretch to
# 1.5× (~7.5%). See optimizer.py APEX_TP_CAP log.
[apex.tp_cap_multiplier_by_class]
dead = 1.2
low = 1.3
medium = 1.3
high = 1.4
extreme = 1.5

# =============================================================================
# SENTINEL — Exit Firewall + Smart Deadline + Portfolio Advisor
# =============================================================================
[sentinel]
enabled = true

# Part 1: Exit Firewall — blocks strategic review from closing positions
firewall_enabled = true

# Part 2: Deadline Engine — tiered expiry logic based on PnL
deadline_profit_pct = 0.5
deadline_breakeven_lower_pct = -0.3
deadline_small_loss_pct = -1.5
deadline_grace_minutes = 5.0
deadline_small_loss_sl_pct = 0.5

# Part 3: Portfolio Advisor — DeepSeek V3 risk assessment every 5 min
advisor_enabled = true
advisor_interval_seconds = 300
advisor_model = "deepseek/deepseek-chat-v3-0324"
advisor_temperature = 0.2
advisor_max_tokens = 800
advisor_timeout_seconds = 30
# TRADE LIBERATION: Min profit before allowing stop tightening (%)
advisor_min_profit_for_tighten_pct = 0.50

# =============================================================================
# X-RAY — Structural Market Intelligence Engine
# =============================================================================
# Detects support/resistance, market structure (BOS/CHoCH), structural SL/TP.
# Runs as a background worker refreshing structural analysis per coin.

[analysis.structure]
enabled = true
worker_interval_seconds = 60
cache_ttl_seconds = 300
min_candles = 50
swing_lookbacks = [3, 5, 10]
cluster_pct = 0.3
min_touches = 2
max_levels_per_side = 5
ms_swing_lookback = 5
ms_min_swing_points = 3
sl_buffer_pct = 0.15
tp_buffer_pct = 0.10
min_rr_ratio = 2.0
sl_fallback_pct = 2.0
tp_fallback_pct = 4.0
# Phase 2: Smart Money Concepts
fvg_min_gap_pct = 0.1
fvg_max_age_candles = 50
ob_displacement_min = 0.6
ob_max_age_candles = 50
liq_equal_tolerance_pct = 0.05
liq_min_equal_count = 2
liq_round_number_step = 100.0
sweep_max_age_candles = 10
sweep_min_wick_pct = 0.3
# Phase 4: Intelligence
setup_scanner_mode = "supplement"
# Layer 1 universe alignment (Phase 6 cleanup): structure_worker now
# reads scanner.get_active_universe() directly — CoinDiscovery, the
# scan_full_market gate, and coin_refresh_interval are removed.
batch_size = 25
shadow_db_path = "../shadow/data/shadow.db"

# Layer 1 restructure Phase 2 — categorical setup classification
# thresholds. Conservative defaults; relax via Phase 9 observation.
[analysis.structure.setup_types]
fvg_ob_min_confluence = 0.7
structural_break_require_retest = true
sweep_min_displacement_pct = 0.5
range_breakout_min_compression_bars = 20
mtf_alignment_required = true

[analysis.volatility_profile]
enabled = true
# Phase 5 (P0-4 DB Contention): TTL 120 s + per-symbol jitter window of
# +/- 30 s spreads expirations across a full 60 s window. With 30 coins
# and uniform hash distribution that's ~1 expiration every 2 s instead
# of 30 in a single second. Eliminates the thundering-herd recompute
# storm that pegged WD_TICK_SLOW > 5 s.
cache_ttl_seconds = 120.0
jitter_range_seconds = 30
dead_threshold = 0.05
low_threshold = 0.15
medium_threshold = 0.40
high_threshold = 1.00
min_tp_pct = 0.30
min_sl_pct = 0.20
max_tp_pct = 8.0
max_sl_pct = 5.0

# =============================================================================
# Time-Decay Loser-Lane SL (5-model institutional exit intelligence)
# =============================================================================
# Runs inside PositionWatchdog only when pnl_pct < 0. Combined formula:
#   allowed = atr_room × time_factor × recovery × momentum × probability
#   allowed = max(allowed, min_allowed_loss_pct)  # 0.15% floor
#   allowed = min(allowed, original_sl_pct)       # never widen SL
# Force-closes when p_win < p_win_force_close. Propagates tighter-only SL
# via _push_sl_to_shadow (source="time_decay"). All scalar defaults come
# from TimeDecaySettings in settings.py; per-class overrides below.
[time_decay]
enabled = true

# Absolute-PnL-depth penalty on Bayesian p_win update. Catches slow bleeders
# whose tick-over-tick deepening stays <1 ATR (so the ATR-relative penalty
# never fires). At |pnl| > 1.5% a mild 0.90 multiplier applies per tick the
# loss deepens; at |pnl| > 3.0% a strong 0.70 multiplier applies.
p_win_abs_depth_threshold_pct = 1.5
p_win_abs_depth_strong_pct = 3.0
p_win_abs_depth_penalty = 0.90
p_win_abs_depth_strong_penalty = 0.70

# Per-class grace window (seconds before Time-Decay can act on a new loser).
# Slow bleeders (dead/low) act sooner — the whole point of the fix.
# Fast movers get more settling room so normal bar noise doesn't force exit.
[time_decay.grace_seconds_by_class]
dead = 30
low = 45
medium = 120
high = 180
extreme = 240

# Per-class ATR room multiplier (Model 2 — base atr_room = atr × mult).
# Dead coins stay tight (1.0×); extreme coins get 3.0× so huge-ATR swings
# don't trigger premature exits. `min_allowed_loss_pct = 0.15%` floor still
# applies post-combine so SL never goes below floor.
[time_decay.atr_room_multiplier_by_class]
dead = 1.0
low = 1.2
medium = 2.0
high = 2.5
extreme = 3.0


# ─── Layer 1 restructure Phase 1: observability ─────────────────────
# Standardized log-tag and cycle-tracker knobs. The defaults match
# blueprint Section 14 — 100-cycle in-memory history (≈8h at 5-min
# cadence), hourly flush to cycle_metrics, tick markers at INFO.
[observability]
cycle_tracker_history = 100
cycle_metrics_flush_seconds = 3600
log_tick_done_at_info = true

# ─── Phase 2 (post-Layer-1 fix): LayerManager safety knobs ────────────
# lm_attach_deadline_sec — hard deadline (seconds since OrderService
#   init) before the gate flips to fail-close for ALL purposes when
#   layer_manager is still None. Layer 4 close/SL normally bypass during
#   the bootstrap window so a watchdog close can still execute, but
#   exceeding the deadline implies attachment failure (LayerManager
#   never constructed) — at that point even Layer 4 cannot be allowed.
#   Default 60 s comfortably covers the observed boot ordering window
#   (≤ 5 s in production).
# state_sync_interval_sec — disk/memory layer state sync heartbeat
#   cadence. Every interval the LayerManager reads data/layer_state.json
#   and compares to layer_active in memory; a mismatch triggers a
#   recovery action (see [layer_manager.state_sync] below). Default 60 s
#   — fine for catching drift within one Strategy/Scanner cycle.
# state_sync.on_drift_action — Phase 11 (dead-workers fix). What the
#   heartbeat does when disk and memory disagree.
#     "rewrite_disk" (default, post-fix): memory wins. Re-persist
#       memory to disk; emit LAYER_STATE_DRIFT_RECOVERED. Correct
#       semantics — persist failures should be RECOVERED by re-
#       attempting persist, not by undoing the in-memory state.
#     "reload_memory" (legacy, pre-fix): disk wins. Overwrite memory
#       from disk; emit LAYER_STATE_DRIFT. This is the exact behaviour
#       that produced the Layer 3 toggle revert regression observed
#       on 2026-04-27 — only set this for emergency rollback.
[layer_manager]
lm_attach_deadline_sec = 60.0
state_sync_interval_sec = 60.0

[layer_manager.state_sync]
on_drift_action = "rewrite_disk"

# ── SignalGenerator multi-source classification (Phase 1 output-quality) ─────
# Pre-fix, _evaluate_signal() used sentiment as a HARD gate: every BUY/SELL
# rule required abs(sentiment) > 0.2. With sentiment=0.0 in 97.9% of coins
# (Reddit disabled, Finnhub no altcoin coverage, aggregator.py:165 zero-coverage
# rule), all signals fell through to NEUTRAL by design.
# Post-fix evaluator computes a weighted direction_score across 4 components
# (sentiment, F&G contrarian, funding rate, OI change). Each component is
# "active" only if abs(score) >= its min threshold; INACTIVE components are
# dropped (don't pull toward NEUTRAL). A coin with sentiment=0.0 but F&G=15
# and funding=-0.012 will now correctly classify BUY via F&G+funding alone.
[signal_generator.multi_source]
sentiment_min_active = 0.05
fg_min_active = 0.10
funding_min_active = 0.20
oi_min_active = 0.20
sentiment_weight = 0.40
fg_weight = 0.25
funding_weight = 0.20
oi_weight = 0.15
strong_threshold = 0.55
buy_threshold = 0.25
fg_normalize_range = 30.0
funding_normalize = 0.005
oi_normalize_pct = 5.0

# ── CoinPackage validator (Phase 5 output-quality) ──────────────────
# Validates each CoinPackage produced by ScannerWorker before it lands
# in layer_manager._coin_packages (which Stage 2 reads). Packages with
# completeness < fail_below are QUARANTINED (not included). warn_below
# packages still flow but are flagged in the per-package log.
# Score formula: (sum_required + 0.5*sum_optional) / (count_required +
# 0.5*count_optional). See src/core/coin_package_validator.py docstring
# for the full rule list.
[coin_package_validator]
fail_below = 0.50
warn_below = 0.85
staleness_fail_seconds = 300.0

# ── Worker liveness watchdog (Phase 11 dead-workers fix) ────────────
# Watchdog probes the per-worker liveness tracker every interval and
# emits WORKER_NEVER_TICKED / WORKER_TICK_OVERDUE warnings when a
# worker has registered but produced no first tick within the grace
# window, or has gone quiet for overdue_multiplier × expected_interval
# after its first tick. WORKER_LIVENESS_HEARTBEAT INFO log fires every
# tick regardless so workers.log has a continuous trail.
#
# Cycle-gate aware: cycle_gated workers (1B/1C/1D) that haven't ticked
# while LayerManager.is_cycle_active() is False are NOT alarmed —
# they're intentionally silent. Without this awareness the watchdog
# would false-alarm on the 5 cycle_gated workers every L3=OFF window.
[worker_liveness]
watchdog_interval_sec = 30
first_tick_grace_sec = 90
overdue_multiplier = 2.0
alert_rate_limit_sec = 3600
```

## End of file

Total sections present in config.toml:
- `[general]`, `[bybit]`, `[finnhub]`, `[reddit]`, `[altdata]`, `[database]`,
- `[workers]`, `[workers.sweet_spots]`, `[workers.sweet_spots.altdata]`,
- `[brain]`, `[risk]`, `[alerts]`, `[mcp]`, `[watchdog]`, `[mcp_pool]`, `[price]`,
- `[sl_gateway]`, `[sl_gateway.min_distance_class_ceiling]`,
- `[scanner]`, `[scanner.hysteresis]`, `[scanner.scoring_weights]`, `[scanner.qualitative]`,
- `[universe]`, `[regime]`, `[strategy_engine]`, `[pnl_targets]`, `[leverage]`,
- `[optimizer]`, `[factory]`, `[backtesting]`, `[trial]`, `[portfolio]`,
- `[telegram_interactive]`, `[fund_manager]`, `[enforcer]`, `[mode4]`,
- `[tias]`, `[apex]`, `[apex.tp_cap_multiplier_by_class]`,
- `[sentinel]`, `[analysis.structure]`, `[analysis.structure.setup_types]`,
- `[analysis.volatility_profile]`, `[time_decay]`, `[time_decay.grace_seconds_by_class]`,
- `[time_decay.atr_room_multiplier_by_class]`, `[observability]`,
- `[layer_manager]`, `[layer_manager.state_sync]`,
- `[signal_generator.multi_source]`, `[coin_package_validator]`, `[worker_liveness]`

There is NO `[news]`, `[stage2]`, or `[layer1c]` block in the file.


================================================================================
FILE: G2_hardcoded_thresholds.md
================================================================================

# G2 — Hardcoded Thresholds in Layer 1 Code

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Scope:** `src/workers/{price,kline,news,altdata,signal,regime,structure,strategy,scanner}_worker.py`, `src/strategies/{scanner,regime,scorer,ensemble}.py`, `src/analysis/structure/*.py`, `src/core/{coin_package_validator,freshness_guard}.py`
- **Method:** Module-level constants + inline numeric comparisons grepped via `^\s*_?[A-Z][A-Z0-9_]*\s*=\s*[0-9]`, `[<>]=?\s*[0-9.]+`, `min_/max_/threshold` substring; verified with file:line reads.

The `Config?` column flags whether the value should arguably live in `config.toml`. `Y` = belongs in config; `N` = legitimately hardcoded (algorithmic constant, structural minimum, data-shape invariant).

---

## Layer 1A — `src/workers/price_worker.py` (264 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| price_worker.py:186 | `if last_price <= 0` | `0` | reject non-positive WS quote (data-shape invariant) | N |
| price_worker.py:239 | `def get_ws_quote(..., max_age_s: float = 5.0)` | `5.0` s | default WS-quote freshness window for callers | Y |
| price_worker.py:255 | `if _time.monotonic() - ts > max_age_s` | param | freshness gate using parameter above | (n/a) |
| price_worker.py:257 | `return price if price > 0 else None` | `0` | reject non-positive quote | N |

No module-level constants in price_worker.py.

---

## Layer 1A — `src/workers/kline_worker.py` (494 lines)

Module-level constants (lines 32-50):
```
TIMEFRAME_SCHEDULE = {
    TimeFrame.M5: 60,
    TimeFrame.H1: 60,
    TimeFrame.H4: 300,
    TimeFrame.D1: 3600,
}
_KLINE_FRESHNESS_THRESHOLD_S = 600.0
_LAG_QUERY_MAX_SYMBOLS = 500
```

| File:line | Variable | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| kline_worker.py:33 | `TIMEFRAME_SCHEDULE[M5]` | `60` s | per-tick min interval — M5 fetched on every kline_worker tick | Y |
| kline_worker.py:34 | `TIMEFRAME_SCHEDULE[H1]` | `60` s | min interval between H1 fetches | Y |
| kline_worker.py:35 | `TIMEFRAME_SCHEDULE[H4]` | `300` s | min interval between H4 fetches | Y |
| kline_worker.py:36 | `TIMEFRAME_SCHEDULE[D1]` | `3600` s | min interval between D1 fetches | Y |
| kline_worker.py:44 | `_KLINE_FRESHNESS_THRESHOLD_S` | `600.0` | KLINE_FRESHNESS_WARN trigger after 2 missed M5 closes | Y |
| kline_worker.py:50 | `_LAG_QUERY_MAX_SYMBOLS` | `500` | SQLite IN-clause cap (algorithmic — `SQLITE_MAX_VARIABLE_NUMBER=999`) | N |
| kline_worker.py:140 | `if ratio < 0.5` | `0.5` | quality bucket: <50% expected klines → "critical" | Y |
| kline_worker.py:142 | `elif ratio < 0.9` | `0.9` | quality bucket: <90% → "warning" | Y |
| kline_worker.py:340 | `_M5_PERIOD_S = 300` | `300` s | M5 candle period (algorithmic constant) | N |
| kline_worker.py:341 | `_LAG_BUFFER_S = 60` | `60` s | tolerated lag past M5 close | Y |
| kline_worker.py:342 | `_LAG_THRESHOLD_S = _M5_PERIOD_S + _LAG_BUFFER_S` | `360` s | derived | (derived) |

---

## Layer 1A — `src/workers/altdata_worker.py` (274 lines)

Grep `^\s*[A-Z_][A-Z_0-9]*\s*=\s*[0-9]` returned nothing. Grep `[<>]=?\s*[0-9.]+|threshold|min_|max_` against the file body returned nothing. AltDataWorker pulls all cadences from `settings.workers.sweet_spots.altdata.*` and `settings.altdata.*`. **No hardcoded thresholds detected.**

---

## Layer 1A — `src/workers/news_worker.py` (not separately greped — Reddit gating handled in manager.py only)

Grep `^\s*[A-Z_][A-Z_0-9]*\s*=\s*[0-9]` returned nothing for news_worker.py.

---

## Layer 1A — `src/workers/price_worker.py`

Already covered above.

---

## Layer 1B — `src/workers/structure_worker.py` (368 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| structure_worker.py:127 | `if not candles or len(candles) < self.settings.structure.min_candles` | settings | min candles for structural analysis (already in config) | (already config) |
| structure_worker.py:219 | `_p50 = _sorted[max(0, int(0.50 * (_n - 1)))]` | `0.50` | percentile (algorithmic) | N |
| structure_worker.py:220 | `_p95 = _sorted[max(0, int(0.95 * (_n - 1)))]` | `0.95` | percentile (algorithmic) | N |
| structure_worker.py:283 | `if stats["cached_entries"] > 0` | `0` | guard for division (algorithmic) | N |
| structure_worker.py:354 | `if candles and len(candles) >= self.settings.structure.min_candles` | settings | already in config | (already config) |
| structure_worker.py:363 | `if candles and len(candles) >= self.settings.structure.min_candles` | settings | already in config | (already config) |

Module-level constants: none.

---

## Layer 1B — `src/workers/signal_worker.py` (178 lines)

Grep `^\s*[A-Z_][A-Z_0-9]*\s*=\s*[0-9]` returned nothing. Grep `[<>]=?\s*[0-9.]+` returned nothing. **No hardcoded thresholds detected.**

---

## Layer 1B — `src/workers/regime_worker.py` (313 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| regime_worker.py:235 | `if divergent > 0` | `0` | non-zero guard (algorithmic) | N |
| regime_worker.py:282 | `if self._cleanup_counter >= 100` | `100` | run cleanup every 100 ticks | Y |

Module-level constants: none.

---

## Layer 1C — `src/workers/strategy_worker.py` (1592 lines)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| strategy_worker.py:248 | `_kline_max_age_s = 300.0` | `300.0` s | STRAT_SKIP_STALE gate: skip TA on klines older than 5 min | Y |
| strategy_worker.py:275 | `if len(_stale_syms) < 5` | `5` | sample size for STRAT_SKIP_STALE_AGG log | N |
| strategy_worker.py:283 | `if len(klines) >= 50` | `50` | min bars for TA (algorithmic — TA libraries fail < 50) | N |
| strategy_worker.py:289 | `if _coin_ms > 200` | `200` ms | per-coin TA "slow" classification | Y |
| strategy_worker.py:339 | `if not klines_h1 or len(klines_h1) < 50` | `50` | min H1 bars for TA pre-population | N |
| strategy_worker.py:447 | `if _section_ms["prefetch"] > 5000` | `5000` ms | STRAT_PREFETCH_SLOW WARN | Y |
| strategy_worker.py:459 | `if _section_ms["prefetch"] > 8000` | `8000` ms | STRAT_PREFETCH_CRITICAL ERROR (24h shows 5 fires) | Y |
| strategy_worker.py:501 | `if _strat_ms > 2000` | `2000` ms | per-strategy slow warn | Y |
| strategy_worker.py:510 | `if _section_ms["l1"] > 5000` | `5000` ms | L1 slow warn | Y |
| strategy_worker.py:629 | `if _section_ms["l2"] > 2000` | `2000` ms | L2 slow warn | Y |
| strategy_worker.py:872 | `if _cycle_el > 30000` | `30000` ms | STRAT_TICK_SLOW threshold (>30s) | Y |
| strategy_worker.py:880 | `if len(self._tick_times) >= 10` | `10` | rolling window for tick timing stats | N |
| strategy_worker.py:1067 | `if _structural.setup_quality == "SKIP" and _rr is not None and _rr < 0.5` | `0.5` | RR-skip gate when structure marks SKIP | Y |
| strategy_worker.py:1099 | `if direction == "Buy" and _sp.rr_long < 1.0 and _sp.rr_short >= 2.0` | `1.0`, `2.0` | direction-flip detector: weak long but strong short | Y |
| strategy_worker.py:1105 | `elif direction == "Sell" and _sp.rr_short < 1.0 and _sp.rr_long >= 2.0` | `1.0`, `2.0` | direction-flip detector: weak short but strong long | Y |
| strategy_worker.py:1123 | `if _ratio > 5.0` | `5.0` | "block at >5×" RR-asymmetry block | Y |
| strategy_worker.py:1135 | `if _ratio > 3.0` | `3.0` | "size-reduce at >3×" RR-asymmetry warn | Y |
| strategy_worker.py:1238 | `if sz_mult < 1.0` | `1.0` | size multiplier guard | N |
| strategy_worker.py:1310-1320 | `sl >= current_price`, `tp <= current_price`, etc. | `0` | direction-consistent SL/TP guards (data-shape) | N |
| strategy_worker.py:1371 | `if qty <= 0` | `0` | reject non-positive qty | N |

Module-level constants: none — `_kline_max_age_s` etc. are function-local literals.

---

## Layer 1D — `src/workers/scanner_worker.py` (1090 lines)

Composite scoring component normalization constants (lines 187-200):
```
struct_norm = max(0.0, min(1.0, (struct_raw or 0.0) / 100.0))
strat_norm  = max(0.0, min(1.0, (strat_raw or 0.0) / 100.0))
sig_norm    = max(0.0, min(1.0, self._get_signal_confidence(coin) or 0.0))
regime_norm = (regime_align + 1.0) / 2.0
funding_norm = max(0.0, min(1.0, (funding_raw or 0.0) / 0.001))
```

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| scanner_worker.py:147 | `return 1.0` (regime trending) | `1.0` | regime alignment factor for trending_up/down | Y |
| scanner_worker.py:149 | `return 0.5` (regime volatile) | `0.5` | regime alignment factor for volatile | Y |
| scanner_worker.py:151 | `return 0.0` (regime ranging) | `0.0` | regime alignment factor for ranging | Y |
| scanner_worker.py:152 | `return -1.0` (regime dead/unknown) | `-1.0` | regime alignment factor for dead/unknown | Y |
| scanner_worker.py:187 | `(struct_raw or 0.0) / 100.0` | `100.0` | structure_score divisor (X-RAY 0-100 → 0-1) | N |
| scanner_worker.py:190 | `(strat_raw or 0.0) / 100.0` | `100.0` | strategy_score divisor (TradeScorer 0-100 → 0-1) | N |
| scanner_worker.py:200 | `(funding_raw or 0.0) / 0.001` | `0.001` (0.1%) | funding-rate saturation threshold for normalization | Y |
| scanner_worker.py:285 | `if rate > cfg.funding_blocker_threshold_pct` | settings | already in config (`scanner.qualitative.funding_blocker_threshold_pct = 0.001`) | (already config) |
| scanner_worker.py:289 | `elif rate < -cfg.funding_blocker_threshold_pct` | settings | mirror | (already config) |
| scanner_worker.py:432-433 | `"longs_paying" if rate > 0 else "shorts_paying" if rate < 0` | `0` | sign label (algorithmic) | N |
| scanner_worker.py:591 | `if rr < cfg.min_rr_ratio` | settings | already in config (`scanner.qualitative.min_rr_ratio = 2.0`) | (already config) |
| scanner_worker.py:778 | `n_max = int(cfg_q.max_selection)` | settings | already in config (`scanner.qualitative.max_selection = 15`) | (already config) |
| scanner_worker.py:779 | `n_min = int(cfg_q.min_selection)` | settings | already in config (`scanner.qualitative.min_selection = 0`) | (already config) |
| scanner_worker.py:839 | `_fail_below = float(getattr(_vld_cfg, "fail_below", 0.50)) if _vld_cfg else 0.50` | `0.50` (fallback) | validator fail cutoff fallback when settings missing (config has `coin_package_validator.fail_below = 0.50`) | (fallback) |
| scanner_worker.py:840 | `_warn_below ... 0.85` | `0.85` (fallback) | validator warn cutoff fallback (config has `coin_package_validator.warn_below = 0.85`) | (fallback) |
| scanner_worker.py:842-843 | `staleness_fail_seconds ... 300.0` | `300.0` s (fallback) | validator staleness fallback (config has `coin_package_validator.staleness_fail_seconds = 300.0`) | (fallback) |
| scanner_worker.py:939 | `_pct_or_unk(klines_ages, 0.50)` | `0.50` | percentile (algorithmic) | N |
| scanner_worker.py:940 | `_pct_or_unk(klines_ages, 0.95)` | `0.95` | percentile (algorithmic) | N |

Module-level constants: none.

---

## `src/strategies/scanner.py` (`MarketScanner`, NOT the worker)

Module-level: `CACHE_TTL_SECONDS = 300` (line 18).

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| scanner.py:18 | `CACHE_TTL_SECONDS = 300` | `300` s | TTL for the active-universe cache (already there is also `[scanner].scan_interval_seconds = 300`) | Y |
| scanner.py:188 | `if now_ts - t < 3600` | `3600` s | 1-hour cooldown registry purge window | Y |
| scanner.py:262 | `sorted_scored[max_coins - 1].get("score", 0)` | settings | uses settings.scanner.max_coins | (already config) |
| scanner.py:268 | `entry_floor = cutoff + hyst_cfg.entry_threshold_above_min` | settings | uses scanner.hysteresis.entry_threshold_above_min | (already config) |
| scanner.py:269 | `exit_ceiling = cutoff + hyst_cfg.exit_threshold_below_min` | settings | mirror | (already config) |
| scanner.py:424 | `if vol < 5_000_000` | `5_000_000` USDT | hardcoded volume reject (separate from `[scanner].min_volume_24h = 5000000` — duplicate constant!) | Y |
| scanner.py:426 | `if price < 0.0001` | `0.0001` | hardcoded micro-price reject | Y |
| scanner.py:433 | `if spread_pct > 0.5` | `0.5` % | hardcoded spread reject (separate from `[scanner].max_spread_pct = 0.15` — different number!) | Y |
| scanner.py:449 | `if change_abs >= 10` | `10` | tiered momentum bucket | Y |
| scanner.py:451 | `elif change_abs >= 5` | `5` | tier | Y |
| scanner.py:453 | `elif change_abs >= 3` | `3` | tier | Y |
| scanner.py:455 | `elif change_abs >= 1.5` | `1.5` | tier | Y |
| scanner.py:457 | `elif change_abs >= 0.8` | `0.8` | tier | Y |
| scanner.py:463 | `if daily_range_pct >= 8` | `8` | tiered daily-range bucket | Y |
| scanner.py:465 | `elif daily_range_pct >= 5` | `5` | tier | Y |
| scanner.py:467 | `elif daily_range_pct >= 3` | `3` | tier | Y |
| scanner.py:469 | `elif daily_range_pct >= 1.5` | `1.5` | tier | Y |
| scanner.py:475 | `if trend_ratio >= 0.6` | `0.6` | tiered trend-ratio bucket | Y |
| scanner.py:477 | `elif trend_ratio >= 0.4` | `0.4` | tier | Y |
| scanner.py:479 | `elif trend_ratio >= 0.25` | `0.25` | tier | Y |
| scanner.py:485 | `if vol >= 500_000_000` | `500_000_000` USDT | tiered volume bucket | Y |
| scanner.py:487 | `elif vol >= 100_000_000` | `100_000_000` | tier | Y |
| scanner.py:489 | `elif vol >= 50_000_000` | `50_000_000` | tier | Y |
| scanner.py:491 | `elif vol >= 20_000_000` | `20_000_000` | tier | Y |
| scanner.py:493 | `elif vol >= 5_000_000` | `5_000_000` | tier | Y |
| scanner.py:497 | `if spread_pct <= 0.02` | `0.02` % | tiered spread bucket | Y |
| scanner.py:499 | `elif spread_pct <= 0.05` | `0.05` % | tier | Y |
| scanner.py:501 | `elif spread_pct <= 0.10` | `0.10` % | tier | Y |

---

## `src/strategies/regime.py` (`RegimeDetector`)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| regime.py:80 | `if len(klines) < 50` | `50` | min bars for regime detection (algorithmic) | N |
| regime.py:122 | `if adx > cfg.trending_adx_threshold and ... and choppiness < 45` | `45` | choppiness inverse-cutoff hardcoded for trending classification (config has `trending_adx_threshold` but not the choppiness counter-threshold) | Y |
| regime.py:126 | `elif adx > cfg.trending_adx_threshold and ... and choppiness < 45` | `45` | mirror | Y |
| regime.py:130 | `elif atr_percentile > cfg.volatile_atr_percentile or volume_ratio > 2.0` | `2.0` | hardcoded volatile-volume multiplier | Y |
| regime.py:134 | `elif adx < cfg.ranging_adx_threshold and choppiness > cfg.ranging_choppiness_threshold` | settings | already in config | (already config) |
| regime.py:138 | `elif adx < cfg.dead_adx_threshold and volume_ratio < cfg.dead_volume_ratio and atr_percentile < 50` | `50` | hardcoded ATR-percentile counter-threshold for dead | Y |

---

## `src/strategies/scorer.py` (`TradeScorer`)

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| scorer.py:55 | `if total >= 70` | `70` | score-bucket cutoff | Y |
| scorer.py:58 | `if total >= 80` | `80` | quality label | Y |
| scorer.py:60 | `elif total >= 68` | `68` | quality label | Y |
| scorer.py:62 | `elif total >= 56` | `56` | quality label | Y |
| scorer.py:64 | `elif total >= 45` | `45` | quality label | Y |
| scorer.py:119 | `if strength > 0.8` | `0.8` | bucket | Y |
| scorer.py:121 | `elif strength > 0.6` | `0.6` | bucket | Y |
| scorer.py:123 | `elif strength > 0.4` | `0.4` | bucket | Y |
| scorer.py:190 | `if ta_conf > 0.6` | `0.6` | bucket | Y |
| scorer.py:200 | `(is_buy and sent_score > 0.2) or (not is_buy and sent_score < -0.2)` | `0.2` | sentiment direction gate | Y |
| scorer.py:208 | `if fg_val < 15` | `15` | F&G "extreme fear" bucket | Y |
| scorer.py:210 | `elif fg_val < 25` | `25` | bucket | Y |
| scorer.py:212 | `elif fg_val < 35` | `35` | bucket | Y |
| scorer.py:214 | `elif fg_val > 85` | `85` | "extreme greed" bucket | Y |
| scorer.py:216 | `elif fg_val > 75` | `75` | bucket | Y |
| scorer.py:218 | `elif fg_val > 65` | `65` | bucket | Y |
| scorer.py:231 | `(is_buy and fr < -0.01) or (not is_buy and fr > 0.01)` | `0.01` (1%) | funding-rate strong direction gate | Y |
| scorer.py:233 | `elif abs(fr) > 0.005` | `0.005` (0.5%) | funding-rate medium gate | Y |
| scorer.py:259 | `if vol_ratio and vol_ratio > 2.0` | `2.0` | volume surge bucket | Y |
| scorer.py:261 | `elif vol_ratio and vol_ratio > 1.3` | `1.3` | bucket | Y |
| scorer.py:279 | `if dist_pct < 1.0` | `1.0` % | "near support" gate | Y |

---

## `src/strategies/ensemble.py` (`EnsembleVoter`)

Module-local constant inside method (line 99):
```
CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
```
| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| ensemble.py:99 | `CONSENSUS_SIZE[STRONG]` | `1.0` | size multiplier per consensus tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[GOOD]` | `0.75` | tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[LEAN]` | `0.50` | tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[WEAK]` | `0.30` | tier | Y |
| ensemble.py:99 | `CONSENSUS_SIZE[CONFLICT]` | `0.15` | tier | Y |
| ensemble.py:101 | `if agreeing >= 4.0 and opposing <= 1.5` | `4.0`, `1.5` | STRONG-tier classification | Y |
| ensemble.py:103 | `elif agreeing >= cfg.min_ensemble_agreement and opposing <= cfg.max_ensemble_opposition` | settings | already in config (`strategy_engine.min_ensemble_agreement = 2.5`, `max_ensemble_opposition = 2.5`) | (already config) |
| ensemble.py:105 | `elif agreeing >= 1.5 and opposing <= 1.5` | `1.5`, `1.5` | LEAN/WEAK tier classification | Y |

---

## `src/analysis/structure/setup_scanner.py`

Module-level constants (lines 18-19):
```
MAX_SETUPS = 12
MIN_QUALIFYING_CRITERIA = 3
```

| File:line | Variable | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| setup_scanner.py:18 | `MAX_SETUPS` | `12` | max setups returned to caller (Claude) | Y |
| setup_scanner.py:19 | `MIN_QUALIFYING_CRITERIA` | `3` | must pass at least 3/6 criteria | Y |
| setup_scanner.py:113 | `qual["rr_adequate"] = sp.rr_ratio >= 2.0` | `2.0` | RR adequacy criterion (also `[analysis.structure].min_rr_ratio = 2.0`) | (duplicate) |
| setup_scanner.py:124 | `qual["confluence_good"] = mtf.score >= 5` | `5` | confluence score threshold | Y |
| setup_scanner.py:243 | `if sp.rr_ratio >= 4.0` | `4.0` | A+ setup classification | Y |
| setup_scanner.py:245 | `elif sp.rr_ratio >= 3.0` | `3.0` | A setup classification | Y |
| setup_scanner.py:247 | `elif sp.rr_ratio >= 2.0` | `2.0` | B setup classification | Y |

---

## `src/analysis/structure/fibonacci.py`

Module-level constants (lines 23-30):
```
RETRACE_RATIOS = {"0.236": 0.236, "0.382": 0.382, "0.500": 0.500, "0.618": 0.618, "0.786": 0.786}
EXTEND_RATIOS  = {"1.000": 1.000, "1.272": 1.272, "1.618": 1.618, "2.000": 2.000}
CONFLUENCE_TOLERANCE_PCT = 0.5
MIN_SWING_PCT = 2.0
```
All five values are mathematical Fibonacci constants except `CONFLUENCE_TOLERANCE_PCT = 0.5` and `MIN_SWING_PCT = 2.0` — both could move to config.

---

## `src/analysis/structure/fair_value_gap.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| fair_value_gap.py:25 | `if ratio >= 0.75` | `0.75` | FVG strength bucket | Y |
| fair_value_gap.py:27 | `elif ratio >= 0.5` | `0.5` | bucket | Y |
| fair_value_gap.py:70 | `if n < 10` | `10` | min candles for FVG scan (algorithmic) | N |
| fair_value_gap.py:211 | `partially = max_penetration > 0.3` | `0.3` | partial FVG mitigation threshold | Y |

---

## `src/analysis/structure/order_blocks.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| order_blocks.py:65 | `if n < 10` | `10` | min candles (algorithmic) | N |
| order_blocks.py:121 | `abs(f_idx - i) <= 2` | `2` | FVG-OB index proximity | Y |
| order_blocks.py:148 | `if body_ratio >= 0.75` | `0.75` | OB strength bucket | Y |
| order_blocks.py:150 | `elif body_ratio >= 0.5` | `0.5` | bucket | Y |
| order_blocks.py:174 | `if ob.strength_score >= 40.0 and ob.retests < 3` | `40.0`, `3` | strength + retests gating | Y |

---

## `src/analysis/structure/structural_levels.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| structural_levels.py:217 | `if rr >= 3.0` | `3.0` | quality A+ tier | Y |
| structural_levels.py:219 | `elif rr >= 2.0` | `2.0` | A tier (also config: `min_rr_ratio = 2.0`) | (duplicate) |
| structural_levels.py:221 | `elif rr >= 1.5` | `1.5` | B tier | Y |
| structural_levels.py:228 | `if position < 0.15` | `0.15` | position-in-range bucket | Y |
| structural_levels.py:230 | `elif position < 0.30` | `0.30` | bucket | Y |
| structural_levels.py:232 | `elif position <= 0.70` | `0.70` | bucket | Y |
| structural_levels.py:239 | `if position > 0.85` | `0.85` | bucket | Y |
| structural_levels.py:241 | `elif position > 0.70` | `0.70` | bucket | Y |
| structural_levels.py:243 | `elif position >= 0.30` | `0.30` | bucket | Y |

---

## `src/analysis/structure/liquidity.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| liquidity.py:28 | `if ratio >= 0.6` | `0.6` | strength bucket | Y |
| liquidity.py:30 | `elif ratio >= 0.35` | `0.35` | bucket | Y |
| liquidity.py:37 | `if rev_ratio >= 0.5 and depth_pct >= 0.1` | `0.5`, `0.1` | sweep validity | Y |
| liquidity.py:39 | `elif rev_ratio >= 0.3 or depth_pct >= 0.05` | `0.3`, `0.05` | sweep partial | Y |
| liquidity.py:188 | `if n < 5` | `5` | min candles (algorithmic) | N |

---

## `src/analysis/structure/market_structure.py`

| File:line | Variable / expression | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| market_structure.py:59 | `if n < 20` | `20` | min candles for MS detection | N |
| market_structure.py:75 | `if len(swing_highs) < 2 and len(swing_lows) < 2` | `2` | min swings (algorithmic) | N |
| market_structure.py:197 | `if dominant >= 4` | `4` | strength bucket | Y |
| market_structure.py:199 | `elif dominant >= 2` | `2` | bucket | Y |

---

## `src/analysis/structure/mtf_confluence.py`

(line 38-39 are docstring references, not code; `>= 40` and `>= 2.0` are documented as criteria but the actual code path was not exhaustively dumped.)

---

## `src/core/freshness_guard.py`

Module-level constants (lines 16-17):
```
MAX_TICKER_AGE = 120
MAX_KLINE_AGE = 300
```

| File:line | Variable | Current value | Apparent purpose | Config? |
|---|---|---|---|---|
| freshness_guard.py:16 | `MAX_TICKER_AGE` | `120` s | freshness gate for tickers | Y |
| freshness_guard.py:17 | `MAX_KLINE_AGE` | `300` s | freshness gate for klines (matches `[coin_package_validator].staleness_fail_seconds = 300.0`) | Y |

---

## `src/core/coin_package_validator.py`

Function-default fallbacks (line 75-77 of `validate_package`):
```
fail_below: float = 0.50
warn_below: float = 0.85
staleness_fail_seconds: float = 300.0
```
All three are also present in `[coin_package_validator]` section of config.toml — these are defensive fallbacks, not pure hardcodes.

Module-level constants used as verdict labels (lines 49-51):
```
VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_FAIL = "fail"
```
String enum values — not thresholds.

---

## Summary of "should-be-in-config" thresholds NOT currently in config.toml

The following appear to be tunable values still hardcoded (`Config? = Y`, no override path through settings):

1. **kline_worker.py: TIMEFRAME_SCHEDULE per-tf cooldowns** (M5=60, H1=60, H4=300, D1=3600 s)
2. **kline_worker.py:140/142:** quality bucket cutoffs (0.5, 0.9)
3. **kline_worker.py:341:** `_LAG_BUFFER_S = 60`
4. **kline_worker.py:44:** `_KLINE_FRESHNESS_THRESHOLD_S = 600.0`
5. **regime_worker.py:282:** cleanup-every-100-ticks
6. **strategy_worker.py:248:** `_kline_max_age_s = 300.0` (STRAT_SKIP_STALE)
7. **strategy_worker.py:289/447/459/501/510/629/872:** TA/prefetch/cycle slow-warn ms thresholds (200, 5000, 8000, 2000, 5000, 2000, 30000)
8. **strategy_worker.py:1067/1099/1105/1123/1135:** RR direction-flip and RR-asymmetry block/warn thresholds (0.5, 1.0, 2.0, 5.0, 3.0)
9. **scanner_worker.py:147-152:** regime-alignment factors (1.0, 0.5, 0.0, -1.0)
10. **scanner_worker.py:200:** funding normalize divisor (0.001)
11. **scanner.py (MarketScanner):18:** `CACHE_TTL_SECONDS = 300`
12. **scanner.py:188:** registry-purge cooldown 3600s
13. **scanner.py:424/426/433:** legacy hardcoded volume/price/spread rejects (5_000_000, 0.0001, 0.5%) — note divergence from config `[scanner].max_spread_pct = 0.15`
14. **scanner.py:449-501:** five tiered scoring buckets (change_abs / daily_range_pct / trend_ratio / vol / spread_pct), 25 hardcoded values
15. **regime.py:122/126:** `choppiness < 45` counter-thresholds
16. **regime.py:130:** `volume_ratio > 2.0` volatile multiplier
17. **regime.py:138:** `atr_percentile < 50` dead counter-threshold
18. **scorer.py:55-218:** 30+ hardcoded score-bucket / sentiment / F&G / funding / volume / position-distance thresholds
19. **ensemble.py:99/101/105:** consensus-tier size multipliers (1.0/0.75/0.5/0.3/0.15) and STRONG/LEAN agreement counts (4.0, 1.5)
20. **setup_scanner.py:18/19/124/243/245:** `MAX_SETUPS = 12`, `MIN_QUALIFYING_CRITERIA = 3`, `mtf.score >= 5`, `rr >= 4.0/3.0`
21. **fair_value_gap.py:25/27/211:** FVG strength buckets and partial-mitigation threshold
22. **order_blocks.py:121/148/150/174:** index proximity, body-ratio buckets, strength+retests gates
23. **structural_levels.py:217-243:** RR-tier and position-in-range buckets (9 values)
24. **liquidity.py:28/30/37/39:** sweep-strength tiers and validity gates
25. **market_structure.py:197/199:** dominant-swing-count buckets
26. **freshness_guard.py:16/17:** MAX_TICKER_AGE=120, MAX_KLINE_AGE=300 (latter duplicates `[coin_package_validator].staleness_fail_seconds`)
27. **fibonacci.py:27/30:** `CONFLUENCE_TOLERANCE_PCT = 0.5`, `MIN_SWING_PCT = 2.0`

## Already configurable (cited for completeness)

- `scanner_worker.py:285/289/591/778/779/839/840/842`: all read from `settings.scanner.qualitative` and `settings.coin_package_validator`.
- `regime.py:122/126/134`: read from `settings.regime`.
- `strategy_worker.py`: ensemble agreement/opposition from `settings.strategy_engine`.
- `structure_worker.py:127/354/363`: `settings.structure.min_candles`.

## Notes / divergences

- `[scanner].max_spread_pct = 0.15` (config) vs `scanner.py:433: spread_pct > 0.5` (hardcoded). Different number used in different paths.
- `[scanner].min_volume_24h = 5000000` (config) vs `scanner.py:424: vol < 5_000_000` (hardcoded). Same number in both, but two sources of truth.
- `[analysis.structure].min_rr_ratio = 2.0` (config) vs `setup_scanner.py:113: sp.rr_ratio >= 2.0` AND `structural_levels.py:219: elif rr >= 2.0` (both hardcoded). Three sources of truth for the same threshold.
- `[coin_package_validator].staleness_fail_seconds = 300.0` (config) vs `freshness_guard.MAX_KLINE_AGE = 300` (hardcoded). Two sources of truth.


================================================================================
FILE: G3_live_cache_snapshots.md
================================================================================

# G3 — Live Cache Snapshots (reconstructed from logs)

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC (file write)
- **Log read window:** 2026-04-27 22:05 - 23:12 UTC
- **Process state:** workers process restarted at 2026-04-27 22:53:35 UTC (line `4338` of current `workers.log`). The 22:05-22:26 cycles in the log come from the prior PID that exited at 22:45:52 (line `4248` `Worker 'scanner_worker' stopped (total_ticks=7, errors=0)`), and were captured while running.
- **Snapshot mechanism:** **NOT FOUND — searched** `src/workers/scanner_worker.py`, `src/workers/strategy_worker.py`, `src/workers/structure_worker.py`, `src/workers/regime_worker.py`, `src/workers/signal_worker.py`, `src/strategies/scanner.py`, `src/core/coin_package.py`, `src/server.py` — no in-process cache-dump endpoint or file-write mechanism for `_score_cache`, `_strategy_consensus`, `_signal_cache`, `_per_coin_regimes`, `_coin_packages`, `StructureCache`. The only available signal is the aggregate emitted by each worker on every tick (`STRAT_L4_HANDOFF`, `STRAT_CONSENSUS_SUMMARY`, `SIG_BATCH_STATS`, `XRAY_TICK_SUMMARY`, `REGIME_TICK_SUMMARY`, `PACKAGE_VALIDATE` per package, `SCANNER_PACKAGE_BUILD_DONE`, `CYCLE_FRESHNESS`). Reconstructed below from the latest log lines.

---

## A. `_score_cache` (StrategyWorker) — reconstructed from `STRAT_L4_HANDOFF` aggregates

Source: `src/workers/strategy_worker.py:819`

Logged shape: total entries only. Per-coin contents not in the aggregate log.

| Cycle (sid time) | score_cache_size | consensus_size | consensus_summary_size | hints_top20_size | handoff el_ms |
|---|---|---|---|---|---|
| 2026-04-27 22:06:33.372 | **16** | 16 | 7 | 7 | 2 |
| 2026-04-27 22:11:33.807 | **18** | 18 | 7 | 9 | 2 |
| 2026-04-27 22:16:38.778 | **18** | 18 | 7 | 9 | 8 |
| 2026-04-27 22:21:37.786 | **19** | 19 | 6 | 7 | 1 |
| 2026-04-27 22:26:39.110 | **19** | 19 | 7 | 7 | 1 |

Most recent: **19 of 50 watch_list coins have a score cache entry** at 22:26:39 UTC. Per-coin score values **NOT FOUND in logs** — only the count is emitted. The 50-coin target is not met; ~31/50 coins missing.

---

## B. `_strategy_consensus` — reconstructed from `STRAT_CONSENSUS_SUMMARY` and `STRAT_CONSENSUS_CHANGE`

Source: `src/workers/strategy_worker.py:743/755/771`

Most recent summary (cycle sid=s-1777328790019, 2026-04-27 22:26:39.109 UTC):
```
STRAT_CONSENSUS_WRITE | full_count=9 filtered_count=7 setups_in=10 cache_size_after=19 mode=NORMAL threshold=50
STRAT_CONSENSUS_SUMMARY | total=9 GOOD=5 STRONG=1 WEAK=3
```

Per-coin transitions logged this cycle (full coverage of changes only — coins whose consensus did not change are not re-logged):
- `BSBUSDT from=STRONG to=GOOD votes=38 score=0.75`
- `AAVEUSDT from=STRONG to=GOOD votes=38 score=0.75`
- `HYPERUSDT from=LEAN to=WEAK votes=38 score=0.30`

Prior cycle summary (22:21:37.786, sid=s-1777328490002):
```
STRAT_CONSENSUS_SUMMARY | total=10 GOOD=3 LEAN=1 STRONG=3 WEAK=3
```
Per-coin transitions in that cycle:
- `AAVEUSDT from=WEAK to=STRONG votes=38 score=1.00`
- `AEROUSDT from=WEAK to=GOOD votes=38 score=0.75`
- `HYPERUSDT from=WEAK to=LEAN votes=38 score=0.50`
- `DYDXUSDT from=NONE to=WEAK votes=38 score=0.30`

**Reconstructed snapshot at 2026-04-27 22:26:39 UTC:**

- Cache size: **19** entries (out of 50 watch_list target)
- Filtered (mode=NORMAL threshold=50): **7** entries above threshold; **9** entries above 0
- Distribution (most recent): `STRONG=1, GOOD=5, WEAK=3` (sums to 9; LEAN missing means LEAN=0 this cycle)
- Vote_count per coin: **38** (constant in all logs — number of registered strategies)
- `score` field: STRONG=1.00, GOOD=0.75, LEAN=0.50, WEAK=0.30 (per `ensemble.py:99` map, see G2)

Per-coin direction field: **NOT FOUND in logs.** Direction lives in the in-memory consensus dict but is not emitted.

`last_updated`: implicit — equals the `STRAT_CONSENSUS_WRITE` timestamp `2026-04-27 22:26:39.109`.

---

## C. `_strategy_consensus_summary`

Source: same `STRAT_CONSENSUS_SUMMARY` line above. The "summary" cache referenced by `STRAT_L4_HANDOFF.consensus_summary_size=7` matches the `filtered_count=7` count in the WRITE line — only coins with score above the mode=NORMAL threshold=50 land here. **7 entries at 22:26:39 UTC.**

---

## D. `_signal_cache` (SignalWorker) — reconstructed from `SIG_TICK_SUMMARY` + `SIG_BATCH_STATS`

Source: `src/workers/signal_worker.py:115/129/143/163`

Per-tick aggregate (most recent 5 ticks):

| Timestamp | universe | signals | mean_conf | conf_min | conf_max | conf_std | strongest |
|---|---|---|---|---|---|---|---|
| 22:06:01.889 | 50 | **50** | 0.25 | 0.203 | 0.429 | 0.054 | (n/a in line shown) |
| 22:11:00.940 | 50 | **50** | 0.24 | 0.203 | 0.343 | 0.048 | — |
| 22:16:01.296 | 50 | **50** | 0.27 | 0.203 | 0.344 | 0.058 | — |
| 22:21:00.537 | 50 | **50** | 0.22 | 0.203 | 0.344 | 0.035 | — |
| 22:26:03.374 | 50 | **50** | 0.21 | 0.203 | 0.335 | 0.025 | ORCAUSDT type=neutral conf=0.33 |

Per-coin sample lines from cycle ending 22:26:03.374:
```
22:26:02.549 Signal for FILUSDT: neutral (confidence: 0.20)
22:26:02.575 Signal for MNTUSDT: neutral (confidence: 0.20)
22:26:02.591 Signal for MONUSDT: neutral (confidence: 0.20)
22:26:02.849 Signal for SKRUSDT: neutral (confidence: 0.20)
22:26:02.868 Signal for PLUMEUSDT: neutral (confidence: 0.24)
22:26:02.887 Signal for EGLDUSDT: neutral (confidence: 0.20)
22:26:02.906 Signal for ALGOUSDT: neutral (confidence: 0.20)
22:26:03.062 Signal for BSBUSDT: neutral (confidence: 0.25)
22:26:03.078 Signal for KATUSDT: neutral (confidence: 0.20)
22:26:03.096 Signal for HYPERUSDT: neutral (confidence: 0.20)
22:26:03.114 Signal for ORCAUSDT: neutral (confidence: 0.33)
22:26:03.136 Signal for BLURUSDT: neutral (confidence: 0.20)
22:26:03.292 Signal for OPUSDT: neutral (confidence: 0.20)
22:26:03.313 Signal for APTUSDT: neutral (confidence: 0.20)
22:26:03.330 Signal for LTCUSDT: neutral (confidence: 0.24)
22:26:03.350 Signal for BCHUSDT: neutral (confidence: 0.20)
22:26:03.374 Signal for ALICEUSDT: neutral (confidence: 0.20)
```

Reconstructed snapshot at **2026-04-27 22:26:03 UTC**:
- 50 entries (full universe coverage)
- All 50 in the sample window classified `signal_type=neutral`
- Confidence range 0.203 - 0.335; mean 0.214; std 0.025
- Direction: **`neutral`** for all 50 coins this cycle (no buy/sell signals fired)
- Strongest: ORCAUSDT @ conf=0.335

---

## E. `_per_coin_regimes` (RegimeDetector) — reconstructed from `REGIME_TICK_SUMMARY`

Source: `src/workers/regime_worker.py:293`

Per-tick aggregate (most recent 5 ticks):

| Timestamp | universe | global | per_coin_size | el_ms |
|---|---|---|---|---|
| 22:06:19.059 | 50 | ranging | **49** | 4057 |
| 22:11:19.046 | 50 | ranging | **49** | 4044 |
| 22:16:24.883 | 50 | ranging | **49** | 9863 |
| 22:21:21.657 | 50 | ranging | **49** | 6655 |
| 22:26:24.807 | 50 | ranging | **49** | 9789 |

Most recent reading: 49 of 50 coins have a per-coin regime. Per-coin regime values and confidences **NOT FOUND in logs** — only the count is emitted; per-coin lines such as `REGIME_DETECTED | sym=...` were not present in the searched window. `last_updated` per coin: implicit ≈ `REGIME_TICK_SUMMARY` timestamp.

Brain prompt reads (sample reference for direction values, from `STRAT_DIRECTIVE` lines 14601 in brain.log): `ETHUSDT [TRENDING_DOWN 64%]`, `BSBUSDT [TRENDING_UP 99%]` — confirms the cache keys regime + confidence per coin even though the worker tick log emits only aggregates.

---

## F. `StructureCache` — reconstructed from `XRAY_TICK_SUMMARY` and `XRAY_CLASSIFY`

Source: `src/workers/structure_worker.py:268`, `src/workers/structure_worker.py:183`, `src/analysis/structure/setup_scanner.py:84`

### F.1 Per-tick aggregate

| Timestamp | universe | batch | symbols | analyzed | errors | cached | session | setups | skips | el_ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 22:10:45.614 | 50 | 0/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 16 | 613 |
| 22:15:45.821 | 50 | 1/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 13 | 819 |
| 22:20:45.581 | 50 | 0/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 18 | 579 |
| 22:25:47.329 | 50 | 1/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 13 | 2303 |

`cached=50` confirms the StructureCache has **50 entries** at 22:25:47.329 UTC. Each tick refreshes 25 (one of two batches).

### F.2 Setup-type distribution from XRAY_CLASSIFY (cycle ending 22:25:47)

Distribution from grep `setup_type=` between 22:25:00 and 22:26:00 (19 entries seen — partial; only 25 are refreshed per tick):
- `bearish_fvg_ob`: **18**
- `bullish_fvg_ob`: **1** (INJUSDT)
- `none`: **NOT FOUND in this window** (would emit `XRAY_CLASSIFY | sym=X setup_type=none confidence=...` per code at structure_worker.py:152-154)

Per-coin sample lines (cycle ending 22:25:47.329):

```
22:25:45.078 XRAY_CLASSIFY | sym=BTCUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:45.109 XRAY_CLASSIFY | sym=ETHUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.381 XRAY_CLASSIFY | sym=BNBUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.566 XRAY_CLASSIFY | sym=XRPUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.591 XRAY_CLASSIFY | sym=ADAUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=30  direction=short
22:25:45.659 XRAY_CLASSIFY | sym=DOGEUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:45.782 XRAY_CLASSIFY | sym=AVAXUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:45.805 XRAY_CLASSIFY | sym=LINKUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.866 XRAY_CLASSIFY | sym=ARBUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.887 XRAY_CLASSIFY | sym=NEARUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:46.088 XRAY_CLASSIFY | sym=ATOMUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:46.134 XRAY_CLASSIFY | sym=INJUSDT  setup_type=bullish_fvg_ob confidence=0.55 score=38  direction=long
22:25:46.392 XRAY_CLASSIFY | sym=RENDERUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64 direction=short
22:25:46.584 XRAY_CLASSIFY | sym=ONDOUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:46.660 XRAY_CLASSIFY | sym=PYTHUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:46.797 XRAY_CLASSIFY | sym=SEIUSDT  setup_type=bearish_fvg_ob confidence=0.70 score=100 direction=short
22:25:47.061 XRAY_CLASSIFY | sym=GALAUSDT setup_type=bearish_fvg_ob confidence=0.55 score=100 direction=short
22:25:47.089 XRAY_CLASSIFY | sym=MANAUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:47.111 XRAY_CLASSIFY | sym=SANDUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
```

### F.3 Setup-score distribution

From the 19-coin sample at 22:25:
- score=100: 2 (SEIUSDT, GALAUSDT)
- score=64: 6
- score=49: 9
- score=38: 1
- score=30: 1

### F.4 RR ratio + age

**NOT FOUND.** RR is not emitted in the XRAY_CLASSIFY line. The `[XRAY_SCANNER]` line emits top-3 by score but not RR per coin. Age in seconds **NOT FOUND** in any structure_worker log emission — only in scanner_worker's CYCLE_FRESHNESS aggregate (`xray_age_p50_ms=194995` at 22:24, `xray_age_p95_ms=494936`). Cache TTL is 300 s per `[analysis.structure].cache_ttl_seconds = 300` (config.toml:897).

### F.5 XRAY_SCANNER aggregate (most recent 4 cycles)

```
22:10:45.612 XRAY_SCANNER | total=28 qualified=26 skipped=16 | #1=DYDXUSDT(76) #2=LDOUSDT(73) #3=BLURUSDT(73)
22:15:45.820 XRAY_SCANNER | total=25 qualified=24 skipped=13 | #1=RUNEUSDT(73) #2=GALAUSDT(73) #3=LDOUSDT(73)
22:20:45.580 XRAY_SCANNER | total=30 qualified=28 skipped=18 | #1=DYDXUSDT(76) #2=GALAUSDT(73) #3=LDOUSDT(73)
22:25:47.326 XRAY_SCANNER | total=25 qualified=24 skipped=13 | #1=RUNEUSDT(73) #2=GALAUSDT(73) #3=LDOUSDT(73)
```

---

## G. `_coin_packages` (last cycle)

Source: `src/workers/scanner_worker.py:826/861/899/905`

### G.1 Most recent cycle (cycle_id=c-2026-04-27-22:20, tick at 22:24:00 UTC)

```
22:24:00.014 SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-04-27-22:20 total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1 fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0
22:24:00.016 SCANNER_PACKAGE_BUILD_START | cycle_id=c-2026-04-27-22:20 packages_to_build=2
22:24:00.017 PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
22:24:00.017 PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed'] stale=[]
22:24:00.018 SCANNER_PACKAGE_BUILD_DONE | cycle_id=c-2026-04-27-22:20 packages=2 total_size_bytes=1894 elapsed_ms=2
22:24:00.019 PACKAGE_VALIDATE_SUMMARY | cycle_id=c-2026-04-27-22:20 packages_built=2 ok=0 warn=2 fail_quarantined=0
22:24:00.020 CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20 klines_age_p50_ms=209932 klines_age_p95_ms=1704546 xray_age_p50_ms=194995 xray_age_p95_ms=494936 packages_age_p50_ms=1 packages_age_p95_ms=1 klines_keys=200 xray_keys=50 packages_keys=3
22:24:00.031 SCANNER_SELECT | cycle_id=c-2026-04-27-22:20 qualified=0 selected=2 forced=2 watch_list=50
22:24:00.031 SCANNER_TICK_SUMMARY | watch_list=50 protected=0 scored=2 selected=2 top_n=15 forced_in=2 mean_score=0.000 top=BTCUSDT(0.000) el=29ms drift_ms=1
```

Per-package field detail (`PACKAGE_VALIDATE` lines):
- **BTCUSDT:** completeness=**0.67** verdict=warn; missing fields = `price_data.current, xray.setup_type, price_data.regime, alt_data.fear_greed`; stale=[]
- **ETHUSDT:** completeness=**0.73** verdict=warn; missing fields = `price_data.current, xray.setup_type, alt_data.fear_greed`; stale=[]

Cycle-level totals: `packages_built=2 ok=0 warn=2 fail_quarantined=0`. **Both packages qualified=False (forced via open-position rule).**

### G.2 packages_keys=3

`packages_keys=3` in CYCLE_FRESHNESS but `packages=2` in BUILD_DONE. Three packages live in `_coin_packages`; one extra key is from a previous cycle that hasn't been replaced (typically `__stage1_summary__` or similar — exact key name **NOT FOUND** in this log window).

### G.3 Bucket / stale

`packages_age_p50_ms=1` and `packages_age_p95_ms=1` confirm packages are written fresh on every cycle.

---

## Summary of gaps

| Cache | Snapshot mechanism | What's logged | What's missing |
|---|---|---|---|
| `_score_cache` | NOT FOUND — only aggregate count | size, e.g. 19 | per-coin scores, last_write timestamp per coin |
| `_strategy_consensus` | NOT FOUND — only aggregate + transitions | size, distribution counts | per-coin (consensus, score, vote_count, direction, last_updated) for stable rows |
| `_strategy_consensus_summary` | NOT FOUND — only count | filtered_count, threshold | full filtered set |
| `_signal_cache` | per-coin per-cycle log lines | full coverage available | none significant |
| `_per_coin_regimes` | NOT FOUND — only aggregate | size only | per-coin regime, confidence, last_updated |
| `StructureCache` | per-coin XRAY_CLASSIFY lines | setup_type, confidence, score, direction per coin | RR, age, structural_levels.suggested_sl/tp |
| `_coin_packages` | per-package PACKAGE_VALIDATE | completeness, missing/stale, qualified | inner field values (current price, regime string, etc.) |


================================================================================
FILE: G4_scanner_cycles.md
================================================================================

# G4 — Last 7 ScannerWorker Cycles

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Sources:** `data/logs/workers.log` (current), `data/logs/workers.2026-04-27_01-31-00_169356.log` (rotated, contains pre-22:53 ticks). The current `workers.log` was rotated/started at 22:06:01 UTC after a worker restart so cycles 21:50-22:00 come from the rotated file.
- **Cycles selected:** the 7 most recent cycles ending at scanner sweet-spot 4:00 within each 5-minute window where SCANNER_FILTER_AGGREGATE was emitted (i.e., cycle ran rather than skipped). Process restart at 22:53:35 means no further qualifying cycle ran within the capture window (the 22:29, 22:34, 22:39, 22:44, 23:04, 23:14 ticks all logged `LAYER1D_TICK_SKIP | reason=cycle_inactive`).
- **Per-cycle log tags grepped:** `SCANNER_FILTER_AGGREGATE`, `SCANNER_PACKAGE_BUILD_START`, `PACKAGE_VALIDATE`, `SCANNER_PACKAGE_BUILD_DONE`, `PACKAGE_VALIDATE_SUMMARY`, `CYCLE_FRESHNESS`, `SCANNER_SELECT`, `SCANNER_TICK_SUMMARY`, `LAYER1D_TICK_DONE`.

---

## Cycle 1 — `c-2026-04-27-21:50` (tick 21:54:00 UTC)

**Source:** `workers.2026-04-27_01-31-00_169356.log:57687-57699`

Per-coin qualification (aggregate only):
- `total=50 qualified=0 fail_no_xray=50 fail_setup_none=0 fail_consensus=0 fail_regime=0 fail_rr=0 fail_blockers=0 pass_xray=0 pass_consensus_strong=0 pass_consensus_good=0`
- **Why:** `fail_no_xray=50` — every coin failed the X-RAY presence check. StructureCache had `xray_keys=0` per the CYCLE_FRESHNESS line (`xray_age_p50_ms=unknown`).

Bucket counts: see qualification line — single bucket (`fail_no_xray=50`).

Selection result:
```
SCANNER_SELECT | qualified=0 selected=2 forced=2 watch_list=50
```
- 0 qualified, 2 forced (BTCUSDT + ETHUSDT — both held open positions).

Package validation:
```
PACKAGE_VALIDATE | sym=BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
PACKAGE_VALIDATE | sym=ETHUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0
```

Cycle freshness: `klines_keys=0 xray_keys=0 packages_keys=3` (cold caches — kline_worker had not yet ticked).

Total elapsed (LAYER1D_TICK_DONE line 57699): `elapsed_ms=66 drift_ms=1`. SCANNER_TICK_SUMMARY: `el=64ms`.

---

## Cycle 2 — `c-2026-04-27-21:55` (tick 21:59:00 UTC)

**Source:** `workers.2026-04-27_01-31-00_169356.log:58658-58669`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=5 fail_consensus=19 fail_regime=0 fail_rr=1 fail_blockers=0 pass_xray=20 pass_consensus_strong=0 pass_consensus_good=1`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages built: 2
- BTCUSDT completeness=0.89 verdict=ok missing=['price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.94 verdict=ok missing=['alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=2 warn=0 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=190410 xray_age_p50_ms=194691 packages_age_p50_ms=0 klines_keys=200 xray_keys=25 packages_keys=3`.

Total elapsed: `elapsed_ms=19 drift_ms=1` (LAYER1D_TICK_DONE), `el=18ms` (SCANNER_TICK_SUMMARY). `mean_score=0.000 top=BTCUSDT(0.000)`.

---

## Cycle 3 — `c-2026-04-27-22:00` (tick 22:04:00 UTC)

**Source:** `workers.2026-04-27_01-31-00_169356.log:59446-59457`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=13 fail_regime=0 fail_rr=2 fail_blockers=0 pass_xray=15 pass_consensus_strong=2 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0`

Cycle freshness: `klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=22 drift_ms=1`, `mean_score=0.027 top=BTCUSDT(0.055) protected=1`.

---

## Cycle 4 — `c-2026-04-27-22:05` (tick 22:09:00 UTC)

**Source:** `workers.log:369-381`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=6 fail_consensus=15 fail_regime=1 fail_rr=3 fail_blockers=0 pass_xray=19 pass_consensus_strong=4 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.89 verdict=ok missing=['price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.94 verdict=ok missing=['alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=2 warn=0 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=209947 xray_age_p50_ms=194959 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=31 drift_ms=2`, `mean_score=0.123 top=BTCUSDT(0.247) protected=1`. PACKAGE size: `total_size_bytes=1977`.

---

## Cycle 5 — `c-2026-04-27-22:10` (tick 22:14:00 UTC)

**Source:** `workers.log:1252-1263`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1 fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']
- BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=202235 xray_age_p50_ms=194966 packages_age_p50_ms=0 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=20 drift_ms=1`, `mean_score=0.196 top=ETHUSDT(0.392) protected=1`. Size: `total_size_bytes=1876`.

---

## Cycle 6 — `c-2026-04-27-22:15` (tick 22:19:00 UTC)

**Source:** `workers.log:2090-2101`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=6 fail_consensus=14 fail_regime=2 fail_rr=3 fail_blockers=0 pass_xray=19 pass_consensus_strong=4 pass_consensus_good=1`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.89 verdict=ok missing=['price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.94 verdict=ok missing=['alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=2 warn=0 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=201980 xray_age_p50_ms=194985 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=22 drift_ms=3`, `mean_score=0.000 top=BTCUSDT(0.000) protected=0`. Size: `total_size_bytes=1956`.

---

## Cycle 7 — `c-2026-04-27-22:20` (tick 22:24:00 UTC)

**Source:** `workers.log:2799-2810`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1 fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=209932 xray_age_p50_ms=194995 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=32 drift_ms=1`, `mean_score=0.000 top=BTCUSDT(0.000) protected=0`. Size: `total_size_bytes=1894`.

---

## After 22:24

The next 5 scheduled scanner sweet-spots (22:29, 22:34, 22:39, 22:44, **22:53 process restart**, 23:04, 23:14) all logged:
```
LAYER1D_TICK_SKIP | sub=scanner_worker reason=cycle_inactive drift_ms=N rate_limited=true
```
The L3 cycle gate flipped to inactive (LayerManager.is_cycle_active()=False) after 22:24. Per `WORKER_LIVENESS_HEARTBEAT` lines, `cycle_active=False` was first observed at 2026-04-27 22:27:59.775 (workers.log:3655). No SCANNER_FILTER_AGGREGATE cycles ran past 22:24 within the capture window.

---

## Cross-cycle aggregate table

| # | Cycle | Tick UTC | total | qualified | fail_no_xray | fail_setup_none | fail_consensus | fail_regime | fail_rr | fail_blockers | pass_xray | pass_str | pass_good | selected | forced | el_ms | top |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | c-21:50 | 21:54:00 | 50 | 0 | 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 | 66 | BTCUSDT(0.000) |
| 2 | c-21:55 | 21:59:00 | 50 | 0 | 25 | 5 | 19 | 0 | 1 | 0 | 20 | 0 | 1 | 2 | 2 | 19 | BTCUSDT(0.000) |
| 3 | c-22:00 | 22:04:00 | 50 | 0 | 25 | 10 | 13 | 0 | 2 | 0 | 15 | 2 | 0 | 2 | 2 | 22 | BTCUSDT(0.055) |
| 4 | c-22:05 | 22:09:00 | 50 | 0 | 25 | 6 | 15 | 1 | 3 | 0 | 19 | 4 | 0 | 2 | 2 | 31 | BTCUSDT(0.247) |
| 5 | c-22:10 | 22:14:00 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 | 2 | 2 | 20 | ETHUSDT(0.392) |
| 6 | c-22:15 | 22:19:00 | 50 | 0 | 25 | 6 | 14 | 2 | 3 | 0 | 19 | 4 | 1 | 2 | 2 | 22 | BTCUSDT(0.000) |
| 7 | c-22:20 | 22:24:00 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 | 2 | 2 | 32 | BTCUSDT(0.000) |

Across all 7 cycles: `qualified=0` every cycle. Selection mechanism: forced packages (BTCUSDT + ETHUSDT — both held open positions) for all 7. No coin ever passed the qualitative checklist (Phase 5 gate: STRONG/GOOD consensus + RR≥2.0 + regime alignment + no blockers).

Per-cycle WHY summary:
- **Cycle 1 (21:50):** 100% fail_no_xray — StructureCache empty (kline_worker hadn't ticked yet).
- **Cycles 2-7:** ~25/50 fail_no_xray (the structure_worker batches half the universe per tick — see G3.F where `cached=50` but each tick analyzes only `symbols=25`); 5-10 fail_setup_none; 12-19 fail_consensus; 0-3 fail_regime; 1-3 fail_rr; **0 blockers** in any cycle. `pass_consensus_strong` ranges 0-4 per cycle; `pass_consensus_good` ranges 0-1.


================================================================================
FILE: G5_brain_cycles.md
================================================================================

# G5 — Last 5 Brain CALL_A Invocations

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Sources:** `data/logs/brain.log` (ERR/INFO emissions from `src.brain.strategist`), `data/logs/general.log` (cross-reference for ALERT_SENT under same `did=`).
- **Per-cycle log tags:** `STRAT_CALL_A_START`, `STRATEGIST_PACKAGES_READ`, `STRAT_CALL_A_CTX`, `PROMPT_BUILD_DONE`, `STRAT_CALL_A`, `STRAT_CALL_A_PLAN`, `STRAT_DIRECTIVE`, `STRAT_CALL_A_NO_TRADES`, `STRAT_CALL_A_END`, `ALERT_SENT`.
- **NOT FOUND** — searched for `BRAIN_DECISION`, `BRAIN_DO_START`, `BRAIN_DO_END`, `BRAIN_TRADE_HALT`, `TRADE_EXEC`, `EXEC_PLACED`, `EXEC_BLOCKED`, `Claude trade failed`, `_execute_new_trades` in `brain.log` and `general.log`. No execution-result line was emitted in the 5 captured cycles. The closest signal of execution is the `ALERT_SENT | level=info` line under the same `did=` (telegram alert published).

---

## CALL_A #1 — `did=d-1777326781241` (2026-04-27 21:53:01 UTC)

```
21:53:01.241 STRAT_CALL_A_START | did=d-1777326781241
21:53:06.047 STRATEGIST_PACKAGES_READ | call=CALL_A count=0 age_min_s=0 age_max_s=0 reader=brain_call_a
21:53:06.155 STRAT_CALL_A_CTX | sections=22 chars=2716 el=4913ms
21:53:06.155 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=2737 sections=22 packages=0 elapsed_ms=4913
21:53:06.155 STRAT_CALL_A | chars=2737
21:55:18.370 STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Both tradeable coins showing clear bearish momentum with RSI mid-30s and strongl'
21:55:18.373 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='ETH bearish momentum: RSI=35 falling, MACD_hist=-3.91 strongly negative, ADX=32'
21:55:18.374 STRAT_DIRECTIVE | #2 sym=BTCUSDT dir=Sell lev=3 rsn='BTC bearish: RSI=36, MACD_hist=-117.84 deeply negative, -1.3% 24h. ADX=25 weaker'
21:55:18.374 STRAT_CALL_A_END | el=137133ms trades=2
21:55:19.120 ALERT_SENT | level=info len=652 (general.log:58539)
21:55:33.398 ALERT_SENT | level=info len=344
21:55:34.032 ALERT_SENT | level=info len=348
```

Reconstructed details:
- **Packages received:** 0 (count=0 — fell back to legacy path; coins=2 came from forced/legacy code)
- **Per-package completeness + key fields:** N/A — no packages
- **Prompt size:** 2737 bytes; 22 sections; build elapsed 4913 ms; chars=2716 in CTX
- **Decision:** `trades=2`, risk=`cautious`, action: 2 Sell directives (ETHUSDT lev=3, BTCUSDT lev=3)
- **Execution result:** NOT FOUND in logs — only ALERT_SENT lines (3) under same did
- **CALL_A_END elapsed:** 137133 ms (137 s)

---

## CALL_A #2 — `did=d-1777327303869` (2026-04-27 22:01:43 UTC)

```
22:01:43.869 STRAT_CALL_A_START | did=d-1777327303869
22:01:43.871 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=163 age_max_s=163 reader=brain_call_a
22:01:44.310 STRAT_CALL_A_CTX | sections=49 chars=6946 el=440ms
22:01:44.310 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6994 sections=49 packages=2 elapsed_ms=440
22:01:44.310 STRAT_CALL_A | chars=6994
22:03:28.093 STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Both BTC and ETH in strong bearish momentum approaching 24h lows. Late NY dead z'
22:03:28.093 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='TRENDING_DOWN regime (64% conf), ADX=32 confirms trend strength, RSI=35 in downt'
22:03:28.093 STRAT_DIRECTIVE | #2 sym=BTCUSDT dir=Sell lev=3 rsn='Global ranging but strong bearish momentum -2.12% 24h, RSI=34 declining, MACD de'
22:03:28.094 STRAT_CALL_A_END | el=104224ms trades=2
22:03:28.828 ALERT_SENT | level=info len=652 (general.log:58547)
22:03:43.566 ALERT_SENT | level=info len=344
22:03:44.215 ALERT_SENT | level=info len=346
```

- **Packages received:** 2; both packages aged 163 s (read from `_coin_packages` populated at scanner cycle c-21:55, tick 21:59:00 — matches age 163s back from 22:01:43)
- **Per-package completeness:** packages built at scanner cycle c-21:55 — BTCUSDT=0.89 ok, ETHUSDT=0.94 ok (per G4 cycle 2)
- **Prompt size:** 6994 bytes; 49 sections; build el=440 ms
- **Decision:** trades=2; ETHUSDT Sell lev=3; BTCUSDT Sell lev=3
- **Execution result:** NOT FOUND — 3 ALERT_SENT under same did
- **CALL_A_END elapsed:** 104224 ms (104 s)

---

## CALL_A #3 — `did=d-1777327727920` (2026-04-27 22:08:47 UTC)

```
22:08:47.920 STRAT_CALL_A_START | did=d-1777327727920
22:08:47.922 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=287 age_max_s=287 reader=brain_call_a
22:08:48.567 STRAT_CALL_A_CTX | sections=41 chars=6517 el=647ms
22:08:48.568 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6557 sections=41 packages=2 elapsed_ms=647
22:08:48.568 STRAT_CALL_A | chars=6557
22:10:26.000 STRAT_CALL_A_PLAN | trades=1 risk=cautious view='Extremely limited opportunity set — only 2 coins tradeable and BTC already has p'
22:10:26.001 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='ETHUSDT [TRENDING_DOWN 64%] — trading WITH per-coin regime. RSI=35 in downtrend'
22:10:26.001 STRAT_CALL_A_END | el=98081ms trades=1
22:10:26.725 ALERT_SENT | level=info len=666 (general.log:58551)
22:10:37.223 ALERT_SENT | level=info len=344
```

- **Packages received:** 2; both packages aged 287 s (= 4m47s — the package was built at scanner cycle c-22:00 tick 22:04:00, 287s before 22:08:47)
- **Per-package completeness:** scanner cycle c-22:00 had BTCUSDT=0.67 warn, ETHUSDT=0.73 warn (per G4 cycle 3)
- **Prompt size:** 6557 bytes; 41 sections; build el=647 ms
- **Decision:** trades=**1**; ETHUSDT Sell lev=3 (BTC dropped — `BTC already has position`)
- **Execution result:** NOT FOUND — 2 ALERT_SENT under same did
- **CALL_A_END elapsed:** 98081 ms (98 s)

---

## CALL_A #4 — `did=d-1777328223516` (2026-04-27 22:17:03 UTC)

```
22:17:03.516 STRAT_CALL_A_START | did=d-1777328223516
22:17:03.520 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=183 age_max_s=183 reader=brain_call_a
22:17:04.080 STRAT_CALL_A_CTX | sections=44 chars=6973 el=564ms
22:17:04.080 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=7016 sections=44 packages=2 elapsed_ms=564
22:17:04.081 STRAT_CALL_A | chars=7016
22:18:22.829 STRAT_CALL_A_PLAN | trades=0 risk=cautious view='Only 2 coins available (BTCUSDT, ETHUSDT). ETHUSDT already has a position being '
22:18:22.829 [WARNING] STRAT_CALL_A_NO_TRADES | view='Only 2 coins available (BTCUSDT, ETHUSDT). ETHUSDT already has a position being managed by watchdog '
22:18:22.833 STRAT_CALL_A_END | el=79317ms trades=0
22:18:23.206 ALERT_SENT | level=info len=644 (general.log:58559)
```

- **Packages received:** 2; aged 183 s (= 3m03s — package built at scanner cycle c-22:10 tick 22:14:00, 183 s before 22:17:03)
- **Per-package completeness:** scanner cycle c-22:10 had BTCUSDT=0.67 warn, ETHUSDT=0.73 warn (per G4 cycle 5)
- **Prompt size:** 7016 bytes; 44 sections; build el=564 ms
- **Decision:** trades=**0** (`STRAT_CALL_A_NO_TRADES` WARNING)
- **Execution result:** N/A — no trades planned
- **CALL_A_END elapsed:** 79317 ms (79 s)

---

## CALL_A #5 — `did=d-1777328602866` (2026-04-27 22:23:22 UTC, MOST RECENT)

```
22:23:22.866 STRAT_CALL_A_START | did=d-1777328602866
22:23:23.019 STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=263 age_max_s=263 reader=brain_call_a
22:23:24.085 STRAT_CALL_A_CTX | sections=40 chars=6529 el=1207ms
22:23:24.085 PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6568 sections=40 packages=2 elapsed_ms=1207
22:23:24.119 STRAT_CALL_A | chars=6568
22:25:16.717 STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Late NY dead zone, both BTC and ETH sold off hard today (-2.3% and -3.5%). Low v'
22:25:16.717 STRAT_DIRECTIVE | #1 sym=ETHUSDT dir=Sell lev=3 rsn='TRENDING_DOWN 64% per-coin regime. Score 68 STRONG ensemble, short signal. RSI=3'
22:25:16.717 STRAT_DIRECTIVE | #2 sym=BTCUSDT dir=Buy lev=2 rsn='Global ranging regime default. RSI=34 oversold = mean-reversion buy opportunity'
22:25:16.717 STRAT_CALL_A_END | el=113851ms trades=2
22:25:17.459 ALERT_SENT | level=info len=652 (general.log:58560)
22:25:34.893 ALERT_SENT | level=info len=344
22:25:35.537 ALERT_SENT | level=info len=346
```

- **Packages received:** 2; aged 263 s (= 4m23s — package built at scanner cycle c-22:15 tick 22:19:00, 263s before 22:23:22)
- **Per-package completeness:** scanner cycle c-22:15 had BTCUSDT=0.89 ok, ETHUSDT=0.94 ok (per G4 cycle 6)
- **Prompt size:** 6568 bytes; 40 sections; build el=1207 ms
- **Decision:** trades=2; ETHUSDT Sell lev=3, BTCUSDT **Buy** lev=2 (note opposite directions in same plan)
- **Execution result:** NOT FOUND — 3 ALERT_SENT under same did
- **CALL_A_END elapsed:** 113851 ms (114 s)

---

## Cross-cycle aggregate

| # | did time | Packages | Pkg age (s) | Sections | Prompt bytes | Build ms | trades | symbols/dirs | CALL_A end ms |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 21:53:01 | 0 | 0 | 22 | 2737 | 4913 | 2 | ETHUSDT/Sell, BTCUSDT/Sell | 137133 |
| 2 | 22:01:43 | 2 | 163 | 49 | 6994 | 440 | 2 | ETHUSDT/Sell, BTCUSDT/Sell | 104224 |
| 3 | 22:08:47 | 2 | 287 | 41 | 6557 | 647 | 1 | ETHUSDT/Sell | 98081 |
| 4 | 22:17:03 | 2 | 183 | 44 | 7016 | 564 | 0 | (no_trades) | 79317 |
| 5 | 22:23:22 | 2 | 263 | 40 | 6568 | 1207 | 2 | ETHUSDT/Sell, BTCUSDT/Buy | 113851 |

Notes:
- Across all 5 cycles, the strategist received **either 0 or 2 packages**; never more.
- The 2 packages are always BTCUSDT + ETHUSDT (forced by open-position rule per G4 — qualified=0 every cycle).
- Package age at CALL_A start ranges 163-287s (= 2m43s - 4m47s). Scanner sweet-spot is 4:00 in the 5-min window; CALL_A fires every 150s (see config.toml:163 `strategic_interval = 150`); the offset between scanner write and brain read varies per cycle.
- `chars` vs `size_bytes` in PROMPT_BUILD_DONE: bytes is slightly larger (UTF-8 encoding, e.g., 6994 vs 6946). Prompt grew from 2737 bytes (no packages) to 6994 bytes when packages started flowing — +4257 bytes added by the 2 packages combined.
- `sections` count: 22 baseline (no packages) → 40-49 with 2 packages.
- **Execution outcome:** No EXEC_OK / EXEC_FAIL / TRADE_PLACED / Claude trade failed lines for any of these 5 dids in `brain.log` or `general.log`. Only ALERT_SENT (level=info) telegram messages under the same did. Whether the trades actually placed cannot be confirmed from the available log set — the relevant emission either lives in a log not in `data/logs/` or is not emitted at all under these dids.


================================================================================
FILE: G6_errors_24h.md
================================================================================

# G6 — ERROR / CRITICAL events in last 24h

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Window:** 2026-04-26 23:00:00 → 2026-04-27 23:00:00 UTC (24h)
- **Sources:**
  - `data/logs/workers.log` (current — covers 22:06 → 23:12 from new PID, then split by 22:53 restart)
  - `data/logs/workers.2026-04-27_01-31-00_169356.log` (prior PID — covers 01:31 → 22:06)
  - `data/logs/brain.log` (continuous; cumulative ERRs back to 2026-04-13)
  - `data/logs/general.log` (continuous; cumulative ERRs back to 2026-04-23)
- **Method:** `awk '/^2026-04-26 23:|^2026-04-27 / && /( ERROR | CRITICAL )/' <files>` then aggregate by event tag.
- **Total raw lines in window:** 79 (71 ERROR + 8 CRITICAL)

---

## Aggregate by event tag (24h)

| Count | Tag | Source file:line (sample location) |
|---|---|---|
| 20 | `ORDER_GATE_LM_DEADLINE_EXCEEDED` | `src.trading.services.order_service:_enforce_layer3_gate:240` |
| 20 | `ORDER_BLOCKED` | `src.trading.services.order_service:_emit_order_blocked:182` |
| 11 | `DB_PROTECT_BLOCKED` | `src.database.protected_tables:assert_not_protected_destructive:135` |
| 8 | `WORKER_SHUTDOWN` (CRITICAL) | `workers:_sync_emit` and `__main__:_atexit_log:82` |
| 7 | `STRAT_PREFETCH_CRITICAL` | `src.workers.strategy_worker:tick:418` (prior log) and `:tick:460` (current log) |
| 4 | `STRAT_CALL_A_FAIL` | `src.brain.strategist:create_trade_plan:407` |
| 4 | `Claude trade failed for ...: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'` | `src.core.layer_manager:_execute_new_trades:888` |
| 3 | `DB_ERR` (`no such table: cycle_metrics`) | `src.database.connection:execute:314` |
| 2 | `ORDER_RETRY_EXHAUSTED` | `src.trading.services.order_service:_place_order_with_idempotent_retry:576` |

(Counts verified by `grep -oE "\| [A-Z][A-Z_0-9]+ "` on the windowed line set.)

---

## Detail per pattern

### 1. ORDER_GATE_LM_DEADLINE_EXCEEDED (20×)

- **Source:** `src/trading/services/order_service.py:240` (`_enforce_layer3_gate`)
- **Sample full line (workers.2026-04-27_01-31-00_169356.log):**
  ```
  2026-04-27 11:19:32.667 | ERROR    | src.trading.services.order_service:_enforce_layer3_gate:240 | ORDER_GATE_LM_DEADLINE_EXCEEDED | link_id=ti-bfdb675042834456befef3e8 sym=ETHUSDT purpose=mcp_tool elapsed_s=4872.8 deadline_s=60.0 action=block | no_ctx
  ```
- **Context:** Layer 3 gate flips fail-close once `OrderService.init_elapsed_s > lm_attach_deadline_sec=60.0`. All 20 fires occurred between 11:19 and 19:11 UTC, all marked `purpose=mcp_tool` (operator-driven manual close attempts), `sym=ETHUSDT` (10×) or `sym=BTCUSDT` (10×). `elapsed_s` ranged 4872 → 33213 seconds (= LayerManager attach lifetime).

### 2. ORDER_BLOCKED (20×)

- **Source:** `src/trading/services/order_service.py:182` (`_emit_order_blocked`)
- **Sample:**
  ```
  2026-04-27 11:19:32.668 | ERROR    | src.trading.services.order_service:_emit_order_blocked:182 | ORDER_BLOCKED | link_id=ti-bfdb675042834456befef3e8 sym=ETHUSDT side=Sell purpose=mcp_tool reason=lm_deadline_exceeded force=False deadline_s=60.0 elapsed_s=4872.8 | no_ctx
  ```
- **Context:** Paired 1:1 with each ORDER_GATE_LM_DEADLINE_EXCEEDED. Same 20 events, downstream emission step.

### 3. DB_PROTECT_BLOCKED (11×)

- **Source:** `src/database/protected_tables.py:135` (`assert_not_protected_destructive`)
- **Sample (general.log):**
  ```
  2026-04-26 23:18:11.061 | ERROR    | src.database.protected_tables:assert_not_protected_destructive:135 | DB_PROTECT_BLOCKED | sql_kind=DELETE table=trade_thesis sql='DELETE FROM trade_thesis WHERE opened_at < ?' | no_ctx
  ```
- **Context:** Hourly. Each fire is a `DELETE FROM trade_thesis WHERE opened_at < ?` from CleanupWorker; the protected-tables guard rejects it. 11 fires correspond to 11 hours that emitted (some hours skipped).

### 4. WORKER_SHUTDOWN (CRITICAL, 8×)

- **Sources:** `workers:_sync_emit` (4) + `__main__:_atexit_log:82` (4)
- **Sample (workers.log):**
  ```
  2026-04-27 22:45:52.782 | CRITICAL | workers:_sync_emit | WORKER_SHUTDOWN | reason=atexit | clean exit recorded
  2026-04-27 22:45:52.781 | CRITICAL | __main__:_atexit_log:82 | WORKER_SHUTDOWN | reason=atexit | clean exit recorded
  ```
- **Context:** 4 process restarts in the 24h window: 06:16:42, 09:56:47, 22:45:52, plus the prior shutdown sequence. Each restart emits one CRITICAL from each of the two sync paths (8 total).

### 5. STRAT_PREFETCH_CRITICAL (7×)

- **Source (prior log):** `src/workers/strategy_worker.py:418`
- **Source (current log):** `src/workers/strategy_worker.py:460` (line shifted post-rebuild)
- **Sample:**
  ```
  2026-04-27 22:16:38.601 | ERROR    | src.workers.strategy_worker:tick:460 | STRAT_PREFETCH_CRITICAL | el=8571ms db=1087ms h1_db=774ms coins=50 | sid=s-1777328190020
  ```
  ```
  2026-04-27 04:01:42.561 | ERROR    | src.workers.strategy_worker:tick:418 | STRAT_PREFETCH_CRITICAL | el=12504ms db=1249ms h1_db=747ms coins=50 | sid=s-1777262490009
  ```
- **Context:** StrategyWorker prefetch >8000 ms threshold (per G2: hardcoded `if _section_ms["prefetch"] > 8000`). 7 fires in 24h. `el` ranged 8571 → 17030 ms; `db` ranged 570 → 1503 ms; `h1_db` ranged 521 → 2010 ms; coins=50 always.

### 6. STRAT_CALL_A_FAIL (4×)

- **Source:** `src/brain/strategist.py:407` (`create_trade_plan`)
- **Sample (brain.log):**
  ```
  2026-04-27 15:25:47.418 | ERROR    | src.brain.strategist:create_trade_plan:407 | STRAT_CALL_A_FAIL | err='Cannot extract JSON from response:
  ```
  (the err= field is multi-line — only the first line is shown above)
- **Context:** All 4 fires used identical `err='Cannot extract JSON from response:`. Timestamps: 15:25:47, 16:16:06, 16:22:01, 16:53:44 (afternoon window, all on 2026-04-27).

### 7. Claude trade failed (4×)

- **Source:** `src/core/layer_manager.py:888` (`_execute_new_trades`)
- **Sample (workers.2026-04-27_01-31-00_169356.log):**
  ```
  2026-04-27 06:34:50.866 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  2026-04-27 06:41:50.715 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  2026-04-27 06:48:20.718 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for RUNEUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  2026-04-27 06:48:20.805 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for ETHUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  ```
- **Context:** Signature mismatch — `_execute_new_trades` calls `ShadowOrderService.place_order(..., purpose=...)` but the Shadow wrapper does not accept `purpose`. All 4 fires inside one window 06:34-06:48 UTC. Affects 4 distinct symbol attempts (DYDXUSDT×2, RUNEUSDT, ETHUSDT). Process restarted at 06:16:42; this regression was hit on the next strategist cycle and continued on retries until the next process restart at 09:56:47 (per WORKER_SHUTDOWN above).

### 8. DB_ERR no such table: cycle_metrics (3×)

- **Source:** `src/database/connection.py:314` (`execute`)
- **Sample (general.log):**
  ```
  2026-04-27 07:18:06.986 | ERROR    | src.database.connection:execute:314 | DB_ERR | err='no such table: cycle_metrics' sql='INSERT OR REPLACE INTO cycle_metrics (hour_ts, cycles_count,  layer1a_p50_ms, la' | no_ctx
  ```
- **Context:** Hourly cycle-metrics flush from `CycleTracker` (config: `[observability].cycle_metrics_flush_seconds = 3600`). Table missing → 3 fires at 07:18, 08:18, 09:18 UTC. Disappears after 09:56 restart (the migration presumably ran).

### 9. ORDER_RETRY_EXHAUSTED (2×)

- **Source:** `src/trading/services/order_service.py:576` (`_place_order_with_idempotent_retry`)
- **Sample (workers.2026-04-27_01-31-00_169356.log):**
  ```
  2026-04-27 06:27:15.121 | ERROR    | src.trading.services.order_service:_place_order_with_idempotent_retry:576 | ORDER_RETRY_EXHAUSTED | link_id=ti-04caef00f3354840ba03b3b6 sym=ETHUSDT attempts=2 purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007) (ErrTime: 06:27:15).
  2026-04-27 06:27:16.040 | ERROR    | src.trading.services.order_service:_place_order_with_idempotent_retry:576 | ORDER_RETRY_EXHAUSTED | link_id=ti-c9ce7c6a9fc84233b067f74b sym=BTCUSDT attempts=2 purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007) (ErrTime: 06:27:15).
  ```
- **Context:** Bybit ErrCode 110007 ("ab not enough for new order"). Both fires within one second at 06:27:15-16. Sym=ETHUSDT and BTCUSDT, both purpose=mcp_tool.

---

## Distribution over time

| Hour bucket | ERRORs |
|---|---|
| 2026-04-26 23:00-00:00 | 1 |
| 2026-04-27 00:00-01:00 | 1 |
| 2026-04-27 01:00-02:00 | 1 |
| 2026-04-27 02:00-03:00 | 1 |
| 2026-04-27 03:00-04:00 | 1 |
| 2026-04-27 04:00-05:00 | ~5 (4× STRAT_PREFETCH_CRITICAL + 1× DB_PROTECT) |
| 2026-04-27 05:00-06:00 | 2 (1× STRAT_PREFETCH + 1× DB_PROTECT) |
| 2026-04-27 06:00-07:00 | ~10 (2× ORDER_RETRY_EXHAUSTED, 4× Claude trade failed, 2× WORKER_SHUTDOWN, 1× DB_PROTECT, 1× STRAT_PREFETCH likely) |
| 2026-04-27 07:00-09:00 | 3× DB_ERR cycle_metrics + 2× DB_PROTECT |
| 2026-04-27 09:00-10:00 | 2× WORKER_SHUTDOWN + 1× DB_PROTECT |
| 2026-04-27 11:00-12:00 | 2× ORDER_GATE + 2× ORDER_BLOCKED |
| 2026-04-27 12:00-13:00 | 2× ORDER_GATE + 2× ORDER_BLOCKED |
| 2026-04-27 13:00-14:00 | 2× ORDER_GATE + 2× ORDER_BLOCKED |
| 2026-04-27 15:00-16:00 | 1× STRAT_CALL_A_FAIL |
| 2026-04-27 16:00-17:00 | 3× STRAT_CALL_A_FAIL + 6× ORDER_GATE/BLOCKED + 1× DB_PROTECT |
| 2026-04-27 18:00-19:00 | 4× ORDER_GATE/BLOCKED |
| 2026-04-27 19:00-20:00 | 2× ORDER_GATE/BLOCKED (ETHUSDT) + ... |
| 2026-04-27 22:00-23:00 | 2× STRAT_PREFETCH_CRITICAL + 2× WORKER_SHUTDOWN |

(The exact per-hour count was not exhaustively bucketed by `awk` — distribution above is reconstructed from sample reads.)

---

## Notes

- **No DB_LOCK_WAIT errors** in the 24h window (the `WARNING` lines for DB_LOCK_WAIT are below the ERROR threshold; e.g., `2026-04-27 22:53:46.036 | WARNING ... DB_LOCK_WAIT | wait_ms=3621`). Per config, threshold is 1000 ms.
- **No BrainError / Claude CLI timed out / Cannot extract JSON failures in workers.log/general.log** — only in `brain.log` (4 fires, all 15:25-16:53).
- **No Reddit / Finnhub / Bybit-WS errors** in the 24h window.
- **No ShadowKlineReader async errors** — the 2026-04-26 fix (per memory) appears to have held.
- **No regime / signal / structure worker ERROR-level emissions** in the 24h window — these workers logged WARNING-and-below only.


================================================================================
FILE: G7_worker_inventory.md
================================================================================

# G7 — Worker Inventory

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Sources:**
  - Registration: `src/workers/manager.py:929-1378` (WorkerManager._create_workers)
  - Config gating: `config.toml` per section
  - Tier assignment: `src/workers/<name>.py:worker_tier = WorkerTier.LAYERnX`
  - Class: `src/workers/<name>.py`
  - Liveness aggregate: `WORKER_LIVENESS_HEARTBEAT total=19` (`workers.log`, e.g. line 5092 at 2026-04-27 23:18:41)
  - First-tick: `WORKER_FIRST_TICK | name=...` from `data/logs/workers.log` (current PID, 22:53:35-23:12 UTC) and `data/logs/workers.2026-04-27_01-31-00_169356.log`
  - Tick rate: `[HEARTBEAT] Worker 'X' alive | ticks=N | last_tick=...` lines in current `workers.log`. Each heartbeat re-emits the cumulative tick count for the current PID.

---

## Currently registered workers (19 total per WORKER_LIVENESS_HEARTBEAT)

The registrations executed in `manager.py._create_workers` (this session, current PID started at 22:53:35):

| # | Name | Tier | File location | Class | Config-gated by | First-tick (this PID) | Avg ticks/hour (24h) | Avg elapsed_ms / tick |
|---|---|---|---|---|---|---|---|---|
| 1 | `price_worker` | LAYER1A | `src/workers/price_worker.py` | `PriceWorker(BaseWorker)` | `_services["ws"]` present | 2026-04-27 22:53:40.962 (`el_to_first_tick_ms=576`) | continuous WS — heartbeat tick every ~45s = ~80/h | n/a (event-driven WS) |
| 2 | `kline_worker` | LAYER1A | `src/workers/kline_worker.py` | `KlineWorker(SweetSpotWorker)` | `_services["market"]` present | 2026-04-27 22:55:51.236 (`first_tick_el_ms=21235`) | 12/h (5-min sweet-spot) | 10433-21230 ms (M5+H1+H4+D1 mix) |
| 3 | `news_worker` | LAYER1A | `src/workers/news_worker.py` | `NewsWorker(BaseWorker)` | `_services["news"]` present (Finnhub `[finnhub].enabled=true`) | 2026-04-27 22:53:46.050 (`first_tick_el_ms=5086`) | ~12/h (news_interval=300s) | 1192-5518 ms |
| 4 | `altdata_worker` | LAYER1A | `src/workers/altdata_worker.py` | `AltDataWorker(SweetSpotWorker)` | any of `fear_greed`/`funding`/`oi`/`onchain` services available | 2026-04-27 22:56:54.192 (`first_tick_el_ms=9191`) | 12/h (5-min sweet-spot) | 4435-10137 ms |
| 5 | `signal_worker` | LAYER1B | `src/workers/signal_worker.py` | `SignalWorker(SweetSpotWorker)` | `ta`+`aggregator`+`signal_gen` services present | 2026-04-27 22:55:51 (estimated; first_tick line not separately captured this PID) | 12/h | 534-3352 ms |
| 6 | `regime_worker` | LAYER1B | `src/workers/regime_worker.py` | `RegimeWorker(SweetSpotWorker)` | `ta` service + scanner present | 2026-04-27 22:55:51 (estimated) | 12/h | 4044-9863 ms |
| 7 | `structure_worker` | LAYER1B | `src/workers/structure_worker.py` | `StructureWorker(SweetSpotWorker)` | `[analysis.structure].enabled=true` AND `structure_engine`+`structure_cache` services present | 2026-04-27 22:55:51 (estimated) | 12/h | 579-2303 ms |
| 8 | `strategy_worker` | LAYER1C | `src/workers/strategy_worker.py` | `StrategyWorker(SweetSpotWorker)` | `ta` + `scanner` + `regime_detector` services present | 2026-04-27 22:55:51 (estimated) | 12/h (cycle-gated) | 6011-6931 ms (TA), 8571-8870 ms (with prefetch_critical) |
| 9 | `scanner_worker` | LAYER1D | `src/workers/scanner_worker.py` | `ScannerWorker(SweetSpotWorker)` | `[scanner].enabled=true` AND `market_svc` present | 2026-04-27 22:53:35 register; 2026-04-27 22:09:00 prior PID first tick (current PID hadn't completed cycle by 23:00 — `LAYER1D_TICK_SKIP cycle_inactive` for 22:53+) | 12/h max (cycle-gated) | 19-66 ms |
| 10 | `position_watchdog` | UTILITY (BaseWorker, no tier set) | `src/workers/position_watchdog.py` | `PositionWatchdog(BaseWorker)` | `[watchdog].enabled=true` AND `position`+`market` services | 2026-04-27 22:53:41.592 (`first_tick_el_ms=627`) | 360/h (10s interval) | n/a in aggregate |
| 11 | `profit_sniper` | UTILITY | `src/workers/profit_sniper.py` | `ProfitSniper(BaseWorker)` | `[mode4].enabled=true` AND `position`+`market` services | 2026-04-27 22:53:41.567 (`first_tick_el_ms=600`) | 720/h (5s interval) | n/a |
| 12 | `enforcer_worker` | UTILITY | `src/workers/enforcer_worker.py` | `EnforcerWorker(BaseWorker)` | `[enforcer].enabled=true` | 2026-04-27 22:53:41.569 (`first_tick_el_ms=572`) | 60/h (60s interval) | n/a |
| 13 | `fund_manager_worker` | UTILITY | `src/workers/fund_manager_worker.py` | `FundManagerWorker(BaseWorker)` | `[fund_manager].enabled=true` | 2026-04-27 22:53:41.626 (`first_tick_el_ms=628`) | 60/h (60s interval) | n/a |
| 14 | `fund_reconciler` | UTILITY | `src/workers/fund_reconciler.py` | `FundReconciler(BaseWorker)` | `[fund_manager].reconcile_enabled=true` AND account_service present | 2026-04-27 22:53:41.627 (`first_tick_el_ms=627`) | 60/h | n/a |
| 15 | `cleanup_worker` | UTILITY | `src/workers/cleanup_worker.py` | `CleanupWorker(BaseWorker)` | always | 2026-04-27 22:53:46.040 (`first_tick_el_ms=5039`) | hourly | n/a |
| 16 | `telegram_bot_worker` | UTILITY | `src/workers/telegram_bot_worker.py` | `TelegramBotWorker(BaseWorker)` | `[telegram_interactive].enabled=true` | 2026-04-27 22:53:40.995 (`first_tick_el_ms=0`) | varies | n/a |
| 17 | `price_alert_worker` | UTILITY | `src/workers/price_alert_worker.py` | `PriceAlertWorker(BaseWorker)` | `[telegram_interactive].enabled=true` | 2026-04-27 22:53:41.563 (`first_tick_el_ms=567`) | 360/h (10s `[telegram_interactive].price_alert_check_interval=10`) | n/a |
| 18 | `scheduled_report_worker` | UTILITY | `src/workers/scheduled_report_worker.py` | `ScheduledReportWorker(BaseWorker)` | `[telegram_interactive].enabled=true` | 2026-04-27 22:53:41.565 (`first_tick_el_ms=569`) | varies (cron-scheduled) | n/a |
| 19 | `worker_liveness_watchdog` | UTILITY | `src/workers/worker_liveness_watchdog.py` | `WorkerLivenessWatchdog(BaseWorker)` | always (Phase 11 dead-workers fix) | 2026-04-27 22:53:41.002 (`first_tick_el_ms=1`) | 120/h (`[worker_liveness].watchdog_interval_sec=30`) | ~1 ms |

**Total: 19 workers** — matches `WORKER_LIVENESS_HEARTBEAT total=19`.

### 24h ticks/hour evidence

Heartbeat snapshot at 23:18:41 UTC reports `total=19 healthy=14 never_ticked=0 overdue=0 idle_cycle_gate=5 cycle_active=False`. The 5 `idle_cycle_gate` entries map to the 5 cycle-gated workers (kline_worker, structure_worker, signal_worker, regime_worker, strategy_worker, scanner_worker — all cycle_gated under L3=OFF). Wait — that's 6, not 5. Per `src/workers/scanner_worker.py:59 cycle_gated = True`, it is gated. But heartbeat says 5 idle. The 5 most likely are signal_worker, regime_worker, strategy_worker, scanner_worker, structure_worker (kline_worker is LAYER1A, may be exempt). **NOT FOUND** — exact gate-membership mapping for `idle_cycle_gate=5`.

Most-recent heartbeat tick counts (current PID, 22:53→23:00, ~7 min runtime):

| Worker | ticks (this PID) | minutes elapsed | impl. ticks/hour |
|---|---|---|---|
| profit_sniper | 296 | ~25 min | ≈710/h (cadence 5 s) |
| price_alert_worker | 149 | ~25 min | ≈360/h (10 s) |
| position_watchdog | 149 | ~25 min | ≈360/h (10 s) |
| worker_liveness_watchdog | 51 | ~25 min | ≈120/h (30 s) |
| price_worker | 29 | ~22 min | ≈80/h (45 s heartbeat) |
| telegram_bot_worker | 26 | ~25 min | ≈60/h (60 s) |
| fund_reconciler | 26 | ~25 min | ≈60/h (60 s) |
| fund_manager_worker | 26 | ~25 min | ≈60/h (60 s) |
| enforcer_worker | 26 | ~25 min | ≈60/h (60 s) |
| structure_worker | 7 | ~25 min | ≈12/h (5 min) |
| strategy_worker | 7 | ~25 min | ≈12/h (5 min) |
| signal_worker | 7 | ~25 min | ≈12/h (5 min) |
| scanner_worker | 7 | ~25 min | ≈12/h (5 min, but cycle-gated → many SKIP) |
| regime_worker | 7 | ~25 min | ≈12/h (5 min) |
| scheduled_report_worker | 6 | ~25 min | varies |
| news_worker | 6 | ~25 min | ≈12/h |
| altdata_worker | 5 | ~25 min | ≈12/h |
| kline_worker | 4 | ~22 min | ≈12/h |

Tick counts taken from grep `[HEARTBEAT] Worker '<name>' alive | ticks=N` lines, latest occurrence per worker name in current `workers.log`.

---

## NOT registered (manager.py never appends them)

The following 7 workers **exist as classes** but are **not registered** in this PID's WorkerManager:

| Worker | File | Class | Gating mechanism (verbatim) | Currently disabled? |
|---|---|---|---|---|
| `discovery_worker` | `src/workers/discovery_worker.py` | `DiscoveryWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` | **YES** — `[factory].enabled = false` (config.toml:545) |
| `live_monitor_worker` | `src/workers/live_monitor_worker.py` | `LiveMonitorWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` (same block) | **YES** — same `[factory].enabled = false` |
| `backtest_worker` | `src/workers/backtest_worker.py` | `BacktestWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` (same block) | **YES** — same `[factory].enabled = false` |
| `trial_monitor_worker` | `src/workers/trial_monitor_worker.py` | `TrialMonitorWorker(BaseWorker)` | `manager.py:1195: if s.factory.enabled:` (same block) | **YES** — same `[factory].enabled = false` |
| `reddit_worker` | `src/workers/reddit_worker.py` | `RedditWorker(BaseWorker)` | `manager.py:959: if self._services.get("reddit"):` — Reddit service is created only when `s.reddit.client_id` is set (manager.py:142). | **YES** — `[reddit].enabled = false` (config.toml:52) AND no `client_id` set (manager.py logs `REDDIT_DISABLED` WARNING at startup) |
| `optimization_worker` | `src/workers/optimization_worker.py` | `OptimizationWorker(BaseWorker)` | **No registration block in manager.py.** Comment at manager.py:1244 reads: *"AllocationWorker and OptimizationWorker removed — replaced by IntelligentFundManager (M1, M8 modules)"*. Class file still exists but is never instantiated. | **YES** — code path removed |
| `allocation_worker` | `src/workers/allocation_worker.py` | `AllocationWorker(BaseWorker)` | Same as above — removed at manager.py:1244 | **YES** — code path removed |

### Confirmation per worker

- `factory`-gated four (discovery, live_monitor, backtest, trial_monitor): all four registered together in the `if s.factory.enabled:` block at manager.py:1195-1227. Config `[factory].enabled = false  # Disabled: 0 patterns discovered, 0 backtests run — wasting CPU` (config.toml:545). **Intentional**.
- `reddit_worker`: gated by `_services.get("reddit")` at manager.py:959. Reddit service skipped at manager.py:141-156 because either `reddit.client_id` is unset or `[reddit].enabled = false`. The startup log emits `REDDIT_DISABLED | reason=no_credentials | impact=sentiment_degraded` (warning, manager.py:153-156). **Intentional** — config.toml:52 explicitly sets `[reddit].enabled = false`.
- `optimization_worker`, `allocation_worker`: **no registration code path exists in manager.py at all**. Replaced by `IntelligentFundManager` (M1/M8 modules) per the comment at line 1244. The class files (`src/workers/optimization_worker.py`, `src/workers/allocation_worker.py`) are dead code by registration but still importable. **Intentional removal**.

### NOT FOUND — `live_monitor_worker` log lines

Searched `data/logs/workers.log` and `data/logs/workers.2026-04-27_01-31-00_169356.log` for any line containing `DiscoveryWorker|OptimizationWorker|RedditWorker|LiveMonitorWorker|TrialMonitorWorker|BacktestWorker|AllocationWorker`. Zero matches. Confirms registration did not occur.

---

## Tier breakdown

| Tier | Workers | Count |
|---|---|---|
| LAYER1A (always-on data) | price_worker, kline_worker, news_worker, altdata_worker | 4 |
| LAYER1B (analyzers) | signal_worker, regime_worker, structure_worker | 3 |
| LAYER1C (strategy pipeline) | strategy_worker | 1 |
| LAYER1D (smart scanner) | scanner_worker | 1 |
| UTILITY (no `worker_tier`) | position_watchdog, profit_sniper, enforcer_worker, fund_manager_worker, fund_reconciler, cleanup_worker, telegram_bot_worker, price_alert_worker, scheduled_report_worker, worker_liveness_watchdog | 10 |
| **Total** | | **19** |

(LAYER4 — `Tier=4` per memory note for some legacy classification — does not have any *currently registered* worker assigned `worker_tier = WorkerTier.LAYER4` per grep of `src/workers/*.py`. The Layer 1 sub-layers map to LAYER1A/1B/1C/1D only.)

