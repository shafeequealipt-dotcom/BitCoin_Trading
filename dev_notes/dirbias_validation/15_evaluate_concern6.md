# Phase 2.6 — Concern 6: Phase E verification is hand-wavy

## Concern restated

The prior report's Phase E says "after 13-15 commits and 17-23 days, look at it and decide what production values to lock." No specific success metrics. No "if WR < X, revert." The senior reviewer's concern: this kind of verification is the source of operational drift and post-hoc rationalization.

## Evaluation

### Is the criticism factually correct?

YES. The prior report's verification section (`DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md` §7.1 Phase E) says: "Live A/B with haircut = 0.3, 0.5, 0.7 on alternating days; measure short:long ratio and PnL... Lock the best haircut value and document."

Issues with this:
- "Best" is undefined. Best by what metric? Highest profit? Lowest variance? Highest Buy WR? Convergence of Buy/Sell WR?
- "Lock the value" lacks a process. Single TOML edit? Multiple operator approvals? Soak window?
- No revert thresholds. If WR degrades during A/B, when do we stop?
- No measurement of regression in previously-shipped fixes (wd_scoring, R1, etc.).

### What good verification looks like (from project precedent)

The wd_brain_scoring fix (Issue 1 of three-issues fix on 2026-05-18) had a clearer Phase 4 verification:
- Specific events to grep for (`WATCHDOG_CLOSE_SCORE_COMPUTED`).
- Specific minimum sample size before flipping (`enforce`) flag.
- Specific scoring threshold defaults validated against worked examples.

The CALL_B framing fix (commit `f62683c` on 2026-05-06) had a more concrete Phase 6 + Phase 7:
- 9 specific monitors (Phase 6).
- 9 sections of skeleton in Phase 7 verification report.
- Monitor 3 explicitly answered the operator's "is APEX flipping working or wasteful?" question.

Both are better-structured than the prior report's Phase E.

### Concrete verification design — per-fix metrics

For each shipped fix, define BEFORE shipping:

**A. Pre-ship baseline metrics** (24h pre-fix snapshot):
- M1: Direction distribution at STRAT_DIRECTIVE level — current %.
- M2: Direction distribution at BYBIT_DEMO_ORD_SEND level — current %.
- M3: Buy WR over 7 days, Sell WR over 7 days.
- M4: Trades per hour (mean over 24h).
- M5: Total session PnL per 24h.
- M6: All previously-shipped fix sentinels still firing.

**B. Post-ship measurement window** (24-48h, depending on fix):
- Re-measure M1-M6.
- Compute deltas vs baseline.

**C. Pass thresholds** (per-fix):

For Issue 4 (symmetric prompt):
- PASS: M1 drops to 70-90% Sell (from 92.3%). Direction shift visible.
- PASS: Buy WR ≥ 40% AND Sell WR ≥ 40%. Both directions remain viable.
- PASS: M4 ≥ 80% of baseline trades/hour. Trade frequency held.
- PASS: M5 not worse than 80% of baseline PnL.
- HARD REVERT: M1 < 30% Sell (severe over-correction — brain became Buy-biased).
- HARD REVERT: Buy WR < 35% within 48h (Buys are losing badly).
- HARD REVERT: Any M6 sentinel stops firing (regression in shipped fix).

For Issue 2 config-test (`counter_confidence_multiplier = 1.0`):
- PASS: counter setup confidence in logs jumps from 0.21 to 0.30 average.
- PASS: counter-LONG entries per hour rise.
- PASS: Buy WR doesn't drop below 35%.
- HARD REVERT: Counter-LONG WR < 30% over 48h (counter trades are genuinely bad).
- HARD REVERT: Session PnL worse than 50% of baseline.

For Issue 3 labeller soft haircut:
- PASS: LONG-direction label count rises by at least 30% in trending_down regime.
- PASS: Brain output direction distribution shifts toward balance.
- HARD REVERT: Buy WR drops below 35% AND label-LONG count > 2× baseline.

For Issue 1 (Phase A core — structural fix):
- PASS: `is_structurally_invalid` flag visible in logs.
- PASS: `XRAY_DIR_FLIP` count drops by at least 50% (collapse-driven flips eliminated).
- PASS: Buy WR ≥ 40%.
- HARD REVERT: Total trade frequency drops > 30% (legitimate trades getting blocked).

**D. Evaluation procedure**:
- Run for the specified window (24h or 48h).
- Pull metrics via specific grep / DB query commands (documented per fix).
- Compare to thresholds.
- Decision: PASS → proceed to next fix; FAIL → revert; INCONCLUSIVE → extend window.

### Tooling required

To make this concrete, each fix needs:
- A `dev_notes/<fix>/phase6_metrics.md` file with the exact commands to run.
- A `scripts/verify_<fix>.sh` script (optional) that runs the queries and prints PASS/FAIL.

The CALL_B framing fix has precedent — `dev_notes/callb_framing_fix/phase6_trial.md` with 9 monitor queries.

## Verdict

**VALID.** The prior report's Phase E is hand-wavy. Concrete metrics, thresholds, queries, and procedures must be designed per fix.

## Recommendation

For each fix the operator approves (Phase 4 gate), the implementation plan MUST include:
1. A `phase0_<fix>_baseline.md` with pre-ship metrics.
2. A `phase6_<fix>_thresholds.md` with specific PASS/HARD REVERT criteria.
3. A specific window duration (24h for Issue 4 prompt; 48h for Issue 2 config test; 48h for Issue 3; 72h for Issue 1 structural).
4. A script or documented procedure for running the verification.

## Implications for fix path

This concern is process, not architecture. Apply to whichever path the operator chooses.
