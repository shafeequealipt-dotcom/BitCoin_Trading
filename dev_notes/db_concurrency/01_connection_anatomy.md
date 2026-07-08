# 01 — Connection Layer Anatomy

Target: `src/database/connection.py` (533 lines, current commit `461f7c6`).
Module exports: `DatabaseManager`, `_extract_external_caller_frame`, `DB_LOCK_WAIT_WARN_MS`, `DB_LOCK_HIST_SAMPLE_LIMIT`, `DB_CASCADE_THRESHOLD_MS`.

## 1. Module-level constants

| Constant | Value | File:Line | Purpose |
|---|---|---|---|
| `DB_LOCK_WAIT_WARN_MS` | 1000.0 | connection.py:38 | Threshold (ms) above which `DB_LOCK_WAIT` warns. Fallback when no per-instance value is passed. |
| `DB_LOCK_HIST_SAMPLE_LIMIT` | 1000 | connection.py:39 | Cap on `_wait_samples` ring buffer used by `log_lock_histogram()`. |
| `DB_CASCADE_THRESHOLD_MS` | 5000.0 | connection.py:50 | Threshold (ms) above which `CASCADE_DETECTED` warns and `DB_LOCK_BREAKDOWN` emits the top-5 contributors. |

The two thresholds form a two-tier instrumentation strategy: `DB_LOCK_WAIT` fires on the merely-slow tail (1 s) so operators see contention as it builds; `CASCADE_DETECTED` fires only when the wait is severe enough to cause downstream worker overdue, keeping the cascade signal rare and high-signal.

## 2. Helper function — `_extract_external_caller_frame()`

Defined: connection.py:53–79.

Returns the first `file:line` in the call stack that is OUTSIDE `src/database/connection.py`. Walks `traceback.extract_stack(limit=20)` from deepest frame upward, returning the basename of the first frame whose filename does not include `database/connection.py`. Returns `"unknown"` on any failure.

Used by the `_locked` wrapper to attribute slow lock acquisitions to the actual upstream worker (Phase 1 D-3 fix). Observation from log analysis: the walker often resolves to `contextlib.py:204` because `async with self._locked(...)` is invoked from inside the `asynccontextmanager` decorator, which masks the user frame. This is a known instrumentation limitation, not a code bug. Pooled-engine implementation will copy this helper but raise the `limit=20` cap to 40 to skip past the contextlib frames.

## 3. `class DatabaseManager`

Constructor: connection.py:95.

### 3.1 Constructor parameters

| Param | Type | Default | Source |
|---|---|---|---|
| `db_path` | `str` | required | `settings.database.path` |
| `wal_mode` | `bool` | `True` | `settings.database.wal_mode` |
| `lock_wait_warn_ms` | `float` | `DB_LOCK_WAIT_WARN_MS` | `settings.database.db_lock_wait_threshold_ms` (D-3 plumbing) |

### 3.2 Instance state

| Field | Type | Purpose |
|---|---|---|
| `self.db_path` | str | DB file path |
| `self.wal_mode` | bool | Whether to set journal_mode=WAL on connect |
| `self._db` | `aiosqlite.Connection \| None` | The single open connection |
| `self._lock` | `asyncio.Lock` | THE serialization point (line 104) — every operation acquires this |
| `self._lock_wait_warn_ms` | float | Per-instance warn threshold |
| `self._current_holder` | `str \| None` | Op tag of the coroutine HOLDING the lock right now |
| `self._last_holder` | `str \| None` | Op tag of the previous holder (preserved across release so the next waiter can name who blocked them) |
| `self._wait_samples` | `deque[float]` | Bounded rolling buffer for percentile reports |
| `self._caller_wait_counts` | `Counter[str]` | Per-caller acquisition counts (bounded to 64 keys; smallest evicted) |
| `self._caller_wait_total_ms` | `Counter[str]` | Per-caller total wait time |

### 3.3 Public methods

#### `connect()` — connection.py:127

Steps:

1. `aiosqlite.connect(self.db_path)` — opens the single connection (line 130).
2. `row_factory = aiosqlite.Row` (line 131).
3. Sets 10 PRAGMAs in order (lines 132–147):
   - `PRAGMA journal_mode=WAL` (conditional on `wal_mode`)
   - `PRAGMA busy_timeout=10000` (10 s engine-level retry budget)
   - `PRAGMA foreign_keys=ON` (no FK constraints declared in schema, but the pragma is set)
   - `PRAGMA cache_size=-65536` (64 MiB per-connection cache)
   - `PRAGMA synchronous=NORMAL` (WAL-paired)
   - `PRAGMA wal_autocheckpoint=2000` (every 2000 frames)
   - `PRAGMA journal_size_limit=104857600` (100 MiB WAL cap)
   - `PRAGMA temp_store=MEMORY`
   - `PRAGMA mmap_size=268435456` (256 MiB memory-mapped reads)
4. Probes `PRAGMA auto_vacuum` and warns if not INCREMENTAL (line 156–176).
5. Emits `DB_CONN`, `DB_PRAGMAS`, `DB_PRAGMA` info logs.

Failure mode: `DatabaseError` wraps any exception with `details={"path": self.db_path}`.

#### `disconnect()` — connection.py:194

Closes the aiosqlite connection if open, sets `self._db = None`, emits info log. No lock acquired here; called from shutdown path only.

#### `db` (property) — connection.py:201

Returns `self._db` or raises `DatabaseError("Database not connected.")`. Used internally only.

#### `execute(sql, params, *, force_protected=False)` — connection.py:353

Public single-statement exec. Wraps the call in `_locked(f"execute:{sql[:48]}")`, calls `self.db.execute(sql, params)`, then `self.db.commit()`. Retries up to 3 times on `"locked"` error (sleeps `0.5 * attempt` between). Returns the cursor.

Pre-flight: `assert_not_protected_destructive(sql, force=force_protected)` rejects DELETE/TRUNCATE/DROP against protected tables before lock acquire (`src/database/protected_tables.py`).

#### `executemany(sql, params_list, *, force_protected=False)` — connection.py:399

Same shape as `execute` but uses `self.db.executemany`. Holds the lock for the whole batch.

#### `fetch_one(sql, params)` — connection.py:432

Acquires lock, executes SELECT, fetches one row, releases lock. Returns `dict[str, Any] | None`.

#### `fetch_all(sql, params)` — connection.py:452

Acquires lock, executes SELECT, fetches all rows, releases lock. Returns `list[dict[str, Any]]`.

#### `checkpoint(mode="PASSIVE")` — connection.py:470

Acquires lock and runs `PRAGMA wal_checkpoint(<mode>)`. Returns `{"busy", "log_pages", "ckpt_pages", "mode"}`. Logs `WAL_CHECKPOINT` (info), `WAL_CHECKPOINT_BUSY` (warning, when busy != 0), `WAL_CHECKPOINT_NORESULT` (warning, on null cursor).

Called by `cleanup_worker` hourly.

#### `transaction()` (async context manager) — connection.py:521

Acquires lock, yields `self.db`, commits on success, rolls back on exception. Define-only — `grep -rn "transaction()" src/ tests/` returns ZERO callers as of commit `461f7c6`.

### 3.4 Internal — `_locked(op)` — connection.py:208

The single serialization point. Async context manager.

```text
1. t0 = time.monotonic()
2. await self._lock.acquire()
3. wait_ms = (time.monotonic() - t0) * 1000
4. _wait_samples.append(wait_ms)
5. prev_holder = self._last_holder
6. self._current_holder = op
7. self._last_holder = op
8. _caller_wait_counts[op] += 1
9. _caller_wait_total_ms[op] += wait_ms
10. evict smallest if len > 64
11. if wait_ms >= _lock_wait_warn_ms:
       emit DB_LOCK_WAIT (warning)
       if wait_ms >= DB_CASCADE_THRESHOLD_MS:
           emit CASCADE_DETECTED (warning)
           emit DB_LOCK_BREAKDOWN (warning) with top-5 contributors
12. yield
13. finally:
       _current_holder = None
       _lock.release()
```

Holder identity tracking is split intentionally: `_current_holder` is the dashboard view (cleared on release); `_last_holder` is what the NEXT waiter sees when it wakes up (preserved across release).

The per-caller counter dict caps at 64 keys (smallest evicted) — bounded memory. The 64 cap is also why `DB_LOCK_BREAKDOWN | total_callers=64` is the maximum total_callers value the audit observed; it's not a measurement of true contention breadth, it's the counter cap.

### 3.5 Internal — `log_lock_histogram()` — connection.py:315

Called from `cleanup_worker` once per hour. Sorts `_wait_samples`, computes p50/p95/max, builds top-5 caller summary, emits `DB_LOCK_HIST` info line. Resets `_caller_wait_counts` and `_caller_wait_total_ms` so the next window is independent.

`_wait_samples` is NOT cleared — only the per-caller counters are. That's because `_wait_samples` is a bounded deque (maxlen=1000) that naturally slides; the per-caller counters are unbounded counters that need explicit reset.

## 4. Lock acquisition pattern

Every public method on `DatabaseManager` (other than `connect()` and `disconnect()`) routes through `_locked`. The lock acquisition is exclusive; while any operation holds the lock, every other awaited operation queues on `asyncio.Lock` and resolves in FIFO order.

The lock is the only application-level serialization. aiosqlite itself serializes within a single Connection (its background thread queue), but multiple Connection instances would not contend at the aiosqlite level. Therefore the lock is purely an application choice — and the choice the refactor changes.

## 5. Connection lifecycle

- Opened: once, during `ServiceContainer.initialize()` at `src/core/container.py:35` (`await db.connect()`).
- Closed: once, during shutdown via `ServiceContainer` teardown (workers.py / brain.py / server.py exit path).
- Replaced: never. If the connection drops mid-run, the next operation raises `DatabaseError` and is retried (up to 3 attempts in `execute`/`executemany`).

The single-connection assumption is baked into the property `self.db` (line 201) which raises if `_db is None`. Any path that needs DB access must go through this property.

## 6. Error handling

| Error class | Origin | Handling |
|---|---|---|
| `DatabaseError` | `src/core/exceptions.py` | Wraps any failure from aiosqlite. Carries `details` dict. |
| `ProtectedTableViolation` | `src/database/protected_tables.py` | Raised pre-lock when DELETE/TRUNCATE/DROP targets a protected table. Terminal — never retried. |
| `"database is locked"` | SQLite engine | `execute` and `executemany` retry up to 3 times with linear backoff (0.5/1.0/1.5 s). |

No global retry policy for `fetch_one`/`fetch_all` — they raise on first failure. This is fine because reads have no side effect, so the caller can retry.

## 7. Logging emission points

| Tag | Level | Site | Trigger |
|---|---|---|---|
| `DB_CONN` | INFO | connect():177 | After connection open |
| `DB_PRAGMAS` | INFO | connect():178 | After core pragmas set |
| `DB_PRAGMA` | INFO | connect():183 | After contention pragmas set |
| `DB_AUTO_VACUUM_OK` | INFO | connect():170 | When auto_vacuum=INCREMENTAL |
| `DB_AUTO_VACUUM_NOT_INCREMENTAL` | WARN | connect():162 | When auto_vacuum != INCREMENTAL |
| `DB_AUTO_VACUUM_PROBE_FAIL` | DEBUG | connect():174 | PRAGMA probe failure |
| `Database disconnected` | INFO | disconnect():199 | Connection close |
| `DB_LOCK_WAIT` | WARN | _locked():258 | wait_ms >= warn threshold |
| `CASCADE_DETECTED` | WARN | _locked():275 | wait_ms >= cascade threshold |
| `DB_LOCK_BREAKDOWN` | WARN | _locked():301 | After CASCADE_DETECTED (top-5 contributors) |
| `DB_LOCK_HIST` | INFO | log_lock_histogram():344 | Hourly from cleanup_worker |
| `WAL_CHECKPOINT` | INFO | checkpoint():515 | After successful PASSIVE checkpoint |
| `WAL_CHECKPOINT_BUSY` | WARN | checkpoint():510 | When busy != 0 |
| `WAL_CHECKPOINT_NORESULT` | WARN | checkpoint():500 | Null cursor return |
| `DB_ERR` | ERROR | execute():396 | After 3 retries fail |

## 8. `__aenter__` / `__aexit__` semantics

`DatabaseManager` itself does NOT implement async context manager. Only its internal `_locked()` and `transaction()` do.

`_locked.__aenter__` acquires the asyncio lock and records the holder. `_locked.__aexit__` clears `_current_holder` and releases the lock.

`transaction.__aenter__` acquires the lock and yields the raw aiosqlite connection. `__aexit__` commits on clean exit or rolls back on exception.

## 9. Implications for the refactor

The refactor must preserve every public surface here exactly:

- Six public methods: `execute`, `executemany`, `fetch_one`, `fetch_all`, `checkpoint`, `transaction` — same signatures, same return types, same exception types.
- Three log tag families that downstream tools depend on: `DB_*`, `WAL_*`, `DB_LOCK_*`.
- The protected-tables guard pre-flight check (`assert_not_protected_destructive`).
- The retry-on-locked behavior in `execute` and `executemany` (3 retries, linear backoff).
- The hourly histogram emit via `log_lock_histogram()`.

The refactor adds a parallel internal class `_PooledDatabaseEngine` that implements the same dispatch but over a reader pool plus one writer connection. The `DatabaseManager` becomes a facade: at `connect()` time it chooses either the legacy single-connection path or the pooled path based on `settings.database.concurrency_model`.

This way every importing file (117 files, 477 call sites) sees an unchanged API, and the rollout/rollback is a single config flag.

End of `01_connection_anatomy.md`.
