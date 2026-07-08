# Tier 5 — Operational quality (combined investigation + proposals)

Five issues. T5-4 + T5-5 share root cause. T5-1 + T5-2 are expected to resolve via Tier 1 fixes.

## T5-1 (F2 — kline_worker slow ticks 10-23 s vs 8 s threshold)

**Status**: No standalone code change. Expected to resolve via T1-4 incremental_vacuum migration.

**Rationale**: T1-4 baseline confirmed VACUUM held the EXCLUSIVE lock up to 21 s today, blocking kline_worker's ticker_cache writes during the window. After T1-4 migration to `auto_vacuum=INCREMENTAL`, the daily 21 s freeze is replaced with hourly <1 s incremental ticks. kline_worker tick durations should drop back below the 8 s threshold for the vast majority of cycles. Re-measure required after operator runs `scripts/t1_4_migrate_to_incremental_vacuum.sh` + restarts services.

## T5-2 (F3 — profit_sniper slow ticks 2.0-5.0 s vs 2 s threshold)

**Status**: No standalone code change. Same expected resolution as T5-1.

**Rationale**: profit_sniper's 5 s tick interval means a 5 s VACUUM-induced lock-wait consumes the entire window. T1-4's incremental migration removes the 21 s freezes; tick durations should drop. The remaining low-grade slowness (2.0-2.5 s ticks) is acceptable and matches pre-VACUUM baseline. Re-measure required.

## T5-3 (F5 — SL gateway rate-limit reject thrash)

**Status**: Shipped — sentinel-source coalescing added.

**Defect**: Up to 7 rejects per symbol per 30 s window. Multiple writers (`profit_sniper_trail`, `trail_activation`, `trail_update`, `sentinel_advisor`) compete for the per-symbol R4 30 s rate-limit slot.

**Fix scope** (within this engagement):
- T1-2 added 10 s trail-source coalesce.
- T5-3 adds 10 s sentinel-source coalesce (`sentinel_advisor` + `sentinel_deadline`) at the same `_push_sl_to_shadow` chokepoint.
- profit_sniper sources retain their existing SNIPER_CAP clamp + tighten_cooldown=15 s.

Single-writer-of-record consolidation (Architectural Theme 1) deferred to a future engagement — the prompt's plan acknowledged this. The combined coalesces eliminate the in-flight thrash for trail + sentinel without restructuring the SL ownership model.

## T5-4 + T5-5 (F19 + Phase5 F-1 — WS staleness + reconnect storm)

**Status**: Shipped — staleness threshold bumped 120 s → 600 s.

**Defect**: Private-WS `_STALE_MESSAGE_THRESHOLD_SECONDS = 120 s` triggers a reconnect every 2-4 min during trade-quiet windows because the private-channel WS produces NO payloads when the account is idle. 92 reconnects per 8 h session today.

**Fix**: bump threshold to 600 s. TCP keepalive at the OS layer + pybit's library-level auto-reconnect both still catch transport drops in seconds; the local threshold is only a secondary safety net. Quiet trade-windows no longer trigger spurious reconnects.

**Per-symbol exec event lag** during the up-to-600 s "no payload" window is mitigated by `bybit_demo_ws_worker`'s reconnect path + the watchdog's existing REST reconciliation. Operator can tighten back toward 120 s if reconnect events drop visibly post-fix and they want a tighter detection latency for genuinely-stale sockets — operator-tunable via the module constant.
