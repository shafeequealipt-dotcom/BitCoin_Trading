# Issue 1 Phase 1 — Synthesis

## WHERE does the direction flip happen?

`src/workers/strategy_worker.py:1604-1748` — the XRAY direction-flip block.

Specifically:
- Lines 1718-1736 are the mutation site (`trade["direction"] = _flipped_dir`, SL/TP swap, metadata flags).
- Line 1738 emits `XRAY_DIR_FLIP` at WARNING level.
- The block runs ~after APEX returns (APEX_OK already logged), and ~before TradeGate.validate (which doesn't touch direction).

**No other production path mutates `trade["direction"]` between brain and Bybit.** APEX has its own legitimate flip emitting `APEX_FLIP`. TradeGate reads direction for branching but never writes. The order proxy and Bybit-demo adapter both pass-through.

## WHY does it flip?

Trigger condition at line 1631:

```python
_ratio = rr_opposite / rr_chosen
if _ratio > xray_dir_flip_threshold_ratio (default 3.0):
    flip direction
```

`_ratio` is computed from `structural_placement.rr_long` and `rr_short` — structural risk-reward measured against XRAY's structural-target placement, not realized P&L.

Today's ratios: 4.6x (GMTUSDT), 19.4x (ONDOUSDT), 45.8x (PYTHUSDT), 45.9x (SEIUSDT), 53.0x (NEARUSDT), 108.3x (CRVUSDT). All far exceed the 3.0 default threshold.

Guards before the flip:
1. `_has_dual_levels` — all 4 structural SL/TP fields populated for both directions.
2. `_new_conflict` — flipped direction does not create a structural conflict with `setup_quality in ("SKIP", "C")`.

If both guards pass, the flip applies. Today **6/6 XRAY_DIR_FLIP attempts passed the guards and flipped**.

## Is this a bug or intentional behavior?

**Intentional behavior with two operational defects.**

The block's docstring (line 1604) explicitly dates it 2026-05-05 and labels it "Phase 1 of dir-block-fix" — it replaced an earlier `block-at-ratio>5 / size-reduce-at-ratio>3` regime with the current flip-instead-of-block design. The intent was: when XRAY structurally proves the opposite direction is much better, flip rather than waste the entry.

The defects are not in the flip logic — they are in operational visibility and contract:

### Defect A — Audit-grep blind spot

The log line uses tag `XRAY_DIR_FLIP`. The APEX family uses `APEX_FLIP`, `APEX_FLIP_RR`, `APEX_FLIP_RESIZE_ACCEPTED`, etc. Operators and audit scripts grep-ing for `APEX_FLIP*` miss every XRAY flip. The audit-report claim "no flip log at all" actually means "no flip log matching my search pattern". This is a naming/observability defect.

### Defect B — APEX_DIR_LOCK contract violation

APEX explicitly emits `APEX_DIR_LOCK` when the regime + claude direction combination forbids a flip (e.g., volatile regime without TIAS evidence). Inside APEX, the lock is enforced — `APEX_DIR_LOCK_OVERRIDE` at line 319 reverts any DeepSeek-attempted flip back to claude's original direction.

But the lock state does not propagate to `strategy_worker._execute_claude_trade`. The XRAY block at line 1604 has no awareness of `APEX_DIR_LOCK`. Today, 2 of 6 XRAY flips (SEIUSDT, ONDOUSDT) flipped a direction that APEX had explicitly locked. This is a contract violation: APEX promises the operator "lock holds"; XRAY breaks that promise downstream.

## P&L correlation

| Flip type | Count today | Wins | Losses | Net $ |
|-----------|-------------|------|--------|-------|
| APEX legitimate flip | 1 | 0 | 1 | -$39.86 (ATOMUSDT) |
| XRAY flip (no APEX lock) | 4 | 2 | 2 | +$13.34 (GMT +19.49, PYTH +0.90, NEAR -5.20, CRV -1.86) |
| XRAY flip overriding APEX lock | 2 | 0 | 2 | -$6.66 (SEI -6.07, ONDO -0.59) |

XRAY flips overall: 4 losses + 2 wins; net **+$6.67** dominated by one big win (GMTUSDT). XRAY flips that overrode APEX_DIR_LOCK: 2 losses, 0 wins (small N, but worth noting).

This is *not* a clear-cut "XRAY flips lose money" story. Excluding the GMT outlier, XRAY-flipped trades net -$12.82 across 5 trades — small losses, small frequency. The strategy survives on the occasional big winner. Operator must decide whether the contract-violation cases (SEIUSDT, ONDOUSDT) should be allowed at all, separately from whether the XRAY flip mechanism as a whole is desirable.

## Three candidate fix directions

### Option A — Observability-only (rename + summary event)

**Changes:**
1. Rename `XRAY_DIR_FLIP` log tag to `APEX_FLIP_RR` (matches operator's mental model of the `APEX_FLIP*` family; "RR" = R:R-driven).
2. Add a new `DIRECTION_DECISION | sym=… brain_dir=Buy apex_dir=Buy xray_dir=Sell final=Sell reason=xray_rr_flip ratio=45.9x` summary event emitted at the end of `_execute_claude_trade`, before the order send. Covers all paths (no-flip, APEX-flip, XRAY-flip, lock-override).
3. Update audit scripts (if any committed) to scan the new tag.

**Trade-offs:** Zero behavior change, fixes audit blind spot, gives operator a single grep target for direction decisions. Does NOT fix APEX_DIR_LOCK contract violation — SEIUSDT/ONDOUSDT would still XRAY-flip.

### Option B — Respect APEX_DIR_LOCK at strategy_worker boundary (Recommended)

**Changes:**
1. Plumb `_apex_locked` and `_apex_lock_reason` flags through `trade` dict (APEX sets them when `direction_locked` fires).
2. In the XRAY flip block (`strategy_worker.py:1604-1748`), before the ratio check, if `trade.get("_apex_locked")`:
   - Skip the flip.
   - Emit `XRAY_FLIP_SUPPRESSED_BY_LOCK | sym=… ratio=X.Yx lock_reason='…' final_dir=<original>` at WARNING level.
   - Continue with original direction.
3. Plus Option A's `DIRECTION_DECISION` summary event for end-to-end visibility.

**Trade-offs:** Honors APEX's contract; restores "lock means locked" guarantee. Operator-facing aim (aggressive exploitation) preserved outside volatile-regime locks. Today: SEIUSDT/ONDOUSDT would have stayed Buy; outcomes unknown (counterfactual). The remaining 4 non-locked XRAY flips proceed unchanged.

### Option C — Conviction-gated XRAY flip (more invasive)

**Changes:**
1. Include APEX confidence (`_apex_confidence`) in the XRAY flip decision: only flip when `apex_conf < flip_conviction_threshold` (operator-tunable, default e.g. 0.65).
2. Above threshold, treat the direction as trusted and skip the XRAY flip with a `XRAY_FLIP_SUPPRESSED_BY_CONVICTION` log.
3. Plus Option A's `DIRECTION_DECISION` summary.

**Trade-offs:** Reduces XRAY flip frequency overall, not just for locked directions. Requires choosing a threshold and tuning. More moving parts. Could under-cut aggressive philosophy.

### Option D — Eliminate XRAY flip path entirely (most invasive — not recommended)

**Changes:** Remove the XRAY flip block (1604-1748); replace with the legacy `block-at-ratio>5 / size-reduce-at-ratio>3` behavior. Restores "only APEX can flip" purity. Reverts the 2026-05-05 design.

**Trade-offs:** Major behavior change; eliminates today's GMTUSDT win path (which earned +$19.49). Conflicts with the operator's stated aim. Not recommended.

## Recommendation

**Option B + Option A's summary event.** This is the smallest-blast-radius change that:
- Honors APEX's `APEX_DIR_LOCK` contract (fixes the actual bug)
- Provides operator-facing audit hygiene (fixes the perceived bug)
- Preserves aggressive-exploitation philosophy for non-locked symbols (Rule 8)
- Does not require tuning a new conviction threshold
- Reverts cleanly if not working out (1 commit per concern)

If operator's true intent is "XRAY should never flip", Option D is on the table — but the data does not yet justify killing the flip mechanism entirely.

## Open questions for the operator (deferred to Phase 2 discussion)

1. **Naming preference.** `APEX_FLIP_RR` vs preserving `XRAY_DIR_FLIP` plus a `DIRECTION_DECISION` umbrella tag — which is more operationally useful?
2. **Locked-flip semantics.** When XRAY ratio is overwhelming (e.g., 100x) but APEX has locked the direction, should XRAY be allowed to flip with extra logging, or never? (Option B assumes never; a relaxed variant is "log loudly and continue".)
3. **Visibility threshold.** Are there other operator-facing dashboards or alerts (Telegram, Loguru sinks) that should fire on every flip — beyond log entries?
4. **Counterfactual concern.** Operator may want to backtest "what if these 2 lock-overrides had stayed Buy" before committing to Option B. The data is in `trade_history` already.

## Phase 2 deliverable

Write `i1_phase2_report.md` consolidating Phase 0 + Phase 1 evidence into operator-facing form (h1/h2/h3, no emoji, deterministic). Present the three options. Stop for operator decision before Phase 3 implementation.
