# Phase 1 — Baseline Measurements

**Date:** 2026-04-25 ~23:30 UTC
**Status:** Baseline captured. No code changed.
**Workers process:** `trading-workers.service` PID 397, running 3h 16min, Python `workers.py`.

These numbers are the comparison point for Phases 2-6.

---

## Measurement 1 — sqlite3 Connection-Open Cost

50-iteration harness (10 distinct symbols × 5 each):

```python
import sqlite3, time
URI = "file:/home/inshadaliqbal786/shadow/data/shadow.db?mode=ro"
for sym in SYMBOLS[:50]:
    t0 = time.perf_counter()
    conn = sqlite3.connect(URI, uri=True, timeout=5)
    t1 = time.perf_counter()
    # ... query + close ...
```

| Metric | connect+pragma (ms) |
|---|---:|
| n   | 50 |
| min | 0.10 |
| median | **0.15** |
| mean | 0.22 |
| p95 | 0.25 |
| max | 2.23 |

**Verdict:** sub-millisecond. **Connection-open is NOT the dominant cost.** The cost story is dominated by query execution (Measurement 2).

---

## Measurement 2 — Single-Symbol Query Cost

Same harness; the simple SELECT used by `_aggregate_simple` at `shadow_kline_reader.py:122-131`:

```sql
SELECT timestamp, open, high, low, close, volume, turnover
FROM klines
WHERE symbol = ?
ORDER BY timestamp DESC
LIMIT ?
```
Bind: `(symbol, 12060)` — 200 H1 buckets × 60 mins/bucket + 60-min buffer.

| Metric | query+fetchall (ms) |
|---|---:|
| n   | 50 |
| min | 43.86 |
| median | **197.41** |
| mean | 182.75 |
| p95 | 307.52 |
| max | 494.34 |

| Metric | full open→close (ms) |
|---|---:|
| n   | 50 |
| min | 44.32 |
| median | 197.99 |
| mean | 186.13 |
| p95 | 409.10 |
| max | 494.79 |

**Verdict:** **the query itself is ~200 ms median, ~500 ms p95.** Connection open + close are negligible (~0.2 ms each). The bottleneck is SQLite reading and returning 12,060 rows from an 817 MB DB per symbol.

**Per-tick budget primitives (with current code, 25-symbol batch, all falling through to shadow_reader):**
- 25 symbols × 2 connections × 0.22 ms = ~11 ms in `connect()`
- 25 symbols × 2 queries × 197 ms = **~9.85 seconds in SQL execution**
- Python aggregation: ~few ms per symbol = ~few hundred ms total
- **Total per-tick CPU on shadow.db: ~10 seconds.**

**But observed XRAY_TICK is far higher** (see Measurement 5 below — median 169 SECONDS). The discrepancy is the asyncio event loop being held for all 10 seconds of synchronous shadow.db work, during which every other worker's tick falls behind. Cumulatively, the system collapses into a thundering-herd where each worker's slow tick triggers the next.

**This is exactly the dual-cost case the brief predicts: query-execute IS expensive, connection-open is cheap — but the COMBINED effect on the event loop (sync calls × 50 connections per tick × 200 ms median) blocks every async worker.**

---

## Measurement 3 — EXPLAIN QUERY PLAN

```
sqlite3 file:/home/inshadaliqbal786/shadow/data/shadow.db?mode=ro
> EXPLAIN QUERY PLAN
> SELECT timestamp, open, high, low, close, volume, turnover
> FROM klines WHERE symbol = 'BTCUSDT' ORDER BY timestamp DESC LIMIT 12060;
QUERY PLAN
`--SEARCH klines USING INDEX sqlite_autoindex_klines_1 (symbol=?)
```

**Verdict:** the query uses `sqlite_autoindex_klines_1` — the auto-index on PRIMARY KEY `(symbol, timestamp)`. This is the optimal index for this query shape. The `ORDER BY timestamp DESC` is satisfied by reading the index in reverse (no temp B-tree sort).

**Phase 4 will be a no-op.** Index plan is already optimal. The 197 ms median per query is intrinsic SQLite cost on this row count + DB size, not an index miss.

---

## Measurement 4 — Database Size & Growth

```
shadow.db                    856,678,400 bytes  (≈817 MB)  Apr 25 23:27
klines row count             4,002,184
Rows added in last hour      6,018
Rows added in last minute    0  (last flush window — KlineCollector flushes every 5 s)
```

Growth rate: ~6,000 rows/hour ≈ 100/min ≈ ~144,000/day. At ~80 bytes/row, ~11 MB/day growth. The 817 MB current size will reach ~1 GB in ~17 days.

**Implication for the fix:** even with persistent connection (Phase 3) and dead-query removal (Phase 2), per-query cost grows linearly with table size. The per-query 197 ms median is expected to drift higher over months. Mitigations beyond this fix (e.g., a rolling-window kline retention in shadow.db) are out of scope.

---

## Measurement 5 — Live `XRAY_TICK` Distribution

From `data/logs/workers.log` (current rotation), 21 `XRAY_TICK` lines spanning the last several hours:

| Metric | XRAY_TICK el (ms) |
|---|---:|
| n   | 21 |
| min | 2,221 |
| p50 | **168,741** |
| mean | 276,021 |
| p95 | 710,705 |
| p99 | 801,761 |
| max | **1,015,871** |

**Last 21 samples (chronological), elapsed ms:**
```
15891    8014    2221    5740    3898    8322    8686
79606  160791  199364  189637  168741  801761  710705
1015871 695481  454735  226006  329418  251263  460304
```

The progression from `15891 → 8014 → 2221 → 5740 → 3898` (early state, healthy) to `801761 → 1015871 → 460304` (current, ~7-17 minutes per tick on what should be a 60-second cadence) is the bug at full intensity. The brief documented the 8014 → 199364 ms walk; we now see ticks reach **1,015,871 ms (17 min)** — worse than the brief observed.

**Brief's pass criteria for Phase 6:**
- p95 < 3,000 ms (current p95 = 710,705 ms)
- max < 10,000 ms (current max = 1,015,871 ms)

We need to drive these down by **~99.6 %** (p95) and **~99 %** (max). With the dead-query removal (Phase 2) cutting work by ~50 % and the persistent async connection (Phase 3) eliminating event-loop blocking + amortizing connect cost, both targets are within reach.

---

## Measurement 6 — Memory & File Descriptors

**At Phase 1 start (this measurement, 23:30 UTC):**
```
Memory: 515.5M (high: 600.0M  max: 800.0M  available: 84.4M)
Tasks: 57
CPU: 1h 56min 25s
```

Headroom 84 MB before MemoryHigh kicks in (was 52 MB earlier in the session, was 15.8 MB at the brief's original observation). Memory is high but not yet over the soft cap.

**File descriptors (open by PID 397):** 75 fds. Notable file fds:
- `/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db` (fd 21) — persistent via `DatabaseManager`
- `trading.db-wal` (fd 22), `trading.db-shm` (fd 23)
- `data/logs/workers.log`, `data/logs/general.log`, `data/logs/brain.log` (loguru file handles)
- Sockets (24-28+) — Bybit WS, Telegram bot, etc.
- **NO fd to shadow.db** — confirms `ShadowKlineReader` opens-and-closes per call (no persistent fd).

This number (75) is the baseline for Phase 5's leak verification. After Phase 3 lands (persistent shadow.db read connection), expect baseline +1 (one shadow.db fd) and possibly +2 (shadow.db-wal). After 30 minutes, the count should remain stable.

---

## Companion Observations (system-wide)

These are NOT structure_worker; they confirm the wider event-loop starvation effect.

- `STRAT_PREFETCH_CRITICAL` events in current log file: **30**. Recent samples:
  ```
  el=60220ms  db= 9844ms  h1_db=35372ms
  el=68107ms  db=21051ms  h1_db=12424ms
  el=61559ms  db=26532ms  h1_db=21314ms
  el=64773ms  db=14297ms  h1_db= 6746ms
  el=48502ms  db=17395ms  h1_db=13083ms
  el=23189ms  db= 9175ms  h1_db= 8011ms
  ```
  Strategy_worker's prefetch competes with structure_worker on the asyncio loop. When structure_worker holds the loop for 200+ s, strategy_worker's awaitables queue up and only get to execute in bursts.

- `BASE_WORKER_TICK_SLOW` for `structure_worker` in current log file: **21** (matches XRAY_TICK count — every single tick exceeded the 2-second threshold).

- Other workers also tripping `BASE_WORKER_TICK_SLOW`: `price_alert_worker`, `enforcer_worker`, `fund_manager_worker`, `news_worker`, `position_watchdog`, `altdata_worker`, `scheduled_report_worker`, `scanner_worker`. The shadow_reader sync block is starving the entire system, not just structure_worker.

---

## Per-Tick Budget Decomposition (verdict)

For a typical 25-symbol structure_worker tick:

| Component | Cost | Notes |
|---|---:|---|
| Connection open × 38 (typical) | **~8 ms** | Negligible |
| `_market_repo.get_klines` × 25 (async, persistent) | ~200-500 ms | trading.db, async, doesn't block event loop |
| `_shadow_reader.get_klines` × 18-20 fallbacks: 2 queries each | **~7-8 seconds** | **bottleneck primitive** |
| Python aggregation × 18-20 | ~50-200 ms | Negligible vs queries |
| StructureEngine.analyze × 25 | ~100-500 ms total | (reads from cache, mostly) |
| Setup Scanner over full 134-coin cache | ~50-200 ms | reads only, no DB |
| Other awaitables queued during sync block | UNKNOWN | This is where the 100-1000 s comes from |

**The 7-8 seconds of pure shadow_reader work is the floor. The remaining ~160 seconds median tick time is event-loop starvation: every other worker's queued awaitables fire only when shadow_reader yields.**

Removing the dead query (Phase 2) cuts the shadow_reader floor to ~3.5-4 seconds. Persistent async aiosqlite connection (Phase 3) eliminates event-loop blocking entirely (queries happen in the aiosqlite worker thread; the event loop yields between awaits). Combined, these fix the 7-8s floor AND the 100-1000s amplification.

---

## Verification Gate (Phase 1 → Phase 2)

The brief requires Phase 1 answer four questions:

1. **Cost in ms to open one read-only connection to shadow.db:** **median 0.15 ms, p95 0.25 ms.** Sub-millisecond.

2. **Cost to execute the simple-select query for one symbol:** **median 197 ms, p95 308 ms, max 494 ms** (50-iter harness against the live DB).

3. **EXPLAIN QUERY PLAN result:** `SEARCH klines USING INDEX sqlite_autoindex_klines_1 (symbol=?)` — autoindex on PK is used. Optimal.

4. **Per-tick fraction by category:**
   - Connection-open: <1 % (~10 ms / ~10 s primitive)
   - **Query-execute: ~80 % of the primitive cost (~8 s of ~10 s on shadow.db)**
   - Data marshalling + Python aggregation: ~5 % (~few hundred ms)
   - **Event-loop starvation: the AMPLIFIER that takes the 10 s primitive cost to 100-1000 s observed tick time.** This is the second target Phase 3 must address (persistent async connection prevents event-loop blocking).

**Verification gate PASSED. Proceeding to Phase 2.**
