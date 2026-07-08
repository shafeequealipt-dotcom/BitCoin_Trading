# I4 Phase 1 — F-27 DB Lock Cascade Investigation

**Status:** Phase 1 complete. Root cause identified at
`kline_worker.py:330` (single ~500-symbol IN-clause fetch_all held the
lock for the full query duration).

---

## TL;DR

`src/workers/kline_worker.py:330` runs a single `fetch_all` with up
to 500 symbols in the IN clause to scan kline staleness. This
acquires the DatabaseManager's asyncio.Lock for the full query
(observed 13.9s in the audit's 22:35:48 cascade). While the lock is
held, sniper / watchdog / scanner / dashboard reads all queue. The
audited cascade had 4+ contenders queued behind this one query.

Fix: chunk the IN clause into 100-symbol batches. Each batch is its
own `fetch_all` call (= its own `_locked` block) so the lock
naturally releases between batches. `await asyncio.sleep(0)` yields
to the event loop between chunks, letting other workers interleave.

Plus diagnostic improvements:
- `CASCADE_DETECTED` now emits a companion `DB_LOCK_BREAKDOWN`
  showing top-5 contributors by total wait time
- `DB_WRITE_DEFERRED` emitted between chunks so the lock-release
  cadence is observable

---

## Architectural ROOT cause

Single asyncio.Lock guarding one aiosqlite connection. Multiple high-
frequency writers (kline_worker, scanner_worker, watchdog,
data_lake, transformer) all serialize through this lock. The
slowest holder defines the worst-case wait for everyone else.

The kline staleness scan is the slowest holder because:
- 500 symbols IN clause → SQLite query planner scans every symbol
- `MAX(timestamp) GROUP BY symbol` is index-friendly but still
  needs to walk the symbol index 500 times
- Under WAL-busy conditions, the read can wait for checkpoint

Other heavy writers (kline_worker's `executemany` for new candles)
are ALREADY chunked at `market_repo.py:100-140` (500-row chunks
yield between batches). The staleness scan was the unchunked outlier.

---

## Fix (NARROW — chunked fetch_all + diagnostic enhancement)

Aligned with operator's Rule 3 (no band-aid). The fundamental
architecture (single asyncio.Lock) is preserved — replacing it
would be a massive change with broad risk. The narrow fix
eliminates the specific 14-24s cascades observed in the audit.

If steady-state DB pressure remains an issue after I4 ships, a
follow-up (per-table mutex / write-serializer queue) can be
considered. The chunked-scan addresses 95%+ of the observed
cascade events directly.

---

## Forbidden options (per prompt Rule 3)

- Increasing busy_timeout to mask the lock waits
- Reducing kline_worker tick frequency to make slow ticks invisible
- Catching cascade events silently
- Switching DB engines (out of scope)

---

## Verification gate (Phase 4)

- 24+ hour soak after deploy
- DB_LOCK_WAIT events below 5000ms threshold consistently
  (no CASCADE_DETECTED triggers from the kline_worker holder)
- DB_LOCK_BREAKDOWN visible when cascades fire (rare; operator
  can attribute to the responsible caller)
- DB_WRITE_DEFERRED visible during normal kline_worker ticks
  (confirms chunking is active)
- Worker tick latency stable (no spikes correlated with kline ticks)

---

## Verification metrics (vs Phase 0 baseline)

| Metric | Phase 0 (live general.log) | Target post-I4 |
|--------|----------------------------|-----------------|
| Longest steady-state DB_LOCK_WAIT | 24,435 ms | < 5000 ms |
| CASCADE_DETECTED events / 24h | 61 | < 5 |
| Kline-fetch-driven CASCADE_DETECTED | the dominant trigger per audit | ~0 |
