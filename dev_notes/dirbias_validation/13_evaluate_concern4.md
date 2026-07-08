# Phase 2.4 — Concern 4: Phase C defaults are no-op with manual ramp

## Concern restated

Phase C of the prior report (Issue 1 structural root fix) ships with:
- `tp_min_distance_pct = 0.0` (no-op default — clamp never fires).
- `min_touches_resistance = 1` (preserves current asymmetric behavior).

The plan is then to "ramp" these values manually over time: 0% → 0.5% → 1.0%; 1 → 2.

The senior reviewer's concern: "ship inactive, ramp later" is a way to ship without committing to the change. The actual fix sits in code but produces zero behavior change until operator manually flips a TOML value.

## Evaluation

### Is the criticism factually correct?

YES. Phase C ships:
- New `tp_min_distance_pct` field with default 0.0.
- New `min_touches_resistance` field with default 1 (matches current hardcoded behavior).
- New `is_structurally_invalid` flag — gets set when raw TP would land on the wrong side, but with default 0.0 the clamp doesn't fire so the flag is rarely set.

Net behavior at ship time: identical to pre-fix. The fix is structurally in code but functionally inactive.

### Is "ship inactive, ramp later" a valid pattern?

Sometimes. Example from the project: `wd_brain_scoring_enforce = False` (default) — the wd_claude_action scoring fix ships in log-only mode, operator reviews 24-48h of `WATCHDOG_CLOSE_SCORE_COMPUTED` events, then flips `enforce` to True via single-line settings commit.

That precedent is acceptable BECAUSE:
1. The log-only mode produces OBSERVABLE EVIDENCE the operator can review before activation.
2. The activation is a single explicit step.
3. The semantics of "log-only vs enforce" is well-defined and tested.

Does Phase C have these properties?

- **Observability**: with `tp_min_distance_pct=0`, the new `is_structurally_invalid` flag rarely gets set (only on extreme edge cases where TP is literally below current price). Operator can't observe the fix's impact on production until the default is non-zero.
- **Explicit activation**: yes, single TOML edit.
- **Well-defined semantics**: yes — the clamp formula is clear.

The middle property (observability) is the weakness. Unlike `wd_brain_scoring_enforce`, Phase C's no-op default doesn't produce useful log signal — the fix is invisible until activated.

### Phase 1.1 finding — non-zero defaults would catch most cases

Per Phase 1.1: 80.7% of XRAY_ANALYZE rows in audit window have `sup=0 res=5` (the structural pattern that triggers `rr_long` collapse). A non-zero `tp_min_distance_pct=0.5` would clamp the TP-min-distance in those cases.

So shipping with `tp_min_distance_pct=0.5` (active) vs `0.0` (inactive) is the difference between fixing 80% of cases on day 1 vs zero cases on day 1.

The Phase 1.1 agent's recommendation: "non-zero defaults would be safer" — the collapse is real and frequent enough that the fix should be active by default.

### Counter-argument

Argument for inactive default: shipping with active behavior risks regressions in cases the engineer didn't anticipate. Inactive default = ship the code with confidence, then activate after a soak window when production reveals any issues.

Counter-counter: the regression risk is captured by the kill-switch pattern (operator can revert to 0.0 in a single edit). And the "wait and see" pattern leaves the fix sitting in production doing nothing — which is the senior reviewer's specific concern.

### Comparison to other shipped fixes

Recent direction-related fixes that shipped ACTIVE by default:
- R1 (2026-05-17): shipped active. Trade_direction immediately plumbed through APEX.
- R2 (2026-05-17): shipped active. Composite scoring replaced regime-only lock.
- R3 (2026-05-17): shipped active. WR-aware threshold immediately replaced static 10×.
- 5-min reentry cooldown (2026-05-18): shipped active.

The only fix that shipped inactive was wd_brain_scoring (Issue 1 of the three-issues fix on 2026-05-18) — and that one has the observability log-only mode justification.

Phase C does NOT have a similar observability justification. The fix should ship active.

## Verdict

**VALID.** The "ship inactive, ramp later" pattern for Phase C does not have the observability justification that made it acceptable for wd_brain_scoring. The Phase 1.1 evidence (80.7% sup=0/res=5) shows the collapse is frequent — non-zero defaults would catch most cases on day 1.

## Recommendation

If Phase C ships, it should ship with:
- `tp_min_distance_pct = 0.5` (active, ~ATR-equivalent floor) — clamps tiny TPs without affecting most legitimate trades.
- `min_touches_resistance = 2` (symmetric with support) — eliminates the asymmetric filter on day 1.

Alternative: ship with `tp_min_distance_pct = 0.1` (mild) for the first week, ramp to 0.5 if no regression observed.

If the operator insists on inactive defaults, document this explicitly: "Phase C ships in 'observability mode' — the `is_structurally_invalid` flag will be visible in logs but no behavior change until operator activates." Then provide a clear activation procedure.

## Implications for fix path

- Path A (ship as-is) inherits the no-op-default flaw — operator should reject this aspect.
- Path B (modified) — fix the default values to be active.
- Path C (Issue 4 first, measure) — moot until Phase C is reached.
- If Phase C is shipped, it must be with active defaults.
