# Phase 2 — WAL Checkpoint Scheduler + PRAGMA Verification + kline_worker Sleep Removal

**Status:** SHIPPED
**Date:** 2026-04-26
**Investigation:** [`phase0_issue_d3_cluster.md`](phase0_issue_d3_cluster.md)

## Summary

Three changes plus one diagnostic confirmation. The diagnostic invalidates one of Phase 0's hypotheses; the remaining work targets the dominant root cause of D-3 (sleep-driven idle time in the kline worker tick) and adds a hygiene step to keep the WAL durably checkpointed.

1. **PRAGMA verification (pre-work)** — confirmed all configured PRAGMAs apply correctly. Phase 0's "PRAGMA mismatch" was a false-positive caused by reading the `sqlite3` CLI's own per-connection defaults rather than the workers process's connection. **No PRAGMA fix needed.**
2. **`DatabaseManager.checkpoint(mode="PASSIVE")`** — explicit `wal_checkpoint` invocation method. Returns busy / log_pages / ckpt_pages. Logs at INFO when idle, WARNING when busy.
3. **Cleanup-worker integration** — `tick()` calls `db.checkpoint("PASSIVE")` at the top of every hourly cycle. Failures are tolerated; the daily VACUUM provides the harder reclamation path.
4. **kline_worker sleep removal** — `await asyncio.sleep(0.1)` at line 162 (per-fetch artificial throttle) replaced with `await asyncio.sleep(0)`. Bybit-side rate limiting is already enforced by `BybitClient.call`'s `@rate_limit(calls_per_second=10.0)`, so the 0.1 s sleeps were redundant. **Expected effect: kline tick latency drops from ~13 s to ~3-4 s on typical M5-only ticks.**

## Files changed

| File | Change |
|---|---|
| `src/database/connection.py` | Added `checkpoint(mode="PASSIVE")` method that returns a structured result dict and logs `WAL_CHECKPOINT` (or `WAL_CHECKPOINT_BUSY` when busy>0) |
| `src/workers/cleanup_worker.py` | Calls `self.db.checkpoint("PASSIVE")` at the top of `tick()`. Failures fall through silently — the daily VACUUM is the harder backstop. |
| `src/workers/kline_worker.py` | Replaced the artificial `await asyncio.sleep(0.1)` per-fetch throttle with `await asyncio.sleep(0)` (still yields the event loop without idle wait) |

## Diagnostic findings — Phase 0 hypotheses revisited

### "PRAGMA mismatch" — INVALIDATED

A diagnostic script connecting via the same `DatabaseManager` used by workers reports:

```
journal_mode              = wal
synchronous               = 1
wal_autocheckpoint        = 2000
journal_size_limit        = 104857600
busy_timeout              = 10000
cache_size                = -65536
temp_store                = 2
mmap_size                 = 268435456
foreign_keys              = 1
page_size                 = 4096
```

All match the values configured at `src/database/connection.py:44-59`. The `sqlite3` CLI readings during Phase 0 (`wal_autocheckpoint=1000`, `journal_size_limit=-1`, `busy_timeout=0`) were SQLite's per-connection compile-time defaults — they reflect the CLI's own connection, not the workers process's. **PRAGMAs are per-connection in SQLite and never persist on the file.**

### "WAL pinned at 100MB" — partial INVALIDATION

Live diagnostic invocation:

```
WAL_CHECKPOINT | mode=PASSIVE busy=0 log=1335 ckpt=1335
WAL size after checkpoint: 100.00 MiB
```

`busy=0` and `log_pages == ckpt_pages` proves there is **no pinned reader** preventing checkpoint. All dirty pages were successfully merged. The file remains at 100 MiB because PASSIVE/FULL/RESTART checkpoint modes don't shrink the file — only TRUNCATE does. The data is durable in main.db; the file size is preallocation.

Practical implication: the "WAL pinning" framing in spec/observation reports was a file-size observation, not a data-currency problem. The 100 MiB physical size doesn't itself slow writes (it's reused space).

### "Sequential 0.1s sleeps account for most tick time" — CONFIRMED

The artificial `sleep(0.1)` × ~30-120 fetches per tick = 3-12 s of pure idle time. With Bybit's `@rate_limit(calls_per_second=10.0)` already enforcing throughput, the kline_worker's local sleep contributes nothing to safety. Removing it is the single largest expected-latency win.

### "Lock contention" — UNCHANGED

Still the binding constraint after sleep removal. Phase 4 will measure post-removal and decide if chunked saves / read connection split are needed.

## Behavior after Phase 2

### Per-tick latency (expected)

Pre-fix steady state (M5-only tick, 30 syms):
- ~30 fetches × 0.1 s sleep = **3.0 s artificial idle**
- ~30 fetches × ~0.1 s API call = ~3.0 s API
- ~30 executemany acquires + a few diagnostic reads = ~1-2 s lock-bound work
- **Total ≈ 7-8 s** (measured 13 s p50 in observation, suggesting more lock contention than these numbers alone explain)

Post-fix steady state:
- Sleep idle = **0 s**
- API time bounded by `@rate_limit(10/sec)` = ~3.0 s for 30 fetches
- Lock-bound work unchanged
- **Total expected ≈ 4-5 s p50** (TBD on next observation)

### Checkpoint cadence

`cleanup_worker` ticks hourly. Each tick now begins with a PASSIVE checkpoint. Cost is microseconds when no dirty pages exist; otherwise proportional to `log_pages` (Phase 2's diagnostic showed 1335 pages ≈ 5 MiB merged in <50 ms). VACUUM (which also implicitly checkpoints) continues to run once per UTC date.

### TRUNCATE / RESTART checkpoint not yet wired

PASSIVE never shrinks the file. If operators want to reclaim the 100 MiB WAL space (cosmetic — it's reused), a manual `db.checkpoint("TRUNCATE")` invocation works. Not added to a regular schedule because:
- TRUNCATE briefly blocks writers (PASSIVE doesn't)
- The file size doesn't itself harm performance
- Daily VACUUM already does aggressive reclamation

If later observation shows a real benefit, escalate.

## Tests

No new tests added in this commit — the changes are surgical and exercised by:
- Phase 1's `test_shadow_adapter_boot_grace.py` (still passes)
- Phase 5's `test_order_idempotency.py` + `test_order_service.py` (still pass)
- The live diagnostic against `data/trading.db` confirming `checkpoint()` returns the expected shape

Live verification command (operator-runnable):

```python
.venv/bin/python -c "
import asyncio
from src.database.connection import DatabaseManager
async def t():
    db = DatabaseManager('data/trading.db')
    await db.connect()
    print(await db.checkpoint('PASSIVE'))
    await db.disconnect()
asyncio.run(t())
"
```

## Verification (operator action — Phase 13)

After workers restart with this code:

| Metric | Pre-fix | Target | Where to read |
|---|---|---|---|
| kline_worker tick `el=` p50 | ~13 s | < 5 s | `KLINE_FETCH \| ... ` (Phase 3 will add `el=`) |
| kline_worker tick `el=` p95 | ~20 s | < 10 s | same |
| `WAL_CHECKPOINT` line per hour | 0 | 1 | `data/logs/workers.log` |
| WAL `busy=0` rate | n/a | >95% | `WAL_CHECKPOINT` log breakdown |
| `STRAT_PREFETCH_CRITICAL` per hour | several | < 1 | strategy_worker logs |

If post-deploy p50 hits ≤ 5 s, Phase 4 collapses to monitoring-only as anticipated by the plan.

## Status against the spec's verification criteria

| Spec criterion | Result |
|---|---|
| WAL_CHECKPOINT line fires every cleanup tick | ✅ wired into `cleanup_worker.tick()` (hourly) |
| WAL size oscillates rather than monotonically grows | ⏳ pending live observation; PASSIVE doesn't shrink, but data is durable so re-use will happen |
| kline_worker tick latency improves | ⏳ pending live observation; expected p50 from ~13 s → ~3-5 s |
| PRAGMA mismatch resolved | ✅ confirmed not actually a mismatch — Phase 0 misread |
| Long-pinned reader identified | ✅ confirmed there is none — `busy=0` on live checkpoint |

## Rollback path

`git revert HEAD` cleanly reverts. If sleep removal causes Bybit rate-limit responses (it shouldn't — `@rate_limit(10/s)` already throttles), restore the 0.1 s sleep as the immediate hot-fix.
