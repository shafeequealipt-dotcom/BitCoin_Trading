# M4 — Layer Active Gating

Forensic snapshot 2026-05-02 — refreshed from 2026-04-28 baseline.

---

## 1. `data/layer_state.json` current contents

Path: `/home/inshadaliqbal786/trading-intelligence-mcp/data/layer_state.json`

```json
{
  "layer_active": {
    "1": true,
    "2": false,
    "3": false
  },
  "user_stopped": true,
  "timestamp": "2026-05-02T11:26:52.917178+00:00"
}
```

State as of collection: Layer 1 ON, Layer 2 OFF, Layer 3 OFF, `user_stopped=true`. Timestamp matches the operator-driven Telegram stop sequence shown in §3 below.

---

## 2. `layer_active` dict in `LayerManager`

- **Allocation:** `src/core/layer_manager.py:73` — `self._layer_active = {1: False, 2: False, 3: False}`.
- **State file constant:** `src/core/layer_manager.py:28` — `_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "layer_state.json"`.
- **Reads:**
  - `src/core/layer_manager.py:1536-1537` — `def is_layer_active(self, layer: int) -> bool: return self._layer_active.get(layer, False)`.
  - `src/core/layer_manager.py:565, 570, 575, 629, 635, 712, 784, 844` — internal control flow (cascade stop, brain loop, execute trades / position actions).
  - `src/core/layer_manager.py:195` — serialized in `_persist_state` (`{"layer_active": {str(k): v for k, v in self._layer_active.items()}}`).
  - `src/core/layer_manager.py:335` — read in `_sync_state_with_disk` for compare.
- **Writes (in-memory):**
  - `src/core/layer_manager.py:656` — `self._layer_active[1] = True` (`_start_data_layer`).
  - `src/core/layer_manager.py:663` — `self._layer_active[2] = True` (`_start_brain_layer`).
  - `src/core/layer_manager.py:676` — `self._layer_active[3] = True` (`_start_execution_layer`).
  - `src/core/layer_manager.py:683` — `self._layer_active[1] = False` (stop_data_layer).
  - `src/core/layer_manager.py:687` — `self._layer_active[2] = False` (stop_brain_layer).
  - `:716` etc. — stop_execution_layer.
  - `src/core/layer_manager.py:369` — `self._layer_active[layer_id] = disk_active[layer_id]` (legacy `reload_memory` drift recovery branch only).
- **Persistence to disk:** `src/core/layer_manager.py:177-211` — `_persist_state()` writes `_STATE_FILE` and emits `LAYER_STATE_PERSIST_OK` / `LAYER_STATE_PERSIST_FAIL`.
- **Restore on boot:** `src/core/layer_manager.py:213-222` — `_load_persisted_state()` restores ONLY `user_stopped`; layers always start inactive (`# Layers start inactive regardless`).
- **`LayerSnapshot` dataclass:** `src/core/layer_manager.py:33-54` — frozen point-in-time view of `layer_active` (used by Layer 3 race-check, see §4).

---

## 3. `LAYER_STATE_SYNC` heartbeat (Phase 2 of post-Layer-1 fixes)

### Current implementation

- **Start:** `src/core/layer_manager.py:231-279` — `start_state_sync(interval_sec: float = 60.0, *, on_drift_action: str = "rewrite_disk")`. Validated values: `"rewrite_disk"` (default) or `"reload_memory"` (legacy emergency rollback). Defaults captured at `:144` — `self._drift_action: str = "rewrite_disk"`.
- **Loop:** `src/core/layer_manager.py:281-300` — `_state_sync_loop(interval_sec)` — sleeps first, then ticks, swallows transient errors (logs `LAYER_STATE_SYNC_LOOP_ERROR`).
- **One iteration:** `src/core/layer_manager.py:302-377` — `_sync_state_with_disk()`:
  - If `_STATE_FILE` missing → emit `LAYER_STATE_SYNC | match=na disk=missing memory=...` at DEBUG and return (`:323-329`).
  - Read disk JSON, coerce keys to int (`:330-334`).
  - Compute `match = disk_active == memory_active` (`:336`).
  - Emit `LAYER_STATE_SYNC | match=true|false disk=... memory=...` at INFO **every tick** (`:338-341`).
  - On match: return.
  - On drift + `_drift_action == "rewrite_disk"`: emit `LAYER_STATE_DRIFT_RECOVERED | direction=memory_to_disk disk=... memory=... reason=disk_was_stale` at WARNING and call `_persist_state()` (`:346-359`).
  - On drift + `_drift_action == "reload_memory"`: emit `LAYER_STATE_DRIFT | disk=... memory=... action=reload_from_disk` at WARNING and overwrite memory from disk for known keys (`:361-369`).

### Drift recovery direction

- **Default direction (post-Phase-11 fix): MEMORY → DISK** (`:144` — `self._drift_action: str = "rewrite_disk"`).
- The old direction (DISK → MEMORY) is preserved behind the `"reload_memory"` value for emergency rollback only. Comment block at `:130-145` and `:301-317` documents that the previous default produced the Layer 3 toggle revert regression.
- Verified live in logs: every `LAYER_STATE_SYNC` emission shows `match=true` (heartbeat is healthy with no drift):

```text
2026-05-02 11:35:44.435 | INFO | src.core.layer_manager:_sync_state_with_disk:338 | LAYER_STATE_SYNC | match=true disk={1: True, 2: False, 3: False} memory={1: True, 2: False, 3: False} | no_ctx
2026-05-02 11:36:44.436 ... match=true ...
2026-05-02 11:37:44.438 ... match=true ...
2026-05-02 11:38:44.439 ... match=true ...
2026-05-02 11:39:44.441 ... match=true ...
```

(60-second cadence as configured.)

### Toggle audit trail (live evidence from `data/logs/workers.log`)

```text
2026-05-02 11:22:44.923 | WARNING | LAYER_TOGGLE | layer=1 from=False to=True reason=unspecified actor=system
2026-05-02 11:22:46.951 | INFO    | LAYER_STATE_PERSIST_OK | layer_active={1: True, 2: True, 3: False} user_stopped=False
2026-05-02 11:22:46.952 | WARNING | LAYER_TOGGLE | layer=2 from=False to=True reason=unspecified actor=system
2026-05-02 11:22:48.954 | INFO    | LAYER_STATE_PERSIST_OK | layer_active={1: True, 2: True, 3: True} user_stopped=False
2026-05-02 11:22:48.954 | WARNING | LAYER_TOGGLE | layer=3 from=False to=True reason=unspecified actor=system
2026-05-02 11:26:52.917 | INFO    | LAYER_STATE_PERSIST_OK | layer_active={1: True, 2: False, 3: False} user_stopped=True
2026-05-02 11:26:52.917 | WARNING | LAYER_TOGGLE | layer=3 from=True to=False reason=telegram_dash_stop_trading actor=telegram_user:<REDACTED_CHAT_ID> cascade_root=2
2026-05-02 11:26:52.918 | WARNING | LAYER_TOGGLE | layer=2 from=True to=False reason=telegram_dash_stop_trading actor=telegram_user:<REDACTED_CHAT_ID> cascade_root=2
```

`stop_layer(2)` cascaded to layer 3 first (per `src/core/layer_manager.py:565-578` — cascade stops higher layers first then lower).

---

## 4. Gate enforcement points

### Layer 2 — Brain CALL_A enforcement

- Brain cycle loop: `src/core/layer_manager.py:712` — `while self._layer_active[2]:` (in `_brain_review_loop`). When Layer 2 turns off, the next loop iteration exits.
- Cycle entry: `_run_brain_cycle()` at `:726`. Dispatches Call A (`:744-756`) or Call B based on `self._call_type`.
- **There is NO explicit `is_layer_active(2)` check inside Call A's strategist code.** The brain loop is gated by `self._layer_active[2]` at the loop boundary; once the loop exits, Call A doesn't fire. Verified by grep: `grep is_layer_active(2)` yields zero hits in `src/brain/`.
- Inside Call A, after the plan is built, **Layer 3** is checked before execution (`:784` — `if self._layer_active[3]:`); plans built without L3 produce `BRAIN_TRADES_DROPPED | layer=3_inactive ...` at `:830-833`.

### Layer 3 — OrderService enforcement (`src/trading/services/order_service.py`)

- **Hard gate function:** `_assert_layer3_allows(...)` at lines 198-397 (called from `place_order` before `ORDER_START`).
- **Live LM check:** `:325` — `live_l3 = bool(lm.is_layer_active(3))`.
- **Race check (snapshot vs live):** `:329-363` — when `purpose == "layer3_entry"` AND a `layer_snapshot` was supplied, comparing `snap_l3 = bool(layer_snapshot.is_layer_active(3))` against `live_l3` and raising `Layer3RaceError` (with `ORDER_REJECT_LAYER3_RACE` log) on mismatch.
- **Hard gate when L3 OFF:** `:366-391` — emits `ORDER_REJECT_LAYER3_OFF | link_id=... reason="Layer 3 disabled"` and raises `Layer3DisabledError`. `purpose == "layer3_entry"` is unconditionally gated; `force=True` does not bypass for that purpose.
- **`force=True` operator override path:** `:392-397` — emits `ORDER_LAYER3_OFF_FORCED ... reason=operator_override` at WARNING for telegram_manual / mcp_tool when L3 is off.
- **Boot-window policy:** `:241-323` — when LM is not yet attached:
  - Past `lm_attach_deadline_sec` → all rejected (`ORDER_GATE_LM_DEADLINE_EXCEEDED`).
  - Within deadline + gated purpose (`_GATED_PURPOSES`) → rejected (`ORDER_REJECT_LM_BOOT`).
  - Within deadline + Layer 4 management purpose → allowed (single `ORDER_GATE_NO_LM | reason=layer_manager_not_attached_yet | action=allow_layer4_only` at WARNING).
- **Snapshot wiring upstream:**
  - `src/core/layer_manager.py:1661-1679` — `snapshot_layer_state()` returns frozen `LayerSnapshot` (`MappingProxyType`).
  - `src/workers/strategy_worker.py:1149-1150` — `_lm.snapshot_layer_state() if _lm and hasattr(_lm, "snapshot_layer_state") else None`.
  - `src/workers/strategy_worker.py:1530` — passed via `layer_snapshot=_layer_snapshot` to `place_order`.
  - `src/trading/services/order_service.py:504` — `layer_snapshot=layer_snapshot` arg in `_assert_layer3_allows` call.

### StrategyWorker — Layer 3 gate on `_strategy_hints` write

- `src/workers/strategy_worker.py:798` — `if not layer_manager or not layer_manager.is_layer_active(3): ...` (early skip).
- Comment at `:821` — `# the is_layer_active(3) gate so ScannerWorker sees consensus even` (consensus stays gated; hints stay gated at `:825`).

### Layer 4 — ProfitSniper / PositionWatchdog

- **Status:** ProfitSniper does NOT consult `is_layer_active(4)` (no Layer 4 in the live state file — only layers 1, 2, 3 exist).
- Verified by grep: `grep is_layer_active /home/inshadaliqbal786/trading-intelligence-mcp/src/workers/profit_sniper.py /home/inshadaliqbal786/trading-intelligence-mcp/src/workers/position_watchdog.py` — zero matches.
- Per `src/core/types.py:101` and `src/core/layer_manager.py:1561` ("Layer 8 forward-compat: can ProfitSniper/Watchdog intervene?"), Layer 4 is a forward-compat naming convention. The active gate IS Layer 3: when L3 toggles off, OrderService rejects ProfitSniper / Watchdog placements unless `purpose` is in the Layer-4 management whitelist.
- **OrderService Layer-4 path:** see `_GATED_PURPOSES` excluded in §4 above — `layer4_close` and `layer4_sl` purposes are allowed during the boot window; once LM is attached and L3 is OFF, those purposes route through the `force` / `purpose` checks at `:369` (only `layer3_entry` is unconditionally rejected; close/SL paths can still fire when permissioned).

### Cold-start gate (additional, Phase 4 of Layer 1 restructure)

- `src/core/layer_manager.py:146-173` — `self._cold_start_resume_done: bool = True` (default fail-open).
- Worker-side check pattern (`getattr(..., default=True)`) referenced in comment at `:170`. Workers emit `LAYER1{B,C,D}_TICK_SKIP | reason=cold_start_boundary_pending` while the flag is `False`.
- `CYCLE_RESUME` log at `:545` is emitted exactly when `_cold_start_resume_done` flips back to True.

---

## 5. Telegram `/layer` commands — handler file:line, supported commands, permission gating

- **There is NO direct `/layer` command.** Layer toggling is exposed through the dashboard inline-button callbacks in `src/telegram/handlers/control_handler.py`.
- **Callback handler:** `src/telegram/handlers/control_handler.py:230-373` — `control_callback`. Registered in `src/telegram/handlers/dashboard_handler.py:2336-2339` with `pattern="^(layer_|emergency_|view_|brain_interval_|capital_|mode_)"`.
- **Supported callbacks** (from `control_handler.py` docstring lines 8-14 + dispatcher):
  - `layer_start_1` / `layer_start_2` / `layer_start_3` → `control_handler.py:248-267` — calls `layer_manager.start_layer(layer, reason="telegram_control_start", actor=f"telegram_user:{user_id}")`.
  - `layer_stop_1` / `layer_stop_2` / `layer_stop_3` → `:269-297` — calls `layer_manager.stop_layer(layer, reason="telegram_control_stop", actor=f"telegram_user:{user_id}")`. Cascade preview at `:274-280`.
  - `emergency_close` → `:299-326` — calls `layer_manager.emergency_close_all(reason="telegram_control_emergency", actor=f"telegram_user:{user_id}")`.
  - `view_plan` → `:329-330` (helper at `:378-395`).
  - `view_positions` → `:333-336` (helper at `:400-430`).
  - `brain_interval_60` / `brain_interval_180` / `brain_interval_300` → `:338-352` — sets `layer_manager.brain_interval_seconds`.
  - `capital_*` → `_handle_capital_callback` (`:539-562`).
  - `mode_*` → `_handle_mode_callback` (`:602-642`).
- **Slash commands that bring up the dashboard with these buttons:**
  - `/control`, `/dashboard` → `dashboard_handler.py:2321-2322` — `CommandHandler("control", control_command)` / `CommandHandler("dashboard", control_command)`.
  - `/stopdash` → `:2323`, `/positions` → `:2324`, `/performance` → `:2325`, `/plan` → `:2326`, `/workers` → `:2327`, `/capital` → `:2334`, `/mode` → `:2335`.
- **Top-level `/emergency` command:** `src/telegram/bot.py:138` — `app.add_handler(CommandHandler("emergency", self.emergency_handler.execute))`. This is the slash-command alternative to the inline button.

### Permission gating

- **Auth class:** `src/telegram/auth.py:9-33` — `TelegramAuth` reads `settings.alerts.chat_id` (single chat ID) at construction (`:18-23`).
- **Check:** `:25-29` — `is_authorized(chat_id)`: `if not self.authorized_chat_ids: return True  # No restriction if no IDs configured`. Otherwise `chat_id in self.authorized_chat_ids`.
- **Where invoked (selected):**
  - `src/telegram/bot.py:293` — `if not self.auth.is_authorized(chat_id): ... await update.message.reply_text("Unauthorized.")` for `/start`.
  - `src/telegram/bot.py:315, 352, 384, 440, 476, 645` — same pattern for other commands and the inline callback handler.
- **Important gap:** the `control_callback` in `src/telegram/handlers/control_handler.py:230-373` does NOT call `is_authorized` itself. It relies on the upstream Telegram bot wrapper having filtered, OR — if `authorized_chat_ids` is empty — it permits all. Verified by grep: `grep is_authorized src/telegram/handlers/control_handler.py` returns zero matches.
- **Effective permission model:** single `chat_id` from `settings.alerts.chat_id`. If unset, all chats can toggle layers. The actor in the audit trail is `telegram_user:{from_user.id}` (passed through `start_layer`/`stop_layer`/`emergency_close_all` args). The 2026-05-02 11:26:52 stop event in §3 above was attributed to `telegram_user:<REDACTED_CHAT_ID>`.

---

## 6. Gaps

- No top-level `/layer` slash command; toggling is button-based via `/control` or `/dashboard`. An operator running scripts via the slash interface alone cannot toggle layers without going through the inline keyboard.
- `control_callback` does not re-verify `is_authorized` — it inherits the upstream filter. If a future telegram API change routes callbacks past the auth check, the callback would accept any chat.
- Layer 4 is a naming convention only; no `_layer_active[4]` exists. References to "Layer 4" in OrderService comments (`_GATED_PURPOSES`, `layer4_close`, `layer4_sl`) describe purpose tags, not a fourth toggleable layer.
