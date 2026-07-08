# Issue 2 — Layer 3 boot-window fail-open gate

**Status:** PARTIAL — hard gate works; boot-window allows all purposes through.
**Tier:** 1 (safety-critical, real-money pre-condition).
**Source:** `IMPLEMENT_POST_LAYER1_FIXES_PROFESSIONAL.md` Phase 2; `src/trading/services/order_service.py:163-175`.

## A. Mechanism

`OrderService._enforce_layer3_gate` (155-228) is the canonical Layer 3 gate. It distinguishes by `purpose`:

- `_GATED_PURPOSES = {layer3_entry, telegram_manual, mcp_tool}` (gated when L3 OFF; layer3_entry unconditional, others honour `force=True`).
- Layer 4 purposes (`layer4_close`, `layer4_sl`) intentionally bypass — managing existing positions must not be blocked by L3.

When `self._layer_manager is None` (boot before LM attaches) the gate logs a single warning and returns:

```python
# order_service.py:168-175
if lm is None:
    log.warning(
        f"ORDER_GATE_NO_LM | link_id={order_link_id} sym={symbol} "
        f"purpose={purpose} reason=layer_manager_not_attached_yet "
        f"action=allow | {ctx()}"
    )
    return
```

The docstring (163-166) states this is intentional: "failing open is preferable to falsely rejecting every order during startup". Rationale: Layer 4 close/SL orders triggered by the position watchdog during the boot window must execute — otherwise startup could orphan a stop-loss.

The gap: the fail-open is undifferentiated. Layer 3 entries and operator surfaces (`telegram_manual`, `mcp_tool`) are also allowed pre-attach, with no race detection (no `layer_snapshot` is meaningful pre-attach). There is no legitimate code path that needs to enter a new position before LM exists.

The 06:27:14-16 ETHUSDT/BTCUSDT incidents in the observation report were `purpose=mcp_tool` orders that failed at the exchange with ErrCode 110007 (`layer1_live_monitor_2026-04-27.md:217-218`) — those are Issue 5 (balance drift), not gate leaks. We have not directly observed a fail-open gate leak, but the purpose-undifferentiated boot policy is a latent hole; a position watchdog or strategist firing pre-attach would slip an entry.

## B. Dependencies

- **Caller surfaces:**
  - `src/core/layer_manager.py` `_execute_new_trades` (brain auto, `purpose='layer3_entry'`)
  - `src/mcp/tools/trading_tools.py` (`purpose='mcp_tool'`)
  - `src/telegram/handlers/*` (`purpose='telegram_manual'`)
  - Position watchdog and Layer 4 stop loss adjuster (`purpose='layer4_*'`) — must continue to bypass.
- **State persistence:** `data/layer_state.json` is the operator's source of truth (mutated by Telegram toggles); `LayerManager.layer_active` is the in-memory mirror.
- **Race detection:** `Layer3RaceError` at `order_service.py:185-202` checks `layer_snapshot.is_layer_active(3)` against live LM view; only relevant once LM is attached.
- **Boot timing:** `WorkerManager` instantiates OrderService before LayerManager in `manager.py` startup sequence — `attach_layer_manager(lm)` is called shortly after, but the window is observable in workers.log.

## C. Constraints

- Layer 4 management orders MUST be allowed during the boot window (see Hard Rule 4 of original Layer 1 plan; also `phase0_issue_3_layer3_investigation.md`).
- `layer_state.json` is operator-mutable via Telegram — so disk is the source of truth for steady-state. Memory must reload on disk drift.
- Cannot break the existing test suite (`tests/test_order_service_layer3_gate.py` etc.) — must preserve `Layer3DisabledError` / `Layer3RaceError` semantics.
- The MCP tool path already routes through `OrderService.place_order` with `purpose='mcp_tool'` — verified by reading `src/mcp/tools/trading_tools.py:170-176`. No bypass.

## D. Fix candidates

1. **Purpose-aware boot policy (chosen).** During the `lm is None` window, allow only Layer 4 purposes; reject `_GATED_PURPOSES` with new `Layer3BootNotReadyError`. Adds a defensive boot deadline (`lm_attach_deadline_sec` config, default 60s) — past the deadline, fail-close all purposes (something is broken with attachment).
2. Fail-close everything during boot (per prompt's nominal "Fix 2.1 strict" option). Rejected — would orphan Layer 4 close/SL orders triggered during the brief boot window.
3. Defer all orders during boot via a queue. Rejected — adds a queue subsystem, complicates failure modes, no live evidence we need queueing.
4. No change. Rejected — leaves a latent entry-side hole.

In addition to (1), three additive observability features regardless:

- **Disk/memory sync heartbeat.** Read `data/layer_state.json` every 60s, compare to `layer_active`, emit `LAYER_STATE_SYNC | match=t|f`. On mismatch, reload from disk and emit `LAYER_STATE_DRIFT`.
- **LAYER_TOGGLE audit.** Single helper that wraps every mutation of `layer_active[N]`, emits `LAYER_TOGGLE | layer={n} from={old} to={new} reason={r} actor={who} disk_synced={t|f}`.
- **Uniform `ORDER_BLOCKED` log.** When the gate rejects (any reason), emit a single normalized line in addition to the existing reason-specific events.

## E. Observability gap

Today:
- `ORDER_GATE_NO_LM` is the only boot-window event, level WARNING — and it implies "allow" without distinguishing purpose tiers.
- No periodic `layer_state.json` ↔ memory sync log; an out-of-band edit drifts undetected.
- No structured `LAYER_TOGGLE` audit; toggle events scatter across Telegram handler logs and `LayerManager.set_layer` direct calls without a single greppable tag.
- Existing reason-specific events (`ORDER_REJECT_LAYER3_OFF`, `ORDER_REJECT_LAYER3_RACE`) are good but not unified — operators have to know all three tags.

## F. Verification approach

- Unit test (boot gate): construct OrderService with `layer_manager=None`, attempt `place_order(purpose='layer3_entry')` → `Layer3BootNotReadyError`. Same with `layer4_close` → succeeds. Same with `layer3_entry` after `attach_layer_manager` and `layer_active[3]=False` → `Layer3DisabledError` (existing).
- Unit test (deadline): construct OrderService with init time 70s ago, `layer_manager=None`, any purpose → `Layer3BootNotReadyError` with reason=`lm_deadline_exceeded`.
- Unit test (state sync): synth disk/memory mismatch, run heartbeat, assert reload + `LAYER_STATE_DRIFT` event.
- Live trial (operator): boot system, observe ≤30s `ORDER_GATE_NO_LM` window with Layer 4 only; toggle Layer 3 from Telegram, observe `LAYER_TOGGLE` line; manual edit `layer_state.json`, observe `LAYER_STATE_DRIFT` within 60s.

## G. Rollback path

Three atomic commits make rollback granular:
- Revert boot-policy commit → restores undifferentiated fail-open. No state migration.
- Revert state-sync commit → stops heartbeat. No persistence change (heartbeat is read-only against `layer_state.json`).
- Revert ORDER_BLOCKED+TOGGLE commit → removes new log tags; existing reason-specific events unchanged.

No DB migration in any of these. Tag immediately before Phase 2 commit lands so revert is one operation: `git tag pre-phase2-post-layer1-fixes <sha>`.
