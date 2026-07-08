# Direction-Bias Validation and Fix — Master Report (Phase 4 Operator Decision Gate)

Date: 2026-05-19.  
Branch: `fix/wd-scoring-brain-vote` (working tree implementation-clean).  
Spec: `/home/inshadaliqbal786/IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md`.  
Prior report: `/home/inshadaliqbal786/DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md`.

## Executive summary

The prior report's four CRITICAL issue diagnoses are **substantively accurate** at the code level. All cited operative surfaces verified by independent agents. The funnel data, smoking gun, market reality, and historical claims hold up to regeneration.

However, **new evidence materially refines the framing**:

1. **Issue 1 (XRAY flips) is small effect** — accounts for AT MOST 12% of direction skew in audit window (11 flips / 91 brain directives).
2. **Final order direction is approximately regime-proportional** — 89.3% Sell observed vs 89.9% expected from 8.9× downtrend regime distribution. System tracks market.
3. **14-day WR is below break-even for BOTH directions** (Buy 41.8%, Sell 42.4%). The 7-day "Sell is profitable" narrative cherry-picks a window.
4. **Prompt amplification (Issue 4) is real but small** — brain output 92.3% vs 89.9% regime-proportional (~2-3 pp).

The 80%+ Sell ratio is mostly the *market*, partially the *code*. Fixing the coded asymmetries honors the operator design directive without expecting dramatic PnL change. The right move is to ship the smallest viable change first, measure 48 hours, and let data drive whether further fixes are needed.

**Recommendation: Path C** — ship Issue 4 (symmetric prompt + sentinel correction) + Issue 2 Concern 7 config-only test in parallel as Phase A. ~2-3 days, ~80 LOC code, fully reversible.

## Phase 1 validation results — per-issue verdict

| Issue | Prior report claim | Validation verdict |
|---|---|---|
| 1. XRAY rr_long collapse | 4 root causes: no min-edge floor; abs() masks wrong-side TP; asymmetric `min_touches`; static 3.0× threshold | **VERIFIED.** All four root causes confirmed at cited file:line. Asymmetric `min_touches` (hardcoded `>= 1` for resistance vs config `2` for support) introduced commit `c3e5380` 2026-04-13, untouched 36 days. 80.7% of XRAY_ANALYZE rows in audit have `sup=0 res=5`. 8 of 11 XRAY_DIR_FLIP events in window are collapse-driven. |
| 2. Counter ×0.7 multiplier | ×0.7 at `structure_engine.py:1071, 1188, 1210` + compounds through 4 stacked floor-0.5 multipliers + 1 conviction cut = ~3.88× total | **VERIFIED.** All 9 downstream consumers confirmed. Compounding math reproduced (1.6× mean, 9.3× modal, ~3.88× theoretical). Origin commit `3a59637` 2026-04-30, no tuning since. Concern 7 config-only test (set value to 1.0) is feasible — validator allows. |
| 3. Labeller per-trigger gates | `_qualifies` AND-gate is dead code; real gates are 8 hard `if not _is_X(regime): return None` predicates in `state_labeler.py` | **VERIFIED.** `mode = "briefing"` confirmed. `_qualifies` only called inside exclusion-mode body at line 1604 (unreachable). All 8 predicates at lines 253, 268, 283, 301 (off-by-one — was 302), 356, 371, 477, 491. 716:148 SHORT:LONG label ratio confirmed. |
| 4. Asymmetric MARKET REGIME block | Live at `strategist.py:3371-3390`; dead duplicate at 1416-1435; dead method at 4155-4251; `STRAT_AGGRESSIVE_FRAMING` falsely advertises | **VERIFIED.** All three blocks confirmed byte-for-byte. Caller-chain proves dead-code claims. Boot sentinel at line 870 emits 37 times in audit (correctly noted as misleading). |

### New findings (not in prior report)

- **NF-1**: Issue 1 flips have a 12% ceiling on direction skew (11 / 91 brain decisions). The other 88% is upstream of XRAY flips.
- **NF-2**: Final orders (89.3% Sell) are regime-proportional (89.9% expected). System is tracking market.
- **NF-3**: 14-day WR shows both directions below 50%. System is essentially break-even at scale.
- **NF-4**: 3 additional Issue 2 propagation sites at `scanner_worker.py:623, 771, 835` (not new compounding stages, just propagation).
- **NF-5**: Issue 3 alternative cheap fix — boost `LABEL_BASE_WEIGHTS[COUNTER_TRADE_*]` from 0.45 to 0.65 (one-line edit, untested but cheap).
- **NF-6**: Issue 4 trim-marker lock-step — header text appears at 3 sites in `strategist.py` + 8 lines across 2 test files. Any rename must update all 11 sites.
- **NF-7**: `TRADE_SYSTEM_PROMPT_ZERO_TWO` is the live system prompt (every CALL_A emission has `zero_two_flag=True`). Legacy `TRADE_SYSTEM_PROMPT` is behaviourally dead.
- **NF-8**: Second asymmetry in `structure_engine.py:236-256` — `position_in_range` fallback pushes value to 1.0 when `sup=0 res=5`, mapping to `entry_quality=poor` for longs (independent of RR collapse).
- **NF-9**: Briefing-mode observability gap — `fail_regime=0` hardcoded in aggregate logs. A `label_regime_extinguished` counter would surface labels killed by per-trigger predicates.
- **NF-10**: CALL_B is symmetric. No fix needed there.

### Corrections to prior report

| Item | Prior report | Actual | Materiality |
|---|---|---|---|
| `bearish_fvg_ob` count | 4,124 | 2,062 | Low — in-direction:counter ratio (5.9:1) unchanged |
| APEX_LOCK_OVERRIDE_GRANTED Sell→Buy | 22 | 25 | Low — directional pattern unchanged |
| state_labeler line 302 | 302 | 301 | Negligible |
| STRAT_AGGRESSIVE_FRAMING count | 36 | 37 | Negligible |
| Issue 1 flip decision boundary | line 1727-1739 | ratio at 1727-1739, decision at line 1860, mutation 1923-1977 | Medium — affects fix location |

### Spec typo

Spec line 433 says `src/labellers/state_labeler.py` (path does not exist). Correct path is `src/workers/scanner/state_labeler.py`. **Operator should approve or reject editing the spec file to correct this.**

## Phase 2 critical evaluation — per-concern verdict

| Concern | Verdict | Implication |
|---|---|---|
| 1. Issue 1 Phase A2 is a band-aid | PARTIALLY VALID | Drop Phase A2 (recommendation) or ship only `chosen_rr ≥ 0.5` math-floor (not asymmetric `flipped_rr ≥ 2.0`) |
| 2. Issue 2 Option A violates directive | VALID | REJECT Option A entirely |
| 3. Issue 2 Option B preserves suppression | PARTIALLY VALID | Acceptable as fallback; prefer Option E (Concern 7 removal) first |
| 4. Phase C defaults are no-op | VALID | If Phase C ships, use ACTIVE defaults (`tp_min_distance_pct=0.5`, `min_touches_resistance=2`) |
| 5. Ship Issue 4 alone first, measure | STRONGLY VALID | Path C recommended |
| 6. Phase E verification hand-wavy | VALID | Each fix needs concrete pre-ship baseline, post-ship thresholds, revert triggers |
| 7. ×0.7 should be REMOVED entirely | VALID | Ship Concern 7 Phase 7-1 (config-only test) parallel with Issue 4 |
| 8. Bias may not be a bug | PARTIALLY VALID | Bias is partially regime-proportional; asymmetric coding is still directive violation. Fix anyway. |

## Three paths analysis

| Aspect | Path A (as-is) | Path B (modified) | Path C (smallest first) |
|---|---|---|---|
| Days end-to-end | 17-23 | 10-15 | 2-3 (Phase A) to 13-18 (full) |
| Commits | 13-15 | 4-7 | 1-2 (Phase A) to 7-9 (full) |
| Risk | MEDIUM | LOW-MEDIUM | LOW (Phase A) escalating |
| Honors directive | FAILS in 2 places | YES | YES |
| Aim-bias 5 questions YES on all fixes | NO | YES | YES |
| Concrete success criteria | NO | YES | YES |
| Data drives sequencing | NO | PARTIAL | YES |

## Recommendation: Path C

### Phase A (2-3 days, parallel)

Two independent atomic commits:

**Commit A1: `fix/dirbias-symmetric-regime-prompt`**
- Edit `strategist.py:3371-3390` — symmetric direction_hint dict + paired NOTE on both regimes at conf > 0.60.
- Edit `strategist.py:1416-1435` — apply same to dead duplicate.
- Edit `strategist.py:870` — fix `STRAT_AGGRESSIVE_FRAMING` sentinel (no more `regime_instr=minimal` lie).
- Add `STRAT_REGIME_BLOCK_VERSION = 2` + `STRAT_REGIME_INSTR_REFRAMED` boot sentinel.
- Update `_TRIM_ESSENTIAL_MARKERS` lock-step.
- 8 test marker edits + 1 new test file `tests/test_regime_block_symmetry.py`.
- ~80 LOC.

**Commit A2: `fix/dirbias-counter-mult-config-test`**
- Edit `config.toml:1724` — `counter_confidence_multiplier = 1.0` (from 0.7).
- 1 line. No code, no tests.

### Phase A success criteria (per Concern 6)

After 48h:

| Metric | Threshold | Outcome if met | Outcome if missed |
|---|---|---|---|
| Brain Sell% | drops to 60-90% range (from 92.3%) | proceed to next decision | revert or escalate |
| Buy WR | ≥ 40% | PASS | revert |
| Sell WR | ≥ 40% | PASS | investigate |
| Trades/hour | ≥ 80% baseline | PASS | investigate |
| Session PnL | ≥ 80% baseline | PASS | revert if < 50% |
| All shipped fix sentinels firing | YES | PASS | revert |
| Shadow E2E test | PASS | continue | revert |
| Buy share | < 70% | PASS | revert (over-correction) |

### Phase B (only if Phase A insufficient)

If brain Sell ≥ 90% at 48h: ship Issue 3 labeller soft haircut (`fix/dirbias-labeller-soft-haircut`).  
If Buy WR < 35%: revert and reassess.  
If passing all: ratify Concern 7 with code removal (`fix/dirbias-counter-mult-removal`).

### Phase C (only if Phase B insufficient)

Ship Issue 1 Phase 1.A structural fix (`fix/dirbias-xray-rr-collapse`) with ACTIVE defaults per Concern 4 verdict.

## Five aim-bias questions per proposed fix

### Commit A1 (Issue 4 symmetric prompt)

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserve trade frequency? | YES | Prompt edit doesn't gate trades. |
| 2. Preserve aggression? | YES | Removes a directive ("DEFAULT SELL BIAS"); doesn't add new blocks. |
| 3. Improve decision quality? | YES | Symmetric framing lets Claude weigh both directions on per-coin evidence. |
| 4. Preserve passive-close advantage? | N/A | No close-side change. |
| 5. Structural separation? | YES | Layer 2 fix stays in Layer 2 (strategist). |

### Commit A2 (Issue 2 Concern 7 config test)

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserve trade frequency? | YES | More counter setups will pass through sizing; trade count may rise. |
| 2. Preserve aggression? | YES | Removes a hardcoded confidence cut. |
| 3. Improve decision quality? | YES | Counter setups with strong MTF/SMC no longer pre-suppressed. |
| 4. Preserve passive-close? | YES | Layer 4 force-close becomes symmetric on counter vs in-direction (Phase 1.2 finding). |
| 5. Structural separation? | YES | Layer 1B fix stays in Layer 1B. |

## Open questions for operator

1. **Path choice**: approve Path C (recommended) or different?

2. **Phase A bundling**: ship A1 + A2 simultaneously (parallel, fastest learning) or A1 first then A2 12h later (sequential, clean attribution)? **Recommendation: parallel** — both fixes are independent and the 48h trial is the same window.

3. **Issue 4 wording**: choose between:
   - **Option 4.1 (recommended)**: header `## MARKET REGIME (CONTEXT)`; direction_hint: `"Bias for shorts when per-coin evidence agrees; per-coin tags override."` and mirror for longs.
   - **Option 4.4**: header `## GLOBAL CONTEXT (per-coin tags above are PRIMARY)`. More conservative; emphasizes per-coin primacy.

4. **Concern 1 Phase A2 disposition**: drop entirely (recommendation) or ship `chosen_rr ≥ 0.5` math-floor only?

5. **Spec typo correction**: update `IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md` line 433 from `src/labellers/state_labeler.py` to `src/workers/scanner/state_labeler.py`?

6. **STRAT_AGGRESSIVE_FRAMING sentinel correction**: bundle with Commit A1 (recommended) or ship as standalone Phase 0.1 commit (since the false advertising has been misleading logs for weeks)?

## Rollback plan summary

Phase A:
- A1 revert: `git revert <A1-commit-sha>` + `sudo systemctl restart trading-workers trading-mcp-sse`. ~2 min.
- A2 revert: `git checkout config.toml` + same restart. ~30 sec.

Phase B / C revert paths follow the same pattern.

Phase 6 verification will catch any regression — if any of the 8 metrics fails, the protocol is `revert + escalate + diagnose`.

## What success looks like (project end state)

- Direction distribution responds to market regime PROPORTIONALLY (no hardcoded amplification beyond ~5pp).
- Both Buy and Sell WR ≥ 45% over a 14-day window, OR system honestly tracks regime (one direction ≥ 55%, the other allowed to be lower).
- Trade frequency held or rose.
- Total PnL not degraded vs pre-fix baseline.
- All five aim-bias questions answered YES throughout.
- All previously-shipped fixes (R1, B1a, wd_scoring, portfolio cap removal, 5-min cooldown) still working.
- No new blocking mechanisms introduced.
- No hardcoded asymmetric correction numbers in `config.toml` or source.

## Phase 4 — Operator decision

This is the gate. No code changes will begin until the operator:
- Approves Path C (recommended) OR a different path.
- Picks among the open questions above.
- Authorizes Phase 5 implementation.

Per spec Rule 10 + line 21, implementation without operator agreement is a serious violation. Standing by for direction.
