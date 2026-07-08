# Phase 2.9 — Synthesis of Concern Evaluations

## Verdicts at a glance

| # | Concern | Verdict | Implication |
|---|---|---|---|
| 1 | Issue 1 Phase A2 is a band-aid | PARTIALLY VALID | Drop Phase A2 OR ship with explicit deprecation date OR keep only the `chosen_rr ≥ 0.5` math-floor (not the asymmetric `flipped_rr ≥ 2.0`) |
| 2 | Issue 2 Option A violates directive | VALID | REJECT Option A entirely |
| 3 | Issue 2 Option B preserves suppression | PARTIALLY VALID | Option B is architectural improvement but doesn't fully resolve directive. Prefer Option E (remove) first; fall back to B if removal degrades WR |
| 4 | Phase C defaults are no-op | VALID | If Phase C ships, use active defaults (`tp_min_distance_pct=0.5`, `min_touches_resistance=2`) |
| 5 | Ship Issue 4 alone first, measure | STRONGLY VALID | Path C is the recommended approach |
| 6 | Phase E verification is hand-wavy | VALID | Each fix needs concrete pre-ship baseline, post-ship thresholds, revert triggers, specific queries |
| 7 | ×0.7 should be removed entirely | VALID | Ship Concern 7 Phase 7-1 (config-only test, no code change) as parallel to Issue 4 fix |
| 8 | Bias may not be a bug | PARTIALLY VALID | Bias is partially regime-proportional; the asymmetric coding is still a directive violation. Fix anyway, don't expect dramatic PnL change |

## Cross-concern findings

### Path A (ship report as-is) is REJECTED

Three concerns invalidate the original 17-23 day all-up plan:
- Concern 2 rejects Option 2.A (which Path A includes).
- Concern 4 rejects no-op Phase C defaults (which Path A includes).
- Concern 1 partially rejects Phase A2 (which Path A includes).

A modified Path B is required IF the operator wants multiple fixes shipped before measurement.

### Path C (Issue 4 first, measure 48h) is RECOMMENDED

Strongly supported by:
- Concern 5: empirical validation preferred (per spec Rule 12).
- Phase 1.1: Issue 1 has 12% ceiling (small effect).
- Phase 1.7: orders are regime-proportional (system isn't badly mispricing).
- Phase 1.8: 14d WR is break-even (no edge to protect).

### Pair Concern 7's config-only test with Issue 4 in Phase A

Both fixes:
- Are LOW risk.
- Honor operator directive.
- Are reversible (Issue 4 by git revert; Concern 7 by TOML revert).
- Can run in same 48h trial window.

Combined effect estimate:
- Issue 4 alone: brain Sell 92.3% → 85-90% (orders 89.3% → 82-87%).
- Concern 7 alone: counter LONG entries increase (more Buy orders).
- Both together: brain Sell 75-85% range; Buy WR holds or improves.

### Issue 3 (labeller soft haircut) is the next-best ship if Phase A is insufficient

If 48h post-Phase-A shows brain Sell still ≥ 90%, Issue 3 ships next. The labeller per-trigger regime hard-kills are the OPERATIVE direction-asymmetric mechanism in production. Soft haircut is directive-aligned and operator-tunable.

### Issue 1 structural fix (Phase 1.A core) is the LAST priority

Per Phase 1.1: Issue 1 has at most 12% ceiling on brain output. The structural fix (`tp_min_distance_pct`, symmetric `min_touches`) is correct in principle but small effect. Should only ship after Issues 4 + 7 + 3 have been tried and the residual bias warrants it.

## Recommended fix path (preview of Phase 3 recommendation)

### Phase A — Combined LOW-risk fixes (~2 days)

Two independent atomic commits, run in parallel 48h trial:

1. **Issue 4 + sentinel fix** on `fix/dirbias-symmetric-regime-prompt`:
   - Edit `strategist.py:3371-3390` (symmetric direction_hint + paired NOTE).
   - Edit `strategist.py:1416-1435` (apply same to dead duplicate for hygiene).
   - Add `STRAT_REGIME_BLOCK_VERSION = 2` constant + `STRAT_REGIME_INSTR_REFRAMED` boot sentinel.
   - Update `STRAT_AGGRESSIVE_FRAMING` sentinel at line 870 to truthfully reflect state.
   - Update `_TRIM_ESSENTIAL_MARKERS` at line 397-398.
   - Update test markers in `tests/test_stage2_phase4/test_priority_*.py` (8 lines).
   - Add `tests/test_regime_block_symmetry.py` (new).
   - Total: ~10 LOC code + 8 test marker edits + 1 new test file.

2. **Issue 2 Concern 7 config-only test** on `fix/dirbias-counter-mult-config-test`:
   - Edit `config.toml:1724`: `counter_confidence_multiplier = 1.0`.
   - Single-line change. No code, no test.

Pre-ship baseline capture per Concern 6. 48h post-ship metrics with concrete thresholds.

### Phase B — Decision gate

After 48h:
- If both Phase A fixes PASS → ratify Concern 7 with code removal (Option 7.2). Run 24h more for cleanup soak. STOP if both directions converge to ≥ 45% WR.
- If brain Sell still ≥ 90% → ship Issue 3 labeller soft haircut next.
- If Buy WR < 35% on either fix → revert both, escalate to operator with data.

### Phase C — Issue 3 (only if needed)

If Phase B says needed: `fix/dirbias-labeller-soft-haircut`. Soft haircut at per-trigger predicates in `state_labeler.py`. Default `counter_regime_confidence_haircut=1.0` (no-op) for soak; operator flips to 0.5 after 24h.

### Phase D — Issue 1 structural fix (only if needed)

If Phase C didn't resolve and Issue 1 evidence still warrants: `fix/dirbias-xray-rr-collapse` with ACTIVE defaults per Concern 4 verdict.

## Anti-patterns to avoid (per spec Rule 4)

The synthesis explicitly rejects:
- Option 2.A regime-concentration multiplier (Concern 2).
- No-op defaults for Phase C (Concern 4).
- "Look at it and decide" verification (Concern 6).
- All-up 17-23 day commitment (Concern 5).
- Renaming fields to preserve suppression — but Option 2.B is OK as a structural improvement IF Option E (removal) is the operator's chosen path and 2.B is the fallback.

## Open questions for operator

To be raised in Phase 3 Master Report:

1. **Phase A bundling**: ship Issue 4 + Concern 7 config test together (2 atomic commits, parallel 48h trial), or sequentially (Issue 4 first, then Concern 7)?
2. **Issue 4 fix wording**: prefer the symmetric "Bias for shorts/longs when per-coin evidence agrees" or the more conservative "GLOBAL CONTEXT (per-coin tags above are PRIMARY)" header?
3. **Concern 1 Phase A2**: drop entirely, ship with deprecation date, or ship `chosen_rr ≥ 0.5` floor only (not `flipped_rr ≥ 2.0`)?
4. **Spec typo `src/labellers/state_labeler.py`**: should I correct the spec file before Phase 4, or leave it documented in the Master Report only?
5. **STRAT_AGGRESSIVE_FRAMING sentinel correction**: bundle with Issue 4 fix, or ship as standalone Phase 0.1 commit (since the false advertising has been misleading logs for weeks)?

## Phase 2 verdict

The 8 concerns are mostly valid with nuance. The recommended fix path is Path C variant — Phase A (Issue 4 + Concern 7) + measurement + sequential follow-ons (Issue 3, then Issue 1) only as data demands.

Proceed to Phase 3 (three paths comparison + final recommendation + Master Report).
