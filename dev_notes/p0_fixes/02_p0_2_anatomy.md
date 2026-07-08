# P0-2 Anatomy — Direction-Decision Seam (Dependency Map)

Date: 2026-05-22. Source files read in full before this map was produced.

## H1 — Scope

Map the complete direction-decision seam for a single trade from brain emit → final order direction write. Required by Rule 3 before any P0-2 file is edited.

## H1 — The Three Authorities

### H2 — Brain (Layer 2)

The strategist (`src/brain/strategist.py:875–1077` for `create_trade_plan`) returns a `StrategicPlan` with `new_trades: list[dict]`. Each trade dict carries `direction` ∈ {"Buy", "Sell"} and the brain's intended SL/TP prices in price-space.

The brain's directive is the intended trade per the spec's project aim.

### H2 — APEX (Layer 3)

`src/apex/optimizer.py`. Two relevant code paths:

1. `_check_direction_lock` at `optimizer.py:1379–1556`. Computes a five-component composite score:
   - regime alignment (e.g., trending_up + Buy = +1, trending_up + Sell = -1, weighted by `regime_weight`)
   - structural rr asymmetry (log-scaled, weighted by `structural_weight`)
   - trade direction agreement (R1 ALPHA plumbing: `package.structural_data.trade_direction` vs claude_direction, weighted by `trade_dir_weight`)
   - global per-direction WR (signal in [-1, +1], weighted by `wr_weight`)
   - symbol-specific WR (+1 / 0 / -1, weighted by `symbol_evidence_weight`)
   - composite = sum of weighted components; `score_threshold` default 0.0
   - `locked = (composite_score < score_threshold)` → lock fires when composite is below zero
2. `APEX_DIR_LOCK` emission at `optimizer.py:328–331` when the lock fires.
3. `APEX_LOCK_DECISION_EXPLAINED` at `optimizer.py:313–326` — always emitted.
4. Internally, when APEX's DeepSeek/Qwen optimizer tries to flip the direction, the lock-emit path at `optimizer.py:437–456` (`APEX_DIR_LOCK_OVERRIDE`) reverts the Qwen-suggested flip and forces the locked direction.

APEX writes the locked direction to the trade dict via `modified["direction"] = optimized.direction` at `src/core/layer_manager.py:1745`. It also writes `modified["_apex_locked"] = bool(is_locked)` and `modified["_apex_lock_reason"] = lock_reason`.

### H2 — XRAY (Layer 1B → Layer 3 consumer)

`src/workers/strategy_worker.py:1660–2111` (`_execute_claude_trade`). Five-step flow within this function:

1. **Read APEX lock state** (line 1891): `_apex_locked = bool(trade.get("_apex_locked"))`.
2. **Read structural placement** (lines 1796–1801): `_structural = _sc.get(symbol)` from `services["structure_cache"]`, then `_sp = _structural.structural_placement`. The placement carries `rr_long`, `rr_short`, `long_sl_price`, `long_tp_price`, `short_sl_price`, `short_tp_price` — i.e., dual-direction structural levels and rrs.
3. **Compute flip ratio** (lines 1861–1865):
   - `direction == "Buy"`: `_ratio = _sp.rr_short / _sp.rr_long`
   - `direction == "Sell"`: `_ratio = _sp.rr_long / _sp.rr_short`
   - `_flip_threshold = settings.risk.xray_dir_flip_threshold_ratio` (default 3.0).
4. **Derive WR-aware override threshold** (lines 1929–1934 via `_derive_wr_aware_override_threshold` at 1551–1658):
   - Query last `wr_window=200` rows of `trade_log` for closed trades, split by direction, compute WR per direction.
   - For the direction we would flip INTO: `derived = wr_base * (1.0 - flipped_dir_wr / 100.0)`, clamped to `[wr_floor=2.0, wr_ceiling=15.0]`.
   - Cold-start: `flipped_dir_n < wr_window_min=30` → fall back to `legacy=10.0`.
5. **Decide override** (lines 1949–1954):
   ```python
   _lock_override_active = (
       _apex_locked
       and _ratio > _flip_threshold
       and _lock_override_threshold > _flip_threshold
       and _ratio > _lock_override_threshold
   )
   ```
6. **Three branches** (lines 1955–1993):
   - **Suppressed** (lock holds, ratio doesn't exceed override threshold): log `XRAY_LOCK_PRECEDENCE_RESOLUTION action=suppress` then `XRAY_FLIP_SUPPRESSED_BY_LOCK`; trade keeps brain direction.
   - **Override** (lock holds, ratio exceeds override threshold): log `XRAY_LOCK_PRECEDENCE_RESOLUTION action=override` then `XRAY_OVERRIDE_LOCK`; fall through to flip.
   - **No lock and ratio > threshold**: fall through to flip.
7. **Flip execution** (lines 1994–2111):
   - Verify dual levels exist (`_has_dual_levels`). If not, log `XRAY_DIR_BLOCK` and `TRADE_SKIP`, return `(False, "xray_dir_block")`.
   - Re-verify the flipped direction doesn't itself create a structural conflict (uptrend + flipped_dir=Sell with quality SKIP/C). If yes, log `XRAY_DIR_FLIP_BLOCKED` and `TRADE_SKIP`, return.
   - Otherwise: write `trade["direction"] = _flipped_dir` (line 2081), update sl/tp from the structural placement of the flipped direction, set `_apex_was_flipped=True`, `_flip_source="xray"`, log `XRAY_DIR_FLIP` (line 2102–2111).
8. **Placed-direction destiny** (lines 2272+ onwards): the trade dict's `direction` value is read by the coordinator (`trade_coordinator.register_trade(side=trade["direction"])`), the order service (`order_service.place_order(side=trade["direction"])`), and the thesis save.

## H1 — Long-RR and Short-RR Formulas (Computation Verification)

`src/analysis/structure/structural_levels.py`. Both formulas are mirror-symmetric:

### Long (lines 67–172):

```
SL: structural_sl = nearest_support.zone_low - (nearest_support.price * sl_buffer_pct/100)
   else fallback = current * (1 - sl_fallback_pct/100)
TP: raw_tp = nearest_resistance.zone_low - (nearest_resistance.price * tp_buffer_pct/100)
    min_tp = current + current * (tp_min_distance_pct/100)
    if raw_tp < min_tp:
        is_structurally_invalid = True
        structural_tp = min_tp                         # CLAMPED
    else:
        structural_tp = raw_tp
    fallback (no resistance): structural_tp = current * (1 + tp_fallback_pct/100)

risk = |current - structural_sl|
reward = |structural_tp - current|
rr_long = reward / risk if risk > 0 else 0
```

### Short (lines 174–259):

```
SL: structural_sl = nearest_resistance.zone_high + (nearest_resistance.price * sl_buffer_pct/100)
   else fallback = current * (1 + sl_fallback_pct/100)
TP: raw_tp = nearest_support.zone_high + (nearest_support.price * tp_buffer_pct/100)
    max_tp = current - current * (tp_min_distance_pct/100)
    if raw_tp > max_tp:
        is_structurally_invalid = True
        structural_tp = max_tp                         # CLAMPED
    else:
        structural_tp = raw_tp
    fallback (no support): structural_tp = current * (1 - tp_fallback_pct/100)

risk = |structural_sl - current|
reward = |current - structural_tp|
rr_short = reward / risk if risk > 0 else 0
```

**Roles are not swapped.** Support is used as long-SL reference and short-TP reference. Resistance is used as long-TP reference and short-SL reference. This is correct trading geometry.

### Worked trace: NEARUSDT 2026-05-22 15:20:58 — ratio=100.6x, rr_long=0.1, rr_short=14.1

If price ~= 2.20 and nearest support ~= 1.85 and nearest resistance ~= 2.21 (price hugging resistance):
- Long: risk = 2.20 - 1.85 = 0.35, raw_tp = 2.21 - small_buffer ≈ 2.207, min_tp = 2.20 + 2.20*0.005 = 2.211, clamped → structural_tp = 2.211, reward = 2.211 - 2.20 = 0.011, rr_long = 0.011/0.35 ≈ 0.03 (rounds to 0.1 with floor display).
- Short: risk = 2.21 + small_buffer - 2.20 = 0.011, reward = 2.20 - 1.85 = 0.35, rr_short = 0.35/0.011 ≈ 32.

Geometry is correct. The asymmetry (rr_short much larger than rr_long) is real and arises whenever price sits near a structural level — the direction whose SL is the nearby level has tiny risk and large reward; the direction whose TP is the nearby level has tiny reward (often clamped) and large risk.

**Conclusion:** The long_rr / short_rr formulas are computationally correct. The systematic asymmetry observed in the 2026-05-22 logs is a real structural fact, not a formula bug. P0-2's root cause is therefore **not in the computation**.

## H1 — Precedence Chain in Code (today)

When all three authorities express a direction:

```
1. brain emits trade.direction = Buy
2. APEX optimizes; may lock (sets _apex_locked) but does NOT change direction
   - If APEX's internal Qwen tries to flip while locked, APEX reverts
     (APEX_DIR_LOCK_OVERRIDE), forcing direction back to Buy
3. modified["direction"] = optimized.direction (= Buy)  — layer_manager.py:1745
4. XRAY (strategy_worker._execute_claude_trade) reads _apex_locked
5. If _ratio > flip_threshold AND (lock-override-active OR no-lock):
     trade["direction"] = "Sell"                       — strategy_worker.py:2081
     log XRAY_DIR_FLIP
6. order_service.place_order(side=trade["direction"]=Sell)
```

So **today's precedence is XRAY > APEX > brain.** The dual logging (`APEX_DIR_LOCK | dir=Buy` and `XRAY_DIR_FLIP | flipped_dir=Sell`) is a true reflection of two components asserting their own internal decision; the placed direction is whichever wrote `trade["direction"]` last (XRAY).

## H1 — Aim-Bias Considerations for the Fix

| Aim-bias question | Today | After authority-based fix |
| --- | --- | --- |
| Preserves trade frequency? | yes | yes (vetoes become skips, not silent reversals; net frequency unchanged or higher because brain's good directions execute) |
| Preserves aggression? | partial (high-conviction brain Buys reversed) | yes (brain's intended direction trades) |
| Improves decision quality? | no (contradictory dual logging) | yes (single direction-decision log per trade) |
| Preserves passive-close advantage? | n/a | n/a |
| Respects layer separation? | no (Layer 1B XRAY overrides Layer 2 brain) | yes (XRAY allowed to veto/skip; not reverse) |

## H1 — Dependent Files (must read before edit)

- `src/workers/strategy_worker.py` lines 1551–2111 — primary edit site.
- `src/apex/optimizer.py` lines 313–331, 437–487 — the APEX_DIR_LOCK / OVERRIDE emit. Today's APEX path may need a single coherent decision log instead of two separate ones.
- `src/analysis/structure/structural_levels.py` — read; no edit needed (formulas are correct).
- `src/analysis/structure/structure_engine.py` lines 293–394 — read; provides `rr_long`/`rr_short` to the structural placement. No edit needed.
- `src/core/layer_manager.py` line 1745 — read; APEX writes `modified["direction"]`. Not changed by P0-2 fix.
- `src/core/trade_coordinator.py` (`register_trade` side handling) — read; consumes the final direction. Not changed.
- `config.toml` — `xray_dir_flip_threshold_ratio = 3.0`, `xray_lock_override_*` settings. May add `xray_high_conviction_*` keys if the fix introduces them.

## H1 — Log Tag Inventory (P0-2 surface)

| Tag | File:line | Today's role | After fix |
| --- | --- | --- | --- |
| `APEX_LOCK_DECISION_EXPLAINED` | optimizer.py:313 | always | keep |
| `APEX_DIR_LOCK` | optimizer.py:328 | when lock fires | keep, but gated when XRAY overrides (no dual logging) |
| `APEX_DIR_LOCK_OVERRIDE` | optimizer.py:437 | APEX's internal Qwen-flip revert | keep |
| `APEX_LOCK_OVERRIDE_DENIED` | optimizer.py:450 | Qwen flip suppressed | keep |
| `APEX_LOCK_OVERRIDE_GRANTED` | optimizer.py:479 | Qwen flip allowed when no lock | keep |
| `XRAY_OVERRIDE_RATIO_DETAIL` | strategy_worker.py:1937 | always (WR meta) | keep |
| `XRAY_LOCK_PRECEDENCE_RESOLUTION` | strategy_worker.py:1957/1975 | always (decision) | REPLACED by `DIRECTION_DECISION` |
| `XRAY_FLIP_SUPPRESSED_BY_LOCK` | strategy_worker.py:1963 | when lock blocks flip | possibly replaced by `DIRECTION_DECISION action=keep` |
| `XRAY_OVERRIDE_LOCK` | strategy_worker.py:1981 | when ratio overrides lock | REMOVED (replaced by `DIRECTION_DECISION authority=XRAY action=skip/flip`) |
| `XRAY_DIR_FLIP` | strategy_worker.py:2102 | when flip executes | REPLACED by `DIRECTION_DECISION action=flip` (only in low-conviction case) or removed (high-conviction case becomes skip, not flip) |
| `XRAY_DIR_MISMATCH` | strategy_worker.py:1836 | warning when brain chose worse rr | keep |
| `XRAY_DIR_BLOCK` | strategy_worker.py:2008 | dual levels missing for flip | keep |
| `XRAY_DIR_FLIP_BLOCKED` | strategy_worker.py:2039 | post-flip structural conflict | keep |
| `XRAY_CLAMP_DETECTED` | structure_engine.py:385 | rr clamped to floor | keep |
