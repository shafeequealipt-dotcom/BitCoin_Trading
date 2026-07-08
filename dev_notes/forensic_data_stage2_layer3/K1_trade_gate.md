# K1 — TradeGate (Layer 3 Placement Gate in OrderService)

Collection timestamp: 2026-05-02 ~11:45 UTC

> **Important naming note.** The spec asks for "TradeGate" as a distinct file. There are TWO gate components in this codebase, both colloquially called "TradeGate". They are unrelated by class hierarchy and run at different points in the flow:
>
> 1. **`src/apex/gate.py`** — class `TradeGate` (apex/gate.py:29). 14-check parameter-shaping gate that NEVER blocks; only adjusts size/leverage/TP/SL. Runs between APEX optimizer and `strategy_worker._execute_claude_trade`. See I4 (apex_gate.md) for that gate's audit.
> 2. **`src/trading/services/order_service.py`** — class `OrderService`, method `_enforce_layer3_gate()` (order_service.py:199-397). The block-or-allow Layer 3 placement gate that emits `ORDER_BLOCKED`. This is the ORDER-side gate the brief is asking about (the brief mentions `ORDER_GATE_NO_LM`, per-symbol cooldowns, ORDER_BLOCKED → all OrderService surfaces).
>
> This document covers component (2). Component (1) is documented in I4.

---

## 1. File path & sizing

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/services/order_service.py`
- Lines of code: **~1380** (file extends past inspection window)
- Class: `OrderService` at order_service.py:91
- Gate-related public/private surfaces:
  - `attach_layer_manager(layer_manager: LayerManager)` — order_service.py:130
  - `_emit_order_blocked(...)` — order_service.py:142 (helper, not a gate proper)
  - `_enforce_layer3_gate(...)` — order_service.py:199 (the gate)
  - `place_order(symbol, side, order_type, qty, price=None, stop_loss=None, take_profit=None, leverage=None, *, purpose='other', layer_snapshot=None, force=False)` — order_service.py:399 (caller of the gate)

OrderService is wired into the service registry under key `"order_service"` (constructed in workers/manager.py during boot; LayerManager attached later via `attach_layer_manager()`).

---

## 2. Gate purpose: distinct from APEX gate?

**Yes — different concerns, different return semantics, different position in the call chain:**

| Aspect | APEX TradeGate (apex/gate.py) | OrderService Layer-3 Gate (_enforce_layer3_gate) |
|---|---|---|
| Class/method | `TradeGate.validate(trade)` | `OrderService._enforce_layer3_gate(...)` |
| Return | Mutated `trade` dict | None on pass; raises on reject |
| On rejection | (NEVER rejects, only mutates) | Raises `Layer3DisabledError` / `Layer3RaceError` / `Layer3BootNotReadyError` |
| Inputs read | `_settings.max_position_size_usd`, `max_leverage`, `position_service`, `fund_manager`, `trade_coordinator` (cooldown), `tias_repo` (conviction), `regime_detector`, `structure_cache`, `market_service` | `_layer_manager` (`is_layer_active(3)`), `layer_snapshot`, `purpose`, `force`, `_init_monotonic`, `_settings.layer_manager.lm_attach_deadline_sec` |
| Side effects | `log.info("GATE_ADJUST | ...")`, `log.info("GATE_TIMING | ...")`, mutates `trade["_gate_validation_ms"]`, `trade["_gate_adjustments"]` | `log.error("ORDER_REJECT_*")`, `log.error("ORDER_BLOCKED ...")`, `log.warning("ORDER_GATE_NO_LM ...")` |
| Sequence | After APEX optimizer, before `_execute_claude_trade` (layer_manager.py:1318-1323) | Inside `place_order()`, BEFORE `ORDER_START` log (order_service.py:498-506) — last gate before exchange RPC |

**Separation of concerns:** apex/gate.py adjusts trade parameters within hard caps but cannot stop a trade. OrderService's Layer-3 gate decides whether the placement may even proceed based on operator-controlled toggles and boot-state invariants. They run in series for entry-side trades.

---

## 3. Layer-active check & ORDER_GATE_NO_LM state

### Layer-manager check site

`_enforce_layer3_gate()` reads the layer state at order_service.py:325:
```
order_service.py:325
live_l3 = bool(lm.is_layer_active(3))
```
`lm` is `self._layer_manager`, the `LayerManager` injected via `attach_layer_manager()` (order_service.py:130-140). The check method itself is `LayerManager.is_layer_active(layer)` at layer_manager.py:1536-1537:
```
def is_layer_active(self, layer: int) -> bool:
    return self._layer_active.get(layer, False)
```

The semantic helper `LayerManager.can_execute_orders()` at layer_manager.py:1552-1558 wraps this for layer 3 (forward-compat with v2 5-layer scheme that would map to layer 4) — but the actual call from `_enforce_layer3_gate` uses the raw `is_layer_active(3)`.

### Snapshot race-check (Approach C)

`_enforce_layer3_gate()` also accepts an optional `LayerSnapshot` (order_service.py:329-363). For `purpose="layer3_entry"` only, if `snapshot.is_layer_active(3) != live_l3`, the placement is rejected with `Layer3RaceError` and emits `ORDER_REJECT_LAYER3_RACE` + `ORDER_BLOCKED reason=layer3_race`.

### Pre-attach (boot window) policy

When `self._layer_manager is None` (LayerManager not yet attached), order_service.py:241-323 implements a **purpose-aware boot policy**:

- **Path 4a — deadline exceeded** (order_service.py:250-278): if `time.monotonic() - self._init_monotonic > settings.layer_manager.lm_attach_deadline_sec`, ALL purposes fail-close → emits `ORDER_GATE_LM_DEADLINE_EXCEEDED` (line 252) AND `ORDER_BLOCKED reason=lm_deadline_exceeded` (line 256-267) AND raises `Layer3BootNotReadyError`.
- **Path 4b — gated purpose during boot window** (order_service.py:282-311): for `purpose in {"layer3_entry","telegram_manual","mcp_tool"}` (i.e. `_GATED_PURPOSES`, order_service.py:58), emits `ORDER_REJECT_LM_BOOT` + `ORDER_BLOCKED reason=lm_boot_not_ready` and raises `Layer3BootNotReadyError`.
- **Path 4c — Layer 4 management during boot window** (order_service.py:313-323): for `purpose in {"layer4_close","layer4_sl"}`, emits a single `ORDER_GATE_NO_LM | … action=allow_layer4_only` warn line and **allows** the placement. This is the only "fail-open" path, scoped strictly to Layer-4 management actions during the boot window.

### ORDER_GATE_NO_LM current state

Source emit (order_service.py:317-322):
```
log.warning(
    f"ORDER_GATE_NO_LM | link_id={order_link_id} sym={symbol} "
    f"purpose={purpose} reason=layer_manager_not_attached_yet "
    f"elapsed_s={elapsed_s:.1f} action=allow_layer4_only "
    f"| {ctx()}"
)
```

**Fail-open vs fail-close**: Layer 4 close/SL purposes are intentionally fail-OPEN during the boot window before deadline expiry; everything else (entry/operator surfaces) and ALL purposes after deadline are fail-CLOSE. This is documented in the docstring at order_service.py:226-235.

24h log scan (`grep ORDER_GATE_NO_LM`): **0 hits** in workers.2026-05-02_04-31-00_392071.log and workers.log. Translation: in the active run, LayerManager attached before any Layer-4 placement was attempted, so the fail-open path was not exercised. The deadline-exceeded path WAS hit four times (see K2) — those were `mcp_tool` purpose entries with `elapsed_s` ≈ 9848-12932 seconds (way past the 60 s deadline), so they were blocked under Path 4a, not Path 4c.

### ORDER_BLOCKED event format

Emit site `_emit_order_blocked()` (order_service.py:192-197):
```
log.error(
    f"ORDER_BLOCKED | link_id={order_link_id} sym={symbol} "
    f"side={side.value} purpose={purpose} reason={reason} "
    f"actor={_actor} "
    f"force={force}{extra_str} | {ctx()}"
)
```
Field semantics:
- `link_id` — Bybit `orderLinkId` (`ti-<24hex>`, generated once per `place_order`).
- `sym`, `side`, `purpose` — same fields the caller passed.
- `reason` — closed-set token: `layer3_off`, `layer3_race`, `lm_boot_not_ready`, `lm_deadline_exceeded`.
- `actor` — derived from reason (order_service.py:186-191): `layer3_auto` (for `layer3_off`/`layer3_race`), `system_auto` (for `lm_*`), `gate` (fallback).
- `force` — flag the caller passed (mostly False for entries).
- `extra` — sorted-key reason-specific fields (e.g. `deadline_s=60.0 elapsed_s=9848.2` for `lm_deadline_exceeded`).

---

## 4. Per-symbol cooldowns

The OrderService Layer-3 gate does NOT itself enforce per-symbol cooldowns. Cooldown enforcement happens at TWO upstream sites:

### 4a. APEX TradeGate (size halving, not blocking)

apex/gate.py:174-186 (Check 6):
```
coordinator = self._services.get("trade_coordinator")
if coordinator and hasattr(coordinator, "is_symbol_cooled_down"):
    if coordinator.is_symbol_cooled_down(symbol):
        size = float(trade.get("size_usd", 600) or 600)
        trade["size_usd"] = round(size * 0.5, 2)
        remaining = 0
        if hasattr(coordinator, "get_symbol_cooldown_remaining"):
            remaining = coordinator.get_symbol_cooldown_remaining(symbol)
        modifications.append(f"size_halved_cooldown_{remaining}s")
```

### 4b. TradeCoordinator (state owner)

trade_coordinator.py:116:
```
self._symbol_cooldowns: dict[str, float] = {}  # symbol -> expiry timestamp
```

trade_coordinator.py:544-552 — set on close:
```
# Set per-symbol cooldown based on close outcome
if was_win:
    cooldown_sec = 180  # 3 min after win
elif closed_by in ("hard_stop", "mode4_crash"):
    cooldown_sec = 900  # 15 min after hard stop / flash crash
else:
    cooldown_sec = 600  # 10 min after normal loss
self._symbol_cooldowns[symbol] = time.time() + cooldown_sec
log.info(f"COORD_CLOSE_END | sym={symbol} cooldown={cooldown_sec}s by={closed_by} ...")
```

**Brief mention "5/10/15 min tiers per memory" verification**: actual implemented tiers are **3 min (win) / 10 min (normal loss) / 15 min (hard stop or mode4 crash)** — NOT 5/10/15. The "5 min" tier from the brief's memory does not exist in current code (trade_coordinator.py:544-551 has only the three branches above).

`is_symbol_cooled_down(symbol)` at trade_coordinator.py:554-562 returns True if `expiry > time.time()`, else False (auto-deletes expired entries). `get_symbol_cooldown_remaining(symbol)` at 564-569 returns int seconds remaining or 0.

### Cooldown enforcement events from logs

`COORD_CLOSE_END | … cooldown=…` (set events) — sample (verbatim):
```
2026-05-02 04:51:39.674 | COORD_CLOSE_END | sym=AXSUSDT cooldown=600s by=mode4_p9 cbs_fired=14
2026-05-02 04:54:07.834 | COORD_CLOSE_END | sym=SANDUSDT cooldown=600s by=shadow_sl_tp cbs_fired=14
2026-05-02 05:06:49.199 | COORD_CLOSE_END | sym=RENDERUSDT cooldown=600s by=strategic_review: CLOSE...
2026-05-02 05:35:05.051 | COORD_CLOSE_END | sym=DOGEUSDT cooldown=600s by=time_decay_p_win_low
2026-05-02 05:35:14.822 | COORD_CLOSE_END | sym=AXSUSDT cooldown=600s by=mode4_p9
2026-05-02 05:58:36.533 | COORD_CLOSE_END | sym=DOGEUSDT cooldown=600s by=strategic_review...
2026-05-02 06:05:17.424 | COORD_CLOSE_END | sym=AXSUSDT cooldown=600s by=mode4_p9
```
All observed COORD_CLOSE_END entries in the 24h window show `cooldown=600s` (10 min normal-loss tier). No 180s (win) or 900s (hard-stop) tiers fired in the observed window.

`GATE_ADJUST | … size_halved_cooldown_…` (size-halving on re-entry attempt) — single sample:
```
2026-05-02 05:41:21.546 | INFO | src.apex.gate:validate:334 | GATE_ADJUST | sym=AXSUSDT changes=[conviction_cap=$246(w=0.5x), size_halved_cooldown_233s] | did=d-1777700319292
```
This shows AXSUSDT in cooldown with 233s remaining when APEX gate ran — the trade was not blocked, just size-halved.

NOT FOUND — per-symbol cooldown enforcement at OrderService layer — searched: order_service.py full file, grep "cooldown|cooled". OrderService does NOT consult `is_symbol_cooled_down()`. Cooldown enforcement is upstream, in apex/gate (size-halving) and trade_coordinator (state).

---

## 5. Concurrent position limits

### Per-coin (duplicate-position halving)

apex/gate.py:162-172 (Check 5):
```
existing = await pos_svc.get_position(symbol)
if existing and existing.size and existing.size > 0:
    size = float(trade.get("size_usd", 600) or 600)
    trade["size_usd"] = round(size * 0.5, 2)
    modifications.append("size_halved_existing_pos")
```
Halves size when there is an open position on the same symbol. Does not block.

### Total positions

apex/gate.py:108-121 (Check 3):
```
max_concurrent = 5
try:
    pos_svc = self._services.get("position_service")
    if pos_svc:
        positions = await pos_svc.get_positions()
        open_count = len(positions) if positions else 0
        if open_count >= max_concurrent:
            size = float(trade.get("size_usd", 600) or 600)
            reduced = round(size * 0.3, 2)
            trade["size_usd"] = reduced
            modifications.append(f"size_reduced_max_pos={open_count}")
```
Reduces size to 30% when ≥5 positions open. Hard-coded `max_concurrent = 5` (apex/gate.py:109). Does not block.

OrderService's `_enforce_layer3_gate` does NOT enforce any position-count limit. There is no `max_concurrent` block at the order service layer.

DailyPnLManager-driven mode also caps `max_positions` per `get_current_mode()` (e.g. NORMAL = 10, CAUTION = 3, SURVIVAL = 2, HALTED = 0). That cap is enforced in `apply_restrictions` at the strategy_worker tier (pnl_manager.py:310-333), not the order service.

PerformanceEnforcer's `get_max_positions_override()` (performance_enforcer.py:110-116) returns `_l1_max_pos=3` / `_l2_max_pos=2`. Consumed by strategy-worker layer for setup count throttling.

---

## 6. Risk gates: max position size, max leverage, max notional

### In `OrderService.place_order` itself (order_service.py:543-585)

```
max_pct = self._settings.risk.max_position_size_pct
max_usd = equity * (max_pct / 100)
if notional_value > max_usd:
    old_qty = qty
    qty = max_usd / notional_price
    qty = round_qty(qty, _instrument.qty_step)
    log.warning("POSITION SIZE CAPPED: ...")
```
- `max_position_size_pct` default **10.0%** of equity (settings.py:519).

```
# Per-trade max loss: 2% of equity
eff_lev = int(leverage) if leverage else 1
if stop_loss and float(stop_loss) > 0 and notional_price > 0:
    sl_dist = abs(notional_price - float(stop_loss))
    potential_loss = sl_dist * float(qty) * eff_lev
    max_loss = equity * 0.02
    if potential_loss > max_loss and sl_dist > 0 and eff_lev > 0:
        old_qty = qty
        qty = max_loss / (sl_dist * eff_lev)
        qty = round_qty(qty, _instrument.qty_step)
        log.warning("PER-TRADE RISK CAPPED: ...")
```
- Per-trade max loss hard-coded **2% of equity** (order_service.py:575).

### Pre-RPC validators (called from `place_order`)

- `_validate_symbol(symbol)` — order_service.py:515 (verifies symbol in SUPPORTED_SYMBOLS).
- `_validate_stop_loss(stop_loss)` — order_service.py:516 (mandatory SL).
- `_validate_leverage(leverage)` — order_service.py:517 (caps against `settings.risk.max_leverage`, default 3, settings.py:515).

### Upstream (APEX gate) caps

apex/gate.py:94-99 (Check 1):
```
max_size = self._settings.max_position_size_usd
current_size = float(trade.get("size_usd", 600) or 600)
if current_size > max_size:
    trade["size_usd"] = max_size
    modifications.append(f"size=${current_size:.0f}->${max_size:.0f}")
```
- `APEXSettings.max_position_size_usd = 1200.0` (settings.py:1399).

apex/gate.py:101-106 (Check 2):
```
max_lev = self._settings.max_leverage
current_lev = int(trade.get("leverage", 3) or 3)
if current_lev > max_lev:
    trade["leverage"] = max_lev
    modifications.append(f"lev={current_lev}->{max_lev}")
```
- `APEXSettings.max_leverage = 5` (settings.py:1400).

### Max notional

NOT FOUND — explicit "max notional" check — searched: order_service.py, apex/gate.py. Notional is bounded transitively via `qty × price ≤ max_position_size_pct × equity` (order_service.py:558-569) but there is no separately-named `max_notional_usd` constant.

---

## 7. Decision routing

### Pass path

After `_enforce_layer3_gate(...)` returns successfully (no exception), `place_order()` continues at order_service.py:509-514:
```
log.info(
    f"ORDER_START | link_id={order_link_id} sym={symbol} "
    f"side={side.value} type={order_type.value} qty={qty} "
    f"lev={leverage} sl={stop_loss} tp={take_profit} "
    f"purpose={purpose} | {ctx()}"
)
```
…then runs validators, qty/price rounding, leverage set, position-size cap, per-trade risk cap, and finally the Bybit `place_order` RPC via `_place_order_with_idempotent_retry()` (order_service.py:613-618). On success, emits `ORDER_OK` (order_service.py:638-642) and saves to the trading repository.

### Block path (5 gate exits)

Each gate-side reject emits two events: a reason-specific event + the unified `ORDER_BLOCKED`, then raises:

| Reject reason | Reason-specific event | Exception | order_service.py line |
|---|---|---|---|
| `lm_deadline_exceeded` | `ORDER_GATE_LM_DEADLINE_EXCEEDED` | `Layer3BootNotReadyError` | 250-278 |
| `lm_boot_not_ready` | `ORDER_REJECT_LM_BOOT` | `Layer3BootNotReadyError` | 282-311 |
| `layer3_race` | `ORDER_REJECT_LAYER3_RACE` | `Layer3RaceError` | 332-363 |
| `layer3_off` | `ORDER_REJECT_LAYER3_OFF` | `Layer3DisabledError` | 369-391 |
| `force=True override` (informational) | `ORDER_LAYER3_OFF_FORCED` | (proceeds — no raise) | 392-397 |

Block consumers (caller handling): the `Layer3*Error` exceptions propagate up to `_execute_claude_trade()` (strategy_worker), the brain cycle, or telegram/MCP handlers. Each caller is responsible for its own dropping/alerting — there is no centralized telegram alert from the gate itself. `ORDER_BLOCKED` is the audit-trail tag operators grep.

### Pre-RPC (post-gate) failure paths

After the gate passes but before RPC, the following raise without emitting `ORDER_BLOCKED` (these are validation failures, not gate blocks):
- `InvalidOrderError` — instrument validation (order_service.py:529-533) or missing price for limit orders (order_service.py:537-540).
- `RiskLimitExceededError` — caught upstream by `_validate_leverage`.

---

## 8. Live state observed (24 h window)

- `ORDER_BLOCKED`: 4 events (all `lm_deadline_exceeded`, see K2).
- `ORDER_GATE_LM_DEADLINE_EXCEEDED`: 4 events (each pairs with the above).
- `ORDER_GATE_NO_LM`, `ORDER_REJECT_LM_BOOT`, `ORDER_REJECT_LAYER3_RACE`, `ORDER_REJECT_LAYER3_OFF`, `ORDER_LAYER3_OFF_FORCED`: 0 events each.
- `GATE_ADJUST` (APEX gate): 10+ events with mods like `conviction_cap=$246(w=0.5x)`, `APEX_GUARDRAIL_TP_FLOOR(...)`, `size_halved_cooldown_233s`, `CONVICTION_SIZE_CAP(claude=$500,req=$800,cap=$750)`.
- `ORDER_ATTEMPT` / `ORDER_OK` / `ORDER_REJECT_*`: 4 total in 24h (all 4 are the rejected mcp_tool placements).
