# Phase 0 — Pre-Flight Baseline

Date: 2026-05-14
Branch created: `fix/db-concurrency-refactor` (from `combined-integration-test` at commit `461f7c6`).

## 1. Pre-conditions

| Check | Result |
|---|---|
| Project working tree | Modifications present in `config.toml`, `data/layer_state.json`, `data/logs/layer1c_full.jsonl` (operator-tracked state, not refactor-related). Untracked dev_notes from prior fixes. None of these affect this refactor. |
| Branch source | `combined-integration-test` @ `461f7c6` ("docs(combined): cross-check report + integration test across both sessions") |
| New branch | `fix/db-concurrency-refactor` created and checked out |
| B1a regime detector fix | Present in history at `6938c69` ("docs(regime-investigation): end-to-end pipeline check against real production data"). The actual calibration commit is `dea18d8` ("fix(regime): B1a calibrate detector thresholds to close ELSE-fallback gap"). |
| `trading-workers` systemd service | `active` |
| `trading-mcp-sse` systemd service | `active` |
| `trading-brain` systemd service | `inactive` (operator runs brain manually — expected) |
| DB audit report | `/home/inshadaliqbal786/DB_COMPLETE_DISCOVERY_AUDIT_REPORT.md` available |
| `dev_notes/db_concurrency/` directory | Created |

## 2. Database file state

| File | Size | mtime |
|---|---|---|
| `data/trading.db` | 184 MB (192 MB by `ls -h`) | 2026-05-14 16:40 |
| `data/trading.db-wal` | 8.2 MB | 2026-05-14 16:42 |
| `data/trading.db-shm` | 32 KB | 2026-05-14 16:41 |

Latest backup: `backups/20260514_080738.tar.gz` (36 MB compressed, 2026-05-14 08:07).

Last 5 daily backups present (rolling 7-day retention per `scripts/backup.sh`):
- `20260514_080738.tar.gz` 36 MB
- `20260513_045842.tar.gz` 37 MB
- `20260512_070248.tar.gz` 36 MB
- `20260511_082658.tar.gz` 33 MB
- `20260509_025307.tar.gz` 27 MB

## 3. Baseline contention metrics

Source log: `/home/inshadaliqbal786/SESSION_LOGS_2026-05-14_12-45_to_14-30.log` (1 h 45 m window). This is the same window the audit used, so a direct comparison is possible post-refactor.

### Event counts

| Event | Count |
|---|---|
| `DB_LOCK_WAIT` (warn > 1000 ms) | 129 |
| `CASCADE_DETECTED` (> 5000 ms) | 12 |
| `WORKER_TICK_OVERDUE` (liveness watchdog) | 92 |
| `BASE_WORKER_TICK_SLOW` | 126 |
| `DB_LOCK_BREAKDOWN` (top-5 contributor emit) | 11 |

### Lock-wait distribution (ms)

| Percentile | Value |
|---|---|
| min | 1013 |
| p50 | 2382 |
| p90 | 3313 |
| p95 | 26436 |
| p99 | 43108 |
| max | 44210 |
| count | 128 |
| avg | 4470 |

### Worker TICK_SLOW frequency

| Worker | Count |
|---|---|
| profit_sniper | 33 |
| position_watchdog | 19 |
| kline_worker | 17 |
| fund_manager_worker | 10 |
| scanner_worker | 7 |
| altdata_worker | 7 |
| regime_worker | 5 |
| price_alert_worker | 5 |
| news_worker | 5 |
| signal_worker | 4 |
| structure_worker | 3 |
| fund_reconciler | 3 |
| enforcer_worker | 3 |
| strategy_worker | 1 |
| scheduled_report_worker | 1 |
| cleanup_worker | 1 |
| bybit_demo_ws_worker | 1 |

### Cascade durations observed

Distinct `duration_ms` values from `CASCADE_DETECTED`: 19402, 23356, 26193, 26436, 26473, 30860, 31167, 31332, 43108, 44210 ms.

Note: a separate 300000 ms `duration_ms` value appears once in `BRAIN_FAILURE_CASCADE` lines — that is a Claude CLI subprocess total_timeout (5 min), not a DB lock event. The 11 DB cascades in this window cluster around 13:02, 13:11, 13:34, and 14:09.

### Single most-frequent cascade-holder SQL

`SELECT * FROM price_alerts WHERE triggered = 0` (`src/database/repositories/telegram_repo.py:32`), against a table with 0 rows. The query is fast; the cascade emerges because every poll acquires the shared lock and the index lookup runs serially behind whatever else was holding.

## 4. aiosqlite + WAL facts confirmed

Read directly from `/home/inshadaliqbal786/.local/lib/python3.10/site-packages/aiosqlite/core.py`:

- aiosqlite 0.17.0, sqlite3 3.37.2 (Python 3.10 site-packages).
- `aiosqlite.Connection` extends `threading.Thread`. Each connection has its own queue (`self._tx: Queue`) and its own underlying `sqlite3.Connection`. The thread loop in `run()` (line 84) pops one job at a time from the queue.
- aiosqlite imposes NO cross-connection serialization. The `asyncio.Lock` in `DatabaseManager._locked` is the only application-level serialization point in the current code.
- SQLite WAL mode allows N concurrent readers + 1 writer at the engine level. `PRAGMA busy_timeout=10000` (set in `connection.py:134`) gives writer attempts up to 10 s to retry on engine-level contention.

Conclusion: opening multiple aiosqlite reader connections + one writer connection is supported by the installed driver and the engine. The refactor is unblocked at the driver level.

## 5. Audit reference verification

All audit file:line citations verified against current code (2026-05-14):

- `src/database/connection.py:104` — `self._lock = asyncio.Lock()` ✅
- `src/database/connection.py:208` — `async def _locked(self, op)` ✅
- `src/database/repositories/telegram_repo.py:32` — `price_alerts WHERE triggered = 0` ✅
- `src/database/repositories/telegram_repo.py:79` — `scheduled_reports WHERE enabled = 1` ✅
- `src/strategies/performance_enforcer.py:581, :597` — `trade_thesis` analytical reads ✅
- `src/core/thesis_manager.py:502` — same family ✅
- `src/database/repositories/altdata_repo.py:44` — `fear_greed_index ORDER BY timestamp DESC LIMIT 1` ✅
- `src/database/repositories/altdata_repo.py:181` — `INSERT INTO open_interest` ✅
- `src/database/repositories/news_repo.py:140, :150` — `news_articles WHERE symbols LIKE ?` ✅
- `src/workers/profit_sniper.py:2430, :3861` — `INSERT INTO sniper_log` ✅

## 6. Database access surface — initial counts

| Inventory | Count |
|---|---|
| Files importing from `src/database/` | 117 |
| Direct DB call sites (`db.execute` / `db.executemany` / `db.fetch_one` / `db.fetch_all`) | 477 |
| Repositories | 11 |
| Repository methods total | 85 (46 read / 34 write / 5 mixed) |
| Workers with direct DB access | 9 (of 28) |
| Workers calling DB via repositories | +2 |
| DB-access sites outside repos/workers | 44 across 21 files |
| `transaction()` callers in `src/` and `tests/` | 0 (defined at `connection.py:521` but never used) |

The complete per-file inventories live in Phase 1 deliverables (`01_connection_anatomy.md` through `07_aggregate_analysis.md`).

## 7. Exit gate

| Gate | Status |
|---|---|
| Branch `fix/db-concurrency-refactor` created | ✅ |
| Baseline metrics captured | ✅ |
| DB file size snapshot | ✅ |
| Services healthy (`trading-workers`, `trading-mcp-sse`) | ✅ |
| Backup current (< 24 h old) | ✅ (today 08:07 UTC) |
| `dev_notes/db_concurrency/` exists | ✅ |
| B1a fix in history | ✅ (`6938c69`, calibration at `dea18d8`) |

Phase 0 complete. Proceed to Phase 1 — comprehensive investigation deliverables.
