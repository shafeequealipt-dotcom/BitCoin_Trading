# N6 — Last 24h Failure Inventory

**Collected:** 2026-05-02 ~11:47 UTC
**Window:** 2026-05-01 00:00 UTC – 2026-05-02 11:50 UTC (~36h actually,
covers the last full 24h plus current cycle)
**Sources:** workers.log, workers.2026-05-01_00-01-33_829054.log,
workers.2026-05-02_04-31-00_392071.log, brain.log, general.log
**Filter:** `grep -E "ERROR|CRITICAL|WARNING"` then namespace filter
`src.brain | src.apex | src.trading | src.strategies | src.shadow`
plus a separate scan of `src.workers` for adjacent tags
(strategy_worker, profit_sniper, position_watchdog).

---

## A. brain.log (last 24h, namespace src.brain.*)

| Tag | Count | Source file:line | Sample message | Operational impact |
|---|---|---|---|---|
| `CLAUDE_PROMPT_TRIMMED` | 30 | brain.strategist:2210 (`_build_trade_prompt`) | `CLAUDE_PROMPT_TRIMMED \| site=size reason=chars sections_before=37 sections_after=31 chars_before=17506 chars_after=17162 cap_sections=80 cap_chars=14000` | Prompt over 14000-char cap; lower-priority sections pruned. Trades not blocked but Claude's context truncated. |
| `CLAUDE_PROC_STALL_120S` | 20 | brain.claude_code_client:1201 (`_stream_subprocess_io`) | `CLAUDE_PROC_STALL_120S \| pid=17370 elapsed=120s stdout_so_far=0 timeout_in_s=180 state=S wchan=ep_poll` | Claude CLI silent for 120s. All 20 events recovered before 300s timeout — no cycle was lost to a full timeout in the window. |
| `STRAT_CALL_A_CTX_SLOW` | 3 | brain.strategist:`_build_trade_prompt` | `STRAT_CALL_A_CTX \| sections=49 chars=17660 el=830ms` (slow when build > target threshold) | Prompt build > N seconds; downstream Claude call still proceeds. |
| `STRAT_CALL_A_NO_TRADES` | 2 | brain.strategist:create_trade_plan | (no sample line — JSON parse returned new_trades=[]) | Cycle did nothing. |
| `Claude` (generic) | 2 | brain.claude_code_client | (composite; counted from `Claude attempt N/3 failed` lines) | Retry messages. |
| `STRAT_PROMPT_BUILD_SLOW` | 1 | brain.strategist:`_build_trade_prompt` | (rare — slow prompt assembly) | One-off slow build. |
| `STRAT_CALL_A_FAIL` | 1 | brain.strategist:create_trade_plan | `STRAT_CALL_A_FAIL \| err='...'` | One CALL_A failed completely (parse or claude error). |
| `CLAUDE_PARSE_FAIL` | 1 | brain.claude_code_client | `CLAUDE_PARSE_FAIL \| err='...'` | JSON parse failure. |

**Notes:**
- ZERO `CLAUDE_CALL_TIMEOUT`, `CLAUDE_PROC_KILLED`, `CLAUDE_PROC_PREKILL`,
  `CLAUDE_AUTH`, `CLAUDE_REFRESH_FAIL` events in last 24h.
- 20× STALL_120S all from a recurring pattern: pid spawn → 60s INFO →
  120s WARN → response in 130–135s. Background pattern, not blocking.

---

## B. workers.log + workers.2026-05-0*.log (last 24h)

### B.1 src.brain.* / src.apex.* / src.trading.* / src.strategies.* / src.shadow.*

| Tag | Count | Source file:line | Sample message | Operational impact |
|---|---|---|---|---|
| `REGIME_CHG` | 17 | strategies.regime | `REGIME_CHG \| sym=… old=ranging new=trending_up` | Regime transitions; not an error per se but logged at WARNING. |
| `ORDER_GATE_LM_DEADLINE_EXCEEDED` | 9 | trading.services.order_service:251 (`_enforce_layer3_gate`) | `ORDER_GATE_LM_DEADLINE_EXCEEDED \| link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT purpose=mcp_tool elapsed_s=9848.2 deadline_s=60.0 action=block` | MCP-tool order placement attempts after LayerManager attach deadline elapsed → fail-close. Operator-initiated MCP tools blocked. |
| `ORDER_BLOCKED` | 9 | trading.services.order_service:192 (`_emit_order_blocked`) | `ORDER_BLOCKED \| link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=9848.2` | Same 9 events as above. |
| `ENFORCER_LEVEL` | 8 | strategies.performance_enforcer:265 | `ENFORCER_LEVEL \| old_el=0 new_el=1 \| reason=streak_boost \| pnl=-1.00% strk=-13` | Enforcer escalated to level 1 (capital preservation) on streak boost. Restricts size_mult, lev cap. |
| `APEX_FLIP_RESIZE_BLOCKED` | 7 | apex.optimizer:284 (`optimize`) | `APEX_FLIP_RESIZE_BLOCKED \| sym=NEARUSDT flip=Sell→Buy qwen_size=$1200 forced_to=$500 regime=ranging` | DeepSeek tried to flip+resize; flip allowed, resize blocked → original Claude size used. |
| `APEX_FLIP` | 7 | apex.optimizer:_log_optimization:600 | `APEX_FLIP \| sym=NEARUSDT claude=Sell apex=Buy sl=0.3% tp=0.5% cls=low sz=$500→$500 mode=fixed conf=95% regime=ranging ms=2099` | APEX flipped Claude's direction. |
| `ENFORCER_AUTO_RECOVERY` | 3 | strategies.performance_enforcer:231 | `ENFORCER_AUTO_RECOVERY \| el=1 stuck_for=45min max=45min \| Auto-recovering to el=0` | After 45min at el>=1, auto-revert to 0. Trades re-enabled at full size. |
| `APEX_FLIP_BLOCKED` | 3 | apex.optimizer:266 (`optimize`) | `APEX_FLIP_BLOCKED \| sym=HYPERUSDT reason='flip Buy→Sell in regime=ranging blocked: conf=0.85<0.90' conf=0.85` | DeepSeek wanted to flip below `apex_min_flip_confidence=0.90` → reverted to Claude's direction. |
| `PNL_MANUAL_RESET` | 2 | strategies.pnl_manager | `PNL_MANUAL_RESET` | Manual operator reset of daily PnL. |
| `ENFORCER_MANUAL_RESET` | 2 | strategies.performance_enforcer | Manual operator reset of enforcer level. | Trades re-enabled. |
| `APEX_PRICE_FALLBACK` | 2 | apex.assembler | `APEX_PRICE_FALLBACK \| sym=… source=…` | Mark-price WS unavailable; fell back to alt source. |
| `GATE_TIMING_SLOW` | 1 | apex.gate:342 | `GATE_TIMING_SLOW \| sym=… el=…ms` | One slow gate eval. |

### B.2 src.workers.* / src.risk.* (also in target functional area)

| Tag | Count | Source file:line | Sample message | Operational impact |
|---|---|---|---|---|
| `BASE_WORKER_TICK_SLOW` | 123 | workers.base_worker:349/726 | `BASE_WORKER_TICK_SLOW \| name=kline_worker el=21662ms threshold_ms=8000 interval_s=300.0` | Worker tick exceeded threshold; not blocking but indicates contention. kline_worker is the primary culprit. |
| `Loss` | 29 | (composite — `Loss streak…` etc.) | various | Logged inside enforcer/sniper. |
| `TRADE_SKIP` | 28 | workers.strategy_worker:1129/1164/1199/1256 | `TRADE_SKIP \| sym=AEROUSDT rsn=xray_dir_block detail='ratio=49.3x rr_long=3.5 rr_short=0.1'` | Trade dropped before SHADOW_ORD_SEND. Reasons split: xray_dir_block, xray_skip, enforcer_block. |
| `SNIPER_STALL_ESCAPE` | 28 | workers.profit_sniper | sniper escalated from "actionable=true action=hold" stall | Forced exit. |
| `HIGH` | 24 | composite — `HIGH-priority advisor warnings` | various | sentinel/advisor escalations. |
| `M4_ACT_PARTIAL` | 21 | workers.profit_sniper | partial close action emitted | Position size reduced. |
| `TIME_DECAY_FORCE_CLOSE` | 17 | risk.time_decay_sl:266 | `TIME_DECAY_FORCE_CLOSE \| sym=OPUSDT p_win=0.119 pnl=-0.15% mae=-0.15%` | Probability-based force exit. Closes losing positions when p_win < 0.20. |
| `TIME_DECAY_CLOSE` | 17 | workers.position_watchdog | `TIME_DECAY_CLOSE \| sym=…` | The actual position close event triggered by time-decay engine. |
| `PRICE_STALE` | 17 | (price freshness check) | `PRICE_STALE \| sym=… age_s=…` | Local WS price stale — fallback to Shadow mark. |
| `LAYER_TOGGLE` | 17 | core.layer_manager | `LAYER_TOGGLE` events | Operator/auto layer enable/disable. |
| `XRAY_DIR_MISMATCH` | 14 | workers.strategy_worker | `XRAY_DIR_MISMATCH` | XRAY direction differs from Claude; downstream takes XRAY's view. |
| `GHOST_RECONCILED` | 13 | workers.position_watchdog | `GHOST_RECONCILED \| sym=… local→exchange` | Position reconcile fixed phantom local state. |
| `CLEANUP_LARGE_BATCH` | 12 | workers.cleanup_worker | `CLEANUP_LARGE_BATCH \| rows=…` | Cleanup deleted large batch. |
| `WD_CLOSE` | 10 | workers.position_watchdog | `WD_CLOSE \| sym=…` | Watchdog closed a position. |
| `MODE4_PARTIAL_CAP_REACHED` | 9 | workers.profit_sniper:2359 | `MODE4_PARTIAL_CAP_REACHED \| sym=ATOMUSDT ticks=27 partials_so_far=1 cap=1 escalating_to=full_close current_pnl=-0.01%` | Phase-10 lifetime partial cap hit → escalate to full_close. |
| `M4_ACT_CLOSE` | 9 | workers.profit_sniper | sniper full_close action | Position closed by sniper. |
| `WORKER_NEVER_TICKED` | 7 | workers.worker_liveness_watchdog | `WORKER_NEVER_TICKED \| name=…` | Worker registered but never produced first tick within grace. |
| `STRAT_EXEC_BLOCKED` | 7 | workers.strategy_worker:1160 | `STRAT_EXEC_BLOCKED \| sym=NEARUSDT dir=Buy rsn='PRESERVATION: leverage=5 exceeds limit of 3x (PnL=-0.90%)'` | Enforcer leverage cap blocked trade. |
| `BRAIN_INSUFFICIENT_QUALITY` | 7 | core.layer_manager | `BRAIN_INSUFFICIENT_QUALITY \| avg_completeness=…` | Cold-start gate active — cycle short-circuited. |
| `BRAIN_NO_PACKAGES` | 6 | core.layer_manager:792 | `BRAIN_NO_PACKAGES \| reason=empty_packages_cache trades_dropped=2` | Empty `_coin_packages`; Claude directives discarded. |
| `SERVICES_MISSING` | 5 | various services | `SERVICES_MISSING` | Service registry missing dependency. |
| `SENTINEL_ADVISOR_SLOW` | 5 | workers.sentinel | DeepSeek advisor call slow | Advisor still completed. |
| `SENTIMENT_DEGRADED_MODE` | 5 | analysis.sentiment | Sentiment provider degraded | Used contrarian-only. |
| `Recovery` | 5 | composite | various | Auto-recovery messages. |
| `REDDIT_DISABLED` | 5 | workers.reddit_worker | startup info | Just startup notice. |
| `EVENT_LOOP_LAG` | 5 | core.event_loop_monitor | `EVENT_LOOP_LAG \| el=…ms` | Event loop slow. |
| `EVENT_LOOP_BLOCKER` | 5 | core.event_loop_monitor | `EVENT_LOOP_BLOCKER \| stack=…` | Async loop blocked. |
| `STRAT_PREFETCH_SLOW` | 4 | workers.strategy_worker | `STRAT_PREFETCH_SLOW \| el=…` | Prefetch pass slow. |
| `WORKER_TICK_OVERDUE` | 3 | workers.worker_liveness_watchdog | `WORKER_TICK_OVERDUE \| name=…` | Worker missed expected tick window. |
| `WD_MODE` | 3 | workers.position_watchdog | mode flag change | Notice. |
| `STRAT_ACTION_CLOSE` | 3 | workers.strategy_worker | strategist-driven close | Position closed via Claude direction. |
| `XRAY_DIR_REDUCE` | 2 | workers.strategy_worker | XRAY reduces size | Reduced size, not blocked. |
| `WD_MONITOR_TIMEOUT` | 2 | workers.position_watchdog | monitor lap timeout | Re-evaluation skipped. |
| `WD_MONITOR_SLOW` | 2 | workers.position_watchdog | monitor lap slow | Notice. |
| `XRAY_BLOCK` | 1 | workers.strategy_worker:1195 | `XRAY_BLOCK \| sym=BCHUSDT quality=SKIP rr=0.4 \| Trade rejected — structurally invalid` | Trade rejected at XRAY quality gate. |
| `WD_POLL_LAG` | 1 | workers.position_watchdog | poll lag | Notice. |
| `STRAT_PREFETCH_CRITICAL` | 1 | workers.strategy_worker | prefetch critical fail | Single critical event. |

---

## C. general.log (filtered to 2026-05-01..02)

NOT FOUND for our namespace filter — ZERO matching ERROR/WARNING events in
last 24h in general.log when filtered to src.brain | src.apex |
src.trading | src.strategies | src.shadow. (Earlier general.log entries
exist for older Bybit-rejection errors, but all timestamped 2026-04-26
or earlier — outside our 24h window.)

---

## D. Top-line summary

- ZERO actual order rejections by Bybit/Shadow in 24h.
- 9 ORDER_BLOCKED — all from MCP-tool path with lm_deadline_exceeded
  (operator-initiated, system not yet attached).
- 7 STRAT_EXEC_BLOCKED — Enforcer leverage cap blocking APEX-flipped
  high-leverage trades.
- 20 XRAY_DIR_BLOCK + 14 XRAY_DIR_MISMATCH + 1 XRAY_BLOCK — biggest
  single source of Stage 2 → Layer 3 trade-loss in this window. Almost
  every Claude/APEX-approved trade hits the XRAY direction filter.
- 7 BRAIN_INSUFFICIENT_QUALITY + 6 BRAIN_NO_PACKAGES — 13 cycles
  prevented Claude work from reaching APEX/OrderService at all.
- 17 TIME_DECAY_FORCE_CLOSE + 17 TIME_DECAY_CLOSE — biggest exit
  reason, accounts for most of the 24-loss day.
- 123 BASE_WORKER_TICK_SLOW (mostly kline_worker) — DB contention
  warning but not a blocker.
- ZERO Claude-side hard timeouts/auth fails/billing fails in 24h.
