# H4 — Brain cycle orchestration

Collected: 2026-05-02. Logs window: last 24h (combined `brain.log`, `workers.log`, prior `workers.2026-05-02_04-31-00_392071.log`).

## Cycle scheduler

- File: `src/core/layer_manager.py`. Class `LayerManager`.
- Loop entry: `_brain_review_loop` (line 698) — created from `_start_brain_layer` at line 666 (`self._brain_task = asyncio.create_task(self._brain_review_loop())`).
- Body (lines 712-724):
  ```
  while self._layer_active[2]:
      try:
          await self._run_brain_cycle()
      except asyncio.CancelledError: break
      except Exception as e: log.error("Brain cycle failed: {err}", err=str(e))
      try:
          await asyncio.sleep(self.brain_interval_seconds)
      except asyncio.CancelledError: break
  ```
- Interval source: `self.brain_interval_seconds = 150` defaulted at layer_manager.py:85; overridden by `WorkerManager` at `src/workers/manager.py:570`:
  ```
  layer_manager.brain_interval_seconds = getattr(settings.brain, 'strategic_interval', 150)  # 2.5 min
  ```
- Layer-active gating: `_layer_active[2]` (BRAIN flag) — toggled by `_start_brain_layer` (line 663) and `_stop_brain_layer` (line 686). Layer 2 must be active for the loop to keep iterating; the `await asyncio.sleep` is interruptable on cancel.
- Strict A/B alternation switch: `self._call_type` (string `"A"` or `"B"`) — see H1 for full mechanics. Both success and failure paths flip the switch (layer_manager.py:755, 874, 897, 935).

## Pre-call checks (CALL A path)

In order, inside `_run_brain_cycle` and `_execute_trades_background`:

1. **Layer 2 active**: not explicitly re-checked inside `_run_brain_cycle` (`_brain_review_loop` already guards with `while self._layer_active[2]`).
2. **Strategist available**: `if not strategist: log.warning("No strategist service available"); return` (layer_manager.py:736-738).
3. **Pre-call inside strategist**: none (CALL A always enters `_build_trade_prompt`).
4. **Post-strategist `cold_start_block_or_none`** (line 790-793). Implemented at `_cold_start_block_or_none(plan)`:
   - Rule 1: when `[brain.cold_start_protection].enabled=False` returns `None`.
   - Rule 2: empty `_coin_packages` ⇒ `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=N`.
   - Rule 3: boot grace — `time.time() - boot_time < boot_grace_period_sec` requires `avg_completeness >= boot_grace_completeness` (default 0.95) else `BRAIN_LOW_COMPLETENESS`.
5. **Concurrent-execution guard**: `if self._background_exec_task and not self._background_exec_task.done()` (layer_manager.py:798-806) — emits `BRAIN_DO_SKIP | prev_still_running el=...s trades=N`.
6. **Layer 3 active** for execution-routing: `if self._layer_active[3]:` (line 784) — when False, drops with `BRAIN_TRADES_DROPPED | layer=3_inactive trades_count=N sample_syms=[...]` (line 829-833).
7. **Inside `_execute_new_trades`** (per-trade, after schedule): `pnl_manager.can_trade()` → `BRAIN_TRADE_HALT` (line 1194-1198); `enforcer.check_and_enforce()` then `enforcer.should_allow_trade(leverage=1)` → `STRAT_L4_HALT` (line 1218-1222); `[POS] gate` → `POS_GATE_BLOCK` + `TRADE_SKIP rsn=pos_gate` (line 1290-1299).

## Pre-call checks (CALL B path)

1. **Strategist available**: same check (layer_manager.py:736).
2. **Open positions**: `position_service.get_positions()` short-circuit — empty list → `BRAIN_CYCLE_B_SKIP | rsn='no open positions'` (line 884), no Claude call.
3. **Price-divergence defer** (in strategist.py:507-523, `create_position_plan`): if `_has_blocking_price_divergence()` True, emits `PROMPT_DEFERRED | rsn=price_divergence max_div=...% threshold=...%` and returns None. **24h count: 0**.
4. **Recent failure backoff**: NOT FOUND as a separate check at the brain-cycle level. The `claude_code_client._consecutive_failures`/`_adaptive_interval` mechanic (claude_code_client.py:316-318) handles it at the CLI client layer (rate-limit gate at lines 246-251).
5. **Cost ceiling**: NOT enforced for `ClaudeCodeClient` — `ClaudeCodeCostTracker.can_afford_call(...)` always returns `True` (claude_code_client.py:1454-1455). The legacy `BrainV2.evaluate_setups` does check `self.cost_tracker.can_afford_call()` (`src/brain/brain_v2.py:112`) but that path is not invoked from `_run_brain_cycle`.

## Post-call processing chain (CALL A)

Inside `_run_brain_cycle` (success path lines 759-873):

1. **Parse**: already done by `create_trade_plan` (returns parsed `StrategicPlan`).
2. **Merge into `_current_plan`**: market_view, risk_level, max_positions, default_*, new_trades, coin_directives, focus/avoid_coins, raw_reasoning, created_at (lines 760-779). Logged: nothing direct; `STRAT_CALL_A_PLAN | trades=N risk=... view='...'` was already emitted by the strategist at strategist.py:471.
3. **Plan history**: append, cap at 20 (lines 776-778).
4. **Data-lake write**: `_record_decision_to_data_lake(plan, elapsed_ms, "call_a")` (line 781) → `data_lake.write_claude_decision(...)` → `DL_DECISION | type=call_a trades=2 acts=0 el=Nms prompt=0` (data_lake module).
5. **Cold-start gate**: `_cold_start_block_or_none(plan)` at line 790. If non-None, emits the block tag and `_send_cold_start_telegram(block_reason)` (line 793) — does NOT route trades.
6. **Layer 3 active gate** (line 784, 798) — see above.
7. **Concurrent-execution guard** (line 798-806).
8. **Schedule background exec**: `self._background_exec_task = asyncio.create_task(self._execute_trades_background(plan))` (line 810).
9. **Background exec wraps** `_execute_new_trades` with 300 s timeout (line 1164: `await asyncio.wait_for(...,timeout=300)`). Logs `BRAIN_DO_START | trades=N` (1163), `BRAIN_DO_DONE | el=Ns` (1166), or `BRAIN_DO_TIMEOUT | el=Ns | aborted` (1169) / `BRAIN_DO_FAIL | el=... err=...` (1174). Records DO elapsed in `_cycle_times["DO"]`.
10. **Inside `_execute_new_trades`**: per-directive APEX optimize (parallel `asyncio.gather`) → APEX gate validate → strategy_worker._execute_claude_trade → OrderService.place_order. Per-trade emit: `BRAIN_DO_TRADE | sym=X [n/m] el=...ms apex_apply=...ms apex_ds=...ms gate=...ms exec=...ms rsn=...` (line 1368-1373). Aggregate emit: `Claude new trades: N/M executed | skipped={k1=n1,...}` (line 1378).
11. **Urgent position_actions** (CALL A's optional payload): `_execute_position_actions(plan, source="call_a_urgent")` if `_has_urgent_concerns` was True (line 845).
12. **Telegram alert**: `_send_plan_telegram(plan)` (line 860) → fire-and-forget `alert_manager.send_custom(...)`.
13. **Cycle-end log**: `BRAIN_CYCLE_A_DONE | el=Nms trades=N view='...'` (layer_manager.py:862-865).

## Post-call processing chain (CALL B)

Inside `_run_brain_cycle` (success path lines 901-931):

1. **Parse**: already done by `create_position_plan`. Strategist emits `STRAT_CALL_B_PARSED | total=N hold=A close=B tighten=C set_exit=D take_profit=E` (strategist.py:2857).
2. **Merge `position_actions`** into `_current_plan` (line 903).
3. **Data-lake write**: `_record_decision_to_data_lake(plan, elapsed_ms, "call_b")` (line 908).
4. **Layer 3 gate** (line 911) — when False, drop with `Layer 3 inactive — skipped {N} position actions` (line 916).
5. **Execute via `_execute_position_actions(plan, source="call_b")`** (line 912 → 1100-1147):
   - Skip `action=="hold"`.
   - SENTINEL Exit Firewall: `should_allow_strategic_action(action, symbol, reason, source)` (line 1121-1125).
   - Set close attribution for `close`/`take_profit` (line 1136-1137).
   - Queue: `coordinator.queue_strategic_action(symbol, action, reason, new_sl, exit_price)` (line 1139-1145). PositionWatchdog dequeues and executes on its next tick.
6. **Telegram alert**: `_send_plan_telegram(plan)` (line 922).
7. **Cycle-end log**: `BRAIN_CYCLE_B_DONE | el=Nms acts=N` (line 924).

## CALL_A failure modes (24h)

| log tag | recovery action | 24h count |
|---|---|---:|
| `BRAIN_CYCLE_A_FAIL` (layer_manager.py:751) | flip `_call_type` to "B"; record DO time; emit BRAIN_HEALTH if threshold | **0** |
| `STRAT_CALL_A_FAIL` (strategist.py:490) | strategist returns `None`; layer_manager logs `BRAIN_CYCLE_A_DONE | empty_plan=Y` | **1** (the 05:10:56 parse-failure) |
| `CLAUDE_CALL_FAIL` (claude_code_client.py:500) | `BrainError` raised; caught by strategist as `STRAT_CALL_A_FAIL` | **0** |
| `CLAUDE_PARSE_FAIL` (claude_code_client.py:552) | `ValueError` raised; caught by strategist as `STRAT_CALL_A_FAIL` | **1** (`raw_response='System status check blocked by permissions...'`) |
| `CLAUDE_CALL_TIMEOUT` (claude_code_client.py:467) | `RuntimeError` from `_subprocess_call`; retried per `max_retries`; if still failing → `CLAUDE_CALL_FAIL` | **0** |
| `BRAIN_DO_TIMEOUT` (layer_manager.py:1169) | aborts background trade exec at 300s; preserves brain loop | **0** |
| `BRAIN_DO_FAIL` (1174) | unexpected exception in trade-exec wrapper; loop continues | **0** |
| `BRAIN_DO_SKIP` (804) | skip when prior background exec still running; brain loop continues | **0** |
| `BRAIN_TRADE_HALT` (1197) | manual-pause via pnl_manager; halts new-trade loop for the cycle | **0** |
| `STRAT_L4_HALT` (1221) | enforcer-blocked; halts new-trade loop for the cycle | **0** |
| `BRAIN_NO_PACKAGES` (792) | cold-start gate; trades dropped, telegram emitted | **1** (`reason=empty_packages_cache trades_dropped=2 did=d-1777720966952` at 11:24:01 — post-restart) |
| `BRAIN_LOW_COMPLETENESS` | cold-start gate (boot grace) | **0** |
| `BRAIN_TRADES_DROPPED | layer=3_inactive` (829) | layer-3 inactive drop | **0** |
| `CLAUDE_PROMPT_TRIMMED` (strategist.py:2210) | normal trim — chars or sections cap reached | **30** (every CALL_A in window with ≥17 KB prompt) |
| `CLAUDE_PROC_STALL_60S` | informational; no action | **50** |
| `CLAUDE_PROC_STALL_120S` | warning; capture wchan + state | **16** |

## CALL_B failure modes (24h)

| log tag | recovery action | 24h count |
|---|---|---:|
| `BRAIN_CYCLE_B_FAIL` (layer_manager.py:893) | flip `_call_type` to "A"; record DO time | **0** |
| `STRAT_CALL_B_FAIL` (strategist.py:553) | strategist returns `None`; cycle still flips `_call_type` | **0** |
| `BRAIN_CYCLE_B_SKIP | rsn='no open positions'` (884) | normal short-circuit when no positions | **observed at 11:26:32 (did=d-1777720966952)** |
| `PROMPT_DEFERRED | rsn=price_divergence` (strategist.py:516) | skip CALL B; emit `STRAT_CALL_B_END | deferred=Y` | **0** |
| `STRAT_CALL_B_BAD_SHAPE` / `STRAT_CALL_B_BAD_ACTIONS` / `STRAT_CALL_B_BAD_ACTION` / `STRAT_CALL_B_BAD_ACTION_TYPE` / `STRAT_CALL_B_DOWNGRADE` (strategist.py:2792-2842) | invalid actions silently downgraded to "hold" | **0** |
| `CLAUDE_PARSE_FAIL` (any source) | strategist returns None | **0** for CALL_B (the 1 in window was CALL_A) |
| `STRAT_REFRESH_FAIL` (strategist.py:313) | position_service exception during `refresh_positions`; CALL B falls back to direct `position_service.get_positions()` (strategist.py:2273) | **0** |

## End-to-end timing for one CALL A (did=d-1777702618197)

```
06:16:58.197  BRAIN_CYCLE_A | Finding new trades                                                            | layer_manager.py:745
06:16:58.197  STRAT_CALL_A_START                                                                            | strategist.py:416   t=0
06:16:58.199  STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=178 age_max_s=178                   | strategist.py:1684  +2ms
06:16:58.544  STRAT_PROMPT_BUILD | sections=37 | (per-section ms breakdown above)                           | strategist.py:2149  +347ms
06:16:58.545  STRAT_PROMPT_SIZE | sections=37 chars=17506                                                   | strategist.py:2180  +348ms
06:16:58.545  CLAUDE_PROMPT_TRIMMED | site=size reason=chars sections_after=31 chars_after=17162            | strategist.py:2210  +348ms
06:16:58.545  STRAT_CALL_A_CTX | sections=31 chars=17162 el=348ms                                           | strategist.py:2219  +348ms
06:16:58.546  PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17192 sections=31 packages=15 elapsed_ms=348 | strategist.py:2223 +349ms
06:16:58.546  STRAT_CALL_A | chars=17192                                                                    | strategist.py:419   +349ms
06:16:58.547  CLAUDE_CALL_START | call_id=49 in=17192 sys=8985 timeout=300s hash=e507ed26d18e               | claude_code_client.py:262 +350ms
06:16:58.576  CLAUDE_PROC_SPAWNED | pid=17370 spawn_ms=19                                                   | claude_code_client.py:980 +379ms
06:17:58.605  CLAUDE_PROC_STALL_60S | pid=17370 elapsed=60s stdout_so_far=0 timeout_in_s=240                 | claude_code_client.py:1201 +60.4s
06:18:58.621  CLAUDE_PROC_STALL_120S | pid=17370 elapsed=120s stdout_so_far=0 state=S wchan=ep_poll          | claude_code_client.py:1201 +120.4s
06:19:11.884  CLAUDE_CALL_OK | call_id=49 attempt=1/3 el=133327ms out=2112 calls=49                          | claude_code_client.py:305 +133.3s
06:19:11.885  STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime ...'                   | strategist.py:471   +133.3s
06:19:11.885  STRAT_DIRECTIVE | #1 sym=INJUSDT  dir=Buy  lev=2 rsn='TRENDING_UP regime, score 76.1...'      | strategist.py:480
06:19:11.885  STRAT_DIRECTIVE | #2 sym=NEARUSDT dir=Sell lev=2 rsn='RANGE_FADE_SHORT, no cooldown...'       | strategist.py:480
06:19:11.886  STRAT_CALL_A_END | el=133689ms trades=2                                                        | strategist.py:486   +133.7s
06:19:11.886  BRAIN_CYCLE_A_DONE | el=133689ms trades=2 view='Ranging global regime with fear (F&G=39)...'   | layer_manager.py:862 +133.7s
```

Total wallclock: 133.689 s. Phase breakdown:
- Trigger → STRAT_CALL_A_START: 0 ms (synchronous).
- Prompt build (refresh, scan, trim): 349 ms (`STRAT_CALL_A_CTX el=348ms`).
- Send to CALI subprocess + spawn: 30 ms.
- Claude latency (subprocess wallclock): 133,308 ms (`CLAUDE_CALL_OK el=133327` minus 19 ms spawn).
- Parse + plan log: ~1 ms.
- Layer-manager `BRAIN_CYCLE_A_DONE` log emit: ~1 ms.
- Routing/validation completion: NOT FOUND for this `did` — Layer 3 was inactive on this restart (no `BRAIN_DO_*` events in window). Routing path would have taken the form `_record_decision_to_data_lake → _cold_start_block_or_none → _execute_trades_background → _execute_new_trades → APEX → gate → strategy_worker._execute_claude_trade`.

## End-to-end timing for one CALL B (did=d-1777702389333)

```
06:13:09.333  BRAIN_CYCLE_B | Managing positions                                                           | layer_manager.py:878 t=0
06:13:09.333  STRAT_CALL_B_START                                                                           | strategist.py:499  +0ms
06:13:09.337  STRAT_PROMPT_REFRESH | n_positions=1 source=shadow_live cleared_invalidated=1                | strategist.py:321  +4ms
06:13:09.341  STRAT_CALL_B_CTX | positions=1 chars=1146 el=8ms                                             | strategist.py:2396 +8ms
06:13:09.342  PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1159 sections=14 elapsed_ms=8         | strategist.py:2398 +9ms
06:13:09.342  STRAT_CALL_B | chars=1159                                                                    | strategist.py:527  +9ms
06:13:09.342  CLAUDE_CALL_START | call_id=48 in=1159 sys=1338 timeout=300s hash=28b4a21bfe11               | claude_code_client.py:262 +9ms
06:13:09.375  CLAUDE_PROC_SPAWNED | pid=16852 spawn_ms=20                                                  | claude_code_client.py:980 +42ms
06:14:09.385  CLAUDE_PROC_STALL_60S | pid=16852 elapsed=60s stdout_so_far=0 timeout_in_s=240                | +60.0s
06:14:28.193  CLAUDE_CALL_OK | call_id=48 attempt=1/3 el=78839ms out=578 calls=48                           | claude_code_client.py:305 +78.9s
06:14:28.194  STRAT_CALL_B_PARSED | total=1 hold=0 close=1 tighten=0 set_exit=0 take_profit=0               | strategist.py:2857 +78.9s
06:14:28.194  STRAT_CALL_B_PLAN | acts=1                                                                    | strategist.py:539  +78.9s
06:14:28.194  STRAT_POS_ACT | sym=MANAUSDT act=close rsn='Regime is DEAD 80% — fundamentally ...'           | strategist.py:543  +78.9s
06:14:28.194  STRAT_CALL_B_END | el=78861ms acts=1                                                          | strategist.py:549  +78.9s
06:14:28.196  BRAIN_CYCLE_B_DONE | el=78865ms acts=1                                                        | layer_manager.py:924 +78.9s
```

Total wallclock: 78.865 s. Phase breakdown:
- Trigger → STRAT_CALL_B_START: 0 ms.
- Position refresh (live shadow read): 4 ms (cleared 1 invalidated symbol).
- Prompt build: 8 ms (`STRAT_CALL_B_CTX el=8ms` — no TA / X-RAY).
- Send to CLI + spawn: 33 ms.
- Claude latency (subprocess): 78,819 ms (~79 s — short prompt → short response, but still 60+s baseline).
- Parse + downgrade-validation: ~1 ms (`STRAT_CALL_B_PARSED total=1 close=1`).
- Routing (queue strategic action via TradeCoordinator → PositionWatchdog): NOT FOUND a discrete log at this `did`; the watchdog tick that picks up the close on its next iteration does emit `WD_TICK | mode=safety_net n=N syms=[...]` at the workers.log tail (sample seen at 11:47:56), but the bridge from `coordinator.queue_strategic_action` to the executed close is via the watchdog and is not stamped with the originating `did` in the logs surfaced.
- BRAIN_CYCLE_B_DONE: 4 ms after STRAT_CALL_B_END.
