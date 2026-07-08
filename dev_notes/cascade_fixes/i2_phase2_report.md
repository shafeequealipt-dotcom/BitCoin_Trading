# Issue 2 — Phase 2 Operator Discussion Report

## Summary

The ticker_cache write storm is the actual cascade root cause: 99.7-99.8% of all DB_LOCK_WAIT events in Phase 0 baseline are held by ticker_cache `INSERT OR REPLACE` writes. Peak observed wait time was 63.6 seconds (worse than the report's 26.79s).

The pre-fix path schedules one `save_ticker` per WebSocket message via `asyncio.run_coroutine_threadsafe`. At 100-200 messages/sec each acquiring the global `asyncio.Lock`, the writes queue behind themselves and starve every other DB operation.

The fix introduces an in-memory write buffer with periodic batched flush — collapsing many puts for the same symbol into one DB row, and reducing DB write rate from ~180/sec to ~2/sec.

## Evidence

- Writer: `src/workers/price_worker.py:206-296` — per-message `run_coroutine_threadsafe(save_ticker(...))`
- Connection: `src/database/connection.py` single `aiosqlite.Connection` + single `asyncio.Lock`
- Existing batching pattern (NOT applied to ticker_cache before fix): `MarketRepository.save_klines:65-150` (chunks at 500 + `await asyncio.sleep(0)`)
- Phase 0 baseline (rotated `general.log`): 35,290/35,353 events held by ticker_cache, max wait 63,648 ms, count >10s = 27,208 (77%)

## Solution chosen

**Option A (in-memory ring + periodic flush, recommended)**:

1. New module `src/workers/ticker_cache_buffer.py` — `TickerCacheBuffer`:
   - `put(ticker)` — sync, thread-safe, latest-wins per symbol (`{symbol: latest_ticker}`)
   - `get(symbol)` — in-memory snapshot lookup
   - `start()` / `stop()` — async drainer task lifecycle
   - `flush()` — snapshot → `MarketRepository.save_tickers_batch` → clear
   - Default flush interval: 500 ms
   - Heartbeat log every 60 flushes

2. `MarketRepository.save_tickers_batch(tickers)` — single `executemany` mirroring `save_klines` chunking. Single lock acquisition per flush.

3. `PriceWorker` — new optional `ticker_buffer` constructor kwarg. When provided, the WS callback puts into the buffer; the drainer is started in `tick()` and stopped in `cleanup()` with a final flush.

4. `WorkerManager._setup` — construct buffer before PriceWorker, register in services, inject into PriceWorker, attach to Transformer.

5. `Transformer._get_local_price` — consult buffer first; fall back to DB SELECT on miss. Buffer entries are by definition <500ms old (else drainer would have flushed them) — strictly fresher than the DB.

6. `MarketRepository.get_ticker` — same buffer-first pattern for consistency.

## Trade-offs

### Pros
- DB write rate from ticker_cache: 100-200/sec → ~2/sec (50-100x reduction)
- Max wait_ms: 63s → <100ms (one executemany of ≤50 rows)
- Reader latency improved (in-memory hit path for transformer)
- Mirrors the established klines-batching convention (`save_klines`)
- No reader contract change (callers of `get_ticker` see fresher or equally-fresh data)
- Crash recovery: ≤500ms of state lost (acceptable for hot-replace cache)
- Shadow mode benefits identically (PriceWorker is mode-agnostic)
- Drainer survives flush failures (logs and continues)

### Cons
- Adds one new module + ~300 lines (buffer + tests)
- Cross-process readers (other Python processes querying SQLite directly) see DB up to 500ms stale; mitigated by no known cross-process reader on this table
- Buffer holds latest state in memory only — absolute crash loses ≤500ms of data
- `MarketRepository.save_ticker` (singular) retained for backward compat / tests

### Risks
- The drainer is a new long-lived task — if it dies silently, writes back up forever. Mitigated: `stats()` exposes pending size; `_drainer` only exits on `_stop=True` or `CancelledError`; flush errors are logged but do not exit; the next tick of `PriceWorker.tick()` re-starts the drainer if its task is None.
- `attach_ticker_buffer` on the Transformer is late-bound; if the wire is missed, the transformer falls back to the DB-only path (no regression, just the original behavior).

### Alternatives considered

- **Option B (per-second flush)**: same idea, 1s cadence. Lower DB pressure but stale by up to 1 sec. Chose 500ms as middle ground.
- **Option C (separate writer connection)**: open a second `aiosqlite.Connection`. Doesn't reduce the WRITE RATE — only reduces contention with reads. Combinable with Option A but unnecessary for the immediate fix.

## Verification plan

After deploy:
1. New `TICKER_BUFFER_START | flush_interval_ms=500` log line on first PriceWorker tick
2. `TICKER_BUFFER_HEARTBEAT | flushes=N written=M ...` line appears every ~30 seconds
3. DB_LOCK_WAIT count from ticker_cache holders: drops by ~95% (target <5/sec)
4. Max wait_ms: drops to <1000ms
5. Cumulative DB_LOCK_WAIT > 10s: target <10 per 9-min window (was 27,208)
6. `WD_TICK_SLOW` count: substantial drop
7. `BASE_WORKER_TICK_SLOW` count: substantial drop
8. Cascade load test: open 8+ positions, watchdog tick remains <1s
9. Shadow mode: switch mode=shadow, observe PriceWorker still ticks, buffer logs still fire

Tests:
- 12 tests pin the buffer's contract (latest-wins put, thread safety, drainer interval, final flush on stop, drainer survives flush failure, buffer-first read in repo)
