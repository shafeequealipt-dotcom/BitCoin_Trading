# Phase 1.9 — Synthesis of Validation Findings

Spec lines 502-510. Synthesizes Steps 1.1-1.8.

## Headline

The prior report's four CRITICAL issue diagnoses are **substantively accurate at the code level**. All cited operative surfaces verified at the cited file:line (with minor off-by-one corrections noted). The funnel data, smoking gun, market-reality, and historical claims all hold up to independent regeneration.

However, **new evidence emerged that materially refines the prior report's framing**:

1. **Issue 1 (XRAY flips) account for AT MOST 12% of direction skew** in the audit window (11 flips / 91 brain directives). The other 88% is upstream.
2. **Final order direction (89.3% Sell) is nearly proportional to regime distribution (89.9% expected)**. The system is approximately tracking market reality.
3. **Over 14 days, BOTH directions have WR below 50%** (Buy 41.8%, Sell 42.4%). The 7-day "Sell is profitable" narrative is a cherry-picked window. System is essentially break-even.
4. **The prompt amplification (Issue 4) at the brain level is only ~2-3 pp above regime-proportional** — small absolute effect even though the asymmetric coding is real.

## Accurate claims (verified)

### Issue 1 — XRAY rr_long collapse

- `structural_levels.py:67-145` (`_calc_long`) and `:147-212` (`_calc_short`) have no minimum-edge floor — CONFIRMED.
- `support_resistance.py:122-126` asymmetric `min_touches` (config-2 for support, hardcoded `>=1` for resistance) — CONFIRMED. Origin: commit `c3e5380` (2026-04-13, untouched for 36 days).
- RR formula at `structural_levels.py:110-112` uses `abs()` which masks wrong-side TP — CONFIRMED.
- `strategy_worker.py:1727-1739` flip ratio computation — CONFIRMED. Actual decision boundary at line 1860, mutation at lines 1923-1977.
- All four root causes (RC-1.1 through RC-1.4) accurately described.
- 80.7% of XRAY_ANALYZE rows in window have `sup=0 res=5` — confirms universal effect of the asymmetric filter.
- 8 of 11 XRAY_DIR_FLIP events are collapse-driven (chosen-side RR ≤ 0.3).

### Issue 2 — Counter ×0.7 multiplier

- Producer sites at `structure_engine.py:1071, 1188, 1210` — CONFIRMED.
- All 9 downstream consumers confirmed at cited file:line (one off-by-six on consumer 1's upper bound, immaterial).
- Producer assignment at `structure_engine.py:562` — CONFIRMED.
- Origin: commit `3a59637` (2026-04-30), no tuning since — CONFIRMED.
- R1 fix at `assembler.py:758-769` propagates `trade_direction` only, never touches `setup_type_confidence` — CONFIRMED (zero grep hits).
- Compounding math: theoretical 3.92×, live mean 1.6×, live modal 9.3× — brackets the report's "3-5× / 4-6×" estimate.
- Concern 7 config-only test is technically feasible: validator at `settings.py:2503-2507` allows `1.0` (interval `(0, 1]`).

### Issue 3 — Labeller per-trigger gates

- `mode = "briefing"` confirmed at `config.toml:650` and `settings.py:1290`. Production mode.
- `_qualifies` has exactly ONE call site at `scanner_worker.py:1604` inside the exclusion-mode body. Unreachable in briefing mode.
- All 8 per-trigger predicates in `state_labeler.py` confirmed at lines 253, 268, 283, 302 (off-by-one — actually 301, immaterial), 356, 371, 477, 491.
- 716:148 SHORT:LONG label ratio (4.84×) confirmed exactly.
- Briefing-mode aggregate hardcodes `fail_regime=0` (line 1310, 1328) — creates observability gap.
- `_get_regime_alignment` (composite soft signal) is direction-AGNOSTIC, confirming the asymmetry source is labeller hard-kills, not the soft signal.

### Issue 4 — Asymmetric MARKET REGIME prompt block

- Live asymmetric block at `strategist.py:3371-3390` — CONFIRMED byte-for-byte.
- Dead duplicate at `strategist.py:1416-1435` — CONFIRMED byte-for-byte identical.
- Dead method `_build_regime_instructions` at `strategist.py:4155-4251` — CONFIRMED.
- Caller-chain proof: only production caller is `_build_context_prompt:1084` → `create_strategic_plan:683` → only invoked by `scripts/run_30min_test.py:76, 106` (test harness).
- Production CALL_A path: `layer_manager._run_brain_cycle:770` → `create_trade_plan` → `_build_trade_prompt` (which contains the 3371-3390 live block).
- `STRAT_AGGRESSIVE_FRAMING regime_instr=minimal` boot sentinel at line 870 — emitted 37 times in 2026-05-18 brain.log. Technically accurate (dead helper not called) but functionally misleading (asymmetric block IS emitted).

## Discrepancies and corrections

| Item | Prior report | Actual | Materiality |
|---|---|---|---|
| `bearish_fvg_ob` count | 4,124 | 2,062 | Low — in-direction:counter ratio (5.9:1) unchanged |
| APEX_LOCK_OVERRIDE_GRANTED Sell→Buy | 22 | 25 | Low — direction pattern unchanged |
| state_labeler line 302 | 302 | 301 | Negligible — off-by-one |
| STRAT_AGGRESSIVE_FRAMING count | 36 | 37 | Negligible |
| Regime emission count | 1,882 trending_down | 1,567 (per-coin) or 1,789 (broader filter) | Same direction, different filter scope. The 8.9× ratio holds. |
| Spec line 433 path | `src/labellers/state_labeler.py` | `src/workers/scanner/state_labeler.py` | SPEC typo, not code. Must be raised at Phase 4 gate. |
| Issue 1 fix-block citation | `strategy_worker.py:1727-1739` | Ratio computation at 1727-1739 is correct; **decision boundary** is at line 1860, mutation at 1923-1977 | Medium — the proposed Option 1.B floor guard must be added at line 1860 or later, not at 1727-1739 |

## New findings (not in prior report)

### NF-1 — Issue 1 flip cascade is small (12% of direction skew)

Issue 1 (XRAY_DIR_FLIP events) fired 11 times in 5.5 hours, against 91 brain directives. Even if every flip were eliminated, the brain decision direction distribution would shift by at most 12%. The other 88% of the bias is upstream (Issues 2-4).

**Implication**: Path C (ship Issue 4 first, measure) is empirically supported. Issue 1's symptomatic fix (Phase 1.B) is small-impact at the brain-output level.

### NF-2 — Order direction is regime-proportional (89.3% Sell ≈ 89.9% expected)

Computed expected Sell share if direction tracked regime perfectly: `1567 / (1567 + 176) = 89.9%`. Observed at final orders: 89.3%. Almost exact match.

The brain's 92.3% Sell is ~2-3 pp above proportional. The override layer (25 Sell→Buy flips) pulls it back down to ~89.3%.

**Implication**: The system as a whole is NOT mispricing direction relative to market. Issue 4 fix would primarily reduce variance and align with operator directive — expected PnL impact is modest.

### NF-3 — 14-day WR shows both directions below break-even

The 7-day window's Sell WR 52.8% is anomalously good. Over 14 days:
- Buy WR: 41.8% (122 trades)
- Sell WR: 42.4% (681 trades)

Both below 50%. The system is approximately break-even at scale ($472.67 PnL / 803 trades = $0.59 per trade).

**Implication**: Concern 8 (bias might be correct) is WEAKENED. The system isn't profitably riding a bearish market — it's making low-conviction trades on both sides with high volume on Sells coincidentally winning more days. Fixing the asymmetry won't necessarily worsen PnL.

### NF-4 — Additional Issue 2 propagation sites (3 new)

Issue 2 agent found three sites the prior report missed:
- `scanner_worker.py:623` (XrayBlock emit)
- `scanner_worker.py:771` (label_state call)
- `scanner_worker.py:835` (compute_interestingness call)
- `strategy_worker.py:2581-2583` (entry anchor capture)
- `scanner/interestingness.py:_cleanness()` at 138/153 (soft consumer)

These are propagation, not new compounding stages. The 5-stage cascade model holds.

### NF-5 — Issue 3 alternative cheap fix

Issue 3 agent found that `_trigger_counter_trade_long/short` (lines 379-396) also lack regime predicates — another escape hatch. Fired 36+10 times as secondary in audit window.

**Alternative cheap fix**: boost `LABEL_BASE_WEIGHTS[COUNTER_TRADE_*]` from 0.45 to ~0.65 (one-line edit). Could be tested before the full Option 3.1 labeller refactor.

### NF-6 — Issue 4 trim-marker lock-step

Header text `## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)` appears at 3 sites in `strategist.py` (line 398 marker, 1416 dead, 3371 live) and 8 lines across 2 test files. Any rename must update all 11 sites in lock-step or trim logic will drop the section.

### NF-7 — TRADE_SYSTEM_PROMPT_ZERO_TWO is the live system prompt

Every CALL_A emission has `zero_two_flag=True`. The legacy TRADE_SYSTEM_PROMPT (lines 66-142) is behaviourally dead. Issue 4 edits should target the ZERO_TWO version + dead duplicate at 1416-1435 for hygiene.

### NF-8 — Second asymmetry in `structure_engine.py:236-256`

The `position_in_range` fallback formulas in `sup=0 res=5` universe push `position → 1.0`, which then maps to `entry_quality=poor` for longs — amplifying Buy-side degradation independently of the RR collapse. The prior report did not flag this.

### NF-9 — Briefing-mode observability gap

`fail_regime=0` hardcoded in briefing-mode aggregate logs creates a blind spot. A `label_regime_extinguished` counter would surface how many labels are killed by per-trigger regime predicates.

### NF-10 — CALL_B is symmetric, needs no fix

The `_build_position_prompt` (CALL_B, line 3825+) is direction-symmetric. Issue 4 fix is CALL_A-only.

## Operative code surfaces — corrected and complete

| Issue | Operative surface | Status |
|---|---|---|
| 1 — XRAY rr_long collapse | `structural_levels.py:67-212` + `support_resistance.py:122-126` (hardcoded `>=1`) + `strategy_worker.py:1860, 1923-1977` (decision/mutation, not 1727-1739 alone) + `structure_engine.py:236-256` (second asymmetry) | Verified, with line-range correction for the decision boundary |
| 2 — Counter ×0.7 | `structure_engine.py:1071, 1188, 1210` + 9 confirmed consumers + 3 propagation sites + 1 soft consumer | Verified, with 3 additional propagation sites identified |
| 3 — Labeller per-trigger gates | `state_labeler.py:253, 268, 283, 301 (was 302), 356, 371, 477, 491` | Verified, with off-by-one correction and counter_trade/liquidity_sweep/momentum_burst escape hatches documented |
| 4 — Asymmetric MARKET REGIME block | `strategist.py:3371-3390` live + `:1416-1435` dead duplicate + `:870` boot sentinel + `:398` trim marker | Verified, 11 lock-step edit sites identified |

## Verdict per claim

All four issue diagnoses ACCURATE at the code level. The fix-path discussion in Phase 2 must account for the new empirical findings — especially the 12% Issue-1 ceiling, the regime-proportional final-order claim, and the 14-day break-even WR result.

## Implications for Phase 2

Phase 2 evaluates the 8 concerns from spec Part A.4. Key concerns most affected by Phase 1 findings:

- **Concern 5 (Path C — ship Issue 4 first, measure)**: STRONGLY SUPPORTED by Phase 1 findings. Issue 1 has 12% ceiling. Issue 4 is small-surface, low-risk.
- **Concern 7 (×0.7 should be REMOVED)**: SUPPORTED by Phase 1.2 — config-only test is feasible (validator allows 1.0). Low-cost diagnostic that costs nothing but a service restart.
- **Concern 8 (bias might be correct)**: PARTIALLY SUPPORTED at orders level (regime-proportional) but WEAKENED by 14-day WR (both directions below 50%).
- **Concern 4 (Phase C defaults are no-op)**: Phase 1.1 finding (80.7% sup=0/res=5) suggests non-zero defaults would catch most cases — argues against shipping Phase C inactive.

Phase 2 should give specific evidence-grounded verdicts to each concern. Proceed.
