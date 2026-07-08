# Phase 4 (c) — StrategyWorker Migration (50 coins + sweet spot 1:30)

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 4b commit:** `7ff6fce` (preceded this).

## Files modified

- `src/workers/strategy_worker.py`:
  - Parent class `BaseWorker` → `SweetSpotWorker`.
  - Sweet spot `1:30` (last in chain before scanner at 4:00).
  - Universe source: scanner → `settings.universe.watch_list` (50 coins).
  - New `_score_cache: dict[str, float]` populated after Layer 2 scoring.
  - New public method `get_score(coin: str) -> float | None` for Phase 6.
  - New `STRAT_PREFETCH | el=Xms db=... ta=... h1_db=... h1_ta=... src=market_repo+ta_engine ...` always-emit log (was only emitted via threshold-gated SLOW/CRITICAL warnings before).
  - `STRAT_CYCLE_DONE` line gains `drift_ms`.

## Verification

`StrategyWorker.__bases__ = (SweetSpotWorker,)`; `get_score` exposed; module compiles.

## Hard rule check

- HR-1 / HR-5: watch_list is the only source.
- HR-4: kline 0:30 < structure 0:45 < signal 1:00 < regime 1:15 < strategy 1:30.
- HR-6: one commit (third of three for Phase 4).
