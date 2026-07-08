# Phase 2 — Layer 3 Toggle Persistence Ordering Fix

**Date:** 2026-04-27
**Commits:**
- `bff3f16` — persist layer state AFTER toggle, not before
- `7bb5c54` — reverse drift recovery direction (memory wins)
- `5e4e8ec` — persistence-ordering tests for start_layer/stop_layer

## Bug summary

The `LAYER_STATE_SYNC` heartbeat (`src/core/layer_manager.py`) interacted with two latent bugs to produce the Layer 3 toggle revert regression observed twice live on 2026-04-27 (33-second lifetime at 09:59:10, 6-second lifetime at 10:10:37):

1. **`start_layer()` line 289 persisted BEFORE the layer-specific toggle ran**, so disk captured the pre-toggle `_layer_active` snapshot. Any subsequent toggle in the SAME `start_layer` call (e.g. cascaded `/start trading` toggling L2 then L3 within milliseconds) only updated memory, leaving disk stale.
2. **`_sync_state_with_disk()` overwrote memory from disk on drift** ("disk wins" semantics). Combined with bug 1, the next 60 s heartbeat reverted memory back to the stale disk state, silently dropping the operator's L3 toggle. Brain CALL_A then dropped its directives at `_run_brain_cycle:617`.

## Fix summary

| File | Change |
|---|---|
| `src/core/layer_manager.py` | Removed pre-toggle `_persist_state()` from `start_layer()`. Added persist after the success branch, plus per-early-return persists so `_user_stopped = False` mutation is durable even when dependency check rejects. Promoted `_persist_state()` to typed bool, emits `LAYER_STATE_PERSIST_OK` (INFO) and `LAYER_STATE_PERSIST_FAIL` (WARNING). Reversed drift recovery: default action `rewrite_disk` re-persists memory and emits `LAYER_STATE_DRIFT_RECOVERED \| direction=memory_to_disk`. Legacy `reload_memory` retained behind config flag. |
| `src/config/settings.py` | `LayerManagerSettings` gains `on_drift_action: str = "rewrite_disk"`. `__post_init__` validates against `{"rewrite_disk", "reload_memory"}`. `_build_layer_manager` parses both `[layer_manager.state_sync]` nested form and flat-key form. |
| `src/workers/manager.py` | `start_state_sync` call passes `on_drift_action` from settings. |
| `config.toml` | New `[layer_manager.state_sync]` block with `on_drift_action = "rewrite_disk"`. Operator-facing comment block above explains the values, defaults, and the regression history. |
| `tests/test_layer_state_sync.py` | `_make_layer_manager` factory now sets `_drift_action`. `test_sync_drift_reloads_from_disk` renamed and inverted to `test_sync_drift_default_rewrites_disk_from_memory`. New `test_sync_drift_legacy_reload_memory_from_disk` covers the retained legacy direction. `test_sync_disk_has_phantom_keys_ignored` switched to `drift_action="reload_memory"` because phantom-key filtering only runs on the disk→memory copy path. |
| `tests/test_layer_manager_persistence.py` (NEW) | 9 cases covering single-layer start, cascaded start (the regression), stop cascade, dependency-rejected start, persist failure / success events, drift recovered event, and the unknown-layer no-persist branch. |

## Verification — automated

```
pytest tests/test_layer_manager_persistence.py
       tests/test_layer_state_sync.py
       tests/test_corrected_layer1_integration.py
       tests/test_logging_routing.py
       tests/test_order_service/
       59 passed
```

The pre-existing `tests/test_phase7/*` collection errors (`ModuleNotFoundError: src.brain.prompt_builder` etc.) are unrelated to this work and predate Phase 0 — confirmed by `git log -- tests/test_phase7/`.

## Verification — operator-driven (post-deploy)

Run after the worker process picks up the new code (next restart). All checks expected within 5 minutes.

| # | Trial | Pass criterion |
|---|---|---|
| 2.1 | `/stop trading` → `/start 2` → wait 60 s | `data/layer_state.json` shows `{1:T, 2:T, 3:F}`. `LAYER_STATE_SYNC \| match=true` in workers.log. No `LAYER_STATE_DRIFT*` event. |
| 2.2 | From clean state: `/start trading` → wait 60 s | `data/layer_state.json` shows `{1:T, 2:T, 3:T}`. Wait additional 5 min — state still `{2:T, 3:T}`. No revert. |
| 2.3 | With everything ON: manually edit `layer_state.json` to `{3:false}` → wait 60 s | `LAYER_STATE_DRIFT_RECOVERED \| direction=memory_to_disk` event fires. `layer_state.json` re-written to `{3:true}`. |
| 2.4 | With everything ON: `/stop trading` | `data/layer_state.json` shows `{2:F, 3:F}` within 5 s. No drift event. |
| 2.5 | Set config `on_drift_action = "reload_memory"`, restart, repeat 2.3 | `LAYER_STATE_DRIFT \| action=reload_from_disk` fires (legacy event). Memory reverts to disk. |

The last trial (2.5) is optional, only needed if the operator wants to confirm the legacy fallback still works for emergency rollback.

## Out-of-band side effects

- **Phase 1 cycle-gate finding still applies.** The 5 cycle_gated workers (structure, signal, regime, strategy, scanner) only tick when `is_cycle_active() = L2 AND L3` is True. After the operator next runs `/start trading`, this fix ensures L3 stays ON, so the gate becomes True and the workers tick normally — the same behaviour observed in the 06:18 boot (which had L3=ON during each worker's first sweet-spot).
- **Phase 3 watchdog (next phase)** must be aware of the cycle-gate skip path so it doesn't false-alarm on the L2-without-L3 transient state.

## Rollback

If the new `rewrite_disk` direction surfaces a pathology in production, the operator can roll back without redeploying by editing `config.toml`:

```toml
[layer_manager.state_sync]
on_drift_action = "reload_memory"
```

then restarting workers. This restores the pre-fix drift behaviour. The persist-ordering fix in commit `bff3f16` cannot be config-rolled-back; it requires `git revert bff3f16` if needed (extremely unlikely — the ordering bug was definitively wrong).

## Out of scope for this phase

- Phase 3 watchdog (next phase).
- Phase 4 cycle-gate observability upgrade (skip log INFO promotion + /health probe).
- Re-verification of the 10 prior post-Layer-1 fixes (Phase 5).
