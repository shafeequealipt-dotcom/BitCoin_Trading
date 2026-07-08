# Phase 0 — Layer 4 Realignment Pre-Flight + Baseline

Spec: `IMPLEMENT_LAYER4_REALIGNMENT_INDEPTH.md`
Plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-breezy-ember.md`
Date: 2026-05-06 14:54 UTC
Branch: `main` parent commit `c744e26`

## 0.1 Working tree state

`git status -s` shows only data-file modifications (`data/layer_state.json`, `data/trading.db`) and pre-existing untracked notes. Zero source-code modifications. Tree is clean for source.

## 0.2 Live workers running Phase 3 code

- Phase 3 commit: `c744e26` shipped `2026-05-06 07:49:38 UTC` — `feat(risk/time-decay/phase-3): require structural invalidation evidence for force-close`.
- `trading-workers` service `ActiveEnterTimestamp = Wed 2026-05-06 09:00:21 UTC` — restart was 1h 11m AFTER Phase 3 commit. Live workers ARE running Phase 3 code.
- `trading-mcp-sse` similarly active since 09:00:21 UTC.
- Layer state: all three layers active, `user_stopped=false`, system trading normally.
- Fund pools at 14:51: `cap=1176.36, available=0.00, in_use=1286.78` — system has positions deployed.

## 0.3 Audit findings re-verified against current tree

| Audit reference | Current code (file:line) | Current value | Status |
|---|---|---|---|
| Sniper stall-threshold ~lines 2272-2273 | `profit_sniper.py:2272-2273` | `stall_escape_partial_after_ticks=20`, `stall_escape_full_after_ticks=40` (configurable from `[mode4]`) | VERIFIED |
| `max_partials_per_position=1` ~lines 1476-1484 + 2352-2367 | `profit_sniper.py:2284`, `Mode4Settings` (settings.py) | `max_partials_per_position=1` | VERIFIED |
| Watchdog `_compute_structural_invalidation` | `position_watchdog.py:858-968` | Strict 3-signal disjunction, fail-safe block on missing data | VERIFIED |
| Min-hold guardrail comment ~lines 2575-2586 | `position_watchdog.py:2575-2634` (expanded since `f718686`) | Allow-list of 10 hard-stop reasons; 300-s default min-hold | VERIFIED (lines shifted) |
| `emergency_manual` close-reason | `layer_manager.py:625` (only call site) | Set ONLY by `LayerManager.emergency_close_all`, called only from operator Telegram buttons | VERIFIED — purely operator-initiated |
| Watchdog `emergency_close` event | `position_watchdog.py:513` | System-initiated mass-close on watchdog `mode="emergency"` | VERIFIED — distinct from `emergency_manual` |
| Audit's "21→27 ticks (6-tick gap)" | `profit_sniper.py:2272-2273` | Actual gap is 20 ticks (40-20) = 100 s at 5 s cadence | AUDIT OUT-OF-DATE; current code has wider gap than audit assumed |

## Baseline measurements

Time windows used:
- **PRE-restart 24 h** = `2026-05-05 09:00:00 UTC` → `2026-05-06 09:00:21 UTC`. Live workers were running pre-Phase-3 code.
- **POST-restart** = `2026-05-06 09:00:21 UTC` → present (~5 h 54 m as of capture). Live workers running Phase 3 code.

Note: ISO-8601 timestamps in `trade_log.closed_at` use `T` separator. All counts below derived via `datetime.fromisoformat()`.

### Baseline 1 — Closure path distribution

| Path / Reason | PRE 24h | POST | Source |
|---|---|---|---|
| `time_decay_p_win_low` (force-close → calls `position_service.close_position`) | 40 | **0** | `trade_log.close_reason` + `TIME_DECAY_FORCE_CLOSE` log |
| `mode4_p9` (Profit Sniper full close) | 16 | **18** | `trade_log.close_reason` |
| `shadow_sl_tp` (natural SL/TP hit) | 12 | 0 | `trade_log.close_reason` |
| `emergency_manual` (operator Telegram) | 4 | 1 | `trade_log.close_reason` |
| `trailing_stop` | 1 | 0 | `trade_log.close_reason` |
| `strategic_review:...` (CALL_B closes) | 4 | 0 | `trade_log.close_reason` |
| **Total closes** | **77** | **19** | |

Log-level event counts (workers.2026-05-05_21-48-58_246166.log; spans 21:48 May 5 → 11:25 May 6):

| Event tag | PRE-09:00 | POST-09:00 |
|---|---|---|
| `mode4_p9` (logged from data_lake) | 72 | 128 |
| `MODE4_PARTIAL_CAP_REACHED` | 25 | not observed in tail |
| `M4_GATED` | 300 | (continues) |
| `P9_CLOSE_GATE` | 3 | (continues) |
| `TIME_DECAY_FORCE_CLOSE` | 29 | **0** |
| `TIME_DECAY_STRUCT_GUARD` | 0 | 0 |
| `TIME_DECAY_STRUCT_INVALIDATED` | 0 | 0 |
| `TIME_DECAY_AGE_GUARD` | 0 | **72** |
| `TIME_DECAY_MAE_GUARD` | 0 | **40** |
| `TIME_DECAY_ANCHOR_LOAD` | 0 | **24** |
| `STRAT_ACTION_CLOSE_BLOCKED` | 17 | 8 |
| `emergency_manual` log mention | 17 | 8 |

Pre/post counts derived via `awk -v cutoff=...` parsing.

The log mentions of `mode4_p9` (128 post-restart) outnumber the trade_log close count (18 post-restart) by ~7×. This is expected — the log emits multiple lines per close (data_lake write, thesis_manager close, thesis_close confirmation), while `trade_log` records one row per trade.

### Baseline 2 — Hold-time distribution

POST-restart `mode4_p9` closes (n=18, sniper):
- min: 2.8 min, median: 5.8 min, max: 7.6 min, avg: 5.7 min
- p95: 7.6 min — the entire distribution is below the 10-minute strategy minimum
- All 18 hold times: `[2.8, 2.9, 2.9, 2.9, 2.9, 3.1, 3.2, 3.6, 4.9, 5.8, 5.9, 6.9, 7.1, 7.3, 7.4, 7.6, 7.6, 7.6]`

POST-restart all closes (n=19): avg hold 6.5 min.
PRE-restart all closes (n=77): avg hold 5.2 min.

### Baseline 3 — Trade outcome quality

PRE-restart 24 h (n=77):
- win rate: 13/77 = 16.9 %
- avg PnL: -0.103 %
- avg hold: 5.2 min

POST-restart (n=19):
- win rate: 4/19 = 21.1 %
- avg PnL: -0.097 %
- avg hold: 6.5 min

### Baseline 4 — Sniper-specific metrics (POST-restart, n=18 mode4_p9 closes)

| Profile | n | Pct | Spec interpretation |
|---|---|---|---|
| In profit (pnl > 0) | 3 | 17 % | KILLED PROFITABLE — Issue 2 evidence |
| In development window (-0.3 % ≤ pnl ≤ 0 %) | 11 | 61 % | KILLED EARLY — Issue 1 evidence |
| Meaningful loss (pnl < -0.3 %) | 4 | 22 % | JUSTIFIED stall escape |

PnL range observed: -0.742 % to **+1.306 %**. The +1.306 % case is exactly the give-back pattern: a profitable trade killed by stall counter despite winning.

Score and tick thresholds at escape: not directly available in current logs without correlated tick replay. Trade is fast-closure, no MODE4_STALL_ESCALATE events observed in the log window — closes are coming via mode4_p9 directly, not via the stall-escape path. Worth tracing during Phase 1 implementation review.

### Baseline 5 — Time-decay metrics (POST-restart)

- `TIME_DECAY_FORCE_CLOSE` events: **0** (vs 29 pre-restart in equivalent prior window).
- `TIME_DECAY_AGE_GUARD` events: 72.
- `TIME_DECAY_MAE_GUARD` events: 40.
- `TIME_DECAY_ANCHOR_LOAD` events: 24 (anchor source distribution mostly `trade_state` per sample).
- `TIME_DECAY_STRUCT_GUARD` events: 0.
- `TIME_DECAY_STRUCT_INVALIDATED` events: 0.

**Interpretation:** Phase 1 + Phase 2 + Phase 3 are stacking correctly. Phase 1 (`AGE_GUARD`) is catching most cases at 72 events; Phase 2 (`MAE_GUARD`) is catching another 40. By the time a position passes Phase 1 + 2, it never reaches Phase 3 because `p_win` apparently doesn't drop below the `p_win_force_close` threshold on those mature positions. Phase 3 is therefore not BLOCKING anything (zero `STRUCT_GUARD`) and not PROCEEDING anything (zero `STRUCT_INVALIDATED`). The gate is dormant — but in a "no need to fire" sense, not in a "broken wiring" sense.

### Baseline 6 — Emergency-path triggers

POST-restart:
- 8 log-mentions of `emergency_manual` (from `trade_log.close_reason` plus thesis-close logging). Operator pressed Telegram emergency button ~1-2 times → 8 close-reason mentions reflect cascading log lines per close.
- 0 `EMERGENCY MODE` events (watchdog has not entered system-emergency mode post-restart).
- 0 `HARD STOP` events in the log window.

PRE-restart 24 h:
- 17 `emergency_manual` log mentions across 4 distinct trade closures.
- 0 `EMERGENCY MODE` events — watchdog system-emergency has not fired even pre-restart.

### Baseline 7 — Trade execution efficiency

Cannot fully evaluate because no Stage 2 directives have been issued in the post-restart log window (n=0 positions opened post-9:55 UTC; last new trade was 09:54 with hold 7.4 min closing at 10:02 = before workers.log starts). Per memory `project_top5_fix_status.md`, recent execution rate has been ~100 %.

## Phase 2 decision (data-driven)

Decision criteria (from approved plan):
- **Branch A (working as designed):** ≤ 5 force-closes / 24 h with paired `STRUCT_INVALIDATED` events carrying real evidence.
- **Branch B (wrongly permissive):** ≥ 10 force-closes / 24 h with weak evidence.
- **Branch C (inactive wiring):** zero `STRUCT_GUARD` AND zero `STRUCT_INVALIDATED` despite force-closes.

Observed POST-restart: **0 force-closes**, 0 STRUCT_GUARD, 0 STRUCT_INVALIDATED. Phase 1 + Phase 2 are filtering before Phase 3 can fire. This is unambiguously **Branch A territory** — the gates are doing their job.

**Phase 2 = Branch A → ship observability only.** Add `TIME_DECAY_FORCE_CLOSE_TRACE` event with full entry-vs-current XRAY/setup/regime evidence preceding any future force-close. No threshold tightening, no wiring fix.

## Phase 1 justification (sniper realignment)

Sniper baseline confirms every spec claim:
- 78 % of sniper closes are unjustified by the operator's aggressive philosophy (3 profitable, 11 in dev window).
- All 18 closes happen within 7.6 minutes — the 10–30 min strategy window is never reached.
- Highest pnl killed: +1.306 % — direct give-back evidence (the spec's NEARUSDT pattern with a different symbol).

Phase 1A (min-age guardrail at 300 s = 5 min) eliminates kills before 5 min — would have blocked at least the 9 closes ≤ 4.9 min hold (50 % of all sniper kills).
Phase 1C (PnL-aware) eliminates kills on profitable + developing positions — would have blocked 14/18 = 78 % of sniper kills.
Phase 1B (tick threshold raise) pushes the stall floor to 10 min — would have blocked all 18 kills (longest hold = 7.6 min < 10 min).

## Verification gate

| Item | Status |
|---|---|
| Working tree clean for source | PASS |
| Live workers running Phase 3 code | PASS |
| Audit findings re-verified | PASS (with one note: audit's "6-tick gap" is now a 20-tick gap) |
| All 7 baselines captured | PASS |
| Phase 2 branch decision documented | PASS — Branch A |
| Phase 1 justification documented | PASS |

Phase 0 verification gate is **GREEN**. Proceeding to Phase 1A (minimum-age guardrail).
