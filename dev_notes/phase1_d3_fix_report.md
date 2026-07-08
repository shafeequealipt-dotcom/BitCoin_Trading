# Phase 1 — D-3 SQLite Lock Contention Fix Report

**Date:** 2026-04-27
**Status:** Implementation complete (3 commits). Live verification gate is operator-driven.

## Commits

| Commit | Subject | Hash |
|---|---|---|
| 1/3 | chunked MarketRepository.save_klines | `c9503bf` |
| 2/3 | WAL checkpoint scheduler in kline_worker | `e5089ee` |
| 3/3 | DB_LOCK_WAIT instrumentation enrichment | `518f3b6` |

## Root cause (recap from Phase 0)

`MarketRepository.save_klines` (`src/database/repositories/market_repo.py:80`) issued one `executemany` per per-(symbol, timeframe) batch under DatabaseManager's global `asyncio.Lock`. With ~45 saves per kline_worker tick chained without yields, the lock was held 12-20 s. WAL pinned at the 100 MiB cap because `wal_autocheckpoint=2000` is opportunistic and only fires when no readers hold a snapshot — under continuous load, that condition is rare; cleanup_worker's hourly checkpoint was insufficient.

## What landed

**Commit 1 — Chunked saves.** `save_klines` splits the params list into chunks of `kline_save_chunk_size` (default 500) and yields the event loop between chunks via `await asyncio.sleep(0)`. Each chunk acquires the DB lock briefly and releases it; other workers interleave instead of queueing. INSERT OR IGNORE idempotency preserved across chunk boundaries. `KLINE_SAVE_CHUNKED` log fires only when the payload exceeds one chunk.

**Commit 2 — WAL checkpoint scheduler.** `KlineWorker._maybe_run_wal_checkpoint` runs at the end of every tick, fires `PRAGMA wal_checkpoint(PASSIVE)` every `wal_checkpoint_every_n_kline_ticks` ticks (default 50). Escalates to `TRUNCATE` if PASSIVE returns busy != 0 for `wal_checkpoint_truncate_after_busy_count` consecutive scheduled checkpoints (default 3). Reuses existing `DatabaseManager.checkpoint(mode=...)`. New tags: `WAL_CHECKPOINT_SCHEDULED`, `WAL_CHECKPOINT_ESCALATE`, `WAL_CHECKPOINT_ERR` (errors are logged but never raised into the kline_worker tick).

**Commit 3 — DB_LOCK_WAIT enrichment.** `lock_wait_warn_ms` is now configurable per DatabaseManager instance (`db_lock_wait_threshold_ms` setting). `DB_LOCK_WAIT` carries the upstream caller frame (`file:line` outside `connection.py` via `traceback.extract_stack`) plus the active threshold. Per-caller counters (count + total wait_ms) are maintained on every acquire and reported in the existing `DB_LOCK_HIST` line as a `top_callers=[op=total_ms(n=count), ...]` field; counters reset after each emit so the next window is independent. Memory is bounded at 64 distinct op-tags.

## Files modified

- `src/database/repositories/market_repo.py` — chunked save_klines.
- `src/workers/kline_worker.py` — checkpoint scheduler.
- `src/database/connection.py` — caller frame, top-callers histogram, configurable threshold.
- `src/config/settings.py` — three new keys on `DatabaseSettings` with `__post_init__` validation.
- `config.toml` — new keys with operator-facing comments.
- `src/trading/services/market_service.py`, `src/workers/manager.py`, `src/core/container.py`, `src/brain/__init__.py`, `src/mcp/server.py`, `workers.py`, `brain.py` — wiring (pass settings through to the right constructors).

## What did NOT change

- DatabaseManager lock model (still single global `asyncio.Lock` — Approach B rejected because WAL readers don't block on writers; the bottleneck was writers serialising on each other, not readers blocking on writes).
- Kline schema, indexes, retention policy.
- WAL mode itself.
- ShadowKlineReader (untouched per memory note: prior fix already shipped).

## Tests

- `tests/test_market_repo/test_save_klines_chunked.py` — 10 tests (chunk boundaries, partial last chunk, single-chunk no-yield path, INSERT OR IGNORE idempotency across boundaries, event-loop yield, edge cases).
- `tests/test_kline_worker/test_wal_checkpoint_schedule.py` — 8 tests (cadence, escalation, safety).
- `tests/test_market_repo/test_db_lock_wait_enrichment.py` — 2 smoke tests (configurable threshold, counter lifecycle).
- 20 new tests pass; 189-test wider regression run shows the only failures are pre-existing and unrelated (signal_generator sentiment test + bybit client error mapping test, both fail on the prior commit too).

## Operator verification runbook (60-min trial)

After the next worker restart, watch for:

| Metric | Target | grep |
|---|---|---|
| `KLINE_TICK_SUMMARY el=` p50 | < 5 s (was 13 s) | `grep KLINE_TICK_SUMMARY workers.log \| awk` |
| `KLINE_TICK_SUMMARY el=` p95 | < 10 s (was 20 s) | same |
| `STRAT_PREFETCH_CRITICAL` events / hour | 0 | `grep STRAT_PREFETCH_CRITICAL workers.log` |
| StrategyWorker coins / tick | 50 (was 5) | `grep STRAT_L1 workers.log \| tail -20` |
| `data/trading.db-wal` size | < 50 MB sustained | `ls -la data/trading.db-wal` periodically |
| `WAL_CHECKPOINT_SCHEDULED` cadence | every 50 ticks | `grep WAL_CHECKPOINT_SCHEDULED workers.log` |
| `WAL_CHECKPOINT_ESCALATE` events | 0 in steady state | grep |
| `DB_LOCK_WAIT > 5000ms` events / hour | 0 | grep + threshold filter |
| `DB_LOCK_HIST top_callers=` | identifies real contributors | `grep DB_LOCK_HIST workers.log \| tail -5` |

## Rollback

Each commit reverts independently. `git revert <hash>` restores prior behaviour cleanly because:
- Commit 1: chunked path collapses to single executemany (idempotent).
- Commit 2: removes the periodic checkpoint; cleanup_worker hourly checkpoint remains.
- Commit 3: restores 1000 ms hardcoded warn threshold and original log payload.

## Next phase

Phase 2a — consolidate the orphan `src/workers/layer_manager.py` (zero imports verified in Phase 0). Single atomic deletion commit.
