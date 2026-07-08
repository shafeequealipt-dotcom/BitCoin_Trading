# P0-3 Root Cause — Close-Veto Trap

Date: 2026-05-22. Symptom, mechanism, root cause, proposed root fix.

## H1 — Symptom

In the 2026-05-22 15:15–17:15 window, the brain emitted 15 explicit close votes. The watchdog rejected 12 outright and converted 3 to `reject_and_tighten` (SL tightened toward break-even). Zero brain close votes were executed. The composite score across all 15 votes never reached the 6.0 threshold; the observed range was 0.5 to 4.5. INJUSDT had 3 sequential close votes rejected (one at 82.7% SL consumption with the brain text "85% SL consumed, one tick from stop"). ICPUSDT had 5 close votes rejected over ~50 minutes as the loss deepened toward -1.9%. Both positions were force-closed by the operator's emergency-close at 17:14:54.

## H1 — Mechanism

`src/workers/position_watchdog.py:3463–3813`. When the brain emits a close vote, the watchdog computes the seven-factor composite score (see `03_p0_3_anatomy.md` for the formula). When `wd_brain_scoring_enforce = true` (currently the production state per `config.toml:531`, activated 2026-05-21 via commit `3bfb5e4`), the composite is binding:

- `composite >= 6.0` → execute.
- `0 <= composite < 6.0` → reject (`_scoring_skip_close = True` at line 3792, skips the actual close call at line 3820).
- `composite < 0` → reject_and_tighten (SL moved 30% toward entry via `_tighten_sl_breakeven_30pct`).

The brain's explicit close vote enters the scoring only via `reasoning_factor` (the natural-language reasoning string is classified as structural / vague / empty by STRUCTURAL_KEYWORDS at `src/risk/wd_brain_scoring.py:86–104`). The fact that the brain *voted close at all* (as distinct from the close path firing for automated reasons) is not itself a factor input.

## H1 — Root Cause

**The brain's explicit close vote contributes no decisive authority weight.** The 6.0 threshold is structurally unreachable under the realistic loser conditions present in this session because:

- The reasoning_factor maximum is +2.0 (structural keywords), which is the only signal that "the brain explicitly voted close with evidence" — but it is bounded.
- The other factor weights are tuned around the C1 use case (panic-close on a sound position) where the position has time remaining (time_factor negative), is not yet aged-losing (age_factor zero), has comfortable or tight SL (sl_factor zero or negative), and stationary velocity. Under these conditions, the composite floor sits at low single digits even when XRAY breaks and reasoning is structural.

This is provable from the actual ICP 16:50:40 worked example in the anatomy doc: a deep_loser with strong_negative velocity, broken XRAY, structural reasoning, and 74.6% SL consumption produces composite 4.5 — 1.5 short of threshold.

The 6.0 threshold is mathematically reachable but only when (a) time remaining is < 20 minutes (`moderate` or `imminent`), (b) age is > 30 minutes in loss (`aged_losing`), and (c) SL consumption is > 80% (`imminent`). The probability of all three aligning before a position closes via SL is small. The brain — which can read the regime and structure but not the watchdog's per-second velocity / time-remaining bucketing — vote slightly earlier than these alignments and is rejected.

**This is not the threshold being "too high" in the abstract sense.** It is the threshold being structurally unreachable for the *brain's evidence-based close* on a typical loser. The brain's vote needs a real positive contribution to bridge the gap.

The defect is amplified by the P0-2 direction-inversion. When XRAY silently inverts a Buy to Sell against the brain's high-conviction reading, the resulting position fights the trend the brain itself identified. The brain's subsequent close vote is *correct* (this is a wrong-direction trade), but the watchdog's scoring rejects it because the structural factors aren't yet sufficient — and the position rides to stop. This is the C1-versus-P0-3 contradiction reconciled in `03_p0_3_c1_reconciliation.md`: the close-veto's value is conditional on entry quality, and 2026-05-22 had inverted entries.

## H1 — Proposed Root Fix

### H2 — Component 1: `brain_vote_factor`

Add an eighth factor to the composite scoring, gated on whether the brain explicitly voted close (as distinct from the path firing for automated reasons). Default weight:

| Brain vote present | reasoning_bucket | brain_vote_factor |
| --- | --- | --- |
| yes | structural | +2.0 |
| yes | vague | +1.0 |
| yes | empty | +0.5 |
| no (automated close) | n/a | 0.0 |

The factor is bounded — it cannot single-handedly force a close when the structural evidence contradicts. A brain-with-structural-reasoning close on a structurally-sound position (xray_factor = -2.0, velocity_factor ≤ 0) still has composite well below threshold (worked C1 regression example in `03_p0_3_c1_reconciliation.md` shows composite -4.5).

This is implemented in `src/risk/wd_brain_scoring.py` by:

- Adding an optional parameter to `compute_brain_close_score`: `brain_vote_present: bool = False`.
- Adding a new bucket in DEFAULT_WEIGHTS under key `"brain_vote"` with the values above.
- Computing `brain_vote_factor` from `brain_vote_present` and `reasoning_bucket` (it depends on reasoning quality).
- Including `brain_vote_factor` in the composite sum.

The watchdog intercept at `position_watchdog.py:3754–3763` already knows the vote is explicit (it is invoked from `_execute_strategic_actions` with an explicit brain action). The call to `compute_brain_close_score` passes `brain_vote_present=True`.

### H2 — Component 2: `hard_risk_floor`

Independent of the composite, when `sl_consumption_pct >= hard_risk_floor_sl_pct` (default 85%, operator-tunable), force-close regardless of composite. New log tag `WATCHDOG_HARD_FLOOR_HIT | sym=X sl_pct=N floor=N composite=N`.

Implemented at `position_watchdog.py:3793` (just before the recommendation branch). If the floor is hit, jump straight to "execute" (let the close fall through), log the floor-hit event, and skip the composite-based reject. The floor protects against the edge cases where structural evidence is mixed or stale but the position is running out of risk budget.

### H2 — Component 3: Threshold Untouched

The 6.0 threshold remains 6.0. The fix does not lower it (Anti-pattern 4 of the spec). The brain_vote_factor and hard_risk_floor add new mechanisms; they do not nudge the existing one.

### H2 — Worked Examples Under the Fix

For the 12 rejected votes from 2026-05-22, the new composite is `old composite + brain_vote_factor + hard_floor_check`:

| Symbol | Time | Old composite | Reasoning | brain_vote_factor | New composite | Floor (85%)? | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INJ | 15:56:43 | 1.0 | structural | +2.0 | 3.0 | no (sl 72%) | reject (correct — pnl shallow, time deep, vel stationary) |
| INJ | 16:02:14 | 3.0 | structural | +2.0 | 5.0 | no (sl 81.8%) | reject |
| INJ | 16:05:19 | 2.0 | structural | +2.0 | 4.0 | no (sl 82.7%) | reject |
| NEAR | 16:10:21 | -6.0 | structural | +2.0 | -4.0 | no | reject_and_tighten |
| ICP | 16:19:31 | 1.0 | structural | +2.0 | 3.0 | no (sl 81.2%) | reject |
| ICP | 16:47:36 | 2.0 | vague | +1.0 | 3.0 | no (sl 67.2%) | reject |
| ICP | 16:50:40 | 4.5 | structural | +2.0 | 6.5 | no (sl 74.6%) | **execute** |
| ICP | 16:57:07 | 1.0 | vague | +1.0 | 2.0 | no (sl 71.6%) | reject |
| GMT | 16:57:07 | 0.5 | vague | +1.0 | 1.5 | no | reject (correct — pnl shallow, structural data weak) |
| ICP | 17:00:27 | 2.0 | vague | +1.0 | 3.0 | no (sl 67.2%) | reject |

Conclusion: the new factor pushes the ICP 16:50:40 case through (its 4.5 + 2.0 = 6.5 ≥ 6 → execute). The INJ at 82.7% SL is still rejected by the composite, but if the hard floor is set to 80% instead of 85% it would close. The operator's choice of floor value at the gate determines this.

If the floor is set at 80%, the INJ 16:02 (81.8%) and INJ 16:05 (82.7%) also force-close — which is what the spec wants. The cost is some risk of force-closing positions that might have recovered. The trial will measure this.

### H2 — Concrete Code Changes (proposal for operator review)

1. **`src/risk/wd_brain_scoring.py`** — extend the formula:
   - Add `"brain_vote"` to `DEFAULT_WEIGHTS` with the four buckets above.
   - Extend `compute_brain_close_score` signature with `brain_vote_present: bool = False`.
   - Classify `brain_vote_bucket` from `brain_vote_present` + reasoning_bucket.
   - Include `brain_vote_factor` in the composite sum and the `BrainCloseScoreFactors` dataclass.
   - Extend `as_log_dict` to include `brain_vote_bucket` and `brain_vote_factor`.

2. **`src/workers/position_watchdog.py`** — wire the brain_vote and hard_floor:
   - Pass `brain_vote_present=True` to `compute_brain_close_score` at line 3754.
   - Add `_hard_floor_sl_pct` read from settings (default 85.0).
   - Add hard-floor check before the recommendation branch (line 3779): if `_sl_consumption is not None and _sl_consumption >= _hard_floor_sl_pct`, override `_score.recommendation = "execute"` and emit `WATCHDOG_HARD_FLOOR_HIT`. Set `_scoring_skip_close = False`.
   - Extend boot sentinel: `P0_3_SENTINEL | brain_vote_weight=on hard_risk_floor_sl_pct=85.0 threshold=6.0 enforce_mode=on`.

3. **`src/config/settings.py`** — `Watchdog` dataclass new fields:
   - `wd_brain_vote_weight_enabled: bool = True`.
   - `wd_hard_risk_floor_sl_pct: float = 85.0`.
   - `wd_brain_vote_factor_structural: float = 2.0`.
   - `wd_brain_vote_factor_vague: float = 1.0`.
   - `wd_brain_vote_factor_empty: float = 0.5`.

4. **`config.toml`** — corresponding entries (lines added near 519–532):
   - `wd_brain_vote_weight_enabled = true`.
   - `wd_hard_risk_floor_sl_pct = 85.0`.
   - `wd_brain_vote_factor_structural = 2.0`.
   - `wd_brain_vote_factor_vague = 1.0`.
   - `wd_brain_vote_factor_empty = 0.5`.

5. **Boot sentinel** — `P0_3_SENTINEL` at `position_watchdog.py:__init__` emitting the new active configuration.

6. **Verification script `verify_p0_3.py`** — parses trial log, asserts:
   - For every `BRAIN_CLOSE_VOTE_RECEIVED` with `sl_pct >= hard_floor_sl_pct`, a `WATCHDOG_HARD_FLOOR_HIT` follows.
   - For every brain-with-structural-reasoning vote on a broken-XRAY position with composite ≥ 6.0, `WATCHDOG_CLOSE_EXECUTED` follows.
   - For brain-silent automated close paths, the new brain_vote_factor stays 0 (no inflation when brain didn't vote).
   - No-churn regression: brain vague-reasoning panic-close on a structurally-supportive position still rejects (composite below threshold even with brain_vote_factor).

### H2 — What This Fix Preserves

- Trade frequency: preserved. The fix touches close authority only; entries are unaffected.
- Aggression: preserved. The brain's evidenced close vote is honoured.
- Decision quality: improved. The scoring now weighs the brain's vote alongside independent factors.
- Passive-close advantage: preserved. The C1 anti-churn role still applies when the brain is silent or its vote contradicts the structural evidence on a sound position.
- Layer separation: preserved. The watchdog (Layer 6) still owns the composite; Layer 2 (brain) contributes an explicit-vote input.

### H2 — What This Fix Does NOT Do

- It does not lower the 6.0 threshold (Anti-pattern 4).
- It does not disable the watchdog (Anti-pattern 6).
- It does not hardcode "never close below -X%" (Anti-pattern in C1 spec Part I).
- It does not give the brain unilateral close authority. The brain's vote needs reasoning quality and (often) structural evidence to push the composite to threshold.
- It does not change the C1 enforce flag (`wd_brain_scoring_enforce` stays true unless operator sequences differently at the gate).

### H2 — Risk

- The hard floor at 85% may close positions that would have recovered from 85% SL drawback. Mitigation: the floor is operator-tunable; trial measures the rejected-and-held outcome.
- The brain_vote_factor may push edge-case panic-closes through when they should have been rejected. Mitigation: the C1-target worked example shows the composite is still well below threshold for vague-reasoning panic-close on a sound position.
- The new factor changes the composite distribution; any analytics keyed off the old distribution must be updated. Mitigation: the trial measures the new distribution and any consumer (Telegram dashboard, daily summary) is updated to match.

## H1 — Decision Gate (P0-3)

I will ask the operator to approve:

1. The brain_vote_factor with the proposed weights (structural +2.0, vague +1.0, empty +0.5)?
2. The hard_risk_floor at default 85% SL consumption?
3. The C1 sequencing — keep enforce mode ON with the new scoring (recommended), pause to log-only for a session, or roll back?
4. Whether the P0-3 fix is applied AFTER P0-2 is verified (Rule 6) or in parallel (faster but adds the risk of overlapping changes).

No code change will be applied until operator approves at the gate.
