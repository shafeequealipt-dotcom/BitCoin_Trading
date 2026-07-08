# Phase 2 — KlineWorker Migration (50 coins + sweet spot 0:30)

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 1 commit:** `b14ac0d` (preceded this).

## Summary

Migrated `KlineWorker` from `BaseWorker(scanner-based, 45-s polling)` to `SweetSpotWorker(watch_list, 0:30 sweet spot)`. The worker now operates on the full 50-coin `config.universe.watch_list` and fires once per 5-min window at 30 s past each window boundary — aligned with M5 candle close finality.

## Files modified

- `src/workers/kline_worker.py`:
  - Parent class `BaseWorker` → `SweetSpotWorker`.
  - `__init__`: passes `sweet_spot=settings.workers.sweet_spots.kline_worker` and `window_minutes=settings.workers.sweet_spots.window_minutes` to super; legacy `interval_seconds=settings.workers.market_data_interval` removed. The `scanner` parameter is retained as None-safe optional (legacy callers may still pass it; not read by tick).
  - Universe source: `await self._scanner.get_active_universe()` → `list(self.settings.universe.watch_list)`. Three "scanner failure" branches collapsed to one defensive `KLINE_UNIVERSE_EMPTY | reason=watch_list_empty` (should never fire — UniverseSettings.__post_init__ enforces min size + format).
  - `_tracked_symbols` initial seed: `settings.bybit.default_symbols` → `settings.universe.watch_list`.
  - Added per-timeframe `tf_fetched` dict and `skipped_cooldown` counter.
  - Replaced the legacy `Kline worker: fetched N klines for S symbols` line with `KLINE_TICK_SUMMARY | universe={n} fetched={n} saved={n} skipped={k} tf_split={M5:a,H1:b,H4:c,D1:d} errors={e} el={ms}ms drift_ms={d} | {ctx()}`.
  - Added post-tick `KLINE_FRESHNESS_WARN` watchdog: queries `klines` for newest M5 timestamp per watch_list symbol, emits one WARNING per symbol older than `_KLINE_FRESHNESS_THRESHOLD_S` (600 s) or with no kline rows at all.
  - Promoted `_LAG_QUERY_MAX_SYMBOLS = 500` to module level (shared between `KLINE_WRITE_LAG` and `KLINE_FRESHNESS_WARN` scans).
  - `_on_universe_change` body replaced with a deprecation no-op (debug-level log + return). Kept the method so the master callback dispatcher in `manager.py` doesn't crash if it still fires before Phase 7 deletes the registration.
- (Did not need to modify `manager.py` — KlineWorker's constructor signature is unchanged.)

## Behavior change

- Universe scope: 30 → 50 coins.
- Cadence: every 45 s → once per 5-min window (at +30 s offset).
- API call rate: ~60/min → ~15/min (4× reduction).
- Rotation handler: backfill+cleanup → no-op (will be deleted in Phase 7).

## Existing log lines preserved

`KLINE_FETCH`, `KLINE_GAP`, `KLINE_WRITE_LAG`, `KLINE_STRAGGLER`, `KLINE_FETCH_FAIL`, `KLINE_CIRCUIT_BREAKER` all unchanged. The legacy free-text "Kline worker:..." line is the one removal; replaced by the structured `KLINE_TICK_SUMMARY`.

## Verification

- `Settings._load_fresh()` parses cleanly with the new sweet-spots config.
- `KlineWorker.__bases__` now resolves to `(SweetSpotWorker,)` — confirmed.
- `KlineWorker` constructed with the existing manager.py signature continues to work; no manager wiring changes needed in this phase.
- Tests: existing suite covers BaseWorker contract (still satisfied via SweetSpotWorker → BaseWorker chain). The Phase-1 sweet-spot tests cover scheduler behavior. Live verification (sweet spot fires at expected wall-clock offsets, drift < 1000 ms p95, KLINE_TICK_SUMMARY appears once per 5-min window) is deferred to the Phase 9 24-hour observation; trial-level live verification before Phase 3 starts is recommended once the operator restarts trading-workers.

## Hard rule check

- HR-1 (workers on watch_list): YES — direct `settings.universe.watch_list` read, scanner not consulted.
- HR-2 (no sync between workers): YES — only the per-worker SweetSpotScheduler.
- HR-4 (chain ordering): KlineWorker fires at 0:30; structure_worker (next, Phase 3) at 0:45 — strict <.
- HR-5 (watch_list as truth): YES.
- HR-6 (per-phase commits): this is one commit covering only kline_worker + this report.

## Risks & deferred items

- `KLINE_TICK_SUMMARY`'s `saved` field currently mirrors `fetched` because `MarketService.get_klines` wraps `MarketRepository.save_klines` which uses INSERT OR IGNORE — counting actual inserted rows would require capturing rowcount through the service, an extra plumbing change not justified here. Acceptable: `saved == fetched` accurately reflects "what we tried to save"; `saved < fetched` would only show duplicate-rate, which is already implicit in the kline-retention sweep.
- `_on_universe_change` body is dead but the method signature stays until Phase 7 deletes the master dispatcher registration in manager.py.
- Live D-3 lock-contention behavior not characterized in this commit; per the plan, the migration should reduce contention frequency by ~6× (1 fire per 5 min vs. ~7 fires per 5 min) but the lock-hold-time per fire is unchanged. Phase 9 captures this delta.

## Next phase

Phase 3 — structure_worker migration (50 coins + sweet spot 0:45). Builds on this commit.
