# Phase 6 — Cross-Cycle Data Freshness Measurement Report

**Date:** 2026-04-27
**Commits:**
- helper module + tests (cache_freshness.py)
- instrumentation (kline_worker, structure_worker, scanner_worker — record_write hooks + CYCLE_FRESHNESS emit)
- /health Data Freshness section

## Summary

Pure new instrumentation; no behavioural change. Module-level singleton helper records `(cache_name, key) → unix_ts` on every cache write across the 3 most-impactful workers; ScannerWorker emits `CYCLE_FRESHNESS` per cycle aggregating ages across all caches; `/health` renders a Data Freshness block.

## Files

| File | Change |
|---|---|
| `src/core/cache_freshness.py` (NEW) | 4 public functions; RLock; singleton dict; <50µs/call |
| `tests/test_cache_freshness.py` (NEW) | 7 cases incl. overhead bound |
| `src/workers/kline_worker.py` | `record_write("klines", f"{sym}:{tf}")` after fetch |
| `src/workers/structure_worker.py` | `record_write("xray", symbol)` after `_cache.set` |
| `src/workers/scanner_worker.py` | `record_write("packages", ...)` post `lm._coin_packages = packages` + emit `CYCLE_FRESHNESS` per cycle |
| `src/telegram/handlers/system.py` | `/health` Data Freshness block |

## New observability

```
CYCLE_FRESHNESS | cycle_id=X
                  klines_age_p50_ms=15234 klines_age_p95_ms=42150
                  xray_age_p50_ms=22500   xray_age_p95_ms=58900
                  packages_age_p50_ms=8000 packages_age_p95_ms=12000
                  klines_keys=120 xray_keys=50 packages_keys=12 | did=...
```

## Verification — automated

```
pytest 123 passed
```

## Verification — operator-driven (post-deploy)

| # | Trial | Pass criterion |
|---|---|---|
| 6.1 | `CYCLE_FRESHNESS` per cycle | 12/hour at 5-min cadence |
| 6.2 | Numbers match expectation | klines p50 30-90s, xray p50 60-180s, end-to-end ~90s |
| 6.3 | Slow chain detection | manually delay kline_worker by 15s → klines p50 rises |
| 6.4 | `/health` Data Freshness section visible | min/med/max per cache + key count |

## Out of scope

- Other workers (signal/regime/strategy) cache hooks — added later if specific gaps surface
- `CACHE_READ` per-read sampled DEBUG logs (deliberate — overhead concern)
- Freshness as execution gate (measurement only)
