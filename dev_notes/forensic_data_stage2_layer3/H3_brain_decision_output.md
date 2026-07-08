# H3 — Brain decision output shape

Collected: 2026-05-02. Logs window: last 24h.

## Decision JSON schemas

Per the system prompts in `src/brain/strategist.py`:

### CALL_A (new trades) — line 116, `TRADE_SYSTEM_PROMPT`

```
{
  "new_trades": [
    {
      "symbol":                "SYM",                        // exact symbol e.g. ETHUSDT
      "direction":             "Buy" | "Sell",
      "stop_loss_price":        N,                            // EXACT price (not pct)
      "take_profit_price":      N,                            // EXACT price (not pct)
      "max_hold_minutes":       N,                            // 15-60
      "leverage":               N,                            // 1-5
      "size_usd":               N,                            // $500-$5000, MIN $500
      "trailing_activation_pct":N,                            // 0.3-0.8
      "reasoning":             "..."
    }
  ],
  "market_view":            "...",
  "risk_level":             "normal" | "cautious" | "aggressive",
  "max_positions":           N,
  "default_leverage":        N,
  "default_sl_pct":          N,
  "default_tp_pct":          N,
  "default_hold_minutes":    N,
  "trailing_activation_pct": N,
  "focus_coins":             [],
  "avoid_coins":             []
}
```

When `_has_urgent_concerns=True` (urgent watchdog payload injected into the system prompt — strategist.py:434-443), CALL A is permitted to ALSO include a `position_actions` map with the same shape as CALL B. Parser branch at strategist.py:455-468 handles this.

### CALL_B (position management) — line 153, `POSITION_SYSTEM_PROMPT`

```
{
  "position_actions": {
    "SYMBOL": {
      "action":      "hold" | "close" | "tighten_stop" | "set_exit",
      "new_sl":       price_or_null,
      "exit_price":   price_or_null,
      "reasoning":   "..."
    }
  }
}
```

Note: `_parse_position_plan` (strategist.py:2780) ALSO accepts `take_profit` as an action verb (valid set at line 2804 includes it). The system prompt does not mention it, but the parser is tolerant.

## 3 actual decisions verbatim from logs

The successful decision text is NOT logged verbatim (see H1 — only summary fields are persisted). The closest verbatim derivative are the structured `STRAT_DIRECTIVE` / `STRAT_POS_ACT` log lines plus the `claude_decisions` data-lake row.

### Decision #1 — did=d-1777720966952 (CALL A)

Strategist log:
```
STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear at 39. Account in critical drawdown — pure capit'
STRAT_DIRECTIVE   | #1 sym=DYDXUSDT dir=Buy lev=2 rsn='CAPITAL PRESERVATION. RSI=26 deeply oversold in ranging global regime = textbook'
STRAT_DIRECTIVE   | #2 sym=MONUSDT  dir=Buy lev=2 rsn='CAPITAL PRESERVATION. ADX=50 strong trend + RSI=55 healthy momentum zone + MEDIU'
STRAT_CALL_A_END  | el=74437ms trades=2
```

Data-lake row (`claude_decisions.id=1232`, `decision_type=call_a`, `new_trades_count=2`, `position_actions_count=0`, `response_time_ms=74437`):
```
market_view='Ranging global regime with fear at 39. Account in critical drawdown — pure capital preservation. Only taking 2 minimum-size mean-reversion and momentum-continuation buys on MEDIUM vol coins. Avoiding [...]'
risk_level='cautious'
```

### Decision #2 — did=d-1777703051893 (CALL A)

```
STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear sentiment (39). Asian late session with low volu'
STRAT_DIRECTIVE   | #1 sym=ONDOUSDT dir=Buy  lev=2 rsn='STRONG ensemble 76.7, highest buy consensus (6.0 votes) across all candidates. R'
STRAT_DIRECTIVE   | #2 sym=NEARUSDT dir=Sell lev=2 rsn='GOOD ensemble 62.4, strong sell votes (3.45). A+ x-ray setup, pos=82% near range'
STRAT_CALL_A_END  | el=128721ms trades=2
```

Data-lake row id=1230, response_time_ms=128721, market_view excerpt: `'Ranging global regime with fear sentiment (39). Asian late session with low volume — not ideal for directional bets. Both directions struggling badly. Capital preservation is priority. Taking only 2 m[…]'`.

### Decision #3 — did=d-1777701884628 (CALL B)

```
STRAT_CALL_B_PLAN | acts=1
STRAT_POS_ACT     | sym=AXSUSDT act=close rsn='Thesis is broken. TIAS shows 1W/7L (12% WR, PF=0.12) — historically disastrous b'
STRAT_CALL_B_END  | el=84812ms acts=1
```

Data-lake row id=1228 (next-by-time `call_b`): `decision_type=call_b`, `position_actions_count=1`, `response_time_ms=78865`. (The 84812 ms in `STRAT_CALL_B_END` is the strategist wallclock; the CLI subprocess `el=84792ms` matches; the data-lake snapshot stored 78865 ms — different cycle but same shape.)

NOT FOUND — fully verbatim Claude JSON for these three did=...; the logger only persists `market_view[:200]` and `reasoning[:80]` per directive. Searched: `raw_response`, `claude_response`, `Brain v2 raw response`. The `brain_decisions` SQL table (schema below) has columns `claude_response` and `decision_json` but is NOT written by the strategist path — only by the legacy `BrainV2._log_decision` (`src/brain/brain_v2.py:386`), and that path is not currently active (table row count = 0).

## Per-directive fields

`StrategicPlan` (`src/core/strategic_plan.py:38`) holds three slots:

- `new_trades: list[dict]` — Claude's raw new-trade dicts as parsed (no schema enforcement at the dataclass level; downstream consumers read keys directly with `_safe_float`).
- `coin_directives: dict[str, CoinDirective]` — `CoinDirective` (line 13): `symbol`, `direction` ("buy_only"/"sell_only"/"both"/"avoid"), `reason`, `leverage=2`, `sl_pct=2.0`, `tp_pct=2.5`, `max_hold_minutes=30`, `priority=5`.
- `position_actions: dict[str, PositionAction]` — `PositionAction` (line 27): `symbol`, `action` ("hold"/"close"/"tighten_stop"/"set_exit"/"take_profit"), `reason`, `exit_price=0`, `new_sl=0`.

Validation logic line-by-line:

- `_safe_float` (strategist.py:32) and `_safe_int` (strategist.py:50) coerce all numeric fields, default to 0.0 / 0 on `None`/``""``/``ValueError``/``TypeError``.
- `_parse_trade_plan` (strategist.py:2738) plumbs `_safe_int` over `max_positions`, `max_per_coin`, `default_hold_minutes`, `default_leverage`; `_safe_float` over SL/TP pct and `trailing_activation_pct`. `new_trades` is a passthrough — no per-trade validation here. Per-trade SL/TP checks happen later in `strategy_worker._execute_claude_trade` (`src/workers/strategy_worker.py:1110`) via the `TRADE_SKIP rsn=sanity_reject|sltp_skip|qty_zero|order_reject|...` family of skip codes.
- `_parse_position_plan` (strategist.py:2780) — see H1 for the per-action validation. Key downgrades:
  - Unknown `action` string → `STRAT_CALL_B_BAD_ACTION_TYPE` warning, action set to `"hold"` (line 2820-2826).
  - `tighten_stop` with `new_sl<=0` → `STRAT_CALL_B_DOWNGRADE`, action set to `"hold"` (line 2831-2836).
  - `set_exit` with `exit_price<=0` → `STRAT_CALL_B_DOWNGRADE`, action set to `"hold"` (line 2837-2842).
  - Final emit `STRAT_CALL_B_PARSED | total=N hold=A close=B tighten=C set_exit=D take_profit=E` at line 2857.
- 24h tally of these defensive logs: searched `STRAT_CALL_B_BAD_ACTION_TYPE`, `STRAT_CALL_B_DOWNGRADE`, `STRAT_CALL_B_BAD_ACTIONS`, `STRAT_CALL_B_BAD_ACTION`, `STRAT_CALL_B_BAD_SHAPE`, `STRAT_CALL_B_PARSED` in /tmp/h_collect/brain_24h.log: NOT FOUND for the BAD/DOWNGRADE tags (no defensive downgrades fired in window — Claude returned well-formed `position_actions` every time).

## Validation pipeline (post-parse, pre-route)

For CALL A trades, validation runs INSIDE `_execute_new_trades` (`src/core/layer_manager.py:1183-1380`) AFTER `_parse_trade_plan` returns:

1. `pnl_manager.can_trade()` — manual-pause gate (layer_manager.py:1194-1198). If `False` emits `BRAIN_TRADE_HALT` and returns. **24h count: 0**.
2. `enforcer.check_and_enforce()` then `enforcer.should_allow_trade(leverage=1)` — performance enforcer halt (lines 1211-1222). If blocked emits `STRAT_L4_HALT`. **24h count: 0**.
3. APEX optimization in parallel — `apex.optimize(_t, plan)` per directive (lines 1254-1271). Failures fall back to Claude params and emit `APEX_GATHER_FAIL`.
4. `[POS] gate` (lines 1290-1299) — block coins that already have an open position OR are currently being executed. Emits `POS_GATE_BLOCK | sym=... rsn=open_position|executing` and `TRADE_SKIP | rsn=pos_gate`. **24h count: 0**.
5. `_apply_apex_optimization` (line 1316) — pct→price conversion using current ticker.
6. `apex_gate.validate(trade)` (line 1322) — TradeGate hard-safety adjustment (never blocks; emits `_gate_validation_ms` on the dict).
7. `strategy_worker._execute_claude_trade` (line 1326) — final per-trade rejections live here. Skip codes (sample tags, all in strategy_worker.py): `sanity_reject` (line 1132), `enforcer_block` (1165), `survival_block` (1182), `xray_skip` (1200), `xray_conflict` (1219), `xray_dir_block` (1257), `unsupported_symbol` (1282), `dup_position` (1291), `service_missing` (1304), `price_fetch_fail` (1316), `price_invalid` (1323), `sltp_skip` (1400, 1418), `qty_zero` (1507), `order_reject` (1540).

Last 24h trade rejection counts: NOT FOUND in any non-zero count for the `TRADE_SKIP` tags (the brain only proposed trades on cycles where `BRAIN_NO_PACKAGES` blocked them, see `did=d-1777720966952`); no `BRAIN_DO_TRADE`, `BRAIN_DO_SKIP`, `BRAIN_DO_START`, `BRAIN_DO_DONE`, or `TRADE_SKIP` lines in /tmp/h_collect/brain_24h.log or /tmp/h_collect/workers_24h.log. The 24h window is dominated by Layer 3 being inactive (no `BRAIN_DO_*` events) — see also the `BRAIN_NO_PACKAGES` event at 11:24:01 which dropped 2 trades.

For CALL B position actions, validation pipeline = `_execute_position_actions` (`src/core/layer_manager.py:1100-1147`):

- Skip `action=="hold"` (line 1117).
- SENTINEL Exit Firewall: `should_allow_strategic_action(action, symbol, reason, source)` from `src/sentinel/firewall.py` (line 1121-1125). Source values: `"call_b"` (trusted), `"call_a_urgent"` (trusted), `"strategic_review"` (legacy/untrusted), default `"strategic_review"` keeps legacy behavior.
- Close-attribution: `coordinator.set_close_reason(symbol, f"strategic_review: {reason[:100]}")` for `close`/`take_profit` (line 1136-1137).
- Queue to coordinator: `coordinator.queue_strategic_action(symbol, action, reason, new_sl, exit_price)` (line 1139-1145).

## Decision routing after validation

CALL A path (per `_run_brain_cycle` lines 743-865 + `_execute_trades_background` 1148-1181):

`StrategicPlan` ← `create_trade_plan()` →
merge into `self._current_plan` (lines 760-779) →
`_record_decision_to_data_lake(plan, elapsed_ms, "call_a")` (line 781, writes `claude_decisions` table) →
`_cold_start_block_or_none(plan)` gate (line 790; emits `BRAIN_NO_PACKAGES` / `BRAIN_LOW_COMPLETENESS` per `_cold_start_block_or_none`) →
guard against concurrent execution `self._background_exec_task` (line 798-806; emits `BRAIN_DO_SKIP`) →
`asyncio.create_task(self._execute_trades_background(plan))` (line 810; wrapped in `BRAIN_DO_START` / `BRAIN_DO_DONE` / `BRAIN_DO_TIMEOUT(300s)` / `BRAIN_DO_FAIL`) →
inside: `_execute_new_trades(plan)` →
APEX optimize (parallel) → APEX gate adjust → `strategy_worker._execute_claude_trade` →
`OrderService.place_order` (the 7-step pipeline in `strategy_worker.py:1326`).

So the strategist's CALL A output reaches the OrderService via:
**LayerManager._run_brain_cycle → _execute_trades_background → _execute_new_trades → strategy_worker._execute_claude_trade → OrderService.place_order**.

There is no APEX call in CALL B path — only TradeCoordinator queueing.

CALL B path (`_run_brain_cycle` lines 876-935 + `_execute_position_actions` 1100-1147):

`StrategicPlan` ← `create_position_plan()` →
merge `position_actions` into `_current_plan` (line 903) →
`_record_decision_to_data_lake(plan, elapsed_ms, "call_b")` (line 908) →
`if self._layer_active[3]: await self._execute_position_actions(plan, source="call_b")` (line 912) →
SENTINEL firewall → coordinator.queue_strategic_action → **PositionWatchdog** consumes the queue on its own tick (per layer_manager.py:1118 comment "PositionWatchdog executes them next tick") → eventually closes the position via OrderService.

## `did=` decision ID — generation, propagation

- Generated: `new_decision_id()` from `src/core/log_context.py`. Called at the top of every brain entry point: `create_strategic_plan` (strategist.py:331), `create_trade_plan` (415), `create_position_plan` (498).
- Format: `f"d-{int(time.time()*1000)}"` style (sample IDs in window: `d-1777720966952` etc.).
- Propagation: stamped into the loguru context dict via `ctx()` from `src/core/log_context.py`. Every log emit in the strategist + downstream chain ends with `| {ctx()}` which renders `| did=d-...`. The same `did` flows into `claude_code_client.send_message` because the asyncio context is preserved (the executor inherits the loop-local context).
- Decision ID propagation traced for `did=d-1777720966952` (CALL A from 11:22:46 to 11:24:01):

```
brain.log:
  11:22:46.952  STRAT_CALL_A_START | did=d-1777720966952
  11:22:50.089  STRATEGIST_PACKAGES_READ | call=CALL_A count=0 ... did=d-1777720966952
  11:22:51.484  STRAT_PROMPT_BUILD | sections=32 ... did=d-1777720966952
  11:22:51.484  STRAT_PROMPT_SIZE | sections=32 chars=4046 did=d-1777720966952
  11:22:51.485  STRAT_CALL_A_CTX | sections=32 chars=4046 el=4532ms did=d-1777720966952
  11:22:51.485  PROMPT_BUILD_DONE | call=CALL_A coins=30 size_bytes=4077 sections=32 packages=0 elapsed_ms=4532 did=d-1777720966952
  11:22:51.485  STRAT_CALL_A | chars=4077 did=d-1777720966952
  11:22:51.486  CLAUDE_CALL_START | call_id=1 in=4077 sys=8985 timeout=300s hash=e0558dedb7cd did=d-1777720966952
  11:22:51.487  CLAUDE_PREFLIGHT_REFRESH | reason=expires_in mins_left=-82.7 ... did=d-1777720966952
  11:22:51.487  CRED_REFRESH_ATTEMPT | attempt=1/3 did=d-1777720966952
  11:22:51.840  CLAUDE_REFRESH_OK | new_token_expires_in=28800s did=d-1777720966952
  11:22:51.842  CLAUDE_PREFLIGHT_REFRESH_OK | new_mins_left=480.0 did=d-1777720966952
  11:24:01.388  CLAUDE_CALL_OK | call_id=1 attempt=1/3 el=69537ms out=2439 calls=1 did=d-1777720966952
  11:24:01.389  STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime ... did=d-1777720966952
  11:24:01.389  STRAT_DIRECTIVE | #1 sym=DYDXUSDT dir=Buy lev=2 ... did=d-1777720966952
  11:24:01.389  STRAT_DIRECTIVE | #2 sym=MONUSDT  dir=Buy lev=2 ... did=d-1777720966952
  11:24:01.390  STRAT_CALL_A_END | el=74437ms trades=2 did=d-1777720966952

workers.log:
  11:22:51.484  CAPITAL_TIER | eq=6149.85 | tier=CONSERVATIVE | alloc=50% ... did=d-1777720966952
  11:24:01.390  BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=2 did=d-1777720966952
  11:24:01.390  BRAIN_CYCLE_A_DONE | el=74437ms trades=2 view='Ranging global regime ... did=d-1777720966952
  11:24:01.390  DL_DECISION | type=call_a trades=2 acts=0 el=74437ms prompt=0 did=d-1777720966952
  11:26:32.606  BRAIN_CYCLE_B | Managing positions did=d-1777720966952    ← did is reused; cycle uses same value
  11:26:32.663  BRAIN_CYCLE_B_SKIP | rsn='no open positions' did=d-1777720966952
```

(One observation: the `did` displayed in `BRAIN_CYCLE_B` at 11:26:32 is the SAME as the preceding CALL_A — `_run_brain_cycle` does NOT generate a new `did` itself; only the strategist's `create_*_plan` methods do, via `new_decision_id()`. The CALL B branch reused the loop-context `did` from CALL A because the CALL B branch on this cycle hit `BRAIN_CYCLE_B_SKIP` and did not call `create_position_plan` — the only call site that would have minted a new `did`.)

NOT FOUND — APEX log lines for this `did`. The cold-start gate `BRAIN_NO_PACKAGES` blocked the trade flow before `_execute_trades_background` was scheduled, so APEX/gate/order routing was not invoked.

## TIAS hook for closed trades

- Trigger location: `src/workers/manager.py:1762` — `coordinator.register_close_callback(_tias_close_callback)`. This wires `_tias_close_callback` (line 1725) to fire on every `TradeCoordinator.handle_position_close` event.
- Callback flow:
  - `_tias_close_callback(record)` (sync): captures ProfitSniper `m4_snapshot` (lines 1731-1748) — preferred via `profit_sniper.get_closed_snapshot(sym)` (line 1737), fallback to direct `_profit_states` read (line 1742).
  - Schedules `_tias_async_task(record, m4_snapshot)` via `asyncio.get_event_loop().create_task` (line 1751-1754).
- `_tias_async_task` (line 1714):
  1. `await tias_collector.collect_and_save(record, tias_repo, m4_snapshot)` — Phase 1 trade-context capture, returns `(row_id, trade_obj)` (line 1716-1718).
  2. If analyzer enabled and row_id > 0: schedules `_tias_analyze_background(row_id, trade_obj, symbol)` as a separate task (line 1721-1723).
- `_tias_analyze_background` (line 1679):
  1. `await tias_analyzer.analyze(trade_obj)` — DeepSeek call.
  2. `await tias_repo.update_analysis(row_id, analysis)` — writes back.
  3. Emits `TIAS_ANALYZED | id=... sym=... cat=... conf=... cost=$... ms=...` (line 1688-1697).
  4. Failure path: `TIAS_FAIL | id=... sym=... retryable=... err='...'` (line 1699-1705); unexpected: `TIAS_FAIL_UNEXPECTED` (line 1707-1712).
- Data passed: `record` is the close-broadcast dict (from `TradeCoordinator`) containing at minimum `symbol`, `strategy_name`, `pnl_pct`, `pnl_usd`, `was_win`, `hold_seconds`, `closed_by`, `direction`, `entry_price`, `exit_price`. `m4_snapshot` is the ProfitSniper state dict (`peak_pnl_pct`, `ticks_in_profit`, `ticks_total`).
- Back-fill safety: a 30-min retry loop is launched at line 1801 (`asyncio.get_event_loop().create_task(_tias_backfill_loop())`) to re-run failed analyses; first run after 60 s warmup, then every 1800 s.
- 24h activity: NOT FOUND in /tmp/h_collect logs — searched `TIAS_ANALYZED`, `TIAS_FAIL`, `TIAS_CB_FAIL`, `TIAS_BACKFILL_LOOP_ERR`. The `trade_thesis.lesson` column DOES carry an analytic lesson string for closed trades (sample: AXSUSDT row `Orphan thesis closed by watchdog reconciler — no matching Shadow position. Likely a close callback was missed; PnL unknown, do not learn from this row.` with close_reason=`zombie_reconciler`). Other rows in window (snapshot taken at 11:45 UTC) carry close_reason `time_decay_p_win_low`, `mode4_p9`, `strategic_review: ...` — indicating that the TIAS analyzer column itself is not currently producing rich lesson text on this DB.

## DB tables (offline snapshot)

`brain_decisions`:
```
id INTEGER PK AUTOINCREMENT
prompt_hash TEXT NOT NULL
market_state_json TEXT NOT NULL DEFAULT '{}'
claude_response TEXT NOT NULL DEFAULT ''
decision_json TEXT NOT NULL DEFAULT '{}'
action_taken TEXT NOT NULL DEFAULT ''
outcome_json TEXT NOT NULL DEFAULT '{}'
tokens_used INTEGER NOT NULL DEFAULT 0
cost_usd REAL NOT NULL DEFAULT 0
trigger TEXT NOT NULL DEFAULT 'scheduled'
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
**Row count: 0**. Written only by legacy `BrainV2._log_decision` (`src/brain/brain_v2.py:386`), which is not on the active strategist code path.

`claude_decisions` (data-lake table actually populated):
```
id INTEGER PK AUTOINCREMENT
ts_epoch REAL NOT NULL
decision_type TEXT NOT NULL              -- 'call_a' | 'call_b'
new_trades_count INTEGER DEFAULT 0
position_actions_count INTEGER DEFAULT 0
market_view TEXT
risk_level TEXT
response_time_ms INTEGER
prompt_length INTEGER
full_response TEXT
created_at TEXT NOT NULL DEFAULT (datetime('now'))
```
Row count > 1230. Last 5 rows ids 1228, 1229, 1230, 1231, 1232 with decision_type call_b, call_a, call_a, call_b, call_a. `full_response` is empty (NULL/blank) on all sampled rows; only `market_view[:200]`, `risk_level`, and `response_time_ms` are populated. No costs persisted (`prompt_length=0` and no `cost_usd` column in this schema either).
