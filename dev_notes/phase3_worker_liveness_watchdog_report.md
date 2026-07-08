# Phase 3 — Worker Liveness Watchdog (cycle-gate aware)

**Date:** 2026-04-27
**Commits:**
- `4d330d8` — `WorkerLivenessTracker` (cycle-gate aware) + log tags + log routing + tests
- `a07c253` — `WorkerLivenessWatchdog` (Layer 1A always-on BaseWorker) + tests
- `0af5204` — Hook BaseWorker tick + SweetSpotScheduler into tracker
- `2c4e4b6` — Wire tracker + watchdog into WorkerManager (settings + config + register pre-WM_START)
- `8c41226` — Extend `/health` Telegram command with worker-liveness section
- `65219e8` — Widen effective grace for sweet-spot workers (false-positive prevention)

## Problem

The 09:58 dead-workers incident remained invisible for **7 hours** because:
1. `LAYER1{B,C,D}_TICK_SKIP` is logged at DEBUG and filtered out at the configured `log_level=INFO`.
2. `WORKER_FIRST_TICK` exists at `base_worker.py:213/507` but only fires on the FIRST successful tick — its absence isn't surfaced anywhere.
3. The /health command showed cycle history but no per-worker registration vs first-tick comparison.

Phase 0 evidence proved the workers weren't actually hung — they were skipping per the cycle gate (`is_cycle_active() = L2 AND L3 = True AND False`). But the operator had no way to distinguish "intentionally skipping (L3 OFF)" from "really hung" without grepping workers.log for SWEET_SPOT_FIRED vs LAYER1B_TICK_DONE pairs.

## Fix

Build a watchdog that:
- Tracks per-worker registration timestamp, first-tick timestamp, last-tick timestamp, tick count, sweet-spot fire count.
- Probes the tracker every 30 s and classifies each worker as `HEALTHY`, `NEVER_TICKED`, `OVERDUE`, or `IDLE_CYCLE_GATE`.
- Emits structured warnings (`WORKER_NEVER_TICKED`, `WORKER_TICK_OVERDUE`) and a continuous heartbeat (`WORKER_LIVENESS_HEARTBEAT`).
- Sends rate-limited Telegram alerts (default 1/hour/worker) for unhealthy states.
- Surfaces the same data in `/health` so the operator sees it without grep.

Critically: **cycle-gate aware**. A `cycle_gated=True` worker that hasn't ticked while `is_cycle_active()` is False is `IDLE_CYCLE_GATE`, NOT `NEVER_TICKED`. This is the load-bearing property — without it, the watchdog would have generated 5 false alarms continuously through the 7-hour L3=OFF window.

## Files changed

| File | Change |
|---|---|
| `src/core/worker_liveness.py` (NEW) | `WorkerLivenessTracker` (register / record_tick / record_sweet_spot / snapshot / is_alive). Module-level singleton via `get_default_tracker` / `set_default_tracker`. ~340 lines. |
| `src/workers/worker_liveness_watchdog.py` (NEW) | `WorkerLivenessWatchdog(BaseWorker)`: utility-tier, always-runs, depends on Tracker + LayerManager.is_cycle_active() + optional AlertManager. ~210 lines. |
| `src/core/log_tags.py` | Added `WORKER_NEVER_TICKED`, `WORKER_TICK_OVERDUE`, `WORKER_LIVENESS_HEARTBEAT`. |
| `src/core/logging.py` | Routed new component `worker_liveness` to `workers.log`. |
| `src/workers/base_worker.py` | Added `_liveness_record_tick(name)` helper; called from both `BaseWorker.start` (line ~243) and `SweetSpotWorker.start` (line ~539) after the existing `LAYER1*_TICK_DONE` log. |
| `src/workers/sweet_spot_scheduler.py` | Inline lazy import + `record_sweet_spot()` after the `SWEET_SPOT_FIRED` log. |
| `src/workers/manager.py` | `WorkerManager.__init__` constructs the tracker singleton. `_create_workers` appends `WorkerLivenessWatchdog` after `CleanupWorker`. `_run_worker` calls `tracker.register(...)` before `WM_START`. Late-bind loop wires `_layer_manager` onto the watchdog. |
| `src/config/settings.py` | New `WorkerLivenessSettings` dataclass (4 tunables, validated). `Settings.worker_liveness` field. `_build_worker_liveness` parser. Wired through `from_files`. |
| `config.toml` | New `[worker_liveness]` block with operator-facing comment block. |
| `src/telegram/handlers/system.py` | `/health` extended with a "Worker liveness" section. Reads `services["worker_liveness"]` and renders per-status counts plus a list of unhealthy workers (capped at 10). |
| `tests/test_worker_liveness.py` (NEW) | 16 cases covering tracker behaviour, including the cycle-gate false-positive prevention. |
| `tests/test_worker_liveness_watchdog.py` (NEW) | 9 cases covering watchdog tick logic, alert rate-limiting, and conservative defaults when LM is missing or raises. |

## Tunables (defaults from `config.toml`)

```toml
[worker_liveness]
watchdog_interval_sec = 30
first_tick_grace_sec = 90
overdue_multiplier = 2.0
alert_rate_limit_sec = 3600
```

| Knob | Default | Purpose |
|---|---|---|
| `watchdog_interval_sec` | 30 | How often the watchdog probes the tracker. Validated >= 10. |
| `first_tick_grace_sec` | 90 | Raw grace from `WM_START` before `WORKER_NEVER_TICKED`. Validated >= 30. For SweetSpotWorker (`expected_interval_s ≥ 300`), the EFFECTIVE grace is widened to `expected + grace = 390 s` so the boot warmup doesn't false-alarm. |
| `overdue_multiplier` | 2.0 | Multiplier on `expected_interval_s` for `WORKER_TICK_OVERDUE`. Validated >= 1.5. |
| `alert_rate_limit_sec` | 3600 | Per-worker Telegram alert rate limit. Validated >= 60. |

## Verification — automated

```
pytest tests/test_worker_liveness.py
       tests/test_worker_liveness_watchdog.py
       tests/test_layer_state_sync.py
       tests/test_layer_manager_persistence.py
       tests/test_corrected_layer1_integration.py
       tests/test_logging_routing.py
       70 passed
```

The 06:18 reference boot's first-tick latencies (158-352 s for the 7 SweetSpotWorker workers) are explicitly covered by the new `test_sweet_spot_worker_in_boot_window_is_healthy` and `test_sweet_spot_worker_alarms_after_effective_grace` cases.

## Verification — operator-driven (post-deploy)

After the next workers-process restart (operator-controlled — Phase 3 changes ship dormant; the running workers process won't pick up the new code until restarted):

| # | Trial | Pass criterion |
|---|---|---|
| 3.1 | Restart workers, tail workers.log for 2 min | `WORKER_LIVENESS_WATCHDOG_INIT_FAIL` MUST NOT appear. `WORKER_FIRST_TICK \| name=worker_liveness_watchdog` fires within 90+30 = 120 s. `WORKER_LIVENESS_HEARTBEAT` fires every 30 s with `total>=18`. |
| 3.2 | With L3=OFF (current state): wait 5 min | `idle_cycle_gate=5` in heartbeat. Zero `WORKER_NEVER_TICKED`. Zero `WORKER_TICK_OVERDUE`. |
| 3.3 | `/start trading` (toggles L2 + L3): wait 5 min | `idle_cycle_gate=0` in heartbeat. `healthy=18`. All 5 cycle-gated workers tick at their next sweet-spot. |
| 3.4 | Inject `await asyncio.sleep(180)` at the top of `structure_worker.tick()`, restart workers, revert | `WORKER_NEVER_TICKED \| name=structure_worker cycle_active=True` fires within 390+30 = 420 s. ONE Telegram alert sent. Subsequent ticks within `alert_rate_limit_sec` produce more log lines but no additional Telegram. |
| 3.5 | `/health` Telegram | "Worker liveness" block visible after "Layer toggles". Counts add up. With L3=OFF: `idle_cycle_gate=5`. With L3=ON for >5 min: `healthy=18`. |
| 3.6 | Force a worker to skip 2 consecutive ticks | `WORKER_TICK_OVERDUE` fires once `last_tick_age_s > expected_interval_s × 2`. |

## Cycle-gate awareness — why it matters

Without it, the watchdog would behave as follows on the current production state (L3=OFF the entire 7-hour run):

```
Every 30 s:
  WORKER_NEVER_TICKED | name=structure_worker ...
  WORKER_NEVER_TICKED | name=signal_worker    ...
  WORKER_NEVER_TICKED | name=regime_worker    ...
  WORKER_NEVER_TICKED | name=strategy_worker  ...
  WORKER_NEVER_TICKED | name=scanner_worker   ...
  Telegram alert × 5
```

Within an hour the operator's phone would be unusable. The cycle-gate-aware classification means these 5 workers report `STATUS_IDLE_CYCLE_GATE` while L3 is OFF — visible in the heartbeat's `idle_cycle_gate=5` counter and in `/health`, but NOT alarmed.

## Out of scope for this phase

- Phase 4 cycle-gate observability upgrade (promote `LAYER1{B,C,D}_TICK_SKIP` from DEBUG to INFO with rate-limit; emit `WORKER_TICK_START`/`WORKER_TICK_FAIL` events for cradle-to-grave coverage).
- Phase 5 re-verification of the 10 prior post-Layer-1 fixes.

## Rollback

If the watchdog produces false positives or unexpected load:
- Set `[worker_liveness] watchdog_interval_sec = 600` (10 min) to reduce probe rate.
- Revert commit `8c41226` to drop the `/health` extension while keeping the watchdog.
- Revert commit `2c4e4b6` to disable the watchdog entirely while keeping the tracker (no Telegram alerts, but no functional impact on workers).
- Worst case: revert all 6 commits (`git revert 65219e8 8c41226 2c4e4b6 0af5204 a07c253 4d330d8`) — leaves the codebase identical to before Phase 3.
