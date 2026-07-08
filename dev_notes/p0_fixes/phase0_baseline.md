# Phase 0 — Pre-Flight Baseline and Defect Re-Confirmation

Date: 2026-05-22. Performed against `main` (commit `3ab0c06`).

## Goal

Confirm working state, record protected-table row counts, identify the log window for the 2026-05-22 incident, and re-confirm whether each of the four defects still reproduces in current code and current logs. Per Rule 14, a defect that does not reproduce is documented and escalated rather than fixed.

## H1 — Working Tree State

Branch `main` is up to date with `origin/main`. The working tree is clean except for two runtime files: `data/layer_state.json` and `data/logs/layer1c_full.jsonl`. Both are runtime-written artifacts of the running process; neither carries source changes. No unpushed commits on `main`. No unmerged branches.

## H1 — Process State

The trading process is **partially running**:

- PID 400: `workers.py` (started 2026-05-22 15:07 UTC) — alive.
- PID 401: `server.py --transport sse --port 8080` (started 15:07 UTC) — alive.
- PID 390: `/home/inshadaliqbal786/shadow/.venv/bin/python shadow.py` — alive.
- pm2 reports only `n8n` (stopped), unrelated.

However, the layer state currently reads `{1: True, 2: False, 3: False}` (LAYER_STATE_SYNC, latest sample 2026-05-22 19:26:16). Layers 2 (Brain) and 3 (Execution) were toggled off at 2026-05-22 17:14:56 by the emergency-close action; Layer 1 (data flow) is still active and the watchdog ticks in `mode=safety_net` against zero positions. This matches the operator's stated "stopped" — trading is stopped, data ingestion continues for telemetry purposes.

Implication: code changes can land on `main` without affecting trading. A trial requires the operator to re-enable layers 2 and 3 after a restart.

## H1 — Adapter Configuration

`config.toml:22` sets `mode = "bybit_demo"`. This matches the incident-window adapter. The bybit_demo adapter block at `config.toml:32` is configured with `base_url = "https://api-demo.bybit.com"`.

## H1 — Capital Pool

The latest `FUND_POOLS` log line at 2026-05-22 19:26:08 reads `cap=88929.14 | available=88929.14 | in_use=0.00`. This matches the spec's statement that "the session pool was about 88,900 units, not the documented 100." All P0-4 PnL math is to be grounded in this pool size, not the placeholder `initial_capital = 10000` at `config.toml:1067` (which is a per-strategy allocation, not the total pool).

There is a separate, persistent reconciliation drift `FUND_INUSE_DRIFT | diff=-9928.92 streak=259 action=alert_only` (Bybit reports 79000.22 in-use while local says 88929.14 in-use). This is a pre-existing fund-accounting issue listed in the spec's Part H as out of scope.

## H1 — Protected-Table Row Counts (baseline pins)

The Trading Intelligence MCP system uses two databases: the main `data/trading.db` and the Shadow virtual exchange's `/home/inshadaliqbal786/shadow/data/shadow.db`. The PROTECTED_TABLES set defined at `src/database/protected_tables.py:47-56` names tables that are protected against destructive operations. Some names in that set do not exist as physical tables in either DB (renamed during prior schema work) but the protection guard still triggers on the SQL string.

Pinned counts as of 2026-05-22 19:25 UTC:

| Table | Database | Row count |
| --- | --- | --- |
| `trade_log` | trading.db | 2812 |
| `trade_history` | trading.db | 1129 |
| `thesis_events` | trading.db | 0 |
| `positions` | trading.db | 0 |
| `position_snapshots` | trading.db | 102075 |
| `sniper_log` | trading.db | 225646 |
| `virtual_positions` | shadow.db | 477 |
| `tias_results` | n/a | table does not exist (protected-name guard still active) |
| `tias_analyses` | n/a | table does not exist |
| `trade_intelligence` | n/a | table does not exist |
| `thesis_store` | n/a | table does not exist |

Sign-off (Phase 6) will compare these counts against the post-trial state and confirm no loss.

## H1 — Log File Identification (incident window 15:15–17:15 UTC)

Two log files cover the 2026-05-22 incident window:

- `data/logs/workers.2026-05-21_12-38-37_846444.log` (rotated at 2026-05-22 15:55) — covers 15:15–15:55.
- `data/logs/workers.log` (current, 35,703 lines as of capture) — covers 15:55–17:15+.

Both are pinned as evidence sources for this work. No log deletion is permitted by verification scripts (Rule 13).

## H1 — C1 Activation Chronology

The `wd_brain_scoring_enforce` flag is currently `true` at `config.toml:531`. Commit history confirms `3bfb5e4 c1: activate wd_brain_scoring_enforce (operator-approved 2026-05-21)` activated enforce mode. Boot sentinels in the incident-window logs confirm the active state:

- `2026-05-22 07:09:11.244 | WD_SCORING_ENFORCE_ACTIVE | scoring_enabled=True enforce=True threshold=6.00`
- `2026-05-22 15:07:15.452 | WD_SCORING_ENFORCE_ACTIVE | scoring_enabled=True enforce=True threshold=6.00`

The 2026-05-22 session is the first production day with enforce mode live. The defect P0-3 evidence below was generated under enforce-on conditions.

## H1 — Defect Re-Confirmation Against Actual Logs

### H2 — P0-1 Execution Blackout — DOES NOT REPRODUCE

**Spec claim:** "The brain produced fifteen new-trade directives across five cycles between 15:19 and 15:53 ... None executed. The first execution event of any kind ... appears at or after 16:02:27. Thesis row ids for the first executed trades begin at 3150 ... The fifteen directives simply vanished with no skip event and no rejection event."

**What the actual logs show:**

Layer toggles at session start, from the logs:

- `2026-05-22 15:07:16.554 | LAYER_TOGGLE | layer=1 from=False to=True reason=unspecified actor=system`
- `2026-05-22 15:07:18.561 | LAYER_TOGGLE | layer=2 from=False to=True reason=unspecified actor=system`
- `2026-05-22 15:07:20.565 | LAYER_TOGGLE | layer=3 from=False to=True reason=unspecified actor=system`

All three layers active by 15:07:20.565. Layer 3 (the executor gate at `src/core/layer_manager.py:807`) was active throughout the 15:15–17:15 window.

Brain cycles in the 15:00–17:15 window:

- 15:07:18 — `BRAIN_CYCLE_A_DONE status=empty_plan trades=0` — brain returned no ideas.
- 15:12:18 — `BRAIN_CYCLE_A_DONE status=empty_plan trades=0` — brain returned no ideas.
- 15:19:57 — `BRAIN_CYCLE_A_DONE status=success trades=3` — first non-empty plan. `BRAIN_DO_START | trades=3` fired immediately at 15:19:57.303.
- 15:20:59 — first `STRAT_EXEC | sym=NEARUSDT dir=Sell` and first `BYBIT_DEMO_ORD_SEND` (link_id=bd-NEARUSDT-S-1779463259256).
- Subsequent cycles at 15:28:34, 15:35:56, 15:44:01, 15:53:45, 16:02:06, 16:10:15, 16:19:21, 16:27:25, 16:39:15 (empty), 16:47:36, 16:57:01, 17:06:29, 17:14:56 (cancelled by emergency).

The first trade was placed at 15:20:59.555 (trade_log id 3117, NEARUSDT Sell). Subsequent thesis IDs in the window run from 3116 onward, not "from 3150" as the spec asserts. There is no 47-minute blackout.

Directive accounting in the strict 15:15–17:15 window:

- Brain cycles producing trades: 8 (counts 3+3+3+3+2+2+2+3+2+2 — multiple cycles, all executed).
- `BRAIN_DO_TRADE`: 30 events.
- `STRAT_EXEC`: 25 events.
- `BRAIN_TRADES_DROPPED`: **0** events.
- `STRAT_DIRECTIVE_REJECTED`: 3 events (sample: `2026-05-22 15:54:47.924 | sym=BSBUSDT dir=Sell rsn=sltp_skip detail='strategy_worker rejected: sltp_skip' blocker_layer=strategy_worker`).
- Other SKIP/BLOCKED events (`STRAT_EXEC_SKIP`, `STRAT_EXEC_BLOCKED`, `TRADE_SKIP`, `XRAY_BLOCK`): 5 events combined.
- Difference `BRAIN_DO_TRADE - STRAT_EXEC = 5` matches the 5 skip/block events. Accounting is balanced.

The 15:07 and 15:12 empty-plan cycles are the brain's normal warmup, not a directive drop. The system was correctly idle during those cycles because the brain produced no new-trade directives in them.

**Conclusion:** P0-1 as written in the spec does not reproduce in the actual 2026-05-22 logs. The "47-minute blackout" appears to be an artifact of the prior monitoring report that the spec itself describes as "demonstrably wrong" (page 1, paragraph 1). The current code's executor wiring is working as designed.

**Escalation required.** Per Rule 14, I will pause at the P0-1 decision gate and present this finding before any P0-1 code change. The operator may choose to (a) skip P0-1 (no defect to fix), (b) point to an earlier session where the blackout truly reproduced and we re-baseline, or (c) ask me to investigate the cold-start orchestration anyway because the silent-drop class of bug remains worth defending against even if this specific 47-minute case is a red herring.

### H2 — P0-2 Direction Inversion — REPRODUCES STRONGLY

**Spec claim:** "XRAY flipped the direction from Buy to Sell on six of six Buy directives that reached execution, with extreme and consistent ratios: ICP at 9.6 ... INJ at 68.1 ... GMT at 7.1 ... ICP again at 9.6, INJ again at 68.1, PLUME at 50.4."

**What the actual logs show:**

`XRAY_DIR_FLIP` count by symbol in the 15:00–16:59 window:

- NEARUSDT: 3 flips
- INJUSDT: 4 flips
- AVAXUSDT: 1 flip
- BSBUSDT: 2 flips
- FILUSDT: 1 flip
- GMTUSDT: 1 flip
- ICPUSDT: 2 flips
- PLUMEUSDT: 1 flip

Total: 15 flips, broader than the spec's "six".

Concrete cases (Buy→Sell with extreme ratios) confirmed:

- `2026-05-22 15:20:58.944 | XRAY_DIR_FLIP | sym=NEARUSDT original_dir=Buy flipped_dir=Sell rr_original=0.1 rr_flipped=14.1 ratio=100.6x`
- `2026-05-22 15:20:59.866 | XRAY_DIR_FLIP | sym=INJUSDT original_dir=Buy flipped_dir=Sell rr_original=0.1 rr_flipped=9.0 ratio=99.7x`
- `2026-05-22 16:02:27.202 | XRAY_DIR_FLIP | sym=ICPUSDT original_dir=Buy flipped_dir=Sell rr_original=0.2 rr_flipped=2.1 ratio=9.6x`
- `2026-05-22 16:20:22.949 | XRAY_DIR_FLIP | sym=INJUSDT original_dir=Buy flipped_dir=Sell rr_original=0.1 rr_flipped=6.8 ratio=68.1x`
- `2026-05-22 16:20:24.098 | XRAY_DIR_FLIP | sym=GMTUSDT original_dir=Buy flipped_dir=Sell rr_original=0.5 rr_flipped=3.2 ratio=7.1x`
- `2026-05-22 16:28:27.908 | XRAY_DIR_FLIP | sym=ICPUSDT original_dir=Buy flipped_dir=Sell rr_original=0.2 rr_flipped=2.1 ratio=9.6x`
- `2026-05-22 16:48:36.941 | XRAY_DIR_FLIP | sym=INJUSDT original_dir=Buy flipped_dir=Sell rr_original=0.1 rr_flipped=6.8 ratio=68.1x`
- `2026-05-22 16:58:03.037 | XRAY_DIR_FLIP | sym=PLUMEUSDT original_dir=Buy flipped_dir=Sell rr_original=0.2 rr_flipped=10.1 ratio=50.4x`

Dual-logging confirmed on the same trades — example for ICPUSDT 16:02:27:

- `2026-05-22 16:02:06.955 | APEX_DIR_LOCK | sym=ICPUSDT dir=Buy regime=volatile reason='composite_score=-1.12_below_0.0'`
- `2026-05-22 16:02:26.079 | APEX_DIR_LOCK_OVERRIDE | sym=ICPUSDT qwen_tried=Sell locked_to=Buy regime=volatile`
- `2026-05-22 16:02:27.201 | XRAY_LOCK_PRECEDENCE_RESOLUTION | sym=ICPUSDT ratio=9.6x flip_threshold=3.0 override_threshold=4.3 action=override`
- `2026-05-22 16:02:27.201 | XRAY_OVERRIDE_LOCK | sym=ICPUSDT dir=Buy ratio=9.6x rr_long=0.2 rr_short=2.1 override_threshold=4.3 lock_reason='composite_score=-1.12_below_0.0' | structural RR overrides APEX lock`
- `2026-05-22 16:02:27.202 | XRAY_DIR_FLIP | sym=ICPUSDT original_dir=Buy flipped_dir=Sell rr_original=0.2 rr_flipped=2.1 ratio=9.6x`

The placed-order direction at `src/workers/strategy_worker.py:2081` is Sell (the XRAY-flipped value), confirming XRAY > APEX precedence in code. The two log lines (`APEX_DIR_LOCK` and `XRAY_DIR_FLIP`) are emitted independently by the two components, both correctly logging their internal decision; the placed direction is XRAY's.

The systematic 0.1-to-0.5 long-rr vs 2-to-14 short-rr asymmetry is present in every Buy directive, across many symbols and regimes (including trending_up regimes where the brain's Buy directive is regime-aligned). The Hypothesis B.3 ("the long and short risk-reward computation is biased") therefore has strong support and the Phase 2 investigation must read the structure engine to determine whether the formulas, the level-role selection, or the buffer/clamp parameters are the cause.

**Conclusion:** P0-2 reproduces. Investigation proceeds as planned.

### H2 — P0-3 Close-Veto Trap — REPRODUCES STRONGLY

**Spec claim:** "The brain voted to close positions twelve times during the session, and the watchdog rejected every one because its composite score never reached the threshold of 6.0. The observed composite scores were 1.0, 2.0, 3.0, 4.0, 4.5, and 5.0 — never the threshold."

**What the actual logs show:**

Counts in the 15:00–17:15 window:

- `BRAIN_CLOSE_VOTE_RECEIVED`: 15
- `WATCHDOG_CLOSE_SCORE_COMPUTED`: 15 (matches votes)
- `WATCHDOG_CLOSE_REJECTED`: 12
- `WATCHDOG_CLOSE_EXECUTED`: 0
- `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`: 3 (composite < 0 → reject_and_tighten SL)

The "12 rejections" matches the spec. The remaining 3 votes were `reject_and_tighten`, i.e., still not executed.

Observed composite-score values: 0.5, 1.0, 2.0, 3.0, 4.5, -6.0. The maximum observed composite is **4.5** (ICPUSDT at 16:50:40). The spec quoted a range up to 5.0; 4.5 is consistent.

INJ saga (3 votes at 15:56, 16:02, 16:05) confirmed:

- `15:56:43 | BRAIN_CLOSE_VOTE_RECEIVED | sym=INJUSDT rsn='CLOSE — 76% SL consumed at -0.79% in only 11min...'`
- `15:56:43 | WATCHDOG_CLOSE_SCORE_COMPUTED | composite=1.0 ... pnl_pct=-0.7493 pnl_bucket=moderate_loser pnl_factor=-1.0 time_factor=-2.0 age_factor=0.0 velocity_factor=0.0 sl_pct=72.0 sl_bucket=tight sl_factor=0.0 xray_factor=2.0 reasoning_factor=2.0`
- `16:02:14 | WATCHDOG_CLOSE_SCORE_COMPUTED | composite=3.0 ... pnl_pct=-0.8515 sl_pct=81.8 sl_bucket=imminent sl_factor=1.0 velocity_factor=1.0`
- `16:05:19 | WATCHDOG_CLOSE_SCORE_COMPUTED | composite=2.0 ... pnl_pct=-0.8609 sl_pct=82.7 sl_bucket=imminent sl_factor=1.0` — brain reason text: "CRITICAL: SL consumed 85%, price at 5.33 vs SL 5.34 — one tick away. Down -0.89%"

ICP saga (votes at 16:19, 16:47, 16:50, 16:57, 17:00) confirmed — five votes rejected. Sample at 16:50:40 (composite=4.5, the ceiling):

- `pnl_pct=-1.8615 pnl_bucket=deep_loser pnl_factor=0.5 time_factor=-2.0 age_factor=0.0 velocity_factor=2.0 sl_pct=74.6 sl_bucket=tight sl_factor=0.0 xray_factor=2.0 reasoning_factor=2.0` → composite 0.5-2.0+0.0+2.0+0.0+2.0+2.0=4.5.

The arithmetic shows the structural reason 4.5 is the ceiling for this position state: pnl_factor at deep_loser is only +0.5, sl_factor at "tight" (60-80%) is 0.0, time_factor at "deep" (>20 min) is -2.0. To reach 6.0, the position would need either pnl_factor ≥ +2.0 (only possible at strong_winner, +3.0) or simultaneous sl_factor=+1.0 (imminent >80%) AND age_factor=+1.0 (aged_losing >30 min in loss) AND time_factor not at "deep" (i.e., time-to-deadline <20 min). The combinations that reach 6.0 in real loser conditions are narrow.

**Conclusion:** P0-3 reproduces. The composite math is consistent with the spec's worked example in B.4. The brain's explicit vote weight is zero — it is the trigger of the scoring, not a factor input. Investigation proceeds as planned with the C1 reconciliation explicitly per A.7.

### H2 — P0-4 Inverted Risk-Reward — PARTIALLY REPRODUCES

**Spec claim:** "Net realized PnL was negative $78.32 across 20 closes. Win rate was 40 percent (8 wins, 12 losses). Zero trades closed on take-profit. Every win was a breakeven scratch between plus 0.01 and plus 0.78 percent; the losses ran larger. The average win was about 3.45 units and the average loss about 8.83 units, so the average loss was roughly two and a half times the average win."

**What the actual logs and trade_log show for strict window 15:15:00–17:14:59:**

- Trades closed: **25** (spec said 20).
- Wins: **12** (spec said 8). Losses: **13** (spec said 12). Win rate: **48%** (spec said 40%).
- Net PnL: **+$30.05** (spec said -$78.32).
- Avg win: **+$11.51**, avg loss: **-$8.32**. Win is larger than loss in absolute terms — the spec's "average loss was roughly 2.5x the average win" does NOT reproduce.
- Take-profit hits (`bybit_tp_hit`): **0** — this part of the spec does reproduce.

Close-reason distribution in the strict window:

| close_reason | count | net PnL |
| --- | --- | --- |
| `bybit_sl_hit` | 8 | +$52.42 |
| `system_close` | 6 | -$17.91 |
| `wd_timeout` | 4 | -$42.84 |
| `wd_dl_action` | 4 | +$5.25 |
| `wd_trail` | 2 | +$39.35 |
| `mode4_stall_valve` | 1 | -$6.23 |
| `bybit_tp_hit` | 0 | n/a |

The 8 `bybit_sl_hit` events net positive (+$52.42) because most were on shorts that hit their downward TP-style stop — many of these were the XRAY-flipped Sells where the Sell direction was structurally well-targeted by XRAY's analysis. The largest negative bucket is `wd_timeout` (-$42.84) on 4 trades.

Direction split in the strict window: Buy = 8 trades, -$25.09 net; Sell = 17 trades, +$55.13 net. Sells dominated (a consequence of P0-2 inversion flipping Buy directives to Sell), and Sells were profitable in this window — which is precisely why the spec's "inverted risk-reward" conclusion does not survive contact with the actual numbers in this window. The system shorted into a market that happened to drop in the 15:15–17:15 window.

Emergency-close at 17:14:54 confirmed:

- `2026-05-22 17:14:54.761 | LAYER_EMERGENCY | closing_all reason=telegram_dash_emergency actor=telegram_user:<REDACTED_CHAT_ID>`
- 6 positions force-closed in that batch (PLUMEUSDT +$8.47, ARBUSDT +$0.96, MNTUSDT -$0.66, EGLDUSDT -$3.69, INJUSDT -$21.72, MONUSDT -$1.27). Net -$17.91.

**Conclusion:** P0-4 partially reproduces — only the "zero take-profit hits" element is confirmed. The "inverted risk-reward" claim does NOT hold for this window's strict data. The PnL math in the spec's A.2 appears to be from a different, broader window or from the disputed prior monitoring report.

**Escalation required.** Per Rule 14 and Rule 16, I will pause at the P0-4 decision gate and present this finding. The valid residual concerns for Phase 4 are: (a) the absent take-profit path (no `bybit_tp_hit` across 25 closes — the watchdog-side closes are firing before the native exchange TP order fills), (b) the `wd_timeout` losses (-$42.84 on 4 trades is a real concentration), and (c) the hardcoded `wd_profit_take` 1.5%-cap at watchdog lines 2488–2527 even if it did not visibly fire in this window. The "inverted average win/loss" framing should be dropped or re-derived from a larger sample if the operator wishes.

## H1 — Spec-versus-Logs Divergence Summary

| Defect | Spec claim | Logs say | Status |
| --- | --- | --- | --- |
| P0-1 | 47-min blackout; 15 vanished directives; first exec at 16:02:27 | Layers 1-3 active by 15:07:20; first exec at 15:20:59; 0 BRAIN_TRADES_DROPPED; full accounting | **Does not reproduce** |
| P0-2 | 6 Buy→Sell flips; ratios 9.6/68.1/7.1/50.4 | 15 flips across 8 symbols; ratios up to 100.6x; dual-logging confirmed | **Reproduces, broader** |
| P0-3 | 12 close votes all rejected; ceiling 5.0; INJ + ICP sagas | 12/15 rejected (+ 3 reject_and_tighten); ceiling 4.5; sagas confirmed | **Reproduces** |
| P0-4 | -$78.32 net, 8/12 W/L, avg-loss=2.5x-avg-win, zero TP | +$30.05 net, 12/13 W/L, avg-win > avg-loss, zero TP confirmed | **Partial — only the zero-TP element holds** |

## H1 — What Happens Next

Per Rule 14 and the spec's "if a defect no longer reproduces, document that and escalate" instruction, the next step is the P0-1 decision gate. I will present this baseline to the operator and ask for explicit direction on three points before any code change:

1. P0-1: skip, or re-baseline against a different earlier session, or investigate the silent-drop class of bug anyway?
2. P0-4: drop the "inverted risk-reward" framing and focus on the zero-take-profit symptom and the wd_timeout concentration, or re-derive from a larger window?
3. The remaining P0-2 and P0-3 defects clearly reproduce. The investigation methodology for those (in the plan, phases 2 and 3) is unchanged.

No code modification has been performed in Phase 0. Protected tables remain at their pinned row counts (no destructive operations were issued).
