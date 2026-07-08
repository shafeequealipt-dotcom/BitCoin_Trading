# Issue 1 Phase 1 — Combined Anatomy (APEX + TradeGate + strategy_worker + order path + thesis)

Each subsystem read end-to-end. Direction-modifying paths catalogued.

## 1. APEX — `src/apex/optimizer.py` (975 lines) and `src/apex/gate.py` (544 lines)

### Direction flow inside APEX

```python
# optimizer.py — optimize() main flow
claude_direction = directive.get("direction", "Buy")               # 216
regime = package.situation_data.regime                              # 217
direction_locked, lock_reason = self._check_direction_lock(...)     # 218
if direction_locked:
    log APEX_DIR_LOCK                                               # 222
    inject "[DIRECTION LOCKED: ...]" into DeepSeek prompt           # 227-231

result = await self._client.optimize(...)                           # 287
optimized = self._parse_response(result, directive)                 # 313

# Suspenders: if DeepSeek flipped despite the lock instruction, revert
if direction_locked and optimized.direction != claude_direction:    # 318
    log APEX_DIR_LOCK_OVERRIDE                                      # 319
    optimized.direction = claude_direction                          # 324
    optimized.was_flipped = False                                   # 325

# Phase 9 — confidence-gated flip discipline (ranging/dead/unknown only)
_flip_revert, _flip_reason = self._enforce_flip_confidence(...)     # 377
if _flip_revert:
    log APEX_FLIP_BLOCKED                                           # 382
    optimized.direction = claude_direction                          # 392

# Flip-resize policy (if flip authorized + apex_block_flip_resize=True)
elif optimized.was_flipped:
    self._apply_flip_resize_policy(...)                             # 408ish

# Final emit
_log_optimization(opt, directive, regime, vol_class)                # 730
  → if opt.was_flipped: log APEX_FLIP (WARNING)                     # 745
  → else: log APEX_OK (INFO)                                        # 766
```

### Direction-modification methods inside APEX

| Method | File:Line | Mutates direction? | Logged? |
|--------|-----------|--------------------|---------|
| `_check_direction_lock` | optimizer.py:828 | No (returns bool) | n/a |
| `_check_flip_evidence` | optimizer.py:813 | No (returns bool) | n/a |
| `_enforce_flip_confidence` | optimizer.py:876 | Caller mutates back to `claude_direction` if reverted | `APEX_FLIP_BLOCKED` (WARNING) |
| `_apply_flip_resize_policy` | optimizer.py:922 | No (mutates size only) | `APEX_FLIP_RESIZE_ACCEPTED` (INFO) / `APEX_FLIP_RESIZE_CAPPED` (WARNING) |
| Inline override at optimize() | optimizer.py:324 | Reverts to `claude_direction` when DIR_LOCK | `APEX_DIR_LOCK_OVERRIDE` (WARNING) |
| Inline revert at optimize() | optimizer.py:392 | Reverts to `claude_direction` when conf below threshold | `APEX_FLIP_BLOCKED` (WARNING) |
| `_log_optimization` final | optimizer.py:730-790 | No | `APEX_FLIP` (WARNING) / `APEX_OK` (INFO) |

**Contract observed:** APEX makes direction immutable from outside (claude_direction always wins on conflict) AND emits a flip log whenever direction differs from claude_direction. **Every direction-modification path inside APEX is logged.**

### TradeGate — `gate.py:48` `validate()`, 14 checks

| Check | Line | Touches direction? | Notes |
|-------|------|--------------------|-------|
| Check 0 — Claude size cap | 79 | No | Caps size at `1.5×` claude original |
| Check 1 — Max position size | 100 | No | |
| Check 2 — Max leverage | 107 | No | |
| Check 3 — Max concurrent | 114 | No | Reduces size if 5+ open |
| Check 4 — Capital (conviction-weighted) | 132 | No | Reads direction indirectly via signal_score |
| Check 5 — Duplicate position | 220 | No | Halves size |
| Check 6 — Cooldown reduce | 233 | No | Halves size |
| Check 7 — Min floor | 247 | No | `$50` floor |
| Check 8 — TP floor vs claude | 281 | Reads direction (Buy vs Sell branch) | Mutates TP only |
| Check 9 — Trail activation | 306 | No | |
| Check 10 — Trail distance floor | 324 | No | |
| Check 11 — Mode override | 337 | No | |
| Check 12 — Confidence size scale | 345 | No | |
| Check 13 — R:R ratio | 362 | No | |
| Check 14 — TP/SL sanity | 381 | Reads direction (line 386) | Mutates TP only when identical |

**Conclusion:** `TradeGate.validate` never writes to `trade["direction"]`. Reads only for branching. **Not a flip site.**

## 2. strategy_worker._execute_claude_trade — `src/workers/strategy_worker.py:1417-2434`

### The function's direction-handling timeline

```python
direction = trade.get("direction", "")                              # 1436
# Sanity reject if missing                                          # 1437-1442
# Layer snapshot for L3 enforcement                                 # 1454-1459
# Enforcer leverage clamp (does NOT touch direction)                # 1467-1495
# X-RAY SURVIVAL quality gate                                       # 1501-1550
# X-RAY structural gates (SKIP+RR, conflict, mismatch)              # 1553-1602
# === XRAY DIR FLIP BLOCK (1604-1748) — THE FLIP HAPPENS HERE ===
# Per-symbol enforcer / X-RAY / price / qty / SL/TP validation
# trade["direction"] is now whatever XRAY decided
# TradeGate.validate (does NOT touch direction)
# place_order (passes direction through as `side`)
```

### XRAY flip block (strategy_worker.py:1604-1748) line-by-line

```python
# Compute ratio = rr_opposite / rr_chosen
_ratio = 0.0
if direction == "Buy" and _sp.rr_long > 0:
    _ratio = _sp.rr_short / _sp.rr_long
elif direction == "Sell" and _sp.rr_short > 0:
    _ratio = _sp.rr_long / _sp.rr_short

_flip_threshold = float(getattr(self.settings.risk, "xray_dir_flip_threshold_ratio", 3.0))

if _ratio > _flip_threshold:
    _flipped_dir = "Sell" if direction == "Buy" else "Buy"
    _has_dual_levels = (
        _sp.long_sl_price > 0 and _sp.long_tp_price > 0
        and _sp.short_sl_price > 0 and _sp.short_tp_price > 0
    )
    if not _has_dual_levels:
        log XRAY_DIR_BLOCK (no flip; TRADE_SKIP)                    # 1644-1661

    # Post-flip structural conflict guard
    if _ms.structure in ("uptrend", "downtrend"):
        _new_conflict = ...
        if _new_conflict and quality in ("SKIP", "C"):
            log XRAY_DIR_FLIP_BLOCKED (TRADE_SKIP)                  # 1676-1691

    # APPLY THE FLIP
    trade["direction"] = _flipped_dir                               # 1718
    trade["stop_loss_price"] = _new_sl                              # 1719
    trade["take_profit_price"] = _new_tp                            # 1722
    trade["_apex_was_flipped"] = True                               # 1725
    trade["_flip_source"] = "xray"                                  # 1726
    trade["_xray_flip_ratio"] = round(_ratio, 2)                    # 1727
    trade["_xray_flip_rr_long"] = ...                               # 1734
    trade["_xray_flip_rr_short"] = ...                              # 1735
    direction = _flipped_dir                                        # 1736

    log XRAY_DIR_FLIP (WARNING)                                     # 1738
```

### Crucial observations

1. The XRAY flip block has **no check for `_apex_was_flipped` already set** — it can override an APEX legitimate flip too (though the threshold gates this in practice).
2. The XRAY flip block has **no check for APEX_DIR_LOCK status**. The `_apex_locked` flag (if it existed) would not be inspected. Today's SEIUSDT and ONDOUSDT cases prove this.
3. `trade["_apex_was_flipped"] = True` reuses the APEX field with `_flip_source = "xray"` to distinguish. Downstream consumers (thesis save, alerts) accept both equally.
4. The flip mutates SL/TP from the structural placement for the new direction. If `_sp.short_sl_price > 0` and `_sp.short_tp_price > 0` are populated, the new SL/TP are structurally valid. Order arrives at Bybit with consistent direction/SL/TP.
5. **The log line uses `XRAY_DIR_FLIP`, not anything starting with `APEX_`.** Operators or scripts searching for `APEX_FLIP*` miss this entirely.

## 3. Order path — Transformer → adapter

### `Transformer._OrderProxy.place_order` — `src/core/transformer.py:1270-1320`

- Accepts `*args, **kwargs`, no direction modification.
- Blocks `place_order` during `is_switching` (returns REJECTED Order).
- For `bybit_demo` mode (line 1284), runs L3 gate (`check_layer3_for_bybit_demo`); on block, returns REJECTED `Order(side=side_from_args)`.
- Otherwise calls `self._t.active_order_service.place_order(*args, **kwargs)` — pass-through.

### `BybitDemoOrderService.place_order` — `src/bybit_demo/bybit_demo_adapter.py:829-1056`

- Signature: `(symbol, side: Side, order_type, qty, price, stop_loss, take_profit, leverage, *, purpose, layer_snapshot, force)`.
- Line 859: `side_str = side.value if isinstance(side, Side) else str(side)` — extracts string, never modifies.
- Line 874: `BYBIT_DEMO_ORDER_RECEIVED | sym=... side={side_str} qty=...` — first observable log on the adapter side.
- Lines 915-924: builds JSON body with `"side": side_str`.
- Line 934: `BYBIT_DEMO_ORD_SEND | sym=... side={side_str} qty=... lev=... sl=... tp=...` — final log before HTTP POST to `/v5/order/create`.

**Conclusion:** Side flows from caller's `side: Side` arg through `side_str` into the JSON body unchanged. **Not a flip site.**

## 4. THESIS_FLIP_PERSISTED — `src/core/thesis_manager.py:101`

### When and what it logs

```python
# save_thesis() — inserts row into trade_thesis (v28 columns include xray_flip_*)
# After INSERT succeeds:
log THESIS_OPEN                                                     # 95
if xray_flip_source:                                                # 99
    log THESIS_FLIP_PERSISTED                                       # 101-106
```

Fires only when `xray_flip_source` was carried into the save (i.e., XRAY flip happened). Persists the flip's metadata (`source`, `ratio`, `rr_long`, `rr_short`) into the database for CALL_B prompt enrichment downstream.

This is **observability of persistence**, not a separate flip site. The flip already happened upstream at `strategy_worker.py:1718`.

## 5. Brain Call-B's view of the flipped trade

Brain Call B reads `trade_thesis` rows via `get_open_theses` (`thesis_manager.py:116-164`). The thesis row has:
- `direction` — the post-flip direction
- `apex_flipped` — boolean (covers both APEX and XRAY flips)
- `apex_original_direction` — pre-flip direction
- `xray_flip_source` — "xray" or empty
- `xray_flip_ratio`, `xray_flip_rr_long`, `xray_flip_rr_short`

Brain therefore sees the post-flip direction with full provenance. The flip is not invisible to brain after the fact; it is only invisible during audit-log grep filtered to `APEX_FLIP*`.

## Summary — where does the silent flip happen?

**It does not happen silently.** Every direction-modification site in the call chain emits a log event:

| Site | Logged event | File:Line | Level |
|------|--------------|-----------|-------|
| APEX DIR_LOCK rejection of DeepSeek flip | `APEX_DIR_LOCK_OVERRIDE` | optimizer.py:319 | WARNING |
| APEX confidence-gated flip block | `APEX_FLIP_BLOCKED` | optimizer.py:382 | WARNING |
| APEX flip-resize cap | `APEX_FLIP_RESIZE_CAPPED` | optimizer.py:957 | WARNING |
| APEX flip-resize accept | `APEX_FLIP_RESIZE_ACCEPTED` | optimizer.py:967 | INFO |
| APEX legitimate flip | `APEX_FLIP` | optimizer.py:745 | WARNING |
| APEX no-flip | `APEX_OK` | optimizer.py:766 | INFO |
| **XRAY direction flip** | **`XRAY_DIR_FLIP`** | **strategy_worker.py:1738** | **WARNING** |
| XRAY flip blocked (post-flip conflict) | `XRAY_DIR_FLIP_BLOCKED` | strategy_worker.py:1676 | WARNING |
| XRAY flip blocked (missing dual levels) | `XRAY_DIR_BLOCK` | strategy_worker.py:1644 | WARNING |
| TradeGate | `GATE_ADJUST` (size only) / `GATE_PASS` | gate.py:404 / 408 | INFO / DEBUG |
| Adapter receive | `BYBIT_DEMO_ORDER_RECEIVED` | bybit_demo_adapter.py:874 | INFO |
| Adapter send | `BYBIT_DEMO_ORD_SEND` | bybit_demo_adapter.py:934 | INFO |
| Thesis persistence | `THESIS_FLIP_PERSISTED` | thesis_manager.py:101 | INFO |

The audit's framing — "silent path emits no flip log at all" — is incorrect at the code level. What is real:

1. **Naming inconsistency.** `XRAY_DIR_FLIP` does not begin with `APEX_FLIP*`. Audit scripts filtered to `APEX_FLIP*` (or operators eyeballing `APEX_FLIP`) miss the XRAY path entirely.
2. **Contract violation.** APEX_DIR_LOCK is honored *inside* APEX (lines 318-324 hard-revert) but the lock state does not propagate into `strategy_worker._execute_claude_trade`, so the XRAY flip block (1604-1748) can flip a locked direction with no awareness.
3. **No CONTRACT_VIOLATION emission.** When XRAY overrides an APEX_DIR_LOCK, no dedicated log records the override of an upstream guarantee.
