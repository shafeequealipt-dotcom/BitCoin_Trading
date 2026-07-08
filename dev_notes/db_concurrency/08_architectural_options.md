# 08 — Architectural Options A–F (Detailed)

Each option is evaluated against four axes: cascade reduction, migration complexity, risk, and aim preservation (aggressive opportunity exploitation per spec Rule 8).

## Option A — Status quo (single connection, single asyncio.Lock)

### What it is

One `aiosqlite.Connection`. One `asyncio.Lock`. Every operation acquires the lock, runs through the single connection's thread queue, releases.

### Strengths
- Zero migration risk.
- Single integrity boundary — atomicity is implicit and trivial.
- Lowest memory footprint.

### Weaknesses
- ANY slow operation blocks everything else.
- Reads compete with reads.
- Reads compete with writes.
- Writes compete with reads.
- Cascade signature is intrinsic.

### Cascade reduction: 0% (status quo).

### Migration complexity: zero.

### Decision: rejected. This is the bottleneck the operator wants removed.

---

## Option B — Reader pool + single writer (CHOSEN)

### What it is

- N independent `aiosqlite.Connection` instances opened by `DatabaseManager.connect()` for READS, managed by an `asyncio.Queue`-backed bounded pool.
- 1 dedicated `aiosqlite.Connection` for WRITES, gated by a single `asyncio.Lock`.
- All connection-level PRAGMAs applied to every connection at open time via a shared `_apply_pragmas(conn)` helper.
- The public `DatabaseManager` API (`execute`, `executemany`, `fetch_one`, `fetch_all`, `transaction`, `checkpoint`) preserved bit-for-bit. Internal dispatch:
  - `fetch_one` / `fetch_all` → acquire reader → SELECT → release reader.
  - `execute` / `executemany` → acquire writer lock → DML → commit → release.
  - `transaction()` → writer lock for context lifetime (matches SQLite single-writer).
  - `checkpoint()` → writer lock (PRAGMA wal_checkpoint must run on a writer).

### Sketch of new `DatabaseManager`

Internal structure:

```text
DatabaseManager
├── connect() / disconnect() — same external behavior
├── execute / executemany / fetch_* / transaction / checkpoint — same signatures
│
├── _engine: _PooledDatabaseEngine | _LegacyEngine  (chosen at connect)
│
├── _LegacyEngine
│   ├── single aiosqlite.Connection
│   ├── single asyncio.Lock
│   ├── _locked() context manager (unchanged from today)
│   └── instrumentation: _wait_samples, _caller_wait_counts, _last_holder
│
└── _PooledDatabaseEngine
    ├── _writer_conn: aiosqlite.Connection
    ├── _writer_lock: asyncio.Lock
    ├── _writer_holder_state (same instrumentation as _LegacyEngine)
    ├── _reader_pool: _ReaderPool
    │   ├── _conns: list[aiosqlite.Connection] (size N)
    │   ├── _available: asyncio.Queue[aiosqlite.Connection]
    │   ├── _hard_cap: int (default 2N)
    │   ├── _stats: {acquires, waits_total_ms, exhausted_count, growths}
    │   └── _apply_pragmas (called on each open)
    └── _log_lock_histogram() — emits DB_LOCK_HIST + CONN_POOL_STATS
```

`_LegacyEngine` is the current code path repackaged into a class. `_PooledDatabaseEngine` is the new path. The `DatabaseManager` facade dispatches based on `settings.database.concurrency_model`.

### How it works under WAL

- WAL: many readers + one writer concurrently at the engine level.
- Each reader: own aiosqlite.Connection → own thread → own sqlite3.Connection. They do not contend at the aiosqlite or asyncio level.
- Single writer: avoids `SQLITE_BUSY` retries between in-process writers. The writer connection's commit appends to WAL; readers see their existing snapshot until the next read.

### Estimated cascade reduction

Cascade events drop to near-zero on the hot path. Reads no longer block reads. Reads no longer block writes. Writes still serialize on the writer lock, but writer-on-writer contention is bounded by:

- 5 writers fire on different cadences (kline_worker 5 min, sniper 5 s, watchdog 10 s, ticker_buffer 500 ms, regime 5 min).
- Most writes are single-row INSERTs/UPDATEs (sub-millisecond).
- The two batched writers (`save_klines` and `save_tickers_batch`) already chunk with `await asyncio.sleep(0)` between chunks, freeing the writer lock briefly between chunks.

Worst-case writer contention: a kline tick (chunked 9000-row executemany) overlapping with a sniper tick (single-row INSERT). Sniper waits until the next chunk boundary — at most ~50 ms. Far below today's 44 s cascade.

### Per-file migration scope

| File | Change |
|---|---|
| `src/database/connection.py` | Main change: add `_PooledDatabaseEngine` class, refactor `DatabaseManager` to dispatch via settings flag |
| `src/config/settings.py` | Add `concurrency_model: str` and `reader_pool_size: int` to `DatabaseSettings`; extend `_build_database` |
| `config.toml` | Declare `concurrency_model = "single_lock"` and `reader_pool_size = N` under `[database]` |
| `src/workers/cleanup_worker.py` | Extend `log_lock_histogram()` emit to include `CONN_POOL_STATS` |
| `tests/database/test_connection_pool.py` | New unit-test file |
| `tests/stress/test_db_concurrency_stress.py` | New stress-test file |
| Everything else | Untouched |

Total: 4 modified, 2 new. 117 importing files = ZERO changes.

### Effort

Phase 3: 7-10 working days per the spec.

### Risks

| Risk | Mitigation |
|---|---|
| Reader pool exhausts under burst | Dynamic growth up to hard cap (2N). `CONN_POOL_EXHAUSTED` warned. Default N chosen from stress-test results. |
| WAL multi-connection issue at scale | aiosqlite source already verified. Stress tests confirm under production-like load. |
| Reader sees stale snapshot before writer commit | WAL provides snapshot isolation by design. Document for callers that need fresh-after-write. |
| Reader-writer deadlock | Architecture forbids holding a reader while waiting for the writer. Code review + lint check during 3.4. |
| Hidden DM consumer breaks | 117 files cataloged; public API preserved bit-for-bit. Per-worker live verification in 3.6. |

### Rollback

Feature-flag controlled (`concurrency_model = "single_lock" | "reader_pool"`). Cutover (Phase 3.7) flips one line in `config.toml`. Revert is a flip + service restart. No code revert needed.

### Stress-test prediction

Five scenarios from `09_stress_test_scenarios.md`. Expected outcomes:

- Klines burst: cascade events 0, lock waits < 100 ms p95.
- New trade burst: lock waits < 50 ms p95.
- Dashboard read storm: completes in < 200 ms total (vs. multi-second today).
- Combined burst: 0 cascades, lock waits < 500 ms p95.
- Sustained mixed load: 0 cascades over 30 min, no pool exhaustion at chosen size.

---

## Option C — Per-domain `DatabaseManager` instances

### What it is

Group tables by domain. Possible split:

- Trade-state: orders, positions, trade_log, trade_history, trade_thesis, trade_intelligence, strategy_trades.
- Time-series: klines, ticker_cache, sniper_log, position_snapshots, account_snapshots, funding_rates, open_interest, fear_greed_index, coin_regime_history.
- Telemetry: event_log, claude_decisions, market_snapshots, orderbook_snapshots.
- Learning: strategy_performance, signal_accuracy, pattern_log, brain_decisions, discovered_patterns, generated_strategies, pattern_occurrences.
- Telegram/config: price_alerts, scheduled_reports, watchlists, user_preferences, conversation_log, trade_journal, active_strategies, session_log, schema_version.

Each domain has its own DatabaseManager, its own connection, its own lock. They all point to the same SQLite file.

### Why rejected for v1

- 117 importing files would need to know which manager to use. Massive caller migration.
- Cross-domain reads (Telegram /dashboard joins multiple domains) become awkward — either union-query at the application level or share a connection.
- Locks at the application level multiply, but the engine-level write serialization is unchanged — all writers still contend for the SQLite write lock through `busy_timeout`. The application-level split adds complexity without changing the engine ceiling.
- Cycle detection: if two coroutines each hold their domain's lock and wait on the other, deadlock. Detection adds runtime overhead.

### Reserve as

Phase-5+ enhancement layered on top of Option B, IF specific domains prove to remain hot under B. Most likely candidates would be the trade-state domain and the time-series domain, since the time-series writes (klines, ticker_cache) dominate volume and the trade-state writes are critical-path.

### Migration complexity

- 4-6 weeks.

---

## Option D — Dedicated writer task + reader pool

### What it is

All writes enqueued onto an `asyncio.Queue` consumed by a single writer task. Readers use a pool.

### Why rejected

- Writer task crash = lost durability for queued writes. Process restart loses any writes that were buffered.
- Backpressure semantics complex — when queue full, caller blocks or fails?
- Returning the cursor / lastrowid to the caller requires per-write futures, adding indirection.
- Trade-open flow that does `INSERT` then later reads back the rowid would need to await the writer task's response — same latency as Option B's writer lock, but with more moving parts.

### When this would win

If writes outnumbered reads 10x AND read latency was more important than write latency AND durability could tolerate occasional loss. Not our profile.

---

## Option E — Hybrid: B + C

### What it is

Per-domain managers, each internally using a reader pool + writer connection.

### Why rejected for v1

- Sum of B and C migration costs. 8+ weeks.
- The incremental cascade-reduction over Option B alone is small at current scale (3-9 open positions, ~100 ops/min peak). At 10x scale, this becomes attractive.

### Reserve as

Phase-5+ if Option B's writer lock becomes the residual bottleneck after migration.

---

## Option F — Multi-process SQLite WAL

### What it is

Workers become OS processes. Each opens its own aiosqlite.Connection. Multiple writer processes contend at the engine level via `busy_timeout`.

### Why rejected

- Out of scope per spec.
- Requires full re-architecture: process supervision, IPC, shared in-memory state moves to shared memory or another store.
- The single SQLite file is still the bottleneck at the engine level (one writer at a time at the file lock).

### When this would win

If the project moves to PostgreSQL OR if SQLite scales out across multiple files (vertical sharding by domain across multiple .db files).

---

## Summary table

| Option | Cascade↓ | Migration | Risk | Aim preserved | Decision |
|---|---|---|---|---|---|
| A | 0% | 0 days | — | partial | rejected — status quo |
| B | ~95% | 7-10 days | low | yes | **CHOSEN** |
| C | ~60% | 20-30 days | medium | partial | reserved |
| D | ~80% | 15-20 days | medium-high (durability) | partial | rejected |
| E | ~98% | 40+ days | medium | yes | reserved |
| F | ~95% | 60+ days | high | partial | out of scope |

End of `08_architectural_options.md`.
