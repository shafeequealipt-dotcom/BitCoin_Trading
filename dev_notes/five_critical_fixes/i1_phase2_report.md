# Issue 1 Phase 2 Report — Silent Direction Flips Investigation

# 1. Executive Summary

The audit reports characterized Issue 1 as "silent direction flips with no audit trail." The investigation confirms that direction flips between brain decision and Bybit order are common (6 of 11 trades today, 55 %), but **none of them are silent at the code level**. Every flip emits a structured log line. The audit was searching for `APEX_FLIP*` tags; the actual flip mechanism uses a different tag (`XRAY_DIR_FLIP`) at `strategy_worker.py:1738` (WARNING).

Two operational defects underlie the operator's experience:

1. **Naming inconsistency.** `XRAY_DIR_FLIP` is outside the `APEX_FLIP` family, so grep-based audits miss it.
2. **Contract violation.** APEX emits `APEX_DIR_LOCK` to signal "do not flip this symbol in this regime." The lock is enforced inside APEX but does not propagate to `strategy_worker._execute_claude_trade`, where the XRAY flip block can flip a locked direction with no awareness of the lock. Two of six XRAY flips today (SEIUSDT, ONDOUSDT) overrode an explicit APEX_DIR_LOCK.

Both defects are observability and contract issues, not silent-mutation bugs.

# 2. What The Reports Said vs What Current Code Shows

## 2.1 Report Claim

> "6 of approximately 11 Claude directives today (~55%) were silently inverted between brain output and order placement. The silent path emits no flip log at all."

## 2.2 Verified in Current Code

The flip mechanism is observable. Six of six "silent flip" symbols today (SEIUSDT, PYTHUSDT, NEARUSDT, CRVUSDT, GMTUSDT, ONDOUSDT) each have an `XRAY_DIR_FLIP` log entry in `data/logs/workers.log` emitted by `strategy_worker._execute_claude_trade` line 1738.

Example (GMTUSDT, the tightest window):

```
10:19:55.105 brain.log    STRAT_DIRECTIVE  | sym=GMTUSDT dir=Buy
10:20:26.420 workers.log  APEX_OK           | sym=GMTUSDT dir=Buy ...
10:20:26.649 workers.log  XRAY_DIR_FLIP     | sym=GMTUSDT original_dir=Buy flipped_dir=Sell ratio=4.6x
10:20:26.864 workers.log  BYBIT_DEMO_ORD_SEND | sym=GMTUSDT side=Sell
```

229 milliseconds between APEX_OK and XRAY_DIR_FLIP. The flip is logged.

## 2.3 Drift From Reports

| Report-cited file:line | Current location |
|------------------------|------------------|
| `transformer.py:1228` (`_OrderProxy.place_order`) | `transformer.py:1270` |
| `bybit_demo_adapter.py:831-1056` (`place_order`) | `bybit_demo_adapter.py:829` |
| `APEX_OK` emission `optimizer.py:767` | `optimizer.py:766` |
| `WD_CLOSE` emission `position_watchdog.py:3149` | `position_watchdog.py:3148` |

No structural drift. The reports' diagnoses map cleanly to current code.

# 3. Evidence — Per-Trade Cross-Lifecycle Table

11 ORD_SEND trades today (2026-05-11). Every flip has at least one log event in the chain.

| sym | did | STRAT_DIRECTIVE | APEX_DIR_LOCK | APEX_OK | XRAY_DIR_FLIP | ORD_SEND | Classification |
|-----|-----|-----------------|---------------|---------|----------------|----------|----------------|
| AXSUSDT | …92032551 | Sell | — | Sell | — | Sell | clean |
| OPUSDT | …92032551 | Sell | — | Sell | — | Sell | clean |
| ATOMUSDT | …92438281 | Buy | — | Sell (APEX_FLIP) | — | Sell | legit APEX flip |
| **SEIUSDT** | **…92438281** | **Buy** | **Buy (volatile)** | **Buy** | **Sell ratio=45.9x** | **Sell** | **XRAY override of APEX_DIR_LOCK** |
| PYTHUSDT | …93028286 | Sell | — | Sell | Buy ratio=45.8x | Buy | XRAY flip |
| NEARUSDT | …93527139 | Buy | — | Buy | Sell ratio=53.0x | Sell | XRAY flip |
| APTUSDT | …93527139 | Buy | Buy (volatile) | Buy | — | Buy | clean (lock held, XRAY did not fire) |
| CRVUSDT | …93527139 | Buy | — | Buy | Sell ratio=108.3x | Sell | XRAY flip |
| XRPUSDT | …94073038 | Sell | — | Sell | — | Sell | clean |
| GMTUSDT | …94613425 | Buy | — | Buy | Sell ratio=4.6x | Sell | XRAY flip |
| **ONDOUSDT** | **…94613425** | **Buy** | **Buy (volatile)** | **Buy** | **Sell ratio=19.4x** | **Sell** | **XRAY override of APEX_DIR_LOCK** |

Of 11 trades:
- 4 clean (no direction change)
- 1 legitimate APEX flip (ATOMUSDT, observed via APEX_FLIP)
- 4 XRAY flips with no APEX lock to violate (PYTHUSDT, NEARUSDT, CRVUSDT, GMTUSDT)
- 2 XRAY flips overriding APEX_DIR_LOCK (SEIUSDT, ONDOUSDT)

# 4. Where The Flip Happens (Root Cause)

## 4.1 The Single Mutation Site

`src/workers/strategy_worker.py:1718` (inside the XRAY direction-flip block 1604-1748):

```python
trade["direction"] = _flipped_dir
trade["stop_loss_price"] = _new_sl
trade["take_profit_price"] = _new_tp
trade["_apex_was_flipped"] = True
trade["_flip_source"] = "xray"
trade["_xray_flip_ratio"] = round(_ratio, 2)
...
log.warning(
    f"XRAY_DIR_FLIP | sym={symbol} "
    f"original_dir={_orig_dir} "
    f"flipped_dir={_flipped_dir} "
    f"rr_original={_orig_rr:.1f} "
    f"rr_flipped={_new_rr:.1f} "
    f"ratio={_ratio:.1f}x ..."
)
```

This is the **only** production-path direction mutation outside of APEX itself. The TradeGate (`apex/gate.py:48`) runs 14 checks, none of which touch `direction`. The Transformer order proxy (`transformer.py:1270`) and the Bybit-demo adapter (`bybit_demo_adapter.py:829`) both pass `side` through unchanged.

## 4.2 Trigger Condition

```python
_ratio = rr_opposite / rr_chosen
_flip_threshold = settings.risk.xray_dir_flip_threshold_ratio  # default 3.0
if _ratio > _flip_threshold:
    # guards (dual SL/TP levels, no post-flip structural conflict)
    # apply flip
```

The `_ratio` measures structural risk-reward — XRAY's view of how much better the opposite direction's structural placement is. Today's ratios spanned 4.6x to 108.3x, all far above the 3.0 threshold.

## 4.3 The Block's Intent (Per Inline Comment)

The block's docstring at line 1604 dates it 2026-05-05 and labels it "Phase 1 of dir-block-fix." It replaced an earlier `block-at-ratio>5 / size-reduce-at-ratio>3` behavior with the current flip-instead-of-block design. The intent: "when XRAY structurally proves the opposite is much better, flip rather than waste the entry."

This is intentional behavior, not a hidden mutation.

# 5. The Two Operational Defects

## 5.1 Defect A — Audit-grep Blind Spot

`XRAY_DIR_FLIP` does not begin with `APEX_`. Audit scripts (or operators eyeballing `APEX_FLIP*`) miss it. The audit's "no flip log at all" finding actually means "no flip log matching my search pattern."

## 5.2 Defect B — APEX_DIR_LOCK Contract Violation

APEX uses `APEX_DIR_LOCK` to signal that the regime + direction combination forbids a flip. Inside APEX (`optimizer.py:319`), the lock is enforced — any DeepSeek-attempted flip on a locked direction is hard-reverted with `APEX_DIR_LOCK_OVERRIDE`. But the lock state does not propagate to `_execute_claude_trade`. The XRAY block has no awareness of the lock and can flip anyway.

Today this happened twice (SEIUSDT 09:44, ONDOUSDT 10:20). Both were APEX-locked in volatile regime, both were XRAY-flipped, both placed orders in the opposite of the lock direction.

## 5.3 P&L Correlation (For Context, Not Decisive)

| Flip type | Count | Wins | Losses | Net $ |
|-----------|-------|------|--------|-------|
| APEX legitimate flip | 1 | 0 | 1 | -$39.86 (ATOMUSDT) |
| XRAY flip with no APEX lock | 4 | 2 | 2 | +$13.34 |
| XRAY flip overriding APEX lock | 2 | 0 | 2 | -$6.66 |

XRAY flips overall: 2 wins ($20.39), 4 losses ($-13.72), net +$6.67. One win (GMTUSDT TP +$19.49) dominates. Lock-overrides: 0 wins, 2 losses. Small sample size; do not over-fit.

# 6. Solution Options

## 6.1 Option A — Observability-Only (rename + summary event)

**Changes:**

- Rename the `XRAY_DIR_FLIP` log tag to `APEX_FLIP_RR` so it joins the `APEX_FLIP` family.
- Add a `DIRECTION_DECISION` summary event at the end of `_execute_claude_trade`, emitted for every trade, containing `brain_dir`, `apex_dir`, `xray_dir`, `final_dir`, `reason`. One grep covers all paths.

**Pros:** Zero behavior change. Preserves aggressive-exploitation aim. Fixes audit grepability. Single-grep target for direction outcomes.

**Cons:** Does not fix Defect B. SEIUSDT / ONDOUSDT lock-override cases would still proceed as today.

## 6.2 Option B — Respect APEX_DIR_LOCK at strategy_worker boundary (Recommended)

**Changes:**

- Plumb `_apex_locked` (bool) and `_apex_lock_reason` (string) through the `trade` dict; APEX sets them whenever `direction_locked` fires.
- In `strategy_worker.py:1604-1748`, before the `_ratio > threshold` check, test `trade.get("_apex_locked")`. If set, skip the flip, emit `XRAY_FLIP_SUPPRESSED_BY_LOCK | sym=… ratio=X.Yx lock_reason='…' final_dir=<original>` at WARNING.
- Plus Option A's `DIRECTION_DECISION` summary.

**Pros:** Restores APEX's contract end-to-end. Fixes both defects. Preserves aggressive-exploitation aim outside volatile-regime locks. Small surface area, easy revert.

**Cons:** Today's 2 lock-override cases (SEIUSDT, ONDOUSDT) would have stayed Buy; counterfactual P&L unknown. Slight reduction in XRAY flip frequency in volatile regimes.

## 6.3 Option C — Conviction-gated XRAY flip

**Changes:**

- Include APEX confidence (`_apex_confidence`) in the XRAY decision. Only flip when `apex_conf < flip_conviction_threshold` (operator-tunable, e.g. 0.65).
- Above threshold, treat the direction as trusted and emit `XRAY_FLIP_SUPPRESSED_BY_CONVICTION`.
- Plus Option A's summary.

**Pros:** Broader gating; respects both lock and high-conviction non-locked decisions.

**Cons:** Requires choosing a threshold. More moving parts. May under-cut aggressive philosophy. Today, all 6 XRAY flips would need their `_apex_confidence` checked to know what changes.

## 6.4 Option D — Eliminate XRAY flip path entirely

**Changes:** Remove the XRAY flip block (1604-1748); restore the older `block-at-ratio>5 / size-reduce-at-ratio>3` regime.

**Pros:** Maximum APEX purity.

**Cons:** Major behavior change. Today, would have killed the GMTUSDT TP win (+$19.49). Conflicts with the 2026-05-05 design and the aggressive-exploitation aim. Not recommended.

# 7. Recommendation

**Option B (respect APEX_DIR_LOCK) plus Option A's `DIRECTION_DECISION` summary event.**

Reasoning:

- Fixes the actual contract bug (APEX_DIR_LOCK promised, XRAY broke it). Two of six XRAY flips today violated this contract.
- Fixes the audit-grep blind spot via the new `DIRECTION_DECISION` summary event.
- Preserves aggressive exploitation in 4 of 6 today's XRAY flip cases (no APEX lock to violate).
- Minimal blast radius: two changes (one in optimizer to plumb the flag, one in strategy_worker to honor it) plus one summary log. Easy to revert.
- Does not require tuning a new confidence threshold.

If the operator's actual goal is "XRAY should never flip" or "XRAY should always be gated on confidence," Option C or D are available — but the current evidence does not justify going that far.

# 8. Open Questions For The Operator

1. **Tag naming.** Prefer `APEX_FLIP_RR` (joins the family) or keep `XRAY_DIR_FLIP` and add `DIRECTION_DECISION` as the umbrella tag for audits? Either works; the question is which feels more discoverable.
2. **Locked-flip extreme cases.** When XRAY ratio is 50x+ but APEX has locked, should the flip be suppressed entirely (Option B's default), or logged loudly and allowed (a relaxed variant)?
3. **Counterfactual concern.** Want me to backtest "what if SEIUSDT and ONDOUSDT had stayed Buy" against the actual market data before committing?
4. **Telegram / Loguru alerts.** Should `XRAY_FLIP_SUPPRESSED_BY_LOCK` (a contract-restoration event) fire an operator-visible alert, or stay log-only?

# 9. Phase 3 Implementation Plan (Conditional On Operator Approval)

If Option B is chosen, the implementation has these atomic commits:

1. `feat(i1/phase3a)`: `src/apex/optimizer.py` — stamp `_apex_locked=True` and `_apex_lock_reason=<reason>` onto the directive whenever `_check_direction_lock` returns locked (around line 218). No behavior change inside APEX.
2. `feat(i1/phase3b)`: `src/workers/strategy_worker.py` — at line 1631, before the `_ratio > _flip_threshold` check, test `trade.get("_apex_locked")`. If set, emit `XRAY_FLIP_SUPPRESSED_BY_LOCK` and skip the flip. Order placement proceeds with the locked direction.
3. `feat(i1/phase3c)`: `src/workers/strategy_worker.py` — add a `DIRECTION_DECISION` summary log emitted just before order placement (before TradeGate.validate), unifying the brain → APEX → XRAY → final-direction outcome with a `reason` field.
4. `test(i1/phase3)`: unit tests for the lock-respect logic (3 scenarios: locked+ratio-over-threshold → suppress; not locked + ratio-over → flip; locked + ratio-under → no-op).

Verification (Phase 4):

- Deploy. Run for 4-6 hours capturing logs.
- Confirm for 10+ trades: brain direction == ORD_SEND side OR there is a logged flip (APEX_FLIP / XRAY_DIR_FLIP / DIRECTION_DECISION reason=xray_flip).
- Confirm no `XRAY_DIR_FLIP` occurs when `_apex_locked` is set (these become `XRAY_FLIP_SUPPRESSED_BY_LOCK` instead).
- Verify Shadow path unaffected (Rule 10).

# 10. Stop For Operator Decision

The investigation is complete. Awaiting operator's choice of option (A / B / C / D / variant), confirmation of open-question answers, and approval before Phase 3 implementation begins.
