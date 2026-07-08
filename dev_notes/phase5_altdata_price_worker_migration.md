# Phase 5 — AltDataWorker + PriceWorker Migration

**Engagement:** Layer 1 corrected migration.
**Date:** 2026-04-26
**Phase 4c commit:** `a0735ba` (preceded this).

## AltDataWorker

- Parent `BaseWorker` → `SweetSpotWorker`. Sweet-spot wake-up at funding offset (default 1:45).
- Three independent sub-cadences via monotonic deadlines:
  - funding_rates: every wake-up (5-min window).
  - open_interest: every `open_interest_minutes` (default 5).
  - fear_greed: every `fear_greed_minutes` (default 60).
  - onchain: piggybacks funding cadence (low-frequency upstream).
- Universe scanner → `settings.universe.watch_list` (50).
- `_funding_cache` populated each funding fetch; new public `get_funding(coin)` accessor for Phase 6.
- New `ALTDATA_FUNDING_TICK / ALTDATA_OI_TICK / ALTDATA_FG_TICK` per-source summaries. Legacy `ALTDATA` aggregate line preserved.
- `_on_universe_change` → deprecation no-op.
- Commit `84f6606`.

## PriceWorker

- Stays on `BaseWorker` (continuous WS — sweet-spot scheduling would slow failover detection without benefit).
- Universe scanner → `settings.universe.watch_list` (50).
- Removed three "scanner empty" guards; defensive `watch_list_empty` is the only failure mode.
- `_on_universe_change` → deprecation no-op (the rotation-driven full-reconnect is no longer needed; the worker stays subscribed to the same 50 forever unless watch_list changes).
- Existing `PRICE_WS_HEALTH | status=... msgs_per_min=... subscribed=...` heartbeat preserved.
- (This commit folded into Phase 5b.)

## Hard rule check

- HR-1 / HR-5: watch_list is the only source.
- HR-2: each AltData sub-source has its own monotonic deadline; no inter-worker sync.
- HR-4: AltData funding sweet spot 1:45 sits between regime 1:15 and scanner 4:00.
- HR-6: 2 commits (84f6606 altdata, next commit price).
