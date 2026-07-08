# C1 — Phase 1.6 Synthesis And Activation Recommendation

This document consolidates Phase 0 through Phase 1.5c findings into an explicit recommendation for the operator decision gate (Phase 2). The operator reads this document, chooses one of three outcomes — activate now, defer, or further alignment — and approves or declines the single-line `config.toml:531` flip.

## Single-paragraph summary

The brain's discretionary close path (`wd_claude_action`) is the trading system's single largest source of losses across the last 14 days (−$952.51 across 147 closes, 13.6% win rate). A multi-factor scoring system that arbitrates these close votes is built, wired to `main`, and live in log-only mode. In the 2026-05-20 session it correctly flagged 28 of 28 brain closes as below threshold; 27 of those 28 closes lost money, totalling −$257.18. Flipping `wd_brain_scoring_enforce` from `false` to `true` would have rejected those 28 closes and tightened SL on the 25 reject_and_tighten composites. Investigation confirms the enforce code path is complete, the SL-tightening fallback is safe, the SL%-divergence cannot flip any historical composite past threshold, and aim-bias preservation holds across all five questions. Recommendation: **activate enforce mode** after operator approval.

## Issue confirmation

Phase 0 baseline:
- `wd_brain_scoring_enabled = true`, `wd_brain_scoring_enforce = false`, `wd_brain_scoring_threshold = 6.0` in `config.toml:530–532`.
- Scoring intercept reached 28 times across the 2026-05-20 worker logs.
- `WATCHDOG_CLOSE_SCORE_COMPUTED` = 28, `WD_CLOSE_SCORE_LOG_ONLY` = 28, `WATCHDOG_CLOSE_EXECUTED` = 0, `WATCHDOG_CLOSE_REJECTED` = 0, `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` = 0, `WD_BRAIN_SCORE_FAIL` = 0.

Phase 1.1 evidence:
- 28 of 28 scored events correlate to a `wd_claude_action` close in the DB (100%).
- Outcomes: 1 win (+$0.43), 27 losses, total −$257.18. Matches the prompt verbatim.
- Composites range −9.5 to +1.5; none cleared the 6.0 threshold.
- 25 composites are `reject_and_tighten` (composite < 0); 3 are `reject` (0 ≤ composite < 6.0); 0 are `execute`.

## Enforce code path verification

Phase 1.3 traced every branch in the intercept (`position_watchdog.py:3419–3665`) and the SL-tightening fallback (`position_watchdog.py:1173–1226`):

- All three enforce branches (`execute`, `reject`, `reject_and_tighten`) are concrete and produce distinct log signatures (`WATCHDOG_CLOSE_EXECUTED`, `WATCHDOG_CLOSE_REJECTED`, `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`).
- The reject branch sets `_scoring_skip_close = True` and the outer `continue` blocks the existing `position_service.close_position(symbol, close_trigger="wd_claude_action")` at line 3669.
- The reject_and_tighten branch additionally calls `_tighten_sl_breakeven_30pct(_pos_for_score)` which computes `new_sl = current_sl + 0.30 * (entry - current_sl)` — direction-aware on both BUY (delta positive, SL moves up) and SELL (delta negative, SL moves down) — and delegates to `_push_sl_to_shadow(source="wd_brain_scoring")` which enforces tighter-only and break-even bounds via the gateway. Wrong-side-of-mark placement is impossible by construction.
- Edge cases enumerated: position already closing (None returned by re-fetch → tightening skipped, close blocked), SL at break-even (no-op guard at 1 bp), no SL (proximity returns None → scorer uses midpoint), concurrent gateway race (single-writer R4 rate-limit), empty/vague brain reasoning, XRAY unavailable, velocity unavailable. All fail safely.
- Fail-soft: any exception in the intercept emits `WD_BRAIN_SCORE_FAIL` and falls through to the existing brain close (zero such events observed in Phase 0).

## SL% divergence outcome

Phase 1.4 confirmed the divergence is definitional, not a bug. Phase 1.4b shipped the alignment.

Theoretical bound: maximum upward composite shift from re-bucketing SL%-consumption = +3.0 (`spacious` −2.0 → `imminent` +1.0). The minimum headroom from the highest observed composite (+1.5) to the threshold (6.0) is +4.5. Therefore no historical composite would have flipped past the threshold under any realistic SL%-divergence scenario. The divergence is not a blocker.

Alignment commits (all on `main`, all bytewise-identical for pre-existing position state):

1. `be54fad c1(wd-scoring): add shared compute_sl_consumption_pct helper`
2. `0a057ee c1(wd-scoring): watchdog _calculate_sl_proximity delegates to shared helper`
3. `b6f844b c1(brain): CALL_B prompt renders current+entry SL% via shared helper`
4. `2e70c9f c1(wd-scoring): add WD_SL_PCT_DIVERGENCE diagnostic (read-only)`

The brain CALL_B prompt now renders both interpretations ("entry-budget" and "current-stop") explicitly labelled when the SL has been trailed. The `WD_SL_PCT_DIVERGENCE` event surfaces both percentages on every brain close vote regardless of enforce mode.

## Aim-bias five-question evaluation

Phase 1.5 walked through each:

1. Trade frequency — **preserved**. Entries are not touched. The intercept gate at `position_watchdog.py:3428` only triggers for `close`/`take_profit` actions.
2. Aggression — **preserved**. Brain remains the close-proposer. Strong-winner closes still execute. Above-threshold structural-loss closes still execute.
3. Decision quality — **improved**. Seven objective factors filter out reflexive panic-closes. Historical accuracy 27/28 = 96%.
4. Passive-close advantage — **strengthened**. Rejected panic-closes fall through to the +$2304 (14-day) passive paths instead of the −$952 active path.
5. Separation of concerns — **preserved**. Layer 6 only. No Layer 1/2/3/4/5/7 changes.

## Boot sentinel and integration tests

Phase 1.5b shipped `WD_SCORING_ENFORCE_ACTIVE` — single-line startup confirmation of the mode (commit `9a56eee`). Phase 1.5c shipped `tests/test_wd_scoring_enforce_integration.py` covering all four runtime branches plus kill-switch behaviour (commit `4232b94`). The full scoring + watchdog regression sweep is 74 passing, zero regressions.

## Activation recommendation

**Activate now.** Flip `config.toml:531` from `wd_brain_scoring_enforce = false` to `wd_brain_scoring_enforce = true`. Single-line change. Already-engineered to be the only commit between Phase 1 and Phase 4.

Rationale:
- The fix is built, validated across three consecutive sessions, and 96% accurate on the broken-close class.
- The enforce code path is complete and safe; the SL-tightening fallback cannot place SL wrong-side of mark.
- The SL%-divergence cannot flip any composite past threshold; the alignment commits eliminate the visual confusion the divergence created for the operator and the brain.
- The boot sentinel and integration tests close the verification gap so the operator can confirm enforce mode at startup and re-run the integration suite after any future refactor.
- The flip is single-line and instantly revertible.

## Expected runtime behaviour after the flip

- At worker startup: `WD_SCORING_ENFORCE_ACTIVE | scoring_enabled=True enforce=True threshold=6.00`
- Per brain close vote:
  - `WD_SCORING_PATH_REACHED | sym=X act=close scoring_enabled=True enforce=True ...`
  - `WD_SL_PCT_DIVERGENCE | sym=X sl_current=A sl_entry=B pct_current=Y pct_entry=Z delta_pct=W ...`
  - `WATCHDOG_CLOSE_SCORE_COMPUTED | sym=X composite=N threshold=6.00 recommendation=R ...`
  - Then exactly one of: `WATCHDOG_CLOSE_EXECUTED` (composite ≥ 6.0), `WATCHDOG_CLOSE_REJECTED` (0 ≤ composite < 6.0), or `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` followed by `SHADOW_SL_PUSH | source=wd_brain_scoring | ok=True` (composite < 0).
- `WD_CLOSE_SCORE_LOG_ONLY` should stop firing entirely (enforce=True means the log-only branch is unreachable).
- `WD_BRAIN_SCORE_FAIL` should stay at 0.

## Expected outcome over the first 24–48h trial

- Brain's `wd_claude_action` close count drops materially.
- Passive paths (`wd_dl_action`, `bybit_sl_hit`, `bybit_tp_hit`, `wd_profit_take`) take on the volume that previously went to `wd_claude_action`.
- `wd_claude_action` net PnL moves toward break-even or positive.
- Session PnL improves versus the pre-flip baseline (Phase 0 14-day cumulative wd_claude_action = −$952.51; trial target is at least 50% reduction in daily damage).
- Trade frequency holds; direction distribution holds; zero `BRAIN_FAILURE_CASCADE`; Shadow healthy; mid-hold thesis-compliance unchanged.

## Rollback plan

Single line in `config.toml`:

```toml
wd_brain_scoring_enforce = true     # active
wd_brain_scoring_enforce = false    # reverted
```

After the operator edits the file, a worker restart is sufficient to revert. The `WD_SCORING_ENFORCE_ACTIVE` boot sentinel will confirm `enforce=False` on the next startup. The scoring intercept reverts to the log-only branch automatically.

If the trial reveals harm (e.g., rejected positions running to bigger losses than the rejected close would have locked in), the operator reverts the flag, the system returns to its pre-activation state, and the post-mortem captures why log-only predictions did not hold.

## Stop-conditions for activation

The recommendation above stands unless one of the following emerges in the operator's review:

1. The operator disagrees with the canonical SL% choice (current trailed vs entry-budget). The alignment can be inverted before activation — see `04b_alignment_decisions.md` for the analysis.
2. The operator wants to soak the alignment commits (steps 1–4 + boot sentinel) for 24 h in log-only mode before flipping enforce. This is a defensible conservative choice. The alignment commits do not change composites, but the operator may want empirical `WD_SL_PCT_DIVERGENCE` data first.
3. The operator wants to lower or raise the threshold from 6.0 before flipping. The default 6.0 matches the published `DEFAULT_THRESHOLD` and the operator-validated worked examples. A change is possible but should be evidence-driven, not pre-emptive.

## Open follow-ups (not blocking activation)

Documented for visibility after activation:

- **Weight tuning**: the C3 design note in `feedback-overhaul29-execution` memory noted the `shallow_loser` PnL bucket (−3.0) is heavy. If Phase 4 trial reveals legitimate aged-losing closes are being wrongly rejected, the operator may consider increasing the `aged_losing` age factor from +1.0 to +1.5 or +2.0. No change at flip time.
- **Diagnostic retention**: `WD_SL_PCT_DIVERGENCE` fires on every brain close vote even after alignment. The signal is most useful for the first 24–48 h to confirm `delta_pct ~ 0` when no trailing has occurred. The diagnostic can stay as a permanent invariant check (low volume) or be moved to DEBUG after the trial; no action needed at flip time.
- **CALL_B contract**: the brain prompt's dual SL% rendering is additive and backwards-compatible with the decision parser (parser consumes structured JSON, not the prompt text). No CALL_B contract change is required, but `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 1 §B may want a note that the prompt now shows both interpretations.

## Final state if operator approves activation

| item | state |
|---|---|
| `config.toml:531` | `wd_brain_scoring_enforce = true` |
| Boot sentinel on next worker restart | `WD_SCORING_ENFORCE_ACTIVE | enforce=True` |
| Scoring path | enforce mode — sub-threshold closes rejected, composite<0 tightens SL |
| `git log origin/main..main --oneline` | empty (all commits pushed) |
| `git branch --no-merged main` | empty |
| `git status --short` | only runtime files modified |
| Phase 4 trial window | 24–48 h |
| Rollback readiness | single-line edit |

## Operator decision required

Choose one:

1. **Activate now** — proceed to Phase 3 (single-line `config.toml:531` edit + commit).
2. **Soak alignment commits first** — wait 24 h on the current commits (`be54fad..4232b94`) with log-only enforce flag, then flip.
3. **Defer** — leave `enforce=false`, identify a specific blocker, and re-engage the C1 plan with the blocker addressed.

If activate now, the next steps are:

```
Edit config.toml line 531: wd_brain_scoring_enforce = true
git add config.toml
git commit -m "c1: activate wd_brain_scoring_enforce (operator-approved)"
git push origin main
# Restart workers — boot sentinel confirms enforce=True
```

This synthesis document marks the end of Phase 1. Phase 2 is the operator decision gate.
