# Issue 2 — Phase 2 — Aim-Bias Report

Date: 2026-05-18. Operator: Inshad. Branch ready: `fix/remove-portfolio-cap`.

## Decision Summary

Remove the portfolio direction concentration cap (R4 / GAMMA / CHECK 15) entirely. This was the operator's stated intent in `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 2 and confirmed in-session. No additional decisions needed from operator beyond this acknowledgment; Phase 3 may proceed immediately.

## Why the Cap Violates Aim

The cap was added on 2026-05-17 (commit `5713d43`) as an "aim-conditional" guard against the 2026-05-16 14:45 cascade where 5 same-direction Sells hit SL within 6 minutes. The "aim-conditional" qualifier was intended to soften it (permits same-direction entry in mono-trending markets). In practice the cap still rejected 25 of 97 proposals in the 2026-05-17 6h session and 31 raw event occurrences in the active log file — about 30% of the gate-rejection volume.

Per `IMPLEMENT_THREE_ISSUES_FIX.md` §A.1, the project aim is **aggressive opportunity exploitation, not capital preservation**. The cap is a portfolio-level preventive block: it rejects new entries based on aggregate state rather than the per-trade evidence the brain and APEX already evaluate. R2 (composite-score lock) and R3 (WR-aware override) make the per-trade direction decision; the cap restricts that decision at a coarser layer for a different reason. This is the exact pattern the operator told the agent NOT to do: "Don't tell the system what not to trade — fix what produces wrong decisions."

## Five Aim-Bias Answers

### 1. Does this preserve trade frequency?

**YES — increases it.** The cap rejected 25 of 97 proposals in the most recent session (about 26% rejection rate just from this single check). All 25 become eligible after removal. Combined with Issue 3 (5-min cooldown replacing the more-restrictive learning gate), trade frequency should rise materially.

### 2. Does this preserve aggression?

**YES — restores it.** The cap was an aggression governor that the operator never wanted. Removing it returns full aggressive entry to the system.

### 3. Does this improve decision quality (not just block)?

**YES.** R2 and R3 already perform per-trade direction evaluation with composite scoring and WR-aware override. The cap duplicates that intent without adding per-trade signal — it adds noise, not information. Removing the duplicate makes the gate validation chain shorter and the actual decision logic (R2/R3) the sole source of direction discipline. If direction bias re-emerges post-removal, R2/R3 are the layers to investigate, not the gate.

### 4. Does this preserve passive-close advantage?

**Not applicable.** The cap is entry-side; it cannot affect close paths. Passive close mechanisms (deadline, SL, mature-stall) are unchanged. Verified by grep: cap references touch no close path.

### 5. Does this respect structural separation of concerns?

**YES — improves it.** The gate becomes simpler (one less check). Concentration concerns revert to the architectural responsibility of R2 (APEX direction lock with composite signals including portfolio-WR data) and R3 (WR-aware threshold for XRAY override). The aim-conditional cap was a third layer trying to do the same thing.

## Risk Acknowledgment

Per `IMPLEMENT_THREE_ISSUES_FIX.md` §F Risk 4, removing the cap means a 14:45-style cascade may recur. The operator has stated tolerance for this: "the operator will tolerate it" / "after more data, the operator may design a different mechanism — but it will be evidence-driven, not preventive." We accept the risk explicitly.

If cascades recur frequently, the remedy is to revisit R2/R3 calibration (the per-trade evidence layer), not to reintroduce the cap.

## Forbidden Anti-Patterns Explicitly Avoided

Per `IMPLEMENT_THREE_ISSUES_FIX.md` §C Rule 3 (forbidden choices for Issue 2):

- Not setting cap to 100% (true removal).
- Not gating with a config flag (full delete, including settings fields).
- Not keeping logic "for future use" (helper deleted too — `get_direction_counts` was cap-exclusive).
- Not replacing with a different concentration mechanism (R2/R3 already address it).
- Not keeping cap metrics (all 6 log events removed).
- Not leaving dangling log events (audited via grep, all removed).

## Verification Plan

After Phase 3 lands and operator deploys, Phase 4 confirms:

- `grep -i "portfolio_direction_cap" data/logs/*.log` over the post-deploy session returns zero new event lines.
- `grep -rn "portfolio_direction_cap\|get_direction_counts\|CHECK_15" src/ tests/ scripts/` returns nothing.
- Gate rejection volume drops by the cap's prior share (25/97 ~= 26% on the 2026-05-17 baseline).
- Shadow path smoke test still works.
- No new errors referencing removed paths.

## Approval

The cap removal is the ALREADY-CORRECT answer per aim — investigation phase is light, and Phase 3 commits proceed without further operator gate per the prompt §C Rule 8.
