# Phase 7 — Cleanup of Obsolete Rotation-Driven Backfill Handlers

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 6 commit:** `bb75115` (preceded this).

## Summary

Removed the dead `_on_universe_change` rotation-handler dispatcher (in `manager.py`) and the per-worker handler methods (5 of 7 had them). Under the corrected Layer 1 architecture, workers operate on the full 50-coin watch_list and don't need to react to ScannerWorker's selection rotations — there are no rotation-driven backfills/cleanups.

## Files modified

- `src/workers/manager.py`:
  - Removed the master `_on_universe_change` closure (12 lines).
  - Removed the `scanner.subscribe(_on_universe_change)` registration.
  - Replaced with a 6-line comment block referencing blueprint §13.5.
- `src/workers/kline_worker.py`: removed `_on_universe_change` deprecation no-op (~17 LOC).
- `src/workers/signal_worker.py`: removed `_on_universe_change` deprecation no-op.
- `src/workers/regime_worker.py`: removed `_on_universe_change` deprecation no-op.
- `src/workers/altdata_worker.py`: removed `_on_universe_change` deprecation no-op.
- `src/workers/price_worker.py`: removed `_on_universe_change` deprecation no-op.

The `MarketScanner._subscribers` list and `subscribe(callback)` API are kept intact (zero-cost — the list is empty post-Phase-7, the iteration in scanner_worker's notification loop runs over an empty list and is a no-op). Future use cases (e.g. a non-worker subscriber) remain possible without re-introducing the API.

## Verification

- All 8 worker classes import cleanly.
- `hasattr(W, '_on_universe_change')` is now False for every one of the 7 data workers + ScannerWorker.
- The dispatcher comment in `manager.py` references the blueprint section for future maintainers.

## Behavior change

- ScannerWorker tick still updates `MarketScanner._active_universe` and writes the `active_universe` table — that path is unchanged.
- The scanner subscriber-notify loop in scanner_worker.py now iterates over an empty list (no callbacks registered).
- `KLINE_BACKFILL`, `KLINE_STATE_CLEANUP`, `SIGNAL_BACKFILL`, `SIGNAL_REMOVED`, `REGIME_BACKFILL`, `REGIME_STATE_CLEANUP`, `ALTDATA_ADDED`, `ALTDATA_REMOVED`, `PRICE_UNSUB`, `PRICE_UNIVERSE_SYNC` log lines no longer fire (they were emitted only from the deleted handlers).

## Hard rule check

- HR-1: workers operate on watch_list (still true).
- HR-2: workers don't synchronize via inter-worker events (now structurally true — there is no event bus).
- HR-6: one commit covers Phase 7.

## Next phase

Phase 8 — cycle code review.
