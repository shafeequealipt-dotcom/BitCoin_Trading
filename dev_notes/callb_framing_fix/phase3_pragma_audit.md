# Phase 3 — DB lock root-cause audit

**Date:** 2026-05-06
**Outcome:** **NO-OP**. No actual PRAGMA discrepancy in the running process. Phase 0's measurement was a CLI-tool artifact; corrected here. `DB_LOCK_WAIT` count is 0 in current logs, so targeted batching is also unnecessary at this volume.

## Phase 0 baseline mistake

Phase 0 (`phase0_baseline.md`) reported:

> Runtime PRAGMA (live `data/trading.db`):
>   journal_mode = wal
>   busy_timeout = 0  (CODE SETS 10000)
>   cache_size = -2000  (CODE SETS -65536)
>   ... etc.

This was measured via:

```
sqlite3 data/trading.db "PRAGMA journal_mode; PRAGMA busy_timeout; ..."
```

The mistake: SQLite `PRAGMA busy_timeout`, `cache_size`, `mmap_size`, `temp_store`, `synchronous`, `wal_autocheckpoint` are **per-connection** and reset to defaults on every fresh connect. The `sqlite3` CLI opens a brand-new connection for each invocation — its PRAGMAs are not the running worker's PRAGMAs.

The actual values in the long-running `trading-workers` process are set in `src/database/connection.py:116-152` via the `connect()` method, which executes:

```python
await self._db.execute("PRAGMA journal_mode=WAL")
await self._db.execute("PRAGMA busy_timeout=10000")
await self._db.execute("PRAGMA foreign_keys=ON")
await self._db.execute("PRAGMA cache_size=-65536")
await self._db.execute("PRAGMA synchronous=NORMAL")
await self._db.execute("PRAGMA wal_autocheckpoint=2000")
await self._db.execute("PRAGMA journal_size_limit=104857600")
await self._db.execute("PRAGMA temp_store=MEMORY")
await self._db.execute("PRAGMA mmap_size=268435456")
```

Followed by the `DB_PRAGMAS` log line that reports the applied values. Confirmed via boot-time test:

```
DB_CONN | path=:memory: wal=Y
DB_PRAGMAS | journal_mode=WAL cache_size=64MiB synchronous=NORMAL busy_timeout=10000ms foreign_keys=ON
DB_PRAGMA | wal_autocheckpoint=2000 jsize_lim=100MiB temp_store=MEMORY mmap_size=256MiB
```

Only `journal_mode=WAL` is database-level (persists in the file); the rest are per-connection. The CLI's defaults are NOT what the worker process sees.

## Other connection sites — audited

| File | Target DB | Pattern | Notes |
|---|---|---|---|
| `src/database/connection.py:119` | `data/trading.db` | `aiosqlite.connect` + 9 PRAGMAs | Production. Correct. |
| `src/analysis/structure/shadow_kline_reader.py:94` | `shadow.db` (different DB) | `aiosqlite.connect` + 5 read-side PRAGMAs | Out of scope (Shadow's DB, not trading.db). |
| `scripts/health_check.py:91/103/116` | `data/trading.db` | `sqlite3.connect`, short-lived | CLI-style scripts. No long-running connection. |
| `scripts/monitor.py:84` | `data/trading.db` | `sqlite3.connect` (mode=ro) | CLI. No PRAGMAs needed for read-only inspection. |
| `scripts/observe_phase9.py:48` | `data/trading.db` | `sqlite3.connect` | CLI. |
| `scripts/backfill_*.py:*` | misc | `sqlite3.connect` | CLI. |

No connection path in the long-running worker process bypasses the PRAGMA setup.

## DB_LOCK_WAIT current state

Phase 0 baseline counted DB_LOCK_WAIT events in the current and recent worker.log files:

```
data/logs/workers.log                         : 0
data/logs/workers.2026-05-06_11-25-38_*.log   : 0
data/logs/workers.2026-05-05_21-48-58_*.log   : 0
```

Zero events. The earlier kline batching (Phase 1 D-3 fix at `market_repo.py:65-156`) and the per-connection cache_size+mmap+temp_store PRAGMAs are doing their job. The spec's quoted "14 events per minute" reflected an earlier window before those fixes landed.

## Conclusion and follow-ups

No Phase 3 code changes are warranted at this time:
- PRAGMA application is correct in production.
- `DB_LOCK_WAIT` count is 0.
- WAL is persistent on the DB file (database-level setting).

Follow-ups (if Phase 6 trial reveals new DB pressure):
- If DB_LOCK_WAIT events appear under increased trade volume, examine the altdata_repo + trading_repo per-event write patterns and consider batching them per worker tick (Phase 3C in the plan).
- If a future regression is suspected, the boot-time `DB_PRAGMAS` log already confirms the applied values — operators tail it post-restart.
- If sqlite3 CLI is used for debugging, remember PRAGMAs are per-connection and the CLI's view is NOT the worker's view.

No commit beyond this audit document.
