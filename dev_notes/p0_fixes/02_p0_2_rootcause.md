# P0-2 Root Cause — Direction Inversion

Date: 2026-05-22. Symptom, mechanism, root cause, proposed root fix.

## H1 — Symptom

Of 25 trades opened in the 2026-05-22 15:15–17:15 window, 15 had their direction silently flipped from the brain's intended direction by XRAY. Concrete cases include NEARUSDT (Buy→Sell ratio=100.6x), INJUSDT (multiple Buy→Sell ratios 68.1x–99.7x), GMTUSDT (Buy→Sell 7.1x), ICPUSDT (Buy→Sell 9.6x twice), PLUMEUSDT (Buy→Sell 50.4x), and one Sell→Buy (AVAXUSDT 3.8x). The same trades carried paired log lines `APEX_DIR_LOCK | dir=Buy` and `XRAY_DIR_FLIP | flipped_dir=Sell` — two components asserting opposite truths on the same trade.

## H1 — Mechanism

`src/workers/strategy_worker.py:1994` falls through to the flip block when `_lock_override_active OR (not _apex_locked AND _ratio > _flip_threshold)`. The flip block writes `trade["direction"] = _flipped_dir` at line 2081, replacing the brain's value. Downstream consumers (coordinator, order service, thesis save) read this overwritten value as the placed direction. APEX's earlier `APEX_DIR_LOCK` log line at `optimizer.py:328` reflects APEX's *internal* decision and is true at the time it is logged; XRAY's later `XRAY_DIR_FLIP` is also true at the time it is logged; the two truths describe two stages of the same flow, but the *placed direction* is whichever wrote `trade["direction"]` last.

## H1 — Root Cause

**Authority misallocation, with no computation defect.**

The long-rr and short-rr formulas (`src/analysis/structure/structural_levels.py:67–259`) are computationally correct mirror images of one another and use support/resistance in the correct roles. The systematic 0.1-to-0.5 long-rr vs 2-to-14 short-rr asymmetry observed on every Buy in the 2026-05-22 logs is a real structural fact that arises whenever price sits near a level: the direction whose SL coincides with the nearby level has tiny risk and large reward; the direction whose TP must land *just past* the nearby level has its TP clamped (via `tp_min_distance_pct`) to a near-entry value and so its reward is tiny relative to risk. The math reproduces the observed ratios exactly (worked example for NEARUSDT in `02_p0_2_anatomy.md`).

The defect is the *authority* XRAY is given to silently reverse the brain's high-conviction directive. Today the precedence in code is XRAY > APEX > brain. The brain emits Buy because (per its reasoning text in the same brain cycle) it has read the ensemble votes (3.42 buy vs 0.00 sell on INJ), the per-coin regime (trending_up), and the global fear regime, and concluded contrarian-long is the intended trade. XRAY reads the same structural placement and concludes "but price is near resistance, short has better structural-rr right now". The watchdog cannot decide which is right *in general*; both perspectives are valid in different regimes. The defect is that the code lets XRAY win silently, with no skip-on-conflict, no operator-visible authority decision, and contradictory log lines.

The spec's anti-pattern list bans clamping the flip ratio or disabling XRAY. The proposed fix below neither clamps nor disables; it redefines authority.

## H1 — Proposed Root Fix (Authority-Based)

### H2 — Definition: High-Conviction Brain Directive

A brain directive is **high-conviction** when:

- The brain emits an explicit direction (Buy or Sell), AND
- The per-coin regime as reported in the same brain cycle is `trending_up` (for Buy) or `trending_down` (for Sell), AND
- The structural_data `trade_direction` returned by XRAY (R1 ALPHA plumbing) agrees with the brain's direction, OR the ensemble vote for the brain's direction exceeds the opposite by a defined margin (default 1.5x).

The conviction definition is operator-tunable. The above defaults can move to `config.toml`.

### H2 — New Precedence

When the brain's directive is **high-conviction**:

- XRAY is allowed to **veto** (no trade is placed; logged as a single-reason skip), but is **not** allowed to reverse.
- APEX is allowed to **adjust** parameters (SL/TP/size), but is **not** allowed to reverse.

When the brain's directive is **low-conviction** (e.g., ranging regime, ensemble disagreement, neutral structural_data.trade_direction):

- The existing XRAY override path remains, but the dual-logging is removed in favour of a single coherent `DIRECTION_DECISION` line emitted once per trade.

In neither case is the contradictory pairing of `APEX_DIR_LOCK | dir=Buy` and `XRAY_DIR_FLIP | flipped_dir=Sell` permitted on the same trade.

### H2 — Concrete Code Changes (proposal for operator review)

1. **`src/workers/strategy_worker.py:1925–2111`** — replace the `_lock_override_active` decision and the three branches (suppress / override / flip) with:
   - Determine `_high_conviction` by reading regime + ensemble + structural_data.trade_direction from the trade dict (these fields are already available via the R1 ALPHA plumbing).
   - If `_ratio > _flip_threshold` and `_high_conviction == True`: VETO path — log `DIRECTION_DECISION | sym=X intended=<brain_dir> decision=skip authority=XRAY reason=high_conviction_disagrees_with_structure rr_long=N rr_short=N ratio=N`, log `TRADE_SKIP | rsn=xray_veto`, return `(False, "xray_veto")`.
   - If `_ratio > _flip_threshold` and `_high_conviction == False`: FLIP path — perform the existing flip logic but emit a single `DIRECTION_DECISION | sym=X intended=<brain_dir> decision=<flipped_dir> authority=XRAY reason=low_conviction_structural_disagreement rr=N ratio=N`. Suppress the standalone `XRAY_DIR_FLIP` line; it is replaced by `DIRECTION_DECISION`.
   - If `_ratio <= _flip_threshold`: no flip, no extra log.

2. **`src/apex/optimizer.py:328–331`** — gate the `APEX_DIR_LOCK` info-level emission so it does not fire when XRAY subsequently overrides. The cleanest mechanism: emit `APEX_DIR_LOCK` only at WARNING level (today INFO), and have the XRAY override path log `DIRECTION_DECISION authority=XRAY` which supersedes; the operator's audit query for "what direction decision happened on this trade" reads `DIRECTION_DECISION` lines only.

3. **`config.toml`** — new tunables (with defaults that match current behaviour):
   - `xray_high_conviction_required_for_protection = true` (boolean kill-switch — if false, current XRAY-override behaviour holds).
   - `xray_high_conviction_ensemble_margin = 1.5` (ratio for ensemble-agreement leg of conviction definition).

4. **Boot sentinel** — add at strategy_worker `__init__`:
   - `P0_2_SENTINEL | high_conviction_protection=on high_conviction_ensemble_margin=1.5 dual_logging=removed unified_decision_log=on`.

5. **Verification script `verify_p0_2.py`** — parses the trial-window log, asserts:
   - Zero co-occurring `APEX_DIR_LOCK | dir=Buy` + `XRAY_DIR_FLIP | flipped_dir=Sell` on the same `did=`.
   - Exactly one `DIRECTION_DECISION` log line per trade.
   - For every trade where high-conviction was true and XRAY structural-rr disagreed, the trade was skipped (`decision=skip authority=XRAY`).

### H2 — What This Fix Preserves

- Trade frequency: preserved or improved. A brain Buy that XRAY would have silently flipped to Sell now either executes as Buy or is skipped. Net entries do not drop; the brain's good directions execute as intended.
- Aggression: preserved. The brain's directional conviction is honoured.
- Decision quality: improved. No more contradictory dual logging.
- Passive-close advantage: untouched (this is an entry-direction fix, not a close fix).
- Layer separation: improved. Layer 1B (XRAY) no longer silently overrides Layer 2 (brain) intent.

### H2 — What This Fix Does NOT Do

- It does not clamp the flip ratio (Anti-pattern 2 of the spec).
- It does not disable XRAY (Anti-pattern 2).
- It does not lower the flip threshold (Anti-pattern 4).
- It does not force-Buy in fear regimes (Anti-pattern 3 / aim violation).
- It does not change the long_rr / short_rr formulas (the formulas are correct).

### H2 — Risk

- If the high-conviction definition is too tight, XRAY's flip authority is too easily blocked and the system misses structurally-correct reversals on legitimately ranging coins. Mitigation: the conviction definition is operator-tunable, defaults are conservative, and a trial confirms that low-conviction flips still happen.
- If the high-conviction definition is too loose, the spec's intended protection is weakened. Mitigation: same; trial measurement.
- The `APEX_DIR_LOCK` log-level change may affect existing operator dashboards. Mitigation: search project for any consumer of the INFO-level event before the change; only one is known (the operator's audit query, which I will update).

## H1 — Decision Gate (P0-2)

I will now ask the operator to approve the authority-based fix as described. Specifically I will ask:

1. Approve the high-conviction protection? (default yes, recommended)
2. Approve removing the dual logging in favour of `DIRECTION_DECISION`? (default yes)
3. The `xray_high_conviction_ensemble_margin` default — 1.5 OK, or different?
4. Any preference for the `xray_high_conviction_required_for_protection` kill-switch default (on or off)?

No code change will be applied until operator approves at the gate.
