# Database Concurrency Model — Developer & Operator Reference

This page describes the production DB concurrency model after the
`fix/db-concurrency-refactor` work landed on 2026-05-14 (commits
`c913585`..`baca07c`). Authoritative for adding new workers / repositories /
DB-using services to the codebase.

## 1. What the model is

A single `DatabaseManager` instance is constructed at boot in
`workers.py:147`, `brain.py:50`, and `src/mcp/server.py:53`, and passed
through `ServiceContainer` (`src/core/container.py:21`) to every consumer.

Internally, `DatabaseManager` is a facade. At `connect()` time it
instantiates one of two engines based on
`settings.database.concurrency_model`:

- `"single_lock"` (`_LegacyEngine`) — one `aiosqlite.Connection`, one
  `asyncio.Lock`. Every read and write serialises on the lock. Retained
  for revert capability; default in test contexts.
- `"reader_pool"` (`_PooledDatabaseEngine`) — `reader_pool_size` independent
  reader connections in an `asyncio.Queue`-backed pool, plus one dedicated
  writer connection guarded by a single `asyncio.Lock`. This is the
  production setting since 2026-05-14 17:16.

### Why a pool + a single writer

- SQLite WAL allows N concurrent readers + 1 writer at the engine level.
  The pool matches that semantics exactly.
- aiosqlite has no driver-level lock — each `aiosqlite.Connection` is its
  own Python thread + its own `sqlite3.Connection`. Multiple connections
  do not contend at the aiosqlite layer.
- Two writers from the same process would still serialise at the SQLite
  engine via `PRAGMA busy_timeout`. The explicit writer lock makes that
  serialisation predictable and observable instead of best-effort.

## 2. Public API (unchanged)

The six methods callers use are identical under both engines. New code
should NOT reach inside to touch `_engine` or its internals.

```python
db = DatabaseManager(path, ...)
await db.connect()
...
await db.execute(sql, params)              # writer-locked, auto-commit per call
await db.executemany(sql, params_list)     # writer-locked, auto-commit per call
row = await db.fetch_one(sql, params)      # reader-pool acquire, no commit
rows = await db.fetch_all(sql, params)     # reader-pool acquire, no commit
async with db.transaction() as conn:       # writer-locked, commit on success
    await conn.execute(...)
    await conn.execute(...)                # multiple writes in one atomic group
result = await db.checkpoint("PASSIVE")    # writer-locked PRAGMA wal_checkpoint
db.log_lock_histogram()                    # called hourly from cleanup_worker
await db.disconnect()
```

## 3. Adding a new worker that needs DB access

1. Construct the worker via `ServiceContainer.services["db"]`. Do NOT
   instantiate a new `DatabaseManager`.
2. Use either a repository (`src/database/repositories/<your>_repo.py`)
   or call `db` directly via the six public methods. Repositories are
   preferred for tables that already have one — keeps the SQL central.
3. Read-only workers: use `fetch_one` / `fetch_all`. They acquire a
   pooled reader connection automatically.
4. Writers: use `execute` / `executemany`. They acquire the writer lock
   automatically.
5. Mixed-tick workers (most workers): `fetch_*` for reads, `execute(many)`
   for writes. Do NOT hold any reader across a write — release the
   reader by completing the `fetch_*` call before starting the write.
6. For batch writes > 100 rows, use `executemany`. Chunk the batch with
   `await asyncio.sleep(0)` between chunks (see
   `market_repo.save_klines` for the canonical pattern) — this releases
   the event loop briefly between chunks, letting other workers' reads
   land between chunks.
7. Set the worker's `tick_interval` to something the system can sustain.
   A 5-second tick that takes 4 seconds per tick is a bottleneck.

## 4. Adding a new repository

1. New file in `src/database/repositories/<name>_repo.py`.
2. Constructor accepts `db: DatabaseManager`.
3. One class with async methods, one method per logical DB operation.
4. Each method calls one of `db.execute / executemany / fetch_one / fetch_all`.
5. No transaction() calls unless you have a genuine multi-statement
   atomic write group. The system currently has zero. If you add one,
   keep the transaction body short — do NOT `await` external services
   (HTTP, Bybit, Claude) while holding the writer lock.
6. Register it on `ServiceContainer.services` so other services can find
   it. Pattern in `src/core/container.py`.

## 5. Transaction-scoping best practices

The writer lock is held for the duration of a `transaction()` context.
Holding it for too long blocks every other writer in the process. Rules:

- The body must be **synchronous-only awaits on SQLite** — no Bybit
  HTTP, no Claude subprocess, no Reddit fetch, no any external I/O.
  External I/O can take seconds; holding the writer lock for seconds
  re-creates the cascade signature the refactor eliminated.
- Group only writes that MUST land atomically. If you're persisting
  three unrelated rows, three separate `execute` calls are fine — they
  are atomic individually (auto-commit per call) and the writer lock
  releases between them so other writers can interleave.
- If you need to read inside a transaction (e.g. to look up an FK target
  before inserting), use the writer connection that `transaction()`
  yields — do NOT call `db.fetch_*` from inside the context (that would
  acquire a separate reader, see stale snapshot under WAL).

```python
# GOOD — short atomic group, no external I/O
async with db.transaction() as conn:
    cur = await conn.execute(
        "INSERT INTO trade_log (...) VALUES (...)", params
    )
    new_id = cur.lastrowid
    await conn.execute(
        "INSERT INTO strategy_trades (trade_log_id, ...) VALUES (?, ...)",
        (new_id, ...)
    )

# BAD — external await inside transaction
async with db.transaction() as conn:
    await conn.execute("INSERT INTO trade_log ...")
    response = await bybit.place_order(...)  # NO — holds writer lock for HTTP RTT
    await conn.execute("UPDATE trade_log ...")
```

## 6. WAL snapshot semantics (readers)

A pooled reader sees a snapshot of the database at the moment it begins
its first read. The snapshot persists for the duration of that
connection's open transaction inside aiosqlite (auto-committed after each
SELECT in our code — see `fetch_one` / `fetch_all`). After the
connection returns to the pool, its next checkout starts a fresh
snapshot.

If a worker needs **fresh data after a known recent write**, the
straightforward pattern is to issue the read AFTER the write completes
(awaiting the write returns to the event loop, so the write's commit
has landed in the WAL). The next `fetch_*` will see it.

If a worker needs to read and then write atomically against the same
row (read-modify-write), use `transaction()` so the read and write run
on the same writer connection. Do not split the read into a separate
`fetch_*` call — that would acquire a pooled reader on a snapshot taken
before the write, opening a lost-update window.

## 7. Pool sizing tuning

`reader_pool_size` is in `[database]` of `config.toml`. Hard cap on
dynamic growth is `2 * reader_pool_size` (set at engine init).

Watch these CONN_POOL_STATS fields in the hourly emit:

- `peak_in_use` — the highest concurrent reader count observed in the
  emit window. If this consistently approaches or exceeds
  `reader_pool_size`, bump the size.
- `exhausted_count` — number of times the pool drained to hard_cap and a
  coroutine had to queue. Should be 0 in normal operation. Non-zero
  means the hard cap is too low.
- `growths` — number of times the pool grew dynamically. Small values
  are fine; consistently > 0 each emit window suggests `reader_pool_size`
  is undersized for the average load.

Sizing decision tree:

- `peak_in_use < reader_pool_size`: pool is right-sized or oversized.
- `peak_in_use > reader_pool_size`, `growths > 0`, `exhausted_count == 0`:
  dynamic growth handling load fine; consider bumping size to avoid
  the per-tick growth cost.
- `exhausted_count > 0`: hard cap insufficient. Bump size; consider also
  bumping the workload's expected concurrency.

## 8. Switching engines (runtime)

Cutover and revert are both single-line edits:

```toml
[database]
concurrency_model = "reader_pool"   # or "single_lock"
```

Then restart `trading-workers` and `trading-mcp-sse`. No code revert
needed. Env override:

```
DATABASE_CONCURRENCY_MODEL=single_lock systemctl restart trading-workers
```

(The env var takes precedence over the config file.)

## 9. Observability tags reference

| Tag | Level | Meaning |
|---|---|---|
| `DB_CONN` | INFO | Boot. Includes `engine=single_lock` or `engine=reader_pool`. |
| `DB_PRAGMAS` / `DB_PRAGMA` | INFO | Per-connection PRAGMA confirmation at boot. |
| `DB_AUTO_VACUUM_OK` / `DB_AUTO_VACUUM_NOT_INCREMENTAL` | INFO / WARN | Auto-vacuum mode probe at boot. |
| `CONN_POOL_INIT` | INFO | Pooled engine ready: readers, hard_cap, writer status. |
| `CONN_POOL_GROW` | INFO | Pool grew dynamically (size → new size). |
| `CONN_POOL_EXHAUSTED` | WARN | Pool drained to hard cap and waiter queued. |
| `CONN_POOL_WAIT` | INFO / WARN | Reader-pool acquire took > 500 ms (WARN if > 2 s). |
| `CONN_POOL_STATS` | INFO | Hourly from cleanup_worker. Stats described above. |
| `DB_LOCK_WAIT` | WARN | Single-lock engine: lock acquire > threshold. |
| `WRITER_LOCK_WAIT` | WARN | Pooled engine: writer lock acquire > threshold. |
| `CASCADE_DETECTED` | WARN | Lock acquire > 5 s. Should be zero post-refactor. |
| `DB_LOCK_BREAKDOWN` | WARN | Top-5 contributors emitted alongside CASCADE_DETECTED. |
| `DB_LOCK_HIST` | INFO | Hourly percentile histogram for the lock used by current engine. |
| `WAL_CHECKPOINT` / `WAL_CHECKPOINT_BUSY` / `WAL_CHECKPOINT_NORESULT` | INFO / WARN | `cleanup_worker` PASSIVE checkpoint results. |
| `DB_ERR` | ERROR | Operation failed after 3 retries. |

## 10. Stress testing

Run the harness from `tests/stress/test_db_concurrency_stress.py`
manually:

```bash
scripts/run_db_concurrency_stress.sh                  # short scenarios 1-3
STRESS_KLINES_ROWS=5000 scripts/run_db_concurrency_stress.sh   # spec-sized burst
STRESS_LONG=1 scripts/run_db_concurrency_stress.sh    # +5-min combined + 30-min sustained
```

Tests are marked `@pytest.mark.stress` and skip in default CI runs.

## 11. Known follow-ups

- **Phase 3.9** — once Phase 4 verifies 1 week stable, delete the
  `_LegacyEngine` and the `"single_lock"` config option.
- **Phase 5.1 / 5.2** — drop duplicate indexes on `fear_greed_index` and
  `position_snapshots` (see `phase5_cleanup_targets.md`).
- **Phase 5.3** — stop polling zero-row tables (`price_alerts`,
  `scheduled_reports`).
- **Phase 5.4** — remove or redirect `brain_decisions` reads in Telegram
  handlers (table is never written).
- **Future** — if scale grows 10x and the writer lock becomes the
  residual bottleneck, evaluate per-domain DatabaseManager instances
  (Option C in `08_architectural_options.md`).

End of `concurrency_model_docs.md`.
