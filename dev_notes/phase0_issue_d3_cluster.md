# Phase 0 — Issue Investigation: D-3 Cluster (Issues #1, #2, #6)

**Issues covered:**
- #1 KlineWorker tick latency 12-20s chronic
- #2 KLINE_WRITE_LAG stale_count=31 (every symbol 200-330s stale)
- #6 WAL pinned at 104MB for 60+ minutes
- Cascade: StrategyWorker queueing 2-7s
- Cascade: Latency degradation 13s → 15.5s mean

## Section A — The mechanism

### A.1 KlineWorker tick anatomy

**File:** `src/workers/kline_worker.py:95-263`

The tick loop is **sequential**, not concurrent (no `asyncio.gather`):

```python
# kline_worker.py:141-168
for symbol in self._tracked_symbols:
    for timeframe, min_interval in TIMEFRAME_SCHEDULE.items():
        cache_key = f"{symbol}:{timeframe.value}"
        last = self._last_fetch.get(cache_key, 0)
        if now - last < min_interval:
            continue
        per_symbol_expected[symbol] += 200
        try:
            klines = await self.market_service.get_klines(symbol, timeframe, limit=200)
            n = len(klines)
            total_fetched += n
            per_symbol_fetched[symbol] = per_symbol_fetched.get(symbol, 0) + n
            self._last_fetch[cache_key] = now
            await asyncio.sleep(0.1)        # <-- artificial yield, line 162
        except Exception as e:
            log.debug("Kline fetch failed {s}/{tf}: {err}", ...)  # debug-only, line 164-167
```

`TIMEFRAME_SCHEDULE` covers M5/H1/H4/D1 with `min_interval` of 60/60/300/3600s respectively. With ~30 active symbols, the per-tick fetch count is bounded by which timeframes have aged past their cooldown. In steady state during M5 candle closes: ~30 fetches/tick (M5 only), each followed by `await asyncio.sleep(0.1)` → **~3.0s of artificial sleep**. At H1 boundaries: ~60 fetches → **~6s sleep**. At rare H4/D1 boundaries: up to ~120 fetches → **~12s sleep**.

This sleep **is outside the DB lock** but consumes wall-clock time on the tick.

### A.2 The save path

**File:** `src/database/repositories/market_repo.py:46-137`

`save_klines` is invoked from inside `market_service.get_klines` (per Phase 1 verification — saves on the path of the fetch). It executes per-(symbol, timeframe), batches of typically 200 rows:

```python
# market_repo.py:64-83
sql = "INSERT OR IGNORE INTO klines (...) VALUES (?, ...)"
params = [...]                                     # ~200 tuples
await self._db.executemany(sql, params)            # acquires DatabaseManager._lock
```

For ~30 symbols × ~1.5 average timeframes-per-tick = ~45 `executemany` calls per tick, each acquiring the lock independently.

The **deferred retention DELETE** (`market_repo.py:101-129`) fires every 50 calls per (sym,tf) — silent in steady state but adds an additional `await self._db.execute(...)` lock acquisition periodically, with a slightly more expensive query plan (`SELECT ... LIMIT 1 OFFSET ?` for the cutoff timestamp).

### A.3 The DatabaseManager lock

**File:** `src/database/connection.py:36-37, 117-168`

```python
class DatabaseManager:
    def __init__(self, db_path: str, wal_mode: bool = True) -> None:
        ...
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()                # <-- single shared lock
```

All five access methods (`execute`, `executemany`, `fetch_one`, `fetch_all`, `transaction`) acquire `self._lock` before each operation. There is **one** `aiosqlite.Connection` (the single file-handle to trading.db) and one lock guarding it. All workers and services share this manager instance via the WorkerManager (`src/workers/manager.py`).

Practical consequence: while `kline_worker.save_klines` holds the lock for one of its 45+ executemany acquires, `strategy_worker.prefetch` (which reads klines for the universe) is blocked.

### A.4 PRAGMA configuration vs. live state

**Code (`connection.py:44-59`):**
```
journal_mode=WAL
busy_timeout=10000
foreign_keys=ON
cache_size=-65536           (64 MiB)
synchronous=NORMAL
wal_autocheckpoint=2000
journal_size_limit=104857600 (100 MiB)
temp_store=MEMORY
mmap_size=268435456         (256 MiB)
```

**Live database (run via `sqlite3 data/trading.db`):**
```
journal_mode = wal           ✓
synchronous = 2 (NORMAL)     ✓
wal_autocheckpoint = 1000    ❌ (code: 2000)
journal_size_limit = -1      ❌ (code: 104857600)
busy_timeout = 0             ❌ (code: 10000)
cache_size = -2000 (2 MiB)   ❌ (code: -65536)
```

Four mismatches. Possible causes:
1. `sqlite3` CLI opens its own connection that does NOT receive the workers process's PRAGMAs (most likely — PRAGMAs are per-connection).
2. The workers process crashed or was killed during connect() before all PRAGMAs were applied.
3. A different process (shadow.py? brain.py?) opened trading.db with different settings, and the CLI sees one of those.

This must be diagnosed via a one-shot script run inside the workers process before scheduling WAL checkpoints. **The PRAGMAs may actually be correct in the live workers process; the `sqlite3` CLI reading was misleading.**

### A.5 WAL pinning

`trading.db-wal` was at 104,857,600 bytes (100 MiB) — exactly the `journal_size_limit` set in code. This suggests the workers process **did** apply that PRAGMA. The WAL grows toward the limit and stalls there because:
- `wal_autocheckpoint=2000` in code (or 1000 if live state is correct) fires on every-N-frames basis
- Even when autocheckpoint fires, **a long-running reader holds the snapshot open**, preventing WAL truncation
- No code path explicitly invokes `PRAGMA wal_checkpoint(TRUNCATE)` to force reclamation

**Reader candidates:**
- The single shared `DatabaseManager._db` connection — every read holds a brief snapshot but releases on lock release. Should not hold WAL open continuously.
- `ShadowKlineReader` (`src/shadow/shadow_kline_reader.py:66-116`) — confirmed long-lived, but reads `shadow.db`, NOT `trading.db`. Not a culprit for trading.db WAL.
- `sqlite3` CLI sessions — but CLI exits release the read.
- Long `fetch_all` queries — none observed of significance.

The pinning is more likely the autocheckpoint silently no-op'ing because the `fetch_all`/`execute` interleaving creates micro-snapshots faster than the autocheckpoint completes.

## Section B — The dependencies

- **strategy_worker** (`src/workers/strategy_worker.py:317-335, 383`) reads klines via `db.fetch_all` during prefetch. Blocks on the same `_lock` that kline_worker holds. `STRAT_PREFETCH_CRITICAL` fires when prefetch exceeds 8000ms.
- **structure_worker** (`src/workers/structure_worker.py`) reads klines for X-RAY computation per symbol per session. Same lock contention.
- **regime_worker** (`src/workers/regime_worker.py`) reads klines for per-coin regime detection. Same.
- **signal_worker** (`src/workers/signal_worker.py`) reads klines indirectly via TA layer. Same.
- **cleanup_worker** (`src/workers/cleanup_worker.py`) writes via `db.execute` for cleanup tasks. Same.

Every read or write to trading.db serializes on `connection.py:37`'s lock.

## Section C — The constraints

- **Cannot break Layer 1 invariants** (HR-1/HR-2/HR-3). Universe sourcing from scanner stays as-is.
- **Cannot break the deferred retention DELETE** (Stage-1/2 fix at `market_repo.py:25-44`).
- **Cannot change the klines schema** — too many readers depend on its current shape.
- **Cannot abandon WAL mode** — single-writer SQLite + concurrent readers requires WAL.
- **Cannot drop `INSERT OR IGNORE`** — kline replay tolerance depends on it.
- **Bybit rate limits** — must not flood API. Currently the 0.1s sleep at line 162 acts as a crude throttle. **However**, `market_service.get_klines` and the underlying Bybit client likely have their own rate limiting (must verify in Phase 2 before removal).

## Section D — The fix candidates

### D.1 Drop the artificial 0.1s sleep — HIGHEST EXPECTED IMPACT

**Change**: `kline_worker.py:162` `await asyncio.sleep(0.1)` → `await asyncio.sleep(0)` (yield without rate-limiting).

**Pre-condition**: Verify rate-limit elsewhere. Read `market_service.get_klines` and the Bybit client — if they have a `@rate_limit` decorator or token bucket, this is safe.

**Expected impact**: tick p50 drops from ~13s to ~3-4s. **This may be the entire D-3 fix.**

### D.2 PRAGMA verification

Run a one-shot script that uses `DatabaseManager.connect()` and immediately dumps all PRAGMAs. Confirm whether the live workers process actually has the values code sets. If not, investigate the `aiosqlite` PRAGMA application path.

### D.3 Add scheduled WAL checkpoint

Add a `DatabaseManager.checkpoint(mode="PASSIVE")` method invoked every ~30 minutes from `cleanup_worker`. Logs `WAL_CHECKPOINT | busy=N log=N ckpt=N`. If `busy>0` repeats, identify the pinned reader.

### D.4 Chunked saves (if D.1+D.3 insufficient)

Split `executemany` of >500 rows into chunks of 500 with `await asyncio.sleep(0)` between chunks. Each chunk re-acquires the lock — this is intentional, allowing other writers in.

### D.5 Read connection split (last resort)

Open a second `aiosqlite.Connection` configured `query_only=ON` for hot read paths (strategist prefetch). Writers still serialize; readers no longer block on writers.

**Recommendation**: Do D.1 first → re-measure → do D.2/D.3 → only do D.4/D.5 if necessary. Each its own commit per Hard Rule 3.

## Section E — Verification criteria

After Phase 2 + Phase 4:
- kline_worker tick `el=` p50 < 5s, p95 < 10s, max < 15s
- Zero `STRAT_PREFETCH_CRITICAL` over 60min
- `KLINE_WRITE_LAG stale_count` < 5 typical
- WAL size < 50MB consistently
- `WAL_CHECKPOINT busy=0` on most invocations

## Verified citations

| Claim | File:Line |
|---|---|
| Sequential fetch loop | `src/workers/kline_worker.py:141-168` |
| Artificial 0.1s sleep | `src/workers/kline_worker.py:162` |
| Per-symbol failure logged at DEBUG only | `src/workers/kline_worker.py:164-167` |
| Single `asyncio.Lock` | `src/database/connection.py:37` |
| PRAGMA configuration | `src/database/connection.py:44-59` |
| `executemany` lock acquisition | `src/database/connection.py:157-160` |
| `save_klines` per-(sym,tf) batch | `src/database/repositories/market_repo.py:46-137` |
| Deferred retention DELETE every 50 calls | `src/database/repositories/market_repo.py:44, 101-129` |
| `KLINE_WRITE_LAG` diagnostic source | `src/workers/kline_worker.py:225-253` |
| `STRAT_PREFETCH_CRITICAL` definition | `src/workers/strategy_worker.py:383` |
