# 07 — Aggregate Access Analysis

Synthesis of 01–06. Answers the question: what does the concurrency picture look like at peak?

## 1. Surface area

| Inventory | Count |
|---|---|
| Files that import from `src/database/` | 117 |
| Direct `db.execute` / `db.executemany` / `db.fetch_one` / `db.fetch_all` call sites in `src/` | 477 |
| Repository methods | 85 (46 read / 34 write / 5 read-modify-write) |
| Workers with direct DB access | 13 of 28 (kline, ticker_cache_buffer, profit_sniper, position_watchdog, regime, scanner, cleanup, telegram_bot, discovery, news, reddit, structure, manager) |
| DB-access sites outside repos/workers | 44 across 21 files (core 19, fund_manager 11, tias 10, strategies 4, apex 2, portfolio 2, risk 2, brain 1, factory 1) |
| Telegram handler direct sites | 26 across 7 files (dashboard, watchlist, system, brain, apex, analysis, portfolio) |
| MCP direct sites | 2 (system_tools only) |

## 2. API call distribution (repositories only)

| API | Count | Share |
|---|---|---|
| `execute` | 42 | 49% |
| `fetch_one` | 29 | 34% |
| `fetch_all` | 26 | 31% |
| `executemany` | 2 | 2% (batch writers) |
| `transaction()` | 0 | 0% (defined but never called) |

The read:write ratio in the repository surface is approximately **55:45** (55 reads, 36 writes, plus 2 batched executemany that are write-heavy).

Across the whole codebase including workers' direct DB calls, the operational read:write ratio at steady state (estimated from worker tick cadences) is roughly **65:35** in operation count terms. By contention-time weight, it skews further toward reads — analytical reads with temp B-tree sorts hold the lock longer than single-row INSERTs.

## 3. Operations per minute (steady-state estimate)

From `03_worker_access_map.md` section 2:

- Reads: ~18-25/min steady-state, peak 50+/min during dashboard refreshes and trade events.
- Writes: ~11-65/min depending on trade activity. Dominated by:
  - `ticker_cache_buffer` 2/min (each is executemany of ~50 rows).
  - `position_watchdog` 6/min (per open position × 10 s tick = ~54/min at 9 open positions).
  - `kline_worker` 0.2/min (each is executemany of ~9000 rows in chunks of 500).
  - `profit_sniper` 2/min (sniper_log INSERT).
  - `regime_worker` 0.4/min (51-row batch).
  - `manager` event-driven 0-10/min during fills.

At peak (an active trading minute with a 5-min sweet-spot worker firing and a dashboard refresh), the system can attempt 100+ DB operations in a 60-second window. Today, every one of those operations contends on a single asyncio.Lock; tomorrow, only the writers contend with other writers, and reads run concurrently in the pool.

## 4. Critical-path vs background

Critical-path consumers (a slow tick directly affects live trading decisions):

- `kline_worker` (writes data feeding every analyzer)
- `ticker_cache_buffer` (writes feeding every read of latest price)
- `price_worker` (writes via buffer)
- `profit_sniper` (5 s tick, sniper_log writes + klines reads)
- `position_watchdog` (10 s tick, thesis writes + klines/orders reads)
- `manager` (event-driven trade lifecycle writes)

Background consumers (slow tick is operationally annoying but not trade-affecting):

- All other workers (altdata, news, reddit, regime, scanner, structure, signal, cleanup, allocation, optimization, discovery, enforcer, fund_manager, fund_reconciler, trial_monitor, live_monitor, sweet_spot_scheduler, telegram_bot, scheduled_report, price_alert, worker_liveness_watchdog).

Telegram/MCP: on-demand, not on a fixed cadence, but operator-facing — slowness here is user-visible.

## 5. Read/write ratio by criticality

Critical-path workers do BOTH reads and writes:

- kline_worker: 1 read (staleness scan) + heavy chunked write per cycle.
- ticker_cache_buffer: read fallback + heavy write per 500 ms.
- profit_sniper: many reads (klines, ticker cache) + write every N ticks.
- position_watchdog: many reads (klines, thesis, orders) + few writes per tick.
- manager: read state + write state on fills.

Background workers split:

- Read-only or read-dominant: structure_worker, backtest_worker, allocation_worker (mostly in-memory), telegram_bot_worker, enforcer_worker, fund_manager_worker, fund_reconciler, signal_worker.
- Write-dominant: regime_worker, scanner_worker, cleanup_worker, news_worker, reddit_worker, sentiment_repo path, discovery_worker.

## 6. Cross-domain transactional dependencies

ZERO explicit `transaction()` callers in `src/` or `tests/`.

Implicit multi-write flows that span multiple `execute` calls without atomicity:

- Trade open: thesis INSERT → strategy_trades INSERT (~2 statements, non-atomic).
- Trade close: trade_log INSERT OR REPLACE → trade_thesis UPDATE → TIAS collector reads (~3 statements, non-atomic).
- Mode switch: 2-3 sequential UPDATE transformer_state calls (non-atomic).

A process crash during these mid-sequences can leave inconsistent state. The system tolerates this today because `trade_log` is the authoritative ledger and the rest can be reconstructed from it on next startup. The refactor preserves this exactly — Option B's writer lock matches today's auto-commit-per-execute semantics bit-for-bit.

## 7. Concurrency picture summarized

At peak load in the current single-lock model:

- 1 operation runs at any moment.
- 5-20 waiters queue during dashboard refresh + kline tick overlap.
- Each waiter's wait time = sum of all preceding holders' work time.
- If the kline tick batch holds the lock for 2 seconds, every other worker's next operation is delayed by 2 seconds.
- If 64 distinct callers queue up (the bounded counter cap), the audit logs `total_callers=64` and that is the maximum the instrumentation can observe (true contention breadth may be higher).
- Cascades up to 44 seconds observed in the field. Most cascades start with a slow analytical read or a chunked write, and the cascade duration is the sum of subsequent waiters.

Under Option B (reader pool + single writer):

- N reads run concurrently (one per pooled reader connection, up to the pool's hard cap).
- 1 write runs at a time (writer lock, single writer connection).
- Reads do not block reads.
- Writes do not block reads.
- Reads do not block the writer.
- Only writer-writer overlap contends, and even that overlap is bounded by SQLite's WAL semantics (writer commits append-only to WAL, fast).
- Cascade events should drop to zero in normal operation. The remaining writer lock pressure should be 1-2 orders of magnitude smaller because (a) writes are the smaller share of traffic and (b) writes were never the read-cascade root cause.

## 8. Implications for the refactor

- The single seam to change is `src/database/connection.py`.
- The ServiceContainer wiring (`src/core/container.py:21`) is unchanged — one `DatabaseManager` instance still threads through every service.
- 0 files in `src/database/repositories/` change.
- 0 files in `src/workers/` change (except `cleanup_worker.py` for the optional histogram extension).
- 0 files in `src/core/`, `src/brain/`, `src/apex/`, `src/strategies/`, `src/risk/`, `src/tias/`, `src/fund_manager/`, `src/portfolio/`, `src/factory/`, `src/mcp/`, `src/telegram/` change.
- 1 file changes in `src/config/settings.py` (add `concurrency_model` and `reader_pool_size` fields).
- 1 file changes in `config.toml` (declare the same fields).

Total surface of the Phase 3 implementation: 3 files modified, 2 new test files added.

End of `07_aggregate_analysis.md`.
