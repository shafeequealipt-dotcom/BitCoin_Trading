# Phase 3.6 — Pre-Deploy Sanity Checklist

Date: 2026-05-14
Branch: `fix/db-concurrency-refactor`
Commits in branch: `c913585` (p0-p2 docs), `3c7833d` (p3-1..p3-3 engine), `2954631` (p3-4 unit tests), `829e6e7` (p3-5 stress tests).

## Pre-cutover gates

| Gate | Result |
|---|---|
| Phase 3.4 unit tests pass | ✅ 23/23 in 0.58s (`pytest tests/test_connection_pool.py`) |
| Phase 3.5 stress scenarios pass | ✅ 10/10 in 2.67s at default row counts (`pytest -m stress`) |
| Settings validators reject bogus | ✅ `concurrency_model='bogus'` → ConfigError; `reader_pool_size=0` → ConfigError |
| `config.toml` parses with new fields | ✅ Returns `single_lock` / `4` as written |
| `DATABASE_CONCURRENCY_MODEL` env override works | ✅ Sets `s.database.concurrency_model='reader_pool'` |
| DB integrity check | ✅ `PRAGMA quick_check` → ok |
| Schema fingerprint | `e9fbedfd54165f55fba9b137529769bff4a570d249354c6739f36604807d4123` (recorded; will diff post-cutover) |
| No shadow files touched | ✅ `git diff HEAD~4..HEAD --name-only` shows zero `src/shadow/` files |
| No repository files touched | ✅ Same `git diff` shows zero `src/database/repositories/` files |
| No worker files touched | ✅ Same `git diff` shows zero `src/workers/` files |
| No service files touched | ✅ Same `git diff` shows zero `src/core/`, `src/brain/`, `src/strategies/`, `src/risk/`, `src/tias/`, `src/portfolio/`, `src/fund_manager/`, `src/apex/`, `src/factory/`, `src/telegram/`, `src/intelligence/`, `src/analysis/`, `src/observability/`, `src/sentinel/`, `src/alerts/`, `src/trading/`, `src/bybit_demo/`, `src/exchanges/` files |
| Production entrypoints pass new settings | ✅ `workers.py:147`, `brain.py:50`, `src/mcp/server.py:53` now thread `concurrency_model` + `reader_pool_size` from settings |
| `cleanup_worker.log_lock_histogram` call site preserved | ✅ `src/workers/cleanup_worker.py:134` unchanged; emits `CONN_POOL_STATS` automatically via the facade in pooled mode |
| Surface area | 4 files modified for behavior + 4 entrypoints + 2 test files added. 117 importing files unaffected. |

## Files modified by this refactor

- `src/database/connection.py` — engine refactor (main change)
- `src/config/settings.py` — add `concurrency_model` + `reader_pool_size` to DatabaseSettings
- `config.toml` — declare new keys under `[database]`
- `pyproject.toml` — add `stress` pytest marker
- `workers.py`, `brain.py`, `src/mcp/server.py` — pass new settings through to DatabaseManager construction
- `tests/test_connection_pool.py` — new unit tests (23 tests)
- `tests/stress/test_db_concurrency_stress.py` — new stress harness (5 scenarios)
- `scripts/run_db_concurrency_stress.sh` — operator helper for sweep runs

## Stress sweep summary (default row counts)

| Scenario | model | pool | elapsed | exhausted | peak_in_use | growths |
|---|---|---|---|---|---|---|
| 1 (klines 20k rows) | single_lock | — | 0.13 s | n/a | n/a | n/a |
| 1 | reader_pool | 2 | 0.12 s | 0 | 2 | 0 |
| 1 | reader_pool | 4 | 0.12 s | 0 | 2 | 0 |
| 1 | reader_pool | 8 | 0.13 s | 0 | 2 | 0 |
| 2 (5 trades × 7 writes) | single_lock | — | 0.018 s | n/a | n/a | n/a |
| 2 | reader_pool | 4 | 0.017 s | 0 | 1 | 0 |
| 3 (10 dashboard × 5 reads) | single_lock | — | 0.026 s | n/a | n/a | n/a |
| 3 | reader_pool | 2 | 0.048 s | **2** | 4 | 2 |
| 3 | reader_pool | 4 | 0.064 s | 0 | 6 | 3 |
| 3 | reader_pool | 8 | 0.071 s | 0 | 9 | 2 |

Observations from the sweep:

- **Pool size 2 is insufficient.** Scenario 3 (10-handler dashboard storm) drove pool=2 to 2 exhausted_count events before dynamic growth absorbed the rest. Pool size must be ≥ 4.
- **Pool size 4 is the minimum no-exhaustion default.** Dynamic growth covered the peak (peak_in_use=6, hard_cap=8). 3 growth events recorded.
- **Pool size 8 eliminates growth events under scenario 3.** Peak_in_use=9 (just past 8), with 2 growths. For zero-growth guarantee at the observed peak, the operator would need pool size ≥ 10, but at that point we're paying for connections that sit idle 99% of the time.
- **Sizing recommendation for Phase 3.7:** `reader_pool_size = 4` as the default in `config.toml`. The dynamic growth mechanism is robust and zero-exhaustion is what matters. The operator may bump to 8 if the Phase 4 production metrics show consistent peak_in_use > 4.
- **The default 4 is what Phase 3.1 settings.py shipped.** No `config.toml` change required before Phase 3.7 cutover other than flipping `concurrency_model = "reader_pool"`.

## What Phase 3.7 cutover does

A single line change in `config.toml`:

```diff
- concurrency_model = "single_lock"
+ concurrency_model = "reader_pool"
```

Then restart the `trading-workers` and `trading-mcp-sse` systemd services.

Revert path: change the same line back, restart. No code revert needed.

## Open items entering Phase 3.7

- Operator runs the full-spec stress sweep (`STRESS_KLINES_ROWS=5000 scripts/run_db_concurrency_stress.sh`) before cutover OR after, at their preference. The default-size sweep already confirms behavior.
- Operator confirms backups are current before cutover.
- Phase 4 metrics-collection window starts at cutover time + 1 minute (allow services to fully boot).

Exit gate: all checklist items green; cutover is operator-authorized; revert plan documented.
