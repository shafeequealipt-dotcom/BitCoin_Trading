# Master Investigation Report — Database Concurrency Refactor

Date: 2026-05-14
Branch: `fix/db-concurrency-refactor`
Audit source: `/home/inshadaliqbal786/DB_COMPLETE_DISCOVERY_AUDIT_REPORT.md`
Spec: `/home/inshadaliqbal786/IMPLEMENT_DB_CONCURRENCY_REFACTOR.md`
Plan file: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-scalable-kahn.md`

## 1. Executive summary

The single `asyncio.Lock` in `DatabaseManager._locked` (`src/database/connection.py:104`/`:208`) is the root cause of every database cascade in production logs. Every read, every write, from every worker, serializes through this one lock. Cascades up to 44 seconds observed in the field — workers go OVERDUE in lockstep, Telegram dashboards lag, and live trading decisions delay.

The fix is to replace the single-connection + single-lock model with a small reader pool plus one writer connection (Option B), preserving the public `DatabaseManager` API verbatim so no caller above the database layer changes contract. SQLite WAL already supports many readers + one writer at the engine level. aiosqlite already supports multiple independent Connection instances (each is its own thread + sqlite3 connection). The only obstacle today is the application-level lock; that is what changes.

Migration touches 4 files (`src/database/connection.py`, `src/config/settings.py`, `config.toml`, `src/workers/cleanup_worker.py`) and adds 2 new test files. 117 importing files, 477 call sites, 85 repository methods, 28 workers — none of those change. ServiceContainer (`src/core/container.py`) wiring is unchanged.

Operator decisions captured (2026-05-14):

- Architecture: Option B.
- Pool size: decided from Phase 3.5 stress-test sweep (2/4/8/12 readers).
- Rollout: single global flag (`concurrency_model = "reader_pool"` in `config.toml`).
- Phase 5 includes zero-row polling stops.

## 2. Confirmation of the audit's diagnosis

The audit's claim is verified:

> "The cascades are not caused by any single slow query. The common factor is that every operation passes through the same asyncio.Lock in DatabaseManager._locked... This is a property of the single-connection + single-lock concurrency model, not of any one query."

All audit file:line references checked against current code on 2026-05-14 — every one still holds. Baseline metrics from the same SESSION_LOGS window the audit used (1 h 45 m):

| Event | Count |
|---|---|
| `DB_LOCK_WAIT` | 129 |
| `CASCADE_DETECTED` | 12 |
| `WORKER_TICK_OVERDUE` | 92 |
| `BASE_WORKER_TICK_SLOW` | 126 |

Wait_ms: count=128, p50=2382, p90=3313, p95=26436, p99=43108, max=44210.

The most-frequent cascade holder SQL is `SELECT * FROM price_alerts WHERE triggered = 0` — a 0-row table. This is not a query problem; it is a serialization problem.

## 3. Complete database access map

Synthesized from `01_connection_anatomy.md` through `07_aggregate_analysis.md`:

- 117 files import from `src/database/`.
- 477 direct DB call sites.
- 85 repository methods (46 R / 34 W / 5 mixed).
- 13 of 28 workers directly touch the DB.
- 44 sites outside repos/workers across 21 files.
- 26 direct sites in Telegram handlers.
- 2 direct sites in MCP (operator maintenance).
- **0** `transaction()` callers anywhere in `src/` or `tests/`.

Read/write ratio: ~55:45 by method count, ~65:35 by operation count, but reads dominate contention time because of TEMP B-TREE sorts in `trade_thesis` analytical reads.

Critical-path consumers: kline_worker, ticker_cache_buffer, profit_sniper, position_watchdog, price_worker, manager.

Background consumers: 22 other workers and the entire telemetry + analytics + learning surface.

The full per-file inventory lives in the Phase-1 documents.

## 4. Architectural options

Full analysis in `08_architectural_options.md`. Summary:

| Option | Cascade↓ | Migration | Decision |
|---|---|---|---|
| A — status quo | 0% | 0 days | rejected |
| **B — reader pool + writer** | **~95%** | **7-10 days** | **CHOSEN** |
| C — per-domain managers | ~60% | 20-30 days | reserved |
| D — writer queue | ~80% | 15-20 days | rejected (durability risk) |
| E — hybrid B+C | ~98% | 40+ days | reserved |
| F — multi-process | ~95% | 60+ days | out of scope |

## 5. Stress test scenarios

Five scenarios in `09_stress_test_scenarios.md`:

1. Klines burst (50,000 rows / 10 workers).
2. New trade burst (5 trades / 35 writes).
3. Dashboard read storm (10 concurrent reads).
4. Combined burst (1+2+3 simultaneously, 5 min).
5. Sustained mixed load (30 min at production rate).

Pass criteria: 0 cascades, writer waits < 500 ms p95, reader waits < 200 ms p95, no pool exhaustion at chosen size, memory steady. The smallest pool size that holds all 5 scenarios is the default.

## 6. Recommended option — Option B

Already chosen by operator. The plan from Phase 3 onward is locked to Option B (see plan file).

## 7. Implementation plan

The 6-phase plan lives in `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-scalable-kahn.md`. Summary:

- Phase 0: branch + baseline ✅ (this directory's `phase0_baseline.md`).
- Phase 1: 7 investigation documents ✅ (`01_*` through `07_*` here).
- Phase 2: options + scenarios + this master report ✅ (`08_*`, `09_*`, this file).
- Phase 2.5: operator decision gate ✅ (decisions captured 2026-05-14).
- Phase 3: implementation, 9 sub-commits (`conn-pool/p3-*`).
- Phase 4: 48 h verification, metrics comparison.
- Phase 5: optimization pass — drop duplicate indexes, stop zero-row polls, redirect brain_decisions reads, write `concurrency_model_docs.md`.

## 8. Open questions reaching into Phase 3

- Stress-test DB copy location: `data/trading_stress_test.db` (gitignored). Confirm at 3.5 kickoff.
- The 5 read-modify-write methods in `learning_repo.py` stay out of Phase 3 scope. Phase 5 candidate if Phase 4 reveals contention.
- The Telegram `brain_decisions` reads (handlers/system.py:102, handlers/brain.py:33): redirect to `claude_decisions` or remove handler? Operator decides at p5-4.

## 9. Sign-off

Investigation Phase 0–2 complete. Diagnosis matches the audit. Option B chosen. Implementation proceeds from Phase 3.1.

End of master report.
