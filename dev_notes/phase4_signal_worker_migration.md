# Phase 4 (a) — SignalWorker Migration (50 coins + sweet spot 1:00)

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 3 commit:** `c54819b` (preceded this).

## Files modified

- `src/workers/signal_worker.py`:
  - Parent class `BaseWorker` → `SweetSpotWorker`.
  - Sweet spot `1:00` (after structure_worker's 0:45).
  - Universe source: scanner → `settings.universe.watch_list`.
  - New `_signal_cache: dict[symbol -> SignalResult]` populated each tick.
  - New public method `get_signal(coin: str) -> SignalResult | None` for Phase 6.
  - Existing `SIG_BATCH` line gains `drift_ms`.
  - New `SIG_TICK_SUMMARY | universe=50 signals=N mean_conf=M el=Xms drift_ms=D | {ctx()}` (mean_conf is -1 when no signals to distinguish from genuine 0).
  - `_on_universe_change` replaced with deprecation no-op.

## Verification

`SignalWorker.__bases__` = `(SweetSpotWorker,)`; `get_signal` exposed; settings parse cleanly.

## Hard rule check

- HR-1 / HR-5: watch_list is the only source.
- HR-4: kline 0:30 < structure 0:45 < signal 1:00.
- HR-6: one commit (paired with Phase 4 regime + strategy, but each is its own commit).
