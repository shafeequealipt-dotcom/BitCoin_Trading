# Phase 0.5 — Sweet-Spot Scheduling Investigation

**Investigated:** `src/workers/sweet_spot_scheduler.py` (245 lines), `src/workers/base_worker.py` (392 lines). HEAD = `8dca492`.

## A. Mechanism

Wall-clock-anchored scheduler. Each worker has an MM:SS offset within a `window_minutes`-long window (default 5 min). Two helper functions are pure:

- `parse_sweet_spot(value: str) -> tuple[int, int]` (lines 26-61) — validates `MM:SS` format, returns `(minutes, seconds)`.
- `seconds_until_next_sweet_spot(spot, *, window_minutes=5, now=None, skip_threshold_s=0.1)` (lines 64-101) — given current time, returns positive seconds until next firing.
- `is_at_sweet_spot(...)` (lines 104-121) — for tests.

`SweetSpotScheduler` class (lines 141-245):
- `__init__(worker_name, offset, window_minutes)` — emits `SWEET_SPOT_REGISTERED | worker=… offset=… window_min=…` at construction.
- `wait_for_sweet_spot()` — `await asyncio.sleep(seconds_until_next())` then computes `drift_ms = (pos_in_window - spot_s) * 1000`, normalizes split-window cases, emits `SWEET_SPOT_FIRED | worker=… offset=… drift_ms=… fires=…`. Returns drift_ms.
- `get_stats()` — `{worker, offset, window_min, fires, mean_drift_ms, max_drift_ms, last_drift_ms}`.

## B. SweetSpotWorker base class (`base_worker.py:226-393`)

Wraps `BaseWorker.start()` to await `_scheduler.wait_for_sweet_spot()` BEFORE each tick (not after). This guarantees chain ordering applies from boot — kline doesn't tick at boot before structure; both wait for their respective spots in the SAME first window after startup.

Drift exposed as `self._last_drift_ms` (line 277), used in worker tick summaries (e.g., `SCANNER_TICK_SUMMARY ... drift_ms=X`).

Slow-tick warning per `_TICK_SLOW_PER_WORKER` overrides (lines 37-42): kline=8s, strategy=10s, structure=6s, regime=4s; default 2s.

## C. Configuration (validated upstream by `SweetSpotsSettings.__post_init__`)

`config.toml [workers.sweet_spots]`:
```toml
window_minutes = 5
kline_worker     = "0:30"
structure_worker = "0:45"
signal_worker    = "1:00"
regime_worker    = "1:15"
strategy_worker  = "1:30"
altdata.funding_rates = "1:45"
scanner_worker   = "4:00"
```

Validation (per blueprint HR-4): `SweetSpotsSettings.__post_init__` enforces chain ordering before any worker starts — empty watch_list, out-of-window minutes, or mis-ordered chain are rejected at startup.

## D. Cycle-active gating (Phase 4 plan)

Today, sweet-spot scheduling fires regardless of trading toggle. The `SweetSpotWorker` base does NOT check any layer-active state. Layer 1A semantics (always-on) are **implicit** — workers fire whether trading is on or off.

Phase 4 makes this explicit:
- `worker_tier: WorkerTier` field on `BaseWorker.__init__` (default UTILITY).
- 4 LAYER1A workers (kline, price, altdata, news) keep running unconditionally.
- 3 LAYER1B workers (structure, signal, regime), 1 LAYER1C (strategy), 1 LAYER1D (scanner) skip their tick when `not layer_manager.is_cycle_active()`. The skip happens INSIDE `tick()` at the top, after `wait_for_sweet_spot()` returns. The scheduler still fires; the worker just does no work.
- Skip log: `LAYER1B_SKIP | reason=cycle_inactive | {ctx()}` (DEBUG by default to avoid log spam when trading is off).

## E. Cold-start boundary wait (Phase 4 plan)

When operator runs `/layer 2 on` and `/layer 3 on` mid-window (e.g., at `:02:30`):

1. Toggle flips `_layer_active` true.
2. `LayerManager.start_layer()` schedules `asyncio.create_task(self._await_resume_boundary())`.
3. `_await_resume_boundary()` computes `_seconds_to_next_window_boundary(now=time.time())` returning `(300 - (now % 300)) % 300`.
4. Logs `CYCLE_RESUME_WAIT | next_boundary_in_sec=150`.
5. `await asyncio.sleep(s)` then logs `CYCLE_RESUME | boundary={iso}`.
6. Workers naturally wake at the next sweet spots within the new window — no need to push events to them.

## F. Restructure change plan (Phase 4)

1. Add `WorkerTier(str, Enum)` to `src/core/types.py` with values `LAYER1A, LAYER1B, LAYER1C, LAYER1D, LAYER4, LAYER5, UTILITY`.
2. Add `worker_tier: WorkerTier = WorkerTier.UTILITY` to `BaseWorker.__init__` and accept via `super().__init__(...)`.
3. Tag each worker class with its tier:
   - `kline_worker.py`, `price_worker.py`, `altdata_worker.py`, `news_worker.py` → LAYER1A
   - `structure_worker.py`, `signal_worker.py`, `regime_worker.py` → LAYER1B
   - `strategy_worker.py` → LAYER1C
   - `scanner_worker.py` → LAYER1D
   - `profit_sniper.py`, `position_watchdog.py`, `recovery_planner.py` → LAYER4 (stays even after Phase 8 renumbering — tier reflects sub-layer, not toggle layer)
4. Add `is_cycle_active()` on `LayerManager`: returns `_layer_active[2] AND _layer_active[3]` (OLD numbering for Phase 4; Phase 8 migrates).
5. Inject `services` dict into the 4 LAYER1B/1C/1D workers that need it (StructureWorker, SignalWorker, RegimeWorker, StrategyWorker). ScannerWorker already has it. WorkerManager wires them.
6. Add cycle-skip at top of `tick()` for each 1B/1C/1D worker.
7. Add cold-start `_await_resume_boundary` and `_seconds_to_next_window_boundary` on LayerManager; call from `start_layer(2)` / `start_layer(3)` when `is_cycle_active()` becomes true.

## G. Verification criteria

- T4.1: with `/layer 2 off`, over 10 min, kline/altdata/news/price keep emitting `LAYER1A_TICK_DONE`; structure/signal/regime/strategy/scanner emit `LAYERnX_SKIP | reason=cycle_inactive`.
- T4.2: at `:02:30`, toggle layer 2+3 ON; observe `CYCLE_RESUME_WAIT | next_boundary_in_sec=150` then `CYCLE_RESUME` at next `:00:00`.
- T4.3: cycle latency p95 unchanged within ±5% when fully active.
