# Phase 0 — Issue 1: D-3 SQLite Lock Contention Investigation

**Date:** 2026-04-27
**Project root:** `/home/inshadaliqbal786/trading-intelligence-mcp`
**Brief reference:** `IMPLEMENT_FIVE_CRITICAL_FIXES_PROFESSIONAL.md` § Issue 1, Phase 1

## A — The mechanism

The DatabaseManager exposes a **single global `asyncio.Lock`** through which every read and every write serialises. The lock is acquired by the `_locked()` context manager at `src/database/connection.py:116-160`. The wrapper logs `DB_LOCK_WAIT` whenever wait time meets or exceeds `DB_LOCK_WAIT_WARN_MS = 1000` (line 31, log emit at 149-152).

`MarketRepository.save_klines` (`src/database/repositories/market_repo.py:43-95`) builds one parameter list for the entire batch and calls a single `executemany` at line 80:

```python
sql = """
    INSERT OR IGNORE INTO klines
    (symbol, timeframe, timestamp, open, high, low, close, volume, turnover)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
params = [ ... per-row tuples ... ]
await self._db.executemany(sql, params)
```

Inside `DatabaseManager.executemany` (`connection.py:229-251`), the lock is acquired once and held for the full executemany + commit window:

```python
async with self._locked(f"executemany:{sql[:48]}"):
    await self.db.executemany(sql, params_list)
```

`KlineWorker.tick()` calls `market_service.get_klines()` (`src/workers/kline_worker.py:181`), which in turn calls `MarketRepository.save_klines` (`src/trading/services/market_service.py:210`). The worker iterates ~50 symbols × multiple timeframes per tick; each save holds the global lock for the duration of its executemany. Other workers wanting the lock queue behind it.

Symptom chain:
- `STRAT_PREFETCH_CRITICAL el=35733ms db=14757ms` — strategy_worker waited ~15 s on the lock for its prefetch.
- `KLINE_FETCH el=1032990ms` — 17-minute kline fetch in the worst observed case (bursts of contention back-to-back).
- `BASE_WORKER_TICK_SLOW` — every other worker delayed.
- StrategyWorker processes 5/50 coins because the prefetch was so slow, the freshness gate skipped 45 of them.

`scanner_worker.py:278-283` also runs an `executemany` against `active_universe` under the same lock; it is a smaller batch (≈50 rows) but contributes.

## B — The dependencies

Workers that read/write `trading.db` and therefore queue behind the writer lock:

| Worker | Writes? | Reads? | Hot? |
|---|---|---|---|
| kline_worker | klines (heavy) | yes | YES (every 45 s) |
| scanner_worker | active_universe | yes | every 5 min |
| strategy_worker | — | klines + caches | yes (every 5 min) |
| structure_worker | — | klines | yes (every 60 s) |
| signal_worker | signals + sentiment | yes | every ~ minute |
| regime_worker | regime_history | yes | minutes |
| altdata_worker | funding/oi/fg | yes | minutes |
| cleanup_worker | DELETE retention; VACUUM; PRAGMA wal_checkpoint | yes | hourly |
| position_watchdog | reads positions | yes | seconds |
| profit_sniper | sniper_log | yes | 5 s |

Cascading failures observed:
- Strategy_worker prefetch starvation → cycle decisions on 5-17 min stale data.
- Universe-flap rotation-in coins miss the freshness window → STRAT_SKIP_STALE storm.
- BASE_WORKER_TICK_SLOW for ≥ 6 workers in the same minute (live observation 2026-04-26).

The `protected_tables.py` guard performs a pre-flight check inside `DatabaseManager.execute/executemany` BEFORE acquiring the lock, so it is not in the hot path of contention. Untouched by this work.

## C — The constraints

- **Schema:** klines indexed on `(symbol, timeframe, timestamp)`; can't drop the index (used by `market_repo.get_klines` and `kline_worker` freshness queries).
- **WAL mode:** must remain. Concurrent readers don't block on the writer in WAL — but with our single-Lock architecture, all ops still serialise in Python.
- **PRAGMA defaults are already production-correct** (verified live; see Section D).
- **`INSERT OR IGNORE` idempotency:** must be preserved across the chunking change.
- **PROTECTED tables:** guard remains in force; klines is not protected so writes are allowed; no fix touches this.

## D — Live diagnostics (captured 2026-04-27)

```
journal_mode    : wal
synchronous     : 1 (NORMAL)
wal_autocheckpoint : 2000 (frames)
journal_size_limit : 104857600 (100 MiB)
busy_timeout    : 10000 ms
cache_size      : -65536 (64 MiB)
mmap_size       : 268435456 (256 MiB)
```

File state:
```
trading.db       156.4 MB
trading.db-wal   104.9 MB  (PINNED at journal_size_limit cap)
trading.db-shm   327 KB
```

The WAL is pinned at the cap because `wal_autocheckpoint=2000` is opportunistic — it only fires when no readers hold a snapshot. Under continuous worker load the snapshot-free condition is rare. `cleanup_worker` calls `db.checkpoint(mode="PASSIVE")` only hourly, which is insufficient under peak write load.

## E — The observability gap

Today, around DB ops, we emit:

- `DB_LOCK_WAIT` only when wait_ms ≥ 1000 (`connection.py:149-152`)
- `DB_LOCK_HIST` once per hour from cleanup_worker (`connection.py:177-181`, called at `:101` of cleanup)
- `DB_CONN`, `DB_PRAGMAS`, `DB_ERR` (init / errors)
- `KLINE_FETCH`, `KLINE_WRITE_LAG`, `KLINE_FRESHNESS_WARN`, `KLINE_TICK_SUMMARY` (kline_worker)
- `STRAT_PREFETCH_SLOW`, `STRAT_PREFETCH_CRITICAL` (strategy_worker)
- `BASE_WORKER_TICK_SLOW` (any worker)

What is **not** logged today:
- The current lock holder when a wait fires (caller name, op tag).
- Per-call DB_LOCK_WAIT percentiles roll-up.
- WAL checkpoint outcome on every iteration (only the hourly call gets a one-line log; no busy/log/ckpt counters in the rolling stream).
- Per-chunk save latency (because there are no chunks today).

Phase 1 will add:
- `KLINE_SAVE_CHUNKED | sym=... tf=... rows=N chunks=K avg_chunk_ms=... el_ms=...`
- `WAL_CHECKPOINT | mode=PASSIVE busy=B log=L ckpt=C wal_size_before=...MB wal_size_after=...MB`
- `WAL_CHECKPOINT_ESCALATE | reason=busy_count_3 to=TRUNCATE`
- `DB_LOCK_WAIT_SUMMARY | window=60s p50=... p95=... max=... top_callers=[...]`
- Enriched `DB_LOCK_WAIT` payload with `holder=`, `last_holder=`, `op=`, `caller=`.

## F — The verification approach

After deployment, 60-minute trial measuring:

| Metric | Target | Source |
|---|---|---|
| `KLINE_TICK_SUMMARY` el p50 | < 5 s (was 13 s) | `grep KLINE_TICK_SUMMARY workers.log` |
| `KLINE_TICK_SUMMARY` el p95 | < 10 s (was 20 s) | same |
| `STRAT_PREFETCH_CRITICAL` events / hour | 0 | grep |
| StrategyWorker coins / tick | 50 (was 5) | `grep STRAT_L1` |
| WAL size sustained | < 50 MB | `ls -la data/trading.db-wal` |
| `DB_LOCK_WAIT > 5000ms` events / hour | 0 | new instrumentation |

Edge cases that could break the verification:
- Bursts at H1/H4 boundaries (00:00 UTC) when many timeframes save together — verify chunked saves still hold p95 in this window.
- Cleanup_worker hourly VACUUM — independent path; should not regress.
- Universe-flap-induced KLINE_BACKFILL — the per-tick chunked saves should keep this contained without amplifying contention.

## G — The rollback path

Each commit reverts independently:

1. **Chunked saves** (`market_repo.py` + `settings.py` + `config.toml`): revert reverts to the original single-executemany behaviour; no data migration needed because INSERT OR IGNORE is idempotent across either form.
2. **WAL checkpoint scheduler** (`kline_worker.py` + `settings.py` + `config.toml`): revert removes the periodic call; cleanup_worker's hourly checkpoint remains.
3. **DB_LOCK_WAIT enrichment** (`connection.py` + `settings.py` + `config.toml`): revert restores the 1000 ms warning baseline.

Recovery is fast: `git revert <hash>`, restart workers, observe metrics return to prior baseline.

## Recommendation

**Combination fix: Approach A + Approach C.** Approach B (read/write split) is rejected — readers do not block on writers in WAL; the bottleneck is writes serialising on each other. Approach D (PRAGMAs) is already done in a prior phase. The observed pinned WAL is the smoking gun for Approach C; the executemany hold time is the smoking gun for Approach A.

Defaults to ship:
- `[database] kline_save_chunk_size = 500`
- `[database] wal_checkpoint_every_n_kline_ticks = 50`
- `[database] wal_checkpoint_truncate_after_busy_count = 3`
- `[database] db_lock_wait_threshold_ms = 1000`
