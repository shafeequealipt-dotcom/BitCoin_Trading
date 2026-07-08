# Phase 3.1 — Three Paths Comparison

Per spec lines 594-619.

## Path A — Ship the prior report's plan as-is

### Scope
Phases A-D from the prior report:
- Issue 4 + Issue 1 Phase A2 (Phase A, ~1 day, 2 commits).
- Issue 3 labeller soft haircut (Phase B, 3-5 days, 2 commits).
- Issue 1 Phase C structural fix (Phase C, 5-7 days, 4-5 commits) with `tp_min_distance_pct=0` default.
- Issue 2 Option B split fields + Option A regime-concentration multiplier (Phase D, 5-7 days, 4-5 commits).

### Estimated effort
- 13-15 atomic commits.
- ~680 LOC.
- 51-66 new tests.
- 17-23 days end-to-end.

### Risk
- MEDIUM. Multiple large surfaces touched. Cross-fix interactions possible.

### Aim-bias evaluation (FIVE QUESTIONS per fix)

Issue 4 fix: 
- 1. Preserve trade frequency? YES.
- 2. Preserve aggression? YES.
- 3. Improve decision quality? YES.
- 4. Preserve passive-close advantage? N/A (no close-side change).
- 5. Structural separation respected? YES.

Issue 1 Phase A2: 
- 1. Preserve trade frequency? PARTIALLY (suppresses 73% of collapse-driven flips — small effect).
- 2. Preserve aggression? PARTIALLY (adds a new block at decision boundary).
- 3. Improve decision quality? PARTIALLY (it's a band-aid per Concern 1).
- 4. Preserve passive-close? N/A.
- 5. Separation? YES.

Issue 3 soft haircut: 
- 1. Frequency? YES (haircut allows more counter-trend labels).
- 2. Aggression? YES.
- 3. Decision quality? YES.
- 4. Passive-close? N/A.
- 5. Separation? YES.

Issue 1 Phase C with no-op defaults: 
- 1. Frequency? YES.
- 2. Aggression? YES.
- 3. Decision quality? NO — fix is inactive at ship time. Operator must manually ramp.
- 4. Passive-close? N/A.
- 5. Separation? YES.
- **Aim-bias FAIL on question 3 per Concern 4.**

Issue 2 Option B + A: 
- 1. Frequency? PARTIALLY (Option B helps; Option A may force rebalancing).
- 2. Aggression? PARTIALLY.
- 3. Decision quality? PARTIALLY (Option A is hardcoded asymmetric correction per Concern 2 — VIOLATES directive).
- 4. Passive-close? N/A.
- 5. Separation? YES.
- **Aim-bias FAIL on question 3 per Concern 2.**

### Expected outcome
- Brain direction shifts toward balance — magnitude unclear due to coupled fixes.
- WR direction may diverge or converge.
- High variance in outcome.

### Reversibility
- Each fix has kill switch (config flag).
- Full rollback requires reverting 13-15 commits sequentially.
- Cumulative effect harder to disentangle than incremental.

### Verdict

**REJECT.** Path A violates the operator's design directive in two places (Concern 2 Option 2.A, Concern 4 no-op defaults). It also contains Phase A2 which is partially band-aid (Concern 1).

---

## Path B — Modified version

### Scope
- Remove the band-aid components (Issue 1 Phase A2 dropped).
- Reconsider Issue 2 — replace Options 2.A and 2.B with Concern 7's removal path (config-only test first, then code removal).
- Add concrete success criteria per Concern 6.
- Phase C ships with ACTIVE defaults per Concern 4.

Resulting commit list:
- **Phase B1**: Issue 4 + sentinel fix (1-2 days, 1 commit on `fix/dirbias-symmetric-regime-prompt`).
- **Phase B2**: Issue 2 Concern 7 config-only test (parallel with B1, 0 LOC).
- **Phase B3** (if Phase B1/B2 don't fully resolve): Issue 3 labeller soft haircut (3-5 days, 1 commit).
- **Phase B4** (if Phase B3 also doesn't): Issue 1 Phase C structural fix with active defaults (5-7 days, 2-3 commits).
- **Phase B5** (if Concern 7 config test passed): ratify with code removal of ×0.7 multiplier (1 commit).

### Estimated effort
- 4-7 commits total (if all four issues need shipping).
- ~250 LOC if all phases ship (much less than Path A).
- ~25-40 new tests.
- 10-15 days end-to-end (if all four).
- 2-5 days if only Phase B1 + B2 + ratification needed.

### Risk
- LOW-MEDIUM. Smaller per-fix surfaces. Phased decisions reduce blast radius.

### Aim-bias evaluation

Issue 4 fix: all 5 questions YES.
Issue 2 Concern 7 (config-test): all 5 questions YES.
Issue 2 Phase B5 (code removal): all 5 questions YES.
Issue 3 soft haircut: all 5 questions YES.
Issue 1 Phase C with active defaults: all 5 questions YES (no longer fails Q3).

**All proposed fixes pass aim-bias.**

### Expected outcome
- Phase B1+B2 (48h): brain direction shifts to 75-85% Sell. Buy WR maintained or improved.
- If B3 needed: labels rebalance from 4.84:1 to ~3:1 SHORT:LONG.
- If B4 needed: XRAY_DIR_FLIP collapse-driven flips eliminated.
- End state: directionally balanced trading aligned with regime, no hardcoded asymmetric correction.

### Reversibility
- Each phase has individual revert path.
- Phase B2 (config test) reverts in seconds.
- Phase B1 reverts by git revert.

### Verdict

**ACCEPTABLE.** Honors directive. Phased. Each phase has clear decision gate.

---

## Path C — Ship smallest viable change first, then decide (RECOMMENDED)

### Scope
Phase A only: Issue 4 + sentinel fix + Concern 7 config test, run 48h, measure, then decide whether more fixes are needed.

Sequence:
- Day 0: ship Phase A (Issue 4 commit + Concern 7 TOML edit).
- Days 1-2: 48h soak with metrics capture.
- Day 2: decision gate based on data.
  - If brain Sell drops to ≤80% AND Buy WR ≥ 40%: hold. Observe 7 days. STOP if WR continues converging.
  - If brain Sell stays 80-90% AND Buy WR ≥ 40%: ship Issue 3 next.
  - If brain Sell ≥ 90%: ship Concern 7 ratification + Issue 3 in parallel.
  - If Buy WR < 35%: revert; reassess.

### Estimated effort
- Phase A: 1-2 days. 1 atomic commit (Issue 4 + sentinel) + 1 TOML edit (Concern 7). ~80 LOC code + 8 test-marker edits + 1 new test file.
- TOTAL if Phase A is sufficient: ~2-3 days.
- TOTAL if Issue 3 needed: ~6-8 days.
- TOTAL if Issue 1 also needed: ~13-18 days.

### Risk
- LOW for Phase A.
- MEDIUM for follow-on phases (same as Path B).

### Aim-bias evaluation

Phase A passes all 5 questions on both fixes. Subsequent phases match Path B verdicts.

### Expected outcome
Phase A only (likely outcome based on Phase 1.7 evidence):
- Brain direction shifts from 92.3% Sell to ~85-90% Sell (small but measurable).
- Buy WR holds or improves (because counter-LONGs no longer 0.7-suppressed).
- 48h tells us how much of the bias was prompt-level vs upstream-level.

### Reversibility
- Phase A reverts in seconds (TOML) or minutes (git revert + restart).

### Verdict

**RECOMMENDED.** Lowest risk first move. Highest information yield per LOC. Aligned with Concern 5 + Concern 7 + Concern 8 partial validity.

---

## Comparison summary

| Aspect | Path A | Path B | Path C |
|---|---|---|---|
| Days end-to-end | 17-23 | 10-15 | 2-3 (Phase A only) to 13-18 (full) |
| Commits | 13-15 | 4-7 | 1-2 (Phase A) to 7-9 (full) |
| LOC | ~680 | ~250 | ~80 (Phase A) to ~250 (full) |
| Tests new | 51-66 | 25-40 | 5-8 (Phase A) to 25-40 (full) |
| Risk | MEDIUM | LOW-MEDIUM | LOW (Phase A) then escalating |
| Honors directive | FAILS (2 fixes) | YES | YES |
| Concrete success criteria | NO (Concern 6 fails) | YES (per Concern 6) | YES |
| Empirical data drives sequencing | NO | PARTIAL | YES |
| Aim-bias all 5 questions YES | NO | YES | YES |

---

## Recommendation preview

Path C is recommended. The reasoning is in `20_recommendation.md`. The Master Report at `MASTER_REPORT.md` presents the case to the operator for Phase 4 decision.
