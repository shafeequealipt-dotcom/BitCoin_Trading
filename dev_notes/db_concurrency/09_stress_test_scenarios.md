# 09 — Stress Test Scenarios

Five scenarios that simulate production load against a COPY of the live database. Runs both single-lock and reader_pool paths; compares metrics.

Database under test: `data/trading_stress_test.db` (copy of `data/trading.db`, gitignored).
Test framework: `tests/stress/test_db_concurrency_stress.py` (pytest, marker `@pytest.mark.stress`, skipped by default in CI).

Each scenario records:

- Total DB operations performed.
- Operations completed within budget.
- `CASCADE_DETECTED` count (target: 0 on pooled path).
- `DB_LOCK_WAIT` / `WRITER_LOCK_WAIT` count and p50/p95/p99/max wait.
- `CONN_POOL_EXHAUSTED` count (target: 0 at the chosen pool size).
- Reader pool peak occupancy.
- Writer lock peak hold time.
- Worker tick latency P50/P95/P99.

Pool sizes swept per scenario: 2, 4, 8, 12. The smallest pool that holds pass criteria across all 5 scenarios is the default committed to `config.toml` before Phase 3.7 cutover.

---

## Scenario 1 — Klines burst

### Setup
- 10 coroutines each acting as a kline-writer worker.
- Total payload: 50,000 rows split into 100-row, 500-row, and 1000-row chunks (mixed).
- `INSERT OR IGNORE INTO klines (...) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`.
- Background: 1 coroutine simulating profit_sniper (SELECT klines every 5 s).
- Background: 1 coroutine simulating position_watchdog (SELECT trade_thesis + orders every 10 s).

### Pass criteria
- All 50,000 rows committed within 60 s.
- `CASCADE_DETECTED` = 0.
- profit_sniper's reads have wait p95 < 100 ms.
- position_watchdog's reads have wait p95 < 100 ms.

### Baseline expectation (single_lock)
- Total writes complete in ~120 s.
- Cascades observed (1-3 events).
- Reader waits cluster around 1-5 s.

---

## Scenario 2 — New trade burst

### Setup
- 5 coroutines opening trades back-to-back, mirroring BRAIN_DO_TRADE flurry.
- Each trade writes: `orders` (INSERT), `positions` (INSERT OR REPLACE), `trade_log` (INSERT OR REPLACE), `trade_thesis` (INSERT), `position_snapshots` (INSERT), `claude_decisions` (INSERT), `trade_intelligence` (INSERT).
- 7 writes per trade × 5 trades = 35 sequential writes.

### Pass criteria
- All 35 writes complete within 5 s.
- Per-write writer-lock wait p95 < 50 ms.
- 0 cascades.

### Baseline expectation (single_lock)
- 35 writes complete in ~15-30 s if any read is concurrent.

---

## Scenario 3 — Dashboard read storm

### Setup
- 10 coroutines simulating concurrent /dashboard, /positions, /performance, /capital, /performance_enforcer Telegram requests.
- Each request fires 5-8 fetch_one/fetch_all calls.

### Pass criteria
- Total response time across all 10 requests p95 < 500 ms.
- All requests complete < 1 s.
- 0 cascades.

### Baseline expectation (single_lock)
- Serial — each request adds 50-200 ms of queue depth.

---

## Scenario 4 — Combined burst

### Setup
- Scenarios 1, 2, 3 running simultaneously for 5 minutes.

### Pass criteria
- 0 cascades.
- Klines burst completes within 90 s (vs 60 s standalone — some contention permissible).
- Trade burst completes within 8 s.
- Dashboard responses p95 < 700 ms.
- No `CONN_POOL_EXHAUSTED` at chosen pool size.

### Baseline expectation (single_lock)
- Multiple cascades. Total runtime double or more.

---

## Scenario 5 — Sustained mixed load

### Setup
- 30 minutes of typical mixed read/write at expected production rate:
  - profit_sniper-equivalent every 5 s.
  - position_watchdog-equivalent every 10 s (9 simulated positions).
  - ticker_cache_buffer flush every 500 ms.
  - regime_worker 5-min sweep.
  - kline_worker 5-min sweep.
  - 1 dashboard refresh every 2 min.

### Pass criteria
- 0 cascades over 30 min.
- Operation count > 20000.
- No writer-lock waits > 500 ms.
- No reader-pool waits > 200 ms.
- Pool stays within hard cap (no growth beyond 2N).
- Memory steady (no leak).

### Baseline expectation (single_lock)
- Periodic cascades (1-3 per 30 min based on baseline observations).

---

## Pool size selection algorithm

1. Run scenarios at pool size N=2.
2. If any scenario fails or `CONN_POOL_EXHAUSTED` > 0, bump to N=4.
3. Continue doubling until all 5 scenarios pass cleanly at N.
4. Confirm at N=12 that doubling further produces no further improvement.
5. Pick the smallest N that passes all 5 scenarios with > 50% headroom on pool occupancy.

The chosen N is committed to `config.toml` before Phase 3.7 production cutover.

## Measurement methodology

- Stress tests use the real `DatabaseManager` (via factory in pytest fixture).
- The DB-under-test is a copy of the live `data/trading.db` at the start of the test.
- After each scenario, the test resets the DB to the snapshot for the next iteration.
- Metrics captured via the new `CONN_POOL_STATS` log emit and via in-process counters returned from the test harness.
- Pass criteria are asserted; failures fail the test.

End of `09_stress_test_scenarios.md`.
