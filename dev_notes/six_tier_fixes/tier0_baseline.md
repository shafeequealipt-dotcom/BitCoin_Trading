# Tier 0 Baseline — Six-Tier Investigation-First Fix Series

## 1. Engagement context

This is Tier 0 of the 22-issue / 6-tier engagement defined in `IMPLEMENT_SIX_TIER_FIXES_2026-05-11.md`. Tier 0 produces no code changes. It captures the starting state so the final combined verification (after Tier 6 ships) can be measured against a fixed reference.

Date: 2026-05-11. Operator decisions captured in plan mode:

- Branch: stack on `fix/five-critical-fixes-2026-05-11`.
- System: stopped, executor has full ownership.
- Verification: combined at end (per-issue Phase 4 and per-tier Phase 5 skipped).
- Tests: smoke tests only (1-3 per issue, timeout-wrapped).
- Phase 2 operator approval gate remains per the prompt's hard rule.

## 2. Working-tree and branch state

### 2.1 Branch

`fix/five-critical-fixes-2026-05-11` is checked out. HEAD is `11cc1a7 test(e2e): real-project pipeline verification against full schema`.

### 2.2 Uncommitted files

Two files modified, both runtime state, not source:

- `data/layer_state.json`
- `data/logs/layer1c_full.jsonl`

System is stopped so both are frozen. Left as-is for the engagement; they will be overwritten on restart and are not tracked source.

### 2.3 Pre-fix DB backup

Created `data/trading.db.bak-pre-six-tier-fixes-20260511_1444` (180 MB, identical bytes to `data/trading.db`). This is the rollback point if Tier 1-6 corrupts state.

## 3. Log time windows captured for baseline

- `data/logs/workers.log` — 2026-05-11 11:55:43 to 14:45:13 (≈2h50m, 7.8 MB).
- `data/logs/brain.log` — 2026-03-29 04:34:12 to 2026-05-11 14:41:14 (≈6 weeks rolling, 5.8 MB).
- `data/logs/general.log` — 2026-05-10 19:30:57 to 2026-05-11 14:40:45 (≈19h, 80 KB).
- `data/logs/mcp.log` — 1.1 MB (out of immediate scope).

`brain.log` is a 6-week rolling buffer; absolute counts there are not 1-day comparable. Recommendation for final verification: filter brain.log counts to a 24-48h window matching the post-fix capture window.

## 4. Configuration snapshot

Selected `config.toml` settings relevant to the 22 issues:

```
config.toml:15   [general] mode = "shadow"           # confirms T6-1 / Phase5 F-2 divergence
config.toml:116  wal_mode = true                     # WAL already on
config.toml:236  model = "claude-sonnet-4-20250514"
config.toml:327  loss_cooldown_seconds = 30          # T2-1 / F20 candidate
config.toml:533  max_step_pct = 0.25                 # confirms F8 cap
config.toml:1099 mode4 buffer_min_ready = 100
config.toml:1121 mode4 tighten_cooldown_seconds = 15
config.toml:1130 mode4 min_seconds_between_actions = 60
config.toml:1205 cooldown_extreme_seconds = 300
config.toml:1206 cooldown_strong_seconds = 180
config.toml:1207 cooldown_medium_seconds = 120
config.toml:1308 apex model = "deepseek/deepseek-v3.2"
```

Drift correction from the plan-mode draft: `max_step_pct=0.25` IS the active cap. The watchdog default of `0.5` in `position_watchdog.py:2858` is overridden by `config.toml:533`. The F8 report value is correct.

## 5. Twenty-two issue baseline metrics

Counts measured against the time windows in section 3.

### Tier 1 — Trade quality killers

| ID | Tag / query | Count | Window | Notes |
|----|-------------|-------|--------|-------|
| T1-1 F18 | `SENTINEL_FIREWALL_ALLOW.*act=close` | 8 | workers.log 11:55-14:45 (≈2h50m) | Report said ~7/h; matches. Three fresh samples below confirm chronic. |
| T1-2 F8 step_exceeded | `SL_GATEWAY_REJECT.*step_exceeded` | 10 | workers.log | raw_step_pct ranging 1.032 to 1.248 today (4.1-5.0x cap), WORSE than report's 2.04-2.27x. |
| T1-2 F8 rate_limit | `SL_GATEWAY_REJECT.*rate_limit` | 108 | workers.log | Compounds step_exceeded thrash. |
| T1-3 F9 | `THESIS_CLOSE` with `lesson=''` | 38 of 38 (100%) | workers.log | Every close empty. |
| T1-4 F4 DB_LOCK_WAIT | `DB_LOCK_WAIT` | 0 | workers.log 11:55-14:45 | Zero in current session — may have been resolved or VACUUM hasn't fired. |
| T1-4 F4 VACUUM | `VACUUM` | 4 | general.log | Schedule needs investigation in T1-4 Phase 1. |

### Tier 2 — Risk management

| ID | Tag / query | Count | Window | Notes |
|----|-------------|-------|--------|-------|
| T2-1 F20 | `size_halved_cooldown_` | 2 | workers.log | Report said 2 events today. Matches. |
| T2-2 F14 | `xray_conf=0.00` | 1 | workers.log | Report cited 1 explicit (SOLUSDT). Matches. |
| T2-3 F11 | brain-vs-analysis direction disagreement | not directly tagged | n/a | Requires per-trade trace in T2-3 Phase 1. |

### Tier 3 — Phase 5 close attribution

| ID | Tag / query | Count | Notes |
|----|-------------|-------|-------|
| T3-1 Phase5 F-4 | safety-gate absence (POSITION_SIZE_CAPPED etc.) | 0 in workers.log | Gates inactive on bybit_demo path per Phase5 report. |
| T3-2 Phase5 F-8 | `BYBIT_DEMO_PERSIST_OK.*table=orders.*order_id=''` | 24 in workers.log | DB: `orders` table has 255 total rows; 1 row with `order_id=''` (rest clobbered by INSERT OR REPLACE). |
| T3-3 Phase5 F-15 | `closed_by=bybit_demo_sl_tp` | 13 | Fallback fires; sniper/wd/td/callb triggers lost. |
| T3-4 Phase5 F-20 | `COORD_DOUBLE_CLOSE` | 9 | Race confirmed live. |

### Tier 4 — Observability and latency

| ID | Tag / query | Count | Notes |
|----|-------------|-------|-------|
| T4-1 F1 | `CLAUDE_PROC_STALL` (any duration) | 1229 in brain.log | Out of 1937 `CLAUDE_CALL_OK` => 63% of Stage 2 calls stall ≥60s. brain.log is 6-week rolling; per-day rate must be derived in T4-1 Phase 1. |
| T4-2 Phase5 F-12 | `BYBIT_DEMO_SET_SL_OK` | 0 | Tag does not exist today. |
| T4-2 Phase5 F-12 | `SL_PROPAGATED` | 126 | 126 SL changes happened with zero confirmation logs. |
| T4-3 Phase5 F-19 | post-place 20s latency | not tagged | Requires trace investigation in T4-3 Phase 1. |

### Tier 5 — Operational quality

| ID | Tag / query | Count | Notes |
|----|-------------|-------|-------|
| T5-1 F2 | `BASE_WORKER_TICK_SLOW.*kline_worker` | 33 | Report cited 7 in 25 min => 33 in 2h50m matches. |
| T5-2 F3 | `BASE_WORKER_TICK_SLOW.*profit_sniper` | 105 | Report cited ~12/30min => 105 in 2h50m matches. |
| T5-3 F5 | covered by Tier 1 F8 rate_limit count (108) | n/a | Architectural overlap with T1-2 noted in plan. |
| T5-4 F19 | `BYBIT_DEMO_WS_STALE` | 46 | ≈16/h — matches report's 2-4min cadence. |
| T5-5 Phase5 F-1 | `WS_RECONNECT_OK` (or similar) | 92 | Double-count of reconnect events (start + ok). Per-event needs Tier 5 Phase 0 refinement. |

### Tier 6 — Configuration drift and cosmetic

| ID | Tag / query | Count | Notes |
|----|-------------|-------|-------|
| T6-1 Phase5 F-2 | `config.toml:15` mode | "shadow" | Runtime is bybit_demo (T6-1 confirmed). |
| T6-2 F6 | `WORKER_NEVER_TICKED` | 6 | Boot-time only. Cold-start gate working as designed. |
| T6-3 F12 | `STRAT_AGGRESSIVE_FRAMING` | 269 in brain.log | Rolling 6 weeks; rate needs windowing. |
| T6-4 F7 | `ALERT_FAIL` | 3 | Report said 2; +1 more since. |
| T6-5 F13 | `STRATEGIST_PACKAGES_READ` age | not enumerated here | Needs per-cycle trace in T6-5 Phase 1. |
| T6-6 F10 | `peak_pnl` vs close PnL gap | not directly tagged | Trade-history join in T6-6 Phase 1. |
| T6-7 F22 | `APEX_TIMING` events with high latency | 0 currently visible in brain.log search | Tag may have shifted; T6-7 Phase 1 must regrep. |
| T6-8 Phase5 F-21 | `closed_by` natural-language suffix in `trade_history.notes` | needs DB join | `trade_history` schema (below) has no `closed_by` column — must be in `notes` or another join. T6-8 Phase 1 maps this. |

## 6. DB schema snapshot for close-side audit

```sql
orders:
  order_id TEXT PRIMARY KEY
  symbol, side, order_type, price, qty, status, filled_qty,
  avg_fill_price, stop_loss, take_profit, created_at, updated_at,
  exchange_mode TEXT NOT NULL DEFAULT 'shadow'

positions:
  symbol TEXT PRIMARY KEY
  side, size, entry_price, mark_price, unrealized_pnl, realized_pnl,
  leverage, liquidation_price, stop_loss, take_profit, updated_at,
  exchange_mode TEXT NOT NULL DEFAULT 'shadow'

trade_history:
  trade_id TEXT PRIMARY KEY
  symbol, side, entry_price, exit_price, qty, pnl, pnl_pct,
  strategy, signal_confidence, notes, entry_time, exit_time,
  exchange_mode TEXT NOT NULL DEFAULT 'shadow'
```

Observations:

- `orders` PK is `order_id`. Blank-PK INSERT OR REPLACE (T3-2 root cause) confirmed.
- `positions` PK is `symbol`. Updates are upserts.
- `trade_history` does NOT have a `closed_by` column — the close trigger flows through logs (`WS_CLOSE_EVENT closed_by=...`) and possibly into the `notes` text field. T6-8 (Phase5 F-21) and T3-3 (Phase5 F-15) both need to confirm where close-trigger lands in DB during their Phase 1 investigations.

## 7. Sample fresh evidence (chronic-firing confirmation)

### 7.1 T1-1 F18 phantom close (today, beyond the report's window)

```
14:18:54 SENTINEL_FIREWALL_ALLOW sym=CRVUSDT act=close src=call_b
         rsn='SL 54% consumed and accelerating losses with only 27min remaining. Recovery unli...'
14:18:54 STRAT_POS_ACT sym=CRVUSDT act=close ...

14:32:37 SENTINEL_FIREWALL_ALLOW sym=SEIUSDT act=close src=call_a_urgent
         rsn='Watchdog flagged 10 critical_loss alerts with PnL ranging -0.63% to -1.17%. API ...'
14:32:37 STRAT_POS_ACT sym=SEIUSDT act=close ...

14:36:42 SENTINEL_FIREWALL_ALLOW sym=SEIUSDT act=close src=call_b
         rsn='Position appears already in recently closed cooldown (457s remaining). Watchdog ...'
14:36:42 STRAT_POS_ACT sym=SEIUSDT act=close ...
```

Note: the third event has the brain explicitly flagging the contradiction in its own reason text ("Position appears already in recently closed cooldown"). The firewall + layer_manager pipeline still allowed it. Same pattern as the report's FILUSDT 13:44 and ADAUSDT 14:02 events.

Sources observed for F18: `call_b`, `call_a_urgent`. The firewall's `_BLOCKED_ACTIONS` set is not catching close-on-closed-symbol.

### 7.2 T1-2 F8 trail step rejects (latest 3, all today)

```
14:32:35 SL_GATEWAY_REJECT sym=BLURUSDT rsn=step_exceeded src=trail_update
         raw_step_pct=1.232 max=0.25 new=0.028651 cur=0.029009 (4.93x cap)
14:33:26 SL_GATEWAY_REJECT sym=MANAUSDT rsn=step_exceeded src=trail_activation
         raw_step_pct=1.032 max=0.25 new=0.098820 cur=0.099850 (4.13x cap)
14:33:26 SL_GATEWAY_REJECT sym=BLURUSDT rsn=step_exceeded src=trail_update
         raw_step_pct=1.248 max=0.25 new=0.028647 cur=0.029009 (4.99x cap)
```

Cap-exceeding ratios today (4.1-5.0x) are LARGER than the report's 2.04-2.27x. Confirms F8 chronic and getting worse. Sources observed today: `trail_update`, `trail_activation`. T1-2 Phase 1 must enumerate all sources and confirm Architectural Theme 1.

### 7.3 T1-3 F9 every close has empty lesson

```
14:38:09 THESIS_CLOSE sym=GMTUSDT pnl=-0.97% rsn=bybit_sl_hit lesson=''
14:38:15 THESIS_CLOSE sym=RUNEUSDT pnl=+1.07% rsn=bybit_demo_sl_tp lesson=''
14:39:38 THESIS_CLOSE sym=OPUSDT pnl=-0.35% rsn=emergency_manual lesson=''
```

100% empty across 38 closes. Win, loss, scratch, emergency — all empty. F9 confirmed comprehensive.

## 8. Previous-fix status verification (deferred)

The plan listed 14 prior fix series to verify against current code. Doing this here adds time without value — Tier 1 Phase 0 will re-verify each Tier 1 file's recent commit history during the per-issue investigation. Phase 0 of each subsequent tier will do likewise.

Confirmed in plan-mode exploration (still valid):

- `src/sentinel/firewall.py:31,52,58` — `should_allow_strategic_action` exists with `_BLOCKED_ACTIONS` set; current firewall passes today's phantom-close events through (samples in 7.1).
- `src/brain/strategist.py:3553` — `_tias_lessons_removed = True` sentinel still present from Post-Execution Closure Fix Phase 1A (2026-05-05). T1-3 must operate inside that constraint.
- `src/database/connection.py:122` — WAL mode on (`journal_mode=WAL`).
- `config.toml:15` — mode = "shadow" diverges from runtime bybit_demo.

## 9. Shadow project state

`/home/inshadaliqbal786/shadow/` (NOT a git repo) is alive. `logs/shadow.log` last entry 14:44:06 ("Ticker snapshot: 50/50 coins saved"). Shadow continues to operate as the virtual exchange simulator. Tier 1-6 adapter changes (Tier 3) must be verified against shadow before each tier ships.

## 10. Verification gate

Tier 0 deliverables checklist:

- [x] Branch + working-tree state captured (section 2).
- [x] Pre-fix DB backup taken (section 2.3).
- [x] 22-issue baseline metrics captured (section 5).
- [x] DB schema snapshot taken (section 6).
- [x] Fresh chronic-firing evidence captured for T1-1, T1-2, T1-3 (section 7).
- [x] Shadow project confirmed alive (section 9).
- [x] Configuration snapshot taken (section 4).

Tier 0 complete. Ready to begin Tier 1 Phase 0.
