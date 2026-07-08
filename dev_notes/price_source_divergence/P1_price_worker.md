# P1 — PriceWorker (main project's WebSocket-facing price fetcher)

## P.1.1 — File and overview

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/price_worker.py`
- **Lines of code:** 264 (incl. blanks/comments) — `wc -l` confirmed
- **Class:** `PriceWorker(BaseWorker)` at `price_worker.py:26`
- **Worker tier:** `WorkerTier.LAYER1A` at `price_worker.py:41`
- **Tick interval:** `settings.workers.market_data_interval` (default 45 s) — `price_worker.py:49`

The tick body is a connection-health/reconnect loop. The actual price ingest happens in the WebSocket callback `_handle_ticker_update` (`price_worker.py:161`), which runs on whatever thread `pybit` dispatches it on (NOT the asyncio event loop).

## P.1.2 — WebSocket subscription

- **Library:** `pybit.unified_trading.WebSocket` wrapped by `src.trading.websocket.BybitWebSocket`
- **Connect:** `await self.ws.connect_public()` — `price_worker.py:110`
- **Subscribe:** `self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)` — `price_worker.py:111`
- **Stream type:** Bybit `tickers.{symbol}` linear-perp ticker stream
- **Universe:** `settings.universe.watch_list` — 50 symbols pre-seeded into `self._tracked_symbols` at `price_worker.py:59`
- **Reconnect policy:** `tick()` polls `self.ws.is_running` and resets `self._connected = False` so the next tick reconnects (`price_worker.py:134-137`). No exponential backoff on this side — the underlying `pybit` client manages reconnects itself.

## P.1.3 — Where prices are written

Verbatim from `price_worker.py:185-220`:

```python
last_price = _sf(tick_data.get("lastPrice"))     # line 185
if last_price <= 0:
    log.debug(...)
    return  # Skip update with zero/invalid price

# Phase 6: update in-memory quote cache for APEX / assembler.
self._ws_quotes[symbol] = (last_price, _time.monotonic())   # line 196
self._ws_msg_count += 1                                     # line 200

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

# Save synchronously via the repo (callback is sync)
import asyncio
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self.market_repo.save_ticker(ticker))   # line 218
except RuntimeError:
    pass    # ← OBSERVED ANOMALY: silent swallow when no loop on this thread
```

**Source field:** `tick_data["lastPrice"]` — Bybit's last traded price.
**Two write sinks:**
1. `self._ws_quotes[symbol] = (last_price, _time.monotonic())` — in-memory dict (line 196)
2. `loop.create_task(self.market_repo.save_ticker(ticker))` — writes to `ticker_cache` SQLite table (line 218)

**OBSERVED ANOMALY:** the `try / except RuntimeError: pass` at `price_worker.py:216-220` silently swallows the case where the pybit callback runs on a thread without an asyncio running-loop. In that case the SQLite write is dropped. See live evidence in `W2_anomalies.md` (anomaly A1) — `ticker_cache` has only 8 rows and its newest entry is 5+ hours stale at capture time.

## P.1.4 — Cache structure: `_ws_quotes`

Defined at `price_worker.py:66`:

```python
self._ws_quotes: dict[str, tuple[float, float]] = {}
```

- **Key:** symbol (e.g. `"BTCUSDT"`)
- **Value:** `(last_price: float, monotonic_ts: float)` where `monotonic_ts` is `_time.monotonic()`
- **TTL:** 5.0 s — accessor `get_ws_quote(symbol, max_age_s=5.0)` returns `None` for stale entries (`price_worker.py:239-257`)
- **Live sample:** could not be sampled — the dict lives in PID 398's memory and is not introspectable from outside the process. The `PRICE_WS_HEALTH` log line (`price_worker.py:149-157`) emits `quotes_cached=N` every tick. Per Shadow's `/api/health` we see Shadow has 50,886 WS msgs total over ~8 min uptime (~106 msgs/s aggregate), so equivalent rate on main side should be similar order.

## P.1.5 — Cache structure: `ticker_cache` table

Schema (verbatim from live `trading.db` SQLite):

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
)
```

**Write site:** `src/database/repositories/market_repo.py:266-283`

```sql
INSERT OR REPLACE INTO ticker_cache
(symbol, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

**Read site:** `src/database/repositories/market_repo.py:294-296` (`get_ticker`) and `src/core/transformer.py:666-669` (`_get_local_price`).

**Live state at 2026-05-02 11:30:27 UTC** — eight rows total:

| symbol | last_price | updated_at | age at capture |
|---|---|---|---|
| ONDOUSDT | 0.2696 | 2026-05-02T06:30:10 | 5 h 0 m |
| NEARUSDT | 1.2877 | 2026-05-02T06:26:34 | 5 h 4 m |
| INJUSDT | 3.784 | 2026-05-02T06:19:29 | 5 h 11 m |
| MANAUSDT | 0.0895 | 2026-05-02T06:14:41 | 5 h 16 m |
| AXSUSDT | 1.3789 | 2026-05-02T06:06:19 | 5 h 24 m |
| DOGEUSDT | 0.10751 | 2026-05-02T05:59:29 | 5 h 31 m |
| HYPERUSDT | 0.12379 | 2026-05-02T05:25:11 | 6 h 5 m |
| AEROUSDT | 0.4558 | 2026-05-02T05:18:07 | 6 h 12 m |

**OBSERVED ANOMALY:** despite PriceWorker subscribing to 50 symbols and the WS being demonstrably healthy on Shadow's side (50,886 msgs received), `ticker_cache` contains only 8 symbols and none have been updated for 5+ hours. The 8 symbols correspond exactly to the symbols that were traded today; the rows were almost certainly written by `MarketService._fetch_ticker` (REST, `market_service.py:101`) when `OrderService` priced the orders, NOT by the live WS callback. See `W2_anomalies.md` A1 for the full diagnosis.

**Indexes:** none beyond the implicit PK on `symbol`.

## P.1.6 — Other price-related caches

- **`MarketService._ticker_cache`** (in-memory) — `src/trading/services/market_service.py:45`
  - Type: `dict[str, tuple[float, Ticker]]`
  - TTL: 5.0 s — `market_service.py:46`
  - Populated by `_fetch_ticker` on REST hit at `market_service.py:67`
  - Special key `_all_linear` for bulk fetch at `market_service.py:118-123` with 30 s TTL
- **`klines` SQLite table** — written by `KlineWorker` (`src/workers/kline_worker.py`)
  - Read at multiple places via `MarketRepository.get_klines_by_timeframe` and similar
- **DB tables:** `ticker_cache`, `klines`, `ticker_snapshots` (Shadow only — see Q1)
- No additional in-memory price storage in main project beyond the three above.

## P.1.7 — Live measurement

- Shadow's `/api/health` reports `ws_messages_total=50886` over `uptime_seconds=494` ≈ **103 WS msgs/sec aggregate** at capture time.
- Main project's PriceWorker should run at similar order; the per-tick `PRICE_WS_HEALTH` log line gives `msgs_per_min` over the most recent ~45 s window. Could not be sampled here — would require reading the live log file.
- `ticker_cache` row count: 8 (verified via `SELECT COUNT(*) FROM ticker_cache`).
- `_ws_quotes` size: not externally observable. The `PRICE_WS_HEALTH` line in `logs/` would show it.

**NOT IDENTIFIED — live in-process introspection of `_ws_quotes`** — investigated locations: only direct attach to PID 398 (gdb / py-spy) would surface it, and that's outside the data-collection scope.
