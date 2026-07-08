# Phase 0 — Dead Workers: Live Diagnostic Capture & Headline Finding

**Capture window:** 2026-04-27 16:53–16:58 UTC (system uptime 6:58:10 since 09:59 UTC restart)
**System state at capture:** `L1=ON L2=ON L3=OFF` — same as the original 09:58 observation.

## Headline Finding (overrides prompt hypothesis)

**The 5 "dead" workers are NOT silently hung. They are correctly skipping per the cycle-gate logic, and the skip is invisible because it logs at DEBUG.**

### Evidence chain

1. **`SweetSpotWorker.start()` cycle gate** (`src/workers/base_worker.py:475–486`):
   ```python
   if (
       self.cycle_gated and self._layer_manager
       and hasattr(self._layer_manager, "is_cycle_active")
       and not self._layer_manager.is_cycle_active()
   ):
       if self.layer_tier_tag:
           log.debug(  # ← DEBUG level
               f"{self.layer_tier_tag}_TICK_SKIP | "
               f"sub={self.name} reason=cycle_inactive ..."
           )
       continue   # ← skip the actual tick body
   ```
   The skip happens BEFORE `await self.tick()` (line 495), so no `WORKER_FIRST_TICK`, no `LAYER1*_TICK_DONE`, no error, no traceback.

2. **`is_cycle_active()` definition** (`src/core/layer_manager.py:1249`):
   ```python
   return self._layer_active.get(2, False) and self._layer_active.get(3, False)
   ```
   Returns `True` IFF both Layer 2 AND Layer 3 are on. Layer 3 has been OFF the entire 7-hour run.

3. **Cycle-gated workers** (only these 5):
   ```
   src/workers/structure_worker.py:48: cycle_gated = True
   src/workers/signal_worker.py:44:    cycle_gated = True
   src/workers/regime_worker.py:40:    cycle_gated = True
   src/workers/strategy_worker.py:54:  cycle_gated = True
   src/workers/scanner_worker.py:59:   cycle_gated = True
   ```
   Healthy SweetSpotWorkers (kline, altdata) are NOT cycle_gated → they tick normally.

4. **Log level is INFO** (`config.toml:log_level = "INFO"`) → DEBUG skip lines are filtered out, so the operator sees `SWEET_SPOT_FIRED` but nothing after it.

5. **TICK_SKIP grep returned 0 matches** in `data/logs/workers.log` — confirming the skip exists but is filtered.

6. **Layer state has been L3=OFF the entire window**:
   - 1st sample 10:32:43: `disk={1:T 2:T 3:F} memory={1:T 2:T 3:F} match=true`
   - Last sample 16:55:44: same
   - `LAYER_STATE_SYNC | match=true` consistently — no drift, no flip.

7. **Worker-uptime confirmation**: process uptime 06:58:10 (from `ps -o etime`), started ~09:59 UTC. This is the same process the prompt observed.

8. **Comparison to last good run (06:18 boot)**: every one of the 5 problem workers DID emit `WORKER_FIRST_TICK` (structure 06:20:45 158s after start, signal 06:21:00 173s, regime 06:21:19 192s, strategy 06:21:32 205s, scanner 06:24:00 352s). The 06:18 process had L3=ON during their first sweet-spots — so the gate let them through. The 09:58 process never had L3=ON during a sweet-spot fire (the two brief L3=ON windows reported in the prompt — 09:59:10–09:59:43 (33s) and 10:10:37–10:10:43 (6s) — did NOT overlap any of the 5 workers' offsets: structure 0:45, signal 1:00, regime 1:15, strategy 1:30, scanner 4:00).

### Implication for the planned phases

- **Phase 4 (silent-death root-cause fix) is NO LONGER A FIX. There is no silent death.** Phase 4's contingency tree (lock-init / deadlock / DB lock / cache wait) is irrelevant — none of those failure modes matches the actual cause.
- **Phase 2 (Layer 3 persistence ordering) is the primary fix.** Once L3 stays ON across sweet-spot windows, the cycle gate becomes True and the 5 workers will tick. This is the behaviour we saw in the 06:18 run.
- **Phase 3 watchdog must distinguish `cycle_inactive` skip from a real hang** — otherwise it will false-alarm whenever L3 is intentionally OFF.
- **Phase 4 becomes an observability upgrade**, not a hang fix:
  - Promote `LAYER1{B,C,D}_TICK_SKIP` from DEBUG to INFO with per-worker rate-limit (1 per 10 min) so operators can see "workers skipping due to L3 OFF".
  - Add a health probe / Telegram /health line: "5 cycle_gated workers idle: cycle_inactive (L3=OFF)".

### Boot-time clue (separate observation)

`EVENT_LOOP_BLOCKER lag=692ms top_tasks=[Task-28,telegram_bot_worker,structure_worker]` at 09:58:49.630 — a 692 ms event-loop block at boot, naming structure_worker. This is a one-off boot-time issue, NOT the cause of the silent-skip pattern. Likely a heavy first-init in structure_worker's setup; tracked separately.

## Captures produced

All in `dev_notes/phase0_dead_workers_capture/` (and mirrored in `/tmp/dead_workers/`):

| File | Content |
|---|---|
| `_capture_start.txt` | Capture start timestamp (2026-04-27 16:53:25 UTC) |
| `stacks_pid396_workers.txt` | py-spy dump (workers, MainThread idle in `select(timeout=0.85)` with `sched_count=56`) |
| `stacks_pid396_workers_locals.txt` | py-spy dump --locals (439 lines) |
| `stacks_pid397_server.txt`, `stacks_pid384_shadow.txt` | Server, shadow stacks (idle event loops) |
| `flame_pid396_workers.svg` | py-spy record 60s @ 50Hz, 1397 samples, 0 errors |
| `process_metadata.txt` | ps -o for 3 PIDs (uptime 06:58:10 confirms 09:59 boot) |
| `pid396_status.txt`, `pid396_wchan.txt` | /proc/396 status (S sleeping, ep_poll) |
| `db_state.txt`, `db_files.txt` | sqlite PRAGMA database_list, wal_checkpoint=922 frames OK |
| `recent_relevant_logs.txt` | 1663 filtered log lines (WORKER_FIRST_TICK, WM_START, SWEET_SPOT_FIRED, LAYER_STATE_*, ...) |
| `event_loop_lag.txt` | 4 EVENT_LOOP_BLOCKER events total (boot-time, not steady-state) |
| `init_log.txt` | All WM_START + WORKER_FIRST_TICK events across both runs |
| `boot_first_200.txt` | First 200 lines of current workers.log |
| `file_mtimes.txt` | File mtimes of relevant src files (most recent: manager.py 09:43) |
| `recent_commits.txt`, `recent_changes_window.txt`, `fbd13dea_show.txt` | Git history |
| `sys_mem.txt`, `sys_uptime.txt`, `sys_disk.txt` | System state (3.9GB RAM, load 0.88, disk fine) |

## Phase 0 verification gate

| Criterion | Status |
|---|---|
| Stack dumps captured for all 3 processes | ✓ (pid 396, 397, 384) |
| 5 dead worker tasks identified with blocked-on state | ✓ (correctly skipping at SweetSpotWorker.start:486) |
| Git log identifies what changed in last 6–24h | ✓ (16 commits 03:00–10:30, latest manager.py mtime 09:43) |
| Captures stored persistently | ✓ (dev_notes/phase0_dead_workers_capture/) |
| Captures timestamped before any other change | ✓ (16:53–16:58 UTC) |

**No code touched in Phase 0.** Only `data/trading.db` backup added (`data/trading.db.bak-pre-dead-workers-fix-20260427-165401`) and git tags `pre-dead-workers-fix` + `pre-dead-workers-fix-20260427`.

## Next steps

1. Phase 1 — formalise the per-worker findings using the evidence above (each of the 5 workers blocks at the same place: `SweetSpotWorker.start():486 continue` after the cycle gate).
2. Phase 2 — fix Layer 3 persistence ordering so toggles stay ON. This is the PRIMARY user-visible fix.
3. Phase 3 — build the watchdog with cycle-gate-aware logic so it doesn't false-alarm on L3=OFF state.
4. Phase 4 — observability upgrade (promote skip to INFO+rate-limit) instead of the hang-fix originally planned.
5. Phase 5 — re-verify the 10 prior post-Layer-1 fixes.
