# Phase 0.6 — Layer Toggle / `layer_state.json` Investigation

**Investigated:** `src/core/layer_manager.py` (1046 lines), `data/layer_state.json` (current contents), and Telegram `/layer` handlers (`src/telegram/handlers/control_handler.py` and `src/telegram/handlers/system.py` — to be read in Phase 8 when actually editing). HEAD = `8dca492`.

## A. Current scheme (3 layers)

Constants at `layer_manager.py:64-66`:
```python
LAYER_DATA = 1
LAYER_BRAIN = 2
LAYER_EXECUTION = 3
```

`_layer_active = {1: False, 2: False, 3: False}` (line 73). Initialized False; restored from disk via `_load_persisted_state()` at line 78 (BUT only `user_stopped` is restored — layers always start False per line 116 docstring).

## B. Persistence

`_STATE_FILE = data/layer_state.json` (line 28). `_persist_state()` writes:
```json
{
  "layer_active": {"1": true, "2": false, "3": false},
  "user_stopped": true,
  "timestamp": "2026-04-26T22:51:34.859582+00:00"
}
```
Current file (read at session start): `{1: T, 2: F, 3: F, user_stopped: T}` — operator stopped trading; only data is on.

## C. Toggle semantics

- `start_layer(1)` — unconditional.
- `start_layer(2)` — requires `_layer_active[1]`.
- `start_layer(3)` — requires `_layer_active[1]` AND `_layer_active[2]`.
- `stop_layer(N)` — cascades downward: stop_layer(1) stops 2 and 3.

Each successful transition emits `LAYER_TOGGLE | layer=N from=X to=Y reason=… actor=… | {ctx()}` (Phase 2 of 5-fixes engagement, commit `028a6d5`/`386e1b3`).

Special methods:
- `emergency_close_all` (lines 222-280) — closes positions, stops L2+L3, sets `user_stopped=True`.
- `snapshot_layer_state()` (lines 1028-1046) — returns frozen `LayerSnapshot` (capture-and-pass for Layer 3 OFF enforcement, the 5-fixes Phase 2 work).

## D. LayerSnapshot dataclass (lines 31-58)

```python
@dataclass(frozen=True)
class LayerSnapshot:
    layer_active: Mapping[int, bool]            # MappingProxyType-wrapped, immutable
    captured_at_monotonic: float
    captured_at_wall: str = field(default_factory=...)  # ISO UTC

    def is_layer_active(self, layer: int) -> bool: ...
    def age_ms(self) -> float: ...
```

Used by Brain → Strategy → OrderService chain. OrderService re-checks against live LayerManager at placement time and aborts with `Layer3RaceError` if disagreement on `purpose=layer3_entry`.

## E. Brain loop (lines 328-560)

Alternating CALL_A/CALL_B every `brain_interval_seconds = 150`. Strict alternation: A → sleep(150) → B → sleep(150) → A → … Mandatory sleep is load-bearing per docstring at line 329-340.

Background trade execution via `_execute_trades_background` (line 637) with 5-min timeout and BRAIN_DO_TRADE per-trade summary.

## F. Restructure change plan (Phase 8)

Migrate to 5 layers per blueprint Section 12.1:

| New | Name | Old mapping |
|---|---|---|
| 1 | DATA | old.1 (DATA) |
| 2 | ANALYSIS | derived from old.2 (BRAIN intent) |
| 3 | BRAIN | old.2 (BRAIN actual) |
| 4 | EXECUTION | old.3 (EXECUTION) |
| 5 | MONITORING | derived from old.3 (EXECUTION-related; Watchdog/Sniper) |

`scripts/migrate_layer_state_to_v2.py` reads v1, writes v2 with mapping above. Backup as `data/layer_state.v1.json.bak`. Idempotent (no-op if already v2).

LayerManager updates (Phase 8):
1. Constants `LAYER_DATA=1, LAYER_ANALYSIS=2, LAYER_BRAIN=3, LAYER_EXECUTION=4, LAYER_MONITORING=5`.
2. `_layer_active = {1..5: False}`.
3. `is_cycle_active()` returns `_layer_active[2]` (ANALYSIS).
4. Semantic helpers: `can_run_brain() = _layer_active[3]`, `can_execute_orders() = _layer_active[4]`, `can_run_monitoring() = _layer_active[5]`.
5. Persist `schema_version=2`.
6. Dependency cascade: L2 requires L1; L3 requires L1+L2; L4 requires L1+L2+L3; L5 requires L1.
7. Stop cascade: stop Lk → stop Lk+1..L5.

Telegram `/layer` (Phase 8): accept `1..5`. `/layer 4 off` stops execution but keeps L5 monitoring. `/layer 5 off` stops Watchdog/Sniper.

Code touch-up sites:
- `src/apex/gate.py` — `is_layer_active(3)` for execution → `is_layer_active(4)`.
- `src/workers/{profit_sniper,position_watchdog,recovery_planner}.py` — `is_layer_active(3)` → `is_layer_active(5)` (these workers are MONITORING).
- `src/trading/services/order_service.py` — Layer3RaceError check (5-fixes Phase 2 commit `028a6d5`) reads layer 3 today; Phase 8 updates to layer 4 (EXECUTION).
- `src/telegram/handlers/{system,control_handler}.py` — `/health` and `/layer` handlers.

## G. Verification criteria

- Migration script run on staged copy produces v2 schema and `.v1.json.bak`.
- Each of 5 layers toggles independently with correct cascade.
- `/health` shows 5 rows.
- Cycle latency unchanged.
- No worker hardcodes a layer number — all use semantic helpers.

## H. Anomaly resolved in Phase 0 step 0.0

Working tree had `A src/workers/layer_manager.py` re-staged after commit `d6e1b1c` deleted it. Diff against canonical `src/core/layer_manager.py` showed it was a partial duplicate (LayerSnapshot defined at the same place). Per `d6e1b1c` intent ("phase2a-fixup: re-delete orphan src/workers/layer_manager.py"), removed in Phase 0 step 0.0 commit `8dca492`. Working tree now has only the canonical `src/core/layer_manager.py`.
