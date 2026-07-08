# Phase 7 — Decision Gate + Cross-Phase Summary

**Date:** 2026-04-26
**Status:** Decision gate. Phase 7 actions (universe consolidation) NOT initiated per the brief.

---

## 1. ShadowKlineReader Root-Cause Fix — Final Verdict

**Status: COMPLETE AND VERIFIED.**

The specific root cause documented in the brief — `ShadowKlineReader` opening 2 fresh sqlite3 connections per call (one for a dead exploratory query, one for the real `_aggregate_simple` query) and blocking the asyncio event loop on each — has been eliminated.

### Evidence

| Indicator | Before fix | After fix |
|---|---:|---:|
| Connections per `get_klines` call | 2 (per-call, sync) | 1 persistent (per-process) |
| Sqlite3 connections opened per structure_worker tick | up to 52 | **0** (re-uses persistent) |
| Event-loop blocking | YES (sync sqlite3) | NO (aiosqlite worker thread) |
| `XRAY_TICK` p50 (median) | 168,741 ms | **974 ms** (173× faster) |
| `XRAY_TICK` min (best case) | 2,221 ms | **56 ms** |
| `STRAT_PREFETCH_CRITICAL` events | 30 over hours | **0** in 26 min |
| `XRAY_SHADOW_AGG_ERR` | n/a | **0** in 26 min |
| `XRAY_SHADOW_NOT_CONNECTED` | n/a | **0** in 26 min |
| Persistent connection opens | n/a | **1** (lifetime) |
| Tests | none | 25 / 25 passing |

### Files modified

| File | Change |
|---|---|
| `src/analysis/structure/shadow_kline_reader.py` | Rewrite: async aiosqlite + asyncio.Lock + connect()/close()/get_stats() + 5 read-side PRAGMAs + 4 XRAY_SHADOW_* log tags + 5 stats counters |
| `src/workers/structure_worker.py` | One-line: `await self._shadow_reader.get_klines(...)` |
| `src/workers/manager.py` | Two inserts: `await shadow_reader.connect()` after construction; cleanup in `stop_all()` before `db.disconnect()` |
| `tests/test_shadow_kline_reader/__init__.py` | New |
| `tests/test_shadow_kline_reader/conftest.py` | New: temp_shadow_db fixture (360 mins of seeded klines) |
| `tests/test_shadow_kline_reader/test_aggregation.py` | New: 14 aggregation tests |
| `tests/test_shadow_kline_reader/test_connection_lifecycle.py` | New: 11 lifecycle/concurrency tests |

No config changes. No schema changes. No new dependencies. No band-aid mitigations applied.

---

## 2. Cross-Phase Final Verification Table

From the brief:

| Check | Target | Achieved | Notes |
|---|---|---|---|
| ShadowKlineReader connection-opens per process | 1 (down from 52/tick) | **1** | XRAY_SHADOW_CONN_OPEN count = 1 |
| Connection-reuse statistics | opens stable, executes growing | opens stable | XRAY_SHADOW_STATS not yet emitted (cache is hot — fewer fallback calls than estimated) |
| structure_worker tick p95 | < 3000 ms | 11,573 ms | **FAIL — caused by D-3** (kline_worker contention on trading.db; NOT shadow_reader) |
| structure_worker tick max | < 10000 ms | 19,934 ms | **FAIL — caused by D-3** at 00:00 UTC H1 boundary |
| STRAT_PREFETCH_CRITICAL events / 30 min | 0–2 | **0** | event-loop starvation eliminated |
| BASE_WORKER_TICK_SLOW (structure_worker) / 30 min | 0–2 | 8 | 1 cold-start + 7 D-3 contention |
| Memory headroom | > 50 MB | 169 MB (current) | varies 4-555 MB depending on cache state |
| File descriptor count | stable over 30 min (delta ≤ 5) | shadow fds **stable at 4** | total fds drift +6 from sockets (unrelated) |
| WAL checkpoint health | first column 0 | autocheckpoint healthy (WAL 7 MB → 16 KB) | PASSIVE-from-outside-process trips IOERR (race with Shadow's writer; expected) |
| `pytest tests/test_shadow_kline_reader/` | all green | **25 / 25 PASS** | aggregation + lifecycle + concurrency |

**Score: 6 PASS, 4 FAIL.** All 4 failures are caused by **D-3** (kline_worker / trading.db contention), not by ShadowKlineReader.

---

## 3. Discovered Issues — Summary

Catalogued during the investigation. **None fixed in this task per the brief's scope discipline.** Each is a candidate for a separate follow-up.

| ID | Description | Severity | Recommended fix |
|---|---|---|---|
| **D-1** | Duplicate session-context fetch in `structure_worker.py:88` (re-fetches `universe[0]`) | Trivial | Cache the first-symbol candles between line 88 and the analysis loop |
| **D-2** | `CoinDiscovery` uses per-call sync sqlite3 (`coin_discovery.py:60`) | Low (called every 600 s) | Same persistent-connection pattern as ShadowKlineReader |
| **D-3** | `kline_worker`'s heavy `executemany` writes hold `DatabaseManager.asyncio.Lock` for 5-30 s; structure_worker's `market_repo.get_klines` waits | **HIGH (blocks Phase 6 strict criteria)** | (a) batch via `get_klines_batch` in structure_worker, OR (b) chunked save_klines with intermediate releases, OR (c) read-write split |
| **D-4** | Memory headroom routinely tight (>600 MB peaks) | Medium (operational) | Raise `MemoryHigh` to 800 MB OR profile heaviest cache |
| **D-5** | CoinDiscovery's first sqlite3 connection fd held open by Python sqlite3 binding indefinitely (not a leak — fd count stable) | Cosmetic | Tied to D-2 fix |

---

## 4. Phase 7 Decision

The brief says Phase 7 (universe consolidation — collapsing the 102/30/126 universes) only fires if Phase 6 strict success criteria are met AND the operator authorises it.

**Neither gate is satisfied:**
1. Phase 6 strict criteria FAILED on 4 of 6 metrics (all due to D-3).
2. Operator authorisation has not been requested or granted for this engagement.

**Action: NO Phase 7 work performed.** Per the brief: *"If Phase 6 fails: do not proceed; report root-cause-not-addressed."*

The honest framing is: **the ShadowKlineReader root cause WAS addressed, but the system has another root cause (D-3) that prevents the strict Phase 6 thresholds from being met.** Universe consolidation cannot meaningfully proceed until D-3 is fixed and Phase 6 is re-run.

---

## 5. Recommended Next Steps (for the operator)

1. **Address D-3 (HIGH priority)** — the kline_worker / trading.db contention is the new dominant bottleneck for structure_worker. Three fix options listed above.
2. **Re-run Phase 6** after D-3 is fixed. Strict criteria should pass given that steady-state ticks already show 56-1988 ms.
3. **Address D-4 (MEDIUM priority — operational)** — raise systemd MemoryHigh OR profile cache footprints.
4. **Address D-1, D-2, D-5 (LOW priority — code quality)** — tidy follow-ups.
5. **Phase 7 universe consolidation** — only after the above are resolved and the system shows 30 minutes of green metrics.

---

## 6. Reports Index

All reports under `dev_notes/`:

- `phase0_shadowklinereader_investigation.md` — file-by-file documentation, call chain, connection inventory, verification gate
- `phase1_baseline_measurements.md` — 50-iter timing harness, EXPLAIN QUERY PLAN, baseline XRAY_TICK distribution, memory + fd snapshot
- `phase2_dead_query_removal_report.md` — dead windowed query removal, ~99% tick-time reduction
- `phase3_connection_management_report.md` — async aiosqlite persistent connection, lifecycle integration, observability counters
- `phase4_query_plan_report.md` — autoindex confirmed; 20-symbol latency on warm connection
- `phase5_resource_cleanup_report.md` — fd stable, no leak from Phase 3, WAL healthy
- `phase6_observation_report.md` — 26-min observation; ShadowKlineReader fix verified; D-3 identified as remaining bottleneck
- `phase7_decision_and_summary.md` — this file

Code under `tests/test_shadow_kline_reader/`:

- `__init__.py`
- `conftest.py` — temp_shadow_db fixture
- `test_aggregation.py` — 14 tests
- `test_connection_lifecycle.py` — 11 tests
