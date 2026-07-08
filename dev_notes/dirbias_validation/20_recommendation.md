# Phase 3.2 — Recommendation

## Recommendation: Path C (smallest viable first, measure, decide)

### Phase A (2-3 days, parallel)

Ship two independent atomic commits in parallel:

**Commit A1: Issue 4 — symmetric MARKET REGIME block + sentinel correction**

Branch: `fix/dirbias-symmetric-regime-prompt`. Off `main` (after current `fix/wd-scoring-brain-vote` merges).

Changes (~80 LOC + 8 test markers + 1 new test):
- `src/brain/strategist.py:3371-3390` — replace asymmetric block with symmetric scenario-driven version (recommended wording per Phase 1.4 finding: `## MARKET REGIME (CONTEXT)` header; symmetric `direction_hint` for trending_down/trending_up; conditional NOTE that fires on BOTH high-confidence regimes with parallel wording).
- `src/brain/strategist.py:1416-1435` — apply same edit to dead duplicate for code hygiene.
- `src/brain/strategist.py:870` — update `STRAT_AGGRESSIVE_FRAMING` sentinel from `regime_instr=minimal` to truthful `regime_instr=symmetric` (or similar).
- `src/brain/strategist.py:595` area — add module constant `STRAT_REGIME_BLOCK_VERSION = 2` and emit `STRAT_REGIME_INSTR_REFRAMED | block_version=2 mode=symmetric_scenario` boot sentinel.
- `src/brain/strategist.py:397-398` — update `_TRIM_ESSENTIAL_MARKERS` to match new header text (lock-step with the 3371 and 1416 edits).
- `tests/test_stage2_phase4/test_priority_trim_inline.py` lines 121, 208, 349, 438, 517 — update header marker strings.
- `tests/test_stage2_phase4/test_priority_classifier.py` lines 46, 373, 383 — update header marker strings.
- `tests/test_regime_block_symmetry.py` (new) — assert symmetric dict + symmetric NOTE on both regimes.

**Commit A2: Issue 2 — Concern 7 config-only test**

Branch: `fix/dirbias-counter-mult-config-test`. Off `main`.

Changes (1 line):
- `config.toml:1724` — `counter_confidence_multiplier = 1.0` (from 0.7).

That is the entire change. No code touched. No tests changed.

### Why this works

The two commits are independent and complementary:
- Commit A1 fixes the brain prompt asymmetry (the ~2-3 pp amplification at brain output).
- Commit A2 fixes the counter setup sizing suppression (the high-conviction counter-LONG sizing problem).

Both honor the operator directive (no hardcoded asymmetric corrections; asymmetry emerges from data and scenario).

Both have observable per-fix sentinels (A1 emits `STRAT_REGIME_INSTR_REFRAMED`; A2 changes the `counter_mult` value seen in `XRAY_CONFIDENCE_DETAIL` logs).

Both are reversible:
- A1 reverts by git revert + service restart.
- A2 reverts by `git checkout config.toml && restart` — under one minute.

### Pre-ship baseline (Phase 0 of Phase A)

Snapshot capture (already in `phase0_baseline.md`):
- M1: STRAT_DIRECTIVE direction count over 24h (current).
- M2: BYBIT_DEMO_ORD_SEND side count over 24h.
- M3: Buy WR / Sell WR over 7 days.
- M4: Trades per hour.
- M5: Session PnL per 24h.
- M6: All shipped fix sentinels firing.

### Post-ship measurement (48h)

Re-measure M1-M6. Decision matrix:

| Outcome | Action |
|---|---|
| Brain Sell drops to ≤80% AND Buy WR ≥ 40% AND M5 ≥ 80% baseline | HOLD. Observe 7 days. STOP if direction WR converge. |
| Brain Sell 80-90% AND Buy WR ≥ 40% AND M5 OK | Ship Issue 3 next (labeller soft haircut). |
| Brain Sell ≥ 90% AND Buy WR ≥ 40% | Issue 4 was inert. Ratify Concern 7 with code removal (Option 7.2). Then ship Issue 3. |
| Brain Sell ≤ 30% (over-correction) | REVERT BOTH. Reassess wording. |
| Buy WR < 35% within 48h | REVERT BOTH. Diagnostic: which fix caused it? |
| M5 < 50% baseline | REVERT BOTH. Investigate. |
| Any sentinel from M6 stops firing | REVERT BOTH. Diagnose regression. |

### Phase B (only if Phase A is insufficient)

If 48h shows brain Sell ≥ 90%: ship Issue 3 (`fix/dirbias-labeller-soft-haircut`). Soft haircut at `state_labeler.py` per-trigger predicates with `counter_regime_confidence_haircut: float = 1.0` default (no-op for soak); operator flips to 0.5 after 24h. Same metric framework.

### Phase C (only if Phase B is insufficient)

If Phase B also doesn't resolve: ship Issue 1 Phase 1.A structural fix (`fix/dirbias-xray-rr-collapse`). With ACTIVE defaults per Concern 4 verdict — `tp_min_distance_pct=0.5`, `min_touches_resistance=2`. Skip Phase A2 (RR floor guard) per Concern 1.

### Phase D (cleanup / ratification)

If Concern 7 config test passes: ratify with code removal (`fix/dirbias-counter-mult-removal`). Drop `* counter_mult` from `structure_engine.py:1188, 1210`. Mark setting deprecated.

## Why NOT Path A

- Concern 2: Option 2.A regime-concentration multiplier violates directive.
- Concern 4: Phase C no-op defaults make the fix inactive at ship time.
- Concern 1: Phase A2 partially band-aid.
- Concern 6: Phase E verification not concrete.

Path A fails the directive in two places and the spec rules in two others.

## Why NOT Path B (the modified all-up plan)

Path B is the second-best option. It's acceptable and honors all directives. But it commits the operator to all four fixes before observing data. The empirical evidence (Phase 1.1: Issue 1 has 12% ceiling; Phase 1.7: orders are regime-proportional; Phase 1.8: 14d break-even) argues for shipping the smallest move first and letting data drive sequencing.

If the operator strongly prefers commitment to a complete plan, Path B is acceptable.

## Open questions for operator

These are tactical decisions inside Path C that the operator should pick at Phase 4:

1. **Phase A bundling vs sequencing**: ship A1 + A2 simultaneously (parallel branches, both go live at same restart), or A1 first then A2 12h later (sequential)? Parallel gives fastest learning; sequential gives clean attribution.

2. **Issue 4 wording choice**: prefer Option 4.1 ("Bias for shorts/longs when per-coin evidence agrees; per-coin tags override.") or Option 4.4 ("GLOBAL CONTEXT (per-coin tags above are PRIMARY)" — more conservative)?

3. **Concern 1 disposition**: drop Phase A2 entirely (the recommendation), or keep `chosen_rr ≥ 0.5` math-floor only?

4. **Spec typo correction**: update `IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md` line 433 to fix `src/labellers/state_labeler.py` → `src/workers/scanner/state_labeler.py`?

5. **STRAT_AGGRESSIVE_FRAMING sentinel correction**: bundle with Issue 4 in commit A1 (recommended) or ship as standalone Phase 0.1 commit?

## Risk register

- **R1**: Phase A doesn't shift brain direction noticeably (prompt change inert). MITIGATION: 48h measurement; if no shift, proceed to Issue 3.
- **R2**: Phase A over-corrects (brain becomes Buy-biased). MITIGATION: HARD REVERT if Buy share > 70% in 24h.
- **R3**: Concern 7 config test increases counter-LONG entries that lose money. MITIGATION: HARD REVERT if counter-LONG WR < 30% in 48h or M5 < 50% baseline.
- **R4**: Shipped fix breaks a previously-shipped fix. MITIGATION: Phase 6 metric M6 checks all sentinels still firing.
- **R5**: Shadow regresses. MITIGATION: Phase 6 Shadow E2E test before declaring Phase A success.

## Success criteria (FINAL, for operator approval at Phase 4)

Phase A SUCCESS:
- Brain Sell shifts to 60-90% range (from 92.3%).
- Buy WR ≥ 40% over 48h.
- Sell WR ≥ 40% over 48h.
- M5 (session PnL) ≥ 80% of baseline.
- M6 (all shipped sentinels) intact.
- No DB cascades.
- Shadow E2E test passes.

Project SUCCESS (end state):
- Direction distribution responds to market regime proportionally (no hardcoded amplification).
- Both Buy and Sell win rates ≥ 45% OR system honestly reflects regime (one direction ≥ 55%, the other allowed lower).
- Trade frequency held or rose.
- Total PnL not degraded.
- All five aim-bias questions answered YES.
- No new blocking mechanisms.
- No hardcoded asymmetric correction numbers.
