# Phase 4 (b) — RegimeWorker Migration (50 coins + sweet spot 1:15)

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 4a commit:** `0da6ae6` (preceded this).

## Files modified

- `src/workers/regime_worker.py`:
  - Parent class `BaseWorker` → `SweetSpotWorker`.
  - Sweet spot `1:15` (after signal_worker's 1:00).
  - Universe source: scanner → `settings.universe.watch_list`.
  - `t0 = time.monotonic()` added at tick start for `el=` field.
  - REGIME_GLOBAL line gains `drift_ms`.
  - New `REGIME_TICK_SUMMARY | universe=50 global=R per_coin_size=N el=Xms drift_ms=D | {ctx()}` at end of tick.
  - New public `get_regime(coin: str)` — thin wrapper around `RegimeDetector.get_coin_regime` for Phase 6.
  - `_on_universe_change` replaced with deprecation no-op.
  - Removed the `if self._scanner:` guard in the per-coin detection block — now `if universe:` instead, since scanner is no longer the universe source.
  - First-tick `coin_regime_history` restore filter now uses watch_list directly.

## Verification

`RegimeWorker.__bases__ = (SweetSpotWorker,)`; `get_regime` exposed.

## Hard rule check

- HR-1 / HR-5: watch_list is the only source.
- HR-4: kline 0:30 < structure 0:45 < signal 1:00 < regime 1:15.
- HR-6: one commit (paired with Phase 4a/c).
