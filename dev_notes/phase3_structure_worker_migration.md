# Phase 3 — structure_worker Migration (50 coins + sweet spot 0:45)

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 2 commit:** `e118eec` (preceded this).

## Summary

Migrated `StructureWorker` from `BaseWorker(scanner-based, 60-s polling)` to `SweetSpotWorker(watch_list, 0:45 sweet spot)`. Worker now operates on the full 50-coin `config.universe.watch_list` and fires once per 5-min window 15 s after KlineWorker (so the X-RAY analysis reads fresh klines). Adds a public `get_setup_score(coin)` accessor for Phase 6's new ScannerWorker.

## Files modified

- `src/workers/structure_worker.py`:
  - Parent class `BaseWorker` → `SweetSpotWorker`.
  - `__init__`: `interval_seconds=settings.structure.worker_interval_seconds` removed; new `sweet_spot=settings.workers.sweet_spots.structure_worker` and `window_minutes=...` passed to super.
  - `_get_universe`: scanner path removed; reads `settings.universe.watch_list` directly. Batch wrap-around math unchanged (batch_size=25 + 50 coins = 2 sweeps).
  - Renamed log line `XRAY_TICK` → `XRAY_TICK_SUMMARY` and added `universe={len(full_universe)}` + `drift_ms={self._last_drift_ms}` fields. Existing fields (batch, symbols, analyzed, errors, cached, session, setups, skips, el) preserved.
  - New `XRAY_CACHE_HEALTH` log emitted per tick: `size`, `oldest_age_s`, `hits`, `misses`, `hit_rate`. Helps operators detect batch-cursor stalls.
  - New public method `get_setup_score(coin: str) -> float | None`. Reads `_cache.get(coin)` then `getattr(analysis, "setup_score", None)`; returns None on miss or non-numeric.
  - `XRAY_TICK_ERR` was already promoted DEBUG → WARNING in a prior phase (no change here).
  - `ShadowKlineReader` integration unchanged (already async-aiosqlite from the 2026-04-25 fix).

## Behavior change

- Universe scope: 30 → 50 coins.
- Sweet spot: 0:45 of every 5-min window (15 s after kline_worker's 0:30).
- A full universe sweep now takes 2 ticks (50/25=2) ≈ 10 min.

## Verification

- `Settings._load_fresh()` parses cleanly.
- `StructureWorker.__bases__` = `(SweetSpotWorker,)`.
- `get_setup_score` exposed on the class.
- Live verification (drift, XRAY_TICK_SUMMARY at 0:45, cache size = 50 after 2 ticks) deferred to Phase 9.

## Hard rule check

- HR-1 (workers on watch_list): YES — direct `settings.universe.watch_list` read.
- HR-2 (no inter-worker sync): YES.
- HR-4 (chain ordering): kline 0:30 → structure 0:45 → signal 1:00 — strict <.
- HR-6 (per-phase commits): one commit.

## Next phase

Phase 4 — SignalWorker, RegimeWorker, StrategyWorker (3 commits).
