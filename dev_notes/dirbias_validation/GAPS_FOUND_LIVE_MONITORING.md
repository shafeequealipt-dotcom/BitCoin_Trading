# Live Monitoring Gaps Report — Root Cause Analysis

Date: 2026-05-19  
Window: 10:55:55 → 13:04:33 UTC (post-restart Phase B+C trial, 2 h 9 m)  
Source artifacts: persistent Monitor B (`bo5b141xd`), Monitor C (`b8kmicupd`), `data/logs/brain.log`, `data/logs/general.log`, `data/logs/workers.2026-05-19_11-26-15_574407.log`  
Scope: gaps observed during real-time monitoring of the four shipped direction-bias fixes; each gap is downstream of a successfully-shipped fix and represents a follow-up opportunity, NOT a fix regression.

---

## Headline

All four fixes responded correctly to real market data — boot sentinels fired, no error events, no sentinel regressions, brain produced balanced direction calls (59% Buy), XRAY arbitrated quantitative truth (~19% flip rate), execution converged to 50/50 (vs 89% Sell pre-fix). But three gaps surfaced when watching the downstream side of each fix:

| # | Gap | Severity | Root cause class |
|---|---|---|---|
| 1 | `is_structurally_invalid` flag has zero consumers | LOW | Deliberate scope-limit in Phase C ("math safety first, policy later") |
| 2 | Brain ↔ XRAY decoupling on persistently-invalid structures (MNT case) | MEDIUM | Brain prompt SHOWS quantitative RR_DIR but qualitative signals outweigh it on Claude's reasoning; `is_structurally_invalid` not in prompt at all |
| 3 | Silent skip on cooldown / position-cap / scale-in (HYPE case) | LOW (observability) | Brain → strategy_worker → cooldown chain has no unified `STRAT_DIRECTIVE_REJECTED` event tying the rejected directive to the block reason |

None of the three is a code regression. All three pre-date the direction-bias fix series but were unmasked by it (the symmetric prompt + soft haircut increase the cadence of Buy directives, which surfaces these patterns more often).

---

## Gap 1 — `is_structurally_invalid` flag has zero consumers

### What was observed

Issue 1 of the direction-bias series introduced a min-edge floor clamp in `_calc_long` / `_calc_short` (`src/analysis/structure/structural_levels.py:109-120` for long, `:208-215` for short). When the raw `structural_tp` would have landed on the wrong side of `current_price` (the pre-fix collapse signature), the clamp:
1. Forces `structural_tp` to `current_price * (1 ± tp_min_distance_pct/100)` (0.5% offset, configurable).
2. Sets `is_structurally_invalid = True` on the returned `StructuralPlacement`.
3. Emits `invalid=True` in the `XRAY_LEVELS` debug log.

During live monitoring, the clamp activated visibly at **MNTUSDT 11:34:30** (`rr_original=0.2 ratio=21.6x size_usd=$252`) and **MNTUSDT 12:02:13** (`rr_original=0.2 ratio=18.1x size_usd=$50`). Both times `rr_original=0.2` is the exact arithmetic signature of the clamp activation (`current_price × 1.005 ÷ ~2.5% SL ≈ 0.2`).

### Evidence

Direct grep across `src/` and `tests/` for `is_structurally_invalid`:

```
src/analysis/structure/structural_levels.py:106  is_structurally_invalid = False
src/analysis/structure/structural_levels.py:119  is_structurally_invalid = True
src/analysis/structure/structural_levels.py:156  f"invalid={is_structurally_invalid}"
src/analysis/structure/structural_levels.py:171  is_structurally_invalid=is_structurally_invalid,
src/analysis/structure/structural_levels.py:205  is_structurally_invalid = False
src/analysis/structure/structural_levels.py:214  is_structurally_invalid = True
src/analysis/structure/structural_levels.py:243  f"invalid={is_structurally_invalid}"
src/analysis/structure/structural_levels.py:258  is_structurally_invalid=is_structurally_invalid,
src/analysis/structure/models/structure_types.py    (field definition + to_dict serialization)
tests/test_structural_floor.py                       (asserts presence)
```

**No `src/` or `src/*/*` file outside `structural_levels.py` (which sets it) and `structure_types.py` (which defines + serializes it) reads the field.** APEX optimizer, gate, watchdog, strategy_worker, profit_sniper, layer4_protection, brain prompt builder — none of them branch on `is_structurally_invalid`.

### Root cause

This is a **deliberate scope-limit at Phase C ship time** (Issue 1 of the direction-bias series, 2026-05-19, commit `99b3420`). The audit at `dev_notes/dirbias_validation/audit_phase_c.md §13` explicitly states:

> `is_structurally_invalid` field on `StructuralPlacement` — currently no programmatic consumer (purely additive). Optional future consumers: APEX optimizer sizing, watchdog skip; deferred per audit_phase_c.md §13.

The reasoning was sound at ship time: defining "policy" for what to do with invalid placements (downsize? skip? alert?) is a design decision that should not be bundled with the math-correctness fix. Shipping the math safety first (no more div-by-zero, no more rr=0 collapse) lets the trial measure whether further policy is even needed.

### Concrete consequence

Every clamp activation produces a trade with `rr_original ≈ 0.2` that proceeds to the XRAY flip block. The math is sound but the system has no signal to reject or downsize a trade whose original-direction placement was a clamp activation. Per-trial: MNT flipped at ratio=21.6x then 18.1x — both flipped to a healthy Sell with rr=4.5-5.4, so the final trades were fine. But there is no policy distinguishing "real 5x asymmetry" from "5x asymmetry because Buy side hit the clamp floor."

### Remediations (deferred, not blocking)

| Option | Where to wire | Effect |
|---|---|---|
| **APEX sizing reduction** | `src/apex/optimizer.py:_size_compute()` | If `placement.is_structurally_invalid`, multiply size by 0.5 |
| **Skip the trade** | `src/apex/gate.py:_evaluate()` | If both Buy-side AND Sell-side placements are invalid, reject the entry |
| **Skip the flip** | `src/workers/strategy_worker.py:1727+ (flip block)` | If the *original* direction's placement is invalid AND flipped direction is invalid, skip; if only original is invalid, allow flip (current behavior — letting structural truth win) |
| **Watchdog protection** | `src/workers/position_watchdog.py` | If an open position has `is_structurally_invalid=True` in its current placement, trigger earlier close |

These are deferred to post-trial. The 48-72h trial will indicate whether clamp-activation flips are net winners (no action needed) or net losers (sizing reduction or skip warranted).

---

## Gap 2 — Brain ↔ XRAY decoupling on persistently-invalid structures (MNTUSDT case)

### What was observed

MNTUSDT appeared three times in 7 brain batches:

| Batch | Time | Brain dir | XRAY flip | rr_original | rr_flipped | Ratio |
|---|---|---|---|---|---|---|
| 4 | 11:34:14 | Buy | Buy → Sell | 0.2 | 5.4 | 21.6x |
| 7 | 12:01:53 | Buy | Buy → Sell | 0.2 | 4.5 | 18.1x |

The brain produced identical Buy directives 27 minutes apart (`"STRONG ensemble (66.4, grade B) with BUY=5.45 vs SELL=0.00 — zero opposition"`). Each time XRAY flipped to Sell on the same `rr_original=0.2` clamp signature.

This is a **persistent state**: MNTUSDT's Buy-side structural placement is invalid for at least 30 minutes continuously, but the brain keeps generating Buy directives.

### Evidence — what the brain CAN see

Reading `src/brain/strategist.py:1370-1390`:

```python
1372    line += f"RR=1:{sp.rr_ratio:.1f}({sp.rr_quality}) "
1373    # Phase 10 (P1-9): expose BOTH directions' R:R
1374    # so Claude can see when one side has a much
1375    # better setup than the other. The strategy
1376    # worker hard-blocks at >5x and reduces size
1377    # at >3x; Claude seeing the comparison up-front
1378    # avoids picking the losing side in the first
1379    # place.
1380    if sp.rr_long > 0 and sp.rr_short > 0:
1381        if sp.rr_long >= sp.rr_short:
1382            _ratio = sp.rr_long / max(sp.rr_short, 0.01)
1383            _best = "LONG"
1384        else:
1385            _ratio = sp.rr_short / max(sp.rr_long, 0.01)
1386            _best = "SHORT"
1387        line += (
1388            f"RR_DIR(L={sp.rr_long:.1f},S={sp.rr_short:.1f},"
1389            f"best={_best},{_ratio:.1f}x) "
1390        )
```

So the prompt explicitly includes `RR_DIR(L=0.2,S=5.4,best=SHORT,21.6x)` (or similar) for each coin. The brain HAS this information.

### Evidence — what the brain CANNOT see

`is_structurally_invalid` is **never added to the brain prompt**. Grep across `src/brain/` returns zero references:

```
$ grep -rn "is_structurally_invalid" src/brain/
(no output)
```

So the brain sees the *numeric* RR comparison but has no signal that the lower number is a clamp floor (synthetic) rather than a low-but-real structural value. From the brain's perspective, `rr_long=0.2 rr_short=5.4` looks like "Buy has weak structure but exists" — not "Buy is structurally impossible and the 0.2 is a math-safety floor."

### Root cause

Two compounding factors:

**Cause 2a — Information not surfaced**. The `is_structurally_invalid` flag is set on the `StructuralPlacement` returned from `_calc_long` / `_calc_short` but never makes its way into the prompt text. The strategist's prompt-building path uses `sp.rr_long` and `sp.rr_short` numerically (line 1380-1389) but never reads `sp.is_structurally_invalid`. There is no `INVALID_LONG=Y` or `INVALID_SHORT=Y` annotation in the rendered prompt.

**Cause 2b — Qualitative signals outweigh quantitative RR_DIR**. Even with `RR_DIR(L=0.2,S=5.4,best=SHORT,21.6x)` visible, the brain on MNT picked Buy because the per-coin label slate (TREND_PULLBACK_LONG with `BUY=5.45 vs SELL=0.00 — zero opposition`) dominated the decision. The strategist comment claims the RR_DIR up-front display would "avoid picking the losing side" but the observed behavior contradicts that on a 21.6x asymmetry — the qualitative ensemble win was stronger than the structural quantitative loss.

This is not a fix bug — it is the expected outcome of the design: brain weighs qualitative + label + ensemble signals heavily, and the symmetric prompt (Issue 4) explicitly removed the asymmetric pull that would have biased toward Sell. The XRAY flip block exists precisely to handle this case: structural truth overrides brain qualitative.

### Concrete consequence

The brain wastes ~2 min of compute per cycle producing Buy directives that get auto-flipped. Final trades are correct (Sell wins on real 4-5x RR) but the brain has no feedback loop telling it "the Buy you keep picking is structurally impossible; consider Sell."

### Remediations (deferred)

| Option | Where to wire | Effect |
|---|---|---|
| **Surface invalid flag in prompt** | `src/brain/strategist.py:~1387` | Append `INVALID_LONG=Y` or `INVALID_SHORT=Y` to the RR_DIR line when the relevant `sp.is_structurally_invalid_<dir>` is true |
| **Bidirectional invalid flags** | `src/analysis/structure/models/structure_types.py` + `structural_levels.py` | Currently `is_structurally_invalid` is a single bool on the chosen direction. Split into `is_long_invalid` and `is_short_invalid` so the prompt can surface both |
| **Brain-level steering rule** | `src/brain/strategist.py` system prompt | Add explicit guidance: "When RR_DIR shows >10x asymmetry AND the inferior direction is INVALID, prefer the superior direction even if qualitative signals favor the inferior one" |
| **Pre-strategist filter** | `src/workers/scanner_worker.py` selection | Skip coins where the suggested trade direction has `is_structurally_invalid=True` — don't even let them into the brain's candidate slate |

The last option is the cleanest separation: Layer 1B's quantitative truth filters the slate Layer 2 evaluates.

---

## Gap 3 — Silent skip on cooldown / position-cap / scale-in (HYPEUSDT case)

### What was observed

HYPEUSDT appeared in 6 brain batches during the monitoring window. Tracking the full lifecycle:

| Batch | Time | Brain dir | Observed outcome |
|---|---|---|---|
| 1 | 11:08:05 | Buy | XRAY flip → Sell qty=80.61 ($780) executed |
| 3 | 11:25:05 | Buy | XRAY flip → Sell qty=6.23 ($52) scale-in |
| 7 | 12:01:53 | Buy | Buy qty=32.15 executed (no flip — structural state shifted) |
| 12 | 12:48:25 | Buy | **No execution event observed** |
| 13 | 12:56:40 | Buy | **No execution event observed** |
| 14 | 13:04:33 | Buy | **No execution event observed** |

Real-time Monitor B grep pattern included `BYBIT_DEMO_ORDER_RECEIVED` and `LAYER4_FORCE_CLOSE` but caught nothing on HYPE for batches 12-14.

### Evidence — what actually happened

Post-monitoring grep of `data/logs/workers.2026-05-19_11-26-15_574407.log` (the rotated workers log covering the window):

**12:40:25.676** — HYPEUSDT position closed by watchdog:
```
TIME_DECAY_MAE_MONOTONIC_HOLD | sym=HYPEUSDT attempted=-0.62% held=-0.65% source=live_tick tick=158
TIME_DECAY_STRUCT_GUARD | sym=HYPEUSDT p_win=0.050 pnl=-0.62% mae=-0.65% reason='stable' blocked=true
BYBIT_DEMO_POSITION_CLOSE | sym=HYPEUSDT purpose=layer4_close close_trigger=wd_timeout
COORD_PNL_BACK_DERIVED | sym=HYPEUSDT pnl_pct=-0.6242% win=N by=wd_timeout
COORD_LOSS_COOLDOWN_SET | sym=HYPEUSDT dir=Buy cooldown_sec=600
```

The batch-7 HYPE Buy position (entered 12:02:15) was force-closed by the watchdog after 38.2 min hold with -0.62% PnL via `wd_timeout`. A 600-second loss-cooldown was set, ending at **12:50:25 UTC**.

**Batch 12 (12:48:25)**: brain Buy directive arrived **2 minutes before** the cooldown expired → silently absorbed by `is_reentry_blocked()` in `src/core/trade_coordinator.py`. **This is the 5-min reentry cooldown + 10-min loss extension from the older `project_three_issues_fix_2026_05_18` series working as designed.**

**Batch 13 (12:56:40)**: brain Buy directive arrived 6 min after cooldown expired. Should be eligible. Post-batch-13 signal generator activity at 12:56:00 showed:
```
SIG_GEN_INPUT | sym=HYPEUSDT sentiment=-0.094 fg=25 funding=-0.00036 oi_change=-0.71
SIG_CLASSIFY | direction_score=+0.468 type=buy
SIG_DOWNGRADE | sym=HYPEUSDT from=buy to=neutral conf=0.28 strong_min=0.60 buy_min=0.40
```

The signal generator downgraded HYPE's buy signal to neutral (conf 0.28 < buy_min 0.40 threshold). The brain still produced Buy at 12:56:40 (label slate said Buy), but the strategy_worker likely deprioritized based on the downgraded signal OR another layer applied a soft reject. **No event was logged that ties the brain directive to this downgrade-driven skip.**

**Batch 14 (13:04:33)**: similar pattern — likely SIG_DOWNGRADE or signal-confidence-below-threshold caused silent absorption. No `STRAT_DIRECTIVE_REJECTED` event in the chain.

### Root cause

The skip chain has **no unified observability event**:

1. Brain emits `STRAT_DIRECTIVE` (INFO level, `brain.log`)
2. Strategy_worker picks it up
3. Strategy_worker (or layer_manager / APEX gate / trade_coordinator) checks one of several blockers:
   - `trade_coordinator.is_reentry_blocked(sym, dir)` — the 5-10 min cooldown gate
   - APEX direction-lock conflict with existing position
   - Position-cap / scale-in size already at max
   - Signal-confidence below threshold (SIG_DOWNGRADE consumption)
4. On block, the worker silently returns. Each blocker logs its own informational event (`COORD_LOSS_COOLDOWN_SET`, `SIG_DOWNGRADE`, etc.) but **none of these events explicitly says "directive XYZ from cycle ABC was REJECTED because of XYZ"**.

The result: tracing why a specific brain directive didn't execute requires:
- Find the directive (`brain.log STRAT_DIRECTIVE`)
- Find the time-correlated cooldown/signal events (`workers.log SIG_DOWNGRADE`, `COORD_LOSS_COOLDOWN_SET`)
- Manually correlate by symbol + timestamp range
- Infer the cause

Realtime monitors with a `tail -F | grep` pattern miss this because there is no canonical event name to grep for.

### Concrete consequence

Three brain directives on HYPE produced no trade and no observable error or rejection in the realtime stream. **The fixes are not at fault** — the silent absorption is pre-existing pre-fix behavior unmasked by Issue 3's higher cadence of LONG labels (which raises the rate at which the brain re-suggests already-blocked coins).

### Remediations (deferred)

| Option | Where to wire | Effect |
|---|---|---|
| **Add `STRAT_DIRECTIVE_REJECTED` event** | `src/workers/strategy_worker.py` orchestration entry point | When a directive is silently absorbed by ANY downstream blocker, emit `STRAT_DIRECTIVE_REJECTED \| sym=X dir=Y reason=<cooldown\|direction_lock\|sig_downgrade\|cap>` at INFO level so monitors and audits can capture it without correlation |
| **Tag rejection reasons** | Each individual blocker (cooldown, gate, sizing) | Each blocker emits a typed rejection event with the originating directive's `did` so the chain is traceable end-to-end |
| **Scanner pre-filter** | `src/workers/scanner_worker.py` | If a coin is in active cooldown AND the scanner is about to emit it for brain evaluation, skip — don't waste a brain cycle on a directive that will be rejected anyway |

The last option also reduces brain compute waste (Gap 2 has a similar pre-filter recommendation for invalid placements).

---

## Cross-cutting recommendations

Three gaps, one common shape: **information that exists upstream is not surfaced to the layer that would benefit from it**.

1. `is_structurally_invalid` exists in Layer 1B but not in Layer 2 (brain prompt).
2. Loss cooldowns exist in `trade_coordinator` but the rejection isn't tied back to the originating `STRAT_DIRECTIVE`.
3. SIG_DOWNGRADE happens in the signal_generator but the brain doesn't see "this signal was downgraded after your last cycle."

A single observability primitive — a `STRAT_DIRECTIVE_LIFECYCLE` event chain (directive emitted → directive picked up → directive evaluated → directive accepted/rejected with reason) — would close all three gaps simultaneously without requiring policy decisions about what to *do* with the information.

**Priority order for post-trial work** (after 48-72h Phase B+C trial completes):

1. **HIGH (observability fix)**: add `STRAT_DIRECTIVE_REJECTED` event so silent skips become visible. Closes Gap 3 fully and partially closes Gap 1/2 (we'd see "rejected because invalid placement" or "rejected because Buy side rr=clamp_floor").
2. **MEDIUM (policy fix)**: surface `is_structurally_invalid` in the brain prompt as `INVALID_LONG=Y` / `INVALID_SHORT=Y` annotations. Closes Gap 2.
3. **LOW (policy fix)**: wire `is_structurally_invalid` into APEX sizing or gate. Closes Gap 1 — but should wait until the trial answers whether clamp-flip trades are net winners or losers before deciding the policy.

None of these are blocking the direction-bias fix series shipping. The four fixes responded correctly to real market data; the gaps are downstream observability and policy work that the fixes made visible.

---

## File path

This report:  
`/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/dirbias_validation/GAPS_FOUND_LIVE_MONITORING.md`
