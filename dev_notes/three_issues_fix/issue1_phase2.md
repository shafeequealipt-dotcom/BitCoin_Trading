# Issue 1 — Phase 2 — Aim-Bias Report + Operator Decision Surface

Date: 2026-05-18.

## Baseline (Re-Confirmed from DB)

From Phase 0 baseline (`data/trading.db.trade_log`, all-time):
- `wd_claude_action`: 56 closes, -$463.41 cumulative, 4 wins, mean per-close **-$8.28**, WR **7%**.
- `wd_dl_action` (deadline close): 79 closes, +$593.04, 75 wins (95% WR) — unaffected by Issue 1.
- `wd_profit_take`: 8 closes, +$419.18, 100% WR — unaffected.
- `wd_trail`: 3 closes, +$43.03, 100% WR — unaffected.

The brain-discretionary close path is the only loser among the watchdog close family. Issue 1 targets only this path.

## Design Summary

Brain close decisions become VOTES in a multi-factor composite score that the watchdog computes immediately after the existing 300s min-hold guard passes. The score combines 7 factors (PnL, time-to-deadline, age, velocity, SL consumption, XRAY structural verdict, brain reasoning quality) per the operator-approved weights in `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 1 §B.

Two-phase rollout:
1. **Phase 1 (log-only, ~24-48h after operator deploy)** — `wd_brain_scoring_enforce=False`. Scoring runs and logs every factor + composite, but brain's close still executes. Operator validates that scoring correctly predicts good vs bad closes from real session logs before flipping behavior.
2. **Phase 2 (enforce, after operator approval)** — flag flipped to True. composite >= +6 closes; 0 <= composite < +6 holds; composite < 0 holds + tightens SL toward break-even by 30% of remaining distance.

## Five Aim-Bias Answers

1. **Trade frequency?** HIGHER. Positions held longer when brain panics → fewer forced exits that could be re-entered; net frequency stable or up.
2. **Aggression?** PRESERVED. Brain still has voice (its vote is one factor); close still fires when conviction is high (composite >= +6).
3. **Decision quality?** MAJOR IMPROVEMENT. Decision uses 7 signals instead of 1 (the brain's free-form text alone).
4. **Passive-close advantage?** PRESERVED. Only `wd_claude_action` is scored. `wd_dl_action`, `wd_profit_take`, `wd_trail`, `wd_timeout`, `wd_trail`, `wd_hard_stop`, sniper paths, mode4_*, shadow_sl_tp, bybit_sl_hit — all unchanged.
5. **Layer separation?** YES. Watchdog stays sole close-authority; brain stays advisor; gate untouched; no cross-layer hacks. The min-hold guardrail (Phase 1B from 2026-05-05) is preserved as the fast-path bypass for explicit-reason closes.

## Forbidden Anti-Patterns Explicitly Avoided

Per prompt §C Rule 3 (Issue 1 list):
- Brain's close authority is NOT disabled outright (voting weight preserved).
- No hard-coded "always hold below -X%" without considering other factors (PnL is one of 7 factors).
- The existing 300s min-hold is RETAINED (not removed and replaced — the scoring layers on top).
- Log-only validation phase IS included as Phase 1 (not jumping straight to enforce).
- Default thresholds are based on the prompt's worked examples + multi-session aggregate, not a single session.
- Brain's close requests are LOUDLY logged (never silently suppressed).

## Atomic Commit Plan (Locked)

Branch: `fix/wd-scoring-brain-vote` (stacked on Issue 3 tip).

1. `issue1/p3-1 feat(risk): add pure brain-close scoring module` — `src/risk/wd_brain_scoring.py` with `compute_brain_close_score(...)` returning a `BrainCloseScore` dataclass.
2. `issue1/p3-2 feat(config): add wd_brain_scoring_* WatchdogSettings` — new dataclass fields + loader entries.
3. `issue1/p3-3 feat(watchdog): wire brain-close scoring (log-only by default)` — insert scoring call in `_execute_strategic_actions`; add `_brain_score_prev_pnl` cache; add `_tighten_sl_breakeven_30pct` helper; emit `BRAIN_CLOSE_VOTE_RECEIVED`, `WATCHDOG_CLOSE_SCORE_COMPUTED`, `WD_CLOSE_SCORE_LOG_ONLY`, `WATCHDOG_CLOSE_EXECUTED`, `WATCHDOG_CLOSE_REJECTED`, `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` events.
4. `issue1/p3-4 test: surgical brain-scoring tests` — `tests/test_wd_brain_scoring.py` covering composite calculation, factor edge cases, scenario A/B/C, and one integration smoke that exercises the watchdog path with a fake coordinator/structure_cache.

Phase 2 enforce flip (separate post-validation):
5. `issue1/p3-5 feat(watchdog): flip wd_brain_scoring_enforce default to True` — single-line settings change after operator approves the log-only data.

## Trial Scenarios for Phase 4 Verification

Already enumerated in Phase 1 anatomy report §G. Phase 4 dev_notes will pair real session logs against the SCORE_COMPUTED events.

## Approval

Design follows the operator's stated intent in `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 1 verbatim, with the worked examples (BSBUSDT, HYPEUSDT) used as anchor scenarios. Phase 3 implementation proceeds without further operator gate; the operator's approval gate is the Phase 4 log-only → enforce flip per §K of the prompt.
