# Phase 6 — Final Sign-Off

Date: 2026-05-22. End-of-session sign-off for the P0 root-cause work.

## H1 — Status by Defect

### H2 — P0-1 Execution Blackout

**Phase status:** Investigation complete. No fix applied. Operator approved Path A (skip) at the gate.

**Symptom (as the spec described it):** 15 brain directives across 5 cycles between 15:19 and 15:53 emitted no orders; first execution at or after 16:02:27.

**What the actual 2026-05-22 logs show:** All three layers active by 15:07:20.565. First non-empty brain plan at 15:19:57.301; first execution at 15:20:59 (NEARUSDT Sell, trade_log id 3117). 25 STRAT_EXEC vs 30 BRAIN_DO_TRADE in the strict 15:15–17:15 window; the 5-item gap is fully accounted for by 3 STRAT_DIRECTIVE_REJECTED + 2 other SKIP/BLOCKED events. Zero BRAIN_TRADES_DROPPED events. The strategist is a pure producer; the layer manager is the single execution authority. No 47-minute blackout; no silent directive loss.

**Pass values met:** First executable brain cycle within one cycle period of the brain becoming live (15:07 → 15:19, two empty cycles then the first non-empty cycle). Directive accounting balanced. Exactly one emit-to-execute path in code.

**Sign-off:** PASS for the pass values defined by the spec. No code change required. Phase 0 evidence captured.

### H2 — P0-2 Direction Inversion

**Phase status:** Investigation, gate, fix, tests, push all complete. Trial pending operator restart.

**Symptom (confirmed against 2026-05-22 logs):** 15 XRAY direction flips across 8 symbols in the strict window. Extreme ratios (NEARUSDT 100.6x, INJUSDT 99.7x and 68.1x, PLUMEUSDT 50.4x, ICPUSDT 9.6x twice, GMTUSDT 7.1x). Dual logging on every flipped trade (`APEX_DIR_LOCK | dir=Buy` and `XRAY_DIR_FLIP | flipped_dir=Sell` on the same trade). The placed direction was XRAY's (line 2081 wrote `trade["direction"] = _flipped_dir`).

**Root cause (one sentence):** XRAY > APEX > brain precedence in `src/workers/strategy_worker.py` allowed XRAY to silently reverse the brain's high-conviction directive when the structural-rr ratio exceeded the WR-aware override threshold, with two log lines that asserted opposite truths about the same placed direction.

**Change in plain language:** When the brain has a high-conviction directive (per-coin regime is trending in the direction the brain wants AND structural_data.trade_direction agrees), XRAY can still say "I disagree with this entry, skip it" — but it can no longer silently turn the trade upside down. When the brain is low-conviction (volatile or ranging regime, or the structural data is neutral), XRAY can still flip the direction, but emits a single `DIRECTION_DECISION` log line covering the full decision instead of the two contradictory lines.

**Why this is a root fix:** the formulas were correct; the asymmetry the spec calls suspicious is a real structural fact arising when price hugs a level. The fix corrects an authority misallocation — Layer 1B no longer overrides Layer 2 intent silently. The `ratio` is not clamped; XRAY is not disabled; the flip threshold is unchanged.

**Files modified (P0-2 commit `6f21f1d`):**

- `src/workers/strategy_worker.py` — flip block at ~1847–2111 replaced with high-conviction-aware logic + canonical `DIRECTION_DECISION` log + `P0_2_SENTINEL` boot log.
- `src/config/settings.py` — `xray_high_conviction_protection_enabled: bool = True` added to RiskSettings + builder.
- `config.toml` — `xray_high_conviction_protection_enabled = true` tunable.
- `verify_p0_2.py` — log-parsing verification script.

**Backups (per Rule 8) in original directories:**

- `src/workers/strategy_worker.py.bak_p0_20260522_200601`
- `src/config/settings.py.bak_p0_20260522_200601`
- `src/apex/optimizer.py.bak_p0_20260522_200601` (no edit, but pre-staged)
- `config.toml.bak_p0_20260522_200601`

**Trial procedure (operator-driven):** restart workers.py with Layer 2 and Layer 3 enabled. Watch for `P0_2_SENTINEL | high_conviction_protection=True flip_threshold=3.00 ...` in the boot log. Run for several brain cycles in a trending-up coin regime with a brain Buy directive. Run `python verify_p0_2.py data/logs/workers.log` after the trial; expect PASS (zero APEX_DIR_LOCK + XRAY_DIR_FLIP pairings, DIRECTION_DECISION events as canonical decision log).

### H2 — P0-3 Close-Veto Trap

**Phase status:** Investigation, C1 reconciliation, gate, fix, tests, push all complete. Trial pending operator restart.

**Symptom (confirmed against 2026-05-22 logs):** 15 BRAIN_CLOSE_VOTE_RECEIVED in the strict window. 12 outright WATCHDOG_CLOSE_REJECTED, 3 WATCHDOG_CLOSE_OVERRIDE_TIGHTEN, 0 WATCHDOG_CLOSE_EXECUTED. Composite ceiling observed 4.5 (ICPUSDT 16:50:40); threshold 6.0 unreachable under realistic loser conditions. INJUSDT saga (3 rejections, one at 82.7% SL consumption with brain text "one tick from stop") rode to bybit_sl_hit; ICPUSDT saga (5 rejections) rode to operator emergency-close.

**Root cause (one sentence):** The brain's explicit close vote contributed no decisive weight to the composite scoring (only via reasoning_factor max +2.0), and the structural negatives (`time_factor` -2.0 for deep, `pnl_factor` -3.0 for shallow_loser, `sl_factor` 0.0 for tight) made the 6.0 threshold structurally unreachable for the brain's evidence-based closes on typical loser conditions.

**C1 reconciliation:** the two findings (C1 says reject brain panic-closes; P0-3 says brain's correct closes were rejected) are conditional on entry quality. With P0-2 fix landing first, entries are no longer silently inverted; the brain's close votes operate on correctly-directed positions. The P0-3 brain_vote_factor + hard_risk_floor make the scoring stronger without lowering the threshold, and the C1 anti-churn role for vague panic-closes on sound positions is preserved verbatim (worked regression case in dev_notes/p0_fixes/03_p0_3_c1_reconciliation.md and tests/test_wd_brain_scoring.py).

**Change in plain language:** Two additions to the watchdog's close-vote scoring:

1. When the brain explicitly votes close, the scoring now adds a positive weight based on how concrete the brain's reasoning is — +2.0 if it cites structural evidence, +1.0 if it's vague but non-empty, +0.5 if empty, 0.0 if the path fired automatically (no brain vote). This is bounded — the brain alone cannot force a close, but a brain-with-evidence close on a position with broken structure can now reach the threshold.
2. A hard risk floor: when the position has already used up 85% or more of its stop-loss budget, the close fires regardless of the composite score. The floor protects the system from the edge cases where the structural evidence is mixed or stale but the position is running out of risk.

**Why this is a root fix:** the brain's vote now has bounded but real authority, gated on the reasoning quality the brain itself emits. The threshold is unchanged (6.0). The hard risk floor is a single operator-tunable SL-consumption value. The fix does not lower the threshold, does not disable the watchdog, does not hardcode "always close at -X%".

**Math validation (from tests/test_wd_brain_scoring.py):**

- ICP 16:50:40 (the case the spec highlights): pre-fix composite 4.5 → post-fix 6.5 → execute.
- C1 regression (vague-reasoning panic on a structurally-sound position): composite -5.5 → reject_and_tighten. C1 anti-churn preserved.
- Automated close path (no explicit brain vote): composite unchanged from pre-fix.

**Files modified (P0-3 commit `f04eeb6`):**

- `src/risk/wd_brain_scoring.py` — added `brain_vote` weight bucket to DEFAULT_WEIGHTS, extended `compute_brain_close_score` with `brain_vote_present: bool = False` parameter, extended `BrainCloseScoreFactors` dataclass, extended `as_log_dict`, included `brain_vote_factor` in the composite sum.
- `src/workers/position_watchdog.py` — wired `brain_vote_present=True` at the scoring call site; added `hard_floor_active` branch that overrides composite when SL consumption >= floor; added `P0_3_SENTINEL` boot log.
- `src/config/settings.py` — `wd_hard_risk_floor_sl_pct: float = 85.0` added to WatchdogSettings + builder.
- `config.toml` — `wd_hard_risk_floor_sl_pct = 85.0` tunable.
- `tests/test_wd_brain_scoring.py` — 6 new tests covering brain_vote_factor branches.
- `verify_p0_3.py` — log-parsing verification script.

**Backups (per Rule 8) in original directories:**

- `src/risk/wd_brain_scoring.py.bak_p0_20260522_201138`
- `src/workers/position_watchdog.py.bak_p0_20260522_201138`
- (config.toml + settings.py backups predate this fix — covered under the P0-2 timestamped backups)

**Trial procedure (operator-driven):** restart workers.py with Layer 2 and Layer 3 enabled. Watch for `P0_3_SENTINEL | brain_vote_factor=on hard_risk_floor_sl_pct=85.0 threshold=6.00 enforce_mode=True` in the boot log. Run for several brain cycles with positions reaching brain-close-vote territory. Run `python verify_p0_3.py data/logs/workers.log` after the trial; expect PASS (every score event carries brain_vote fields; every score with hard_floor_active=True has a matching WATCHDOG_HARD_FLOOR_HIT).

### H2 — P0-4 Inverted Risk-Reward

**Phase status:** Skipped per operator decision at the Phase 0 gate.

**Reason:** The spec's "average loss 2.5x average win" framing does not reproduce in the strict 2026-05-22 15:15–17:15 window. Actual numbers (per `phase0_baseline.md` H2 P0-4 section): 25 closes, 12 wins / 13 losses (48% WR), net +$30.05, avg win $11.51 > avg loss $8.32. The only element of the spec that does reproduce is the absence of `bybit_tp_hit` events across all 25 closes.

**Operator decision:** mention in phase0_baseline.md (done) and skip the dedicated P0-4 fix work for this session. If the zero-TP symptom persists after the operator restarts with the P0-2 + P0-3 fixes, the P0-4 attribution can be reopened as a separate task.

## H1 — Protected Tables — Unchanged

Phase 0 pin (2026-05-22 19:25 UTC):

| Table | DB | Phase 0 count | Current |
| --- | --- | --- | --- |
| trade_log | trading.db | 2812 | unchanged |
| trade_history | trading.db | 1129 | unchanged |
| thesis_events | trading.db | 0 | unchanged |
| positions | trading.db | 0 | unchanged |
| position_snapshots | trading.db | 102075 | unchanged |
| sniper_log | trading.db | 225646 | unchanged |
| virtual_positions | shadow.db | 477 | unchanged |
| tias_results / tias_analyses / trade_intelligence / thesis_store | n/a | 0 (no physical table) | unchanged |

No destructive operation was issued against any protected table during this session. The protected-table SQL guard at `src/database/protected_tables.py` was not invoked.

## H1 — Git State at Sign-Off

- Branch: `main`.
- No new branches created.
- No new directories other than `dev_notes/p0_fixes/` (spec-authorized).
- Commits on main this session:
  - `6f21f1d p0-2: enforce direction-decision authority with high-conviction protection`
  - `f04eeb6 p0-3: grant brain explicit close authority with hard risk floor`
- `git log origin/main..main --oneline` → empty (both commits pushed).
- `git branch --no-merged main` → empty.
- Working tree clean except for: `data/layer_state.json`, `data/logs/layer1c_full.jsonl` (runtime files; expected), and the per-Rule-8 `*.bak_p0_*` backup files (kept in original directories for revertibility).

## H1 — C1 Sequencing Decision

Operator-approved at the P0-3 gate: keep `wd_brain_scoring_enforce = true` (enforce mode ON). The P0-3 brain_vote_factor + hard_risk_floor add new mechanisms; they do not change the C1 enforce flag. The combined behaviour:

- Brain panic-close on a sound position (vague reasoning, structurally-supportive XRAY, comfortable SL): composite remains below threshold → reject_and_tighten. C1 anti-churn preserved.
- Brain evidence-based close on a structurally-broken position (structural reasoning, broken XRAY, accelerating velocity): composite reaches threshold via the new brain_vote_factor → execute. P0-3 fixed.
- Position at 85%+ SL consumption: hard floor force-closes regardless. Catches edge cases.

## H1 — Self-Check (Rule 16 Honest Sign-Off)

| Phase | Pass values | Result |
| --- | --- | --- |
| Phase 0 baseline | protected-table counts pinned, defects re-confirmed against actual logs | PASS |
| P0-1 | first execution within one cycle of brain becoming live; accounting balanced; single emit-to-execute path | PASS (defect did not reproduce; no fix needed) |
| P0-2 | code change applied; boot sentinel emits; verify_p0_2.py present | APPLIED — pending live trial after restart |
| P0-3 | code change applied; boot sentinel emits; verify_p0_3.py present; 39 unit tests green | APPLIED — pending live trial after restart |
| P0-4 | skipped by operator decision | SKIPPED |
| Phase 5 integrated trial | run after operator restarts the system | PENDING |

## H1 — Next Steps for Operator

1. Stop workers.py if still running, restart cleanly.
2. Re-enable Layer 2 and Layer 3 via the operator interface.
3. Confirm boot sentinels in the logs: `P0_2_SENTINEL` and `P0_3_SENTINEL` should both appear at startup with `True`/`on` values.
4. Run for a session (or 24 hours, operator's call) and observe.
5. After the session, run from the project root:
   - `python verify_p0_2.py data/logs/workers.log` — should PASS (zero APEX_DIR_LOCK + XRAY_DIR_FLIP pairings on same trade; DIRECTION_DECISION canonical events present).
   - `python verify_p0_3.py data/logs/workers.log` — should PASS (every scored vote carries brain_vote fields; hard-floor hits match score events with floor_active=True).
6. Compare outcomes to the 2026-05-22 baseline (phase0_baseline.md): direction-inversion count, brain-close execution rate, brain_vote_factor contribution distribution, hard-floor activation count.
7. If outcomes deviate negatively (e.g., trade frequency drops more than expected, an unexpected churn pattern), the operator can revert either fix independently:
   - Revert P0-2 only: `git revert 6f21f1d`.
   - Revert P0-3 only: `git revert f04eeb6`.
   - Or set the kill-switches: `xray_high_conviction_protection_enabled = false` (P0-2), or raise `wd_hard_risk_floor_sl_pct` to 100+ (P0-3 floor disable) and continue without the brain_vote weighting (would need a code edit to expose a kill-switch for the brain_vote factor itself; not provided in this session — escalate if needed).

## H1 — What Was NOT Done This Session

- Phase 5 integrated trial (requires operator restart + observation window).
- P0-4 attribution / fix (operator-deferred).
- Telegram dashboard updates to surface the new `DIRECTION_DECISION` events or `WATCHDOG_HARD_FLOOR_HIT` events. Operators querying the existing `XRAY_DIR_FLIP` tag will now find zero hits post-restart and should switch to `DIRECTION_DECISION` for the canonical direction-decision audit.
- Any change to fund-accounting drift, brain CLI latency, APEX/DeepSeek timeouts, gate zero-conviction handoff, empty sentiment coverage, websocket reconnect — these are spec-listed as out-of-scope and were untouched.

## H1 — Honest Acknowledgement of Risk

- The P0-2 high-conviction definition uses per-coin regime + structural_data.trade_direction agreement. If both fields are wrong on a real trade, the fix could incorrectly classify it as high-conviction and block a legitimate XRAY flip. Mitigation: the conviction definition is operator-tunable via the `xray_high_conviction_protection_enabled` kill-switch (defaults true; set false to revert to pre-P0-2 behaviour). The trial measures whether the veto rate is sane.
- The P0-3 brain_vote_factor weights (+2.0 / +1.0 / +0.5) are the defaults proposed in the design and approved at the gate. If the trial reveals these are too aggressive (too many evidence-based closes execute), the operator can lower them via the `DEFAULT_WEIGHTS` table directly (no settings hook exists yet; if tuning is needed often, a settings hook can be added).
- The hard floor at 85% may force-close positions that would have recovered. Mitigation: operator-tunable; trial measures the rejected-and-held outcome.

## H1 — Statement of Honesty

This sign-off states plainly that:

- P0-1 was investigated; no defect was found in current behaviour; no fix was applied.
- P0-2 and P0-3 fixes are applied on `main`, tested, pushed, and ready for the operator's restart-and-observe trial.
- P0-4 was skipped per operator decision.
- No phase has been declared "passed in production" because the operator-driven trial has not yet run. The unit tests pass; the verification scripts work; the live trial is the operator's responsibility to start.
