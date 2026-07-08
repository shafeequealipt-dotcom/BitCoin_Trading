# Phase 0.7 — Observability Baseline

**Investigated:** all 33 worker `.py` files in `src/workers/`. HEAD = `8dca492`.

## A. Logger usage

Every worker file (except `__init__.py`, `settings.py`, `sniper_models.py`, `sniper_ring_buffer.py`) declares `log = get_logger(...)` (1 hit each per `grep -c '^log\.\|log = get_logger'`). Loguru via `src/core/logging.get_logger`. Context bind via `ctx()` from `src/core/log_context.py` appended as `| {ctx()}` to every structured log line.

## B. Per-worker tick instrumentation matrix (today)

| Worker | TICK_START? | TICK_DONE? | elapsed_ms? | processed count? | Slow-tick threshold | Notes |
|---|---|---|---|---|---|---|
| kline_worker | ✗ | ✓ (`KLINE_TICK_SUMMARY`) | ✓ | ✓ symbols, errors | 8s | also emits `KLINE_FETCH`, `KLINE_WRITE`, `WAL_CHECKPOINT_*` (5-fixes Phase 1) |
| structure_worker | ✗ | ✓ (`XRAY_TICK_SUMMARY`, `XRAY_CACHE_HEALTH`) | ✓ | ✓ analyzed/errors/cached/setups/skips | 6s | also `XRAY_SESSION_ERR`, `XRAY_TICK_ERR` |
| signal_worker | ✗ | ✓ (`SIGNAL_TICK`/similar — TBD per file read) | partial | partial | 2s default | needs Phase 1 instrumentation |
| regime_worker | ✗ | ✓ | ✓ | ✓ | 4s | emits global + per-coin regime logs |
| strategy_worker | ✓ (`STRAT_L1`/`STRAT_L2`/`STRAT_L3` markers) | ✓ (`STRAT_L4_HANDOFF`, `STRAT_TICK_SLOW`/`STRAT_HEALTH` per Phase 11) | ✓ | ✓ | 10s | `STRAT_PREFETCH_CRITICAL`, `STRAT_SKIP_STALE`, `M4_DECISION/GATED` (5-fixes Phase 4) |
| altdata_worker | ✗ | ✓ | partial | per-source | 2s | sub-cadences for funding/OI/F&G |
| news_worker | ✗ | ✓ | ✓ | ✓ articles | 2s | NEWS_FETCH_DONE |
| scanner_worker | ✗ | ✓ (`SCANNER_TICK_SUMMARY`) | ✓ | ✓ scored/selected/forced_in/mean_score/top | 2s | per-coin DEBUG `SCANNER_SELECTED`; Phase 5 `SCANNER_HYSTERESIS` (5-fixes) |
| price_worker | ✗ | partial (`PRICE_WS_HEARTBEAT` 5min) | ✗ | msgs/min | 2s | continuous WebSocket; Phase 12 `PRICE_WS_HEALTH` |
| profit_sniper | ✗ | ✓ (`M4_DECISION`/`M4_GATED` per 5-fixes Phase 4) | ✓ | ✓ | 2s | type-aware cooldown |
| position_watchdog | ✗ | ✓ (`WD_POLL_LAG`, watchdog summary) | ✓ | ✓ | 2s | brain trigger via UrgentQueue |
| cleanup_worker, discovery_worker, etc. | ✗ | partial | partial | partial | 2s default | utility tier; instrumentation patchy |

**Common pattern**: a *_TICK_SUMMARY line at end of tick with `el=Xms drift_ms=Y`. No `*_TICK_START` markers — the start side is implicit via `SWEET_SPOT_FIRED | worker=… drift_ms=…` from the SweetSpotScheduler.

## C. Cross-cutting tags introduced by 5-fixes

- Phase 1 D-3: `KLINE_SAVE_CHUNKED`, `WAL_CHECKPOINT_SCHEDULED`, `WAL_CHECKPOINT_ESCALATE`, `WAL_CHECKPOINT_ERR`, `DB_LOCK_WAIT` (caller frame), `DB_LOCK_HIST` (top callers).
- Phase 2 Layer 3: `LAYER_TOGGLE`, `ORDER_REJECT_LAYER3_OFF`, `ORDER_REJECT_LAYER3_RACE`, `ORDER_LAYER3_OFF_FORCED`, `ORDER_GATE_NO_LM`, `ORDER_SVC_LAYER_MANAGER_ATTACHED`, `purpose=` field on order events.
- Phase 3 Brain: `CRED_REFRESH_ATTEMPT/_OK/_RETRY/_FAILED_BLOCKING`, `CLAUDE_PROC_STALL_60S/_120S/_240S`, `BRAIN_FAILURE_CASCADE`.
- Phase 4 Sniper: `M4_DECISION`, `M4_GATED`.
- Phase 5 Universe: `SCANNER_HYSTERESIS`.

`BaseWorker.start()` global tags:
- `WORKER_FIRST_TICK | name=… el_to_first_tick_ms=… first_tick_el_ms=…` (Phase 10 milestone).
- `BASE_WORKER_TICK_SLOW | name=… el=Xms threshold_ms=Y` (Phase 5 Stage-1/2 fix).

## D. Logging principles already in use

- Structured `TAG | k=v k=v | {ctx()}` format consistent across new tags from the 5-fixes engagement.
- `loguru` with `get_logger(component_name)` — components: `worker`, `xray`, `strategies`, `layer_manager`, `brain`, `database`, `apex`, `tias`, etc.
- `ctx()` appends OS-process correlation fields (process, time, level, thread).

## E. Restructure change plan (Phase 1)

Phase 1 introduces a STANDARDIZED pair of TICK_START / TICK_DONE markers for each LAYER1A/1B/1C/1D worker, plus a CycleTracker that aggregates per-cycle latency breakdowns.

Tags introduced (per blueprint Section 14.3):
```
LAYER1A_TICK_START | sub=kline trigger=sweet_0:30
LAYER1A_TICK_DONE  | sub=kline elapsed_ms=… next_in_ms=… processed=… errors=…
LAYER1B_TICK_START | LAYER1B_TICK_DONE
LAYER1C_TICK_START | LAYER1C_TICK_DONE
LAYER1D_TICK_START | LAYER1D_TICK_DONE
LAYER1B_CYCLE_START / _DONE
LAYER1C_CYCLE_START / _DONE
LAYER1D_CYCLE_START / _DONE
CYCLE_COMPLETE | cycle_id=c-… layer1a_ms=… layer1b_ms=… layer1c_ms=… layer1d_ms=… total_ms=… packages_ready=… qualified_pct=…
```

These are ADDITIVE — existing `*_TICK_SUMMARY` lines remain (they'll be retired in a follow-up consolidation but Phase 1 does NOT remove them).

CycleTracker (`src/core/cycle_tracker.py` — new file, Phase 1.3):
- `start_cycle(layer: str) -> str` returns `cycle_id` like `c-2026-04-27-21:30` (minute-aligned so 4 sub-layers share an ID for the same window).
- `end_cycle(layer, cycle_id) -> int` returns elapsed_ms.
- `get_recent(n=10) -> list[CycleSummary]`.
- `emit_complete()` logs `CYCLE_COMPLETE`.
- `start_hourly_flush_task()` — periodic (3600s) INSERT into `cycle_metrics` table.

`cycle_metrics` table (Phase 1.5 migration):
```sql
CREATE TABLE IF NOT EXISTS cycle_metrics (
  hour_ts INTEGER PRIMARY KEY,
  cycles_count INTEGER,
  layer1a_p50_ms INTEGER, layer1a_p95_ms INTEGER,
  layer1b_p50_ms INTEGER, layer1b_p95_ms INTEGER,
  layer1c_p50_ms INTEGER, layer1c_p95_ms INTEGER,
  layer1d_p50_ms INTEGER, layer1d_p95_ms INTEGER,
  total_p50_ms   INTEGER, total_p95_ms   INTEGER,
  qualified_pct_avg REAL,
  packages_count_avg REAL,
  created_at INTEGER DEFAULT (strftime('%s','now'))
);
```

`/health` Telegram handler — appends `async def health_command(...)` in `src/telegram/handlers/system.py`. Reads `services["cycle_tracker"].get_recent(10)` plus `layer_manager.get_status()`. Output exactly the 12-section block from blueprint Section 14.5.

## F. Verification criteria

- T1.1: `grep -c "LAYER1A_TICK_DONE" workers.log >= 12` over 60 min (kline ticks once per 5 min × 12).
- T1.2: `/health` reply lists 5 layer rows, real numbers, no `n/a`.
- T1.3: `SELECT COUNT(*) FROM cycle_metrics >= 1` after 1h.
- T1.4: cycle latency change vs baseline within ±5%.
