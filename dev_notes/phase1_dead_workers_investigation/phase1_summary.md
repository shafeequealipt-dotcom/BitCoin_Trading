# Phase 1 — Per-Worker Findings: Cycle-Gate Skip (NOT Silent Death)

**Investigation date:** 2026-04-27
**Source evidence:** `dev_notes/phase0_dead_workers_capture/` and `dev_notes/phase0_dead_workers_capture.md`

## Conclusion (one paragraph)

All 5 workers labelled "silently dead" by the prompt — `structure_worker`, `signal_worker`, `regime_worker`, `strategy_worker`, `scanner_worker` — are blocked at the **same single line** of code: `src/workers/base_worker.py:486` (`continue` inside the cycle-gate branch of `SweetSpotWorker.start()`). They are NOT hung. They register, await their sweet-spot, the scheduler fires (`SWEET_SPOT_FIRED` at INFO), the cycle gate evaluates `not self._layer_manager.is_cycle_active()` which is `True` because Layer 3 is OFF, the worker logs a `LAYER1{B,C,D}_TICK_SKIP` line at DEBUG (filtered out at the configured INFO log level), and `continue`s back to the next sweet-spot wait. No exception, no traceback, no actual hang.

## Per-worker matrix

| Worker | Tier | `cycle_gated` | sweet-spot offset | `WORKER_FIRST_TICK` in 06:18 run | `WORKER_FIRST_TICK` in 09:58 run | Block site | Cause |
|---|---|---|---|---|---|---|---|
| structure_worker | 1B | True (`structure_worker.py:48`) | 0:45 | 06:20:45 (158s) ✓ | none ✗ | `base_worker.py:486` | gate False (L3=OFF); skip-log filtered |
| signal_worker | 1B | True (`signal_worker.py:44`) | 1:00 | 06:21:00 (173s) ✓ | none ✗ | `base_worker.py:486` | same |
| regime_worker | 1B | True (`regime_worker.py:40`) | 1:15 | 06:21:19 (192s) ✓ | none ✗ | `base_worker.py:486` | same |
| strategy_worker | 1C | True (`strategy_worker.py:54`) | 1:30 | 06:21:32 (205s) ✓ | none ✗ | `base_worker.py:486` | same |
| scanner_worker | 1D | True (`scanner_worker.py:59`) | 4:00 | 06:24:00 (352s) ✓ | none ✗ | `base_worker.py:486` | same |

## Evidence answers to the Phase 1 verification gate

1. **WHERE is each worker blocked?** `src/workers/base_worker.py:486` — the `continue` inside the cycle-gate skip branch of `SweetSpotWorker.start()`'s while loop. Same line for all 5.
2. **WHAT is each blocked on?** They are NOT blocked. They are looping `wait_for_sweet_spot → cycle gate False → continue`. No await is hung; the loop iterates correctly every sweet-spot fire.
3. **Is the bug shared or per-worker?** It's a SHARED conditional skip, not a bug. All 5 workers hit the same `is_cycle_active() == False` branch because all 5 are `cycle_gated=True` and `is_cycle_active() = L2 AND L3 = True AND False`.
4. **WHEN was this introduced?** The cycle gate was added in the Layer 1 restructure Phase 4 (commit chain ending at `e94d1e3 phase9-layer1-restructure: observation harness`). It is correct, intentional code. The "silent" aspect — DEBUG-level skip log — has been there since the original cycle-gate implementation.
5. **WHY didn't the prior good run have this symptom?** In the 06:18 boot, Layer 3 was ON during each of the 5 workers' first sweet-spots, so `is_cycle_active()` returned True and they ticked. In the 09:58 boot, the operator-driven L3=ON windows (33s, 6s) did not overlap any of the 5 workers' sweet-spot offsets (0:45 / 1:00 / 1:15 / 1:30 / 4:00), so each one always saw L3=OFF when its scheduler fired.

## What the prompt got right vs wrong

| The prompt says | Phase 0 evidence shows |
|---|---|
| "Five workers SILENTLY DEAD since restart" | They are correctly skipping. The "silent" part is real (DEBUG log filtered), the "dead" part is not. |
| "BaseWorker.tick() never produces output" | Correct — but because the gate `continue`s before reaching `await self.tick()`, not because tick() hangs. |
| "NO ERRORS. NO TRACEBACKS. NO EXCEPTIONS." | Correct — there is no error, because there is no failure. |
| "The hang is likely in one of these shared services on first read" | Wrong. There is no hang. The shared services (`structure_engine`, `shadow_kline_reader`, `ta_cache`) are never reached because tick() is skipped. |
| "EVENT_LOOP_BLOCKER lag=692ms top_tasks=[Task-28, telegram_bot_worker, structure_worker]" | A real but unrelated 692ms boot-time block, fired ONCE at 09:58:49.630. Not the cause of the 7-hour silent skip. |
| "Layer 3 toggle persistence regression — REPRODUCED TWICE LIVE" | CORRECT. This is the actual primary bug. Fixing it makes the cycle gate True and ends the silent-skip pattern. |

## Implication for Phases 2/3/4

- **Phase 2 — primary fix.** L3 persistence ordering at `layer_manager.py:289` is the actual root cause of the user-visible failure. Once L3 stays ON, the cycle gate evaluates True and the 5 workers tick.
- **Phase 3 — watchdog logic must be cycle-gate-aware.** A worker with `cycle_gated=True` whose `_layer_manager.is_cycle_active()` is False is INTENDED to be silent. The watchdog must mark such a worker as "idle: gate inactive" rather than "DEAD".
- **Phase 4 — observability upgrade, not hang fix.** Promote `LAYER1{B,C,D}_TICK_SKIP` from DEBUG to INFO with rate-limiting (e.g. 1 per worker per 10 min), and add a /health line "5 cycle_gated workers idle (L3 OFF)" so operators see the system state instead of nothing. Add `WORKER_TICK_START` / `WORKER_TICK_FAIL` for cradle-to-grave coverage as originally planned.

## What is NOT a bug

- TACache and ShadowKlineReader's `asyncio.Lock()` in `__init__` is fine in Python 3.11 — `_LoopBoundMixin` lazily binds on first use via `events.get_running_loop()`. The earlier hypothesis that "lock is bound to wrong loop" doesn't apply.
- The Phase 0 sub-paths (B deadlock, C DB lock, D cache wait) all assumed a hang. None apply.

No source files modified in Phase 1. Investigation only.
