# T1-4 Phase 1 — F4 VACUUM cascade investigation

## 1. Defect statement

The daily `VACUUM` operation issued by `CleanupWorker` at `src/workers/cleanup_worker.py:205` holds an EXCLUSIVE database lock for 4-21 seconds, blocking every writer in the system during that window. Today's general.log shows four VACUUM-driven `DB_LOCK_WAIT` events (waits of 3924 / 16384 / **21009** / 4114 ms). When the freeze fires, it cascades through F2/F3/F5/F8 — all worker ticks that touch the DB pile up behind the VACUUM.

## 2. Today's evidence

```
08:27:19  DB_LOCK_WAIT wait_ms=3924   holder=execute:VACUUM
09:24:09  DB_LOCK_WAIT wait_ms=16384  holder=execute:VACUUM
11:32:01  DB_LOCK_WAIT wait_ms=21009  holder=execute:VACUUM   <-- 21s freeze
13:00:09  DB_LOCK_WAIT wait_ms=4114   holder=execute:VACUUM
```

Plus regular non-VACUUM 5-min-cadence DB_LOCK_WAIT events at 2-2.7s from `fetch_all` (likely competing with an `executemany` writer on some hot table). These are not VACUUM-related but show baseline contention.

## 3. Current DB configuration

```
PRAGMA journal_mode      -> wal     (good)
PRAGMA wal_autocheckpoint -> 1000   (every 1000 pages — good)
PRAGMA auto_vacuum       -> 0       (NONE — must use VACUUM command)
```

WAL mode allows concurrent reads + one writer; checkpointing flushes to main DB periodically. But `VACUUM` itself takes an EXCLUSIVE lock that blocks even the WAL writer. So WAL doesn't help during VACUUM.

`auto_vacuum = 0` means SQLite does NOT incrementally reclaim freelist pages. The only way to shrink the DB and reclaim space is the full `VACUUM` command — which rewrites the entire DB file.

## 4. VACUUM caller architecture

### Daily VACUUM
`src/workers/cleanup_worker.py:198-212` — once per UTC day with 3-attempt retry loop:

```python
today = now_utc().strftime("%Y-%m-%d")
if today != self._last_vacuum_date:
    for attempt in range(3):
        try:
            if attempt > 0:
                await _aio.sleep(attempt * 5)
            await self.db.execute("VACUUM")
            self._last_vacuum_date = today
            ...
```

CleanupWorker tick interval: 3600 s (hourly). The day-key check guards against running more than once per day.

### Boot VACUUM
`src/workers/manager.py:1075` — runs once at WorkerManager startup. Same blocking semantics.

## 5. DB sizing context

Current `data/trading.db`: 172 MB. Default SQLite page size 4 KB → ~44,000 pages. Most of the size is from `klines` (with retention), `ticker_cache` (high-volume), and historical `trade_log` / `trade_thesis` / `trade_intelligence`. Cleanup deletes rows daily; without VACUUM, deleted rows leave freelist pages that bloat the file.

## 6. Why a 21-second freeze cascades through all workers

Every worker that issues an `execute` or `executemany` blocks on `_locked` (database/connection.py:218) when an exclusive lock is held. The 21s VACUUM at 11:32 today coincided with kline / ticker_cache batch writes; those writers queued up to 21s waiting. During that window:

- `price_worker` ticker_cache writes stall.
- `kline_worker` candle writes stall (matches F2 slow ticks).
- `profit_sniper` per-position state writes stall (matches F3 slow ticks).
- `sl_gateway` SL rate-limit state updates stall (matches F5 thrash).
- `position_watchdog` plan / TIAS writes stall.

All of these emit slow-tick or rate-limit-thrash warnings during the freeze window. F4 is the root cause of a chunk of F2/F3/F5 events.

## 7. Fix options

### Option A — `PRAGMA auto_vacuum = INCREMENTAL` + `PRAGMA incremental_vacuum(N)` (recommended)

Switch the DB to incremental auto_vacuum mode. SQLite tracks freelist pages automatically; `PRAGMA incremental_vacuum(N)` reclaims up to N pages on demand, typically completing in milliseconds for small N. Replace the daily full VACUUM with hourly `incremental_vacuum(N)` calls in `CleanupWorker`.

- One-time migration required: existing DB has `auto_vacuum=0`. To switch, SQLite needs ONE full VACUUM after setting `PRAGMA auto_vacuum=INCREMENTAL`. That's a one-shot 21-s freeze, then incremental forever.
- Pros: eliminates the periodic 21-s freeze. Each incremental call reclaims 1000-4000 pages (4-16 MB) in <1s. No more cascade.
- Cons: one-time migration freeze (acceptable in scheduled maintenance window). New DBs default to NONE auto_vacuum unless we add the PRAGMA at create time.

### Option B — Schedule full VACUUM during known idle window

Keep `VACUUM` but only run during 04:00-04:30 UTC (or whatever the operator confirms is lowest-activity). Outside that window, skip.

- Pros: simplest change (date+time guard around the existing VACUUM block).
- Cons: still has the freeze, just at a known time. Doesn't eliminate the cascade — just hides it from waking hours.

### Option C — Split high-volume tables to a separate DB file

Move ticker_cache, prices (high-write tables) to a separate SQLite file. The hot DB's `VACUUM` only operates on slow-write tables.

- Pros: addresses the cascade architecturally — VACUUM on the slow DB doesn't block ticker writes.
- Cons: large refactor across all repos. Two connection pools. Out of scope for T1-4.

## 8. Investigation conclusions

1. VACUUM is the root cause. 21s freezes confirmed live today.
2. WAL is already on; `auto_vacuum=0` is the missing piece.
3. Option A (PRAGMA auto_vacuum=INCREMENTAL + incremental_vacuum hourly) is the standard SQLite pattern for production hot DBs. Eliminates the cascade after a one-time migration.
4. One-shot transition VACUUM is unavoidable (SQLite requires it once to start tracking freelist) — acceptable in maintenance window.
5. The 5-min-cadence non-VACUUM DB_LOCK_WAIT events are a separate baseline contention — not T1-4 scope. Probably the cleanup_worker hourly tick interacting with a checkpoint.

Phase 2 proposal follows.
