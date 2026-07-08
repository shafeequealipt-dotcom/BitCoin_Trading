# Issue 2 — Phase 1 Investigation Synthesis

## What the report said

`LIVE_PIPELINE_MONITOR_2026-05-10.md` (Bug #1) and `PHASE5_LIVE_MONITORING_REPORT.md` (Finding A14):

> The ticker_cache table is updated via `INSERT OR REPLACE INTO
> ticker_cache` on every ticker WebSocket message. Peak observed:
> 180 writes per second. With 50 symbols in the universe and ticker
> frequency varying per symbol, this can sustain 100-200 writes per
> second indefinitely. Each write acquires the SQLite connection
> mutex briefly. Under normal conditions this is fine. But when the
> fear_greed_index query (Issue 1) holds the mutex for 11 seconds,
> hundreds of writes queue. After Issue 1 releases the mutex, the
> queue drains slowly because each individual write also holds the
> mutex briefly, blocking the next write and any other DB
> operations.

## What current code shows

### Writer

`src/workers/price_worker.py:206-296` `_handle_ticker_update` runs in pybit's WS callback thread. Per message:

```python
# line 285 (pre-fix)
future = asyncio.run_coroutine_threadsafe(
    self.market_repo.save_ticker(ticker), loop,
)
future.add_done_callback(self._on_save_ticker_done)
```

Each `save_ticker` is an individual `INSERT OR REPLACE INTO ticker_cache (...)` under `DatabaseManager`'s global `asyncio.Lock`.

`MarketRepository.save_ticker` (`src/database/repositories/market_repo.py:260-283`) is a single `await self._db.execute(...)` — one lock acquisition per call.

### Database connection

`src/database/connection.py:84-146`:
- Single `aiosqlite.Connection`
- Single `asyncio.Lock` (`self._lock`) protecting ALL DB ops
- WAL mode enabled
- The asyncio lock serializes the whole pool, so WAL's "concurrent reads during writes" benefit is negated at the application layer

### Existing batching pattern in same repo

`MarketRepository.save_klines()` (line 65-150) chunks the params list into 500-row batches and `await asyncio.sleep(0)` between chunks. This pattern was added in Phase 1 (D-3 fix) when kline saves were holding the lock 12-20s. The same pattern was NEVER applied to ticker_cache.

### Phase 0 baseline evidence

| Log file | Sample window | ticker_cache holders | Total events | Max wait_ms | Count > 10s |
|----------|--------------:|---------------------:|-------------:|------------:|------------:|
| `general.log` (current) | a few minutes | 719 (99.7%) | 721 | 2,593 | 0 |
| `general.2026-05-10_17-43-57_716987.log` | 9 min | **35,290 (99.8%)** | 35,353 | **63,648** | **27,208** |

Conservatively: at peak, ~84 DB_LOCK_WAIT events per second; max wait 63.6s.

### Readers (current code paths)

| Caller | File:Line | Path |
|--------|-----------|------|
| `Transformer._get_local_price` | `src/core/transformer.py:850-909` | Direct `SELECT FROM ticker_cache` (Hot — per-position price reconciliation) |
| `MarketRepository.get_ticker` | `src/database/repositories/market_repo.py:285-309` | Direct SELECT (low-frequency — not on hot paths in current code) |
| `MarketService.get_ticker` | `src/trading/services/market_service.py:48-68` | Hits Bybit API (5-second in-memory TTL) — does NOT touch ticker_cache table |

`MarketService.get_ticker` is the dominant ticker reader for production code (telegram, scanner, brain, sniper). It already has a 5-second in-memory cache in front of the API call, and never reads ticker_cache. So the cascade fix needs to address writes, not reads.

The transformer's price-reconciliation reader IS hot and goes through ticker_cache directly — the buffer must be reachable from the transformer.

### PriceWorker also has its own in-memory `_ws_quotes` cache

`src/workers/price_worker.py:67` `self._ws_quotes: dict[str, tuple[float, float]]` already exists and is updated on every WS tick. APEX assembler consults it via `get_ws_quote()`. This proves the write-storm is for the DB row, not for in-memory access — the team already maintains an in-memory cache for hot reads.

## Recommended fix point

Decouple WS writes from DB writes via a buffered batched flush:

1. **New module** `src/workers/ticker_cache_buffer.py` — `TickerCacheBuffer` class with:
   - Synchronous, thread-safe `put(ticker)` (latest-wins per symbol)
   - Async `start()` / `stop()` lifecycle (drainer task)
   - Async `flush()` that snapshots and writes via `save_tickers_batch`
   - Default flush interval: 500 ms (≤ 2 DB writes/sec)
   - Heartbeat log every 60 flushes (~30s)

2. **MarketRepository.save_tickers_batch(tickers)** — single `executemany` call, mirrors `save_klines` chunking pattern.

3. **PriceWorker** — accept `ticker_buffer` constructor kwarg; when provided, replace `run_coroutine_threadsafe(save_ticker(...))` with `buffer.put(ticker)`. Start drainer in `tick()` once loop is captured. Stop in `cleanup()` with final flush.

4. **WorkerManager._setup** — construct `TickerCacheBuffer` before `PriceWorker`, register in services, inject into PriceWorker, attach to Transformer via `attach_ticker_buffer`.

5. **Transformer._get_local_price** — consult buffer first when attached; fall back to DB SELECT on miss.

6. **MarketRepository.get_ticker** — same buffer-first / DB-fallback pattern (consistency with transformer; minor benefit because callers are rare).

## Estimated impact

- DB write rate from ticker_cache: 100-200/sec → ~2/sec (at default 500ms cadence; 50-row batches)
- DB_LOCK_WAIT count: ~84/sec → ~2/sec from this source
- Max wait_ms: 63,648 → < 100 (single executemany of ≤ 50 rows is fast)
- WD_TICK_SLOW count: substantial drop (downstream of cascade)
- Reader latency: in-memory hit on transformer's price reconciliation path (fewer DB hops)
- Crash recovery: ≤ 500ms of latest-state lost; ticker_cache is hot-replace, no historical impact
- Shadow mode: PriceWorker is constructed in both modes — buffer benefits both
- Memory: in-memory `dict[symbol → Ticker]` bounded by universe size (≤ 50 entries)
