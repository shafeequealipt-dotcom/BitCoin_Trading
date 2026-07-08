# PRIMARY Issue — Phase 1 Step P.1.4: strategy_worker Direction-Decision Path

Source: `src/workers/strategy_worker.py` (130,355 bytes / ~3,200 lines)
Function under investigation: `_execute_claude_trade` (lines 1417-2283)
Status: critical sections read end-to-end. Investigation only — no code changes.

## 1. High-level Flow Of `_execute_claude_trade`

| Phase | Lines | Action |
|-------|-------|--------|
| Entry parsing | 1417-1500 | Pull `direction`, `symbol`, `size_usd`, `score`, etc. from `trade` dict (already mutated by `layer_manager._apply_apex_optimization`) |
| Position check | 1500-1580 | Skip if duplicate position; pull X-RAY structural data (`_structural`, `_ms`, `_sp`) |
| X-RAY conflict block | 1580-1602 | If direction conflicts with strong structure on weak setup → emit `XRAY_CONFLICT` + `TRADE_SKIP` |
| X-RAY direction-mismatch warn | 1590-1602 | `XRAY_DIR_MISMATCH` (WARNING) when chosen direction has visibly worse R:R than opposite |
| **X-RAY flip gate** | **1604-1779** | The XRAY-driven flip (or suppression if APEX_DIR_LOCK active) — see §3 |
| Testnet symbol check | 1780-1789 | Skip if symbol not whitelisted on testnet |
| Dup position check | 1791-1798 | Skip if symbol already open |
| (many further validations) | 1800-2150 | Risk checks, capital, leverage, thesis save, etc. |
| Leverage set | 2160-2180 | Push leverage to exchange |
| **`DIRECTION_DECISION` emit** | **2185-2266** | The unified per-trade decision summary — see §4 |
| BRAIN_VS_ANALYSIS visibility | 2268-2288 | Disagreement WARN — see §5 |
| **Order placement** | **2290-…** | Place the order with `side_enum = Side.BUY if direction == "Buy" else Side.SELL` (line 2183) |

## 2. Order Of Direction Mutations

The `direction` local in this function (the value used at line 2183 for order placement) reaches its final state through this sequence of upstream and inline transforms:

1. **Brain output → `directive["direction"]`** in strategy_worker upstream callers.
2. **APEX optimization** (`layer_manager._apply_apex_optimization(trade, optimized)` outside this function):
   - May set `trade["direction"] = optimized.direction`
   - Stamps `trade["_apex_original_direction"] = brain_dir`, `trade["_apex_was_flipped"]`, `trade["_apex_locked"]`, `trade["_apex_lock_reason"]`
3. **Function entry** at line 1417: `direction = trade.get("direction")` (or similar — captured as local).
4. **XRAY flip gate** (lines 1604-1779) may **further** mutate:
   - `trade["direction"]`
   - `trade["stop_loss_price"]`, `trade["sl"]`, `trade["take_profit_price"]`
   - `trade["_apex_original_direction"]` (only if not already set)
   - `trade["_apex_was_flipped"] = True`
   - `trade["_flip_source"] = "xray"`
   - `trade["_xray_flip_ratio"]`, `trade["_xray_flip_rr_long"]`, `trade["_xray_flip_rr_short"]`
   - The local `direction` is reassigned: `direction = _flipped_dir`
5. **`DIRECTION_DECISION` log** (lines 2254-2266) prints the final state for audit.
6. **Order placement** uses the final `direction` local.

## 3. XRAY Direction Flip Gate (lines 1604-1779)

This is the SECOND flip path. Independent of APEX. Runs in strategy_worker (not in `apex.optimize()`).

### Ratio computation (lines 1618-1622)

```python
_ratio = 0.0
if direction == "Buy" and _sp.rr_long > 0:
    _ratio = _sp.rr_short / _sp.rr_long
elif direction == "Sell" and _sp.rr_short > 0:
    _ratio = _sp.rr_long / _sp.rr_short
```

If `_sp.rr_long` (when direction=Buy) or `_sp.rr_short` (when direction=Sell) is zero/missing, `_ratio` stays 0.0. This is why `xray_ratio=0.0x` appears in 17/27 DIRECTION_DECISION events in the spec window — those trades had no usable XRAY data for the chosen-direction RR, so the ratio defaulted to 0 and the XRAY flip block did not fire. Their direction changes were APEX-driven (`reason=apex_flip` not `xray_flip`).

### Threshold

`settings.risk.xray_dir_flip_threshold_ratio` (default 3.0; config.toml:341). A ratio above 3.0 means the opposite direction has at least 3× the R:R of the chosen direction.

### APEX_DIR_LOCK interlock (lines 1648-1660)

```python
_apex_locked = bool(trade.get("_apex_locked"))
if _apex_locked and _ratio > _flip_threshold:
    log.warning(
        f"XRAY_FLIP_SUPPRESSED_BY_LOCK | sym={symbol} ...",
    )
    trade["_xray_flip_suppressed_by_lock"] = True
```

This is recent (Issue 1 fix 2026-05-11, the current branch). When APEX has locked direction (trending/volatile), XRAY no longer overrides. The interlock activated once today (per Phase 0 baseline log count = 1 `xray_flip_suppressed_by_lock` event).

### Dual-levels precondition (lines 1663-1691)

```python
_has_dual_levels = (
    _sp.long_sl_price > 0 and _sp.long_tp_price > 0
    and _sp.short_sl_price > 0 and _sp.short_tp_price > 0
)
if not _has_dual_levels:
    # XRAY_DIR_BLOCK + TRADE_SKIP — trade is SKIPPED entirely, not just unflipped
    return (False, "xray_dir_block")
```

If `_has_dual_levels` is False, the trade is SKIPPED. Not flipped, not kept original — skipped. This is the "missing_dual_structural_levels" path.

### Post-flip conflict recheck (lines 1697-1722)

If the flipped direction itself conflicts with a strong structural trend on a low-quality setup (SKIP/C), emit `XRAY_DIR_FLIP_BLOCKED` and skip the trade. So a flip-into-conflict is treated as no-trade.

### Flip mutation (lines 1724-1778)

When all gates pass, the trade dict is mutated to the flipped direction, SL/TP swapped to structural placement levels for the flipped direction, and:

```python
trade["_apex_original_direction"] = _orig_dir   # only if not already set
trade["_apex_was_flipped"] = True
trade["_flip_source"] = "xray"
trade["_xray_flip_ratio"] = round(_ratio, 2)
trade["_xray_flip_rr_long"] = round(_sp.rr_long, 2)
trade["_xray_flip_rr_short"] = round(_sp.rr_short, 2)
direction = _flipped_dir
```

Then `XRAY_DIR_FLIP` (WARNING) is emitted with `original_dir`, `flipped_dir`, `rr_original`, `rr_flipped`, `ratio`, `size_usd`, `sl`, `tp` fields.

### THESIS_FLIP_PERSISTED (in core/thesis_manager.py:189)

Emitted at INFO level when the thesis save (later in `_execute_claude_trade`) detects `xray_flip_source` and persists the flip rationale to `trade_thesis` table (schema v28 columns).

## 4. Unified `DIRECTION_DECISION` Emission (lines 2185-2266)

This is the **single greppable log line per trade** that captures the brain → APEX → XRAY → final direction journey.

### Field derivation

```python
_brain_dir = trade.get("_apex_original_direction") or direction
_flip_source = trade.get("_flip_source") or ""
_apex_locked = bool(trade.get("_apex_locked"))
_flip_suppressed = bool(trade.get("_xray_flip_suppressed_by_lock"))
_was_flipped = bool(trade.get("_apex_was_flipped"))
```

### Reason taxonomy (the 5 codes, in priority order)

```
if _flip_suppressed:
    _dir_reason = "xray_flip_suppressed_by_lock"
elif _was_flipped and _flip_source == "xray":
    _dir_reason = "xray_flip"
elif _was_flipped:
    _dir_reason = "apex_flip"
elif _apex_locked:
    _dir_reason = "apex_dir_lock_held"
else:
    _dir_reason = "clean"
```

Note: `xray_flip_overrode_apex_flip` is documented in the comment (line 2194) but the code-path that would emit it does not exist — if APEX flipped (`_was_flipped=True`, `_flip_source=""`) and then XRAY flipped on top, the XRAY path overwrites `_flip_source="xray"`, which classifies the row as `xray_flip` not the stacked code. This is dead-string-doc-but-not-actual-emission — a documentation inconsistency, not a bug.

### Live taxonomy distribution today (from Phase 0 baseline)

| Code | Count |
|------|-------|
| `clean` | 28 |
| `xray_flip` | 19 |
| `apex_flip` | 13 |
| `apex_dir_lock_held` | 4 |
| `xray_flip_suppressed_by_lock` | 1 |

XRAY accounts for more flips than APEX today (19 vs 13).

### Full log line fields

```
DIRECTION_DECISION | sym={symbol} brain_dir={brain} final_dir={final}
  flipped={Y|N} flip_source={apex|xray|none}
  apex_locked={Y|N} lock_reason='{reason}'
  xray_ratio={x.x}x reason={code}
  analysis_dir={Buy|Sell|NEUTRAL|UNKNOWN}
  analysis_score={+/-x.xx} analysis_conf={x.xx}
```

Single grep target for all post-hoc analysis.

## 5. `BRAIN_VS_ANALYSIS_DISAGREEMENT` Visibility Event (lines 2268-2288)

A T2-3 / F11 visibility tag from six-tier-fixes (2026-05-11). Fires only when ALL:
- TA cache analysis verdict is `Buy` or `Sell` (not NEUTRAL/UNKNOWN)
- Analysis direction ≠ brain direction
- No flip reconciled (final_dir == brain_dir)
- `_was_flipped` is False

By design **visibility-only** — does not block. Operators were intended to use this to build a counter-example dataset before deciding whether to enforce.

In today's logs the count is unverified (Phase 0 didn't tally this specifically). P.1.9 sampling will capture occurrences.

## 6. Why XRAY-driven Flips Outnumber APEX-driven Today

Today (~9 h of logs):
- `XRAY_DIR_FLIP`: 19 events
- `APEX_FLIP`: 23 events
- DIRECTION_DECISION reasons: 19 `xray_flip` + 13 `apex_flip`

The 23 APEX_FLIP vs 13 DIRECTION_DECISION `apex_flip` discrepancy: `APEX_FLIP` fires inside `optimizer._log_optimization` whether or not strategy_worker eventually proceeds. Some APEX-flipped trades are SKIPPED downstream by gate, X-RAY conflict, dup-position, leverage-set failure, etc., so they never reach the DIRECTION_DECISION log. That explains the delta (23 logged at APEX → 13 reached the final decision step → some XRAY-flipped them onward).

XRAY flips dominate because:
- Per spec, X-RAY structural R:R is what XRAY measures. When structure heavily favors one side, the ratio quickly exceeds 3.0.
- The threshold (3.0) is the same default that APEX uses for the (broken) RR-boost — so the X-RAY signal is consistent across the two systems, but only the XRAY path actually consumes it.

## 7. The XRAY Flip Has Its Own Default Bias

Unlike APEX (which depends on DeepSeek's model output), the XRAY flip is purely deterministic code:
- It flips when `rr_opposite / rr_chosen > 3.0`.
- Brain's choice of direction influences the **direction** field of the ratio, but the flip fires symmetrically: brain=Buy can flip to Sell (when rr_short >> rr_long), and brain=Sell can flip to Buy (when rr_long >> rr_short).

The asymmetry observed (most flips are Buy → Sell) is therefore mediated by the **structural placement data** — specifically whether `rr_short > 3 × rr_long` more often than `rr_long > 3 × rr_short` in the current market. P.1.5 will explore this further.

## 8. Findings Map

| Question | Answer |
|----------|--------|
| Where does the final direction get decided? | `direction` local in `_execute_claude_trade`; final value at line 2183 (`Side.BUY if direction == "Buy" else Side.SELL`) |
| How many flip paths exist? | Two: APEX (via DeepSeek inside `optimize()`) and XRAY (deterministic code in strategy_worker.py:1604-1779) |
| Can XRAY flip a trade after APEX flipped it? | Yes — the XRAY gate runs on `trade["direction"]` regardless of who put it there, but `_flip_source` is overwritten to `"xray"`, so the DIRECTION_DECISION reason becomes `xray_flip` |
| How are stacked flips logged? | They're collapsed to `xray_flip`; the docstring promises a `xray_flip_overrode_apex_flip` code that the actual emit ladder does not produce. |
| Does the XRAY flip respect APEX_DIR_LOCK? | YES since Issue 1 fix 2026-05-11 — `XRAY_FLIP_SUPPRESSED_BY_LOCK` event |
| Why do 17/27 flips have `xray_ratio=0.0x`? | Either rr_long (when Buy) or rr_short (when Sell) was zero/missing, so the ratio defaulted to 0 — those flips were APEX-driven, not XRAY-driven. The ratio field is populated by the XRAY block which didn't run on those trades. |

## 9. Out-of-Scope Confirmation

- No code changes.
- No interaction with Stage 2 brain prompt, Layer 1 scanner, Bybit execution.
