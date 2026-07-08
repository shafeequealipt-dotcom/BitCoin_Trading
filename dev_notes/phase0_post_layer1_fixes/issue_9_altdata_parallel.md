# Issue 9 — altdata_worker chronic slow ticks (5–9s vs 2s threshold)

**Status:** PRESENT — `asyncio.gather` exists but underlying batches are slow.
**Tier:** 4 (log noise; not blocking).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 197-211 (Finding #6); current `BASE_WORKER_TICK_SLOW name=altdata_worker el=4673ms` 07:56 UTC.

## A. Mechanism

`AltDataWorker.tick` at `src/workers/altdata_worker.py:92-135` builds a list of fetch tasks based on per-feed cadences (funding/OI hot every tick; F&G and on-chain on slower schedules), then `asyncio.gather`s them at line 135. The gather itself is correctly parallel.

The slowness is in the underlying batch fetches. Each `_fetch_funding_rates()` (line 227) and `_fetch_open_interest()` (line 230) executes a single multi-symbol Bybit REST call covering the full 50-coin watch list. Bybit's response latency for these batched calls is typically 2-5 seconds; combined with HTTP setup and JSON parse, single-feed elapsed lands at 4-9s. F&G (line 224) is a single REST call to AlternativeMe; on-chain (line 233) is conditional.

The `_BASE_WORKER_TICK_SLOW_SECONDS` constant at `src/workers/base_worker.py:27` is 2.0 — applied uniformly to all workers. AltData's ~5-9s elapsed exceeds this threshold every tick (or near every tick), producing repeated `BASE_WORKER_TICK_SLOW` warnings:

```
2026-04-27 07:56:49.674 | WARNING  | base_worker:start:505 | BASE_WORKER_TICK_SLOW | name=altdata_worker el=4673ms threshold_ms=2000 interval_s=300.0
```

The interval is 300s (5 min), so an elapsed of 5s consumes 1.7% of the cadence — operationally fine. The threshold is mismatched, not the latency.

## B. Dependencies

- Bybit REST client (`src/trading/client.py` and bound services) — shared across workers; rate-limit aware.
- AlternativeMe / F&G client.
- On-chain client (lightweight; conditional).
- Sweet-spot scheduler (`src/workers/sweet_spot_scheduler.py`) fires altdata at a specific offset within the 5-min cycle — preserved.

## C. Constraints

- Must not raise the global `_BASE_WORKER_TICK_SLOW_SECONDS` for other workers (some legitimately need a tight 2s bound).
- Must not split the worker into 3 separate workers (scope expansion; against the prompt).
- Must not exceed Bybit's REST rate quota (current pattern is 1 batch call per feed per tick — well under quota).
- Per-feed timing must be visible without breaking existing `ALTDATA_TICK_DONE` consumers.

## D. Fix candidates

1. **Per-feed timing + per-worker tick_slow threshold override (chosen).**
   - Wrap each sub-fetch with `time.monotonic()`, attach elapsed to the existing `ALTDATA` summary (or a new `ALTDATA_TICK_DONE` line if the prompt's name is preferred).
   - Add per-worker threshold override: class attribute or `[altdata].tick_slow_threshold_sec` config, default 12.0 for altdata. `BaseWorker.start` reads the per-worker override before applying the global default.
2. Per-coin parallelization within funding/OI. Rejected for now — Bybit's batched API is more efficient than per-coin calls; per-coin would actually slow it down due to TCP overhead.
3. Cache aggressively. Rejected — caching is already in place (cached_size=50 visible in logs). Stale data isn't the issue.

## E. Observability gap

- Today's `ALTDATA | fg=None funding=50 oi=0 el=4672ms` doesn't break down per-feed. Operators can't tell which feed is the slow one.
- No per-worker threshold override; one-size-fits-all.

## F. Verification approach

- Unit test: minimal — visual log inspection sufficient. Mocking REST timings is low-value.
- Live trial: 60-min window post-deploy → zero `BASE_WORKER_TICK_SLOW name=altdata_worker`. `ALTDATA_TICK_DONE | funding_ms=... oi_ms=... fg_ms=...` shows real per-feed timings.

## G. Rollback path

Two atomic commits:
- Revert per-feed timing → log shape returns to previous.
- Revert threshold override → altdata returns to 2s threshold; warnings return.

No DB or state changes.
