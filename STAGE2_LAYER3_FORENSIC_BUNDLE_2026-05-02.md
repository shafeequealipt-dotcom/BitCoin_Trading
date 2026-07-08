# STAGE 2 + LAYER 3 FORENSIC BUNDLE — 2026-05-02

Single-file concatenation of all 28 forensic collection files.

**Collection window:** 2026-05-02 11:45–12:08 UTC
**Spec source:** `/home/inshadaliqbal786/COLLECT_STAGE2_AND_LAYER3_FORENSIC_DATA (1).md`
**Project:** `/home/inshadaliqbal786/trading-intelligence-mcp`
**DB snapshot:** `/tmp/trading_snapshot_1777722335.db` (138 MB)
**Live system at capture:** workers.py PID 398; cycle_active=False; layer_state.json L1=true L2=false L3=false user_stopped=true

Components in order: INDEX → H (brain) → I (APEX) → J (enforcer/pnl) → K (gate) → L (orders/exchanges/funds) → M (wiring) → N (e2e/config/live/failures/tias).

Per-file contents below. Each section header marks the start of the next file.

---


=====================================================================
## FILE: INDEX.md
=====================================================================

# Stage 2 + Layer 3 Forensic Data Collection — INDEX

**Collection date:** 2026-05-02 11:45–12:08 UTC (~23 minutes wall clock; 6 parallel collection agents)
**Spec source:** `/home/inshadaliqbal786/COLLECT_STAGE2_AND_LAYER3_FORENSIC_DATA (1).md`
**Project:** `/home/inshadaliqbal786/trading-intelligence-mcp`
**Shadow:** `/home/inshadaliqbal786/shadow`
**DB snapshot:** `/tmp/trading_snapshot_1777722335.db` (138 MB, captured 11:45 UTC)
**Live system:** workers.py PID 398 (alive); server.py PID 399 (alive); cycle_active=False at capture; Layer state L1=true, L2/L3/L4=false (user_stopped=true)

This collection REPLACES the 2026-04-28 baseline (the prior INDEX.md and earlier files in this directory).

---

## File inventory (28 files, ~386 KB)

| File | Size | Topic |
|---|---|---|
| INDEX.md | this file | Index + key observations + gaps |
| H1_claude_strategist.md | 27 KB | ClaudeStrategist core (`src/brain/strategist.py`, 2864 LOC) |
| H2_claude_cli_client.md | 13 KB | Claude CLI subprocess (`src/brain/claude_code_client.py`, 1465 LOC) |
| H3_brain_decision_output.md | 19 KB | Decision JSON shape, validation, did= propagation |
| H4_brain_cycle_orchestration.md | 17 KB | Brain review loop, scheduler, pre/post-call processing |
| I1_apex_architecture.md | 10 KB | APEX file structure + role + apex_optimized flag |
| I2_apex_assembler.md | 11 KB | IntelligenceAssembler |
| I3_apex_optimizer.md | 16 KB | TradeOptimizer + Qwen via OpenRouter |
| I4_apex_gate.md | 11 KB | APEX gate (14 checks) + conviction weight |
| J1_performance_enforcer.md | 15 KB | PerformanceEnforcer (mode ladder, coaching) |
| J2_pnl_manager.md | 11 KB | DailyPnLManager (7-mode ladder) |
| K1_trade_gate.md | 18 KB | OrderService L3 placement gate (the order-side gate) |
| K2_order_block_audit.md | 8 KB | ORDER_BLOCKED taxonomy (last 24h) |
| L1_order_service.md | 15 KB | OrderService.place_order (1156 LOC, 9 callers) |
| L2_bybit_client.md | 10 KB | BybitClient pybit wrapper + rate limiter |
| L3_shadow_adapter.md | 13 KB | ShadowOrderService + signature parity tests |
| L4_fund_manager.md | 17 KB | IntelligentFundManager + FundReconciler |
| M1_caches.md | 13 KB | 10 in-memory caches inventoried |
| M2_db_tables.md | 17 KB | DB schemas + row counts + indexes |
| M3_service_registry.md | 14 KB | 87 service writes, 61 distinct keys, bootstrap order |
| M4_layer_active_gating.md | 15 KB | layer_state.json + LAYER_STATE_SYNC heartbeat |
| N1_e2e_trade_trace.md | 15 KB | **MOST VALUABLE** — full E2E trace (placed + blocked + timeout) |
| N2_stage2_config.md | 8 KB | `[brain]` config + hardcoded constants |
| N3_layer3_config.md | 12 KB | `[apex/risk/fund/enforcer/sl/tias]` + hardcodes |
| N4_live_snapshots.md | 7 KB | Brain/APEX/Enforcer/Gate/Order/Fund state at one timestamp |
| N5_brain_cycles.md | 9 KB | Last 5 CALL_A + last 5 CALL_B detailed |
| N6_failures_24h.md | 12 KB | WARNING/ERROR/CRITICAL inventory by namespace |
| N7_tias_integration.md | 13 KB | TIAS hooks into Stage 2 + Layer 3 |

---

## Headline observations (cross-cutting, with citations)

### 1. Brain CALL_A is healthy but slow
- 34 CALL_A + 20 CALL_B in last 24h (`brain.log`)
- Claude latency p50/p95/max = **109,652 / 160,919 / 169,393 ms**
- Every CALL_A trips the 14K char prompt cap (30 CLAUDE_PROMPT_TRIMMED events)
- 50 STALL_60S + 16 STALL_120S + 0 STALL_240S — stall ladder firing but never escalating to kill
- 1 CLAUDE_PARSE_FAIL (Claude returned a permissions-blocked refusal instead of JSON)
- 1 CLAUDE_PREFLIGHT_REFRESH (mins_left=-82.7) recovered cleanly
- `brain.log` silent since 11:24 UTC — `cycle_active=False` per `worker_liveness_watchdog`

### 2. APEX wiring is now live (memory was stale)
- `apex_optimized` flag in DB: **594/821 historical rows = 1**, **34/34 last-24h rows = 1** — contradicts prior "0 for all trades" memory note
- APEX_FLIP discipline (24h): 6 allowed, 2 confidence-blocked, 6 size-resize-blocked; ALL flips occurred in `regime=ranging` (the unlocked regime)
- **Gap confirmed (Issue 9):** NO rolling/time-windowed FLIP-rate check exists; `flip_rate` is only a cumulative health stat (`src/apex/optimizer.py:640`)
- Qwen timeout is **60s** in production config (`config.toml:960`, raised from 30s); 0 APEX_TIMEOUT events in 24h

### 3. Layer-active gating is hard-enforced; live state shows all layers stopped
- `data/layer_state.json`: L1=true, L2=false, L3=false, user_stopped=true
- LAYER_STATE_SYNC heartbeat runs at 60s cadence; `_drift_action="rewrite_disk"` (memory→disk recovery direction)
- L2 gate is loop-boundary in `_brain_review_loop`
- L3 gate is hard-enforced in `OrderService._assert_layer3_allows` with race-check via `LayerSnapshot`
- ProfitSniper/Watchdog do NOT consult `is_layer_active(4)` — Layer 4 is naming convention only

### 4. Order-side gate disambiguation
- TWO components share the name "TradeGate"
- The order-side gate is `OrderService._enforce_layer3_gate()` at `src/trading/services/order_service.py:199-397`
- Per-symbol cooldowns implemented at `src/trading/trade_coordinator.py:544-551` with **3/10/15 min** tiers (NOT 5/10/15 from memory — gap)
- Cooldown is consumed by APEX gate (size-halving at `src/apex/gate.py:174-186`), NOT by OrderService

### 5. Last 24h ORDER_BLOCKED taxonomy
- Exactly **4 ORDER_BLOCKED** events
- All `lm_deadline_exceeded`, all from `mcp_tool` purpose, all Buy side
- INJUSDT/ONDOUSDT 05:10, AXSUSDT/MANAUSDT 06:01
- Root cause: MCP-side OrderService never had LayerManager attached → all calls fail-close after 60s boot deadline
- Event payload lacks `did=` and notional (gap)

### 6. Shadow exchange operational
- 35 SHADOW_ORDER_RECEIVED events in 24h
- **100% fill rate**, average end-to-end latency **~11.7 ms**
- Shadow `place_order` POST has neither timeout nor retry (gap)
- Signature parity with OrderService is tested in `tests/test_shadow_signature_parity.py`

### 7. Fund manager + reconciler healthy
- 420 FUND_RECONCILE events / 0 DRIFT / 0 AUTO_CORRECT / 0 FAIL in 24h
- 110007 ErrCode count = 0
- Tiered allocation: ROOKIE 20% → MASTER 60%
- Reconciler drift is structurally degenerate in shadow mode (same source for both sides)
- `fund_manager_log` table empty
- Actual file path is `src/fund_manager/manager.py` (prompt's `src/services/fund_manager.py` does not exist — gap)

### 8. PnL Manager state
- 7 modes (TARGET_HIT / PROTECT / GOOD_DAY / NORMAL / CAUTION / SURVIVAL / HALTED) at `src/strategies/pnl_manager.py:204-282`
- `can_trade()` reads `_manual_pause` and `mode["mode"]=="HALTED"` only — no consecutive-loss breaker (streak is tracked but never read; gap)
- `PNL_DAILY` log emit NOT FOUND in 24h (worker tick not driving update; gap)

### 9. Performance Enforcer state
- 577 LOC at `src/strategies/performance_enforcer.py`, 16 public methods
- Stats sourced from `trade_thesis` DB query (line 321-328)
- Coaching text built at lines 428-499 but NOT log-emitted — injected directly into Claude's prompt at `src/brain/strategist.py:564`
- 24h state pinned at `el=1 (CAPITAL_PRESERVATION)`
- Event name is `ENFORCER_STATE` not `ENFORCER_STATS` (memory was stale)

### 10. DB tables: data lake reality
- `orders` rows: **0**
- `positions` rows: **0**
- `trade_thesis` rows: **1257**
- `trade_intelligence` rows: **821** (TIAS)
- `claude_decisions` rows: **1232** (active sink)
- `brain_decisions` rows: **0** (writers exist but unused — write path dead)
- `account_snapshots` rows: **47514**
- `fund_manager_state` rows: **4** (with live values)
- `fund_manager_log` rows: **0**
- `apex_decisions` and `enforcer_stats` tables: **NOT FOUND**

### 11. Service registry
- 87 `self._services[...]=...` writes in `src/workers/manager.py`
- 61 distinct keys
- APEX/TIAS/Brain/Telegram all consume `regime_detector` (the detector with `_per_coin_regimes`)
- Only ScannerWorker uses `regime_worker`
- Multiple late-wires documented (regime_detector to watchdog/scanner/profiler at `src/workers/manager.py:1145-1155`; LayerManager to BybitOrderService at 593-602)

### 12. TIAS integration
- DeepSeek timeout is **45s** (config + code), not 30s and not 60s as memory suggested — gap
- Most "Phase 3" data gaps appear wiring-fixed today (claude_thesis stored, M4 snapshot present, signal score forwarded)
- `m4_peak_pnl_pct` values still sparse in 24h sample due to short holds
- Entry-time indicator columns exist in schema, but the collector still reads close-time values (residual gap)

### 13. `did=d-1777720966952` (provided E2E example)
- Claude returned 2 directives at 11:24:01 UTC
- Both dropped at LayerManager via `BRAIN_NO_PACKAGES | empty_packages_cache`
- Never reached APEX/Gate/OrderService — no order placed
- Most recent placed order traced E2E in `N1_e2e_trade_trace.md`: ONDOUSDT did=d-1777703051893 at 06:26:33 UTC

---

## Documented gaps (collection findings, NOT recommendations)

1. **APEX optimization queue** — NOT FOUND
2. **`apex_decisions` DB table** — NOT FOUND
3. **`enforcer_stats` DB table** — NOT FOUND
4. **`ENFORCER_STATS` log tag** — does not exist; actual emit is `ENFORCER_STATE`
5. **Coaching text never log-emitted** — injected to prompt directly, opaque to ops
6. **`PNL_DAILY` log emit** — not firing in 24h
7. **`ORDER_PREFLIGHT_INSUFFICIENT` log tag** — NOT FOUND
8. **ORDER_BLOCKED event payload** — lacks `did=` and notional
9. **Cooldown tier values** — actual is **3/10/15 min**, memory said 5/10/15
10. **Shadow `place_order` POST** — no timeout, no retry
11. **Fund manager path** — actual `src/fund_manager/manager.py`, not `src/services/fund_manager.py`
12. **Bybit rate-limit value** — hardcoded at 10 calls/sec, NOT wired from `settings.bybit.rate_limit_per_second`
13. **PnL consecutive-loss breaker** — streak tracked but never read by `can_trade()`
14. **`brain_decisions` table** — writers exist, table empty (write path dead)
15. **APEX rolling FLIP-rate window** — only cumulative `flip_rate` exists (Issue 9 confirmed)
16. **Reconciler drift in shadow mode** — structurally degenerate (same source both sides)
17. **TIAS m4 peak pnl pct** — sparse in 24h due to short holds
18. **TIAS entry-time indicator capture** — schema columns present, collector reads close-time values
19. **TIAS DeepSeek timeout** — actual 45s, memory said 30s→60s
20. **`dev_notes/APEX_COMPLETE_INTEGRATION_PROMPT.md`** — file does not exist; philosophy documented from `src/apex/prompts.py:25-26` + `src/apex/optimizer.py:4-5`
21. **Layer 4 (`is_layer_active(4)`)** — naming convention only, not consulted by ProfitSniper/Watchdog
22. **Cache contents not periodically dumped** — no snapshot mechanism for in-memory caches
23. **Telegram `/control` callback** — does not re-check `is_authorized` (relies on upstream filter)

---

## Hard rules compliance

- Verbatim over paraphrase — each module file pastes log lines, code snippets, config blocks
- Measurements over estimates — every runtime claim has a count, latency, or distribution
- Code-level evidence — file:line citations on every code claim
- Live state snapshots with timestamps — capture window 11:45–12:08 UTC, 2026-05-02
- Gaps documented explicitly — 23 itemized above; NOT FOUND markers throughout
- One file per component — 28 files, no bundling within forensic dir
- End-to-end trace in N1 — placed + blocked + timeout traces, all timestamped
- No fix proposals, no architecture critique, no editorializing

---

## Pre-conditions verified

1. System running (workers.py PID 398 alive at capture)
2. At least one CALL_A in last 30 min (11:24:01 UTC, did=d-1777720966952)
3. At least one Layer 3 order attempt in last hour (4 ORDER_BLOCKED + 35 SHADOW_ORDER_RECEIVED)
4. DB snapshot taken: `/tmp/trading_snapshot_1777722335.db` (138 MB)
5. Read access verified across `src/brain/`, `src/apex/`, `src/trading/`, `src/tias/`

---

## How to navigate

The 27 collection files are designed for the external designer to read individually per component. Suggested order:

1. **`N1_e2e_trade_trace.md`** — read first; this is ground truth
2. **`H1` → `H2` → `H3` → `H4`** — Brain pipeline top-down
3. **`I1` → `I2` → `I3` → `I4`** — APEX top-down
4. **`J1` → `J2`** — Enforcer + PnL
5. **`K1` → `K2`** — Gate (place + audit)
6. **`L1` → `L2` → `L3` → `L4`** — Execution
7. **`M1` → `M4`** — cross-cutting wiring
8. **`N2`–`N7`** — config, live state, cycles, failures, TIAS

For a single-file consolidated read of all 28 files: see `STAGE2_LAYER3_FORENSIC_BUNDLE_2026-05-02.md` at the project root (concatenated master deliverable).


=====================================================================
## FILE: H1_claude_strategist.md
=====================================================================

# H1 — ClaudeStrategist core

Collected: 2026-05-02 (snapshot DB: /tmp/trading_snapshot_1777722335.db)
Logs window: last 24h (2026-05-01 12:00 UTC → 2026-05-02 11:48 UTC)

## File metadata

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/strategist.py`
- Lines of code: 2864
- Last modified: 2026-05-01 02:14:18 UTC
- Class: `ClaudeStrategist` (strategist.py:237)
- Module-level constants: `TRADE_SYSTEM_PROMPT` (strategist.py:65), `POSITION_SYSTEM_PROMPT` (strategist.py:150), `STRATEGIST_SYSTEM_PROMPT = TRADE_SYSTEM_PROMPT` (strategist.py:171), `BRIEFING_SYSTEM_PROMPT_SUFFIX` (strategist.py:180)
- Module-level helpers: `_safe_float` (strategist.py:32), `_safe_int` (strategist.py:50)

## Methods (one-liner each)

| line | signature | purpose |
|---|---|---|
| 240 | `__init__(self, claude_client, services: dict, settings)` | wires deps; inits `_last_regime_str/_last_regime_confidence/_last_fg_value`, `_has_urgent_concerns`, `_invalidated_positions` |
| 257 | `invalidate_position(self, symbol: str) -> None` | close-broadcast hook; stages a symbol as stale until next prompt build; emits `POSITION_INVALIDATED` + legacy `STRAT_POS_INVALIDATE` |
| 280 | `_has_blocking_price_divergence(self) -> bool` | reads `transformer._last_enrichment_max_divergence_pct` vs `settings.price.divergence_block_prompt_pct` |
| 300 | `async refresh_positions(self) -> list` | force-fetch live positions via `position_service.get_positions()`; clears `_invalidated_positions` on success |
| 328 | `async create_strategic_plan(self) -> StrategicPlan|None` | legacy combined-call entry — `_build_context_prompt`, send, parse via `_parse_plan` |
| 383 | `async review_positions(self, positions) -> dict` | 30-second compact position review |
| 412 | `async create_trade_plan(self) -> StrategicPlan|None` | **CALL A**: builds `_build_trade_prompt`, sends `TRADE_SYSTEM_PROMPT`, parses with `_parse_trade_plan` |
| 495 | `async create_position_plan(self) -> StrategicPlan|None` | **CALL B**: builds `_build_position_prompt`, sends `POSITION_SYSTEM_PROMPT`, parses with `_parse_position_plan`; defers if `_has_blocking_price_divergence()` |
| 558 | `async _build_context_prompt(self) -> str` | legacy combined market+positions prompt |
| 1240 | `_format_packages_for_prompt(self, packages: dict) -> str` | renders `CoinPackage` dict into TRADE CANDIDATES block (legacy + briefing modes) |
| 1440 | `_format_briefing_extras(self, lines: list, pkg) -> None` | Phase-6 votes block + interestingness breakdown |
| 1510 | `_format_action_hint(self, lines: list, pkg) -> None` | Phase-6 action_hint surfacing |
| 1526 | `async _build_trade_prompt(self) -> str` | **CALL A prompt builder**, target ~12-14K chars |
| 2234 | `async _build_position_prompt(self) -> str` | **CALL B prompt builder**, target 5-8K chars |
| 2407 | `_build_regime_instructions(self, regime, confidence, fear_greed) -> str` | regime-specific trading directives prepended after coaching |
| 2505 | `_build_direction_performance(self) -> str` | last-20-closed buy/sell W/L + warning text |
| 2594 | `async _build_position_review_prompt(self, positions) -> str` | compact prompt for `review_positions` |
| 2681 | `_parse_plan(self, data: dict) -> StrategicPlan` | combined parser — `new_trades`, `coin_directives`, `position_actions` |
| 2738 | `_parse_trade_plan(self, data: dict) -> StrategicPlan` | CALL A parser — `new_trades` + optional `coin_directives` |
| 2780 | `_parse_position_plan(self, data: dict) -> StrategicPlan` | CALL B parser — validates `position_actions`, downgrades invalid `tighten_stop`/`set_exit` to `hold`, emits `STRAT_CALL_B_PARSED` |

## CALL_A vs CALL_B alternation

- **Driver**: `LayerManager._brain_review_loop` (`src/core/layer_manager.py:698`).
- **Interval**: `self.brain_interval_seconds = 150` set at layer_manager.py:85; overridden by `WorkerManager` from `settings.brain.strategic_interval` (default 150) at `src/workers/manager.py:570`. Telegram `/control` can flip it to 60/180/300 (`src/telegram/handlers/control_handler.py:339-343`).
- **Loop body**: `while self._layer_active[2]: await self._run_brain_cycle(); await asyncio.sleep(150)` (layer_manager.py:712-722). Mandatory sleep — comment at 710: "do NOT reintroduce event-trigger bypasses".
- **Strict-alternation switch**: held in `self._call_type` (initial value `"A"`). After CALL_A body, the last line of the success path is `self._call_type = "B"` (layer_manager.py:874); CALL_B branch ends with `self._call_type = "A"` (layer_manager.py:935). Failure paths still flip the switch (layer_manager.py:755, 897) so a failed cycle never starves the other call type.
- **Pre-CALL_B short-circuit**: if `position_service.get_positions()` returns `[]`, layer_manager.py:884 emits `BRAIN_CYCLE_B_SKIP | rsn='no open positions'` and flips `_call_type` to `"A"` without invoking the strategist. Observed once in window — `did=d-1777720966952` at 11:26:32 (account had no positions).

### Last 20 STRAT_CALL_A_START events (last 24h)

```
2026-05-02 04:13:55.927 STRAT_CALL_A_START | did=d-1777695235927
2026-05-02 04:22:22.678 STRAT_CALL_A_START | did=d-1777695742678
2026-05-02 04:31:22.345 STRAT_CALL_A_START | did=d-1777696282345
2026-05-02 04:38:01.969 STRAT_CALL_A_START | did=d-1777696681969
2026-05-02 04:45:51.599 STRAT_CALL_A_START | did=d-1777697151599
2026-05-02 04:54:53.903 STRAT_CALL_A_START | did=d-1777697693903
2026-05-02 05:02:05.354 STRAT_CALL_A_START | did=d-1777698125354
2026-05-02 05:09:05.524 STRAT_CALL_A_START | did=d-1777698545524
2026-05-02 05:15:56.113 STRAT_CALL_A_START | did=d-1777698956113
2026-05-02 05:22:50.291 STRAT_CALL_A_START | did=d-1777699370291
2026-05-02 05:29:56.383 STRAT_CALL_A_START | did=d-1777699796383
2026-05-02 05:38:39.292 STRAT_CALL_A_START | did=d-1777700319292
2026-05-02 05:46:03.208 STRAT_CALL_A_START | did=d-1777700763208
2026-05-02 05:53:27.375 STRAT_CALL_A_START | did=d-1777701207375
2026-05-02 06:00:50.866 STRAT_CALL_A_START | did=d-1777701650866
2026-05-02 06:08:39.444 STRAT_CALL_A_START | did=d-1777702119444
2026-05-02 06:16:58.197 STRAT_CALL_A_START | did=d-1777702618197
2026-05-02 06:24:11.893 STRAT_CALL_A_START | did=d-1777703051893
2026-05-02 06:32:35.781 STRAT_CALL_A_START | did=d-1777703555781
2026-05-02 11:22:46.952 STRAT_CALL_A_START | did=d-1777720966952
```

### Last 20 STRAT_CALL_B_START events (last 24h)

```
2026-05-02 02:38:03.061 STRAT_CALL_B_START | did=d-1777689483061
2026-05-02 02:46:29.211 STRAT_CALL_B_START | did=d-1777689989211
2026-05-02 03:02:29.814 STRAT_CALL_B_START | did=d-1777690949814
2026-05-02 03:10:14.264 STRAT_CALL_B_START | did=d-1777691414264
2026-05-02 03:19:04.514 STRAT_CALL_B_START | did=d-1777691944514
2026-05-02 03:28:04.945 STRAT_CALL_B_START | did=d-1777692484945
2026-05-02 03:36:02.098 STRAT_CALL_B_START | did=d-1777692962098
2026-05-02 03:44:30.155 STRAT_CALL_B_START | did=d-1777693470155
2026-05-02 03:52:51.330 STRAT_CALL_B_START | did=d-1777693971330
2026-05-02 04:01:32.725 STRAT_CALL_B_START | did=d-1777694492725
2026-05-02 04:10:08.107 STRAT_CALL_B_START | did=d-1777695008107
2026-05-02 04:18:24.699 STRAT_CALL_B_START | did=d-1777695504699
2026-05-02 04:27:33.353 STRAT_CALL_B_START | did=d-1777696053353
2026-05-02 04:51:03.487 STRAT_CALL_B_START | did=d-1777697463487
2026-05-02 05:05:55.145 STRAT_CALL_B_START | did=d-1777698355145
2026-05-02 05:34:40.246 STRAT_CALL_B_START | did=d-1777700080246
2026-05-02 05:57:54.112 STRAT_CALL_B_START | did=d-1777701474112
2026-05-02 06:04:44.628 STRAT_CALL_B_START | did=d-1777701884628
2026-05-02 06:13:09.333 STRAT_CALL_B_START | did=d-1777702389333
2026-05-02 06:28:50.620 STRAT_CALL_B_START | did=d-1777703330620
```

(Window totals: 34 CALL_A + 20 CALL_B. The gap from 06:32 → 11:22 corresponds to a worker-process restart visible at the rolling-log boundary `workers.2026-05-02_04-31-00_392071.log` → `workers.log`.)

## `_build_trade_prompt` (CALL A)

Signature: `async def _build_trade_prompt(self) -> str:` — strategist.py:1526.

Section-by-section assembly. The `STRAT_PROMPT_BUILD` log emits per-section ms (last sample: 14 named buckets — `coaching/regime_fetch/regime_instr/dir_perf/trading_mode/universe/market_data/data_lake/xray/sentiment/regime_global/held_symbols/hints/account`).

| order | section | source service / cache | strategist.py line |
|---|---|---|---|
| 1 | `coaching` block (PERFORMANCE COACH) | `services.get("enforcer").get_coaching_text(structure_cache=...)` (`src/strategies/performance_enforcer.py:428`) | 1549-1557 |
| 2 | early regime fetch (cached) | `services.get("regime_detector").get_last_regime()` else `await detect()` | 1568-1583 |
| 3 | early Fear & Greed | `services.get("fear_greed").get_latest()` | 1585-1592 |
| 4 | REGIME-SPECIFIC TRADING INSTRUCTIONS | `_build_regime_instructions(_regime_str, _regime_confidence, _fear_greed_value)` (line 2407) | 1602-1610 |
| 5 | DIRECTION PERFORMANCE last-20 | `_build_direction_performance()` (line 2505) reads `coordinator._closed_trades` | 1615-1622 |
| 6 | TRADING MODE | `services.get("trading_mode").mode.get_claude_mode_instruction()` (`src/core/trading_mode.py:65`) | 1625-1631 |
| 7 | TRADEABLE COINS THIS CYCLE | `services.get("scanner").get_active_universe()` ∩ `SUPPORTED_SYMBOLS` (testnet filter) | 1633-1652 |
| 8 | TRADE CANDIDATES (`_format_packages_for_prompt`) | `services.get("layer_manager").get_coin_packages()` — only when `settings.brain.use_packages` (default True) | 1659-1698 |
| 9 | MARKET DATA per coin (price, %24h, RSI, MACD, ADX, [POS] tag, [REGIME] tag, VOL=class ATR%) | `market_service.get_all_linear_tickers()` bulk + per-symbol `ta_cache.analyze` H1 + `regime_detector.get_coin_regime` + `volatility_profiler.get_profile` | 1707-1812 |
| 10 | REGIME DIVERGENCE list | `regime_detector.get_coin_regime` per coin vs global | 1815-1833 |
| 11 | data lake snapshot write (side-effect) | `services.get("data_lake").write_market_snapshot(btc, eth, sol)` | 1843-1862 |
| 12 | SESSION header (`current_session`/`session_phase`/`trading_recommendation`) | `services.get("structure_cache").get_all()` first analysis with `session_context` | 1869-1891 |
| 13 | X-RAY STRUCTURAL SETUPS (top 8) + skip-coins line | `structure_cache.get_top_setups(n=8)`/`get_all()` — strength/touches/RR/FVG/OB/SWEEP/SMC/POC/FIB/MTF/CONFL | 1893-1946 |
| 14 | SENTIMENT (Fear & Greed value + classification) | already-fetched `_fg_data` | 1959-1963 |
| 15 | MARKET REGIME (CONTROLS YOUR TRADE DIRECTION) | `_regime_state` already fetched | 1969-1988 |
| 16 | HELD SYMBOLS warning OR "No open positions" | `services.get("position_service").get_positions()` | 1994-2008 |
| 17 | STRATEGY HINTS (top 20) | `services.get("layer_manager")._strategy_hints` | 2017-2025 |
| 18 | CONSENSUS PER COIN (top 15 by total_score) | `layer_manager._strategy_consensus_summary` (alias) | 2032-2049 |
| 19 | ACCOUNT (Equity, Available) + TIERED CAPITAL limits + TODAY'S PERFORMANCE (PnL%, trades) + EVENT BUFFER + URGENT QUEUE | `account_service.get_wallet_balance` / `tiered_capital.get_limits` / `pnl_manager` / `event_buffer.get_prompt_text(max_events=settings.brain.prompt_event_buffer_max_events)` / `urgent_queue.drain_concerns` | 2057-2140 |

NOT FOUND — a canonical 19-section list inside `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md`. The file exists but does not enumerate the sections in order; the table above is reconstructed from the assembly code in `_build_trade_prompt`.

After assembly: `STRAT_PROMPT_BUILD` log at strategist.py:2149 with per-section ms; size gate at 2186 trims trailing sections when count > 80 OR chars > 14000 (emits `CLAUDE_PROMPT_TRIMMED | site=size`); final `PROMPT_BUILD_DONE` at 2223.

### 5 PROMPT_BUILD_DONE CALL_A events

```
2026-05-02 06:08:40.470 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17115 sections=31 packages=15 elapsed_ms=1025 | did=d-1777702119444
2026-05-02 06:16:58.546 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17192 sections=31 packages=15 elapsed_ms=348  | did=d-1777702618197
2026-05-02 06:24:12.846 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17137 sections=31 packages=15 elapsed_ms=952  | did=d-1777703051893
2026-05-02 06:32:35.907 PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17231 sections=31 packages=15 elapsed_ms=126  | did=d-1777703555781
2026-05-02 11:22:51.485 PROMPT_BUILD_DONE | call=CALL_A coins=30 size_bytes=4077  sections=32 packages=0  elapsed_ms=4532 | did=d-1777720966952
```

The 11:22 build had `packages=0` (post-restart, before scanner_worker rebuilt the cache); workers.log emitted `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=2` at 11:24:01 (layer_manager.py:792).

CALL_A trim events (last 24h): the 17K-byte builds consistently exceed the 14K char cap and trip `CLAUDE_PROMPT_TRIMMED | site=size reason=chars`, e.g. `2026-05-02 05:15:57.336 CLAUDE_PROMPT_TRIMMED | site=size reason=chars sections_before=66 sections_after=31 chars_before=18948 chars_after=17278`.

## `_build_position_prompt` (CALL B)

Signature: `async def _build_position_prompt(self) -> str:` — strategist.py:2234.

Section assembly:

| order | section | source | strategist.py line |
|---|---|---|---|
| 1 | MARKET REGIME (cached `_last_regime_str`/`_last_regime_confidence` from CALL A) | self-cached | 2244 |
| 2 | SENTIMENT (cached `_last_fg_value`) | self-cached | 2247 |
| 3 | TODAY PnL line | `services.get("pnl_manager").current_pnl_pct` | 2250-2255 |
| 4 | YOUR OPEN POSITIONS header + per-position block (Entry/Now/PnL%/SL/TP/Lev/Age/Remaining/Regime/SL consumed%/Thesis/[APEX-FLIPPED]) | `refresh_positions()` then per-position: `thesis_manager.get_open_theses()`, `coordinator.get_trade_plan/get_trade_info`, `regime_detector.get_coin_regime` | 2258-2347 |
| 5 | RECENT LESSONS (5 closed-trade entries, filtered to positioned syms when available) | `thesis_manager.get_recent_lessons(limit=10)` | 2350-2369 |
| 6 | RECENTLY CLOSED with cooldowns | `coordinator._symbol_cooldowns` | 2372-2381 |
| 7 | URGENT QUEUE residue | `services.get("urgent_queue").drain_concerns()` | 2384-2392 |

Differences from CALL A:
- No market scan, no per-coin TA, no X-RAY, no strategy hints, no HELD-SYMBOLS section (positions ARE the subject), no consensus rollup.
- Caches regime/F&G from CALL A (does not re-fetch). Source for both reads: `self._last_regime_str/_last_regime_confidence/_last_fg_value` written at strategist.py:1595-1597.
- CALL B does NOT consume `_coin_packages`; reads positions directly via `refresh_positions()`.

### 5 actual CALL B prompts (size + section count)

```
2026-05-02 05:34:40.255 PROMPT_BUILD_DONE | call=CALL_B positions=2 size_bytes=1069 sections=10 elapsed_ms=8 | did=d-1777700080246
2026-05-02 05:57:54.120 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=623  sections=7  elapsed_ms=7 | did=d-1777701474112
2026-05-02 06:04:44.637 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1000 sections=13 elapsed_ms=8 | did=d-1777701884628
2026-05-02 06:13:09.342 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1159 sections=14 elapsed_ms=8 | did=d-1777702389333
2026-05-02 06:28:50.628 PROMPT_BUILD_DONE | call=CALL_B positions=1 size_bytes=1067 sections=12 elapsed_ms=7 | did=d-1777703330620
```

CALL_B prompts run in 7-8 ms (no TA / X-RAY / scanner reads).

## Package consumption from `_coin_packages` cache

- Read site: strategist.py:1665 — `packages = lm.get_coin_packages()` where `lm = self.services.get("layer_manager")`.
- Format expected: `dict[str, CoinPackage]`. Each entry has `state_label`, `interestingness_score`, `opportunity_score`, `xray.setup_type`, `xray.structural_levels`, `strategies.fired_count/ensemble_consensus/total_score`, `signals.confidence/direction`, `alt_data.funding_rate/funding_signal`, `qualification_reasons`, `open_position`, `built_at`. Renderer: `_format_packages_for_prompt` (strategist.py:1240).
- Observability: `STRATEGIST_PACKAGES_READ` event at strategist.py:1684 — emits `count`, `age_min_s`, `age_max_s`, `reader=brain_call_a`.

### 5 PROMPT_BUILD events with packages count

```
2026-05-02 05:46:03.209 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=123 age_max_s=123 | did=d-1777700763208
2026-05-02 05:53:27.377 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=267 age_max_s=267 | did=d-1777701207375
2026-05-02 06:00:50.868 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=110 age_max_s=110 | did=d-1777701650866
2026-05-02 06:24:11.894 STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=11  age_max_s=11  | did=d-1777703051893
2026-05-02 11:22:50.089 STRATEGIST_PACKAGES_READ | call=CALL_A count=0  age_min_s=0   age_max_s=0   | did=d-1777720966952
```

When `count=0`, `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=N` fires at layer_manager.py:792 (CALL A drops the trades; CALL B is unaffected because it reads positions, not packages).

## Coaching block source (TIAS feedback)

- Defined: `PerformanceEnforcer.get_coaching_text(structure_cache=None)` — `src/strategies/performance_enforcer.py:428`.
- Read site: strategist.py:1553 — `coaching = enforcer.get_coaching_text(structure_cache=_sc)`. Same logic in `_build_context_prompt` at strategist.py:565-572 (legacy combined call).
- Format (reproduced from lines 436-499):
  ```
  PERFORMANCE COACH (your stats today):
    Trades: <n> | Wins: <w> | Losses: <l>
    Win rate: <wr>% | PnL: <pct>% | Streak: <streak>
    [tier text — PROFITABLE / SLIGHTLY NEGATIVE / CAPITAL PRESERVATION MODE / RISK MANAGEMENT MODE]
    Best coin: <sym> (<pnl>%)
    Worst coin: <sym> (<pnl>%)
    Buy win rate: <pct>% | Sell win rate: <pct>%
    [WARNING: Claude heartbeat stale (>10min since last call)] (when _check_heartbeat fails)
  ```
- Sample text from last 3 prompts (the prompt body itself is not logged verbatim; CALL A directives routinely echo the coaching tier name back as the first token of `reasoning`):
  - `did=d-1777720966952` 11:24:01 → directive `"CAPITAL PRESERVATION. RSI=26 deeply oversold..."` → coaching at level 1.
  - `did=d-1777701207375` 05:55:24 → `"CAPITAL PRESERVATION. Trending_up 100% conf, ADX=51..."`.
  - `did=d-1777700763208` 05:48:27 → `"CAPITAL PRESERVATION. Trending_up regime with ADX=51..."`.
- TIAS feedback: TIAS-derived per-coin (`p_win`, profit factor) is wired into the level-2 coaching text via the `top_picks` X-RAY enrichment path (lines 458-480) and into `STRAT_POS_ACT` reasoning. Sample: `STRAT_POS_ACT | sym=AXSUSDT act=close rsn='Thesis is broken. TIAS shows 1W/7L (12% WR, PF=0.12) — historically disastrous b'` (did=d-1777701884628 06:06:09). The lessons block in CALL B (strategist.py:2350-2369) uses `thesis_manager.get_recent_lessons(limit=10)` which surfaces the `trade_thesis.lesson` column populated by TIAS analysis.

## Direction performance computer

- Location: `_build_direction_performance` — strategist.py:2505.
- Inputs: `services.get("trade_coordinator")._closed_trades` — `recent = closed[-20:]`. Each closed-trade dict has `direction`, `was_win`, `pnl_usd`.
- Outputs to prompt:
  ```
  ## DIRECTION PERFORMANCE (last 20 trades — read carefully)
    BUY/LONG: <wins>W/<losses>L (WR=<pct>%) PnL=$<usd>
    SELL/SHORT: <wins>W/<losses>L (WR=<pct>%) PnL=$<usd>
    WARNING: <DIR> DIRECTION FAILING: ... (only when n≥5 AND wr<0.40)
    RECOMMENDATION: BUY|SELL is outperforming ... (delta ≥ 15%, n≥3 each)
  ```
- Side-effect log: `STRAT_DIR_PERF | buy_n=N sell_n=M warnings=K` (strategist.py:2587).

## Trading mode manager

- Location: `src/core/trading_mode.py`. Class `TradingMode` (line 24), enum `TradingModeType` (line 18), manager `TradingModeManager` (line 112).
- Modes: `TESTNET` and `MAINNET` (TradingModeType enum, lines 18-20).
- Service key: `services["trading_mode"]`. Read by strategist at strategist.py:1626-1628 → `trading_mode_mgr.mode.get_claude_mode_instruction()`.
- Mode change mechanism: `await TradingModeManager.set_mode(mode_type)` (line 138-149). Persists to `fund_manager_state` table key `trading_mode` (line 144-147). Loaded on startup from same row (line 122-132).
- Current mode: TESTNET. The mode is initialised from `settings.bybit.testnet` (trading_mode.py:117-120). The `is_testnet = ...settings.bybit.testnet` read at strategist.py:1635 was True every CALL_A in the window (the `SUPPORTED_SYMBOLS` testnet filter at 1644-1645 was applied each cycle, capping coin universe to the curated testnet list).
- TESTNET instruction text (lines 67-75) emphasises synthetic prices ("BTC testnet might be $340,000 while real BTC is $87,000") and pins the model to in-prompt data only.

## Output parsing

- Format Claude returns: bare JSON or fenced markdown JSON. CALL_A schema per `TRADE_SYSTEM_PROMPT` line 116:
  `{"new_trades":[{"symbol","direction","stop_loss_price","take_profit_price","max_hold_minutes","leverage","size_usd","trailing_activation_pct","reasoning"}],"market_view","risk_level","max_positions","default_leverage","default_sl_pct","default_tp_pct","default_hold_minutes","trailing_activation_pct","focus_coins":[],"avoid_coins":[]}`
- CALL_B schema per `POSITION_SYSTEM_PROMPT` line 153:
  `{"position_actions":{"SYMBOL":{"action":"hold|close|tighten_stop|set_exit","new_sl":price_or_null,"exit_price":price_or_null,"reasoning"}}}`
- Parser entry sites:
  - CALL A: strategist.py:447-450 — `self.claude.extract_json(raw_response)` (`ClaudeCodeClient.extract_json` at `src/brain/claude_code_client.py:505`) then `_parse_trade_plan` (strategist.py:2738).
  - CALL B: strategist.py:531-534 then `_parse_position_plan` (strategist.py:2780).
- `extract_json` strategies (claude_code_client.py:512-556): (1) ```` ```json ... ``` ```` fence; (2) first `{` … last `}`; (3) first `[` … last `]` (wrapped as `{"decisions": [...]}`); (4) raw `json.loads`. On final failure: `CLAUDE_PARSE_FAIL | reason=json_decode err='...' raw_response='...'` (line 552), raises `ValueError`.
- Parse-failure handling: ValueError propagates to `create_trade_plan`/`create_position_plan` exception handler (strategist.py:489-493 / 552-556) which emits `STRAT_CALL_A_FAIL` / `STRAT_CALL_B_FAIL` and returns `None`. Layer manager logs `BRAIN_CYCLE_A_FAIL` / `BRAIN_CYCLE_B_FAIL` (layer_manager.py:751-756 / 893-898) and flips `_call_type` to the other call so the next cycle proceeds.
- `_parse_position_plan` defensive logic (strategist.py:2780-2864):
  - Valid actions: `{"hold","close","tighten_stop","set_exit","take_profit"}` (line 2804).
  - Unknown action → downgraded to `"hold"` with `STRAT_CALL_B_BAD_ACTION_TYPE` warning (line 2820-2826).
  - `tighten_stop` with `new_sl<=0` → `"hold"` + `STRAT_CALL_B_DOWNGRADE` (line 2831).
  - `set_exit` with `exit_price<=0` → `"hold"` + `STRAT_CALL_B_DOWNGRADE` (line 2837).
  - Non-dict `position_actions` → `STRAT_CALL_B_BAD_ACTIONS` warning, returns empty plan.
  - Final emit `STRAT_CALL_B_PARSED | total=N hold=A close=B tighten=C set_exit=D take_profit=E` (line 2857).

### 3 actual Claude responses verbatim with parsing path

The strategist does NOT log the full raw response on success — only `out=<chars>` length on `CLAUDE_CALL_OK`. Verbatim raw text is captured ONLY on parse failure. The single parse-failure raw text in the 24h window (truncated by the warning logger):

```
2026-05-02 05:10:56.106 CLAUDE_PARSE_FAIL | reason=json_decode err='Expecting value: line 1 column 1 (char 0)' raw_response='System status check blocked by permissions. Here's the situation:'
```

(Claude CLI returned a refusal instead of JSON for did=d-1777698545524. Followed by `STRAT_CALL_A_FAIL` at 05:10:56.107 and `STRAT_CALL_A_END | el=110583ms trades=0 failed=Y`.)

For two non-failing examples we capture the structured derivative (parsed core fields):

Example #1 — did=d-1777720966952 (parse path: extract_json → _parse_trade_plan):
```
STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear at 39. Account in critical drawdown — pure capit'
STRAT_DIRECTIVE   | #1 sym=DYDXUSDT dir=Buy lev=2 rsn='CAPITAL PRESERVATION. RSI=26 deeply oversold in ranging global regime = textbook'
STRAT_DIRECTIVE   | #2 sym=MONUSDT  dir=Buy lev=2 rsn='CAPITAL PRESERVATION. ADX=50 strong trend + RSI=55 healthy momentum zone + MEDIU'
```

Example #2 — did=d-1777703051893 (parse path: extract_json → _parse_trade_plan):
```
STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear sentiment (39). Asian late session with low volu'
STRAT_DIRECTIVE   | #1 sym=ONDOUSDT dir=Buy lev=2 rsn='STRONG ensemble 76.7, highest buy consensus (6.0 votes) across all candidates. R'
STRAT_DIRECTIVE   | #2 sym=NEARUSDT dir=Sell lev=2 rsn='GOOD ensemble 62.4, strong sell votes (3.45). A+ x-ray setup, pos=82% near range'
```

Example #3 — did=d-1777701884628 (parse path: extract_json → _parse_position_plan; CALL B):
```
STRAT_CALL_B_PLAN | acts=1
STRAT_POS_ACT     | sym=AXSUSDT act=close rsn='Thesis is broken. TIAS shows 1W/7L (12% WR, PF=0.12) — historically disastrous b'
```

NOT FOUND — full verbatim Claude JSON text bodies in last 24h. Searched: `raw_response`, `Raw response`, `response='`, `Brain v2 raw response` in /tmp/h_collect/brain_24h.log and /tmp/h_collect/workers_24h.log. The brain only logs the first 100-200 chars of `raw_response` on parse failure; successful CALL A/B raw text is consumed by `extract_json` and never re-emitted at INFO level. Only summary fields (`market_view` first 80-200 chars, `STRAT_DIRECTIVE` reasoning prefix 80 chars, `STRAT_POS_ACT` reasoning prefix 80 chars) are persisted to log + the `claude_decisions` table.

## Failure modes (last 24h grep)

| tag | count | sample |
|---|---:|---|
| `CLAUDE_CALL_FAIL` | 0 | — |
| `CLAUDE_PARSE_FAIL` | 1 | `2026-05-02 05:10:56 reason=json_decode err='Expecting value: line 1 column 1 (char 0)' raw_response='System status check blocked by permissions...'` |
| `CLAUDE_CALL_TIMEOUT` | 0 | — |
| `CLAUDE_PROC_STALL` (legacy DEBUG tag) | 66 | DEBUG-level (not surfaced in WARNING grep alone) |
| `CLAUDE_PROC_STALL_60S` | 50 | `2026-05-02 06:14:09 pid=16852 elapsed=60s stdout_so_far=0 timeout_in_s=240` |
| `CLAUDE_PROC_STALL_120S` | 16 | `2026-05-02 05:31:58 pid=14380 elapsed=120s stdout_so_far=0 timeout_in_s=180 state=S wchan=ep_poll` |
| `CLAUDE_PROC_STALL_240S` | 0 | — |
| `BRAIN_CYCLE_A_FAIL` | 0 | — |
| `BRAIN_CYCLE_B_FAIL` | 0 | — |
| `STRAT_CALL_A_FAIL` | 1 | `2026-05-02 05:10:56 err='Cannot extract JSON from response:...' did=d-1777698545524` (caused by the parse-fail above) |
| `STRAT_CALL_B_FAIL` | 0 | — |
| `STRAT_PLAN_FAIL` | 0 | — |
| `BRAIN_NO_PACKAGES` | 1 | `2026-05-02 11:24:01 reason=empty_packages_cache trades_dropped=2 did=d-1777720966952` (post-restart) |

PROC_STALL_60S firings are informational (Claude CLI takes 60-90 s of stdout silence on most successful calls by design — stalls log at INFO, per claude_code_client.py:1195-1200). PROC_STALL_120S WARNINGs occur when total subprocess wall time exceeds 120 s — most calls in the window run 60-130 s and a third of them breach 120 s. None reached 240 s (the SIGKILL pre-warning). No `CLAUDE_PROC_PREKILL`, no `CLAUDE_PROC_KILLED`, no `BRAIN_FAILURE_CASCADE`. No `CLAUDE_AUTH` failures — but one preflight `CLAUDE_PREFLIGHT_REFRESH` fired at 11:22:51 (`mins_left=-82.7`, refresh recovered to 480 min).


=====================================================================
## FILE: H2_claude_cli_client.md
=====================================================================

# H2 — Claude CLI subprocess manager (`ClaudeCodeClient`)

Collected: 2026-05-02. Logs window: 2026-05-01 12:00 UTC → 2026-05-02 11:48 UTC.

## File overview

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/claude_code_client.py`
- Lines: 1465
- Last modified: 2026-04-27 20:44:41 UTC
- Classes: `ClaudeCodeClient` (line 73), `_NonRetryableError` (line 1440), `ClaudeCodeCostTracker` (line 1444).
- Public surface: `send_message(prompt, system_prompt="", max_tokens=4096) -> str` (line 187), `extract_json(response) -> dict` (line 505), `get_stats() -> dict` (line 558), `set_alert_callback(callback)` (line 175).
- Private: `_get_cred_mtime`, `_credentials_changed`, `_get_credential_expiry_seconds`, `_ensure_credentials_fresh`, `_try_token_refresh_with_retries`, `_try_token_refresh`, `_parse_usage_reset`, `_log_diagnostics`, `_validate_setup`, `_execute_cli`, `_subprocess_call`, `_stream_subprocess_io`, `_collect_stall_diagnostics`, `_capture_prekill_diagnostics`, `_kill_process_group`, `_cleanup_orphaned_processes`, `_find_claude`, `_build_env`.

## OAuth credentials

- Path constant: `_CREDENTIAL_PATH = Path(_HOME) / ".claude" / ".credentials.json"` (line 63). `_HOME = os.environ.get("HOME") or str(Path.home())` (line 62).
- Token URL constant: `_OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"` (line 66).
- Client ID constant: `_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"` (line 67).
- TTL read: `_get_credential_expiry_seconds` (line 590) reads `creds["claudeAiOauth"]["expiresAt"]` (ms) and returns `(expires_ms / 1000.0) - now_s`. Negative ⇒ already expired.
- Pre-flight refresh: `_ensure_credentials_fresh(min_remaining_seconds=None)` (line 611) called from `send_message` at line 277. Default margin = `credential_refresh_margin_seconds` (default 600 s, configurable from `BrainSettings`).
- Refresh path: `_try_token_refresh_with_retries` (line 683) — backoff ladder `[1.0, 3.0, 7.0]`. Underlying single attempt is `_try_token_refresh` (line 723).
- urllib request site: `urllib.request.urlopen(req, timeout=30)` at line 766; payload built at lines 749-753 with `Content-Type: application/json`, `User-Agent: claude-code/1.0.0 (python-client)`, `Accept: application/json`. Method POST.
- On success: writes new `accessToken`, `expiresAt` (ms = now + `expires_in` * 1000), and rotated `refreshToken` back to `_CREDENTIAL_PATH` (lines 781-786) and re-syncs `_cred_mtime`.
- "credential hang" failure mode origin: pre-Phase-3 single-attempt 30-s urllib call at 723-803 — when it raised, the caller logged then proceeded to spawn the CLI subprocess with an already-expired token, which would then hang for the full subprocess timeout (300 s) waiting on Anthropic's auth-error round trip via stdout. Phase 3 fix made `_ensure_credentials_fresh` raise `CredentialRefreshError` (line 673) inside the margin instead of silently proceeding, killing the call ~immediately and emitting `CRED_REFRESH_FAILED_BLOCKING | mins_left=... margin_min=... action=abort_call` (line 668-672). When refresh succeeds, log emits `CLAUDE_PREFLIGHT_REFRESH_OK | new_mins_left=...`. Real example from the window:
  ```
  2026-05-02 11:22:51.487 CLAUDE_PREFLIGHT_REFRESH | reason=expires_in mins_left=-82.7 threshold_min=10.0 attempts=3
  2026-05-02 11:22:51.487 CRED_REFRESH_ATTEMPT | attempt=1/3
  2026-05-02 11:22:51.840 CLAUDE_REFRESH_OK | new_token_expires_in=28800s | credentials updated
  2026-05-02 11:22:51.842 CLAUDE_PREFLIGHT_REFRESH_OK | new_mins_left=480.0
  ```

- 3-layer auth recovery in `send_message` exception block (lines 368-452):
  - Layer 1: `_try_token_refresh()` immediate retry (line 371-405).
  - Layer 2: `_credentials_changed()` hot-reload — operator ran `claude login` (line 409-417).
  - Layer 3: exponential backoff via `_AUTH_BACKOFF_SCHEDULE = [300, 600, 1200, 2400, 3600]` (line 70) plus optional Telegram alert via `_alert_callback` (line 430-443).

## Subprocess spawn

- Spawn site: `_subprocess_call` (line 937). `subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, text=False, cwd=_PROJECT, env=self._env, preexec_fn=os.setsid)` (lines 969-978). `text=False` so chunked-stdout streaming yields raw bytes, decoded once at end with `errors="replace"`.
- `_execute_cli` (line 923) runs the synchronous `_subprocess_call` via `loop.run_in_executor(None, ...)`. So although `send_message` is `async`, the actual CLI call runs in the default thread-pool executor, NOT `asyncio.create_subprocess_exec`.
- Command line: `cmd = [self._claude_path, "-p", "--output-format", "text"]` (line 958); `["--system-prompt", system_prompt]` appended if truthy (line 959-960).
- Stdin write: `proc.stdin.write(prompt.encode("utf-8"))` (line 989), then `proc.stdin.flush()` and `proc.stdin.close()` to deliver EOF (lines 990-991). Comment at 985-987: "if we don't close it the CLI will wait for more input and never write a response".
- Stdout/stderr read: `_stream_subprocess_io` (line 1066). Pipes set non-blocking with `fcntl(F_SETFL, ... | O_NONBLOCK)` (lines 1093-1098). Read loop polls every `_SUBPROC_POLL_INTERVAL_S = 0.05` (50 ms) at line 935, accumulating into `bytearray` buffers via `stream.read1(4096)` (line 1122).
- Timeout policy: deadline `self.timeout` (default `90`, but `WorkerManager` passes `300` from `BrainSettings`; CALL_START log at 11:22 confirms `timeout=300s`). Loop checks `if elapsed > self.timeout: self._capture_prekill_diagnostics(proc); raise subprocess.TimeoutExpired(...)` (lines 1131-1138).
- Kill mechanism: `_kill_process_group` (line 1313). On timeout the caller in `_subprocess_call` calls `_kill_process_group(proc)` (line 1037), which `os.killpg(pgid, SIGTERM)` then waits 5 s then `os.killpg(pgid, SIGKILL)` (lines 1322-1328). Process-group isolation comes from `preexec_fn=os.setsid` at spawn (line 977). After kill, emits `CLAUDE_PROC_KILLED | pid=...` (line 1329) and `BRAIN_FAILURE_CASCADE | call_id=... reason=credential_hang|network_or_cli ...` (lines 1048-1055). Pre-call orphan sweep: `_cleanup_orphaned_processes` (line 1340) runs `pgrep -f "claude.*-p"` and `os.kill(pid, SIGKILL)` for survivors.

## CLAUDE_PROC_STALL — where, threshold, levels

- Fires from `_stream_subprocess_io` (line 1179). Threshold buckets configurable via constructor kwarg `stall_warn_buckets_seconds`; default `(60.0, 120.0, 240.0)` (line 114). `WorkerManager` wires `settings.brain.stall_warn_buckets_seconds` into the kwarg (per docstring at lines 110-114).
- Each bucket fires exactly once per call (set `_stall_bucket_fired`, line 1116). Severity selected at lines 1195-1200:
  - `threshold ≤ 60.0` → `log.info` (informational — Claude CLI typically silent for ~60-90 s on the happy path).
  - `threshold ≤ 120.0` → `log.warning`.
  - `threshold > 120.0` → `log.error`.
- Tag pattern: `f"CLAUDE_PROC_STALL_{int(threshold)}S | pid={pid} elapsed={silence_s:.0f}s stdout_so_far={len(stdout_buf)} timeout_in_s={...}{extra}"` (lines 1201-1206). For 120 s and 240 s buckets, `extra` carries `state=R` and `wchan=...` from `_collect_stall_diagnostics` (line 1242).
- Legacy generic `CLAUDE_PROC_STALL` tag (line 1226) preserved for back-compat dashboards but demoted to DEBUG (Phase-7 post-Layer-1 fix per comment 1214-1221) and rate-limited to once per `_STALL_LOG_EVERY_S = 60.0` (line 932).

### 5 brain calls with time-to-first-stdout proxy

(The CLI does not emit a "first stdout byte" event; instead `CLAUDE_PROC_STALL_60S` confirms ≥60 s of stdout silence, and `CLAUDE_PROC_STALL_120S` confirms ≥120 s. Wallclock from `CLAUDE_PROC_SPAWNED` to `CLAUDE_CALL_OK` is the full call duration.)

```
pid=17370  spawn=06:16:58.576  STALL_60S(+60s)  STALL_120S(+120s)  OK=+133.3s   el_reported=133327ms
pid=17450  spawn=06:24:12.890  STALL_60S(+60s)  STALL_120S(+120s)  OK=+127.7s   el_reported=127756ms
pid=17968  spawn=06:28:50.658  STALL_60S(+60s)                     OK=+75.1s    el_reported=75140ms
pid=18045  spawn=06:32:35.942  (process restart cuts the trace before OK)
pid=932    spawn=11:22:51.870  STALL_60S(+60s)                     OK=+69.5s    el_reported=69537ms
```

Interpretation: every call breaches 60 s of stdout silence (so `STALL_60S` fires on every call); roughly half also breach 120 s. None reached 240 s in the window.

## Error handling and retry

- Retry loop: lines 281-498 in `send_message`. `for attempt in range(self.max_retries + 1):` — default `max_retries = 2` (line 84) so 3 attempts total. WorkerManager-passed value (per ctor): unchanged at 2 in production logs (`attempt=1/3` everywhere).
- `_NonRetryableError` (line 1440): raised from inside `_subprocess_call` at line 1010 when stderr/stdout text contains any pattern in `_NON_RETRYABLE` (line 48-58: `credit balance`, `authentication`, `unauthorized`, `api key`, `account suspended`, `quota exceeded`, `rate limit`, `out of extra usage`, `extra usage`). Caught at line 313 and routed to specific exception types: `ClaudeAPIError` for usage exhaustion (line 364-366) or generic non-retryable (line 453-455); `AuthenticationError` for auth-class messages (line 444-452).
- Generic timeout/transient error path (line 457-498): retries with backoff. Sleep between attempts at line 489-498:
  - `is_timeout = "timed out" in str(e).lower()` (line 488).
  - `sleep_s = (attempt+1) * self.retry_timeout_backoff_base` if timeout else `2 ** attempt` (lines 489-493). `retry_timeout_backoff_base` default 30 (line 86), but Phase-2 commentary says manager.py passes `BrainSettings.claude_cli_retry_timeout_backoff_base_seconds` (default 10 per docstring) for a 10/20/30 ladder.
- Fail-loud final emit: line 500 — `CLAUDE_CALL_FAIL | call_id=... err='...' attempts=...` then `raise BrainError(...)` at 501.
- `_consecutive_failures` increments on each non-OK path; `_adaptive_interval = min(min_interval * (2 ** _consecutive_failures), 30.0)` (lines 316-318, 459-461). On success: reset to `self.min_interval = 2.0` (line 298, default at line 85).
- Auth backoff state (lines 146-148): `_auth_failed`, `_auth_backoff_until`, `_auth_failure_count`, `_auth_alert_sent`. Schedule at line 70.
- Usage backoff state (lines 152-155): `_usage_exhausted`, `_usage_backoff_until`, `_usage_alert_sent`. `_parse_usage_reset` (line 805) parses `"resets 6pm (UTC)"`-style patterns from error text.
- Pre-call gates inside `send_message`: auth backoff (215-229), usage backoff (232-243), rate-limit (245-251 — `await asyncio.sleep` until `_adaptive_interval` elapsed since `_last_call_time`).

## Cost tracking

- Class: `ClaudeCodeCostTracker` (line 1444). `can_afford_call(self, estimated_cost=0.0) -> bool: return True` (line 1454-1455 — comment "always free"). `record_call(input_tokens=0, output_tokens=0) -> float: self._calls_today += 1; return 0.0` (line 1457-1459). `get_daily_spend() -> float: return 0.0` (line 1461). `get_remaining_budget() -> float: return self.daily_budget` (line 1464).
- Tokens / dollars: NOT tracked. Comment at lines 1444-1448: "Drop-in replacement for CostTracker. Always returns True (CLI is free)."
- 24h cost data: NOT FOUND — no `COST_TRACK` events in /tmp/h_collect/brain_24h.log. Searched: `COST_TRACK`, `cost_today`, `cost_usd=`. The CLI uses the operator's Max subscription (OAuth) and reports zero cost in the data lake (`claude_decisions.cost_usd` column not populated by this client). The legacy `src/brain/cost_tracker.py:CostTracker` class (line 11, used by Brain v2 / SDK path) does have full pricing logic ($3/M input, $15/M output) but is not invoked when `ClaudeCodeClient` is the strategist's claude client (which it is in production — `claude_code_client.py` is the active path).

## Last 50 CLAUDE_CALL_START → CLAUDE_CALL_DONE pairs

The "DONE" event is `CLAUDE_CALL_OK` (line 305). The 24h window contains 50+ matched pairs.

`elapsed_ms` distribution from `el=` field on `CLAUDE_CALL_OK`:

```
N=52
min  = 26,731 ms
max  = 169,393 ms
mean = 105,725 ms
p50  = 109,652 ms
p75  = 130,492 ms
p90  = 142,973 ms
p95  = 160,919 ms
p99  = 169,393 ms
```

Last 10 pairs (representative):

```
05:53:27.376 CALL_START call_id=42  in=17085 sys=8985 timeout=300s
05:55:24.104 CALL_OK    call_id=42  attempt=1/3 el=115887ms out=2492 calls=42
05:57:54.131 CALL_START call_id=43  in=...
05:55:24 / 05:57:54 — same did=d-1777701207375 / 1777701474112

call_id=43  el=  varies (CALL B small)
call_id=44  el=26731ms  out=1245    ← fastest CALL B in window
call_id=45  el=82496ms  out=2014
call_id=46  el=84792ms  out=665
call_id=47  el=118837ms out=1850
call_id=48  el=78839ms  out=578
call_id=49  el=133327ms out=2112
call_id=50  el=127756ms out=2128
call_id=51  el=75140ms  out=397
call_id=1   el=69537ms  out=2439    ← post-restart, _call_id was reset to 0
```

Failure rate (CALL_FAIL OR PARSE_FAIL OR STRAT_*_FAIL events / total brain calls): 1 / 54 = **1.9 %** in window. The single failure was the parse-failure on did=d-1777698545524 at 05:10:56 (Claude returned a non-JSON refusal). All 52 `CLAUDE_CALL_OK` events used `attempt=1/3` — no retries fired in the window.

CALL_START sample (full line, including `sys=` and `timeout=` fields):

```
2026-05-02 11:22:51.486 | INFO | src.brain.claude_code_client:send_message:262 | CLAUDE_CALL_START | call_id=1 in=4077 sys=8985 timeout=300s hash=e0558dedb7cd | did=d-1777720966952
```

The `sys=8985` matches the size of `TRADE_SYSTEM_PROMPT` (strategist.py:65) since `_has_urgent_concerns=False` and `surface_briefing_fields=False` paths apply. `timeout=300s` confirms the WorkerManager-passed value (overrides default 90 s in the constructor).


=====================================================================
## FILE: H3_brain_decision_output.md
=====================================================================

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


=====================================================================
## FILE: H4_brain_cycle_orchestration.md
=====================================================================

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


=====================================================================
## FILE: I1_apex_architecture.md
=====================================================================

# I1 — APEX Architecture

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).
Project root: `/home/inshadaliqbal786/trading-intelligence-mcp`.

## 1. `src/apex/` directory contents

Top-level listing (`ls -la /home/inshadaliqbal786/trading-intelligence-mcp/src/apex/`):

| File | LOC | One-line description |
|------|-----|----------------------|
| `__init__.py` | 1 | Package docstring only — `"""APEX -- Aggressive Profit Extraction & Exploitation."""` (`__init__.py:1`). |
| `assembler.py` | 769 | `IntelligenceAssembler` builds the 5-section `IntelligencePackage` (Claude directive, coin data, TIAS symbol history, TIAS situation, X-RAY structural) consumed by the optimizer (`assembler.py:32`). |
| `gate.py` | 474 | `TradeGate` runs the 14 hard-safety checks between optimizer and Shadow execution (`gate.py:29`). |
| `models.py` | 435 | Dataclasses: `DirectiveContext`, `CoinData`, `TIASSymbolHistory`, `TIASSituationData`, `StructuralData`, `IntelligencePackage`, `OptimizedTrade` (`models.py:18,47,177,214,245,374,394`). |
| `optimizer.py` | 743 | `TradeOptimizer.optimize()` orchestrator: assemble → tier check → direction-lock → DeepSeek call → parse → constraints → flip-discipline → log (`optimizer.py:36,61`). |
| `prompts.py` | 226 | `APEX_SYSTEM_PROMPT` constant + `build_apex_user_prompt(package)` builder (`prompts.py:21,82`). |
| `qwen_client.py` | 248 | `QwenClient` — async OpenRouter HTTP client (despite the name, the model called is DeepSeek per `_DS_COST_PER_M_INPUT/OUTPUT` constants and config) (`qwen_client.py:47`). |

## 2. Main entry point

`TradeOptimizer.optimize(directive, plan)` defined at `src/apex/optimizer.py:61` is the public entry point.

It is invoked from `src/core/layer_manager.py:1258` inside an `asyncio.gather` over all `plan.new_trades` (parallel optimization, see `layer_manager.py:1254-1271`).

The class is constructed and registered on the service container at `src/workers/manager.py:1829`:
```
apex_optimizer = TradeOptimizer(qwen_client, apex_assembler, apex_cfg)
self._services["apex_optimizer"] = apex_optimizer
```
(`workers/manager.py:1816-1840`)

`apex_gate` (TradeGate) is constructed at `workers/manager.py:1834` and registered as `self._services["apex_gate"]`.

## 3. APEX role per `dev_notes/APEX_COMPLETE_INTEGRATION_PROMPT.md`

NOT FOUND — searched: `find /home/inshadaliqbal786/trading-intelligence-mcp -maxdepth 4 -type f -iname "*apex*integration*"` returned only test files. The file `dev_notes/APEX_COMPLETE_INTEGRATION_PROMPT.md` does not exist.

Closest available source for APEX role description: `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md:567-575` ("Stage 3.1 — APEX (post-Claude trade-parameter optimizer)"), which states "Takes Claude's directive and runs DeepSeek (via OpenRouter) to optimize SL/TP/size/leverage/direction."

### Philosophy "never reject, never skip — only optimize parameters"

Phrase NOT FOUND verbatim — searched: `grep -rn "never reject" src/apex/ dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md`, `grep -rn "never skip" ...`, `grep -rn "only optimize" ...`. All returned no matches.

Equivalent text found in code:
- `src/apex/prompts.py:25-26` (system prompt to DeepSeek):
  ```
  YOU DO NOT REJECT TRADES. YOU DO NOT SAY "SKIP."
  Every directive you receive WILL be traded. Your job is to make it the most profitable version possible.
  ```
- `src/apex/optimizer.py:4-5` (module docstring):
  ```
  Takes Claude's trade directive and returns DeepSeek-optimized parameters for
  maximum profit extraction. If DeepSeek fails for ANY reason, returns Claude's
  original parameters unchanged. APEX failure NEVER blocks a trade.
  ```
- `src/apex/optimizer.py:38-41` (class docstring):
  ```
  If DeepSeek fails for any reason (timeout, bad JSON, API error), the optimizer
  returns an OptimizedTrade with is_fallback=True that preserves Claude's
  original parameters. APEX failure never blocks trade execution.
  ```
- `src/apex/gate.py:2-4`:
  ```
  The gate NEVER blocks a trade. It adjusts parameters within safe bounds.
  ```

### Where in pipeline

Per `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md:131`: "Layer 3 (EXECUTION) APEX → TradeGate → OrderService". Concrete chain:

1. Claude Brain emits `plan.new_trades` (list of directive dicts).
2. `core/layer_manager.py:1248-1253` stamps `_claude_original_size_usd` on every directive (Phase 5 ceiling reference).
3. `core/layer_manager.py:1254-1271` — `apex.optimize` is invoked for every directive in parallel via `asyncio.gather(return_exceptions=True)`.
4. For each trade, `core/layer_manager.py:1314-1316` calls `_apply_apex_optimization(trade, optimized_results[i])` which converts pct→price (`layer_manager.py:1382-1466`).
5. `core/layer_manager.py:1318-1323` calls `gate.validate(trade)` (TradeGate, never blocks).
6. `core/layer_manager.py:1326-1328` calls `strategy_worker._execute_claude_trade(trade, ...)` which routes to `OrderService.place_order`.

## 4. APEX_FLIP behavior

### Where direction flips

Direction flip is computed by DeepSeek inside the LLM response and detected at `src/apex/optimizer.py:387,446`:
```
qwen_dir = analysis.get("direction", original_dir)
...
was_flipped=(qwen_dir != original_dir),
```

The flip log line is emitted at `src/apex/optimizer.py:599-608` (`APEX_FLIP | sym=... claude=... apex=...`).

### Trigger inputs

Inputs to DeepSeek that influence flip choice (see `src/apex/prompts.py:40-47`):
- TIAS direction breakdown for symbol in current regime (`prompts.py:131-141`).
- TIAS situation data (regime + F&G, all-coin direction-bias) (`prompts.py:184-194`).
- Current regime, fed in via `package.situation_data.regime` from `assembler._get_market_conditions` (`assembler.py:575-643`).

### Confidence threshold for flip

Pre-call code-level direction lock (`src/apex/optimizer.py:665-711`):
- `trending_up` / `trending_down`: always lock (`optimizer.py:691-699`).
- `volatile`: lock unless `_check_flip_evidence` shows ≥70% WR over ≥8 opposite-direction trades (`optimizer.py:702-707`, `_check_flip_evidence` at `optimizer.py:650-663`).
- `ranging` / `dead` / `unknown`: NOT locked pre-call (`optimizer.py:709-711`).

Post-parse confidence-gated flip discipline (`src/apex/optimizer.py:713-743`):
- Threshold: `apex_min_flip_confidence`, default `0.90` (`config/settings.py:1445`, `config.toml:997`).
- If regime in {ranging, dead, unknown} AND DeepSeek flipped AND confidence < 0.90, flip is reverted with `APEX_FLIP_BLOCKED` log (`optimizer.py:266-275`).
- Suspenders: if direction was code-locked AND DeepSeek still flipped, `APEX_DIR_LOCK_OVERRIDE` reverts (`optimizer.py:240-251`).

### Count of APEX_FLIP events in last 24h

Time window: 2026-05-01 11:48:00 → 2026-05-02 11:49:30 UTC (logs scanned: `workers.log`, `workers.2026-05-02_04-31-00_392071.log`, `workers.2026-05-01_00-01-33_829054.log`):

| Tag | Count |
|-----|------:|
| `APEX_FLIP` (allowed flips) | 6 |
| `APEX_FLIP_BLOCKED` (reverted by confidence gate) | 2 |
| `APEX_FLIP_RESIZE_BLOCKED` (size forced back) | 6 |
| `APEX_DIR_LOCK` (lock pre-call) | 19 |
| `APEX_DIR_LOCK_OVERRIDE` | 0 |
| `APEX_OK` (no flip) | 51 |
| `APEX_TIER` (total optimizations) | 57 |

Allowed-flip rate = 6 / 57 = 10.5%. Two confidence-gated reverts in the same window prove the discipline is firing.

## 5. `apex_optimized` flag — distribution in DB snapshot

Snapshot: `/tmp/trading_snapshot_1777722335.db` (size 145 MB, mtime 2026-05-02 11:45 UTC).

Total `trade_intelligence` rows: 821 (range `trade_closed_at` 2026-04-06 → 2026-05-02 06:29 UTC).

| `apex_optimized` value | Row count |
|------------------------|----------:|
| 0 | 227 |
| 1 | 594 |

Last-24-hour slice (`trade_closed_at > datetime('now','-24 hours')`):

| `apex_optimized` | `apex_flipped` | Rows |
|------------------|----------------|-----:|
| 1 | 0 | 31 |
| 1 | 1 | 3 |
| 0 | * | 0 |

So the prior memory note ("apex_optimized was 0 for all trades") is no longer true as of this snapshot — 594 of 821 historical rows and all 34 last-24-hour rows have the flag set.

### Where the flag is set

There are two distinct write sites:

1. **In-memory directive flag (`_apex_optimized`)** — set on the directive dict during execution by `src/core/layer_manager.py`:
   - Line 1429: `modified["_apex_optimized"] = True` (partial-apply path when current price unavailable).
   - Line 1453: `modified["_apex_optimized"] = True` (full-apply path).
   - Read by gate at `src/apex/gate.py:201`: `if trade.get("_apex_optimized"):` to gate Checks 8-12.

2. **Persisted DB column (`apex_optimized` in `trade_intelligence`)** — written by TIAS:
   - Schema: `src/database/migrations.py:1221` (`ADD COLUMN apex_optimized INTEGER DEFAULT 0`) + index `:1238`.
   - Set in TIAS collector: `src/tias/collector.py:497-498` (defaults), `:517-519` (override when APEX record present): `result["apex_optimized"] = True; result["apex_flipped"] = bool(record.get("apex_was_flipped", False))`.
   - Repository writes the flag: `src/tias/repository.py:37` (`data["apex_optimized"] = 1 if data.get("apex_optimized") else 0`).
   - The TIAS model dataclass: `src/tias/models.py:87-88` (`apex_optimized: bool = False`, `apex_flipped: bool = False`).

### `apex_optimized` in other tables

Searched via `sqlite3 PRAGMA table_info(orders)` and `PRAGMA table_info(trade_history)`:
- `orders`: NO `apex_optimized` column (schema confirmed — only `order_id, symbol, side, order_type, price, qty, status, filled_qty, avg_fill_price, stop_loss, take_profit, created_at, updated_at`).
- `trade_history`: NO `apex_optimized` column.
- `trade_intelligence` is the sole persisted carrier of `apex_optimized` / `apex_flipped`.
- `trade_thesis` has `apex_flipped` (`migrations.py:1242`) populated from `core/thesis_manager.py:37,52,59,81`.

## 6. APEX-related columns on `trade_intelligence`

From `PRAGMA table_info(trade_intelligence)` (snapshot):

| Col # | Name | Type |
|------:|------|------|
| 75 | `apex_optimized` | INTEGER DEFAULT 0 |
| 76 | `apex_flipped` | INTEGER DEFAULT 0 |
| 77 | `apex_original_direction` | TEXT |
| 78 | `apex_final_direction` | TEXT |
| 79 | `apex_original_sl` | REAL |
| 80 | `apex_final_sl` | REAL |
| 81 | `apex_original_tp` | REAL |
| 82 | `apex_final_tp` | REAL |
| 83 | `apex_original_size` | REAL |
| 84 | `apex_final_size` | REAL |
| 85 | `apex_confidence` | REAL |
| 86 | `apex_tp_mode` | TEXT |
| 87 | `apex_reasoning` | TEXT |
| 88 | `apex_model` | TEXT |
| 89 | `apex_response_ms` | INTEGER |
| 90 | `apex_cost_usd` | REAL |
| 92 | `apex_tp_fill_rate` | REAL |

`gate_adjustments` (col 91) is the persisted GATE adjustment string, written by `core/layer_manager.py:_apply_apex_optimization` chain via TIAS collector.


=====================================================================
## FILE: I2_apex_assembler.md
=====================================================================

# I2 — APEX IntelligenceAssembler

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).

## 1. File path & class

`src/apex/assembler.py:32` — `class IntelligenceAssembler`.

Constructor: `IntelligenceAssembler.__init__(self, services: dict, tias_repo: Any, db: Any = None)` (`assembler.py:50`). Wired in `src/workers/manager.py:1828`:
```
apex_assembler = IntelligenceAssembler(self._services, tias_repo, db)
```

## 2. All public methods

Module exposes one class with the following methods (verbatim signatures with file:line):

| Method | File:line | Purpose |
|--------|-----------|---------|
| `assemble(self, directive: dict) -> IntelligencePackage` | `assembler.py:55` | Build the complete 5-section intelligence package for one coin. |

All other methods (`_build_directive_context`, `_gather_coin_data`, `_populate_ta`, `_populate_mode4`, `_populate_orderbook`, `_populate_volatility_profile`, `_gather_symbol_history`, `_gather_situation_data`, `_get_market_conditions`, `_gather_structural_data`) are private helpers — names start with `_`. They are listed below for traceability.

| Private method | File:line |
|----------------|-----------|
| `_build_directive_context(directive)` | `assembler.py:99` |
| `_gather_coin_data(symbol)` | `assembler.py:118` |
| `_populate_ta(data, symbol)` | `assembler.py:202` |
| `_populate_mode4(data, symbol)` | `assembler.py:279` |
| `_populate_orderbook(data, symbol)` | `assembler.py:324` |
| `_populate_volatility_profile(data, symbol)` | `assembler.py:365` |
| `_gather_symbol_history(symbol, regime)` | `assembler.py:394` |
| `_gather_situation_data(regime, fear_greed)` | `assembler.py:514` |
| `_get_market_conditions(symbol)` | `assembler.py:575` |
| `_gather_structural_data(symbol)` | `assembler.py:649` |

Module-level helpers: `_last_valid_arr(arr)` (`assembler.py:670`), `_gather_structural_data_from_cache(services, symbol)` (`assembler.py:686`).

## 3. `_get_market_conditions` — regime_detector wiring

Defined at `src/apex/assembler.py:575`. The relevant block (`assembler.py:585-617`):

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    # Definitive-fix Phase 7 (2026-04-28) — REGIME_CACHE_QUERY
    # telemetry mirrors apex/gate.py.
    _hit = coin_regime is not None
    _cache_size = (
        len(getattr(detector, "_per_coin_regimes", {}) or {})
    )
    _ready = bool(getattr(detector, "is_ready", lambda: True)())
    log.info(
        f"REGIME_CACHE_QUERY | sym={symbol} reader=apex_assembler "
        f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
    )
    if coin_regime is not None:
        # RegimeState.regime is a MarketRegime enum, .value is the string
        regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        lr = detector._last_regime
        regime = str(lr.regime.value)
        log.warning(
            "REGIME_FALLBACK | sym={sym} source=assembler | "
            "per-coin unavailable, using global={r} | {ctx}",
            sym=symbol, r=regime, ctx=ctx(),
        )
```

VERIFIED: `regime_detector.get_coin_regime(symbol)` is called at `assembler.py:588`. Telemetry tag `REGIME_CACHE_QUERY` with `reader=apex_assembler` is emitted at `:596-599`.

### Fallback when unavailable

Two-tier fallback (`assembler.py:600-617`):
1. If `coin_regime is None` AND `detector._last_regime` exists: use the global regime, emit `REGIME_FALLBACK | source=assembler` at WARNING (`:606-610`).
2. If detector itself is missing or both per-coin and global lookups fail: silently keep the default `regime = "unknown"` initialized at `:581`. Exception path (`:611-617`) catches any failure and emits `APEX_REGIME_FAIL` at DEBUG.

Default values when nothing is available: `regime = "unknown"`, `fear_greed = 50` (`:581-582`).

The Fear & Greed value is sourced separately via direct DB query at `assembler.py:619-642`:
```sql
SELECT value FROM fear_greed_index
WHERE timestamp > datetime('now', '-24 hours')
ORDER BY timestamp DESC LIMIT 1
```
Falls back to 50 (neutral) with `FG_STALE` warning when no row in 24h.

## 4. All APEX inputs (caches / DB tables / services)

### Services consumed (via `self._services.get(...)`)

| Service key | First read | Use |
|-------------|------------|-----|
| `regime_detector` | `assembler.py:586` | Per-coin regime; fallback to global `_last_regime`. |
| `ta_cache` (or `ta`) | `assembler.py:204` | TA indicators (RSI, MACD, ADX, Bollinger, Stochastic, ATR, EMA20/50, volume ratio) on M5 timeframe with `limit=100`. |
| `price_worker` | `assembler.py:144` | WS quote cache (`get_ws_quote(symbol, max_age_s=5.0)`); first price fallback. |
| `market_service` (or `market`) | `assembler.py:162,332` | REST `get_ticker(symbol)` (final price fallback) and `get_orderbook(symbol, depth=10)` (top-5 levels). |
| `volatility_profiler` | `assembler.py:368` | `get_profile(symbol)` returning `volatility_class`, `recommended_tp_pct`, `recommended_sl_pct`, `recommended_hold_min`, `recommended_strategy`. |
| `structure_cache` | `assembler.py:693` (helper) | Synchronous `get(symbol)` returning `StructuralAnalysis` (X-RAY) — feeds Section 5. |

### DB tables read

| Table | Query | File:line |
|-------|-------|-----------|
| `sniper_log` | `SELECT composite_score, hurst_value, momentum_decay_score, extension_score, ev_ratio, volume_div_score FROM sniper_log WHERE symbol=? ORDER BY id DESC LIMIT 1` | `assembler.py:289-298` |
| `fear_greed_index` | `SELECT value FROM fear_greed_index WHERE timestamp > datetime('now','-24 hours') ORDER BY timestamp DESC LIMIT 1` | `assembler.py:622-625` |

### TIAS repository calls

| Method | File:line |
|--------|-----------|
| `tias_repo.get_symbol_full_history(symbol, limit=15, regime=_regime_filter)` | `assembler.py:402` |
| `tias_repo.get_symbol_full_history(symbol, limit=15)` (all-regime fallback when sparse) | `assembler.py:409` |
| `tias_repo.get_situation_stats(regime, fear_greed)` | `assembler.py:519` |

### In-memory caches consumed

- `regime_detector._per_coin_regimes` is read indirectly through `get_coin_regime(symbol)`; cache size also probed at `assembler.py:592-594` (`getattr(detector, "_per_coin_regimes", {})`).
- `regime_detector._last_regime` (global) used as fallback at `assembler.py:603-604`.
- `structure_cache.get(symbol)` (X-RAY) — synchronous in-memory lookup at `assembler.py:696`.
- `_signal_cache` and `StructureCache` (raw class names) — `_signal_cache` is NOT directly referenced in `assembler.py`; only `structure_cache` is consumed. NOT FOUND in this file — searched: `grep -n "_signal_cache\|signal_cache" src/apex/assembler.py`.

## 5. Output structure produced

`IntelligencePackage` dataclass (`src/apex/models.py:374-388`):

```python
@dataclass
class IntelligencePackage:
    directive: DirectiveContext         # Section 1: Claude's trade decision
    coin_data: CoinData                 # Section 2: current coin state
    symbol_history: TIASSymbolHistory   # Section 3: TIAS history for this coin
    situation_data: TIASSituationData   # Section 4: TIAS situation context
    structural_data: Optional[StructuralData] = None  # Section 5: X-RAY structural
```

Each section dataclass and its fields (file:line in `models.py`):

- **Section 1 — `DirectiveContext`** (`models.py:18-40`): `symbol, direction, sl, tp, leverage, size_usd, reasoning, plan_view, signal_score, strategy_name`.
- **Section 2 — `CoinData`** (`models.py:47-170`): `symbol, current_price, change_24h, rsi, macd_line, macd_signal, macd_hist, adx, bollinger_pct, stochastic_k, stochastic_d, ema_20, ema_50, ema_trend, atr, atr_pct, volume_ratio, m4_hurst, m4_momentum, m4_extension, m4_volume_div, m4_ev, m4_composite, m4_trail_sl, bid_depth, ask_depth, book_imbalance_pct, volatility_class, recommended_tp_pct, recommended_sl_pct, recommended_hold_min, recommended_strategy`. Has `format()` method (`:99`).
- **Section 3 — `TIASSymbolHistory`** (`models.py:177-207`): `symbol, total_trades, wins, losses, win_rate, avg_win_pct, avg_loss_pct, total_pnl_usd, ev_per_trade, profit_factor, avg_win_usd, avg_loss_usd, trades, pattern_summary, regime`.
- **Section 4 — `TIASSituationData`** (`models.py:214-238`): `regime, fear_greed, total_trades_in_condition, buy_win_rate, sell_win_rate, avg_buy_pnl, avg_sell_pnl, direction_bias, tp_performance, common_categories, condition_summary`.
- **Section 5 — `StructuralData`** (`models.py:245-367`): X-RAY structural fields (S/R, market structure, R:R, FVG, OB, sweep, liquidity, POC, fib, MTF, session, setup_rank). Has `format()` method (`:303`).

## 6. How the optimizer consumes `IntelligencePackage`

Consumption sites in `src/apex/optimizer.py`:

1. `optimizer.py:112` — `package = await self._assembler.assemble(translated)` (the call).
2. `optimizer.py:116` — `if package.coin_data.current_price <= 0: ...` (price validation, `APEX_SKIP_NO_PRICE`).
3. `optimizer.py:135-136` — `package.symbol_history.total_trades` and `package.situation_data.total_trades_in_condition` for tier classification.
4. `optimizer.py:158-165` — overwrites `package.symbol_history.pattern_summary` for Tier 2 (regime-fallback) optimization.
5. `optimizer.py:184` — `regime = package.situation_data.regime` used for direction-lock decision.
6. `optimizer.py:186-187` — passes `package` to `_check_direction_lock(package, claude_direction, regime)`.
7. `optimizer.py:194-198` — mutates `package.directive.reasoning` to inject lock instruction for DeepSeek.
8. `optimizer.py:206-216` — reads `package.coin_data.volatility_class` and `recommended_tp_pct` to compute the `APEX_TP_CAP`.
9. `optimizer.py:219` — `user_prompt = build_apex_user_prompt(package)` renders all 5 sections into the LLM user message (`prompts.py:82-226`).
10. `optimizer.py:298` — `optimized = self._apply_constraints(optimized, package.coin_data)` (per-class SL/TP floor from `coin_data.recommended_sl_pct` / `recommended_tp_pct`).
11. `optimizer.py:304-309` — enforces `APEX_TP_CAP` (`optimized.tp_pct > _tp_cap`) using `package.coin_data.recommended_tp_pct`.
12. `optimizer.py:317-320` — `_log_optimization(optimized, directive, regime=package.situation_data.regime, vol_class=getattr(package.coin_data, "volatility_class", None))`.

`_check_direction_lock` (`optimizer.py:665-711`) reads:
- `package.symbol_history.trades` (passed to `_check_flip_evidence`, `optimizer.py:704`).

Everything DeepSeek sees is built from `package` via `build_apex_user_prompt` (`prompts.py:82-226`):
- Section 1 from `package.directive` (`prompts.py:104-118`).
- Section 2 from `package.coin_data.format()` (`prompts.py:115-116`).
- Section 3 from `package.symbol_history` and `package.symbol_history.trades` (`prompts.py:121-179`).
- Section 4 from `package.situation_data` (`prompts.py:184-200`).
- Section 5 from `package.structural_data.format()` (`prompts.py:202-207`).
- Output JSON schema instruction (`prompts.py:210-224`).


=====================================================================
## FILE: I3_apex_optimizer.md
=====================================================================

# I3 — APEX TradeOptimizer

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).

## 1. File path & size

`src/apex/optimizer.py` — 743 lines (`wc -l`).

`src/apex/qwen_client.py` — 248 lines (the API client used by the optimizer).

## 2. Public methods

`class TradeOptimizer` (`src/apex/optimizer.py:36`).

| Method | Signature | File:line | Purpose |
|--------|-----------|-----------|---------|
| `__init__` | `(qwen_client, assembler, settings)` | `:49` | Stores collaborators; initializes counters (`_optimized_count`, `_fallback_count`, `_flip_count`, `_lock_override_count`, `_total_time_ms`). |
| `optimize` | `async (directive, plan=None) -> OptimizedTrade` | `:61` | Main pipeline (10-step flow per docstring `:7-17`). |
| `get_stats` | `() -> dict` | `:629` | Returns cumulative `{optimized, fallbacks, flips, flip_rate, lock_overrides, avg_time_ms, qwen_stats}`. |

Private helpers: `_parse_response` (`:369`), `_apply_constraints` (`:462`), `_fallback` (`:535`), `_log_optimization` (`:585`), `_check_flip_evidence` (`:650`), `_check_direction_lock` (`:665`), `_enforce_flip_confidence` (`:713`).

## 3. Qwen / DeepSeek API integration

The truth doc (`dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md:571`) specifies "Qwen 3.6 via OpenRouter" in some legacy notes, but the live code calls **DeepSeek v3.2 via OpenRouter**. Direct evidence:

- Config default: `model: str = "deepseek/deepseek-v3.2"` (`src/config/settings.py:1394`).
- Live config: `model = "deepseek/deepseek-v3.2"` (`config.toml:957`).
- Cost constants encode DeepSeek pricing: `_DS_COST_PER_M_INPUT = 0.30`, `_DS_COST_PER_M_OUTPUT = 0.88` (`src/apex/qwen_client.py:32-33`, comment "DeepSeek V3.2 pricing via OpenRouter (per-million tokens)").
- The class `QwenClient` is named for legacy reasons; its docstring at `qwen_client.py:1-3` explicitly says "DeepSeek client for APEX — calls OpenRouter".

Live log evidence (snapshot row 821 written 2026-05-02 06:29 UTC): `apex_model = "deepseek/deepseek-v3.2-20251201"`.

### Where the API call is made

`src/apex/qwen_client.py:138-143`:
```python
async with session.post(
    self._api_url,
    json=payload,
    timeout=timeout,
) as resp:
```

Default URL: `"https://openrouter.ai/api/v1/chat/completions"` (`qwen_client.py:64`).
Configured URL: `api_url = "https://openrouter.ai/api/v1/chat/completions"` (`config/settings.py:1393`).

### Auth

`qwen_client.py:81-88` — Bearer token + OpenRouter attribution headers, set on the persistent `aiohttp.ClientSession`:
```python
self._session = aiohttp.ClientSession(
    headers={
        "Authorization": f"Bearer {self._api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": self._http_referer,
        "X-Title": self._x_title,
    }
)
```
API key resolution: `_build_apex` at `src/config/settings.py:2856-2861` — `APEX_API_KEY` env var takes precedence over shared `OPENROUTER_API_KEY`.

### Retry policy

NONE. `qwen_client.py:38-44` (class docstring of `APEXOptimizationError`):
> Unlike TIASAnalysisError, there is no retryable flag. APEX operates in the live trade execution path — if DeepSeek fails for any reason the caller immediately falls back to Claude's original parameters. APEX failure NEVER blocks a trade.

`max_retries` is NOT a field on `APEXSettings` (verified by reading `config/settings.py:1389-1446`). Compare TIAS: `TIASSettings.max_retries: int = 1` (`config/settings.py:1382`).

### Current timeout

- Code default (kwarg): `timeout_seconds: int = 30` (`qwen_client.py:98`).
- Settings default: `timeout_seconds: int = 60` (`config/settings.py:1396`).
- Live config: `timeout_seconds = 60` (`config.toml:960`) with comment `"Layer 3: was 30s; DeepSeek frequently responds at 30-32s causing APEX_TIMEOUT."`.
- Pass-through site: `optimizer.py:229` — `timeout_seconds=self._settings.timeout_seconds` so the configured 60s wins.

VERIFIED: timeout was raised from 30s to 60s and is currently 60s in production config.

## 4. What DeepSeek optimizes

System prompt section "WHAT YOU OPTIMIZE" (`src/apex/prompts.py:49-55`):

```
1. DIRECTION: Same as the trader OR flipped if TIAS overwhelmingly shows the opposite wins.
2. STOP LOSS: ATR-proportional. Tight enough to limit damage, wide enough to survive noise.
3. TAKE PROFIT: CRITICAL RULE — NEVER set TP below the trader's original TP. The trader set that target based on analysis. Match or EXCEED it. Regime-adjust upward, never downward.
4. POSITION SIZE: Scale by TIAS profit factor. High profit factor (>2.0) coins get MORE capital. Low profit factor (<1.0) coins get LESS.
5. EXIT STRATEGY: Prefer "fixed" mode (fixed TP target). Use "trail_only" ONLY when TIAS shows >70% win rate AND avg capture >1.5% for this coin with trailing exits. Otherwise use "fixed".
6. ADD-ON: Recommend adding to position on pullback ONLY when TIAS shows the coin trends after pullbacks.
```

Required output JSON (`prompts.py:210-224`): `direction, sl_pct, tp_pct, tp_mode, position_size_usd, leverage, entry_timing, add_on_pullback, add_trigger_pct, add_size_pct, reasoning, confidence`.

Decision tree mapping these to `OptimizedTrade` parsing happens in `_parse_response` (`optimizer.py:369-460`).

## 5. Direction flip discipline

### Pre-call code-level direction lock (`optimizer.py:665-711`)

Verbatim logic:
- `trending_down` → natural direction `Sell`; `trending_up` → natural direction `Buy` (`:685-688`).
- For trending regimes: ALWAYS lock (`:691-699`):
  ```python
  if natural_dir:
      if claude_direction == natural_dir:
          return True, f"{regime} aligns with {claude_direction}"
      else:
          return (True, f"Claude chose {claude_direction} against {regime} (per-coin override)")
  ```
- For `volatile`: lock unless `_check_flip_evidence` returns True (≥70% WR with ≥8 opposite-direction trades, `:650-663`).
- For `ranging`/`dead`/`unknown`: NO pre-call lock (`:709-711`).

### Post-parse confidence-gated discipline (`optimizer.py:713-743`)

```python
def _enforce_flip_confidence(self, optimized, claude_direction, regime):
    if regime in ("trending_up", "trending_down", "volatile"):
        return False, ""  # Already governed by pre-call lock
    if optimized.direction == claude_direction:
        return False, ""  # No flip happened
    threshold = float(getattr(self._settings, "apex_min_flip_confidence", 0.90))
    conf = float(getattr(optimized, "confidence", 0.0) or 0.0)
    if conf < threshold:
        return True, (f"flip {claude_direction}→{optimized.direction} "
                      f"in regime={regime} blocked: conf={conf:.2f}<{threshold:.2f}")
    return False, ""
```

If reverted, `optimizer.py:266-275` resets direction, sets `was_flipped=False`, prepends `[FLIP BLOCKED conf<min]` to reasoning, increments `_lock_override_count`.

### Authorized-flip-blocks-resize (`optimizer.py:276-290`)

When `apex_block_flip_resize` setting is True (default; `config/settings.py:1446`):
```python
elif optimized.was_flipped and getattr(self._settings, "apex_block_flip_resize", True):
    _orig_size = float(getattr(optimized, "original_size", 0.0) or 0.0)
    if _orig_size > 0 and abs(optimized.position_size_usd - _orig_size) > 0.01:
        log.warning(f"APEX_FLIP_RESIZE_BLOCKED | sym={symbol} flip=...")
        optimized.position_size_usd = _orig_size
```

### Per Issue 9: "no rolling FLIP-rate check"

VERIFIED. `flip_rate` is exposed only as a cumulative health stat at `optimizer.py:640`:
```python
"flip_rate": self._flip_count / max(self._optimized_count, 1),
```
No time-windowed throttle exists — searched: `grep -n "flip_rate\|rolling\|flip_count" src/apex/optimizer.py src/apex/gate.py`. Disciplines that DO exist are per-trade only:
- `_check_direction_lock` (regime-based, `optimizer.py:665`).
- `_enforce_flip_confidence` (per-trade confidence ≥0.90, `optimizer.py:713`).
- `apex_block_flip_resize` (per-trade size revert, `optimizer.py:276`).

There is NO check that says "if recent flip rate is X%, block further flips". Issue 9's observation stands.

### 5 examples of APEX_FLIP with full context (24-hour window)

From log union `workers.log + workers.2026-05-02_04-31-00_392071.log + workers.2026-05-01_00-01-33_829054.log` filtered to `ts >= 2026-05-01 11:48`:

1. `2026-05-02 03:00:16.700 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.3% tp=0.5% cls=low sz=$1000→$1000 mode=fixed conf=100% regime=ranging ms=4119 | did=d-1777690683074`
2. `2026-05-02 03:59:14.939 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.8% tp=1.4% cls=medium sz=$1200→$1200 mode=fixed conf=100% regime=ranging ms=8204 | did=d-1777694209734`
3. `2026-05-02 04:16:10.859 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.8% tp=1.4% cls=medium sz=$600→$600 mode=fixed conf=100% regime=ranging ms=2311 | did=d-1777695235927`
4. `2026-05-02 04:57:18.801 | WARNING | APEX_FLIP | sym=RENDERUSDT claude=Buy apex=Sell sl=0.3% tp=0.5% cls=low sz=$500→$500 mode=fixed conf=100% regime=ranging ms=2267 | did=d-1777697693903`
5. `2026-05-02 06:19:24.907 | WARNING | APEX_FLIP | sym=NEARUSDT claude=Sell apex=Buy sl=0.3% tp=0.5% cls=low sz=$500→$500 mode=fixed conf=95% regime=ranging ms=9031 | did=d-1777702618197`

(One additional flip: `2026-05-02 06:26:33.827 NEARUSDT claude=Sell apex=Buy ... conf=95% regime=ranging`.)

Observation: ALL six flips happened in `regime=ranging` (the unlocked regime). FOUR were `RENDERUSDT Buy→Sell` at conf=100%; TWO were `NEARUSDT Sell→Buy` at conf=95%. Both NEARUSDT flips triggered `APEX_FLIP_RESIZE_BLOCKED` (forced size $1200→$500 to original).

Two `APEX_FLIP_BLOCKED` events in the same window (confidence below 0.90):
- `2026-05-02 04:48:52.346 SANDUSDT Sell→Buy regime=ranging conf=0.75<0.90`
- `2026-05-02 05:25:03.906 HYPERUSDT Buy→Sell regime=ranging conf=0.85<0.90`

## 6. Size / SL / TP modification: bounds & limits

`_apply_constraints` (`optimizer.py:462-533`) — applied AFTER DeepSeek response parsed:

| Field | Floor | Ceiling | Source |
|-------|-------|---------|--------|
| `position_size_usd` | `100.0` | `self._settings.max_position_size_usd` (1200, `config.toml:963`) | `:480-482` |
| `leverage` | `1` | `self._settings.max_leverage` (5, `config.toml:964`) | `:485` |
| `sl_pct` | `max(0.2, recommended_sl_pct × 0.6)` (per-class) | `5.0` | `:492-497` |
| `tp_pct` | `max(min_tp_pct=0.3, recommended_tp_pct × 0.6)` (per-class) | `8.0` (then `APEX_TP_CAP` per-class on top) | `:502-510` |
| `confidence` | `0.0` | `1.0` | `:513` |

Volatility-class TP cap (`optimizer.py:200-216,302-309`):
- Map `tp_cap_multiplier_by_class` (default `{"dead": 1.2, "low": 1.3, "medium": 1.3, "high": 1.4, "extreme": 1.5}`, `config/settings.py:1428-1430`, `config.toml:1004-1009`).
- `_tp_cap = round(recommended_tp_pct × multiplier, 2)`. Enforced after DeepSeek by clamping `optimized.tp_pct = _tp_cap` and emitting `APEX_TP_CAP`.

### 5 examples of size changes (APEX_OK with size delta or GATE adjustments) — 24-hour window

1. `2026-05-02 03:50:34.249 APEX_OK | sym=ONDOUSDT dir=Buy sl=0.3% tp=0.5% cls=low lev=3x sz=$1200→$600 conf=65% regime=ranging` (DeepSeek sized $1200 → constrained/decided $600).
2. `2026-05-02 03:50:43.128 APEX_OK | sym=INJUSDT dir=Buy sl=0.8% tp=1.4% cls=medium lev=3x sz=$800→$400 conf=100% regime=ranging`.
3. `2026-05-02 04:41:02.572 APEX_OK | sym=ONDOUSDT dir=Buy sl=0.3% tp=0.5% cls=low lev=3x sz=$500→$300 conf=60% regime=ranging`.
4. `2026-05-02 04:48:47.295 APEX_OK | sym=AXSUSDT dir=Buy sl=0.9% tp=2.5% cls=medium lev=3x sz=$500→$300 conf=100% regime=trending_up`.
5. `2026-05-02 05:18:04.271 APEX_OK | sym=AEROUSDT dir=Buy sl=0.8% tp=1.4% cls=medium lev=5x sz=$500→$800 conf=75% regime=ranging` (size INCREASE — gate then capped at 1.5×: `2026-05-02 05:18:07.214 CONVICTION_SIZE_CAP | sym=AEROUSDT claude=$500 requested=$800 capped=$750 mult=1.5x`).

Gate-side size adjustments (24-hour window, examples):
- `GATE_ADJUST | sym=INJUSDT changes=[conviction_cap=$247(w=0.5x)]` (low profit-factor weight).
- `GATE_ADJUST | sym=MANAUSDT changes=[conviction_cap=$246(w=0.5x), APEX_GUARDRAIL_TP_FLOOR(apex=0.09->claude=0.09), APEX_CONF_SIZE(30%<50%,size_scale=60%)]`.

## 7. Failure modes — counts in last 24h

Time window: 2026-05-01 11:48:00 → 2026-05-02 11:49:30 UTC.

| Tag | Source | Count |
|-----|--------|------:|
| `APEX_TIMEOUT` (raised by `qwen_client.py:195-200`) | grep `APEX_TIMEOUT` | 0 |
| `APEX_TIMEOUT_REGIME` (`optimizer.py:351`, regime-fallback path) | grep | 0 |
| `APEX_PARSE_FAIL` | grep | 0 |
| `APEX_FAIL_UNEXPECTED` (`optimizer.py:359`) | grep | 0 |
| `APEX_FALLBACK` literal | grep | 0 |
| `APEX_SKIP` (`optimizer.py:555` — generic fallback log) | grep | 0 |
| `APEX_SKIP_NO_PRICE` (`optimizer.py:118`) | grep | 0 |
| `APEX_PRICE_FALLBACK` (`assembler.py:170`) | grep | 1 |
| `APEX_OK` | grep | 51 |
| `APEX_FLIP` | grep | 6 |
| `APEX_FLIP_BLOCKED` | grep | 2 |
| `APEX_FLIP_RESIZE_BLOCKED` | grep | 6 |
| `APEX_TIER` (total optimizations) | grep | 57 |
| `APEX_TP_CAP` | grep | 38 |
| `APEX_GUARDRAIL_TP_FLOOR` | grep | 23 |
| `APEX_CONF_SIZE` | grep | 1 |

Failure rate (true failures): 0/57 = 0% in this window. NOTE: APEX module appears stable since the timeout bump from 30s → 60s; the only "skip" event was a single `APEX_PRICE_FALLBACK` to REST ticker, which still produced an APEX_OK.

## 8. Parallelism — `asyncio.gather` usage

VERIFIED. The optimize-fan-out happens in the orchestrator, not inside `optimizer.py` itself.

`src/core/layer_manager.py:1254-1271`:
```python
if apex:
    _apex_tasks = {}
    for _i, _t in enumerate(plan.new_trades):
        if isinstance(_t, dict) and _t.get("symbol"):
            _apex_tasks[_i] = apex.optimize(_t, plan)
    if _apex_tasks:
        _apex_results = await asyncio.gather(
            *_apex_tasks.values(), return_exceptions=True
        )
        for _idx, _res in zip(_apex_tasks.keys(), _apex_results):
            if isinstance(_res, Exception):
                _sym = plan.new_trades[_idx].get("symbol", "?")
                log.warning(
                    f"APEX_GATHER_FAIL | sym={_sym} "
                    f"err='{str(_res)[:80]}' | {ctx()}"
                )
            else:
                optimized_results[_idx] = _res
```

`asyncio.gather(..., return_exceptions=True)` ensures one failed coin does not abort the others.

### Single-coin vs multi-coin timing

Per-call timing is logged via `APEX_TIMING` at `optimizer.py:324-328`:
```
APEX_TIMING | sym={symbol} el={_opt_el_ms:.0f}ms | assemble={_assemble_ms:.0f}ms deepseek={_deepseek_ms:.0f}ms parse={_parse_ms:.0f}ms constraints={_constraints_ms:.0f}ms
```

Single-coin examples (24-hour window):
- `APEX_TIMING | sym=ENAUSDT el=14285ms | assemble=149ms deepseek=14135ms parse=0ms constraints=0ms`
- `APEX_TIMING | sym=AXSUSDT el=5777ms | assemble=178ms deepseek=5598ms parse=0ms constraints=0ms`
- `APEX_TIMING | sym=HYPEUSDT el=34353ms | assemble=118ms deepseek=34234ms parse=0ms constraints=0ms`

Range observed: `el=2099ms` (NEARUSDT 06:26 flip) up to `el=34353ms` (HYPEUSDT). DeepSeek HTTP latency dominates — `assemble` is consistently 100-300 ms, parse/constraints are 0 ms.

Multi-coin parallelism evidence — `did=d-1777693698066` (3-coin batch ONDOUSDT, INJUSDT, BLURUSDT):

```
03:50:21.529 APEX_TIER | sym=ONDOUSDT  ranging fallback
03:50:21.591 APEX_TIER | sym=INJUSDT   full_optimize
03:50:21.625 APEX_TIER | sym=BLURUSDT  ranging fallback
03:50:34.249 APEX_TIMING | ONDOUSDT  el=12916ms
03:50:34.728 APEX_TIMING | BLURUSDT  el=13394ms
03:50:43.128 APEX_TIMING | INJUSDT   el=21795ms
```

All three started within 100 ms of each other (`asyncio.gather` fan-out) and finished as their respective DeepSeek responses arrived. Wall-clock for the whole batch: ~22 seconds (slowest single call), not the sum (~48 seconds). This is the parallelism payoff.

distinct-`did` count (24h window, where ≥2 coins): three batches with 3 coins, eight batches with 2 coins. So multi-coin batches are common.


=====================================================================
## FILE: I4_apex_gate.md
=====================================================================

# I4 — APEX TradeGate

Forensic refresh: 2026-05-02 (replaces 2026-04-28 baseline).

## 1. File path & class

`src/apex/gate.py:29` — `class TradeGate`. File length: 474 lines (`wc -l`).

Constructor: `TradeGate.__init__(self, services: dict, settings: Any)` (`gate.py:41-46`):
```python
self._services = services
self._settings = settings
self._conviction_cache: dict[str, tuple[float, float]] = {}
self._conviction_cache_ttl: float = 300.0  # 5 minutes
```

Wired in `src/workers/manager.py:1833-1835`:
```python
from src.apex.gate import TradeGate
apex_gate = TradeGate(self._services, apex_cfg)
self._services["apex_gate"] = apex_gate
```

## 2. Public methods

| Method | Signature | File:line | Purpose |
|--------|-----------|-----------|---------|
| `__init__` | `(services, settings)` | `:41` | Stores services dict and settings; initializes 5-minute conviction cache. |
| `validate` | `async (trade: dict) -> dict` | `:48` | Runs the 14 hard-safety checks. NEVER blocks; mutates `trade` in place and returns it. |

Private helper:

| Method | File:line |
|--------|-----------|
| `_get_conviction_weight(symbol)` | `:356` |

## 3. `_get_conviction_weight` — regime_detector wiring

VERIFIED: `_get_conviction_weight` calls `regime_detector.get_coin_regime(symbol)` at `src/apex/gate.py:370`. Verbatim block (`:367-396`):

```python
detector = self._services.get("regime_detector")
if detector:
    coin_regime = detector.get_coin_regime(symbol)
    # Definitive-fix Phase 7 (2026-04-28) — emit per-call
    # cache-query telemetry so REGIME_FALLBACK frequency can
    # be correlated with the cold-start window. ``hit`` is
    # True only when the per-coin cache was populated for
    # this symbol; ``cache_size`` shows whether the cache
    # is even warm yet.
    _hit = coin_regime is not None
    _cache_size = (
        len(getattr(detector, "_per_coin_regimes", {}) or {})
    )
    _ready = bool(getattr(detector, "is_ready", lambda: True)())
    log.info(
        f"REGIME_CACHE_QUERY | sym={symbol} reader=apex_gate "
        f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
    )
    if coin_regime is not None:
        _regime = str(coin_regime.regime.value)
    elif hasattr(detector, "_last_regime") and detector._last_regime:
        _regime = str(detector._last_regime.regime.value)
        log.warning(
            "REGIME_FALLBACK | sym={sym} source=gate | "
            "per-coin unavailable, using global={r} | {ctx}",
            sym=symbol, r=_regime, ctx=ctx(),
        )
```

### What conviction weight is for

Conviction weight scales the per-trade capital allocation in `validate` Check 4 (`gate.py:123-160`). It's a multiplier (0.5x – 2.0x) applied to a 40% base capital fraction (`base_pct = 0.4`).

`_get_conviction_weight` (`gate.py:356-474`) computes a profit-factor-based weight from TIAS history:

| Profit factor | Weight |
|---------------|-------:|
| > 3.0 | `2.0` |
| > 2.0 | `1.5` |
| > 1.0 | `1.0` |
| > 0.5 | `0.7` |
| ≤ 0.5 | `0.5` |
| `total_lost == 0` | capped at PF=10.0 then mapped to 2.0 |
| trades < `conviction_min_trades` (3) | default `0.75` |

Implementation steps:
1. Resolve regime via `get_coin_regime` (`gate.py:367-396`).
2. Query `tias_repo.get_symbol_full_history(symbol, limit=20, regime=_regime)` (`gate.py:413-415`).
3. If regime-filtered total < `conviction_min_trades`, fall back to all-regime query (`gate.py:419-421`).
4. Compute profit factor from `pnl_usd` aggregates (`gate.py:434-449`).
5. Map to weight (`gate.py:452-461`).
6. Cache result for 5 minutes keyed on `f"{symbol}:{_regime or 'all'}"` (`gate.py:399-404, 425, 463`).
7. Emit `CONVICTION_WEIGHT | sym=... pf=... weight=...x` at INFO (`gate.py:464-469`).

After computing weight, Check 4 (`gate.py:138-147`) ALSO multiplies a Layer 2 score modifier:
- `signal_score >= 80`: weight × 1.20 (A+).
- `>= 68`: no change (A).
- `>= 56`: weight × 0.90 (B).
- `> 0`: weight × 0.80 (C/D).

Combined `weighted_pct = base_pct (0.40) × weight`, clamped to `[0.05, 0.40]` (`gate.py:148-150`). The trade size is then capped at `available × weighted_pct`.

## 4. Decision logic — allow / block / modify

The gate NEVER blocks. From `gate.py:7-16`:
> Runs 12 checks. Each check MAY adjust parameters but NEVER blocks. Modifications are logged at INFO level and attached to the trade dict as `_gate_adjustments` for TIAS feedback.

(Class docstring says 12; current code has 14 — see truth doc `:587-606`.)

### The 14 checks (verbatim labels, file:line)

| # | Check | File:line | Action |
|---|-------|-----------|--------|
| 0 | Claude directive size cap (Phase 5) — final size ≤ `claude_original × gate_apex_size_cap_mult` (1.5×) | `:65-92` | Modify (clamp size). Logs `CONVICTION_SIZE_CAP`. |
| 1 | Maximum position size (`max_position_size_usd`, default 1200) | `:94-99` | Modify (clamp). |
| 2 | Maximum leverage (`max_leverage`, default 5) | `:101-106` | Modify (clamp). |
| 3 | Maximum concurrent positions (5) — if at max, scale size to 30% | `:108-121` | Modify. |
| 4 | Capital availability (conviction-weighted) — `weight × signal_score modifier × 40% base` | `:123-160` | Modify (size cap). |
| 5 | Duplicate-symbol position → halve size | `:162-172` | Modify. |
| 6 | Recent cooldown (`coordinator.is_symbol_cooled_down`) → halve size | `:174-186` | Modify. |
| 7 | Minimum position size floor ($50) | `:188-193` | Modify. |
| 8 | TP floor — APEX TP cannot cross Claude's TP direction | `:221-237` | Modify (revert TP). Logs `APEX_GUARDRAIL_TP_FLOOR`. |
| 9 | Trail activation floor (`gate_trail_activation_floor_pct_of_tp`, default 50% of TP distance) | `:239-256` | Modify trail param. |
| 10 | Trail distance floor (`gate_trail_distance_floor_pct`, default 40%) | `:257-268` | Modify trail param. |
| 11 | Mode override — `trail_only` → `trail_with_ceiling` (uses Claude TP as ceiling) | `:270-277` | Modify mode. |
| 12 | Confidence-based size scaling (`gate_confidence_floor`, default 0.50) | `:279-291` | Modify size when confidence < floor. Logs `APEX_CONF_SIZE`. |
| 13 | R:R ratio sanity (rr=0 → ×0.25; 0<rr<0.5 → ×0.5) | `:295-311` | Modify size. |
| 14 | TP/SL sanity — if differ <0.1%, nudge TP by ±2% | `:313-327` | Modify TP. Logs `TPSL_IDENTICAL`. |

### Inputs (services consumed)

| Service / key | Used by | File:line |
|---------------|---------|-----------|
| `position_service` | Check 3 (`get_positions`), Check 5 (`get_position`) | `:111, 165` |
| `fund_manager` | Check 4 (read `_account_state.available`) | `:125-130` |
| `regime_detector` | `_get_conviction_weight` (regime classification) | `:368-396` |
| `tias_repo` | `_get_conviction_weight` (`get_symbol_full_history`) | `:408-421` |
| `trade_coordinator` | Check 6 (`is_symbol_cooled_down`, `get_symbol_cooldown_remaining`) | `:176-184` |
| `market_service` | Check 9 (`get_ticker` for entry estimate) | `:213-216` |
| `structure_cache` | Check 13 (`get(symbol).structural_placement.rr_ratio`) | `:297-300` |

Inputs (settings on `APEXSettings`, `config/settings.py:1389-1446`):
- `max_position_size_usd` (1200), `max_leverage` (5).
- `gate_apex_size_cap_mult` (1.5), `gate_tp_floor_enabled` (True), `gate_trail_activation_floor_pct_of_tp` (15.0; runtime default 50.0 — see code path `:241`), `gate_trail_distance_floor_pct` (40.0), `gate_mode_override_enabled` (True), `gate_confidence_floor` (0.50).
- `conviction_enabled` (True), `conviction_min_trades` (3).

Inputs (trade dict):
- `_apex_optimized` flag (set by `core/layer_manager.py:1429,1453`) gates Checks 8-12 at `gate.py:201`.
- `_apex_tp_mode`, `_apex_confidence`, `_apex_original_tp`, `_apex_original_sl`, `_apex_original_size`, `_claude_original_size_usd`.

### Outputs

- `trade["size_usd"]`, `trade["leverage"]`, `trade["take_profit_price"]`, `trade["stop_loss_price"]` mutated in place.
- `trade["_gate_adjustments"]` — comma-joined modification labels (`gate.py:333`). Persisted to `trade_intelligence.gate_adjustments` (col 91).
- `trade["_gate_validation_ms"]` — total validate elapsed ms (`gate.py:331`).
- `trade["_apex_trail_activation_pct"]`, `trade["_apex_trail_distance_pct"]` — set by Checks 9-10 for downstream `TradePlan`.

Logs:
- `GATE_ADJUST | sym=... changes=[...]` at INFO when modifications applied (`:334-337`).
- `GATE_PASS | sym=... no_changes` at DEBUG when nothing changed (`:339`).
- `GATE_TIMING | sym=... el=...ms modifications=N` at INFO (`:342-345`); `GATE_TIMING_SLOW` at WARNING when `el > 500ms` (`:346-350`).

## 5. APEX → Enforcer handoff

There is NO direct call from `TradeGate` to `PerformanceEnforcer`. The handoff is mediated by the brain orchestrator and strategy worker.

Order of operations in `core/layer_manager.py:_execute_new_trades` (`:1184-1380`):

1. APEX optimize fan-out (`layer_manager.py:1254-1271`).
2. `_apply_apex_optimization` per trade (`layer_manager.py:1316`).
3. **TradeGate validate** (`layer_manager.py:1318-1323`):
   ```python
   gate = self.services.get("apex_gate")
   if gate:
       _t0 = time.time()
       trade = await gate.validate(trade)
       _gate_ms = (time.time() - _t0) * 1000
   ```
4. `strategy_worker._execute_claude_trade(trade, position_symbols, plan)` (`layer_manager.py:1326-1328`).

The `PerformanceEnforcer` (`src/strategies/performance_enforcer.py:31`) is queried INSIDE `_execute_claude_trade` to apply the size multiplier at `get_size_multiplier(:126-149)` before `OrderService.place_order` is called.

So the chain post-APEX is:
**APEX optimizer → `_apply_apex_optimization` → TradeGate.validate → StrategyWorker._execute_claude_trade → PerformanceEnforcer (size multiplier inside execute) → OrderService.place_order.**

The exact handoff point from APEX gate to the next stage is `core/layer_manager.py:1326`:
```python
success, _reason_code = await strategy_worker._execute_claude_trade(
    trade, position_symbols, plan,
)
```

## 6. Live evidence (24h window, 2026-05-01 11:48 → 2026-05-02 11:49 UTC)

| Tag | Count |
|-----|------:|
| `GATE_ADJUST` (modifications applied) | many; sample below |
| `CONVICTION_SIZE_CAP` (Check 0 binding) | observed (e.g. AEROUSDT $500→$750 at 1.5×) |
| `APEX_GUARDRAIL_TP_FLOOR` (Check 8) | 23 |
| `APEX_CONF_SIZE` (Check 12) | 1 |
| `CONVICTION_WEIGHT` | observed every TIAS-resolved gate call |

Representative `GATE_ADJUST` lines:
- `2026-05-02 04:41:02.656 GATE_ADJUST | sym=INJUSDT changes=[conviction_cap=$247(w=0.5x)]`
- `2026-05-02 05:18:07.214 CONVICTION_SIZE_CAP | sym=AEROUSDT claude=$500 requested=$800 capped=$750 mult=1.5x`
- `2026-05-02 06:02:33.034 GATE_ADJUST | sym=MANAUSDT changes=[conviction_cap=$246(w=0.5x), APEX_GUARDRAIL_TP_FLOOR(apex=0.09->claude=0.09), APEX_CONF_SIZE(30%<50%,size_scale=60%)]`
- `2026-05-02 03:50:43.207 GATE_ADJUST | sym=ONDOUSDT changes=[conviction_cap=$370(w=0.8x), APEX_GUARDRAIL_TP_FLOOR(apex=0.27->claude=0.27)]`

The combined log (`MANAUSDT` above) shows three independent checks — Check 4 (conviction), Check 8 (TP floor), Check 12 (confidence size scaling) — firing on the same trade in a single `validate()` call, demonstrating that the gate runs the full 14-step chain regardless of which earlier check has already adjusted the trade.


=====================================================================
## FILE: J1_performance_enforcer.md
=====================================================================

# J1 — PerformanceEnforcer

Collection timestamp: 2026-05-02 ~11:45 UTC
DB snapshot: /tmp/trading_snapshot_1777722335.db
Log files searched: workers.2026-05-02_04-31-00_392071.log (active, 4.5 MB), workers.log (current symlink, 320 KB)

---

## 1. File location & size

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/performance_enforcer.py`
- Lines of code: **577** (`wc -l`)
- Class: `PerformanceEnforcer` at performance_enforcer.py:31

### Public methods (16)

| Method | Signature | File:line |
|---|---|---|
| `__init__` | `(self, settings, db, services: dict)` | performance_enforcer.py:37 |
| `is_trading_halted` | `() -> bool` | performance_enforcer.py:87 |
| `should_allow_trade` | `(leverage: int = 1) -> tuple[bool, str]` | performance_enforcer.py:91 |
| `get_max_positions_override` | `() -> int \| None` | performance_enforcer.py:110 |
| `get_min_score_override` | `() -> int \| None` | performance_enforcer.py:118 |
| `get_size_multiplier` | `() -> float` | performance_enforcer.py:126 |
| `qualify_survival_trade` | `(symbol, structure_cache=None) -> tuple[bool, str]` | performance_enforcer.py:151 |
| `check_and_enforce` | `() -> dict` (async) | performance_enforcer.py:198 |
| `get_coaching_text` | `(structure_cache=None) -> str` | performance_enforcer.py:428 |
| `on_signal_generated` | `() -> None` | performance_enforcer.py:503 |
| `on_setup_sent_to_brain` | `() -> None` | performance_enforcer.py:506 |
| `on_trade_executed` | `() -> None` | performance_enforcer.py:509 |
| `on_trade_closed` | `(pnl_pct: float, was_win: bool) -> None` | performance_enforcer.py:514 |
| `get_urgency_level` | `() -> int` | performance_enforcer.py:534 |
| `get_status` | `() -> dict` | performance_enforcer.py:542 |
| `reset` | `() -> None` | performance_enforcer.py:563 |

Private helpers: `_check_recovery` (181), `_build_report` (289), `_get_level_change_reason` (303), `_collect_stats` (317), `_check_heartbeat` (380), `_check_day_reset` (411).

---

## 2. Role in pipeline

Module docstring (performance_enforcer.py:1-20) declares: *"Enforcer v2 — PnL-Based Intelligent Throttling. Primary signal: Daily PnL %. Secondary signal: Loss streak (only when PnL is already negative). Manages trade INTENSITY, never halts. Full halt is delegated to DailyPnLManager."*

Three enforcement levels (performance_enforcer.py:11-13):
- **Level 0 NORMAL**: PnL ≥ 0% — trade freely
- **Level 1 CAPITAL_PRESERVATION**: PnL < -2% — max 3 positions, max 3x leverage
- **Level 2 SURVIVAL**: PnL < -5% — max 2 positions (default `level_2_max_positions=2`), max 3x leverage (default `level_2_max_leverage=3`); quality-gate (A+/A) replaces BTC/ETH-only

Position in pipeline (verified by call-site grep):
- **Tick driver**: `EnforcerWorker` runs `enforcer.check_and_enforce()` on a 300 s default interval (enforcer_worker.py:15-22).
- **Pre-execution**, the enforcer participates in directive filtering via:
  - layer_manager.py:1219 — `should_allow_trade(leverage=1)` (LayerManager pre-execution check)
  - strategy_worker.py:1158 — `should_allow_trade(leverage=_lev)` (StrategyWorker before signal handoff)
  - strategy_worker.py:1175 — `qualify_survival_trade(symbol, _sc)` (L2 quality gate)
- **The enforcer does NOT sit between APEX and the order gate** in the runtime sense. APEX `TradeGate.validate()` (apex/gate.py:48) makes its own checks; it does **not** read the enforcer. The brief's "after APEX, before Gate" framing does not match the wiring — the enforcer affects sizing/coaching upstream and the leverage/quality gate at strategy-worker level, not between APEX and the order-side Layer 3 gate.

---

## 3. Performance stats collection

Implemented in `_collect_stats()` at performance_enforcer.py:317-378.

Source: **DB query** against `trade_thesis` (`status='closed' AND DATE(closed_at)=today`):

```
performance_enforcer.py:321-328
rows = await self.db.fetch_all(
    """SELECT symbol, direction, actual_pnl_pct, close_reason, exchange_mode
       FROM trade_thesis
       WHERE status = 'closed' AND DATE(closed_at) = ?
         AND (close_reason IS NULL OR close_reason != 'transformer_switch')
       ORDER BY closed_at DESC""",
    (today,),
)
```

Computed in-memory from the row set:
- `_trades_today` (line 330), `_wins_today` (331), `_losses_today` (333), `_profit_today_pct` (334).
- Streak detection (336-352): walks rows newest-first, +1 per consecutive win, –1 per consecutive loss; breakeven (pnl == 0) is skipped — does not break or extend streak.
- `_per_coin` dict (355-366), `_per_direction` dict (369-376).

Exception path emits `ENFORCER_STATS_FAIL | err='...' | {ctx()}` at line 378.

### ENFORCER_STATS event verification (gap from prior collection)

**Gap CONFIRMED.** No event named `ENFORCER_STATS` exists in source.

- Codebase grep (`grep -rn "ENFORCER_STATS" src/`): 1 hit — `ENFORCER_STATS_FAIL` at performance_enforcer.py:378 (error-only).
- Logs grep (`grep "ENFORCER_STATS" workers.2026-05-02_04-31-00_392071.log workers.log`): 0 hits.

The actual emitted event for stats is `ENFORCER_STATE` at performance_enforcer.py:280-285, fired by `check_and_enforce()` after `_collect_stats()` runs. 5 sample events (verbatim):

```
2026-05-02 06:22:41.141 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=29 | wins=5 | losses=23 | wr=0.17 | strk=-12 | pnl=-0.90% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 06:29:41.174 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 11:22:45.990 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 11:31:46.066 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
2026-05-02 11:47:46.136 | INFO | src.strategies.performance_enforcer:check_and_enforce:280 | ENFORCER_STATE | trades=30 | wins=5 | losses=24 | wr=0.17 | strk=-13 | pnl=-1.00% | el=1 | sz_mult=0.75 | trigger=streak_boost | no_ctx
```

Companion `ENFORCER_BEAT` (enforcer_worker.py:24) sample:
```
2026-05-02 11:22:45.991 | INFO | src.workers.enforcer_worker:tick:24 | ENFORCER_BEAT | total=30T W=5 L=24 wr=16.7% strk=-13 hb=OK | no_ctx
```

NOT FOUND — `ENFORCER_STATS` (the named event from the task brief) — searched: workers.2026-05-02_04-31-00_392071.log, workers.log, src/. Confirmed: it does not exist; the emit is named `ENFORCER_STATE`.

NOT FOUND — `_collect_stats` elapsed_ms — searched: performance_enforcer.py:317-378. The method does not log its own elapsed time; only the parent `check_and_enforce()` writes the post-collect summary line, which doesn't break out the SELECT cost.

DB snapshot validates the in-log numbers (trade_thesis closed today=2026-05-02): 30 closed rows.

---

## 4. Coaching generation

Builder: `get_coaching_text(structure_cache=None) -> str` at performance_enforcer.py:428-499.

Format (line-by-line from the source):
- Header: `"PERFORMANCE COACH (your stats today):"` (436)
- Trades line: `f"  Trades: {self._trades_today} | Wins: {self._wins_today} | Losses: {self._losses_today}"` (437-439)
- Win-rate line: `f"  Win rate: {wr:.0%} | PnL: {pnl:+.2f}% | Streak: {streak:+d}"` (440)
- One of four "Session" lines depending on `(level, pnl)` (442-480):
  - `level==0 and pnl>=0`: "Session: PROFITABLE. Trade normally with full conviction…"
  - `level==0 and pnl<0`: "Session: SLIGHTLY NEGATIVE. Position sizes reduced to {sz_mult:.0%}…"
  - `level==1`: "CAPITAL PRESERVATION MODE. Max 3 positions, leverage capped at 3x. Only A+ setups with strong consensus. Protect capital."
  - `level==2`: "RISK MANAGEMENT MODE. Max {l2_max_pos} positions, leverage {l2_max_lev}x. Quality-gate: A+/A setups only with confluence>={l2_min_confluence} and RR>={l2_min_rr}…"
- Best/worst coin lines (482-486)
- Buy/Sell win-rate line (488-494)
- Optional heartbeat-stale warning (496-497)

Consumers: `brain/strategist.py:564-568` and `:1549-1553` call `enforcer.get_coaching_text(structure_cache=_sc)` and inject the string into Claude's prompt.

### 3 coaching outputs verbatim from logs

NOT FOUND — coaching text in logs — searched: workers.2026-05-02_04-31-00_392071.log, workers.log via `grep "PERFORMANCE COACH\|capital preservation\|RISK MANAGEMENT"` (0 hits). The string is built by `get_coaching_text()` and embedded directly into Claude's prompt; it is **not** log-emitted (no `log.info(...)` call in the function body, performance_enforcer.py:428-499).

For reference, what coaching text WOULD be produced from the current state (level=1, pnl=-1.00%, trades=30, wins=5, losses=24, streak=-13) per the format above:
```
PERFORMANCE COACH (your stats today):
  Trades: 30 | Wins: 5 | Losses: 24
  Win rate: 17% | PnL: -1.00% | Streak: -13
  CAPITAL PRESERVATION MODE. Max 3 positions, leverage capped at 3x. Only A+ setups with strong consensus. Protect capital.
  Best coin: <best> ({pnl:+.2f}%)
  Worst coin: <worst> ({pnl:+.2f}%)
  Buy win rate: ##% | Sell win rate: ##%
```
This is reconstruction, not a verbatim log capture.

---

## 5. Strategy filtering

The PerformanceEnforcer does **not** itself filter strategies by win-rate threshold. The "failing-strategy threshold" lives elsewhere in the codebase. The enforcer's filtering surface is via:
- `should_allow_trade(leverage)` — performance_enforcer.py:91-108 (blocks trades whose leverage exceeds level cap).
- `get_max_positions_override()` — performance_enforcer.py:110-116 (returns int cap or None).
- `get_min_score_override()` — performance_enforcer.py:118-124 (returns minimum score, default 80 at L1 / L2).
- `qualify_survival_trade(symbol, structure_cache)` — performance_enforcer.py:151-179 (rejects setups with quality<A, confluence<l2_min_confluence, or rr<l2_min_rr).

NOT FOUND — failing-strategy threshold inside performance_enforcer.py — searched: full file, grep "fail|disable|threshold". The min-score caps (level_1_min_score=80, level_2_min_score=80, performance_enforcer.py:75,78) are general signal-quality gates, not per-strategy disablers.

---

## 6. _trades_today

Set by `_collect_stats()` at performance_enforcer.py:330:
```
self._trades_today = len(rows)
```
…where `rows` is the result of the trade_thesis SELECT shown in §3.

NOT FOUND — `elapsed_ms` for the `_collect_stats()` SELECT — searched: performance_enforcer.py:317-378. The query is not timed; only the parent `check_and_enforce()` writes a post-collect summary line, which doesn't break out the SELECT cost.

DB-side timing reference (snapshot probe): `SELECT COUNT(*) FROM trade_thesis WHERE status='closed' AND DATE(closed_at)='2026-05-02'` returned 30 rows; consistent with the in-log `trades=30` field.

In-memory increments are intentionally **not** done on `on_trade_executed()` (performance_enforcer.py:509-512):
```
def on_trade_executed(self) -> None:
    # No in-memory increment — _collect_stats() is authoritative from DB.
    # Incrementing here would cause double-counting until the next stats cycle.
    pass
```

---

## 7. apply_restrictions(consensus_setups, mode)

The PerformanceEnforcer does **not** define `apply_restrictions`. That method belongs to `DailyPnLManager` (pnl_manager.py:310-333). It is called from `strategy_worker.py:681`:
```
filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)
```
See J2 (PnL Manager) for the seven modes (TARGET_HIT / PROTECT / GOOD_DAY / NORMAL / CAUTION / SURVIVAL / HALTED) and per-mode `max_score_threshold` thresholds (90/85/55/50/80/80/100).

The enforcer's "modes" are the three enforcement **levels** (NORMAL/PRESERVATION/SURVIVAL). They are computed in `check_and_enforce()` at performance_enforcer.py:241-258:

```
performance_enforcer.py:241-258
# ── Primary signal: Daily PnL ──
new_level = 0
if pnl >= 0:
    new_level = 0
elif pnl > self._pnl_caution_pct:        # 0% to -2%
    if streak <= self._streak_boost_threshold:
        new_level = 1   # PnL negative AND long streak = real problem
    else:
        new_level = 0
elif pnl > self._pnl_survival_pct:       # -2% to -5%
    new_level = 1   # Capital preservation
else:                                    # below -5%
    new_level = 2   # Survival
```

Level → score override mapping (`get_min_score_override`, performance_enforcer.py:118-124):
- `el >= 2` → `_l2_min_score` (config default 80)
- `el >= 1` → `_l1_min_score` (config default 80)
- otherwise → None (no override)

Level → max-positions mapping (`get_max_positions_override`, performance_enforcer.py:110-116):
- `el >= 2` → `_l2_max_pos` (config default 2)
- `el >= 1` → `_l1_max_pos` (config default 3)
- otherwise → None

Level → leverage gate (`should_allow_trade`, performance_enforcer.py:91-108):
- `el >= 2 and leverage > _l2_max_lev` (default 3) → block with reason `SURVIVAL: leverage=… exceeds limit of 3x`
- `el >= 1 and leverage > _l1_max_lev` (default 3) → block with reason `PRESERVATION: …`

Size-multiplier (`get_size_multiplier`, performance_enforcer.py:126-149):
- `pnl >= 0%` → 1.0
- `0 to -2%` → `_size_reduction_factor` (default 0.75)
- `-2% to -5%` → 0.50
- `< -5%` → 0.25 (or 0.40 / 0.50 if `_recovery_stage` ≥ 1 / ≥ 2)

---

## 8. Live state observed (24 h window)

- Enforcer level pinned at **el=1 (CAPITAL_PRESERVATION)** for the entire log span 04:31 → 11:48 UTC.
- One `ENFORCER_LEVEL` transition observed: 11:22:45.990 — `old_el=0 new_el=1 reason=streak_boost pnl=-1.00% strk=-13` (performance_enforcer.py:265-268). ENFORCER_STATE rows from 06:22 onward already show el=1, so a level reset must have happened between 06:32 (last 06:xx line) and 11:22 (first 11:xx line) — likely a process restart (no `ENFORCER_AUTO_RECOVERY` or `ENFORCER_MANUAL_RESET` event in 24h).
- `ENFORCER_TRADE_IN`: observed once at 06:29:10.278 — `pnl=-0.10 win=N strk=-13 recovery=0` (performance_enforcer.py:529-532).
- `ENFORCER_AUTO_RECOVERY`: 0 events in 24h.
- `ENFORCER_MANUAL_RESET`: 0 events in 24h.
- `ENFORCER_GRACE`: 0 events in 24h.
- `ENFORCER_STATS_FAIL`: 0 events in 24h.

---

## 9. Wiring summary

| Site | Purpose | File:line |
|---|---|---|
| `EnforcerWorker.tick()` | Drives `check_and_enforce()` every 300 s (configurable) | enforcer_worker.py:19-26 |
| `LayerManager._dispatch_claude_trades` | Calls `should_allow_trade(leverage=1)` for blanket level-2 leverage gate | layer_manager.py:1219 |
| `StrategyWorker._execute_claude_trade` (entry path) | `should_allow_trade(leverage=_lev)` → block if leverage exceeds level cap | strategy_worker.py:1158 |
| `StrategyWorker._execute_claude_trade` (entry path) | `qualify_survival_trade(symbol, _sc)` → reject low-quality during L2 | strategy_worker.py:1175 |
| `Strategist._build_prompt` (brain) | Pulls `get_coaching_text(structure_cache=_sc)` into Claude's prompt | strategist.py:564-568, :1549-1553 |
| `OrderService` | Does NOT consult enforcer — Layer 3 gate is the only OrderService-level gate | order_service.py:142-397 |


=====================================================================
## FILE: J2_pnl_manager.md
=====================================================================

# J2 — DailyPnLManager

Collection timestamp: 2026-05-02 ~11:45 UTC
DB snapshot: /tmp/trading_snapshot_1777722335.db
Logs searched: workers.2026-05-02_04-31-00_392071.log, workers.log

---

## 1. File location & size

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/pnl_manager.py`
- Lines of code: **449** (`wc -l`)
- Class: `DailyPnLManager` at pnl_manager.py:16

### Public methods

| Method | Signature | File:line |
|---|---|---|
| `__init__` | `(settings, account_service=None, position_service=None, db=None)` | pnl_manager.py:26 |
| `initialize` | `() -> None` (async) | pnl_manager.py:69 |
| `update` | `() -> None` (async) | pnl_manager.py:141 |
| `get_current_mode` | `() -> dict` | pnl_manager.py:204 |
| `can_trade` | `() -> tuple[bool, str]` | pnl_manager.py:284 |
| `pause_manually` | `(reason: str = "operator") -> None` | pnl_manager.py:293 |
| `resume_manually` | `() -> None` | pnl_manager.py:299 |
| `is_manually_paused` | `@property -> bool` | pnl_manager.py:306 |
| `apply_restrictions` | `(setups: list[EnsembleResult], mode: dict) -> list[EnsembleResult]` | pnl_manager.py:310 |
| `reset` | `() -> None` | pnl_manager.py:335 |
| `on_trade_closed` | `(pnl: float, symbol: str = "") -> None` (async) | pnl_manager.py:359 |
| `on_exchange_switch` | `() -> None` | pnl_manager.py:423 |
| `get_summary` | `() -> dict` | pnl_manager.py:435 |

Private helpers: `_persist_daily_pnl` (102), `_check_new_day` (168), `_recalculate` (195).

---

## 2. can_trade() gate

Logic (pnl_manager.py:284-291):
```
def can_trade(self) -> tuple[bool, str]:
    """Quick check if trading is allowed."""
    if self._manual_pause:
        return False, f"manual pause: {self._manual_pause_reason or 'operator halt'}"
    mode = self.get_current_mode()
    if mode["mode"] == "HALTED":
        return False, mode["message"]
    return True, ""
```

Inputs consumed:
- **`self._manual_pause`** (pnl_manager.py:43) — set to True by `pause_manually(reason)` from Telegram `/pause` (pnl_manager.py:293-297). Cleared by `resume_manually()` (pnl_manager.py:299-304) and by `reset()` (pnl_manager.py:351-352).
- **`self.current_pnl_pct`** (pnl_manager.py:36) — read by `get_current_mode()` (pnl_manager.py:207). Set by `_recalculate()` (pnl_manager.py:195-202) as `(realized_pnl + unrealized_pnl) / starting_equity * 100`.
- The mode dict from `get_current_mode()` — only "HALTED" causes the gate to refuse.

`can_trade()` does **not** look at `_losses_today`, `_streak_count`, or `_max_drawdown_today` directly. They influence `_recalculate()` via realized_pnl only via `on_trade_closed()`.

Caller observability — `STRAT_PNL_GATE` line at strategy_worker.py:124-129:
```
log.info(
    f"STRAT_PNL_GATE | halted={'Y' if not can_trade else 'N'} "
    f"rsn={reason or 'ok'} pnl_pct={_gate_pnl:+.2f} "
    f"wins={_gate_wins} losses={_gate_losses} "
    f"el={_section_ms['gate']:.0f}ms | {ctx()}"
)
```
Sample (verbatim from logs):
```
2026-05-02 11:26:30.003 | INFO | src.workers.strategy_worker:tick:124 | STRAT_PNL_GATE | halted=N rsn=ok pnl_pct=+0.00 wins=0 losses=0 el=0ms | sid=s-1777721190003
2026-05-02 04:36:30.002 | INFO | src.workers.strategy_worker:tick:124 | STRAT_PNL_GATE | halted=N rsn=ok pnl_pct=+0.00 wins=5 losses=15 el=0ms | sid=s-1777696590002
```
26 STRAT_PNL_GATE lines in the 24h window; **all** show `halted=N rsn=ok` — gate has not triggered HALTED in the observed period.

---

## 3. Daily PnL tracking

### Where computed

- `_recalculate()` at pnl_manager.py:195-202:
  ```
  total_pnl = self.realized_pnl + self.unrealized_pnl
  self.current_pnl_usd = total_pnl
  if self.starting_equity > 0:
      self.current_pnl_pct = (total_pnl / self.starting_equity) * 100
  else:
      self.current_pnl_pct = 0.0
  ```

### DB queries vs in-memory

- **In-memory accumulation** for `realized_pnl`: `on_trade_closed(pnl, symbol)` at pnl_manager.py:362 does `self.realized_pnl += pnl`. Stats (`_trades_today`, `_wins_today`, `_losses_today`, `_streak_count`, `_streak_type`, `_avg_win_pct`, `_avg_loss_pct`, `_per_coin_stats`, `_total_win_pnl`, `_total_loss_pnl`) are all incremented in the same method (pnl_manager.py:363-399).
- **External fetch for unrealized PnL**: `update()` at pnl_manager.py:141-156 calls `await self.account_service.get_wallet_balance()` and pulls `account.unrealized_pnl`. The first call also captures `starting_equity = account.total_equity` if 0.
- **DB persistence (one-way write)**: `_persist_daily_pnl()` at pnl_manager.py:102-139 writes to `daily_pnl` via `INSERT OR REPLACE` keyed on `date`. Fields: `starting_equity`, `ending_equity`, `realized_pnl`, `total_trades`, `wins`, `losses`, `max_drawdown_pct`, `target_hit`, `halted`. Persists every 10 cycles (pnl_manager.py:163-166) and immediately on every trade close (pnl_manager.py:404-405) and on day rollover (pnl_manager.py:173).
- **No DB-side read of today's stats** — DailyPnLManager does not query `trade_thesis`. It maintains its own counters from `on_trade_closed` callbacks. (Contrast: PerformanceEnforcer queries `trade_thesis` directly — see J1.)

### Reconciliation with Bybit/Shadow

- Account fetch path (`update()`, pnl_manager.py:141-156) reads `account_service.get_wallet_balance()`. The actual exchange backing depends on whether `_client` is the Bybit live client or a Shadow stub — this is determined at WorkerManager wiring, not by DailyPnLManager.
- `on_exchange_switch()` (pnl_manager.py:423-433) is invoked via callback from `Transformer.register_switch_callback(...)` at workers/manager.py:1967-1969. It zeroes `starting_equity` so the next `update()` re-captures from the new exchange (no PnL carry-over).
- `realized_pnl` reconciliation: closed trades push their pnl-USD into `on_trade_closed()` from the `_callbacks_on_close` chain registered on TradeCoordinator (pnl_manager.py:359-405). The numbers are NOT cross-checked against `trade_thesis.actual_pnl_pct` or any exchange-side ledger inside the manager.

### DB snapshot reconciliation (today)

`SELECT * FROM daily_pnl ORDER BY date DESC LIMIT 5` (snapshot 11:45 UTC):
```
2026-05-02 | 0.0 | 6149.85 | -1.0025 | 29 | 5 | 24
2026-05-01 | 0.0 | 6185.09 | -0.1541 |  2 | 0 |  2
2026-04-30 | 0.0 | 6197.12 |  0.5928 |  9 | 4 |  5
2026-04-29 | 0.0 | 6206.25 |  0.0911 |  5 | 3 |  2
2026-04-28 | 0.0 | 6240.93 | -0.03   |  1 | 0 |  1
```
Live ENFORCER_STATE in J1 shows `trades=30 wins=5 losses=24 pnl=-1.00%`. The DailyPnLManager's `daily_pnl` row (29 trades / -1.00 USD realized) is **one trade behind** the enforcer's `trade_thesis`-sourced count (30 closed) at the snapshot time — consistent with `_persist_counter` only flushing every 10 cycles plus immediate-on-close, but slightly lagging if the most recent close hadn't yet propagated through the persist path or the `_persist_counter` had just rolled. starting_equity column shows 0.0 — `_persist_daily_pnl()` writes `current_equity` (line 119, sourced from a separate wallet fetch) into `ending_equity`, while `starting_equity` is never refreshed from runtime — likely persisted as 0 because the runtime field had not been initialized at persist time.

NOT FOUND — `PNL_DAILY` log lines in 24h window — searched: workers.2026-05-02_04-31-00_392071.log, workers.log via `grep "PNL_DAILY\|PNL_RESET\|PNL_LIMIT\|PNL_TRADE_ADD\|PNL_MANUAL"` → 0 hits. The `update()` method emits `PNL_DAILY` at pnl_manager.py:156 but the call-path (a worker tick that invokes `pnl_manager.update()`) is not firing in the observed window — only `STRAT_PNL_GATE` (which only consults `current_pnl_pct` and `_losses_today`/`_wins_today`) fires.

---

## 4. Loss circuit breaker

### Mode → restriction table (pnl_manager.py:204-282)

The "circuit breaker" is implemented as the seven-mode `get_current_mode()` ladder. Thresholds come from `settings.pnl_targets` (defaults at config/settings.py:1029-1033).

| Mode | Trigger | max_score_threshold | max_leverage | max_positions | allowed_risk | File:line |
|---|---|---|---|---|---|---|
| TARGET_HIT | `pct >= daily_target_pct` (default +5.0) | 90 | 2 | 1 | low | 209-219 |
| PROTECT | `pct >= protect_threshold_pct` (default +3.0) | 85 | 3 | 2 | low, medium | 220-230 |
| GOOD_DAY | `pct >= 1.0` | 55 | 5 | 3 | low, medium | 231-240 |
| NORMAL | `pct >= caution_threshold_pct` (default -1.0) | 50 | 5 | 10 | low, medium, high | 241-250 |
| CAUTION | `pct >= survival_threshold_pct` (default -3.0) | 80 | 3 | 3 | low, medium | 251-260 |
| SURVIVAL | `pct >= halt_threshold_pct` (default -5.0) | 80 | 3 | 2 | low | 261-271 |
| HALTED | `pct < halt_threshold_pct` | 100 | 0 | 0 | (none) | 272-282 |

Halt threshold field declaration: `_daily_loss_limit_pct` at pnl_manager.py:59-61 reads `getattr(settings.pnl_targets, "halt_threshold_pct", -5.0)`.

### Halt activation path

Two paths set `self.halted = True`:
1. `get_current_mode()` (pnl_manager.py:273): when `pct` falls below `halt_threshold_pct`, the HALTED branch returns and sets `self.halted = True`.
2. `on_trade_closed()` (pnl_manager.py:415-421): on each close, if `current_pnl_pct <= halt_threshold_pct` and not already halted, sets `self.halted = True` and emits `PNL_LIMIT | pnl_pct=… | limit=… | rsn=daily_loss_halt`.

Once HALTED is active, `can_trade()` returns `(False, mode["message"])` and the StrategyWorker exits its tick early at strategy_worker.py:130-132.

### Manual pause path

- `pause_manually(reason)` at pnl_manager.py:293-297 sets `_manual_pause=True` and emits `PNL_MANUAL_PAUSE | rsn='…'`.
- `resume_manually()` at pnl_manager.py:299-304 clears it and emits `PNL_MANUAL_RESUME | prev_rsn='…'`.
- `reset()` at pnl_manager.py:335-357 clears `_manual_pause`, halted, and PnL counters, emitting `PNL_MANUAL_RESET | prev_pnl=…% prev_mode=… | new_pnl=0.00% new_mode=NORMAL`.

NOT FOUND — `PNL_LIMIT`, `PNL_MANUAL_PAUSE`, `PNL_MANUAL_RESUME`, `PNL_MANUAL_RESET` events in 24h window — searched: workers.2026-05-02_04-31-00_392071.log, workers.log → 0 hits. No halt or manual-pause activity in the observed window. Live state is `halted=N rsn=ok` per STRAT_PNL_GATE.

### Consecutive-loss circuit breaker

NOT FOUND — explicit "consecutive losses" circuit breaker — searched: pnl_manager.py full file. The streak fields (`_streak_count`, `_streak_type` at pnl_manager.py:57-58) are tracked for telemetry but **not read by `can_trade()` or `get_current_mode()`**. The only loss-streak logic is in PerformanceEnforcer (`_streak_boost_threshold=-5`, performance_enforcer.py:69) which can lift enforcement level from 0→1 when streak ≤ -5 AND pnl < 0; that is enforcer-side, not pnl_manager-side.

---

## 5. apply_restrictions wiring

`apply_restrictions(setups, mode)` (pnl_manager.py:310-333) filters `EnsembleResult` setups by `mode["max_score_threshold"]` and `mode["allowed_coins"]`. It returns `[]` when `mode["mode"] == "HALTED"`.

Caller: `strategy_worker.py:681`:
```
filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)
```
The `mode` dict is fetched immediately above with `self.pnl_manager.get_current_mode()` (strategy_worker.py:680 area).


=====================================================================
## FILE: K1_trade_gate.md
=====================================================================

# K1 — TradeGate (Layer 3 Placement Gate in OrderService)

Collection timestamp: 2026-05-02 ~11:45 UTC

> **Important naming note.** The spec asks for "TradeGate" as a distinct file. There are TWO gate components in this codebase, both colloquially called "TradeGate". They are unrelated by class hierarchy and run at different points in the flow:
>
> 1. **`src/apex/gate.py`** — class `TradeGate` (apex/gate.py:29). 14-check parameter-shaping gate that NEVER blocks; only adjusts size/leverage/TP/SL. Runs between APEX optimizer and `strategy_worker._execute_claude_trade`. See I4 (apex_gate.md) for that gate's audit.
> 2. **`src/trading/services/order_service.py`** — class `OrderService`, method `_enforce_layer3_gate()` (order_service.py:199-397). The block-or-allow Layer 3 placement gate that emits `ORDER_BLOCKED`. This is the ORDER-side gate the brief is asking about (the brief mentions `ORDER_GATE_NO_LM`, per-symbol cooldowns, ORDER_BLOCKED → all OrderService surfaces).
>
> This document covers component (2). Component (1) is documented in I4.

---

## 1. File path & sizing

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/services/order_service.py`
- Lines of code: **~1380** (file extends past inspection window)
- Class: `OrderService` at order_service.py:91
- Gate-related public/private surfaces:
  - `attach_layer_manager(layer_manager: LayerManager)` — order_service.py:130
  - `_emit_order_blocked(...)` — order_service.py:142 (helper, not a gate proper)
  - `_enforce_layer3_gate(...)` — order_service.py:199 (the gate)
  - `place_order(symbol, side, order_type, qty, price=None, stop_loss=None, take_profit=None, leverage=None, *, purpose='other', layer_snapshot=None, force=False)` — order_service.py:399 (caller of the gate)

OrderService is wired into the service registry under key `"order_service"` (constructed in workers/manager.py during boot; LayerManager attached later via `attach_layer_manager()`).

---

## 2. Gate purpose: distinct from APEX gate?

**Yes — different concerns, different return semantics, different position in the call chain:**

| Aspect | APEX TradeGate (apex/gate.py) | OrderService Layer-3 Gate (_enforce_layer3_gate) |
|---|---|---|
| Class/method | `TradeGate.validate(trade)` | `OrderService._enforce_layer3_gate(...)` |
| Return | Mutated `trade` dict | None on pass; raises on reject |
| On rejection | (NEVER rejects, only mutates) | Raises `Layer3DisabledError` / `Layer3RaceError` / `Layer3BootNotReadyError` |
| Inputs read | `_settings.max_position_size_usd`, `max_leverage`, `position_service`, `fund_manager`, `trade_coordinator` (cooldown), `tias_repo` (conviction), `regime_detector`, `structure_cache`, `market_service` | `_layer_manager` (`is_layer_active(3)`), `layer_snapshot`, `purpose`, `force`, `_init_monotonic`, `_settings.layer_manager.lm_attach_deadline_sec` |
| Side effects | `log.info("GATE_ADJUST | ...")`, `log.info("GATE_TIMING | ...")`, mutates `trade["_gate_validation_ms"]`, `trade["_gate_adjustments"]` | `log.error("ORDER_REJECT_*")`, `log.error("ORDER_BLOCKED ...")`, `log.warning("ORDER_GATE_NO_LM ...")` |
| Sequence | After APEX optimizer, before `_execute_claude_trade` (layer_manager.py:1318-1323) | Inside `place_order()`, BEFORE `ORDER_START` log (order_service.py:498-506) — last gate before exchange RPC |

**Separation of concerns:** apex/gate.py adjusts trade parameters within hard caps but cannot stop a trade. OrderService's Layer-3 gate decides whether the placement may even proceed based on operator-controlled toggles and boot-state invariants. They run in series for entry-side trades.

---

## 3. Layer-active check & ORDER_GATE_NO_LM state

### Layer-manager check site

`_enforce_layer3_gate()` reads the layer state at order_service.py:325:
```
order_service.py:325
live_l3 = bool(lm.is_layer_active(3))
```
`lm` is `self._layer_manager`, the `LayerManager` injected via `attach_layer_manager()` (order_service.py:130-140). The check method itself is `LayerManager.is_layer_active(layer)` at layer_manager.py:1536-1537:
```
def is_layer_active(self, layer: int) -> bool:
    return self._layer_active.get(layer, False)
```

The semantic helper `LayerManager.can_execute_orders()` at layer_manager.py:1552-1558 wraps this for layer 3 (forward-compat with v2 5-layer scheme that would map to layer 4) — but the actual call from `_enforce_layer3_gate` uses the raw `is_layer_active(3)`.

### Snapshot race-check (Approach C)

`_enforce_layer3_gate()` also accepts an optional `LayerSnapshot` (order_service.py:329-363). For `purpose="layer3_entry"` only, if `snapshot.is_layer_active(3) != live_l3`, the placement is rejected with `Layer3RaceError` and emits `ORDER_REJECT_LAYER3_RACE` + `ORDER_BLOCKED reason=layer3_race`.

### Pre-attach (boot window) policy

When `self._layer_manager is None` (LayerManager not yet attached), order_service.py:241-323 implements a **purpose-aware boot policy**:

- **Path 4a — deadline exceeded** (order_service.py:250-278): if `time.monotonic() - self._init_monotonic > settings.layer_manager.lm_attach_deadline_sec`, ALL purposes fail-close → emits `ORDER_GATE_LM_DEADLINE_EXCEEDED` (line 252) AND `ORDER_BLOCKED reason=lm_deadline_exceeded` (line 256-267) AND raises `Layer3BootNotReadyError`.
- **Path 4b — gated purpose during boot window** (order_service.py:282-311): for `purpose in {"layer3_entry","telegram_manual","mcp_tool"}` (i.e. `_GATED_PURPOSES`, order_service.py:58), emits `ORDER_REJECT_LM_BOOT` + `ORDER_BLOCKED reason=lm_boot_not_ready` and raises `Layer3BootNotReadyError`.
- **Path 4c — Layer 4 management during boot window** (order_service.py:313-323): for `purpose in {"layer4_close","layer4_sl"}`, emits a single `ORDER_GATE_NO_LM | … action=allow_layer4_only` warn line and **allows** the placement. This is the only "fail-open" path, scoped strictly to Layer-4 management actions during the boot window.

### ORDER_GATE_NO_LM current state

Source emit (order_service.py:317-322):
```
log.warning(
    f"ORDER_GATE_NO_LM | link_id={order_link_id} sym={symbol} "
    f"purpose={purpose} reason=layer_manager_not_attached_yet "
    f"elapsed_s={elapsed_s:.1f} action=allow_layer4_only "
    f"| {ctx()}"
)
```

**Fail-open vs fail-close**: Layer 4 close/SL purposes are intentionally fail-OPEN during the boot window before deadline expiry; everything else (entry/operator surfaces) and ALL purposes after deadline are fail-CLOSE. This is documented in the docstring at order_service.py:226-235.

24h log scan (`grep ORDER_GATE_NO_LM`): **0 hits** in workers.2026-05-02_04-31-00_392071.log and workers.log. Translation: in the active run, LayerManager attached before any Layer-4 placement was attempted, so the fail-open path was not exercised. The deadline-exceeded path WAS hit four times (see K2) — those were `mcp_tool` purpose entries with `elapsed_s` ≈ 9848-12932 seconds (way past the 60 s deadline), so they were blocked under Path 4a, not Path 4c.

### ORDER_BLOCKED event format

Emit site `_emit_order_blocked()` (order_service.py:192-197):
```
log.error(
    f"ORDER_BLOCKED | link_id={order_link_id} sym={symbol} "
    f"side={side.value} purpose={purpose} reason={reason} "
    f"actor={_actor} "
    f"force={force}{extra_str} | {ctx()}"
)
```
Field semantics:
- `link_id` — Bybit `orderLinkId` (`ti-<24hex>`, generated once per `place_order`).
- `sym`, `side`, `purpose` — same fields the caller passed.
- `reason` — closed-set token: `layer3_off`, `layer3_race`, `lm_boot_not_ready`, `lm_deadline_exceeded`.
- `actor` — derived from reason (order_service.py:186-191): `layer3_auto` (for `layer3_off`/`layer3_race`), `system_auto` (for `lm_*`), `gate` (fallback).
- `force` — flag the caller passed (mostly False for entries).
- `extra` — sorted-key reason-specific fields (e.g. `deadline_s=60.0 elapsed_s=9848.2` for `lm_deadline_exceeded`).

---

## 4. Per-symbol cooldowns

The OrderService Layer-3 gate does NOT itself enforce per-symbol cooldowns. Cooldown enforcement happens at TWO upstream sites:

### 4a. APEX TradeGate (size halving, not blocking)

apex/gate.py:174-186 (Check 6):
```
coordinator = self._services.get("trade_coordinator")
if coordinator and hasattr(coordinator, "is_symbol_cooled_down"):
    if coordinator.is_symbol_cooled_down(symbol):
        size = float(trade.get("size_usd", 600) or 600)
        trade["size_usd"] = round(size * 0.5, 2)
        remaining = 0
        if hasattr(coordinator, "get_symbol_cooldown_remaining"):
            remaining = coordinator.get_symbol_cooldown_remaining(symbol)
        modifications.append(f"size_halved_cooldown_{remaining}s")
```

### 4b. TradeCoordinator (state owner)

trade_coordinator.py:116:
```
self._symbol_cooldowns: dict[str, float] = {}  # symbol -> expiry timestamp
```

trade_coordinator.py:544-552 — set on close:
```
# Set per-symbol cooldown based on close outcome
if was_win:
    cooldown_sec = 180  # 3 min after win
elif closed_by in ("hard_stop", "mode4_crash"):
    cooldown_sec = 900  # 15 min after hard stop / flash crash
else:
    cooldown_sec = 600  # 10 min after normal loss
self._symbol_cooldowns[symbol] = time.time() + cooldown_sec
log.info(f"COORD_CLOSE_END | sym={symbol} cooldown={cooldown_sec}s by={closed_by} ...")
```

**Brief mention "5/10/15 min tiers per memory" verification**: actual implemented tiers are **3 min (win) / 10 min (normal loss) / 15 min (hard stop or mode4 crash)** — NOT 5/10/15. The "5 min" tier from the brief's memory does not exist in current code (trade_coordinator.py:544-551 has only the three branches above).

`is_symbol_cooled_down(symbol)` at trade_coordinator.py:554-562 returns True if `expiry > time.time()`, else False (auto-deletes expired entries). `get_symbol_cooldown_remaining(symbol)` at 564-569 returns int seconds remaining or 0.

### Cooldown enforcement events from logs

`COORD_CLOSE_END | … cooldown=…` (set events) — sample (verbatim):
```
2026-05-02 04:51:39.674 | COORD_CLOSE_END | sym=AXSUSDT cooldown=600s by=mode4_p9 cbs_fired=14
2026-05-02 04:54:07.834 | COORD_CLOSE_END | sym=SANDUSDT cooldown=600s by=shadow_sl_tp cbs_fired=14
2026-05-02 05:06:49.199 | COORD_CLOSE_END | sym=RENDERUSDT cooldown=600s by=strategic_review: CLOSE...
2026-05-02 05:35:05.051 | COORD_CLOSE_END | sym=DOGEUSDT cooldown=600s by=time_decay_p_win_low
2026-05-02 05:35:14.822 | COORD_CLOSE_END | sym=AXSUSDT cooldown=600s by=mode4_p9
2026-05-02 05:58:36.533 | COORD_CLOSE_END | sym=DOGEUSDT cooldown=600s by=strategic_review...
2026-05-02 06:05:17.424 | COORD_CLOSE_END | sym=AXSUSDT cooldown=600s by=mode4_p9
```
All observed COORD_CLOSE_END entries in the 24h window show `cooldown=600s` (10 min normal-loss tier). No 180s (win) or 900s (hard-stop) tiers fired in the observed window.

`GATE_ADJUST | … size_halved_cooldown_…` (size-halving on re-entry attempt) — single sample:
```
2026-05-02 05:41:21.546 | INFO | src.apex.gate:validate:334 | GATE_ADJUST | sym=AXSUSDT changes=[conviction_cap=$246(w=0.5x), size_halved_cooldown_233s] | did=d-1777700319292
```
This shows AXSUSDT in cooldown with 233s remaining when APEX gate ran — the trade was not blocked, just size-halved.

NOT FOUND — per-symbol cooldown enforcement at OrderService layer — searched: order_service.py full file, grep "cooldown|cooled". OrderService does NOT consult `is_symbol_cooled_down()`. Cooldown enforcement is upstream, in apex/gate (size-halving) and trade_coordinator (state).

---

## 5. Concurrent position limits

### Per-coin (duplicate-position halving)

apex/gate.py:162-172 (Check 5):
```
existing = await pos_svc.get_position(symbol)
if existing and existing.size and existing.size > 0:
    size = float(trade.get("size_usd", 600) or 600)
    trade["size_usd"] = round(size * 0.5, 2)
    modifications.append("size_halved_existing_pos")
```
Halves size when there is an open position on the same symbol. Does not block.

### Total positions

apex/gate.py:108-121 (Check 3):
```
max_concurrent = 5
try:
    pos_svc = self._services.get("position_service")
    if pos_svc:
        positions = await pos_svc.get_positions()
        open_count = len(positions) if positions else 0
        if open_count >= max_concurrent:
            size = float(trade.get("size_usd", 600) or 600)
            reduced = round(size * 0.3, 2)
            trade["size_usd"] = reduced
            modifications.append(f"size_reduced_max_pos={open_count}")
```
Reduces size to 30% when ≥5 positions open. Hard-coded `max_concurrent = 5` (apex/gate.py:109). Does not block.

OrderService's `_enforce_layer3_gate` does NOT enforce any position-count limit. There is no `max_concurrent` block at the order service layer.

DailyPnLManager-driven mode also caps `max_positions` per `get_current_mode()` (e.g. NORMAL = 10, CAUTION = 3, SURVIVAL = 2, HALTED = 0). That cap is enforced in `apply_restrictions` at the strategy_worker tier (pnl_manager.py:310-333), not the order service.

PerformanceEnforcer's `get_max_positions_override()` (performance_enforcer.py:110-116) returns `_l1_max_pos=3` / `_l2_max_pos=2`. Consumed by strategy-worker layer for setup count throttling.

---

## 6. Risk gates: max position size, max leverage, max notional

### In `OrderService.place_order` itself (order_service.py:543-585)

```
max_pct = self._settings.risk.max_position_size_pct
max_usd = equity * (max_pct / 100)
if notional_value > max_usd:
    old_qty = qty
    qty = max_usd / notional_price
    qty = round_qty(qty, _instrument.qty_step)
    log.warning("POSITION SIZE CAPPED: ...")
```
- `max_position_size_pct` default **10.0%** of equity (settings.py:519).

```
# Per-trade max loss: 2% of equity
eff_lev = int(leverage) if leverage else 1
if stop_loss and float(stop_loss) > 0 and notional_price > 0:
    sl_dist = abs(notional_price - float(stop_loss))
    potential_loss = sl_dist * float(qty) * eff_lev
    max_loss = equity * 0.02
    if potential_loss > max_loss and sl_dist > 0 and eff_lev > 0:
        old_qty = qty
        qty = max_loss / (sl_dist * eff_lev)
        qty = round_qty(qty, _instrument.qty_step)
        log.warning("PER-TRADE RISK CAPPED: ...")
```
- Per-trade max loss hard-coded **2% of equity** (order_service.py:575).

### Pre-RPC validators (called from `place_order`)

- `_validate_symbol(symbol)` — order_service.py:515 (verifies symbol in SUPPORTED_SYMBOLS).
- `_validate_stop_loss(stop_loss)` — order_service.py:516 (mandatory SL).
- `_validate_leverage(leverage)` — order_service.py:517 (caps against `settings.risk.max_leverage`, default 3, settings.py:515).

### Upstream (APEX gate) caps

apex/gate.py:94-99 (Check 1):
```
max_size = self._settings.max_position_size_usd
current_size = float(trade.get("size_usd", 600) or 600)
if current_size > max_size:
    trade["size_usd"] = max_size
    modifications.append(f"size=${current_size:.0f}->${max_size:.0f}")
```
- `APEXSettings.max_position_size_usd = 1200.0` (settings.py:1399).

apex/gate.py:101-106 (Check 2):
```
max_lev = self._settings.max_leverage
current_lev = int(trade.get("leverage", 3) or 3)
if current_lev > max_lev:
    trade["leverage"] = max_lev
    modifications.append(f"lev={current_lev}->{max_lev}")
```
- `APEXSettings.max_leverage = 5` (settings.py:1400).

### Max notional

NOT FOUND — explicit "max notional" check — searched: order_service.py, apex/gate.py. Notional is bounded transitively via `qty × price ≤ max_position_size_pct × equity` (order_service.py:558-569) but there is no separately-named `max_notional_usd` constant.

---

## 7. Decision routing

### Pass path

After `_enforce_layer3_gate(...)` returns successfully (no exception), `place_order()` continues at order_service.py:509-514:
```
log.info(
    f"ORDER_START | link_id={order_link_id} sym={symbol} "
    f"side={side.value} type={order_type.value} qty={qty} "
    f"lev={leverage} sl={stop_loss} tp={take_profit} "
    f"purpose={purpose} | {ctx()}"
)
```
…then runs validators, qty/price rounding, leverage set, position-size cap, per-trade risk cap, and finally the Bybit `place_order` RPC via `_place_order_with_idempotent_retry()` (order_service.py:613-618). On success, emits `ORDER_OK` (order_service.py:638-642) and saves to the trading repository.

### Block path (5 gate exits)

Each gate-side reject emits two events: a reason-specific event + the unified `ORDER_BLOCKED`, then raises:

| Reject reason | Reason-specific event | Exception | order_service.py line |
|---|---|---|---|
| `lm_deadline_exceeded` | `ORDER_GATE_LM_DEADLINE_EXCEEDED` | `Layer3BootNotReadyError` | 250-278 |
| `lm_boot_not_ready` | `ORDER_REJECT_LM_BOOT` | `Layer3BootNotReadyError` | 282-311 |
| `layer3_race` | `ORDER_REJECT_LAYER3_RACE` | `Layer3RaceError` | 332-363 |
| `layer3_off` | `ORDER_REJECT_LAYER3_OFF` | `Layer3DisabledError` | 369-391 |
| `force=True override` (informational) | `ORDER_LAYER3_OFF_FORCED` | (proceeds — no raise) | 392-397 |

Block consumers (caller handling): the `Layer3*Error` exceptions propagate up to `_execute_claude_trade()` (strategy_worker), the brain cycle, or telegram/MCP handlers. Each caller is responsible for its own dropping/alerting — there is no centralized telegram alert from the gate itself. `ORDER_BLOCKED` is the audit-trail tag operators grep.

### Pre-RPC (post-gate) failure paths

After the gate passes but before RPC, the following raise without emitting `ORDER_BLOCKED` (these are validation failures, not gate blocks):
- `InvalidOrderError` — instrument validation (order_service.py:529-533) or missing price for limit orders (order_service.py:537-540).
- `RiskLimitExceededError` — caught upstream by `_validate_leverage`.

---

## 8. Live state observed (24 h window)

- `ORDER_BLOCKED`: 4 events (all `lm_deadline_exceeded`, see K2).
- `ORDER_GATE_LM_DEADLINE_EXCEEDED`: 4 events (each pairs with the above).
- `ORDER_GATE_NO_LM`, `ORDER_REJECT_LM_BOOT`, `ORDER_REJECT_LAYER3_RACE`, `ORDER_REJECT_LAYER3_OFF`, `ORDER_LAYER3_OFF_FORCED`: 0 events each.
- `GATE_ADJUST` (APEX gate): 10+ events with mods like `conviction_cap=$246(w=0.5x)`, `APEX_GUARDRAIL_TP_FLOOR(...)`, `size_halved_cooldown_233s`, `CONVICTION_SIZE_CAP(claude=$500,req=$800,cap=$750)`.
- `ORDER_ATTEMPT` / `ORDER_OK` / `ORDER_REJECT_*`: 4 total in 24h (all 4 are the rejected mcp_tool placements).


=====================================================================
## FILE: K2_order_block_audit.md
=====================================================================

# K2 — Order Block Audit Trail

Collection timestamp: 2026-05-02 ~11:45 UTC
Logs searched: workers.2026-05-02_04-31-00_392071.log (active log, 4.5 MB), workers.log (current symlink, 320 KB)
Search query: `grep -h "ORDER_BLOCKED\|ORDER_GATE_LM_DEADLINE_EXCEEDED\|ORDER_REJECT_\|ORDER_GATE_NO_LM" workers.2026-05-02_04-31-00_392071.log workers.log`

---

## 1. Event-by-event (last 24h)

The 24h window contains exactly **4 `ORDER_BLOCKED` events**. All four are paired with `ORDER_ATTEMPT` (preceded ≤ 4 ms before) and `ORDER_GATE_LM_DEADLINE_EXCEEDED` (same millisecond). Each entry below shows the full triple.

### Event 1 — INJUSDT (05:10:34 UTC)

```
ORDER_ATTEMPT       2026-05-02 05:10:34.126 | link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT side=Buy purpose=mcp_tool qty=133 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 05:10:34.129 | link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT purpose=mcp_tool elapsed_s=9848.2 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 05:10:34.130 | link_id=ti-fa1828f0cd5c41f2b479eac8 sym=INJUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=9848.2
```
- Timestamp: 2026-05-02 05:10:34 UTC
- Symbol/side/qty: INJUSDT / Buy / 133
- Block reason: `lm_deadline_exceeded` (LayerManager not attached after 60 s deadline; elapsed=9848.2 s)
- Caller (purpose): `mcp_tool`
- Actor: `system_auto`
- did=: NOT FOUND in event — `no_ctx` (the placement was made via mcp_tool path, no decision_id was attached to the log_context). The link_id is `ti-fa1828f0cd5c41f2b479eac8`.

### Event 2 — ONDOUSDT (05:10:35 UTC)

```
ORDER_ATTEMPT       2026-05-02 05:10:35.180 | link_id=ti-9e1c48df37024462a1d09bfb sym=ONDOUSDT side=Buy purpose=mcp_tool qty=1852 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 05:10:35.180 | link_id=ti-9e1c48df37024462a1d09bfb sym=ONDOUSDT purpose=mcp_tool elapsed_s=9849.3 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 05:10:35.181 | link_id=ti-9e1c48df37024462a1d09bfb sym=ONDOUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=9849.3
```
- Timestamp: 2026-05-02 05:10:35 UTC
- Symbol/side/qty: ONDOUSDT / Buy / 1852
- Block reason: `lm_deadline_exceeded` (elapsed=9849.3 s)
- Caller: `mcp_tool`
- Actor: `system_auto`
- did=: `no_ctx` (link_id `ti-9e1c48df37024462a1d09bfb`)

### Event 3 — AXSUSDT (06:01:57 UTC)

```
ORDER_ATTEMPT       2026-05-02 06:01:57.117 | link_id=ti-cb5a2864c10c489fb7328344 sym=AXSUSDT side=Buy purpose=mcp_tool qty=362 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 06:01:57.118 | link_id=ti-cb5a2864c10c489fb7328344 sym=AXSUSDT purpose=mcp_tool elapsed_s=12931.2 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 06:01:57.118 | link_id=ti-cb5a2864c10c489fb7328344 sym=AXSUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=12931.2
```
- Timestamp: 2026-05-02 06:01:57 UTC
- Symbol/side/qty: AXSUSDT / Buy / 362
- Block reason: `lm_deadline_exceeded` (elapsed=12931.2 s — over 3 h past deadline)
- Caller: `mcp_tool`
- Actor: `system_auto`
- did=: `no_ctx` (link_id `ti-cb5a2864c10c489fb7328344`)

### Event 4 — MANAUSDT (06:01:57 UTC)

```
ORDER_ATTEMPT       2026-05-02 06:01:57.933 | link_id=ti-294adae3e33c42eabe9432bf sym=MANAUSDT side=Buy purpose=mcp_tool qty=5556 force=False
ORDER_GATE_LM_DEADLINE_EXCEEDED  2026-05-02 06:01:57.933 | link_id=ti-294adae3e33c42eabe9432bf sym=MANAUSDT purpose=mcp_tool elapsed_s=12932.0 deadline_s=60.0 action=block
ORDER_BLOCKED       2026-05-02 06:01:57.933 | link_id=ti-294adae3e33c42eabe9432bf sym=MANAUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=12932.0
```
- Timestamp: 2026-05-02 06:01:57 UTC
- Symbol/side/qty: MANAUSDT / Buy / 5556
- Block reason: `lm_deadline_exceeded` (elapsed=12932.0 s)
- Caller: `mcp_tool`
- Actor: `system_auto`
- did=: `no_ctx` (link_id `ti-294adae3e33c42eabe9432bf`)

### Notional

NOT FOUND — `notional` field — searched: ORDER_BLOCKED format (order_service.py:192-197) and ORDER_ATTEMPT format (order_service.py:488-492). Neither event includes a notional or a price. The events carry only `qty`. To compute notional one would need to cross-reference the price at the timestamp.

---

## 2. Aggregate

### Top block reasons by count

| Rank | Reason | Count | % |
|---|---|---|---|
| 1 | `lm_deadline_exceeded` | **4** | 100% |
| – | `layer3_off` | 0 | 0% |
| – | `layer3_race` | 0 | 0% |
| – | `lm_boot_not_ready` | 0 | 0% |

(Only one reason occurred in the window. The other three closed-set reasons defined at order_service.py:186-191 are absent.)

### Distribution by caller (purpose)

| Purpose | Count | % |
|---|---|---|
| `mcp_tool` | **4** | 100% |
| `layer3_entry` | 0 | 0% |
| `telegram_manual` | 0 | 0% |
| `layer4_close` | 0 | 0% |
| `layer4_sl` | 0 | 0% |

### Distribution by actor (derived field, order_service.py:186-191)

| Actor | Count |
|---|---|
| `system_auto` | 4 |
| `layer3_auto` | 0 |
| `gate` | 0 |

### Distribution by hour (UTC)

| Hour bucket | Count |
|---|---|
| 04:00–05:00 | 0 |
| 05:00–06:00 | 2 (INJUSDT, ONDOUSDT — 05:10) |
| 06:00–07:00 | 2 (AXSUSDT, MANAUSDT — 06:01) |
| 07:00–11:48 | 0 |

### Side distribution

| Side | Count |
|---|---|
| Buy | 4 |
| Sell | 0 |

### Symbols

| Symbol | Count |
|---|---|
| INJUSDT | 1 |
| ONDOUSDT | 1 |
| AXSUSDT | 1 |
| MANAUSDT | 1 |

(Each symbol unique — no repeated rejections of the same coin.)

### Force flag

All 4 events: `force=False`. No `force=True` overrides were attempted.

---

## 3. Provenance / interpretation

All 4 events come from `_emit_order_blocked` at order_service.py:192. The companion `ORDER_GATE_LM_DEADLINE_EXCEEDED` at order_service.py:251 is emitted from `_enforce_layer3_gate` immediately before. The reject path is order_service.py:250-278 (Path 4a — deadline exceeded → fail-close ALL purposes).

`elapsed_s` values (9848.2, 9849.3, 12931.2, 12932.0) are far past the 60 s `lm_attach_deadline_sec`. This implies the OrderService instance handling the mcp_tool calls was **not** the same OrderService that received the LayerManager attachment (or LayerManager failed to attach to it at all). The `_init_monotonic` clock in those calls had been ticking for ~2.7–3.6 hours.

OrderService construction site (workers/manager.py during boot) is the same instance that `attach_layer_manager()` is called against — but the four `mcp_tool` placements were initiated from the MCP server (separate process) which constructs its own OrderService. The MCP-side OrderService never had its LayerManager attached → after the 60 s boot deadline, every placement attempt fails-close with `lm_deadline_exceeded`.

---

## 4. Other gate events in 24h (for context)

Outside the `ORDER_BLOCKED` cohort, the 24h window contains:
- **0** `ORDER_GATE_NO_LM` (Layer-4 fail-open warns — would have been logged for any layer4_close/layer4_sl pre-attach call). Implies LM attached cleanly on the worker-process OrderService before any L4 call.
- **0** `ORDER_REJECT_LAYER3_OFF`, `ORDER_REJECT_LAYER3_RACE`, `ORDER_REJECT_LM_BOOT`, `ORDER_LAYER3_OFF_FORCED`.
- **0** `ORDER_OK` events — no successful placements in the 24h window via OrderService. (Trade activity in the period was via Shadow paths, not Bybit live OrderService.)
- **10+** `GATE_ADJUST` events from the upstream APEX TradeGate (apex/gate.py:334) — these adjust trades but do not block. Not included in the order-block audit.

---

## 5. Gaps

- did= field: NOT FOUND on any of the 4 ORDER_BLOCKED rows — all show `no_ctx` because the calls came from `mcp_tool` purpose, which constructs no `did=` log_context. The `link_id` field is the per-call audit key.
- notional / price: NOT FOUND — emit format does not include them.
- Cross-day visibility: this run started 04:31 UTC on 2026-05-02. Earlier rotated logs (workers.2026-05-01_*.log) were not searched per the brief's "last 24h" window scope; if needed, expand to those files for a strict 24-hour rolling window from 11:45 UTC on 2026-05-01.


=====================================================================
## FILE: L1_order_service.md
=====================================================================

# L1 — OrderService Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/services/order_service.py`
Measured line count: **1156** lines (truth doc said ~620; measurement larger).

---

## 1. Class & Public Method Signatures

Class: `OrderService` (file:91)

Constructor (file:102-128):
```
def __init__(self, client: BybitClient, db: DatabaseManager, settings: Settings) -> None
```
Stored attrs: `_client`, `_db`, `_settings`, `_trading_repo` (TradingRepository), `_instrument_svc` (InstrumentService), `_layer_manager` (None until attach), `_init_monotonic` (boot deadline tracker).

Public methods:
| Method | File:Line | Signature (abridged) |
|---|---|---|
| `attach_layer_manager` | 130 | `(self, layer_manager: LayerManager) -> None` |
| `place_order` | 400 | see section 2 |
| `modify_order` | 855 | `(self, symbol, order_id, qty=None, price=None) -> Order` (decorated `@retry(max_attempts=2, delay=0.5, exceptions=(BybitAPIError,OSError,RuntimeError))`, line 850) |
| `cancel_order` | 911 | `(self, symbol, order_id) -> bool` (`@retry(max_attempts=2, delay=0.5)`, line 909) |
| `cancel_all_orders` | 940 | `(self, symbol=None) -> int` (`@retry(max_attempts=2, delay=0.5)`, line 938) |
| `get_open_orders` | 967 | `(self, symbol=None) -> list[Order]` (`@retry(max_attempts=3, delay=1.0)`, line 965) |
| `get_order_history` | 992 | `(self, symbol=None, limit=50) -> list[Order]` (`@retry(max_attempts=3, delay=1.0)`, line 990) |

Private helpers: `_emit_order_blocked` (142), `_enforce_layer3_gate` (199), `_place_order_with_idempotent_retry` (678), `_recover_order_by_link_id` (773), `_validate_symbol` (1022), `_validate_stop_loss` (1030), `_validate_leverage` (1038), `_set_leverage` (1049), `_get_order_from_exchange` (1067).

Module-level helpers: `_new_order_link_id` (79), `_parse_order` (1098), `_map_order_type` (1123), `_map_order_status` (1135), `_parse_optional_float` (1149).

---

## 2. `place_order` Full Signature

File:399-414, decorated `@timed` only (no `@retry` — comment file:60-72 explains the retry was deliberately narrowed to the inner RPC after duplicate-order incidents).

```python
@timed
async def place_order(
    self,
    symbol: str,
    side: Side,
    order_type: OrderType,
    qty: float,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    leverage: int | None = None,
    *,
    purpose: str = "other",
    layer_snapshot: "LayerSnapshot | None" = None,
    force: bool = False,
) -> Order
```

Closed-set kwargs (file:49-58):
- `_VALID_PURPOSES` = `{layer3_entry, layer4_close, layer4_sl, telegram_manual, mcp_tool, test, other}` — ValueError on misspelling at line 473-477.
- `_GATED_PURPOSES` = `{layer3_entry, telegram_manual, mcp_tool}` — gated at line 498.

---

## 3. Callers of `place_order`

| Caller File | Line | Argument pattern |
|---|---|---|
| `src/workers/strategy_worker.py` | 1521 | `purpose="layer3_entry", layer_snapshot=_layer_snapshot` (full kw set + qty/sl/tp/lev) |
| `src/brain/brain_v2.py` | 487 | `purpose="layer3_entry"` (no `layer_snapshot`, no `force`) |
| `src/telegram/bot.py` | 691 | `purpose="telegram_manual"` (qty, leverage, sl, tp) |
| `src/telegram/handlers/trading.py` | 88 | `purpose="telegram_manual"` |
| `src/mcp/tools/trading_tools.py` | 170 | `purpose="mcp_tool"` (price, sl, tp, leverage) |
| `src/core/transformer.py` | 958 | proxy `*args, **kwargs` to `active_order_service.place_order` |
| `src/brain/executor.py.deprecated` | 112 | (deprecated path, file extension `.deprecated`) |
| `src/core/layer_manager.py` | 1666 | docstring reference only |
| `src/core/trade_recorder.py` | 71 | docstring reference only |

`src/workers/strategy_worker.py:1521-1531`:
```python
order = await order_svc.place_order(
    symbol=symbol,
    side=side_enum,
    order_type=OrderType.MARKET,
    qty=qty,
    stop_loss=sl,
    take_profit=tp,
    leverage=leverage,
    purpose="layer3_entry",
    layer_snapshot=_layer_snapshot,
)
```

`src/brain/brain_v2.py:487-496`:
```python
order = await self.order_service.place_order(
    symbol=sig.symbol,
    side=sig.direction,
    order_type=OrderType.MARKET,
    qty=qty,
    stop_loss=decision.stop_loss,
    take_profit=decision.take_profit_1,
    leverage=decision.leverage,
    purpose="layer3_entry",
)
```

---

## 4. Pre-order Validation

Validation order in `place_order`:

1. Purpose closed-set check (file:473-477): `ValueError` on bad purpose.
2. Idempotency key generation (file:481): `_new_order_link_id()` produces `ti-<24-hex>` once per call.
3. `ORDER_ATTEMPT` audit log (file:488-492).
4. Layer 3 gate for gated purposes (file:498-506) — see section 6.
5. `ORDER_START` audit log (file:509-514).
6. `_validate_symbol` (file:515 -> 1022-1028) — `InvalidOrderError` if symbol not in `SUPPORTED_SYMBOLS`.
7. `_validate_stop_loss` (file:516 -> 1030-1036) — `InvalidOrderError("Stop-loss is mandatory…")` if `settings.risk.mandatory_stop_loss` and `stop_loss is None`. Default `mandatory_stop_loss=True` (config/settings.py:516).
8. `_validate_leverage` (file:517 -> 1038-1047) — `RiskLimitExceededError` if `leverage > settings.risk.max_leverage` (default `max_leverage=3`, settings.py:515).
9. Instrument lookup (file:520) — `InstrumentService.get_instrument_info` fetches `qty_step` and `price_tick`.
10. Round qty/price (file:523-525) via `round_qty` / `round_price`.
11. `InstrumentService.validate_order_params` (file:528-533) — concatenates issues into `InvalidOrderError`.
12. LIMIT-without-price check (file:536-540) — `InvalidOrderError`.
13. `_set_leverage` (file:543-544 -> 1049-1065) — RPC `set_leverage`. "leverage not modified" / `110043` is swallowed as success (file:1062-1063).
14. HARD POSITION SIZE CAP ("FIX 2", file:546-585): reads wallet via `AccountService.get_wallet_balance()`, computes `notional_value = qty * price`, caps to `equity * max_position_size_pct/100` (default `max_position_size_pct=10.0`, settings.py:519). Then computes per-trade-loss cap of `2% of equity` using `(stop_loss_distance * qty * leverage)`. Note: this whole block is wrapped in a bare `try/except: log.warning("Position size cap check failed: ...")` (file:584-585) so any failure here silently lets the order through with un-capped size.

### `ORDER_PREFLIGHT_INSUFFICIENT`

GAP — searched: `grep -rn "ORDER_PREFLIGHT_INSUFFICIENT|preflight" src/` returned **0 matches** in production code. There is no early-abort log tag matching `ORDER_PREFLIGHT_INSUFFICIENT`. Insufficient-balance is detected at exchange level via Bybit retCodes 110012/110043/110044 -> `InsufficientBalanceError` (client.py:58-59) raised from `_handle_response`.

---

## 5. Order Placement to Bybit

URL/transport: there is no direct HTTP — `BybitClient.call("place_order", **kwargs)` calls `pybit.unified_trading.HTTP.place_order` via `asyncio.to_thread` (client.py:190). pybit uses Bybit's mainnet/testnet REST endpoints internally based on `bybit.testnet` flag (client.py:124-129).

Order params built at order_service.py:590-604:
```python
order_params: dict = {
    "category": "linear",
    "symbol": symbol,
    "side": side.value,
    "orderType": order_type.value,
    "qty": str(qty),
    "orderLinkId": order_link_id,
}
if price is not None: order_params["price"] = str(price)
if stop_loss is not None: order_params["stopLoss"] = str(stop_loss)
if take_profit is not None: order_params["takeProfit"] = str(take_profit)
```

Auth: `BybitAuth(api_key, api_secret)` set up at `BybitClient.connect()` (client.py:122). pybit signs requests via `recv_window` from `bybit.recv_window` (client.py:128).

Rate limit: `@rate_limit(calls_per_second=10.0)` at `BybitClient.call` (client.py:161). See L2.

Retry policy: place_order has its own scoped retry — `_place_order_with_idempotent_retry` (file:678-771). At-most-one transient retry of the inner RPC; `_ORDER_PLACE_MAX_ATTEMPTS = 2` (file:76); `_ORDER_PLACE_RETRY_DELAY_S = 0.5` (file:75). Bybit-mapped errors (`InvalidOrderError, RateLimitError, OrderRejectedError, BybitAPIError`) re-raise immediately at file:740-752; non-Bybit exceptions retry once at file:753-766.

Outer retry on `BybitClient.call` itself: `@retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,))` — client.py:160.

### Routing to Shadow vs Bybit

OrderService is the LIVE-trading service. Routing occurs at the `Transformer` level:
- `src/core/transformer.py:958` — proxy: `return await self._t.active_order_service.place_order(*args, **kwargs)`.
- `src/workers/manager.py:289-298` — both `BybitClient` and `ShadowOrderService` are constructed; `transformer.set_services(...)` feeds both; `transformer.initialize()` reads DB mode and selects `active_order_service`.

When `general.mode == "shadow"`, the proxy resolves to `ShadowOrderService` (see L3); when `mode == "live"`, to the live `OrderService`. There is no per-call branch inside `OrderService` itself.

---

## 6. Order ID Generation, Idempotency, RC_DUPLICATE_ORDER_LINK_ID = 110072

`_new_order_link_id` (file:79-86):
```python
_ORDER_LINK_ID_PREFIX = "ti"
_ORDER_LINK_ID_LEN = 24
def _new_order_link_id() -> str:
    return f"{_ORDER_LINK_ID_PREFIX}-{uuid.uuid4().hex[:_ORDER_LINK_ID_LEN]}"
```
Format: `ti-<24-hex>` = 27 chars (Bybit V5 limit 36).

110072 mapping: `client.py:62 -> DuplicateOrderLinkIdError`. Constant defined at `client.py:35: RC_DUPLICATE_ORDER_LINK_ID = 110072`.

Handling in OrderService (file:730-739):
```python
except DuplicateOrderLinkIdError:
    log.warning(f"ORDER_DEDUPED | link_id={...} | ...")
    return await self._recover_order_by_link_id(order_link_id=order_link_id, symbol=symbol)
```

Recovery sequence (`_recover_order_by_link_id` file:773-839):
1. Try `get_open_orders(category="linear", symbol=symbol, orderLinkId=order_link_id)` (file:790-802); if found, `ORDER_RECOVERED | src=open` log and return `{orderId, orderLinkId}`.
2. Else try `get_order_history(...)` (file:811-825); `ORDER_RECOVERED | src=history`.
3. Else synthesize `{"orderId": f"DEDUP-{order_link_id}"}` and emit `ORDER_RECOVERY_SYNTH` (file:835-839).

---

## 7. Position Tracking After Fill

After a successful place_order:

- `Order` object built at file:622-634 with `OrderStatus.NEW`.
- Persisted via `await self._trading_repo.save_order(order)` (file:636).
- `ORDER_OK` logged (file:638-642).
- FIX 3 — VERIFY STOP-LOSS ON EXCHANGE (file:654-674): sleeps 1.5s, instantiates `PositionService` and calls `get_position(symbol)`. If position has no SL or `stop_loss == 0`, logs `SL NOT on exchange` and calls `set_stop_loss`. Re-checks after 0.5s, logs `SL VERIFIED` or `SL FAILED TO SET`. Wrapped in `try/except`.

Cache vs DB:
- DB write: `TradingRepository.save_order` writes to `orders` table.
- No in-memory cache update by `OrderService` itself — position cache is owned by `PositionService` (separate file). Position reconciliation comes from periodic `PositionService.get_positions` polling and FIX 3's explicit re-fetch.

---

## 8. Failure Modes

Bybit retCode -> exception map (`client.py:51-63`):
| retCode | Exception | Note |
|---|---|---|
| 10003 | `AuthenticationError` | Invalid API key |
| 10004 | `AuthenticationError` | Invalid signature |
| 10006 | `RateLimitError` | Rate limited (`RC_RATE_LIMIT`) |
| 110001 | `InvalidOrderError` | Order not found |
| 110003 | `InvalidOrderError` | Quantity not valid |
| 110007 | `PositionError` | Position not exists |
| 110012 | `InsufficientBalanceError` | Insufficient balance for order |
| 110043 | `InsufficientBalanceError` | Insufficient available balance |
| 110044 | `InvalidOrderError` | Insufficient balance after SL (mapped to `InvalidOrderError`, NOT `InsufficientBalanceError`) |
| 110045 | `InvalidOrderError` | Leverage not modified |
| 110072 | `DuplicateOrderLinkIdError` | Idempotency hit |

Comment at client.py:39-50 documents that 10001 (parameter error) deliberately falls through to `BybitAPIError` because the pre-2026-04 mapping (10001 -> `InsufficientBalanceError`) was wrong.

### Counts in last 24h (2026-05-01 to 2026-05-02 from `data/logs/workers.*.log`)

| Pattern | Count | Notes |
|---|---|---|
| `ORDER_FAIL\|ORDER_REJECT_\|ORDER_BLOCKED` | 5 | All 5 are `ORDER_BLOCKED reason=lm_deadline_exceeded actor=system_auto` for purpose=mcp_tool |
| `10006 \| RateLimitError` | 1 false-positive | Single match is a `WORKER_LIVENESS_HEARTBEAT` line happening to contain "10006" — no actual rate-limit event |
| `110012 \| 110043 \| 110044 \| InsufficientBalance` | 0 | None — system is in shadow mode (paper trades route through Shadow, see L3) |
| `10003 \| 10004 \| InvalidAPIKey \| InvalidSign` | 0 | None |
| `110007` | 0 | None |
| `110072 \| ORDER_DEDUPED \| DuplicateOrderLinkId` | 0 | None |
| `ORDER_RETRY` | 0 | None |

5 verbatim `ORDER_BLOCKED` events (last 24h):
```
2026-05-01 00:27:19.777 ORDER_BLOCKED | link_id=ti-60665526ec054cc5b4c1282f sym=OPUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=1045.1
2026-05-01 00:27:20.556 ORDER_BLOCKED | link_id=ti-f7619483a44b4031ac05c12e sym=AEROUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=1045.9
2026-05-01 00:48:44.283 ORDER_BLOCKED | link_id=ti-9bdd2a8b5d0a4835993cd58c sym=AEROUSDT side=Sell purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=2329.6
2026-05-01 01:02:34.509 ORDER_BLOCKED | link_id=ti-831e5d767be5436e82214a22 sym=AXSUSDT side=Sell purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=3159.8
2026-05-01 01:02:35.460 ORDER_BLOCKED | link_id=ti-a100e0bffef3450f80f8f0e7 sym=AEROUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=3160.8
```

All 5 have `elapsed_s` between 1045 and 3161 seconds — well past the 60s LM-attach deadline. The reason path is `_enforce_layer3_gate` Path 4a (file:248-278): when `lm is None and elapsed > deadline`, fail-close ALL purposes including layer4. These came from MCP tools targeting symbols not in any active workers session, confirming LayerManager attachment failed in those processes.

---

## 9. Order Audit (DB)

`orders` table schema (`/tmp/trading_snapshot_1777722335.db`):
```sql
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    qty REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'New',
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_orders_symbol_status ON orders(symbol, status);
```

Sample rows: GAP — `SELECT COUNT(*) FROM orders` returned **0** in the snapshot. The system is in shadow mode and paper trades are NOT persisted to this `orders` table; live `OrderService.place_order` -> `TradingRepository.save_order` is the only writer.

`order_history` table: GAP — does not exist in the snapshot DB. Order history is fetched ad-hoc from Bybit via `OrderService.get_order_history` (file:992-1018) and saved into the `orders` table (file:1015).

`trade_history` table also empty: `SELECT COUNT(*) FROM trade_history` returned **0** (schema present). Confirms no live trades persisted recently.

Shadow/paper trade audit lives in Shadow's own SQLite — see L3.


=====================================================================
## FILE: L2_bybit_client.md
=====================================================================

# L2 — Bybit Client Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/client.py`
Measured line count: **227** lines.

WebSocket source: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/websocket.py`

---

## 1. Class & Public Methods

Class: `BybitClient` (client.py:66)

Constructor (client.py:74-88):
```
def __init__(self, settings: Settings, db: DatabaseManager) -> None
```
Stored attrs: `_settings`, `_db`, `_session: HTTP | None`, `_auth: BybitAuth | None`, `_connected: bool`.

Safety assertion (client.py:84-88): if `not settings.bybit.testnet` AND `settings.general.mode == "paper"`, raise `RuntimeError("SAFETY: bybit.testnet is False but mode is 'paper'...")`. Mainnet data is allowed when mode is `shadow` or `live`.

Public methods/properties:
| Member | File:Line | Notes |
|---|---|---|
| `session` (property) | 90-99 | Returns `_session`, raises `RuntimeError` if not connected |
| `is_testnet` (property) | 101-104 | `_settings.bybit.testnet` |
| `is_connected` (property) | 106-109 | `_connected` |
| `connect` | 111-152 | Build pybit `HTTP` session, validate creds (non-fatal in shadow mode) |
| `disconnect` | 154-158 | Clears session |
| `call` | 163-191 | Central RPC dispatcher, decorated `@retry @rate_limit @timed` |

Private: `_handle_response` (193-227) maps retCode -> exception.

---

## 2. pybit Method Mapping

`BybitClient.call(method, **kwargs)` resolves method by name on the pybit `HTTP` session (`func = getattr(session, method, None)`, client.py:183) and dispatches via `asyncio.to_thread(func, **kwargs)` (client.py:190).

All pybit methods used by the codebase, with caller file:line and purpose:

| pybit method | Caller (file:line) | Purpose |
|---|---|---|
| `place_order` | order_service.py:723 | Place new order |
| `amend_order` | order_service.py:895 | Modify open order qty/price |
| `cancel_order` | order_service.py:921-925 | Cancel single order |
| `cancel_all_orders` | order_service.py:953 | Cancel all (optional symbol filter) |
| `get_open_orders` | order_service.py:790, 980, 1069 | Open orders / dedup recovery / single-order lookup |
| `get_order_history` | order_service.py:812, 1010, 1078 | Filled / cancelled history; dedup recovery; single-order lookup |
| `set_leverage` | order_service.py:1052-1057 | Set buy/sell leverage |
| `get_tickers` | order_service.py:556 | Last-price lookup for position-cap calc |
| `get_wallet_balance` | account_service.py (5 sites; via `BybitClient.call`) | Wallet/equity reads |
| `get_positions` | position_service.py | Position queries |
| `get_kline` | market_service.py | OHLC fetch |
| `get_instruments_info` | instrument_service.py | Symbol metadata |

(WebSocket streams handled separately, see section 4.)

Retry policy (single source): `@retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,))` decorates `BybitClient.call` (client.py:160). This applies UNIVERSALLY to every method dispatched through `call`.

Per-call additional retries are layered ON TOP at the service level (see L1 section 5 for the place_order scoped retry).

---

## 3. Rate Limiter (Token Bucket)

Decorator declaration: `@rate_limit(calls_per_second=10.0)` at `BybitClient.call` (client.py:161).

Settings reference: `rate_limit_per_second: int = 10` defined at `src/config/settings.py:54` (in the `BybitConfig` dataclass), but the decorator hardcodes `10.0` — the settings field is NOT wired into the decorator argument. GAP — the value is duplicated rather than read from settings.

Implementation: `src/core/decorators.py:108-167`.

`_TokenBucket` class (decorators.py:106-133):
```python
class _TokenBucket:
    def __init__(self, calls_per_second: float) -> None:
        self.rate = calls_per_second
        self.max_tokens = calls_per_second
        self.tokens = calls_per_second
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1.0
```

Bucket cache (decorators.py:136): `_buckets: dict[str, _TokenBucket]` keyed on `f"{func.__module__}.{func.__qualname__}:{calls_per_second}"`. Each decorated function gets one shared bucket process-wide.

Capacity: `max_tokens = calls_per_second = 10.0`, refill rate 10 tokens/sec. 11th call within a single second waits `(1.0 - tokens) / rate` seconds.

### Rate-limit hits in last 24h

GAP — searched logs `data/logs/workers.*.log` for `10006|RC_RATE_LIMIT|RateLimitError` between 2026-05-01 and 2026-05-02. The only match is a heartbeat line containing "10006" as part of a worker ID, NOT an actual rate-limit event. **0 actual `RateLimitError` events found in the last 24h.**

The token bucket is process-local; rate limiting is enforced client-side BEFORE the request reaches Bybit. No log line is emitted by the bucket itself — only `loguru.debug` from `@retry` if a `BybitAPIError` happens to bubble.

---

## 4. WebSocket

File: `src/trading/websocket.py`. Class: `BybitWebSocket` (websocket.py:17).

Constructor (websocket.py:29-39):
- `_public_ws`, `_private_ws` — `pybit.unified_trading.WebSocket` instances
- `_callbacks: dict[str, list[Callable]]`
- `_running: bool`, `_reconnect_attempts: int = 0`, `_max_reconnect_attempts: int = 10`
- `_lock: asyncio.Lock`

Subscriptions (all via pybit's WebSocket class):
| Method | File:Line | pybit call | Channel |
|---|---|---|---|
| `subscribe_ticker` | 88 | `_public_ws.ticker_stream(symbol, callback)` | linear public |
| `subscribe_kline` | 104 | `_public_ws.kline_stream(interval, symbol, callback)` | linear public |
| `subscribe_orderbook` | 121 | `_public_ws.orderbook_stream(depth, symbol, callback)` | linear public |
| `subscribe_orders` | 138 | `_private_ws.order_stream(callback)` | private |
| `subscribe_positions` | 151 | `_private_ws.position_stream(callback)` | private |

Connection setup:
- `connect_public` (websocket.py:46-63): `WebSocket(testnet=settings.bybit.testnet, channel_type="linear")` (websocket.py:54-57).
- `connect_private` (websocket.py:65-86): `WebSocket(testnet=, channel_type="private", api_key=, api_secret=)` (websocket.py:74-79).

Reconnect (`reconnect`, websocket.py:183-216):
```python
base_delay = self._settings.bybit.ws_reconnect_delay
while self._reconnect_attempts < self._max_reconnect_attempts:
    self._reconnect_attempts += 1
    delay = base_delay * (2 ** (self._reconnect_attempts - 1))
    delay = min(delay, 300)  # cap at 5 minutes
    log.warning("WebSocket reconnect attempt {n}/{max} in {d}s", ...)
    await asyncio.sleep(delay)
    try:
        await self.disconnect()
        await self.connect_public()
        self._reconnect_attempts = 0
        return
    except Exception as e:
        log.error("Reconnect attempt failed: {err}", err=str(e))
raise MarketDataError(f"WebSocket reconnection failed after {self._max_reconnect_attempts} attempts")
```

Heartbeat: GAP — there is no explicit `heartbeat` / `ping` method in `BybitWebSocket`. pybit's `WebSocket` class manages its own ping internally; this codebase has no override. Searched: `grep -n "heartbeat\|ping" src/trading/websocket.py`.

Callback wrapping: `_wrap_callback(stream_type, callback)` (websocket.py:218+) wraps user callbacks with try/except logging.

---

## 5. `@retry` Decorator on Methods

Definition: `src/core/decorators.py:17-99`.

```python
def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0,
          exceptions: tuple[type[BaseException], ...] = (Exception,)) -> ...
```

Async branch (decorators.py:36-68):
- For each attempt 1..max_attempts: `await func(...)`; on caught `exceptions`, if last attempt -> log "Retry exhausted for {func} after {n} attempts" at WARNING and re-raise; else log "Retry {attempt}/{max} for {func}" at DEBUG, `await asyncio.sleep(current_delay)`, `current_delay *= backoff`.

Decorator usage in `src/trading/`:
| File:Line | Decorated method | Args |
|---|---|---|
| client.py:160 | `BybitClient.call` | `max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,)` |
| order_service.py:850 | `OrderService.modify_order` | `max_attempts=2, delay=0.5, exceptions=(BybitAPIError, OSError, RuntimeError)` |
| order_service.py:909 | `OrderService.cancel_order` | `max_attempts=2, delay=0.5` (default exceptions=Exception) |
| order_service.py:938 | `OrderService.cancel_all_orders` | `max_attempts=2, delay=0.5` |
| order_service.py:965 | `OrderService.get_open_orders` | `max_attempts=3, delay=1.0` |
| order_service.py:990 | `OrderService.get_order_history` | `max_attempts=3, delay=1.0` |
| account_service.py:27,76,87,98 | account methods | `max_attempts=3, delay=1.0` |
| position_service.py:52,82,98,225,345,383,405 | position methods | mixes of 2/0.5 and 3/1.0 |
| market_service.py:70,155,175,231,270,300 | market methods | `max_attempts=3, delay=1.0` |
| instrument_service.py:38,80 | instrument methods | 3/1.0 and 2/2.0 |

The exact decorator that matches the prompt's `@retry(max_attempts=3, delay=1.0, backoff=2.0)` is **`BybitClient.call` only** (client.py:160). Service-level retries use shorter `max_attempts=2, delay=0.5` for cancel/amend (idempotent operations).

### 5 Retry Events from Logs

GAP — searched logs `data/logs/workers.*.log` for `Retry exhausted|Retry [0-9]+/`. No matches in last 24h or any recent file. Reason: retries happen at DEBUG level (decorators.py:54-60); workers run at INFO level by default so `Retry {attempt}/{max}` lines are dropped. Only "Retry exhausted" (WARNING) would appear, and there are 0 in the last 24h — implying no retried calls reached final exhaustion.

The `ORDER_RETRY`/`ORDER_RETRY_OK`/`ORDER_RETRY_EXHAUSTED` log lines are emitted by the OrderService scoped retry, NOT by the `@retry` decorator. They were also not observed in the last 24h.

---

## 6. retCode Constants

Defined at `client.py:31-35`:
```python
RC_OK = 0
RC_RATE_LIMIT = 10006
RC_INVALID_API_KEY = 10003
RC_INVALID_SIGN = 10004
RC_DUPLICATE_ORDER_LINK_ID = 110072
```

Full error map: `BYBIT_ERROR_MAP` (client.py:51-63), 11 entries — see L1 section 8.

`_handle_response` (client.py:193-227): if `retCode == RC_OK` return `result`, else look up exception via `BYBIT_ERROR_MAP.get(ret_code, BybitAPIError)` and raise with details `{retCode, retMsg, operation}`.


=====================================================================
## FILE: L3_shadow_adapter.md
=====================================================================

# L3 — Shadow Adapter Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/shadow/shadow_adapter.py`
Measured line count: **774** lines.
Shadow root: `/home/inshadaliqbal786/shadow/`
Shadow HTTP server: `/home/inshadaliqbal786/shadow/src/api/shadow_client.py` (aiohttp `web` app).

---

## 1. Classes & Public Methods

The adapter file defines THREE service-mirror classes plus helpers.

### `ShadowPositionService` (file:135)

| Method | File:Line | Signature |
|---|---|---|
| `get_positions` | 150 | `(self, symbol: str | None = None) -> list[Position]` |
| `get_position` | 173 | `(self, symbol: str) -> Position | None` |
| `get_last_close` | 192 | `(self, symbol: str) -> dict | None` |
| `close_position` | 227 | `(self, symbol: str, *, purpose: str = "layer4_close") -> Order` |
| `reduce_position` | 273 | `(self, symbol: str, qty: float) -> Order` |
| `close_all_positions` | 332 | `(self) -> list[Order]` |
| `set_leverage` | 341 | `(self, symbol: str, leverage: int) -> bool` |
| `set_stop_loss` | 345 | `(self, symbol: str, stop_loss: float) -> bool` |
| `set_take_profit` | 358 | `(self, symbol: str, take_profit: float) -> bool` |
| `get_pnl_summary` | 371 | `(self) -> dict` |
| `health_check` | 392 | `(self) -> bool` |

### `ShadowOrderService` (file:409)

| Method | File:Line | Signature |
|---|---|---|
| `place_order` | 424 | see section 2 |
| `modify_order` | 549 | `(self, symbol, order_id, qty=None, price=None) -> Order` (returns rejected — Shadow is market-only) |
| `cancel_order` | 560 | `(self, symbol, order_id) -> bool` (no-op, returns True) |
| `cancel_all_orders` | 564 | `(self, symbol=None) -> int` (returns 0) |
| `get_open_orders` | 568 | `(self, symbol=None) -> list[Order]` (returns []) |
| `get_order_history` | 574 | `(self, symbol=None, limit=50) -> list[Order]` (returns []) |
| `health_check` | 580 | `(self) -> bool` |

### `ShadowAccountService` (file:597)

| Method | File:Line | Signature |
|---|---|---|
| `get_wallet_balance` | 611 | `(self) -> AccountInfo` |
| `get_available_balance` | 628 | `(self) -> float` |
| `get_equity` | 633 | `(self) -> float` |
| `get_margin_usage` | 638 | `(self) -> dict[str, float]` |
| `health_check` | 649 | `(self) -> bool` |

Module-level helpers: `_in_boot_grace` (54), `_shadow_get_with_retry` (59), `_parse_side` (666), `_build_position` (673), `_build_close_order` (703), `_build_account_info` (719), `_empty_account_info` (738), `_rejected_order` (748), `_optional_float` (767).

Boot-grace window constant: `_BOOT_GRACE_SECONDS = 30.0` (file:51). Inside the window, exhausted retries log at DEBUG; after the window, at ERROR (file:119-124).

---

## 2. `ShadowOrderService.place_order` — Signature Parity with Live

File:424-437:
```python
async def place_order(
    self,
    symbol: str,
    side: Side,
    order_type: OrderType,
    qty: float,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    leverage: int | None = None,
    *,
    purpose: str = "other",
    layer_snapshot: "LayerSnapshot | None" = None,
    force: bool = False,
) -> Order
```

This signature matches `OrderService.place_order` (live, file:400-414) parameter-by-parameter (name, kind, default). Shadow accepts but does NOT enforce `purpose`/`force`/`layer_snapshot` — values are recorded in the `SHADOW_ORDER_RECEIVED` audit log only (file:482-494).

### Parity Test

`tests/test_shadow_signature_parity.py` (205 lines). Three test functions:

1. `test_shadow_implements_every_public_live_method(live_cls, shadow_cls)` (test_file:144-158) — parametrized over `(OrderService, ShadowOrderService)`, `(PositionService, ShadowPositionService)`, `(AccountService, ShadowAccountService)`. Asserts `live_methods - shadow_methods == set()`.

2. `test_shadow_method_signatures_match_live(live_cls, shadow_cls)` (test_file:162-178) — for every shared method, calls `_assert_signature_match` which compares each parameter as `(name, kind.name, default)` tuples (test_file:79-86, `_normalize_param`).

3. `test_place_order_accepts_phase2_kwargs()` (test_file:181-205) — direct regression test that `inspect.signature(ShadowOrderService.place_order).bind(self=None, symbol=..., purpose="layer3_entry", layer_snapshot=None, force=False, ...)` does not raise `TypeError`.

The test exists because of a 2026-04-27 incident: live `OrderService.place_order` had `purpose`/`layer_snapshot`/`force` added in Phase 2 of the Layer 1 restructure, but the Shadow mirror was NOT updated, causing every brain-driven paper trade to crash with `TypeError: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'` (test_file docstring lines 17-25; references `dev_notes/phase0_post_layer1_fixes/issue_1_shadow_signature.md`).

The annotation is intentionally not compared (test_file:81-86): "What matters for runtime call compatibility is the name, kind, and default."

---

## 3. Shadow HTTP Endpoint Mapping

URL config: `general.shadow_api_url` default `"http://127.0.0.1:9090"` (`src/config/settings.py:41`, also at line 2336 in the loader). Wired into the adapters at `src/workers/manager.py:289-298`:

```python
shadow_url = getattr(settings.general, "shadow_api_url", "http://127.0.0.1:9090")
self._shadow_session = aiohttp.ClientSession()
shadow_position = ShadowPositionService(self._shadow_session, shadow_url)
shadow_order = ShadowOrderService(self._shadow_session, shadow_url)
shadow_account = ShadowAccountService(self._shadow_session, shadow_url)
log.info("Shadow adapters: created (API: {url})", url=shadow_url)
```

A single shared `aiohttp.ClientSession` is used by all three adapters.

### Endpoint table

Routes from the Shadow side (`/home/inshadaliqbal786/shadow/src/api/shadow_client.py:84-97`):
| Method | URL | Adapter caller (file:line) | Purpose |
|---|---|---|---|
| POST | `/api/order` | shadow_adapter.py:509 | Place order |
| POST | `/api/close` | shadow_adapter.py:257 | Full close |
| POST | `/api/reduce` | shadow_adapter.py:292 | Partial close |
| POST | `/api/set-sl` | shadow_adapter.py:350 | Set SL |
| POST | `/api/set-tp` | shadow_adapter.py:362 | Set TP |
| GET | `/api/positions` | shadow_adapter.py:159 | All positions |
| GET | `/api/position/{symbol}` | shadow_adapter.py:177 | Single position |
| GET | `/api/position/{symbol}/last_close` | shadow_adapter.py:210 | Most recent close |
| GET | `/api/balance` | shadow_adapter.py:620 | Wallet balance |
| GET | `/api/ticker/{symbol}` | (not used by adapter) | — |
| GET | `/api/health` | shadow_adapter.py:396, 583, 652 | Health probe |

### POST /api/order request format

Built at shadow_adapter.py:496-503:
```python
payload = {
    "symbol": symbol,
    "side": side_str,
    "qty": qty,
    "leverage": leverage or 1,
    "sl": stop_loss,
    "tp": take_profit,
}
```

Response shape (used at file:516-547):
- `data["status"]` may be `"Rejected"` -> returns `Order(status=REJECTED)` with `data.get("reason", "unknown")` logged.
- Else expects `data["order_id"]`, `data["price"]`, `data.get("qty", qty)`. Returns `Order(status=FILLED)` with `filled_qty = qty`, `avg_fill_price = price`.

Timeout: GAP — `place_order` does NOT set a per-request timeout. The default `aiohttp.ClientSession()` timeout (5 min) applies. Only `health_check` sets `aiohttp.ClientTimeout(total=5)` (file:397, 586, 655).

Retry: `place_order` has NO retry layer; one `aiohttp` POST. On `aiohttp.ClientError` it logs error and returns `_rejected_order(symbol, side=side)` (file:512-514). The shared retry helper `_shadow_get_with_retry` (file:59-127) is GET-only and used for `get_positions` (file:157), `get_wallet_balance` (file:619).

`_shadow_get_with_retry` parameters: `attempts=5`, `base_delay=0.2`, exponential backoff (`base_delay * 2**(attempt-1)`). Worst-case sleep ~3.0s before final exhaust (file:71-73). HTTP 4xx (except 429) abandoned without retry (file:99-104). On full exhaustion, returns `None` and logs `SHADOW_CALL_FAIL`.

---

## 4. Paper-Trade Simulation

The adapter delegates ALL fill simulation to the Shadow service itself (separate process at `127.0.0.1:9090`). Shadow's order engine lives at `/home/inshadaliqbal786/shadow/src/exchange/order_engine.py` (out of scope for the adapter).

The adapter does NOT perform local slippage/latency simulation. Side enum -> string conversion (`side.value`) at file:475, then JSON POST.

Audit logging in the adapter (file:482-494, 505, 533):
- `SHADOW_ORDER_RECEIVED` (line 490): logs symbol, side, qty, purpose, `layer_snapshot_keys=[...]`, force. Phase 1 of post-Layer-1 fix added this for directive→execution audit reconciliation.
- `SHADOW_ORD_SEND` (line 505): logs sl, tp, lev BEFORE the POST.
- `SHADOW_ORD_RESP` (line 533): logs `oid`, `fill` price, `st=FILLED` AFTER the POST.

Note: the snapshot class fields at audit time (from `layer_snapshot_keys`) are `[captured_at_monotonic, captured_at_wall, layer_active]` — confirmed in every recent log line.

---

## 5. Last 20 SHADOW_ORDER_RECEIVED Events (last 24h)

Total counts in last 24h (2026-05-01..2026-05-02 from `data/logs/workers.*.log`):
- `SHADOW_ORDER_RECEIVED`: **35**
- `SHADOW_ORD_RESP`: **35**
- `SHADOW_ORD_RESP` with `st=FILLED`: **35**
- Fill rate: **35/35 = 100.0%** (no rejections observed in last 24h)

Last 20 RECEIVED→RESP pairs (timestamp delta = end-to-end latency):

| # | Time | Symbol | Side | Qty | Latency (ms) | Result |
|---|---|---|---|---|---|---|
| 1 | 2026-05-02 02:35:42.743 | DYDXUSDT | Buy | 17103.6 | 14 | FILLED |
| 2 | 2026-05-02 02:35:43.635 | ORCAUSDT | Buy | 937.5 | 17 | FILLED |
| 3 | 2026-05-02 02:35:44.133 | INJUSDT | Sell | 301.6 | 9 | FILLED |
| 4 | 2026-05-02 02:44:14.063 | EGLDUSDT | Sell | 164.99 | 13 | FILLED |
| 5 | 2026-05-02 02:44:15.058 | AXSUSDT | Buy | 489.7 | 15 | FILLED |
| 6 | 2026-05-02 03:00:16.781 | RENDERUSDT | Sell | 1083.3 | 13 | FILLED |
| 7 | 2026-05-02 03:07:58.682 | AXSUSDT | Buy | 218.5 | 11 | FILLED |
| 8 | 2026-05-02 03:07:59.664 | AEROUSDT | Buy | 668.0 | 10 | FILLED |
| 9 | 2026-05-02 03:16:50.091 | ALGOUSDT | Sell | 2819.5 | 9 | FILLED |
| 10 | 2026-05-02 03:16:51.126 | AXSUSDT | Buy | 218.4 | 9 | FILLED |
| 11 | 2026-05-02 03:16:51.804 | NEARUSDT | Buy | 386.8 | 11 | FILLED |
| 12 | 2026-05-02 03:26:22.216 | BLURUSDT | Buy | 11108.0 | 19 | FILLED |
| 13 | 2026-05-02 03:42:13.855 | RENDERUSDT | Buy | 214.2 | 12 | FILLED |
| 14 | 2026-05-02 03:50:43.307 | INJUSDT | Buy | 99.8 | 9 | FILLED |
| 15 | 2026-05-02 03:50:44.287 | BLURUSDT | Buy | 41244.0 | 10 | FILLED |
| 16 | 2026-05-02 03:59:15.601 | RENDERUSDT | Sell | 175.2 | 10 | FILLED |
| 17 | 2026-05-02 04:07:52.484 | AXSUSDT | Buy | 218.1 | 10 | FILLED |
| 18 | 2026-05-02 04:16:29.144 | RENDERUSDT | Sell | 542.5 | 8 | FILLED |
| 19 | 2026-05-02 04:16:34.259 | HYPEUSDT | Buy | 26.8 | 15 | FILLED |
| 20 | 2026-05-02 04:25:16.987 | AXSUSDT | Buy | 220.9 | 10 | FILLED |

**Average response latency: ~11.7 ms** (sum 224, /20). All purpose=layer3_entry. All carrying `layer_snapshot_keys=[captured_at_monotonic,captured_at_wall,layer_active]`. Symbols repeated across cycles (AXSUSDT 5×, RENDERUSDT 4×, AEROUSDT 1× etc.) reflecting the strategist re-firing on the same setups.

---

## 6. Failure Handling Summary

| Failure path | File:Line | Behaviour |
|---|---|---|
| Shadow listener boot race (GET `/api/balance`/`/api/positions`) | 59-127 | 5 attempts with exponential backoff; in 30s boot grace logs at DEBUG, after at ERROR |
| `aiohttp.ClientError` on POST `/api/order` | 512-514 | logs ERROR `Shadow order error`, returns `_rejected_order(symbol, side=side)` (status=REJECTED, qty=0) |
| `data["status"] == "Rejected"` | 516-529 | logs WARNING `Shadow order rejected: {reason}`, returns `Order(status=REJECTED, ...)` preserving qty |
| `aiohttp.ClientError` on `/api/close` | 260-262 | logs ERROR `Shadow close error`, returns `_rejected_order(symbol)` |
| `aiohttp.ClientError` on `/api/reduce` | 296-301 | logs WARNING `REDUCE_FALLBACK reason=http_error`, falls back to full close |
| Shadow rejects partial reduce (`http != 200` or `status != "Reduced"`) | 320-330 | logs WARNING `REDUCE_FALLBACK reason=shadow_reject http={status} err='...'`, falls back to full close |
| `aiohttp.ClientError` on `/api/set-sl` | 354-356 | logs ERROR `Shadow set_sl error`, returns False |
| Network failure on `/api/health` | 400-401, 588-589, 657-658 | swallows Exception, returns False |

---

## 7. Notes & Gaps

- The `SHADOW_POSITION_CLOSE` audit log (file:250-252) carries `purpose` so close events reconcile with directive→execution traces.
- `purpose`/`layer_snapshot`/`force` parity with live exists on `place_order` only. `close_position` accepts `purpose` (kw-only, default `"layer4_close"`); the live `PositionService.close_position` has the same parameter (verified by parity test).
- Shadow has no Layer 3 gate (file:444-449 docstring): "Shadow has no Layer 3 gate, so the values are ACCEPTED but not enforced here".
- `did=d-<timestamp>` field at the end of each log line is the directive-id from `ctx()` — every order in a cycle shares the same `did` (visible in the table above: did=d-1777689214603 covers 3 orders within a few seconds).
- GAP — no per-request `timeout` on `place_order` POST.
- GAP — no retry on `place_order` POST. Live `OrderService` has a scoped 1-retry; Shadow has 0.


=====================================================================
## FILE: L4_fund_manager.md
=====================================================================

# L4 — Fund Manager Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/fund_manager/manager.py`
NOTE: prompt referenced `src/services/fund_manager.py` — that path does NOT exist (`src/services/` is not a directory). The actual fund manager lives at `src/fund_manager/manager.py`. Class `IntelligentFundManager`.

Measured line count: **579** lines.
Reconciler source: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/fund_reconciler.py` (240 lines).

---

## 1. `IntelligentFundManager` Class & Public Methods

Class: `IntelligentFundManager` (manager.py:20).

Constructor (manager.py:30-95): wires 22 sub-modules `m1_allocator..m22_fees`. Stored:
- `self.settings`, `self.db`, `self.services` (the ServiceContainer dict)
- `self._account_state: AccountState | None`
- `_consecutive_balance_fails: int = 0`, `_consecutive_position_fails: int = 0`, `_FAIL_ALERT_THRESHOLD = 3`

22 sub-modules at construction time (manager.py:58-79):
| Module | File | Purpose |
|---|---|---|
| m1_allocator | `capital_allocator.py` | Progressive level unlock |
| m2_sizer | `position_sizer.py` | Base % + multipliers |
| m3_reserves | `capital_reserves.py` | Pool selection |
| m4_correlation | `correlation_guard.py` | Correlation multiplier |
| m5_time_pools | `time_pools.py` | Horizon-based pools |
| m6_volatility | `volatility_scaler.py` | Volatility multiplier |
| m7_rotation | `sector_rotation.py` | Sector available + coin tier |
| m8_budgets | `strategy_budgets.py` | Strategy budget |
| m9_momentum | `momentum_allocator.py` | Strategy momentum mult |
| m10_weather | `risk_weather.py` | Risk weather assessment |
| m11_velocity | `capital_velocity.py` | Capital velocity tracking |
| m12_recovery | `recovery_planner.py` | Recovery mode plan |
| m13_opportunity | `opportunity_cost.py` | Best-use check |
| m14_ratchet | `profit_ratchet.py` | Profit lock-in |
| m15_time_sync | `time_sync.py` | Time-of-day mult |
| m16_emotion | `emotion_detector.py` | Market emotion |
| m17_ecosystem | `ecosystem_health.py` | Ecosystem score |
| m18_antifrag | `anti_fragile.py` | Anti-fragile override |
| m19_loss_harvest | `loss_harvester.py` | Loss harvesting |
| m20_compound | `compound_optimizer.py` | Compound logic |
| m21_liquidity | `liquidity_mapper.py` | Liquidity gate |
| m22_fees | `fee_optimizer.py` | Min profitable trade |

Public methods:
| Method | File:Line | Signature |
|---|---|---|
| `initialize` | 96 | `(self) -> None` |
| `update_state` | 132 | `(self) -> None` |
| `get_sizing_decision` | 202 | `(self, symbol, side, setup_score, setup_grade, consensus_strength, strategy_name, strategy_category, expected_hold_minutes, stop_loss_pct) -> SizingDecision` |
| `on_trade_opened` | 458 | `(self, symbol, amount, pool, horizon) -> None` |
| `on_trade_closed` | 465 | `(self, symbol, pnl_usd, pnl_pct, was_win, amount=0, horizon=TimeHorizon.FAST) -> None` |
| `get_full_status` | 476 | `(self) -> dict` |

Private helpers: `_get_next_level_info` (525), `_get_current_streak` (534), `_get_daily_pnl_pct` (552), `_load_starting_balance` (558), `_save_starting_balance` (567).

---

## 2. Fund Pool Logic — `FUND_POOLS` Emission

Emission site: `manager.py:200` (inside `update_state`):
```python
state.available = max(0, state.trading_capital - state.in_use)
log.info(f"FUND_POOLS | cap={state.trading_capital:.2f} | available={state.available:.2f} | in_use={state.in_use:.2f} | {ctx()}")
```

### `cap` / `available` / `in_use` computation

`update_state` flow (manager.py:132-200):

1. **Balance read** (lines 146-165): `account_svc.get_wallet_balance()` -> `state.total_equity = account.total_equity`. On Exception: increments `_consecutive_balance_fails`. If `>= _FAIL_ALERT_THRESHOLD` (3), logs `FUND_MGR_BALANCE_FAIL_PERSISTENT` at ERROR (line 154). Otherwise `FUND_MGR_BALANCE_FAIL` at WARNING (line 161).
2. **Growth multiplier** (lines 168-171): `state.growth_multiplier = state.total_equity / state.starting_balance` (or 1.0 if starting==0).
3. **Level update** (line 173): `m1_allocator.update_level(state)` — sets `state.level`.
4. **Profit ratchet update** (line 174): `m14_ratchet.update(state)`.
5. **Trading capital** (line 175): `state.trading_capital = state.total_equity * (state.unlock_pct / 100)`. The `unlock_pct` is set by the level (capital_allocator.py LEVEL_CONFIG: ROOKIE 20%, PROVEN 30%, VETERAN 40%, ELITE 50%, MASTER 60%).
6. **Reserve pool update** (line 176): `m3_reserves.update_pools(state)`.
7. **Position read** (lines 178-197): `pos_svc.get_positions()` -> `state.in_use = sum(abs(p.size * p.entry_price) for p in positions)` (line 181). On Exception: same consecutive-fail mechanism with `FUND_MGR_POSITIONS_FAIL_PERSISTENT` / `_FAIL`.
8. **Available** (line 199): `state.available = max(0, state.trading_capital - state.in_use)`.
9. **Emit FUND_POOLS log** (line 200).

### Recent FUND_POOLS samples (last 24h)

```
2026-05-02 06:24:44.040 FUND_POOLS | cap=1230.28 | available=1230.28 | in_use=0.00 | no_ctx
2026-05-02 06:25:44.051 FUND_POOLS | cap=1230.28 | available=1230.28 | in_use=0.00 | no_ctx
2026-05-02 06:26:44.155 FUND_POOLS | cap=1230.19 | available=676.52 | in_use=553.67 | no_ctx
2026-05-02 06:27:44.168 FUND_POOLS | cap=1230.14 | available=676.48 | in_use=553.67 | no_ctx
2026-05-02 06:28:44.181 FUND_POOLS | cap=1230.14 | available=676.48 | in_use=553.67 | no_ctx
2026-05-02 06:29:10.312 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | tid=t-ONDOUSDT-mon wid=w-1777703349462
2026-05-02 06:29:44.193 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
2026-05-02 06:30:44.205 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
2026-05-02 06:31:44.216 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
2026-05-02 06:32:44.232 FUND_POOLS | cap=1229.97 | available=1229.97 | in_use=0.00 | no_ctx
```

`cap ≈ 1230` matches `bybit_total ≈ 6150 × 20% unlock_pct` — consistent with ROOKIE level.

---

## 3. Reconciliation Worker

File: `src/workers/fund_reconciler.py`, class `FundReconciler(BaseWorker)` (line 43).

### Tick cadence

Settings (settings.py:1361-1370):
```python
reconcile_enabled: bool = True
reconcile_interval_seconds: int = 60
reconcile_drift_alert_threshold_pct: float = 5.0
reconcile_auto_correct: bool = False
```

Wired at `FundReconciler.__init__` (fund_reconciler.py:65-77):
```python
interval = float(getattr(settings.fund_manager, "reconcile_interval_seconds", 60))
super().__init__(name="fund_reconciler", interval_seconds=interval, ...)
```
Default 60s tick. Threshold 5.0% drift. Auto-correct OFF by default.

### Drift detection algorithm

`tick()` (fund_reconciler.py:93-205):
1. Skip with DEBUG log if `account_service` or `fund_manager` missing in services container.
2. Read Bybit-side: `account_svc.get_wallet_balance()` -> `bybit_total`, `bybit_available`. Exception -> `FUND_RECONCILE_FAIL | source=bybit` at WARNING (line 125).
3. Read local-side: `fund_manager._account_state` -> `local_total`, `local_cap`, `local_avail`. None -> `FUND_RECONCILE_FAIL | source=local reason=no_account_state` at WARNING (line 134).
4. Drift formula (line 148): `drift_pct = ((local_total - bybit_total) / bybit_total) * 100.0`. Comment at lines 144-146 documents this uses `total_equity` (single authoritative axis); `available` diverges by design (local subtracts unlock_pct + in_use).
5. Emit `FUND_RECONCILE` at INFO (line 152).
6. If `abs(drift_pct) > threshold`: emit `FUND_RECONCILE_DRIFT` WARNING + Telegram alert via `services.get("telegram").send_alert(...)` (lines 161-185).
7. If `_auto_correct == True`: overwrite `local_state.total_equity = bybit_total`, increment `_reconcile_corrections_today`, emit `FUND_RECONCILE_AUTO_CORRECT` WARNING (lines 188-202).
8. `_maybe_emit_daily_summary` (line 207): once per UTC day, emits `FUND_DAILY_SUMMARY | start_balance=... end_balance=... pnl_realized=... reconcile_corrections=...`.

### 24-hour event counts

| Event | 24h count |
|---|---|
| `FUND_RECONCILE` (INFO baseline) | 420 |
| `FUND_RECONCILE_DRIFT` (WARNING) | 0 |
| `FUND_RECONCILE_AUTO_CORRECT` | 0 |
| `FUND_RECONCILE_FAIL` | 0 |
| `FUND_MGR_BALANCE_FAIL` / `_PERSISTENT` | 0 |
| `FUND_MGR_POSITIONS_FAIL` / `_PERSISTENT` | 0 |

### 5 verbatim FUND_RECONCILE events

```
2026-05-01 00:00:01.861 FUND_RECONCILE | bybit_total=6197.12 bybit_available=6197.12 local_total=6197.12 local_cap=1239.42 local_avail=1239.42 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-01 00:01:15.924 FUND_RECONCILE | bybit_total=6197.12 bybit_available=6197.12 local_total=6197.12 local_cap=1239.42 local_avail=1239.42 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-02 04:31:41.513 FUND_RECONCILE | bybit_total=6163.30 bybit_available=6163.30 local_total=6163.30 local_cap=1232.66 local_avail=1232.66 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-02 04:32:41.526 FUND_RECONCILE | bybit_total=6163.30 bybit_available=6163.30 local_total=6163.30 local_cap=1232.66 local_avail=1232.66 drift_pct=+0.00 auto_correct=false | no_ctx
2026-05-02 04:33:41.538 FUND_RECONCILE | bybit_total=6163.30 bybit_available=6163.30 local_total=6163.30 local_cap=1232.66 local_avail=1232.66 drift_pct=+0.00 auto_correct=false | no_ctx
```

Drift consistently 0.00% — local matches Bybit exactly because local is ALSO sourced from `account_svc.get_wallet_balance()` (which proxies through transformer to Shadow's `/api/balance` in shadow mode). In paper mode, "Bybit" and "local" come from the same source so drift detection is degenerate. (This is a structural observation; not a finding to act on.)

A maximum momentary drift of +0.01% appears at 2026-05-02 06:26:42 (one tick where `bybit_total` updated 0.21 USD before local refreshed) — well under the 5% threshold.

---

## 4. Capital Allocation Strategy

### Tiered (level-based) allocation

`m1_allocator` is `CapitalAllocator` (`src/fund_manager/capital_allocator.py:60-94`). LEVEL_CONFIG dict at lines 18-65:
| Level | unlock_pct | max_leverage | max_positions | max_trade_pct | growth_threshold |
|---|---|---|---|---|---|
| ROOKIE | 20.0 | 3 | 3 | 5.0 | 1.0 |
| PROVEN | 30.0 | 4 | 5 | 7.0 | 1.5 |
| VETERAN | 40.0 | 5 | 7 | 10.0 | 2.0 |
| ELITE | 50.0 | 5 | 10 | 12.0 | 3.0 |
| MASTER | 60.0 | 5 | 10 | 15.0 | 5.0 |

Demotion thresholds (capital_allocator.py:73-75):
- `DEMOTION_DROP_PCT = 10.0` (10% drop from level-up equity)
- `CONSECUTIVE_LOSS_DAYS = 3` (3 consecutive losing days)
- `EMERGENCY_DRAWDOWN_PCT = 15.0` (15% from peak → force ROOKIE)

### Tiered-capital alternative

Comment at manager.py:93-94 and 234: "Profit floor removed (#4) — replaced by tiered capital system. See `src/fund_manager/tiered_capital.py`". Tiered tiers (tiered_capital.py file header):
- Tier 1: equity < 2x starting -> 20% usable (CONSERVATIVE)
- Tier 2: equity 2x-4x starting -> 30% usable (MODERATE)
- Tier 3: equity > 4x starting -> 40% usable (AGGRESSIVE)
- User override via Telegram supported.

### Per-coin / sector limits

Per-coin: handled by `m7_rotation` (`SectorRotation`) — `m7_rotation.get_available(symbol, state.trading_capital)` at manager.py:335 returns the sector-available cap, then `final_amount = min(final_amount, sector_available)` at line 336. Coin tier comes from `m7_rotation.get_coin_tier(symbol)` (used at line 370 for smart leverage). Detail of tier mapping is in `sector_rotation.py` (out of scope here).

Per-strategy budget: `m8_budgets.get_budget(strategy_name, state.trading_capital)` at manager.py:332.

Per-trade caps (manager.py:319-345):
- Pool available: `min(final_amount, pool_available)` (line 321)
- Level max trade %: `level_max_pct = m1_allocator.get_max_trade_pct(state.level)`; `level_max_usd = trading_capital * (level_max_pct/100)` (lines 323-325)
- Time-pool available: `min(final_amount, time_pool_available)` (line 329)
- Strategy budget: `min(final_amount, strategy_budget)` (line 333)
- Sector available: `min(final_amount, sector_available)` (line 336)
- Recovery max (if active): `recovery.max_trade_size_pct/100 * trading_capital` (lines 338-340)
- 2% per-trade-loss cap: `max_loss_allowed = trading_capital * 0.02; max_amount_for_risk = max_loss_allowed / (stop_loss_pct/100); min(final_amount, max_amount_for_risk)` (lines 342-345)
- Min profitable trade: if `final_amount < m22_fees.min_profitable_trade(symbol)` -> REJECT (lines 347-354)

Portfolio Optimizer hierarchy override (manager.py:415-427): a SQL fetch on `portfolio_allocations` table caps `final_amount` at the strategic-allocator percentage if present; failures swallowed silently (`except Exception: pass`).

Paper-trade minimum (manager.py:430-438): if `bybit.testnet` and `final_amount < 25.0`, force `final_amount = 25.0`.

### Leverage selection (manager.py:357-380)

```python
max_lev = m1_allocator.get_max_leverage(state.level)
if weather.max_leverage_override < max_lev:
    max_lev = weather.max_leverage_override
smart_lev = self.services.get("smart_leverage")
if smart_lev:
    leverage = smart_lev.calculate(symbol, direction=side, confidence=score/100, regime=None,
                                   coin_tier=..., volatility_percentile=...,
                                   ensemble_strength=consensus_strength)
    leverage = min(leverage, max_lev)
else:
    leverage = min(3, max_lev)
```

Hard global caps from settings.py:
- `risk.max_leverage: int = 3` (settings.py:515) — enforced by OrderService at order-placement time.
- `risk.max_position_size_pct: float = 10.0` (settings.py:519) — enforced by OrderService FIX 2 cap.

---

## 5. Failure Modes — ErrCode 110007

`110007` is `RC_POSITION_NOT_EXISTS`, mapped to `PositionError` at `client.py:57`:
```python
110007: PositionError,               # Position not exists
```

### Handling in fund_manager

GAP — `grep -rn "110007|PositionError" src/fund_manager/` returned **0 matches**. The fund manager itself does NOT catch `PositionError`. Position read failures are caught generically at `manager.py:183-197`:
```python
try:
    positions = await pos_svc.get_positions()
    state.in_use = sum(abs(p.size * p.entry_price) for p in positions)
    self._consecutive_position_fails = 0
except Exception as e:
    self._consecutive_position_fails += 1
    ...
```
Any `PositionError` (110007) bubbling from `pos_svc.get_positions()` would be caught here, increment `_consecutive_position_fails`, and (after 3 consecutive) emit `FUND_MGR_POSITIONS_FAIL_PERSISTENT` ERROR. `state.in_use` is NOT updated on failure (stays at last known value).

### 110007 count in last 24h

GAP — `grep -hE "110007|PositionError" data/logs/workers.*.log | grep "2026-05-0[12]"` returned **0 matches**. No 110007 events occurred in the last 24h.

### FUND_REJECT events 24h

`grep "FUND_REJECT" workers.*.log | grep "2026-05-0[12]"` returned **0 matches**. No fund-manager-side trade rejections in the last 24h.

---

## 6. DB Tables

| Table | Schema | Status |
|---|---|---|
| `fund_manager_state` | `(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))` | 4 rows present |
| `fund_manager_log` | `(id INT PK AUTOINC, event_type TEXT NOT NULL, symbol TEXT '', details_json TEXT '{}', created_at TEXT)` + INDEX `idx_fm_log` | EMPTY (0 rows in 24h) |
| `capital_level_history` | `(id INT PK AUTOINC, level TEXT, equity REAL, direction TEXT, reason TEXT '', created_at TEXT)` | not queried |

`fund_manager_state` rows in snapshot:
```
starting_equity   | 168000.0    | 2026-04-10 21:00:21
capital_override_pct | 0.5      | 2026-04-14 09:46:52
profit_ratchet    | {"total_locked": 539.97..., "equity_high": 164958.0, ...} | 2026-05-02 04:09:50
peak_equity       | 50000.0     | 2026-05-02 11:22:43
```

Note: `starting_equity` in the DB is 168000 but reconciler logs show local_total ~6151. The DB value reflects an older starting value; the live `_account_state.starting_balance` is loaded via `_load_starting_balance` (manager.py:558-565) reading from `user_preferences` table key `'starting_balance'`. There's a mismatch between which key holds the canonical starting balance (`fund_manager_state.starting_equity` vs `user_preferences.starting_balance`); manager.py only reads the latter.

---

## 7. Notes & Gaps

- Path discrepancy: prompt referenced `src/services/fund_manager.py`. That file/dir does not exist; actual location is `src/fund_manager/manager.py`.
- The `IntelligentFundManager` is constructed by `WorkerManager` and registered in the services container as `"fund_manager"`; `FundReconciler` looks it up there (fund_reconciler.py:106).
- Telegram alert path on drift (lines 171-185) uses `services.get("telegram") or services.get("telegram_bot")`. If neither is present (current paper-mode setup with telegram disabled), the alert is silently skipped via the bare except at line 182-185.
- `_account_state.starting_balance` is initialized with default `10000` if `account_svc` missing (manager.py:101-102). Init failure also defaults to 10000 (line 130).
- 22-module wiring is monolithic in `__init__`; no DI of sub-modules. Each sub-module receives `(settings, db)` or `(settings, services)` per its constructor (e.g. correlation_guard takes services for accessing position_service).


=====================================================================
## FILE: M1_caches.md
=====================================================================

# M1 — Stage 2 / Layer 3 In-Memory Caches

Forensic snapshot 2026-05-02 — refreshed from 2026-04-28 baseline.

Scope: caches owned by Brain (Stage 2), Layer 3 (apex/trade_gate/order_service), TradeCoordinator, FundManager pools.

---

## Cache 1 — `LayerManager._strategy_hints`

- **Owner / writer:** `src/workers/strategy_worker.py:825` — `layer_manager._strategy_hints = hints` (set inside the `is_layer_active(3)` gate per the comment at `strategy_worker.py:822`).
- **Allocation:** `src/core/layer_manager.py:118` — `self._strategy_hints: list = []`.
- **Consumers (readers):**
  - `src/brain/strategist.py:1092-1093` — `if layer_manager and hasattr(layer_manager, "_strategy_hints"): hints = getattr(layer_manager, "_strategy_hints", []) or []`.
  - `src/brain/strategist.py:2018-2019` — same pattern (Call A path).
- **Key format:** N/A (list, not dict).
- **Value structure:** `list` of strategy-hint dicts populated by StrategyWorker (exact element shape not asserted at the read site — defensive `getattr ... or []`).
- **Typical size:** Not explicitly bounded in writer code; one entry per qualifying strategy per cycle. NOT FOUND — searched for `len(self._strategy_hints)`, `_strategy_hints[-`, no truncation logic in `strategy_worker.py` or `layer_manager.py`.
- **5-entry snapshot from logs:** NOT FOUND — searched `data/logs/workers.log` for `STRAT_HINTS`, `_strategy_hints`, `STRATEGY_HINTS`; no log line dumps cache contents. Gap: cache state is not observable in current logging.

---

## Cache 2 — `LayerManager._coin_packages`

- **Owner / writer:** ScannerWorker, two write sites:
  - `src/workers/scanner_worker.py:1284` — `lm._coin_packages = packages`.
  - `src/workers/scanner_worker.py:1816` — `lm._coin_packages = packages` (alternate path).
- **Allocation:** `src/core/layer_manager.py:123` — `self._coin_packages: dict = {}`.
- **Accessor:** `src/core/layer_manager.py:1652-1659` — `get_coin_packages()` returns `getattr(self, "_coin_packages", {}) or {}`.
- **Consumers (readers):**
  - `src/brain/strategist.py:1664-1665` — `if lm is not None and hasattr(lm, "get_coin_packages"): packages = lm.get_coin_packages()`.
  - `src/core/layer_manager.py:1024` — `packages = self._coin_packages or {}` (auto-execute path; comment at lines 996, 1024).
- **Key format:** `symbol` (str, e.g. `"BTCUSDT"`) per `src/core/coin_package.py:121` and `get_coin_packages` docstring at `layer_manager.py:1652-1659`.
- **Value structure:** `CoinPackage` (defined in `src/core/coin_package.py`, scope: filter + ranking output written by ScannerWorker per coin).
- **Typical size:** "12 services per cycle" per docstring at `layer_manager.py:122` and `strategist.py:1533`; bounded by ScannerWorker selection size.
- **5-entry snapshot from logs:** NOT FOUND — searched logs for `STRATEGIST_PACKAGES_READ`, `PROMPT_PACKAGES`, `_coin_packages`. The strategist logs the read event (`strategist.py:1685-1688` `STRATEGIST_PACKAGES_READ | call=CALL_A reader=brain_call_a`) but does not dump entries. Gap: contents are not periodically dumped.

---

## Cache 3 — APEX Optimization Queue

- **Status:** NOT FOUND — searched `src/apex/optimizer.py`, `src/apex/assembler.py`, `src/apex/gate.py` for `queue`, `optimization_queue`, `_queue`. No queue is maintained in APEX. Per `src/workers/manager.py:1842-1854` ("APEX has no in-memory cache to hydrate — the assembler queries `trade_intelligence` on every optimization"), APEX is stateless aside from `TradeGate._conviction_cache`.

---

## Cache 4 — `TradeGate._conviction_cache` (APEX gate)

- **Owner / writer:** `src/apex/gate.py:45` — `self._conviction_cache: dict[str, tuple[float, float]] = {}`. TTL declared at `gate.py:46` — `self._conviction_cache_ttl: float = 300.0  # 5 minutes`.
- **Read site:** `src/apex/gate.py:400-403` — `cached = self._conviction_cache.get(_cache_key); if time.time() - ts < self._conviction_cache_ttl: ...`.
- **Population path:** `src/apex/gate.py:408-412` (under `_get_conviction_weight`) — pulled from `tias_repo` after `min_trades = getattr(self._settings, "conviction_min_trades", 3)`.
- **Key format:** `_cache_key` — string (computed in `_get_conviction_weight`; specific composition not asserted in this excerpt). NOT FOUND — exact key construction code not read in this pass.
- **Value structure:** `tuple[float, float]` — `(timestamp, weight)` (declared annotation at `gate.py:45`).
- **Typical size:** No max-entries bound observed. NOT FOUND — searched `_conviction_cache` for clear/evict; no eviction policy beyond TTL skip on read.
- **5-entry snapshot from logs:** NOT FOUND — no log lines dump conviction cache entries.

---

## Cache 5 — `TradeCoordinator._symbol_cooldowns` (per-symbol cooldown)

- **Owner / writer:** `src/core/trade_coordinator.py:116` — `self._symbol_cooldowns: dict[str, float] = {}  # symbol -> expiry timestamp`.
- **Write sites:** `trade_coordinator.py:551` — `self._symbol_cooldowns[symbol] = time.time() + cooldown_sec` inside the close path (`COORD_CLOSE_END` log emitter at line 552).
- **Cooldown durations** (`trade_coordinator.py:546-551`):
  - WIN: `cooldown_sec = 180` (3 min).
  - HARD STOP / FLASH CRASH: `cooldown_sec = 900` (15 min).
  - Normal LOSS: `cooldown_sec = 600` (10 min).
- **Consumers / readers:**
  - `src/core/trade_coordinator.py:556-560` — `is_in_cooldown(symbol)` reads expiry; deletes when expired.
  - `src/core/trade_coordinator.py:564-566` — `get_symbol_cooldown_remaining(symbol)`.
  - `src/core/rule_engine.py:120-126` — RuleEngine CHECK 1B2 reads `coordinator.get_symbol_cooldown_remaining(symbol)` to reject trades in cooldown.
- **Key format:** `symbol` (str).
- **Value structure:** `float` (Unix epoch expiry timestamp from `time.time() + cooldown_sec`).
- **Typical size:** Bounded by symbols traded × turnover; entries self-evict on read after expiry (`trade_coordinator.py:560`).
- **5-entry snapshot from logs:** NOT FOUND — `COORD_CLOSE_END | sym=... cooldown=...s by=... cbs_fired=...` lines emit per-event but no periodic dump of the cooldown map. Gap: the live dict is not periodically dumped to logs.

NOTE: Spec asked "TradeGate per-symbol cooldown state" — the per-symbol cooldown lives on TradeCoordinator (not TradeGate). TradeGate (`src/apex/gate.py`) does NOT hold a cooldown map; the only state on TradeGate is `_conviction_cache` (Cache 4). Verified by grepping `cooldown` in `src/apex/gate.py` — no hits.

---

## Cache 6 — `TradeCoordinator` other state

Adjacent in-memory state on the same coordinator (`src/core/trade_coordinator.py:109-118`):

- `self._trades: dict[str, TradeState]` (`:110`) — open trade state keyed by symbol.
- `self._closed_trades: list[dict]` (`:111`) — append-only close ring buffer.
- `self._callbacks_on_close: list` (`:112`) — close callbacks (registered via `register_close_callback`, line 571-573).
- `self._last_brain_context: dict[str, str]` (`:113`).
- `self._trade_plans: dict` (`:114`) — `symbol -> TradePlan`.
- `self._trade_info: dict[str, dict]` (`:115`) — extended trade info for Telegram alerts.
- `self._strategic_actions: list[dict]` (`:117`) — queued position actions from LayerManager (drained by Watchdog via `drain_strategic_actions`, lines 136-140).
- `self._close_reasons: dict[str, str]` (`:118`) — symbol -> close reason for attribution.

---

## Cache 7 — `OrderService` link_id tracking

- **Status:** NO PERSISTENT CACHE. `OrderService` does not retain per-symbol or per-order link_id state in memory between calls.
- **Generation site:** `src/trading/services/order_service.py:79` — `def _new_order_link_id() -> str:` (helper). Called inline at `order_service.py:481` — `order_link_id = _new_order_link_id()`.
- **Lifecycle:** Generated locally per call; logged into structured tags (`ORDER_ATTEMPT`, `ORDER_START`, `ORDER_OK`, `ORDER_FAIL`, `ORDER_RETRY`, `ORDER_DEDUPED`) at `order_service.py:489, 510, 639, 749, 763, 734`.
- **Recovery path:** `src/trading/services/order_service.py:773-774` — `_recover_order_by_link_id(self, *, order_link_id: str, symbol: str, ...)` queries Bybit (not an in-memory cache) for a previously-submitted order with the same link_id. Used in retry on `ORDER_DEDUPED` at line 737.
- **Related close/reduce link_ids:** `src/trading/services/position_service.py:143` (`close_link_id`), `:255` (`reduce_link_id`) — both ephemeral, generated per call from `uuid.uuid4().hex[:24]`.
- **5-entry snapshot:** N/A (no map exists).

---

## Cache 8 — Fund Manager pools state

- **Owner:** `src/fund_manager/manager.py:81` — `self._account_state: AccountState | None = None`.
- **Initialization:** `manager.py:101, 111` — populated from `_load_starting_balance()`.
- **Pools live ON `AccountState`** (mutated in place):
  - `src/fund_manager/capital_reserves.py:46` — `state.active_pool = capital * (ACTIVE_PCT / 100.0)` set inside `update_pools(state)` at `:38`.
  - `capital_reserves.py:82, 93, 103` — read paths (`state.active_pool + state.aplus_reserve`, etc.).
- **Time pools:** `src/fund_manager/manager.py:62` — `self.m5_time_pools = TimePoolManager(settings)`. Locked/released via `m5_time_pools.on_capital_locked(horizon, amount)` (`manager.py:461`) and `on_capital_released` (`manager.py:469`). Per-horizon lookup at `manager.py:328` — `time_pool_available = self.m5_time_pools.get_available(horizon, state.trading_capital)`.
- **Failure-streak counters:** `manager.py:89-91` — `_consecutive_balance_fails`, `_consecutive_position_fails`, `_FAIL_ALERT_THRESHOLD = 3` (used in `manager.py:152-163, 184-195`).
- **5-entry snapshot from logs:** NOT FOUND — searched logs; no periodic dump of `_account_state` pools.

### Adjacent fund_manager caches (TTL-based, atomic):

| Cache | File:line | Type | TTL |
|---|---|---|---|
| `RiskWeather._cache` | `src/fund_manager/risk_weather.py:59-60` | `RiskWeatherReport \| None` + `_cache_time: float` | `CACHE_TTL` constant; gate at `:72` `if (now - self._cache_time) < CACHE_TTL` |
| `EmotionDetector._cached_emotion` | `src/fund_manager/emotion_detector.py:73-75` | `MarketEmotion`, `int`, `float` | `_CACHE_TTL_SECONDS` (used at `:89`) |
| `SectorRotation._cached_direction/_cached_dominance` | `src/fund_manager/sector_rotation.py:46-47` | `str` + `float` | No TTL — refreshed by `update()` at `:87, 99` |
| `VolatilityScaler._cache` | `src/fund_manager/volatility_scaler.py:72-73` | `dict[str, tuple[float, float]]` (symbol -> (timestamp, multiplier)); `_percentile_cache` parallel | TTL gate at `:91` (cached lookup) |

---

## Cache 9 — Brain decision history

- **Status:** No persistent in-memory decision-history cache. Brain decisions persist to DB tables `claude_decisions` (DataLake writer at `src/core/data_lake.py:111`) and `brain_decisions` (`src/database/repositories/learning_repo.py:162-173`). See M2.
- **In-memory adjacent state on LayerManager:**
  - `_current_plan: StrategicPlan` and `_plan_history: list` (referenced at `src/core/layer_manager.py:761-778` — `self._plan_history.append(plan)`; capped at 20 entries: `if len(self._plan_history) > 20: self._plan_history = self._plan_history[-20:]`).
  - `_call_type` alternation flag (`layer_manager.py:741, 755` etc.).
- **5-entry snapshot:** Plan history is in-memory only; not dumped to logs. Cycle markers logged via `BRAIN_CYCLE_A`, `BRAIN_CYCLE_A_FAIL`, etc. at `layer_manager.py:745, 750-752`.

---

## Cache 10 — Other Stage-2/Layer-3 caches discovered (not in spec list)

- `MarketService._ticker_cache: dict[str, tuple[float, Ticker]]` — `src/trading/services/market_service.py:45`. Per-ticker TTL cache.
- `InstrumentService._cache: dict[str, InstrumentInfo]` + `_cache_time` — `src/trading/services/instrument_service.py:31-32`. TTL = `CACHE_TTL_SECONDS` (`:36`).
- `TACache` — `src/analysis/ta_cache.py` (TTL=120s set at `src/workers/manager.py:189`). Wraps `TAEngine`, registered as `services["ta"]`, `services["ta_engine"]`, `services["ta_cache"]` at `manager.py:190-193`.
- `StructureCache` — `src/analysis/structure/structure_cache.py`. TTL from `settings.structure.cache_ttl_seconds`. Read by Brain at `strategist.py:817, 1869` and APEX at `assembler.py:693`.
- `RegimeDetector._per_coin_regimes: dict[str, RegimeState]` — referenced at `src/workers/regime_worker.py:69, 111, 122, 192-194, 204` and `src/tias/collector.py:287`. Restored from DB on boot (`regime_worker.py:111-122`). RegimeDetector class lives at `src/strategies/regime.py`.

---

## Gap summary

- No cache periodically dumps its full contents to logs. Operators relying on greppable snapshots get only event-level emissions (write events for `_coin_packages`, close events for cooldowns, etc.).
- No bound observed on `_strategy_hints` length (Cache 1).
- `TradeGate._conviction_cache` has TTL but no max-entries cap (Cache 4).
- "TradeGate per-symbol cooldown state" requested in spec is actually on `TradeCoordinator`, not `TradeGate`; reconciled in Cache 5 note.


=====================================================================
## FILE: M2_db_tables.md
=====================================================================

# M2 — Stage 2 / Layer 3 DB Tables

Forensic snapshot 2026-05-02 — DB snapshot: `/tmp/trading_snapshot_1777722335.db`.

Tables in scope: `orders`, `positions`, `trade_thesis`, `trade_intelligence`, `claude_decisions`, `brain_decisions`, `apex_decisions`, `enforcer_stats`, `account_snapshots`, plus `fund_manager_state`/`fund_manager_log` discovered in scope.

---

## Table: `orders`

### Schema (DDL via .schema)

```sql
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    qty REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'New',
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- `sqlite_autoindex_orders_1` (PK on `order_id`)
- `idx_orders_symbol_status` ON `orders(symbol, status)`

### Writers
- `src/database/repositories/trading_repo.py:42` — `INSERT OR REPLACE INTO orders ...`.

### Readers
- `src/database/repositories/trading_repo.py:74` — `SELECT * FROM orders WHERE order_id = ?`
- `src/database/repositories/trading_repo.py:91` — `SELECT * FROM orders WHERE symbol = ? AND status IN ('New', 'PartiallyFilled') ORDER BY created_at DESC`
- `src/database/repositories/trading_repo.py:96` — `SELECT * FROM orders WHERE status IN ('New', 'PartiallyFilled') ORDER BY created_at DESC`
- `src/database/repositories/trading_repo.py:112` — `SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?`
- `src/database/repositories/trading_repo.py:117` — `SELECT * FROM orders ORDER BY created_at DESC LIMIT ?`

### Counts
- `SELECT COUNT(*) FROM orders` → **0**
- Growth rate by day: NOT FOUND — table is empty in the snapshot.

---

## Table: `positions`

### Schema

```sql
CREATE TABLE positions (
    symbol TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    entry_price REAL NOT NULL,
    mark_price REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    leverage INTEGER NOT NULL DEFAULT 1,
    liquidation_price REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- `sqlite_autoindex_positions_1` (PK on `symbol`)
- `idx_positions_symbol` ON `positions(symbol)`

### Writers
- `src/database/repositories/trading_repo.py:138` — `INSERT OR REPLACE INTO positions ...`
- `src/database/repositories/trading_repo.py:132` — `DELETE FROM positions WHERE symbol = ?`

### Readers
- `src/database/repositories/trading_repo.py:169` — `SELECT * FROM positions WHERE symbol = ?`
- `src/database/repositories/trading_repo.py:182` — `SELECT * FROM positions WHERE size > 0 ORDER BY symbol`

### Counts
- `SELECT COUNT(*) FROM positions` → **0**
- Growth: empty in snapshot.

---

## Table: `trade_thesis`

### Schema

```sql
CREATE TABLE trade_thesis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss_price REAL NOT NULL,
    take_profit_price REAL NOT NULL,
    size_usd REAL NOT NULL,
    leverage INTEGER NOT NULL DEFAULT 2,
    max_hold_minutes INTEGER NOT NULL DEFAULT 30,
    trailing_activation_pct REAL NOT NULL DEFAULT 1.0,
    thesis TEXT NOT NULL,
    market_context TEXT DEFAULT '',
    strategy_hints TEXT DEFAULT '',
    consensus TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    close_price REAL,
    actual_pnl_pct REAL,
    actual_pnl_usd REAL,
    close_reason TEXT,
    lesson TEXT,
    order_id TEXT,
    bybit_position_idx TEXT
, exchange_mode TEXT NOT NULL DEFAULT 'shadow', apex_flipped INTEGER NOT NULL DEFAULT 0, apex_original_direction TEXT NOT NULL DEFAULT '', apex_reason TEXT NOT NULL DEFAULT '');
```

### Indexes
- PK on `id`
- `idx_trade_thesis_symbol_status` ON `trade_thesis(symbol, status)`
- `idx_trade_thesis_status` ON `trade_thesis(status)`
- `idx_trade_thesis_opened` ON `trade_thesis(opened_at)`

### Writers
- `src/core/thesis_manager.py:47` — `INSERT INTO trade_thesis ...` (open).
- `src/core/thesis_manager.py:126` — `UPDATE trade_thesis ...` (close path 1).
- `src/core/thesis_manager.py:140` — `UPDATE trade_thesis ...` (close path 2).

### Readers
- `src/core/thesis_manager.py:82` — `... FROM trade_thesis ...`.
- `src/core/thesis_manager.py:173` — `... FROM trade_thesis ...`.
- `src/core/thesis_manager.py:188` — `SELECT DISTINCT symbol FROM trade_thesis WHERE status = 'open'`.
- `src/tias/collector.py:79` — `SELECT stop_loss_price, take_profit_price FROM trade_thesis ...`.
- `src/tias/collector.py:179` — `... FROM trade_thesis ...`.
- `src/strategies/performance_enforcer.py:323` — `... FROM trade_thesis ...`.
- `src/workers/cleanup_worker.py:243` — `... FROM trade_thesis ...`.

### Counts
- `SELECT COUNT(*) FROM trade_thesis` → **1257**
- Daily growth (column `opened_at`):
  - 2026-05-02: 30
  - 2026-05-01: 5
  - 2026-04-30: 9
  - 2026-04-29: 20
  - 2026-04-28: 32
  - 2026-04-27: 7
  - 2026-04-26: 18
  - 2026-04-25: 5
  - 2026-04-24: 19
  - 2026-04-23: 25

---

## Table: `trade_intelligence`

### Schema (TIAS — multi-group columns + DeepSeek + APEX)

```sql
CREATE TABLE trade_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Group A: Trade Outcome
    symbol TEXT NOT NULL, direction TEXT NOT NULL,
    strategy_name TEXT NOT NULL, strategy_category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '', closed_by TEXT NOT NULL,
    entry_price REAL NOT NULL, exit_price REAL NOT NULL,
    pnl_pct REAL NOT NULL, pnl_usd REAL NOT NULL,
    win INTEGER NOT NULL, hold_seconds REAL NOT NULL,
    -- Group B: Entry Decision Context
    leverage REAL, position_size_usd REAL,
    claude_thesis TEXT, claude_signal TEXT, claude_confidence REAL,
    entry_score REAL, ensemble_votes TEXT,
    -- Group C: Market Conditions at Close
    regime TEXT, fear_greed_value INTEGER, fear_greed_label TEXT,
    -- Group D: Technical Indicators at Close
    rsi REAL, macd_hist REAL, macd_signal REAL, bollinger_pct REAL,
    ema_20 REAL, ema_50 REAL, stochastic_k REAL, stochastic_d REAL,
    adx REAL, atr_value REAL, atr_pct REAL,
    volume_ratio REAL, price_vs_vwap REAL,
    -- Group E: Mode4 Profit Tracking
    m4_peak_pnl_pct REAL, m4_ticks_in_profit INTEGER, m4_ticks_total INTEGER,
    m4_composite_score REAL, m4_hurst_value REAL, m4_momentum_decay REAL,
    m4_extension_score REAL, m4_ev_ratio REAL, m4_volume_div_score REAL,
    -- Group F: DeepSeek Analysis
    ds_why TEXT, ds_what_worked TEXT, ds_what_failed TEXT,
    ds_lessons TEXT, ds_category TEXT, ds_confidence REAL, ds_analyzed_at TEXT,
    -- Group G: Metadata
    trade_id TEXT, trade_closed_at TEXT NOT NULL, captured_at TEXT NOT NULL
,
    -- Later additions (ALTER TABLE):
    ds_correct_direction TEXT, ds_what_should_done TEXT, ds_how_to_exploit TEXT,
    ds_optimal_direction TEXT, ds_optimal_sl_pct REAL, ds_optimal_tp_pct REAL,
    ds_optimal_size_usd REAL, ds_optimal_leverage INTEGER,
    ds_raw_response TEXT, ds_response_time_ms INTEGER,
    ds_input_tokens INTEGER, ds_output_tokens INTEGER,
    ds_cost_usd REAL, ds_model TEXT,
    analysis_version INTEGER, analysis_attempts INTEGER DEFAULT 0,
    entry_regime TEXT, entry_rsi REAL, entry_macd_hist REAL, entry_atr_pct REAL,
    apex_optimized INTEGER DEFAULT 0, apex_flipped INTEGER DEFAULT 0,
    apex_original_direction TEXT, apex_final_direction TEXT,
    apex_original_sl REAL, apex_final_sl REAL,
    apex_original_tp REAL, apex_final_tp REAL,
    apex_original_size REAL, apex_final_size REAL,
    apex_confidence REAL, apex_tp_mode TEXT, apex_reasoning TEXT,
    apex_model TEXT, apex_response_ms INTEGER, apex_cost_usd REAL,
    gate_adjustments TEXT, apex_tp_fill_rate REAL, regime_verified INTEGER DEFAULT 0
);
```

### Indexes
- PK on `id`
- `idx_ti_symbol` ON `trade_intelligence (symbol)`
- `idx_ti_win` ON `trade_intelligence (win)`
- `idx_ti_ds_why` ON `trade_intelligence (ds_why)`
- `idx_ti_trade_closed_at` ON `trade_intelligence (trade_closed_at)`
- `idx_ti_ds_category` ON `trade_intelligence (ds_category)`
- `idx_ti_apex_optimized` ON `trade_intelligence (apex_optimized)`

### Writers
- `src/tias/repository.py:46` — `INSERT INTO trade_intelligence ({col_names}) VALUES ({placeholders})`.
- `src/tias/repository.py:92` — `UPDATE trade_intelligence SET {set_clause} WHERE id = ?`.
- `src/tias/repository.py:131` — `UPDATE trade_intelligence ...` (DeepSeek analysis update).

### Readers (selected — many)
- `src/tias/repository.py:114, 148, 164, 186, 221, 253, 280, 307, 373, 396, 474, 497`
- `src/core/trade_recorder.py:45` — `SELECT DISTINCT symbol FROM trade_intelligence ...`.
- `src/workers/manager.py:896, 901, 905` — APEX startup stats query.
- `src/telegram/handlers/system.py:119`, `analysis.py:64`, `portfolio.py:103`, `apex_handler.py:57, 129, 183`, `dashboard_handler.py:2048, 2092, 2166, 2183, 2246`.

### Counts
- `SELECT COUNT(*) FROM trade_intelligence` → **821**
- Daily growth (column `trade_closed_at`):
  - 2026-05-02: 29
  - 2026-05-01: 5
  - 2026-04-30: 9
  - 2026-04-29: 18
  - 2026-04-28: 27
  - 2026-04-27: 6
  - 2026-04-26: 15
  - 2026-04-25: 5
  - 2026-04-24: 18
  - 2026-04-23: 24

---

## Table: `claude_decisions`

### Schema

```sql
CREATE TABLE claude_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch REAL NOT NULL,
    decision_type TEXT NOT NULL,
    new_trades_count INTEGER DEFAULT 0,
    position_actions_count INTEGER DEFAULT 0,
    market_view TEXT,
    risk_level TEXT,
    response_time_ms INTEGER,
    prompt_length INTEGER,
    full_response TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_claude_decisions_ts` ON `claude_decisions(ts_epoch)`

### Writers
- `src/core/data_lake.py:111` — `INSERT INTO claude_decisions ...`.

### Readers
- NOT FOUND via grep on `FROM claude_decisions` — no readers in `src/`. Gap: write-only audit table.

### Counts
- `SELECT COUNT(*) FROM claude_decisions` → **1232**
- Daily growth:
  - 2026-05-02: 51
  - 2026-05-01: 17
  - 2026-04-30: 33
  - 2026-04-29: 59
  - 2026-04-28: 56
  - 2026-04-27: 119
  - 2026-04-26: 42
  - 2026-04-25: 5
  - 2026-04-24: 29
  - 2026-04-23: 31

---

## Table: `brain_decisions`

### Schema

```sql
CREATE TABLE brain_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_hash TEXT NOT NULL,
    market_state_json TEXT NOT NULL DEFAULT '{}',
    claude_response TEXT NOT NULL DEFAULT '',
    decision_json TEXT NOT NULL DEFAULT '{}',
    action_taken TEXT NOT NULL DEFAULT '',
    outcome_json TEXT NOT NULL DEFAULT '{}',
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_brain_created` ON `brain_decisions(created_at DESC)`

### Writers
- `src/database/repositories/learning_repo.py:162` — `INSERT INTO brain_decisions ...`.
- `src/database/repositories/learning_repo.py:173` — `UPDATE brain_decisions SET action_taken=?, outcome_json=? WHERE id=?`.
- `src/brain/brain_v2.py:392` — `INSERT INTO brain_decisions ...`.

### Readers
- `src/database/repositories/learning_repo.py:180` — `SELECT * FROM brain_decisions ORDER BY created_at DESC LIMIT ?`.
- `src/database/repositories/learning_repo.py:188` — `SELECT COALESCE(SUM(cost_usd), 0) as total FROM brain_decisions WHERE DATE(created_at) = DATE('now')`.
- `src/telegram/handlers/brain.py:34`, `system.py:101` — `SELECT action_taken, trigger, cost_usd, created_at FROM brain_decisions ...`.

### Counts
- `SELECT COUNT(*) FROM brain_decisions` → **0**
- Growth: empty. Gap: writers exist (learning_repo, brain_v2) but no rows in this snapshot — `claude_decisions` (1232 rows) is the live table; `brain_decisions` may be unused / superseded.

---

## Table: `apex_decisions`

- **Status:** NOT FOUND in DB snapshot. `sqlite3 .schema apex_decisions` returned empty. APEX results are stored as columns on `trade_intelligence` (`apex_*` columns added via ALTER TABLE — see schema above) per `src/workers/manager.py:1842-1854` ("APEX has no in-memory cache to hydrate — the assembler queries `trade_intelligence`").
- **Writers/readers:** NOT FOUND via grep on `apex_decisions`.

---

## Table: `enforcer_stats`

- **Status:** NOT FOUND in DB snapshot. `sqlite3 .schema enforcer_stats` returned empty. Searched `src/` for `enforcer_stats` — no hits.
- Performance Enforcer (`src/strategies/performance_enforcer.py`) reads from `trade_thesis` directly (`performance_enforcer.py:323`).

---

## Table: `account_snapshots`

### Schema

```sql
CREATE TABLE account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    total_equity REAL NOT NULL,
    available_balance REAL NOT NULL,
    used_margin REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    margin_level_pct REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_account_snapshots_time` ON `account_snapshots(updated_at DESC)`

### Writers
- `src/database/repositories/trading_repo.py:250` — `INSERT INTO account_snapshots ...`.
- `src/core/transformer.py:919` — `INSERT INTO account_snapshots ...` (T1 mode-switch path).

### Readers
- NOT FOUND via grep on `FROM account_snapshots` — no readers in `src/`. Gap: write-only metric table.

### Counts
- `SELECT COUNT(*) FROM account_snapshots` → **47514**
- Daily growth (column `updated_at`):
  - 2026-05-02: 1006
  - 2026-05-01: 473
  - 2026-04-30: 626
  - 2026-04-29: 1055
  - 2026-04-28: 843
  - 2026-04-27: 3780
  - 2026-04-26: 2120
  - 2026-04-25: 393
  - 2026-04-24: 1209
  - 2026-04-23: 1395

---

## Table: `fund_manager_state` (in-scope; persistent state for fund manager)

### Schema

```sql
CREATE TABLE fund_manager_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### Indexes
- `sqlite_autoindex_fund_manager_state_1` (PK on `key`).

### Writers
- `src/core/trading_mode.py:145` — `INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('trading_mode', ?)`.
- `src/risk/drawdown.py:94` — `INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) ...` (peak_equity).
- `src/fund_manager/tiered_capital.py:91` — `INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('starting_equity', ?)`.
- `src/fund_manager/tiered_capital.py:157` — `INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('capital_override_pct', ?)`.
- `src/fund_manager/tiered_capital.py:162` — `DELETE FROM fund_manager_state WHERE key = 'capital_override_pct'`.
- `src/fund_manager/capital_allocator.py:304` — `INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) ...`.
- `src/fund_manager/profit_ratchet.py:166` — `INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) ...`.

### Readers
- `src/core/trading_mode.py:125` — `SELECT value FROM fund_manager_state WHERE key = 'trading_mode'`.
- `src/risk/drawdown.py:43` — `SELECT value FROM fund_manager_state WHERE key = 'peak_equity'`.
- `src/fund_manager/tiered_capital.py:84` — `SELECT value FROM fund_manager_state WHERE key = 'starting_equity'`.
- `src/fund_manager/tiered_capital.py:98` — `SELECT value FROM fund_manager_state WHERE key = 'capital_override_pct'`.
- `src/fund_manager/capital_allocator.py:93` — `SELECT * FROM fund_manager_state WHERE key = 'capital_level'`.
- `src/fund_manager/profit_ratchet.py:49` — `SELECT * FROM fund_manager_state WHERE key = 'profit_ratchet'`.

### Counts
- `SELECT COUNT(*) FROM fund_manager_state` → **4**
- Live contents (from snapshot):
  - `starting_equity` = `168000.0` (updated 2026-04-10 21:00:21).
  - `capital_override_pct` = `0.5` (updated 2026-04-14 09:46:52).
  - `profit_ratchet` = `{"total_locked": 539.9751076206283, "equity_high": 164958.0, "trade_locked": 539.9751076206283, "updated_at": "2026-05-02T04:09:50.969303+00:00"}` (updated 2026-05-02 04:09:50).
  - `peak_equity` = `50000.0` (updated 2026-05-02 11:22:43).

---

## Table: `fund_manager_log` (audit log)

### Schema

```sql
CREATE TABLE fund_manager_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    symbol TEXT DEFAULT '',
    details_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_fm_log` ON `fund_manager_log(event_type, created_at DESC)`

### Writers / readers
- NOT FOUND via grep — no `INSERT INTO fund_manager_log` / `FROM fund_manager_log` in `src/`. Gap: schema present (created at `src/database/migrations.py:808-816`) but no callers wired in current codebase.

### Counts
- `SELECT COUNT(*) FROM fund_manager_log` → **0**

---

## Other tables in DB snapshot (out of scope but adjacent)

`.tables` returned 71 tables. Stage 1 (data) tables not detailed here: `klines`, `funding_rates`, `orderbook_snapshots`, `news_articles`, `aggregated_sentiment`, `regime_history`, `coin_regime_history`, `signals`, `signal_accuracy`, `pattern_log`, `pattern_occurrences`, `discovered_patterns`, `ticker_cache`, `correlation_matrix`, `market_snapshots`, `open_interest`, `economic_calendar`, `reddit_posts`, `fear_greed_index`, `transformer_state`, etc. Stage 2/3 lifecycle/learning tables also out of scope: `trade_history`, `trade_journal`, `trade_log`, `strategy_*`, `backtest_*`, `pnl_*`.


=====================================================================
## FILE: M3_service_registry.md
=====================================================================

# M3 — Service Registry

Forensic snapshot 2026-05-02 — refreshed from 2026-04-28 baseline.

---

## Construction site

The service registry is a `dict` named `self._services` on `WorkerManager`.

- **Allocation:** `src/workers/manager.py:56` — `self._services: dict = {}`.
- **First insert:** `src/workers/manager.py:57` — `self._services["worker_liveness"] = self._worker_liveness`.
- **No DI container module.** `src/core/container.py` exists (lines 37, 70-71, 106, 114, 131-132 register `db`/`ta`/`ta_engine`/`alert_manager`/`risk_manager`/`registry`/`pnl_manager`) but is NOT the live registry path used by Stage 2 / Layer 3 workers — those receive `WorkerManager._services` by reference.
- Total `self._services["..."] = ...` writes in `src/workers/manager.py`: **87** (counted via `grep -n self._services\[ ... | wc -l`).

---

## All keys registered (in insertion order, with backing instance)

Bootstrap order matters because workers are wired by reference and late attaches occur. Order below is the literal order in `initialize()` (and `_create_workers()` for the second half).

| # | Line | Key | Backing Class / Module |
|---|---|---|---|
| 1 | 57 | `worker_liveness` | `WorkerLivenessTracker` (`src/core/worker_liveness.py`) |
| 2 | 82 / 85 | `cycle_tracker` | `CycleTracker` (`src/core/cycle_tracker.py`) — `None` on init failure |
| 3 | 92 | `transformer` | `Transformer` (`src/core/transformer.py`) |
| 4 | 112 | `bybit` | `BybitClient` (`src/trading/client.py`) |
| 5 | 113 | `ws` | `BybitWebSocket` (`src/trading/websocket.py`) |
| 6 | 114 | `market` | `MarketService` (`src/trading/services/market_service.py`) |
| 7 | 115 | `market_service` | `MarketService` (alias of `market`) |
| 8 | 170-175 | `news`, `calendar`, `reddit`, `fear_greed`, `funding`, `oi`, `onchain`, `aggregator`, `signal_gen` | Intelligence services (`src/intelligence/...`) |
| 9 | 190 | `ta` | `TACache(TAEngine)` (`src/analysis/ta_cache.py`) |
| 10 | 191 | `ta_engine` | `TACache` (alias) |
| 11 | 192 | `ta_cache` | `TACache` (alias) |
| 12 | 193 | `ta_raw` | Raw `TAEngine` instance |
| 13 | 208 | `volatility_profiler` | `VolatilityProfiler` (`src/analysis/volatility_profile.py`) |
| 14 | 222 | `structure_engine` | `StructureEngine` (`src/analysis/structure/structure_engine.py`) |
| 15 | 223 | `structure_cache` | `StructureCache` (`src/analysis/structure/structure_cache.py`) |
| 16 | 239 | `shadow_kline_reader` | `ShadowKlineReader` (`src/analysis/structure/shadow_kline_reader.py`) |
| 17 | 276 | `instrument_service` | `InstrumentService` (`src/trading/services/instrument_service.py`) |
| 18 | 325-330 | `position`, `order`, `account`, `position_service`, `order_service`, `account_service` | `_OrderProxy` / `_PositionProxy` / `_AccountProxy` from `Transformer.create_proxies()` (`src/core/transformer.py`) — fall back to direct shadow/bybit if transformer unavailable. The Bybit-side `OrderService` is at `src/trading/services/order_service.py`; ShadowOrderService at `src/shadow/shadow_adapter.py` |
| 19 | 367 | `cost_tracker` | `ClaudeCodeCostTracker` (`src/brain/claude_code_client.py`) |
| 20 | 368 | `claude_client` | `ClaudeCodeClient` (`src/brain/claude_code_client.py`) |
| 21 | 369 | `decision_parser` | `DecisionParser` (`src/brain/decision_parser.py`) |
| 22 | 386 | `alert_manager` | `AlertManager` (`src/alerts/alert_manager.py`) |
| 23 | 409 | `risk_manager` | `RiskManager` (`src/risk/risk_manager.py`) |
| 24 | 416 | `freshness_guard` | `FreshnessGuard` (`src/core/freshness_guard.py`) |
| 25 | 421 | `trade_coordinator` | `TradeCoordinator` (`src/core/trade_coordinator.py`) |
| 26 | 448 | `sl_gateway` | `SLGateway` (`src/core/sl_gateway.py`) |
| 27 | 473 | `thesis_manager` | `ThesisManager` (`src/core/thesis_manager.py`) |
| 28 | 478 | `sl_validator` | `SLTPValidator` (`src/core/sl_tp_validator.py`) |
| 29 | 483 | `data_lake` | `DataLakeWriter` (`src/core/data_lake.py`) |
| 30 | 488 | `event_buffer` | `EventBuffer` (`src/core/event_buffer.py`) |
| 31 | 504 | `urgent_queue` | `UrgentQueue` (`src/core/urgent_queue.py`) |
| 32 | 511 | `trading_mode` | `TradingModeManager` (`src/core/trading_mode.py`) |
| 33 | 544 | `tiered_capital` | `TieredCapitalManager` (`src/fund_manager/tiered_capital.py`) |
| 34 | 554 | `strategist` | `ClaudeStrategist` (`src/brain/strategist.py`) |
| 35 | 562 | `rule_engine` | `RuleEngine` (`src/core/rule_engine.py`) |
| 36 | 571 | `layer_manager` | `LayerManager` (`src/core/layer_manager.py`) |
| 37 | 950 | `price_worker` | `PriceWorker` (`src/workers/price_worker.py`) |
| 38 | 956 | `kline_worker` | `KlineWorker` (`src/workers/kline_worker.py`) |
| 39 | 972 | `altdata_worker` | (`src/workers/altdata_worker.py`) |
| 40 | 982 | `signal_worker` | (`src/workers/signal_worker.py`) |
| 41 | 1011 | `position_watchdog` | `PositionWatchdog` (`src/workers/position_watchdog.py`) |
| 42 | 1037 | `profit_sniper` | `ProfitSniper` (`src/workers/profit_sniper.py`) |
| 43 | 1074 | `scanner` | `MarketScanner` (`src/strategies/scanner.py`) |
| 44 | 1083 | `scanner_worker` | `ScannerWorker` (`src/workers/scanner_worker.py`) |
| 45 | 1119 | `structure_worker` | `StructureWorker` (`src/workers/structure_worker.py`) |
| 46 | 1132 | `regime_detector` | `RegimeDetector` (`src/strategies/regime.py`) |
| 47 | 1140 | `regime_worker` | `RegimeWorker` (`src/workers/regime_worker.py`) |
| 48 | 1171 | `registry` | `StrategyRegistry` |
| 49 | 1172 | `pnl_manager` | `DailyPnLManager` |
| 50 | 1190 | `strategy_worker` | `StrategyWorker` (`src/workers/strategy_worker.py`) |
| 51 | 1241 | `risk_budget` | (`src/risk/...`) |
| 52 | 1242 | `kelly` | (`src/risk/...`) |
| 53 | 1243 | `correlation_tracker` | (`src/risk/...`) |
| 54 | 1258 | `telegram_bot` | (`src/telegram/bot.py`) |
| 55 | 1291 | `enforcer` | `PerformanceEnforcer` (`src/strategies/performance_enforcer.py`) |
| 56 | 1308 | `fund_manager` | `IntelligentFundManager` (`src/fund_manager/manager.py`) |
| 57 | 1376 | `worker_liveness_watchdog` | `WorkerLivenessWatchdog` (`src/workers/worker_liveness_watchdog.py`) |
| 58 | 1656 | `tias_repo` | `TradeIntelligenceRepo` (`src/tias/repository.py`) |
| 59 | 1830 | `apex_optimizer` | `TradeOptimizer` (`src/apex/optimizer.py`) |
| 60 | 1835 | `apex_gate` | `TradeGate` (`src/apex/gate.py`) |
| 61 | 1895 | `sentinel_advisor` | `PortfolioAdvisor` (`src/sentinel/advisor.py`) |

---

## Key bootstrap-order observations

- **Brain (Stage 2) services depend on Layer 3 services already being registered.** `LayerManager` (line 571) is registered AFTER `OrderService` (line 326-329). Per `manager.py:572-602`, after LM construction the bootstrap walks the transformer's owned service sets and calls `attach_layer_manager` on each underlying instance that exposes the method. Comment at 587-588: ShadowOrderService does NOT expose this (no L3 gate by design); BybitOrderService does. This was a regression-fix audit-finding (Phase 2 post-Layer-1 fix).
- **Late-wires for `regime_detector`:**
  - `manager.py:1145` — `_wd.regime_detector = detector` (PositionWatchdog, created earlier).
  - `manager.py:1150` — `_vp._regime_detector = detector` (VolatilityProfiler).
  - `manager.py:1155` — `_scanner.regime_detector = detector`.
  - `manager.py:205` — VolatilityProfiler constructor passes `regime_detector=None` initially (Late-wired comment).
- **Ordering risk:** `volatility_profiler`, `structure_cache`, `position_watchdog`, `profit_sniper`, `scanner`, `strategy_worker` are all registered BEFORE `regime_detector` (line 1132), so consumers must defensively `.get("regime_detector")` and tolerate `None` until late-wire completes.

---

## `services.get(...)` calls across Stage 2 (Brain) and Layer 3 (apex/order/trade_gate)

### Brain — `src/brain/strategist.py`

| Line | Key | Behavior on miss |
|---|---|---|
| 289 | `transformer` | Implicit `None` — used in `if tf:` checks |
| 307 | `position_service` | `None` tolerated |
| 508 | `transformer` | `None` tolerated |
| 564 | `enforcer` | `None` tolerated |
| 567 | `structure_cache` | `None` tolerated |
| 582 | `regime_detector` | `if regime_detector:` guard |
| 599 | `fear_greed` | `None` tolerated |
| 626 | `trading_mode` | `None` tolerated |
| 633 | `thesis_manager` | `None` tolerated |
| 643 | `market_service` | `None` tolerated |
| 669 | `scanner` | `None` tolerated |
| 670 | `market_service` | `None` tolerated |
| 671 | `ta` OR `ta_cache` | First-non-None fallback chain |
| 672 | `volatility_profiler` | `None` tolerated |
| 693 | `regime_detector` | `None` tolerated |
| 797 | `data_lake` | `None` tolerated |
| 817 | `structure_cache` | `if structure_cache:` |
| 956 | `thesis_manager` | `None` tolerated |
| 1008 | `position_service` | `None` tolerated |
| 1013 | `trade_coordinator` | `None` tolerated |
| 1073 | `trade_coordinator` | `None` tolerated |
| 1091 | `layer_manager` | `if layer_manager and hasattr(...)` |
| 1130 | `account_service` | `None` tolerated |
| 1141 | `tiered_capital` | `None` tolerated |
| 1146 | `position_service` | `None` tolerated |
| 1166 | `pnl_manager` | `None` tolerated |
| 1204 | `event_buffer` | `None` tolerated |
| 1447 | `layer_manager` (via `getattr(self, "services", None)`) | `None` tolerated |
| 1549 | `enforcer` | (Call A path mirror) |
| 1552 | `structure_cache` | |
| 1569 | `regime_detector` | |
| 1586 | `fear_greed` | |
| 1626 | `trading_mode` | |
| 1636 | `thesis_manager` | |
| 1638 | `scanner` | |
| 1639 | `market_service` | |
| 1640 | `ta` OR `ta_cache` | |
| 1641 | `volatility_profiler` | |
| 1663 | `layer_manager` | |
| 1721 | `regime_detector` | |
| 1844 | `data_lake` | |
| 1869 | `structure_cache` | |
| 1995 | `position_service` | |
| 2017 | `layer_manager` | |
| 2059 | `account_service` | |
| 2068 | `tiered_capital` | |
| 2073 | `position_service` | |
| 2089 | `pnl_manager` | |
| 2102 | `event_buffer` | |
| 2130 | `urgent_queue` | |
| 2251 | `pnl_manager` | |
| 2260 | `thesis_manager` | |
| 2261 | `position_service` | |
| 2262 | `trade_coordinator` | |
| 2263 | `regime_detector` | |
| 2384 | `urgent_queue` | |
| 2511 | `trade_coordinator` | |
| 2600 | `market_service` | |
| 2601 | `ta` OR `ta_cache` | |
| 2602 | `trade_coordinator` | |

### APEX — `src/apex/gate.py`

| Line | Key |
|---|---|
| 111 | `position_service` |
| 125 | `fund_manager` |
| 164 | `position_service` |
| 176 | `trade_coordinator` |
| 213 | `market_service` |
| 297 | `structure_cache` |
| 368 | `regime_detector` |
| 408 | `tias_repo` |

### APEX — `src/apex/assembler.py`

| Line | Key |
|---|---|
| 144 | `price_worker` |
| 162-163 | `market_service` OR `market` (fallback) |
| 204 | `ta_cache` OR `ta` (fallback) |
| 333-334 | `market_service` OR `market` |
| 368 | `volatility_profiler` |
| 586 | `regime_detector` |
| 693 | `structure_cache` |

### TIAS — `src/tias/collector.py`

- Line 280: `regime_detector` (uses `get_coin_regime`, `_per_coin_regimes`, `_last_regime`).

### Layer 3 OrderService — `src/trading/services/order_service.py`

- The OrderService stores LM via attach (not via `services.get`). LM use sites: `:325` `lm.is_layer_active(3)`, `:330` `layer_snapshot.is_layer_active(3)`.

---

## `regime_detector` vs `regime_worker` — which key APEX/TIAS get

Both keys are registered (lines 1132 and 1140 of `manager.py`). The bootstrap comment at 1137-1140 explicitly states:

> "Phase 6 (corrected-Layer-1): expose the worker (in addition to the detector) so ScannerWorker's get_regime accessor has a stable handle even if RegimeDetector internals change."

**Consumers:**
- APEX (`apex/gate.py:368`, `apex/assembler.py:586`) → reads `regime_detector` (NOT `regime_worker`).
- TIAS (`tias/collector.py:280`) → reads `regime_detector`.
- Brain (`brain/strategist.py:582, 693, 1569, 1721, 2263`) → reads `regime_detector`.
- Telegram dashboard (`telegram/bot.py:569`, `telegram/handlers/analysis.py:83`, `telegram/features/morning_briefing.py:37`) → reads `regime_detector`.
- ScannerWorker uses `regime_worker`: `src/workers/scanner_worker.py:162, 713-716, 953` — `rw = self.services.get("regime_worker"); if rw and hasattr(rw, "get_regime"): state = regime_worker.get_regime(symbol)`.

**Verdict:** APEX and TIAS get `regime_detector` (the detector with `_per_coin_regimes` / `_last_regime` / `get_last_regime` / `detect`). Only ScannerWorker uses `regime_worker`. Both are wired correctly per the consumer's intended access pattern. The detector is registered FIRST (line 1132) and the worker is registered SECOND (line 1140), so neither consumer can race against missing registration provided their ticks fire after `_create_workers()` completes.

---

## Race conditions / late-wires

- `position_watchdog` and `profit_sniper` are constructed (line 1011, 1037) before `regime_detector` (line 1132). Late-wired at lines 1145 (`_wd.regime_detector = detector`), 1178 (`regime_detector=detector` arg), and `1004, 1032` (`regime_detector=self._services.get("regime_detector")` at construction — likely `None` at that moment).
- `volatility_profiler` (line 208) is constructed before `regime_detector` (line 1132). Late-wired at line 1150.
- `scanner` (line 1074) is constructed before `regime_detector`. Late-wired at line 1155.
- `OrderService` (Bybit-side, line ~268) is constructed before `LayerManager` (line 571). Late-attached via `attach_layer_manager` walk at lines 593-602.
- `event_buffer` (line 488) is constructed AFTER `sl_gateway` (line 448). Late-wired at line 499 (`_sl_gateway.set_event_buffer(event_buffer)`).
- The bootstrap is single-threaded async (`async def initialize`); race conditions arise only when worker ticks beat `_create_workers()` completion. The `Layer3BootNotReadyError` path at `src/trading/services/order_service.py:283-311` handles this for OrderService specifically with a `lm_attach_deadline_sec` budget.

---

## Bot-data registration (parallel registry)

`src/telegram/handlers/dashboard_handler.py:2306-2311` mirrors `services` into `app.bot_data` for handler access:

```python
if services:
    for key, value in services.items():
        if value is not None:
            app.bot_data[key] = value
```

So `_svc(context, "...")` in `control_handler.py:27-29` reads from `bot_data`, not from the LayerManager directly. Stale entries in `bot_data` could diverge from `_services` if late-wires happen after the dashboard registration (no observed re-sync).

---

## Notes / gaps

- No single "service registry" diagram in the codebase. Source of truth is `WorkerManager.initialize()` and `WorkerManager._create_workers()` in `src/workers/manager.py`.
- 6 keys for the trading triple (`position`/`position_service`, `order`/`order_service`, `account`/`account_service`) — alias duplication is intentional but consumers vary in which alias they read.
- 4 keys for the TA cache (`ta`, `ta_engine`, `ta_cache`, `ta_raw`) — `ta_raw` is the only one that returns the unwrapped engine.


=====================================================================
## FILE: M4_layer_active_gating.md
=====================================================================

# M4 — Layer Active Gating

Forensic snapshot 2026-05-02 — refreshed from 2026-04-28 baseline.

---

## 1. `data/layer_state.json` current contents

Path: `/home/inshadaliqbal786/trading-intelligence-mcp/data/layer_state.json`

```json
{
  "layer_active": {
    "1": true,
    "2": false,
    "3": false
  },
  "user_stopped": true,
  "timestamp": "2026-05-02T11:26:52.917178+00:00"
}
```

State as of collection: Layer 1 ON, Layer 2 OFF, Layer 3 OFF, `user_stopped=true`. Timestamp matches the operator-driven Telegram stop sequence shown in §3 below.

---

## 2. `layer_active` dict in `LayerManager`

- **Allocation:** `src/core/layer_manager.py:73` — `self._layer_active = {1: False, 2: False, 3: False}`.
- **State file constant:** `src/core/layer_manager.py:28` — `_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "layer_state.json"`.
- **Reads:**
  - `src/core/layer_manager.py:1536-1537` — `def is_layer_active(self, layer: int) -> bool: return self._layer_active.get(layer, False)`.
  - `src/core/layer_manager.py:565, 570, 575, 629, 635, 712, 784, 844` — internal control flow (cascade stop, brain loop, execute trades / position actions).
  - `src/core/layer_manager.py:195` — serialized in `_persist_state` (`{"layer_active": {str(k): v for k, v in self._layer_active.items()}}`).
  - `src/core/layer_manager.py:335` — read in `_sync_state_with_disk` for compare.
- **Writes (in-memory):**
  - `src/core/layer_manager.py:656` — `self._layer_active[1] = True` (`_start_data_layer`).
  - `src/core/layer_manager.py:663` — `self._layer_active[2] = True` (`_start_brain_layer`).
  - `src/core/layer_manager.py:676` — `self._layer_active[3] = True` (`_start_execution_layer`).
  - `src/core/layer_manager.py:683` — `self._layer_active[1] = False` (stop_data_layer).
  - `src/core/layer_manager.py:687` — `self._layer_active[2] = False` (stop_brain_layer).
  - `:716` etc. — stop_execution_layer.
  - `src/core/layer_manager.py:369` — `self._layer_active[layer_id] = disk_active[layer_id]` (legacy `reload_memory` drift recovery branch only).
- **Persistence to disk:** `src/core/layer_manager.py:177-211` — `_persist_state()` writes `_STATE_FILE` and emits `LAYER_STATE_PERSIST_OK` / `LAYER_STATE_PERSIST_FAIL`.
- **Restore on boot:** `src/core/layer_manager.py:213-222` — `_load_persisted_state()` restores ONLY `user_stopped`; layers always start inactive (`# Layers start inactive regardless`).
- **`LayerSnapshot` dataclass:** `src/core/layer_manager.py:33-54` — frozen point-in-time view of `layer_active` (used by Layer 3 race-check, see §4).

---

## 3. `LAYER_STATE_SYNC` heartbeat (Phase 2 of post-Layer-1 fixes)

### Current implementation

- **Start:** `src/core/layer_manager.py:231-279` — `start_state_sync(interval_sec: float = 60.0, *, on_drift_action: str = "rewrite_disk")`. Validated values: `"rewrite_disk"` (default) or `"reload_memory"` (legacy emergency rollback). Defaults captured at `:144` — `self._drift_action: str = "rewrite_disk"`.
- **Loop:** `src/core/layer_manager.py:281-300` — `_state_sync_loop(interval_sec)` — sleeps first, then ticks, swallows transient errors (logs `LAYER_STATE_SYNC_LOOP_ERROR`).
- **One iteration:** `src/core/layer_manager.py:302-377` — `_sync_state_with_disk()`:
  - If `_STATE_FILE` missing → emit `LAYER_STATE_SYNC | match=na disk=missing memory=...` at DEBUG and return (`:323-329`).
  - Read disk JSON, coerce keys to int (`:330-334`).
  - Compute `match = disk_active == memory_active` (`:336`).
  - Emit `LAYER_STATE_SYNC | match=true|false disk=... memory=...` at INFO **every tick** (`:338-341`).
  - On match: return.
  - On drift + `_drift_action == "rewrite_disk"`: emit `LAYER_STATE_DRIFT_RECOVERED | direction=memory_to_disk disk=... memory=... reason=disk_was_stale` at WARNING and call `_persist_state()` (`:346-359`).
  - On drift + `_drift_action == "reload_memory"`: emit `LAYER_STATE_DRIFT | disk=... memory=... action=reload_from_disk` at WARNING and overwrite memory from disk for known keys (`:361-369`).

### Drift recovery direction

- **Default direction (post-Phase-11 fix): MEMORY → DISK** (`:144` — `self._drift_action: str = "rewrite_disk"`).
- The old direction (DISK → MEMORY) is preserved behind the `"reload_memory"` value for emergency rollback only. Comment block at `:130-145` and `:301-317` documents that the previous default produced the Layer 3 toggle revert regression.
- Verified live in logs: every `LAYER_STATE_SYNC` emission shows `match=true` (heartbeat is healthy with no drift):

```text
2026-05-02 11:35:44.435 | INFO | src.core.layer_manager:_sync_state_with_disk:338 | LAYER_STATE_SYNC | match=true disk={1: True, 2: False, 3: False} memory={1: True, 2: False, 3: False} | no_ctx
2026-05-02 11:36:44.436 ... match=true ...
2026-05-02 11:37:44.438 ... match=true ...
2026-05-02 11:38:44.439 ... match=true ...
2026-05-02 11:39:44.441 ... match=true ...
```

(60-second cadence as configured.)

### Toggle audit trail (live evidence from `data/logs/workers.log`)

```text
2026-05-02 11:22:44.923 | WARNING | LAYER_TOGGLE | layer=1 from=False to=True reason=unspecified actor=system
2026-05-02 11:22:46.951 | INFO    | LAYER_STATE_PERSIST_OK | layer_active={1: True, 2: True, 3: False} user_stopped=False
2026-05-02 11:22:46.952 | WARNING | LAYER_TOGGLE | layer=2 from=False to=True reason=unspecified actor=system
2026-05-02 11:22:48.954 | INFO    | LAYER_STATE_PERSIST_OK | layer_active={1: True, 2: True, 3: True} user_stopped=False
2026-05-02 11:22:48.954 | WARNING | LAYER_TOGGLE | layer=3 from=False to=True reason=unspecified actor=system
2026-05-02 11:26:52.917 | INFO    | LAYER_STATE_PERSIST_OK | layer_active={1: True, 2: False, 3: False} user_stopped=True
2026-05-02 11:26:52.917 | WARNING | LAYER_TOGGLE | layer=3 from=True to=False reason=telegram_dash_stop_trading actor=telegram_user:<REDACTED_CHAT_ID> cascade_root=2
2026-05-02 11:26:52.918 | WARNING | LAYER_TOGGLE | layer=2 from=True to=False reason=telegram_dash_stop_trading actor=telegram_user:<REDACTED_CHAT_ID> cascade_root=2
```

`stop_layer(2)` cascaded to layer 3 first (per `src/core/layer_manager.py:565-578` — cascade stops higher layers first then lower).

---

## 4. Gate enforcement points

### Layer 2 — Brain CALL_A enforcement

- Brain cycle loop: `src/core/layer_manager.py:712` — `while self._layer_active[2]:` (in `_brain_review_loop`). When Layer 2 turns off, the next loop iteration exits.
- Cycle entry: `_run_brain_cycle()` at `:726`. Dispatches Call A (`:744-756`) or Call B based on `self._call_type`.
- **There is NO explicit `is_layer_active(2)` check inside Call A's strategist code.** The brain loop is gated by `self._layer_active[2]` at the loop boundary; once the loop exits, Call A doesn't fire. Verified by grep: `grep is_layer_active(2)` yields zero hits in `src/brain/`.
- Inside Call A, after the plan is built, **Layer 3** is checked before execution (`:784` — `if self._layer_active[3]:`); plans built without L3 produce `BRAIN_TRADES_DROPPED | layer=3_inactive ...` at `:830-833`.

### Layer 3 — OrderService enforcement (`src/trading/services/order_service.py`)

- **Hard gate function:** `_assert_layer3_allows(...)` at lines 198-397 (called from `place_order` before `ORDER_START`).
- **Live LM check:** `:325` — `live_l3 = bool(lm.is_layer_active(3))`.
- **Race check (snapshot vs live):** `:329-363` — when `purpose == "layer3_entry"` AND a `layer_snapshot` was supplied, comparing `snap_l3 = bool(layer_snapshot.is_layer_active(3))` against `live_l3` and raising `Layer3RaceError` (with `ORDER_REJECT_LAYER3_RACE` log) on mismatch.
- **Hard gate when L3 OFF:** `:366-391` — emits `ORDER_REJECT_LAYER3_OFF | link_id=... reason="Layer 3 disabled"` and raises `Layer3DisabledError`. `purpose == "layer3_entry"` is unconditionally gated; `force=True` does not bypass for that purpose.
- **`force=True` operator override path:** `:392-397` — emits `ORDER_LAYER3_OFF_FORCED ... reason=operator_override` at WARNING for telegram_manual / mcp_tool when L3 is off.
- **Boot-window policy:** `:241-323` — when LM is not yet attached:
  - Past `lm_attach_deadline_sec` → all rejected (`ORDER_GATE_LM_DEADLINE_EXCEEDED`).
  - Within deadline + gated purpose (`_GATED_PURPOSES`) → rejected (`ORDER_REJECT_LM_BOOT`).
  - Within deadline + Layer 4 management purpose → allowed (single `ORDER_GATE_NO_LM | reason=layer_manager_not_attached_yet | action=allow_layer4_only` at WARNING).
- **Snapshot wiring upstream:**
  - `src/core/layer_manager.py:1661-1679` — `snapshot_layer_state()` returns frozen `LayerSnapshot` (`MappingProxyType`).
  - `src/workers/strategy_worker.py:1149-1150` — `_lm.snapshot_layer_state() if _lm and hasattr(_lm, "snapshot_layer_state") else None`.
  - `src/workers/strategy_worker.py:1530` — passed via `layer_snapshot=_layer_snapshot` to `place_order`.
  - `src/trading/services/order_service.py:504` — `layer_snapshot=layer_snapshot` arg in `_assert_layer3_allows` call.

### StrategyWorker — Layer 3 gate on `_strategy_hints` write

- `src/workers/strategy_worker.py:798` — `if not layer_manager or not layer_manager.is_layer_active(3): ...` (early skip).
- Comment at `:821` — `# the is_layer_active(3) gate so ScannerWorker sees consensus even` (consensus stays gated; hints stay gated at `:825`).

### Layer 4 — ProfitSniper / PositionWatchdog

- **Status:** ProfitSniper does NOT consult `is_layer_active(4)` (no Layer 4 in the live state file — only layers 1, 2, 3 exist).
- Verified by grep: `grep is_layer_active /home/inshadaliqbal786/trading-intelligence-mcp/src/workers/profit_sniper.py /home/inshadaliqbal786/trading-intelligence-mcp/src/workers/position_watchdog.py` — zero matches.
- Per `src/core/types.py:101` and `src/core/layer_manager.py:1561` ("Layer 8 forward-compat: can ProfitSniper/Watchdog intervene?"), Layer 4 is a forward-compat naming convention. The active gate IS Layer 3: when L3 toggles off, OrderService rejects ProfitSniper / Watchdog placements unless `purpose` is in the Layer-4 management whitelist.
- **OrderService Layer-4 path:** see `_GATED_PURPOSES` excluded in §4 above — `layer4_close` and `layer4_sl` purposes are allowed during the boot window; once LM is attached and L3 is OFF, those purposes route through the `force` / `purpose` checks at `:369` (only `layer3_entry` is unconditionally rejected; close/SL paths can still fire when permissioned).

### Cold-start gate (additional, Phase 4 of Layer 1 restructure)

- `src/core/layer_manager.py:146-173` — `self._cold_start_resume_done: bool = True` (default fail-open).
- Worker-side check pattern (`getattr(..., default=True)`) referenced in comment at `:170`. Workers emit `LAYER1{B,C,D}_TICK_SKIP | reason=cold_start_boundary_pending` while the flag is `False`.
- `CYCLE_RESUME` log at `:545` is emitted exactly when `_cold_start_resume_done` flips back to True.

---

## 5. Telegram `/layer` commands — handler file:line, supported commands, permission gating

- **There is NO direct `/layer` command.** Layer toggling is exposed through the dashboard inline-button callbacks in `src/telegram/handlers/control_handler.py`.
- **Callback handler:** `src/telegram/handlers/control_handler.py:230-373` — `control_callback`. Registered in `src/telegram/handlers/dashboard_handler.py:2336-2339` with `pattern="^(layer_|emergency_|view_|brain_interval_|capital_|mode_)"`.
- **Supported callbacks** (from `control_handler.py` docstring lines 8-14 + dispatcher):
  - `layer_start_1` / `layer_start_2` / `layer_start_3` → `control_handler.py:248-267` — calls `layer_manager.start_layer(layer, reason="telegram_control_start", actor=f"telegram_user:{user_id}")`.
  - `layer_stop_1` / `layer_stop_2` / `layer_stop_3` → `:269-297` — calls `layer_manager.stop_layer(layer, reason="telegram_control_stop", actor=f"telegram_user:{user_id}")`. Cascade preview at `:274-280`.
  - `emergency_close` → `:299-326` — calls `layer_manager.emergency_close_all(reason="telegram_control_emergency", actor=f"telegram_user:{user_id}")`.
  - `view_plan` → `:329-330` (helper at `:378-395`).
  - `view_positions` → `:333-336` (helper at `:400-430`).
  - `brain_interval_60` / `brain_interval_180` / `brain_interval_300` → `:338-352` — sets `layer_manager.brain_interval_seconds`.
  - `capital_*` → `_handle_capital_callback` (`:539-562`).
  - `mode_*` → `_handle_mode_callback` (`:602-642`).
- **Slash commands that bring up the dashboard with these buttons:**
  - `/control`, `/dashboard` → `dashboard_handler.py:2321-2322` — `CommandHandler("control", control_command)` / `CommandHandler("dashboard", control_command)`.
  - `/stopdash` → `:2323`, `/positions` → `:2324`, `/performance` → `:2325`, `/plan` → `:2326`, `/workers` → `:2327`, `/capital` → `:2334`, `/mode` → `:2335`.
- **Top-level `/emergency` command:** `src/telegram/bot.py:138` — `app.add_handler(CommandHandler("emergency", self.emergency_handler.execute))`. This is the slash-command alternative to the inline button.

### Permission gating

- **Auth class:** `src/telegram/auth.py:9-33` — `TelegramAuth` reads `settings.alerts.chat_id` (single chat ID) at construction (`:18-23`).
- **Check:** `:25-29` — `is_authorized(chat_id)`: `if not self.authorized_chat_ids: return True  # No restriction if no IDs configured`. Otherwise `chat_id in self.authorized_chat_ids`.
- **Where invoked (selected):**
  - `src/telegram/bot.py:293` — `if not self.auth.is_authorized(chat_id): ... await update.message.reply_text("Unauthorized.")` for `/start`.
  - `src/telegram/bot.py:315, 352, 384, 440, 476, 645` — same pattern for other commands and the inline callback handler.
- **Important gap:** the `control_callback` in `src/telegram/handlers/control_handler.py:230-373` does NOT call `is_authorized` itself. It relies on the upstream Telegram bot wrapper having filtered, OR — if `authorized_chat_ids` is empty — it permits all. Verified by grep: `grep is_authorized src/telegram/handlers/control_handler.py` returns zero matches.
- **Effective permission model:** single `chat_id` from `settings.alerts.chat_id`. If unset, all chats can toggle layers. The actor in the audit trail is `telegram_user:{from_user.id}` (passed through `start_layer`/`stop_layer`/`emergency_close_all` args). The 2026-05-02 11:26:52 stop event in §3 above was attributed to `telegram_user:<REDACTED_CHAT_ID>`.

---

## 6. Gaps

- No top-level `/layer` slash command; toggling is button-based via `/control` or `/dashboard`. An operator running scripts via the slash interface alone cannot toggle layers without going through the inline keyboard.
- `control_callback` does not re-verify `is_authorized` — it inherits the upstream filter. If a future telegram API change routes callbacks past the auth check, the callback would accept any chat.
- Layer 4 is a naming convention only; no `_layer_active[4]` exists. References to "Layer 4" in OrderService comments (`_GATED_PURPOSES`, `layer4_close`, `layer4_sl`) describe purpose tags, not a fourth toggleable layer.


=====================================================================
## FILE: N1_e2e_trade_trace.md
=====================================================================

# N1 — END-TO-END TRADE TRACE

**Collected:** 2026-05-02 ~11:47 UTC
**Snapshot DB:** /tmp/trading_snapshot_1777722335.db
**Logs:** brain.log, workers.2026-05-02_04-31-00_392071.log, workers.log

---

## NOTE ON did=d-1777720966952 (the example provided)

The provided did=d-1777720966952 (CALL_A at 11:22:46 UTC) is NOT a successful order
example. From workers.log:372 / brain.log:18308–18327:

- 11:22:50.089 — `STRATEGIST_PACKAGES_READ | call=CALL_A count=0 age_min_s=0 age_max_s=0`
  (zero packages — empty cache)
- 11:24:01.388 — `CLAUDE_CALL_OK | call_id=1 attempt=1/3 el=69537ms out=2439`
- 11:24:01.389 — `STRAT_DIRECTIVE | #1 sym=DYDXUSDT dir=Buy lev=2`
- 11:24:01.389 — `STRAT_DIRECTIVE | #2 sym=MONUSDT dir=Buy lev=2`
- 11:24:01.390 — `BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=2`
  (workers.log:372)
- 11:24:01.390 — `BRAIN_CYCLE_A_DONE | el=74437ms trades=2`

So Claude returned 2 trade directives, but the cycle was short-circuited at the
LayerManager because `_coin_packages` was empty. The 2 directives were dropped
before APEX/Gate/OrderService were called. **No SHADOW_ORD_SEND for this did.**

The most recent successful placed order is **ONDOUSDT did=d-1777703051893** at
06:26:33 UTC (workers.2026-05-02_04-31-00_392071.log:21601–21605). Trace below.

---

## SUCCESSFUL TRADE — did=d-1777703051893 (ONDOUSDT Buy)

All timestamps UTC. Source: brain.log + workers.2026-05-02_04-31-00_392071.log.

### 1. Brain CALL_A trigger
- `2026-05-02 06:24:11.893` — brain.log:18258
  `STRAT_CALL_A_START | did=d-1777703051893`

### 2. Package read from _coin_packages
- `2026-05-02 06:24:11.894` — brain.log:18259
  `STRATEGIST_PACKAGES_READ | call=CALL_A count=15 age_min_s=11 age_max_s=11 reader=brain_call_a`
  (15 packages, age 11s — fresh)

### 3. Prompt build start
- `2026-05-02 06:24:11.894` — implicit (immediately after package read)

### 4. Prompt build done
- `2026-05-02 06:24:12.844` — brain.log:18260
  `STRAT_PROMPT_BUILD | sections=35 | coaching=0ms regime_fetch=1ms regime_instr=0ms dir_perf=0ms trading_mode=0ms universe=1ms market_data=936ms data_lake=1ms xray=0ms sentiment=0ms regime_global=0ms held_symbols=3ms hints=0ms account=9ms`
- `2026-05-02 06:24:12.845` — brain.log:18261 `STRAT_PROMPT_SIZE | sections=35 chars=17423`
- `2026-05-02 06:24:12.845` — brain.log:18262
  `CLAUDE_PROMPT_TRIMMED | site=size reason=chars sections_before=35 sections_after=31 chars_before=17423 chars_after=17107 cap_sections=80 cap_chars=14000`
  (chars cap = 14000, but final 17107 — trimming sections to fit; see strategist.py:2184–2185)
- `2026-05-02 06:24:12.846` — brain.log:18264
  `PROMPT_BUILD_DONE | call=CALL_A coins=15 size_bytes=17137 sections=31 packages=15 elapsed_ms=952`

### 5. Claude CLI subprocess spawn
- `2026-05-02 06:24:12.848` — brain.log:18266
  `CLAUDE_CALL_START | call_id=50 in=17137 sys=8985 timeout=300s hash=52aba7c32c75`

### 6. Claude CLI first stdout
- NOT FOUND — searched: brain.log for CLAUDE_PROC_FIRST_STDOUT, CLAUDE_FIRST_TOKEN,
  stdout_so_far. Only stall warnings emit on silence; no first-byte log exists.
  Implied first stdout < 60s after spawn (no stall_60s log fired for this call's
  call_id=50 / pid range).

### 7. Claude CLI completion
- `2026-05-02 06:26:20.612` — brain.log:18270
  `CLAUDE_CALL_OK | call_id=50 attempt=1/3 el=127756ms out=2128 calls=50`
  (total elapsed 127.756s, 2128 chars output)

### 8. Response parse start
- `2026-05-02 06:26:20.612` — implicit (immediately after CLAUDE_CALL_OK)

### 9. Response parse done (validation)
- `2026-05-02 06:26:20.613` — brain.log:18271
  `STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Ranging global regime with fear sentiment (39). Asian late session with low volu'`
- `2026-05-02 06:26:20.613` — brain.log:18272
  `STRAT_DIRECTIVE | #1 sym=ONDOUSDT dir=Buy lev=2 rsn='STRONG ensemble 76.7, highest buy consensus (6.0 votes) across all candidates. R'`
- `2026-05-02 06:26:20.614` — brain.log:18273
  `STRAT_DIRECTIVE | #2 sym=NEARUSDT dir=Sell lev=2 rsn='GOOD ensemble 62.4, strong sell votes (3.45). A+ x-ray setup, pos=82% near range'`
- `2026-05-02 06:26:20.614` — brain.log:18274
  `STRAT_CALL_A_END | el=128721ms trades=2`

### 10. Decision routed (LayerManager queues for APEX)
- `2026-05-02 06:26:20.614` — workers.2026-05-02_04-31-00_392071.log:21482
  `BRAIN_CYCLE_A_DONE | el=128721ms trades=2 view='Ranging global regime with fear sentiment (39)...'`
- `2026-05-02 06:26:20.615` — workers.2026-05-02_04-31-00_392071.log:21483
  `DL_DECISION | type=call_a trades=2 acts=0 el=128721ms prompt=0`
- `2026-05-02 06:26:20.615` — workers.2026-05-02_04-31-00_392071.log:21484
  `BRAIN_DO_START | trades=2`
- `2026-05-02 06:26:20.622` — workers.2026-05-02_04-31-00_392071.log:21485
  `ENFORCER_STATE | trades=29 | wins=5 | losses=23 | wr=0.17 | strk=-12 | pnl=-0.90% | el=1 | sz_mult=0.75 | trigger=streak_boost`
  (Enforcer level 1 = capital preservation; lev limit=3; size mult 0.75)

### 11. APEX assembler invoked
- `2026-05-02 06:26:20.645` — workers.2026-05-02_04-31-00_392071.log:21487
  `APEX_PRICE_SOURCE | sym=ONDOUSDT source=ws price=0.27`
- `2026-05-02 06:26:20.739` — workers.2026-05-02_04-31-00_392071.log:21491
  `REGIME_CACHE_QUERY | sym=ONDOUSDT reader=apex_assembler hit=True ready=True cache_size=49`

### 12. APEX optimizer DeepSeek call start
- `2026-05-02 06:26:20.744` — workers.2026-05-02_04-31-00_392071.log:21492
  `APEX_TIER | tier=2 sym=ONDOUSDT sym_trades=0 regime_trades=59 regime=ranging action=regime_fallback`
- `2026-05-02 06:26:20.745` — workers.2026-05-02_04-31-00_392071.log:21493
  `APEX_REGIME | sym=ONDOUSDT sym_trades=0 regime_trades=59 regime=ranging`
  (tier=2 ⇒ regime fallback path; DeepSeek call still made — see ms=5479
  in APEX_TIMING below)

### 13. APEX optimizer DeepSeek response
- `2026-05-02 06:26:26.224` — workers.2026-05-02_04-31-00_392071.log:21497
  `APEX_TP_CAP | sym=ONDOUSDT qwen_tp=1.4% cap=1.4% cls=medium recTP=1.1% mult=1.30x | Capped to class-aware recTP`
- `2026-05-02 06:26:26.225` — workers.2026-05-02_04-31-00_392071.log:21498
  `APEX_OK | sym=ONDOUSDT dir=Buy sl=0.8% tp=1.4% cls=medium lev=2x no_param_changes conf=65% regime=ranging ms=1580`
- `2026-05-02 06:26:26.225` — workers.2026-05-02_04-31-00_392071.log:21499
  `APEX_TIMING | sym=ONDOUSDT el=5600ms | assemble=119ms deepseek=5479ms parse=0ms constraints=0ms`
  (DeepSeek elapsed 5479ms; total APEX 5600ms)

### 14. APEX gate decision
- `2026-05-02 06:26:33.978` — workers.2026-05-02_04-31-00_392071.log:21593
  `REGIME_CACHE_QUERY | sym=ONDOUSDT reader=apex_gate hit=True ready=True cache_size=49`
- `2026-05-02 06:26:33.980` — workers.2026-05-02_04-31-00_392071.log:21594
  `CONVICTION_WEIGHT | sym=ONDOUSDT regime=ranging trades=0 (< min 3) weight=0.75x(default)`
  (insufficient TIAS history → default 0.75x)
- `2026-05-02 06:26:33.984` — workers.2026-05-02_04-31-00_392071.log:21595
  `GATE_ADJUST | sym=ONDOUSDT changes=[conviction_cap=$369(w=0.8x)]`
- `2026-05-02 06:26:33.985` — workers.2026-05-02_04-31-00_392071.log:21596
  `GATE_TIMING | sym=ONDOUSDT el=9ms modifications=1`

### 15. APEX → Enforcer handoff (size enforcement)
- `2026-05-02 06:26:33.985` — workers.2026-05-02_04-31-00_392071.log:21597
  `ENFORCER_SIZE | sym=ONDOUSDT orig=$369 mult=0.75 final=$277`

### 16. Enforcer evaluation (within strategy_worker._execute_claude_trade)
- (Same line as #15 — Enforcer applies sz_mult=0.75 multiplier; lev=2
  passes since limit=3 at el=1.)

### 17. Enforcer → SL/TP validation handoff
- `2026-05-02 06:26:33.986` — workers.2026-05-02_04-31-00_392071.log:21598
  `XRAY_SLTP | sym=ONDOUSDT sl=$0.2678 struct_rr=0.78 rr_quality=skip`
- `2026-05-02 06:26:33.986` — workers.2026-05-02_04-31-00_392071.log:21599
  `XRAY_TP_NOTE | sym=ONDOUSDT tp=$0.2737 beyond_resistance=$0.2689 (TP may not be reached)`

### 18. TradeGate evaluation (Layer-3 boot/lm gate inside OrderService)
- IMPLICIT — passed (no ORDER_GATE_LM_DEADLINE_EXCEEDED, no ORDER_BLOCKED for did).
  See `_enforce_layer3_gate` in src/trading/services/order_service.py:251 — only
  emits a log when blocking. Pass-through is silent.

### 19. TradeGate → OrderService handoff
- `2026-05-02 06:26:33.987` — workers.2026-05-02_04-31-00_392071.log:21600
  `SHADOW_ORDER_RECEIVED | sym=ONDOUSDT side=Buy qty=2050.0 purpose=layer3_entry layer_snapshot_keys=[captured_at_monotonic,captured_at_wall,layer_active] force=False`

### 20. OrderService.place_order called (full args)
- `2026-05-02 06:26:33.987` — workers.2026-05-02_04-31-00_392071.log:21601
  `SHADOW_ORD_SEND | sym=ONDOUSDT side=Buy qty=2050.0 lev=2 sl=0.26784 tp=0.273699`
  Full args: symbol=ONDOUSDT side=Buy qty=2050.0 lev=2 sl=0.26784 tp=0.273699
  purpose=layer3_entry force=False

### 21. Pre-flight validation
- IMPLICIT — passed (place_order in src/trading/services/order_service.py
  does qty/leverage/SL/TP/min-order-value checks before sending; no
  warning/error logs emitted between SHADOW_ORD_SEND and SHADOW_ORD_RESP).

### 22. Shadow API call
- `2026-05-02 06:26:33.987` — same line as #20 (SHADOW_ORD_SEND fires
  immediately before HTTP POST to http://127.0.0.1:9090).

### 23. API response
- `2026-05-02 06:26:33.999` — workers.2026-05-02_04-31-00_392071.log:21602
  `SHADOW_ORD_RESP | sym=ONDOUSDT oid=0f9a8af3-703a-4468-af08-ad04e2666483 fill=0.270081 st=FILLED`
  (Shadow latency: 12ms send → response)

### 24. Position state update (TradeCoordinator registration)
- `2026-05-02 06:26:33.999` — workers.2026-05-02_04-31-00_392071.log:21603
  `COORD_REG | sym=ONDOUSDT src=claude_direct cat=claude_direct immunity=120s did= order_id=0f9a8af3-703a-4468-af08-ad04e2666483`
- `2026-05-02 06:26:34.000` — workers.2026-05-02_04-31-00_392071.log:21604
  `TradePlan: ONDOUSDT Buy target=$0.27 SL=$0.27 hold=45min trail@1.0% tier=claude_direct`

### 25. Order/thesis persisted to DB
- `2026-05-02 06:26:34.005` — workers.2026-05-02_04-31-00_392071.log:21605
  `THESIS_OPEN | id=1587 sym=ONDOUSDT dir=Buy ent=0.27 sl=0.26784 tp=0.273699 lev=2`
- `2026-05-02 06:26:34.408` — workers.2026-05-02_04-31-00_392071.log:21608
  `ProfitSniper: new position ONDOUSDT Buy @ $0.27, buffer pre-filled with 36 points, atr_entry=0.000430`
- `2026-05-02 06:26:34.775` — workers.2026-05-02_04-31-00_392071.log:21609
  `STRAT_EXEC | sym=ONDOUSDT dir=Buy qty=2050.0000 sz=$277x2 sl=$0.267840 tp=$0.273699`
- `2026-05-02 06:26:34.775` — workers.2026-05-02_04-31-00_392071.log:21610
  `BRAIN_DO_TRADE | sym=ONDOUSDT [1/2] el=875ms | apex_apply=74ms apex_ds=1580ms gate=9ms exec=791ms rsn=ok`

NOTE: orders table in snapshot DB is empty (0 rows). The order is recorded
only in thesis_manager and trade_coordinator state plus eventually in
trade_intelligence on close.

### 26. Telegram alert sent
- NOT FOUND for this entry trade — searched workers.* for ALERT_SENT around
  06:26:34. No entry-time Telegram alert fired (the only ALERT_SENT entries
  are critical/info-level on close). Closing alert at 06:29:10.277:
  `ALERT_SENT | level=info len=449 | tid=t-ONDOUSDT-mon wid=w-1777703349462`
  (general.log:60064)

### Position closed (post-trace)
- `2026-05-02 06:28:39.367` — workers.2026-05-02_04-31-00_392071.log:21732
  `TIME_DECAY_INIT | sym=ONDOUSDT dir=Buy sl=0.80% atr=0.16% cls=medium p_win=0.65 regime_conf=0.40 max_hold_s=2700 grace_s=120 atr_mult=2.00`
- `2026-05-02 06:29:10.282` — workers.2026-05-02_04-31-00_392071.log:21826
  `DL_TRADE | tid=t-ONDOUSDT-1777703350 sym=ONDOUSDT dir=Buy ent=0.27 ext=0.269719 pnl=-0.1040% pnl$=-0.2880 rsn=time_decay_p_win_low held=2.6min`
- `2026-05-02 06:29:10.338` — workers.2026-05-02_04-31-00_392071.log:21834
  `TIAS_SAVE | id=821 sym=ONDOUSDT dir=Buy pnl=-0.10% win=False regime=ranging rsi=60.727209`

---

## BLOCKED TRADE — did=d-1777698125354 (ONDOUSDT XRAY_DIR_BLOCK)

Source: workers.2026-05-02_04-31-00_392071.log

### Steps 1-13 (similar — APEX optimization completed)
- `2026-05-02 05:03:46.715` — line 5956
  `APEX_OK | sym=ONDOUSDT dir=Buy sl=0.3% tp=0.5% cls=low lev=3x sz=$500→$300 conf=60% regime=ranging ms=1941 | did=d-1777698125354`

### Step 14 — XRAY direction block (in strategy_worker._execute_claude_trade:1251)
- `2026-05-02 05:03:47.691` — line 5980
  `XRAY_DIR_BLOCK | sym=ONDOUSDT chosen=Buy rr_long=0.1 rr_short=1.5 ratio=21.7x | did=d-1777698125354`
  (Block reason: XRAY's RR for short was 21.7× the RR for long → direction
  contradiction with Claude/APEX's Buy. Trade dropped at strategy_worker
  level. No SHADOW_ORD_SEND for this did.)

There is no APEX_BLOCKED, GATE_BLOCK, ENFORCER_BLOCK, OrderService block,
or Bybit-side reject for this trace — the block is purely the X-RAY
direction filter inside strategy_worker.

A second blocked example (ENFORCER): same did=d-1777703051893 second
directive NEARUSDT was blocked by Enforcer:
- `2026-05-02 06:26:34.854` — workers.2026-05-02_04-31-00_392071.log:21615
  `STRAT_EXEC_BLOCKED | sym=NEARUSDT dir=Buy rsn='PRESERVATION: leverage=5 exceeds limit of 3x (PnL=-0.90%)'`
- `2026-05-02 06:26:34.854` — workers.2026-05-02_04-31-00_392071.log:21616
  `TRADE_SKIP | sym=NEARUSDT rsn=enforcer_block detail='PRESERVATION: leverage=5 exceeds limit of 3x (PnL=-0.90%)'`
- `2026-05-02 06:26:34.855` — workers.2026-05-02_04-31-00_392071.log:21617
  `BRAIN_DO_TRADE | sym=NEARUSDT [2/2] el=79ms | apex_apply=70ms apex_ds=2099ms gate=8ms exec=1ms rsn=enforcer_block`

(NEARUSDT was APEX-flipped Sell→Buy at conf=95% with lev=5 from APEX, but
Enforcer level=1 caps lev at 3 → blocked.)

---

## CLAUDE STALL/TIMEOUT TRACE — pid=17370 (CALL_A did=d-1777702618197)

No full CLAUDE_CALL_TIMEOUT in 2026-05-01..02; the closest stall (recovered
within timeout) is below. Searched: brain.log for CLAUDE_CALL_TIMEOUT,
CLAUDE_PROC_TIMEOUT_PARTIAL with date-prefix `^2026-05-0[12]` — zero hits.
20 stall_120s events did fire in 24h (all eventually recovered — workers
log shows CLAUDE_CALL_OK after each).

### Sequence
- `2026-05-02 06:16:58.197` — brain.log:18241
  `STRAT_CALL_A_START | did=d-1777702618197`
- `2026-05-02 06:16:58.546` — brain.log:18248
  `STRAT_CALL_A | chars=17192`
- `2026-05-02 06:16:58.547` — brain.log:18249
  `CLAUDE_CALL_START | call_id=49 in=17192 sys=8985 timeout=300s hash=e507ed26d18e | did=d-1777702618197`
- `2026-05-02 06:16:58.576` — brain.log:18250
  `CLAUDE_PROC_SPAWNED | pid=17370 spawn_ms=19`
- `2026-05-02 06:17:58.605` — brain.log:18251
  `CLAUDE_PROC_STALL_60S | pid=17370 elapsed=60s stdout_so_far=0 timeout_in_s=240`
  (60s of zero stdout — INFO level, claude_code_client.py:1201)
- `2026-05-02 06:18:58.621` — brain.log:18252
  `CLAUDE_PROC_STALL_120S | pid=17370 elapsed=120s stdout_so_far=0 timeout_in_s=180 state=S wchan=ep_poll`
  (120s — WARNING level; process state=S(sleeping) wchan=ep_poll waiting on
  network/file event — likely Claude API responding slowly)
- `2026-05-02 06:19:11.884` — brain.log:18253
  `CLAUDE_CALL_OK | call_id=49 attempt=1/3 el=133327ms out=2112 calls=49 | did=d-1777702618197`
  (Recovered after 133s — under the 300s timeout. No retry; no kill.)

For a true CLAUDE_CALL_TIMEOUT example (older — last in brain.log):
- `2026-04-23 13:36:15.100` (brain.log)
  `CLAUDE_CALL_TIMEOUT | call_id=9 attempt=1/3 timeout=300s err='claude CLI timed out after 300s' | did=d-1776950222428`
- followed by `CLAUDE_RETRY | call_id=9 attempt=1/3 err='claude CLI timed out after 300s' interval=4.0s`
- spawn `CLAUDE_PROC_SPAWNED | pid=6340 spawn_ms=319`
- 5 min later `CLAUDE_PROC_KILLED | pid=6340`
- `2026-04-23 13:42:04.934` `CLAUDE_CALL_TIMEOUT | call_id=9 attempt=2/3 timeout=300s`
- (3-attempt retry ladder: 4s/8s sleep between; 300s timeout each per
  config.toml [brain].claude_cli_timeout_seconds=300)


=====================================================================
## FILE: N2_stage2_config.md
=====================================================================

# N2 — Stage 2 Configuration

**Collected:** 2026-05-02 ~11:47 UTC
**Source:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` and source files.

---

## A. config.toml — `[brain]` (verbatim, lines 162–223)

```toml
[brain]
# Claude Code CLI — no API key needed, no budget limit
# Uses existing Claude Max subscription ($0 per call)
enabled = true
use_claude_code = true

# Definitive-fix Phase 6 (2026-04-28): cold-start completeness gate.
# Forensic E.2.4 captured first-cycle packages at completeness=0.67
# (XRAY/regime/F&G caches still warming up) — auto-execute fired and
# placed losing trades on incomplete data. The gate fires BEFORE
# Claude is called: the cycle is short-circuited and a Telegram alert
# warns the operator. ``boot_grace_*`` is the stricter gate during
# the first ``boot_grace_period_sec`` seconds after process start.
# Layer 1 restructure Phase 7 — when true, the strategist reads
# per-coin sections from layer_manager._coin_packages instead of
# querying 12 services per cycle. Set false to fall back to the
# legacy service-query path during Phase 9 observation if a
# regression is detected.
use_packages = true
# Phase 9 cutover (2026-05-01): flipped default to true. The strategist
# surfaces the Phase 3/4 briefing fields (state_label, action_hint,
# interestingness_score, votes block) in the per-coin TRADE CANDIDATES
# block AND extends TRADE_SYSTEM_PROMPT with one new section that
# teaches Claude how to read those fields. Set false to roll back to
# the legacy prompt shape instantly.
surface_briefing_fields = true
# Strategic review interval (seconds) — alternating Call A (trades) / Call B (positions)
# 150s = 2.5 min between calls, giving 5 min per call type
strategic_interval = 150
# Watchdog Claude review interval (seconds) — reviews positions every 30s
watchdog_interval = 30
# Legacy settings kept for backward compatibility
analysis_interval = 900
signal_triggered = true
min_signal_confidence = 0.45
max_calls_per_hour = 30
model = "claude-sonnet-4-20250514"
max_tokens = 4096
temperature = 0.3

# Claude CLI subprocess timing (Phase 2 session-stability fix — Y-22 + timeout retune)
# Hard cap on one Claude CLI invocation. Was hardcoded 300 in manager.py.
claude_cli_timeout_seconds = 300
# Retries after failure (non-retryable errors — auth, billing — still skip retry).
claude_cli_max_retries = 2
# Floor between consecutive Claude CLI invocations (adaptive interval).
claude_cli_min_interval = 2.0
# Backoff base for timeout-path retries: sleep = (attempt+1) * base seconds.
# 10 → ladder 10s/20s/30s. Was hardcoded 30 → 30s/60s/90s.
# Lowering halves the brain-outage window after a single timeout.
claude_cli_retry_timeout_backoff_base_seconds = 10
# Phase 3 (Brain credentials) — pre-flight refresh margin in seconds.
# Trigger an OAuth refresh if the access token expires within this window;
# if the refresh fails AND we are inside the margin, raise
# CredentialRefreshError instead of spawning a doomed subprocess.
credential_refresh_margin_seconds = 600
# Phase 3 (Brain credentials) — refresh attempt budget per call.
# 3 attempts with exponential backoff (1s/3s/7s) before giving up.
credential_refresh_max_attempts = 3
# Cap on watchdog events injected into the Call A URGENT prompt.
# Defence-in-depth — EventBuffer already truncates at 3000 chars.
prompt_event_buffer_max_events = 20
```

### `[brain.cold_start_protection]` (verbatim, lines 226–237)

```toml
# Definitive-fix Phase 6 — cold-start completeness gate.
[brain.cold_start_protection]
enabled = true
min_avg_completeness = 0.85
min_per_package_completeness = 0.75
# Phase 7 of the 1D briefing rewrite — lowered from 3 to 1. The gate's
# purpose is CACHE-WARMUP safety, not minimum-cohort enforcement. One
# well-formed package proves caches are warm. The completeness floors
# (min_avg_completeness, boot_grace_completeness) still detect
# cache-degradation. See dev_notes/phase7_1d_briefing/decision_record.md.
min_qualified_packages = 1
boot_grace_period_sec = 600
boot_grace_completeness = 0.95
```

---

## B. NOT FOUND — `[claude_code_client]`, `[strategist]`

**Searched:** config.toml for `[claude_code_client]`, `[strategist]`,
`[claude]`. NOT FOUND — Claude CLI client settings are read from
`[brain]` (claude_cli_*, credential_refresh_*); strategist has no
dedicated section, all knobs are in `[brain]` or `[scanner.briefing]`.

---

## C. Hardcoded values — src/brain/strategist.py

(file size 2864 lines)

- `strategist.py:65` — `TRADE_SYSTEM_PROMPT = """You are an aggressive
  but intelligent crypto futures trader. ..."""` (multi-line constant;
  CALL_A system prompt at runtime sys=8985 chars)
- `strategist.py:150` — `POSITION_SYSTEM_PROMPT = """You are managing
  open crypto futures positions. ..."""`
- `strategist.py:171` — `STRATEGIST_SYSTEM_PROMPT = TRADE_SYSTEM_PROMPT`
- `strategist.py:180` — `BRIEFING_SYSTEM_PROMPT_SUFFIX = """ ..."""`
  (appended to TRADE_SYSTEM_PROMPT when `surface_briefing_fields=true`)
- `strategist.py:576` — `_regime_confidence = 0.5` (default when regime
  service returns None)
- `strategist.py:577` — `_fear_greed_value = 50` (neutral default)
- `strategist.py:691,1719` — `included_count = 0`, `skipped_count = 0`
- `strategist.py:692,1720` — counters init
- `strategist.py:708,1754,2649` — `rsi = 50` (neutral default)
- `strategist.py:709,1755,2650` — `macd_hist = 0`
- `strategist.py:710,1756` — `adx = 0`
- `strategist.py:1145,2072` — `deployed = 0.0`
- `strategist.py:1317` — `LABEL_NO_TRADEABLE_STATE = "NO_TRADEABLE_STATE"`
- `strategist.py:1563` — `_regime_confidence = 0.5` (CALL_B path)
- `strategist.py:1564` — `_fear_greed_value = 50` (CALL_B path)
- `strategist.py:1659` — `_packages_count = 0`
- `strategist.py:2184` — `_SECTION_CAP = 80` (max sections per prompt)
- `strategist.py:2185` — `_CHAR_CAP = 14000` (max chars; live runs trim
  to ~14000 — see brain.log:18262 CLAUDE_PROMPT_TRIMMED `cap_chars=14000`)
- `strategist.py:2319` — `sl_consumed = 0.0`

(Live observation: section count cap 80 not exceeded in observed
window; char cap 14000 was hit and trimmed in CALL_A
did=d-1777702618197 on 2026-05-02 06:16:58 — `chars_before=17506 →
chars_after=17162`. Trim algorithm prunes lower-priority sections
until under one of the caps.)

---

## D. Hardcoded values — src/brain/claude_code_client.py

(file size 1465 lines)

### Module-level constants
- `claude_code_client.py:48` —
  `_NON_RETRYABLE = frozenset([...])` (auth/billing error tags that skip retry)
- `claude_code_client.py:61` — `_PROJECT = str(Path(__file__).resolve().parents[2])`
- `claude_code_client.py:62` — `_HOME = os.environ.get("HOME") or str(Path.home())`
- `claude_code_client.py:63` — `_CREDENTIAL_PATH = Path(_HOME) / ".claude" / ".credentials.json"`
- `claude_code_client.py:66` — `_OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"`
- `claude_code_client.py:67` — `_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"`
- `claude_code_client.py:70` — `_AUTH_BACKOFF_SCHEDULE = [300, 600, 1200, 2400, 3600]`
  (5min/10min/20min/40min/60min ladder when auth-fails persistently)

### `__init__` defaults (claude_code_client.py:81–122)
- `timeout_seconds: int = 90` (overridden by config → 300)
- `max_retries: int = 2`
- `min_interval: float = 2.0`
- `retry_timeout_backoff_base_seconds: int = 30` (legacy default; config
  overrides to 10)
- `credential_refresh_margin_seconds: int = 600`
- `credential_refresh_max_attempts: int = 3`
- `stall_warn_buckets_seconds = (60.0, 120.0, 240.0)` — 60→INFO,
  120→WARNING, 240→ERROR

### Subprocess streaming constants
- `claude_code_client.py:932` — `_STALL_LOG_EVERY_S = 60.0` (cadence for
  CLAUDE_PROC_STALL warnings)
- `claude_code_client.py:935` — `_SUBPROC_POLL_INTERVAL_S = 0.05`
  (50ms polling cadence for chunked stdout reader)
- `claude_code_client.py:317` —
  `min_interval * (2 ** self._consecutive_failures), 30.0` (cap
  adaptive interval at 30s)
- `claude_code_client.py:336` —
  `backoff_s = max(int(reset_ts - time.time()), 300)` (usage-quota
  reset minimum backoff 300s)
- `claude_code_client.py:338` — `backoff_s = 3600` (default 1h fallback
  when usage reset unparseable)
- `claude_code_client.py:958` —
  `cmd = [self._claude_path, "-p", "--output-format", "text"]`
- `claude_code_client.py:977` — `preexec_fn=os.setsid` (process group
  isolation)


=====================================================================
## FILE: N3_layer3_config.md
=====================================================================

# N3 — Layer 3 Configuration

**Collected:** 2026-05-02 ~11:47 UTC
**Source:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` and source files.

---

## A. config.toml — `[apex]` (verbatim, lines 952–1009)

```toml
# =============================================================================
# APEX — Aggressive Profit Extraction & Exploitation (via OpenRouter)
# =============================================================================
[apex]
enabled = true
model = "deepseek/deepseek-v3.2"
fallback_model = "deepseek/deepseek-chat"
# Layer 3: was 30s; DeepSeek frequently responds at 30-32s causing APEX_TIMEOUT.
timeout_seconds = 60
max_tokens = 800
temperature = 0.2
max_position_size_usd = 1200
max_leverage = 5
min_tias_trades_for_optimization = 3
min_regime_trades_for_fallback = 10

# Guardrails
min_tp_pct = 0.3
gate_tp_floor_enabled = true
gate_trail_activation_floor_pct_of_tp = 15.0
gate_trail_distance_floor_pct = 40.0
gate_mode_override_enabled = true
gate_confidence_floor = 0.50
# Hard size-cap: APEX/conviction inflation cannot exceed 1.5× Claude's
# pre-APEX directive size. Gate CHECK 0 enforces this and logs
# CONVICTION_SIZE_CAP when it binds. Set 0 to disable.
gate_apex_size_cap_mult = 1.5

# Conviction Allocator
conviction_enabled = true
conviction_min_trades = 3

# Definitive-fix Phase 9 (2026-04-28) — flip discipline.
#   apex_min_flip_confidence: confidence floor (0..1) for any flip in
#     ranging / dead / unknown.
#   apex_block_flip_resize: when true, a flip cannot also change size
#     in the same call. One decision change per directive.
apex_min_flip_confidence = 0.90
apex_block_flip_resize = true

# Per-class TP cap multiplier (× recommended_tp_pct from volatility profiler).
[apex.tp_cap_multiplier_by_class]
dead = 1.2
low = 1.3
medium = 1.3
high = 1.4
extreme = 1.5
```

---

## B. config.toml — `[enforcer]` (verbatim, lines 789–831)

```toml
[enforcer]
# Enforcer v2 — PnL-Based Intelligent Throttling
enabled = true
check_interval_seconds = 60

# PnL-based thresholds (daily PnL %)
pnl_caution_pct = -2.0              # Below this → el=1 (capital preservation)
pnl_survival_pct = -5.0             # Below this → el=2 (survival)

# Size reduction for mild negative PnL
size_reduction_enabled = true
size_reduction_at_pnl_pct = 0.0     # Start reducing below this PnL %
size_reduction_factor = 0.75        # 25% smaller positions (0% to caution)

# Streak as secondary signal (only when PnL is negative)
streak_boost_threshold = -5         # 5-loss streak + negative PnL → immediate el=1

# Auto-recovery
max_enforcement_minutes = 45        # Auto-recover after stuck at el>=1
grace_period_minutes = 30           # Manual reset grace (full skip)

# Per-level restrictions
level_1_max_positions = 3
level_1_max_leverage = 3
level_1_min_score = 75
level_2_max_positions = 2
level_2_max_leverage = 3
level_2_min_score = 80
level_2_min_confluence = 7
level_2_min_rr = 3.0

# Legacy fields (kept for backward compatibility)
decay_minutes = 60
min_trades_per_hour = 20
min_profit_per_hour_pct = 5.0
min_win_rate = 0.45
min_signals_per_hour = 50
min_setups_to_brain_per_hour = 10
max_seconds_between_trades = 90
max_escalation_level = 5
force_trade_on_gap = true
rewards_enabled = true
hourly_report_enabled = true
```

---

## C. config.toml — `[fund_manager]` (verbatim, lines 754–787)

```toml
[fund_manager]
# Intelligent Fund Manager — 22-module capital management
enabled = true
check_interval_seconds = 60
starting_unlock_pct = 20
active_pool_pct = 70
aplus_reserve_pct = 20
emergency_reserve_pct = 10
profit_lock_pct = 50
trade_profit_lock_pct = 25
max_correlation_bucket_pct = 30
min_profitable_trade_fee_pct = 0.12

# ─── Phase 5 (post-Layer-1 fix): FundReconciler ──────────────────────
reconcile_enabled = true
reconcile_interval_seconds = 60
reconcile_drift_alert_threshold_pct = 5.0
reconcile_auto_correct = false
```

---

## D. config.toml — `[pnl_targets]` (verbatim, lines 638–644)

```toml
[pnl_targets]
# Daily PnL — AGGRESSIVE (paper trading)
daily_target_pct = 10.0
protect_threshold_pct = 7.0
caution_threshold_pct = -3.0
survival_threshold_pct = -7.0
halt_threshold_pct = -10.0
```

---

## E. NOT FOUND — `[trade_gate]`, `[order_service]`, `[shadow]`, `[pnl_manager]`

**Searched:** config.toml for `[trade_gate]`, `[order_service]`,
`[shadow]`, `[pnl_manager]`. NOT FOUND — these are coded in
src/* and read partially from `[risk]`, `[layer_manager]`,
`[general]` (shadow_api_url), and module-level constants.

For shadow URL/config: `[general].shadow_api_url = "http://127.0.0.1:9090"`
(line 12). For trade-gate boot deadlines:
`[layer_manager].lm_attach_deadline_sec = 60.0` (line 1246).
For order-service risk caps: `[risk]` block (lines 239–251).

---

## F. Hardcoded values — src/apex/optimizer.py

(file size 743 lines)

- `optimizer.py:50` — `self._client = qwen_client`
- `optimizer.py:51` — `self._assembler = assembler`
- `optimizer.py:52` — `self._settings = settings`
- `optimizer.py:78` — `_assemble_ms = 0.0` (timing init)
- `optimizer.py:79` — `_deepseek_ms = 0.0`
- `optimizer.py:80` — `_parse_ms = 0.0`
- `optimizer.py:81` — `_constraints_ms = 0.0`
- `optimizer.py:134` — `min_regime = getattr(self._settings,
  "min_regime_trades_for_fallback", 10)` (default 10)
- `optimizer.py:496` — `_sl_floor = 0.2` (% absolute SL floor)
- `optimizer.py:505,509` — `getattr(self._settings, "min_tp_pct", 0.3)`
- `optimizer.py:562` — `sl_pct=2.0` (placeholder, ignored when
  `is_fallback=True`)
- `optimizer.py:563` — `tp_pct=1.5` (placeholder, ignored when
  `is_fallback=True`)
- `optimizer.py:569` — `add_trigger_pct=0.0`
- `optimizer.py:570` — `add_size_pct=0`
- `optimizer.py:572` — `confidence=0.0`

---

## G. Hardcoded values — src/apex/gate.py

(file size 474 lines)

- `gate.py:42` — `self._services = services`
- `gate.py:43` — `self._settings = settings`
- `gate.py:46` — `self._conviction_cache_ttl: float = 300.0  # 5 minutes`
- `gate.py:74` — `cap_mult = 1.5` (fallback when settings missing)
- `gate.py:78` — `claude_orig = 0.0`
- `gate.py:91` — `f"capped=${max_allowed:.0f} mult={cap_mult}x"`
- `gate.py:109` — `max_concurrent = 5` (max open positions hard-coded)
- `gate.py:117` — `reduced = round(size * 0.3, 2)` (30% size reduction
  when at concurrency cap)
- `gate.py:126` — `available = 1000.0` (safe default when fund_manager
  unavailable)
- `gate.py:140` — `weight *= 1.20  # A+ setup: 20% boost`
- `gate.py:144` — `weight *= 0.90  # B setup: 10% reduction`
- `gate.py:146` — `weight *= 0.80  # C/D setup: 20% reduction`
- `gate.py:148` — `base_pct = 0.4  # base 40% of available`
- `gate.py:150` — `weighted_pct = max(0.05, min(weighted_pct, 0.40))`
  (floor 5%, cap 40%)
- `gate.py:169,180,308` — `trade["size_usd"] = round(size * 0.5, 2)`
  (50% size reduction in various RR-failure paths)
- `gate.py:189` — `min_size = 50.0` (USD min trade size)
- `gate.py:241` — `_floor_pct = getattr(self._settings,
  "gate_trail_activation_floor_pct_of_tp", 50.0)`
- `gate.py:246` — `min_activation = max(min_activation, 0.5)` (absolute
  floor 0.5%)
- `gate.py:259` — `_dist_floor = getattr(self._settings,
  "gate_trail_distance_floor_pct", 40.0)`
- `gate.py:283` — `_conf_floor = getattr(self._settings,
  "gate_confidence_floor", 0.50)`
- `gate.py:286` — `scale = max(0.3, apex_confidence / _conf_floor)`
- `gate.py:304` — `trade["size_usd"] = round(size * 0.25, 2)`
- `gate.py:317` — `abs(_tp - _sl) / max(_tp, _sl) < 0.001` (SL=TP collision)
- `gate.py:320,322` — `trade["take_profit_price"] = round(_tp * 1.02, 8)`
  / `* 0.98`
- `gate.py:424` — `weight = 0.75  # Not enough history — cautious default`
- `gate.py:447` — `profit_factor = 10.0  # Cap at 10 to avoid infinity`
- `gate.py:452` — `if profit_factor > 3.0: weight = 2.0`
- `gate.py:453–458` — conviction weight ladder

---

## H. Hardcoded values — src/trading/services/order_service.py

(file size 1156 lines)

- `order_service.py:73` — `_ORDER_LINK_ID_PREFIX = "ti"`
- `order_service.py:74` — `_ORDER_LINK_ID_LEN = 24` (uuid4 hex chars)
- `order_service.py:75` — `_ORDER_PLACE_RETRY_DELAY_S = 0.5`
- `order_service.py:76` — `_ORDER_PLACE_MAX_ATTEMPTS = 2` (initial + 1 retry)
- `order_service.py:245` — `deadline_s =
  float(self._settings.layer_manager.lm_attach_deadline_sec)` (config-driven)
- `order_service.py:560–561` — `max_pct =
  self._settings.risk.max_position_size_pct; max_usd = equity * (max_pct / 100)`
- `order_service.py:575` — `max_loss = equity * 0.02` (2% max loss per
  trade)
- `order_service.py:817` — `limit=10` (recent-orders helper limit)
- `order_service.py:851` — `@retry(max_attempts=2, delay=0.5,...)` decorator
- `order_service.py:909,938` — `@retry(max_attempts=2, delay=0.5)`
- `order_service.py:965,990` — `@retry(max_attempts=3, delay=1.0)` (other
  RPC calls — set_leverage, query_position)
- `order_service.py:1040` — `if leverage is not None and leverage >
  self._settings.risk.max_leverage:` (config max_leverage = 5)

---

## I. Hardcoded values — src/strategies/performance_enforcer.py

(file size 577 lines; init in __init__ around L40–L85)

- `performance_enforcer.py:44` — `self._trades_today = 0`
- `performance_enforcer.py:45` — `self._wins_today = 0`
- `performance_enforcer.py:46` — `self._losses_today = 0`
- `performance_enforcer.py:47` — `self._profit_today_pct = 0.0`
- `performance_enforcer.py:48` — `self._streak = 0`
- `performance_enforcer.py:51–52` — `{"Buy": {"wins": 0, "losses": 0},
  "Sell": {"wins": 0, "losses": 0}}`
- `performance_enforcer.py:64` — `self._pnl_caution_pct: float =
  getattr(_ecfg, "pnl_caution_pct", -2.0)`
- `performance_enforcer.py:65` — `self._pnl_survival_pct: float =
  getattr(_ecfg, "pnl_survival_pct", -5.0)`
- `performance_enforcer.py:67` — `self._size_reduction_at_pnl_pct: float
  = getattr(_ecfg, "size_reduction_at_pnl_pct", 0.0)`
- `performance_enforcer.py:68` — `self._size_reduction_factor: float =
  getattr(_ecfg, "size_reduction_factor", 0.75)`
- `performance_enforcer.py:69` — `self._streak_boost_threshold: int =
  getattr(_ecfg, "streak_boost_threshold", -5)`
- `performance_enforcer.py:73` — `self._l1_max_pos: int = getattr(_ecfg,
  "level_1_max_positions", 3)`
- `performance_enforcer.py:74` — `self._l1_max_lev: int = getattr(_ecfg,
  "level_1_max_leverage", 3)`
- `performance_enforcer.py:75` — `self._l1_min_score: int = getattr(_ecfg,
  "level_1_min_score", 80)` (note: config has 75, default code 80)
- `performance_enforcer.py:76` — `self._l2_max_pos: int = getattr(_ecfg,
  "level_2_max_positions", 2)`
- `performance_enforcer.py:77` — `self._l2_max_lev: int = getattr(_ecfg,
  "level_2_max_leverage", 3)`
- `performance_enforcer.py:78` — `self._l2_min_score: int = getattr(_ecfg,
  "level_2_min_score", 80)`
- `performance_enforcer.py:79` — `self._l2_min_confluence: int =
  getattr(_ecfg, "level_2_min_confluence", 7)`
- `performance_enforcer.py:80` — `self._l2_min_rr: float =
  getattr(_ecfg, "level_2_min_rr", 3.0)`
- `performance_enforcer.py:140` — `return self._size_reduction_factor`
- `performance_enforcer.py:142–149` — size-reduction ladder values 0.50,
  0.40, 0.25 for deeper losses
- `performance_enforcer.py:184–196` — recovery_stage 0/1/2 thresholds

---

## J. Hardcoded values — src/strategies/pnl_manager.py

(file size 449 lines)

- `pnl_manager.py:74,175,345` — `self.realized_pnl = 0.0`
- `pnl_manager.py:77,179,348` — `self._trades_today = 0`
- `pnl_manager.py:80,182` — `self._max_drawdown_today = 0.0`
- `pnl_manager.py:81,183` — `self._best_trade_pct = 0.0`
- `pnl_manager.py:82,184` — `self._worst_trade_pct = 0.0`
- `pnl_manager.py:83,185,380,389` — `self._streak_count = 0`
- `pnl_manager.py:85,187` — `self._avg_win_pct = 0.0`
- `pnl_manager.py:86,188` — `self._avg_loss_pct = 0.0`
- `pnl_manager.py:88,190` — `self._total_win_pnl = 0.0`
- `pnl_manager.py:89,191` — `self._total_loss_pnl = 0.0`
- `pnl_manager.py:165` — `self._persist_counter = 0`
- `pnl_manager.py:201` — `self.current_pnl_pct = 0.0`
- `pnl_manager.py:347` — `self.starting_equity = 0.0` (forces re-capture)

(All thresholds and PnL targets are read from `[pnl_targets]` config
section via the BookKeeper; pnl_manager itself only stores running
state — no thresholds hardcoded.)


=====================================================================
## FILE: N4_live_snapshots.md
=====================================================================

# N4 — Live State Snapshots

**Snapshot timestamp:** 2026-05-02 11:50:46 UTC (latest worker heartbeat in
workers.log line 1824)

**DB:** /tmp/trading_snapshot_1777722335.db
**Logs cutoffs:** workers.log line 1839 (~12:00 UTC); brain.log line 18327
(11:24:01.390 UTC). brain.log has been silent since 11:24 UTC — no CALL_A
or CALL_B events between 11:24:01 and snapshot time.

---

## A. Brain state

- **Last CALL_A:** `2026-05-02 11:22:46.952 UTC` did=d-1777720966952
  brain.log:18308 → ended 11:24:01.390 with trades=2 (DYDXUSDT Buy lev=2,
  MONUSDT Buy lev=2). Result: BRAIN_NO_PACKAGES (empty packages cache),
  trades_dropped=2 (workers.log:372).
- **Last CALL_B:** `2026-05-02 11:26:32.606 UTC` did=d-1777720966952
  workers.log:1135 → ended 11:26:32.663 with `BRAIN_CYCLE_B_SKIP |
  rsn='no open positions'` (workers.log:1171). Note: CALL_B re-uses the
  same did as CALL_A inside the cycle.
- **Alternation state (which is next):** A→B alternation observed:
  CALL_A at 11:22:46 → CALL_B at 11:26:32 (+~4min). With strategic_interval
  150s, next CALL_A would be expected around 11:29:02 UTC. NOT FOUND —
  no further STRAT_CALL_A_START or STRAT_CALL_B_START in brain.log
  after 11:24:01. Brain has been quiet since (cycle_active=False — see
  `WORKER_LIVENESS_HEARTBEAT | total=19 healthy=14 ... cycle_active=False`
  appearing every 30s from 11:24+).
- **Pending actions queue:** NOT FOUND — no actions since 11:26:32. The
  2 CALL_A directives at 11:24:01 (DYDXUSDT, MONUSDT) were dropped by the
  BRAIN_NO_PACKAGES gate before APEX. Queue is empty.

---

## B. APEX state

- **Last optimization:** `2026-05-02 06:26:33.827 UTC`
  workers.2026-05-02_04-31-00_392071.log:21559
  `APEX_FLIP | sym=NEARUSDT claude=Sell apex=Buy sl=0.3% tp=0.5% cls=low
  sz=$500→$500 mode=fixed conf=95% regime=ranging ms=2099`
  (last APEX_OK at 06:26:26.225 for ONDOUSDT).
- **In-flight:** None. APEX has not been invoked since 06:26 UTC because
  Brain has not produced executable directives (BRAIN_NO_PACKAGES at 11:24
  bypassed APEX entirely).
- **APEX_FLIP rate over last hour:** 0 (no APEX activity at all in
  10:50–11:50 UTC window). Over the entire 24h window:
  - APEX_FLIP: 7 events
  - APEX_FLIP_RESIZE_BLOCKED: 7 events
  - APEX_FLIP_BLOCKED: 3 events
  All 24h events occurred 02:44 UTC ↔ 06:26 UTC (workers.* logs).

---

## C. Enforcer state

- **Today's PnL (DB query):**
  ```sql
  SELECT * FROM daily_pnl WHERE date = '2026-05-02';
  -- → 2026-05-02 | start=0.0 | end=6149.85 | realized=-1.0025 |
  --     trades=29 | wins=5 | losses=24 | mdd=0.0
  ```
  Computed pnl_pct from trade_intelligence (2026-05-02 only):
  COUNT=29, SUM(pnl_pct)=-1.0025, SUM(pnl_usd)=2.34, wins=5.
  (Live ENFORCER_BEAT line 11:48:46 in workers.log:1773 reads
  `total=30T W=5 L=24 wr=16.7% strk=-13` — 30 trades = 29 from
  trade_intelligence + 1 ENFORCER counter increment timing diff.)
- **Consecutive losses:** strk=-13 (per ENFORCER_STATE line 11:48:46:
  `strk=-13`).
- **Active mode (level):** el=1 (capital preservation) — escalated
  `2026-05-02 11:22:45.990` from el=0 → el=1 (workers.log:245)
  `ENFORCER_LEVEL | old_el=0 new_el=1 | reason=streak_boost | pnl=-1.00%
  strk=-13`. Currently sz_mult=0.75. Level-1 caps: max_positions=3,
  max_leverage=3, min_score=75 (per [enforcer] config).
- **Coaching cache contents:** NOT FOUND — searched workers.log/brain.log
  for COACHING, COACH_CACHE, _coaching_cache. No emit observed; all
  STRAT_PROMPT_BUILD lines show `coaching=0ms` indicating empty/no-op.

---

## D. Gate state

- **Per-symbol cooldowns (from logs):**
  Recent SCANNER_LABELED (workers.2026-05-02_04-31-00_392071.log around
  06:29:00) tagged with `secondary=RECENT_LOSER_COOLDOWN`:
  - INJUSDT (rank=2, conf=0.60)
  - DYDXUSDT (rank=3, conf=0.55)
  - AXSUSDT (rank=4, conf=0.55)
  - DOGEUSDT (rank=5, conf=0.55)
  - SANDUSDT (rank=7, conf=0.55)
  - ALGOUSDT (rank=8, conf=0.55)
  This is the recent_failure_blocker_hours=1 from
  `[scanner.qualitative]`. NOT FOUND — explicit
  per-symbol cooldown timestamp emit (only labelled in scanner output).
- **Open position counts (DB):** `SELECT COUNT(*) FROM positions; → 0`.
- **Recent block reasons:**
  Last 4 ORDER_BLOCKED in workers.log (all from 05:10–06:01 UTC,
  outside 1h window before snapshot):
  - 05:10:34 INJUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=9848.2`
  - 05:10:35 ONDOUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=9849.3`
  - 06:01:57 AXSUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=12931.2`
  - 06:01:57 MANAUSDT — `lm_deadline_exceeded deadline_s=60.0 elapsed_s=12932.0`
  All 4 from `purpose=mcp_tool` (operator-driven via MCP, not Stage 2
  cycle). XRAY_DIR_BLOCK count last 24h = 20 events; STRAT_EXEC_BLOCKED
  enforcer leverage = 7 events.

---

## E. OrderService state

- **Last 5 orders placed (DB):** orders table is empty (0 rows in
  snapshot). Source-of-truth is logs:
  Last 5 SHADOW_ORD_RESP (in workers.2026-05-02_04-31-00_392071.log):
  1. 06:26:33.999 ONDOUSDT — oid=0f9a8af3-703a-4468-af08-ad04e2666483
     fill=0.270081 status=FILLED did=d-1777703051893
  2. 06:02:32.167 AXSUSDT — qty=401.5 lev=3 sl=1.3669854 tp=1.413885
     did=d-1777701650866
  3. 04:48:52.499 AXSUSDT — qty=402.3 lev=3 sl=1.3661926 tp=1.413065
     did=d-1777697151599
  4. 04:07:52.485 AXSUSDT — qty=218.1 lev=3 sl=1.3631205 tp=1.4098875
     did=d-1777694725555 (workers.2026-05-01_*.log:43126)
  5. 03:16:51.126 AXSUSDT — qty=218.4 lev=3 sl=1.3609403 tp=1.4076325
     did=d-1777691657786 (workers.2026-05-01_*.log:30758)
- **Last 5 blocks:** see D (4 ORDER_BLOCKED + 7 STRAT_EXEC_BLOCKED in
  24h). No blocks in last 5h.
- **Last 5 fails (Bybit error):** NOT FOUND in 2026-05-01..02 logs —
  searched `Retry exhausted`, `place_order after`, `BybitError`,
  `InvalidOrderError` filtered to last 24h: zero. (Last failures of
  this kind were 2026-04-26 LDOUSDT/INJUSDT — see general.log:42053.)

---

## F. Fund manager state

Most recent FUND_POOLS log line (workers.log:1824):
- `2026-05-02 11:50:46.474` — `FUND_POOLS | cap=1229.97 | available=1229.97
  | in_use=0.00`
  - cap = 1229.97 USDT
  - available = 1229.97 USDT
  - in_use = 0.00 USDT (no open positions)

Most recent FUND_RECONCILE (workers.log:1822):
- `2026-05-02 11:50:46.385` — `FUND_RECONCILE | bybit_total=6149.85
  bybit_available=6149.85 local_total=6149.85 local_cap=1229.97
  local_avail=1229.97 drift_pct=+0.00 auto_correct=false`
  - Last balance fetch from Bybit: 11:50:46.385 (bybit_total=6149.85)
  - Last drift: +0.00% (zero drift).
  - reconcile_interval_seconds=60 → next fetch ~11:51:46.

(Note the gap: total_equity=6149.85 USDT but fund_manager cap=1229.97 USDT
≈ 20% of equity. This is starting_unlock_pct=20 from [fund_manager]
config.)


=====================================================================
## FILE: N5_brain_cycles.md
=====================================================================

# N5 — Last 5 Brain Cycles Detailed

**Collected:** 2026-05-02 ~11:47 UTC
**Sources:** brain.log, workers.2026-05-02_04-31-00_392071.log,
claude_decisions table in /tmp/trading_snapshot_1777722335.db.

For each cycle: timestamp, packages count + age, prompt sections + chars,
Claude response time + first 200 chars verbatim, parsed directives, and
the routing outcome (placed / blocked / failed at which step + did=).

NOTE: brain.log went silent after 11:24:01 UTC (CALL_A
did=d-1777720966952). Most recent 5 CALL_A and 5 CALL_B span 06:19→11:24
UTC — listed below in REVERSE-CHRONOLOGICAL order (newest first).

---

## CALL_A #1 — d-1777720966952 (most recent)

- **STRAT_CALL_A_START:** `2026-05-02 11:22:46.952` (brain.log:18308)
- **Packages:** count=0 age_min_s=0 age_max_s=0
  (`STRATEGIST_PACKAGES_READ | call=CALL_A count=0` — empty cache)
- **Prompt:** sections=32 chars=4046 (`STRAT_PROMPT_SIZE`); not trimmed
  (under cap). Build: regime_fetch=3136ms market_data=1376ms total el=4532ms
- **Claude:** `CLAUDE_CALL_OK | call_id=1 attempt=1/3 el=69537ms out=2439`
- **Response first ~200 chars (claude_decisions.id=1232):**
  > "Ranging global regime with fear at 39. Account in critical drawdown
  > — pure capital preservation. Only taking 2 minimum-size mean-reversion
  > and momentum-continuation buys on MEDIUM vol coins. Avoiding "
- **Parsed directives:** trades=2 risk=cautious
  - #1 DYDXUSDT Buy lev=2 — `RSI=26 deeply oversold in ranging global regime…`
  - #2 MONUSDT Buy lev=2 — `ADX=50 strong trend + RSI=55 healthy momentum zone…`
- **Routing result:** BLOCKED at LayerManager
  (`BRAIN_NO_PACKAGES | reason=empty_packages_cache trades_dropped=2`,
  workers.log:372). Both directives DROPPED before APEX/Gate/OrderService.

## CALL_A #2 — d-1777703051893

- **STRAT_CALL_A_START:** `2026-05-02 06:24:11.893`
- **Packages:** count=15 age_min_s=11 age_max_s=11
- **Prompt:** sections=35 chars=17423 → trimmed → sections=31 chars=17107
  (CLAUDE_PROMPT_TRIMMED hit cap_chars=14000)
- **Claude:** `CLAUDE_CALL_OK | call_id=50 attempt=1/3 el=127756ms out=2128`
- **Response first ~200 chars:**
  > "Ranging global regime with fear sentiment (39). Asian late session
  > with low volume — not ideal for directional bets. Both directions
  > struggling badly. Capital preservation is priority. Taking only 2 m"
- **Parsed directives:** trades=2 risk=cautious
  - #1 ONDOUSDT Buy lev=2 — `STRONG ensemble 76.7, highest buy consensus (6.0 votes)…`
  - #2 NEARUSDT Sell lev=2 — `GOOD ensemble 62.4, strong sell votes (3.45). A+ x-ray setup…`
- **Routing result:**
  - ONDOUSDT: PLACED at 06:26:33.999 (oid=0f9a8af3-703a-4468-af08-ad04e2666483)
    `BRAIN_DO_TRADE | sym=ONDOUSDT [1/2] el=875ms ... rsn=ok`
  - NEARUSDT: BLOCKED at Enforcer — APEX-flipped Sell→Buy at conf=95% lev=5,
    Enforcer level=1 caps lev≤3 →
    `BRAIN_DO_TRADE | sym=NEARUSDT [2/2] el=79ms ... rsn=enforcer_block`

## CALL_A #3 — d-1777702618197

- **STRAT_CALL_A_START:** `2026-05-02 06:16:58.197`
- **Packages:** count=15 age_min_s=178 age_max_s=178
- **Prompt:** sections=37 chars=17506 → trimmed → 31 / 17162
- **Claude:** `CLAUDE_CALL_OK | call_id=49 attempt=1/3 el=133327ms out=2112`
  (stalled 60s+120s on this call — pid=17370 — recovered before timeout)
- **Response first ~200 chars (claude_decisions.id=1229):**
  > "Ranging global regime with fear (F&G=39). Asian late session with
  > low volume - range building expected. Both directions technically
  > valid but performance data strongly favors caution. INJUSDT stands o"
- **Parsed directives:** trades=2 risk=cautious
  - #1 INJUSDT Buy lev=2 — `TRENDING_UP regime, score 76.1 STRONG ensemble, BUY=4.10 vs SELL=0, ADX=53…`
  - #2 NEARUSDT Sell lev=2 — `RANGE_FADE_SHORT, no cooldown tag, SELL=3.45 unanimously…`
- **Routing result:**
  - INJUSDT: BLOCKED by XRAY at strategy_worker
    `BRAIN_DO_TRADE | sym=INJUSDT [1/2] el=83ms ... rsn=xray_dir_block`
  - NEARUSDT: BLOCKED by Enforcer (APEX flipped to Buy lev=5, capped at 3)
    `BRAIN_DO_TRADE | sym=NEARUSDT [2/2] el=83ms ... rsn=enforcer_block`

## CALL_A #4 — d-1777702119444

- **STRAT_CALL_A_START:** `2026-05-02 06:08:39.444`
- **Packages:** count=15 age_min_s=279 age_max_s=279 (4.6 min stale)
- **Prompt:** sections=40 chars=17564 → trimmed → 31 / 17115
- **Claude:** `CLAUDE_CALL_OK | call_id=47 attempt=1/3 el=118837ms out=1850`
- **Response first ~200 chars (claude_decisions.id=1227):**
  > "Ranging global regime with fear sentiment (F&G=39). Asian late
  > session with low volume — not ideal for directional bets. 18% win
  > rate and -11 streak demand extreme caution. Only taking 2 trades with m"
- **Parsed directives:** trades=2 risk=cautious
  - #1 INJUSDT Buy lev=3 — `TRENDING_UP 100% confidence, strongest ensemble 76.1 STRONG…`
  - #2 MANAUSDT Buy lev=3 — `RANGE_FADE_LONG with STRONG ensemble 71.6. BUY=5.80 vs SELL=0.00…`
- **Routing result:**
  - INJUSDT: BLOCKED `rsn=xray_dir_block`
  - MANAUSDT: PLACED `BRAIN_DO_TRADE | sym=MANAUSDT [2/2] el=943ms ... rsn=ok`
    (workers.2026-05-02_04-31-00_392071.log:18298)

## CALL_A #5 — d-1777701650866

- **STRAT_CALL_A_START:** `2026-05-02 06:00:50.866`
- **Packages:** count=15 age_min_s=110 age_max_s=110
- **Prompt:** sections=34 chars=17238 → trimmed → 31 / ~17137
- **Claude:** `CLAUDE_CALL_OK | call_id=45 attempt=1/3 el=82496ms out=2014`
- **Response first ~200 chars:**
  > "System infrastructure down - Layer3 boot failure blocking all
  > trades. Market-wis…"
  (note: market_view truncated in log to 80 chars; full_response in
  claude_decisions cut at 200 chars too — operator note about Layer3 boot)
- **Parsed directives:** trades=2 risk=cautious
  - #1 AXSUSDT Buy lev=3 — `Per-coin trending_up regime, strongest BUY consensus (4.61 votes vs 0)…`
  - #2 MANAUSDT Buy lev=3 — `Ranging regime, range_fade_long at support, highest BUY votes (5.80 vs 0)…`
- **Routing result:**
  - AXSUSDT: PLACED `BRAIN_DO_TRADE | sym=AXSUSDT [1/2] el=871ms ... rsn=ok`
    (line 17062; SHADOW_ORD_SEND at 06:02:32.167)
  - MANAUSDT: BLOCKED `rsn=enforcer_block` (line 17069)

---

## CALL_B (last 5)

CALL_B prompts are smaller (positions-only — no per-coin briefing).

### CALL_B #1 — d-1777703330620 (most recent CALL_B with full data)

- **STRAT_CALL_B_START:** `2026-05-02 06:28:50.620`
- **CTX:** positions=1 chars=1056 sections=12 el=7ms
- **Claude:** `CLAUDE_CALL_OK | call_id=51 attempt=1/3 el=75140ms out=397`
- **STRAT_CALL_B_PARSED:** total=1 hold=1 close=0 tighten=0 set_exit=0 take_profit=0
- **STRAT_CALL_B_PLAN:** acts=1 (HOLD on the single open position)
- **STRAT_CALL_B_END:** el=75158ms
- **Routing:** HOLD action — no transition; position retained until
  position_watchdog/sniper acts.

### CALL_B #2 — d-1777702389333

- **Start:** `2026-05-02 06:13:09.333`
- **CTX:** positions=1 chars=1146 sections=14 el=8ms
- **Claude:** `el=78865ms` (claude_decisions.id=1228)
- **Parsed:** total=1 hold=0 close=1 (1 close action issued)
- **End:** el=78861ms acts=1
- **Routing:** Brain CLOSE acted on (workers logs show close shortly after).

### CALL_B #3 — d-1777701884628

- **Start:** `2026-05-02 06:04:44.628`
- **CTX:** positions=1 chars=988 sections=13 el=8ms
- **Claude:** `CLAUDE_CALL_OK | call_id=46 el=84792ms out=665`
- **Parsed:** total=1 hold=0 close=1
- **End:** el=84812ms acts=1
- **Routing:** CLOSE issued.

### CALL_B #4 — d-1777701474112

- **Start:** `2026-05-02 05:57:54.112`
- **CTX:** positions=1 chars=617 sections=7 el=7ms
- **Claude:** `CLAUDE_CALL_OK | call_id=44 el=26731ms out=1245`
- **Parsed:** total=1 hold=0 close=1
- **End:** el=26751ms acts=1
- **Routing:** CLOSE issued.

### CALL_B #5 — d-1777700080246

- **Start:** `2026-05-02 05:34:40.246`
- **CTX:** positions=2 chars=1060 sections=? el=8ms
- **Claude:** `CLAUDE_CALL_OK | call_id=40 el=89019ms out=1246`
- **Parsed:** total=2 hold=1 close=1
- **End:** el=89043ms acts=2
- **Routing:** 1 hold + 1 close.

---

## Observations

- All 5 most recent CALL_A returned 2 trade directives each. Of the
  10 directives:
  - 2 PLACED (ONDOUSDT @06:26, AXSUSDT @06:02, MANAUSDT @06:10 = actually 3)
  - 4 BLOCKED enforcer_block (APEX flips that exceeded lev cap)
  - 3 BLOCKED xray_dir_block
  - 2 DROPPED brain_no_packages (newest cycle)
- All Claude calls in 5 cycles succeeded on attempt=1/3. No retries.
  Median elapsed ~120s (range 69537–133327ms).
- All 5 most recent CALL_B issued exactly 1 action each (4× close, 1×
  hold) on positions=1. No tighten / set_exit / take_profit observed in
  this 24h window.
- claude_decisions.full_response column is stored truncated to 200
  chars only — full Claude responses are NOT persisted to DB beyond the
  market_view summary.


=====================================================================
## FILE: N6_failures_24h.md
=====================================================================

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


=====================================================================
## FILE: N7_tias_integration.md
=====================================================================

# N7 — TIAS Integration Points

**Collected:** 2026-05-02 ~11:47 UTC
**Sources:** src/tias/*, src/workers/manager.py, src/core/trade_coordinator.py,
src/database/migrations.py, snapshot DB.

---

## A. Trigger on trade close — wiring

**File / line:** `src/workers/manager.py:1646–1763` (block headed
`# TIAS — Trade Intelligence Autopsy System (#9)`).

- `manager.py:1655` — `tias_repo = TradeIntelligenceRepo(db)`
- `manager.py:1657` — `tias_collector = TradeContextCollector(self._services, db)`
- `manager.py:1662–1668` — when `tias_cfg.enabled and tias_cfg.api_key`:
  build `DeepSeekClient(api_key=tias_cfg.api_key,
  api_url=tias_cfg.api_url, http_referer=tias_cfg.http_referer,
  x_title=tias_cfg.x_title)`
- `manager.py:1669` — `tias_analyzer = TradeAnalyzer(client=tias_client,
  settings=tias_cfg)`
- `manager.py:1714–1723` — `_tias_async_task(record, m4_snapshot)`:
  Phase 1 (collect+save) → Phase 2 (analyze in background via
  `asyncio.get_event_loop().create_task(...)`)
- `manager.py:1725–1761` — `_tias_close_callback(record)`:
  - SYNC: read `profit_sniper.get_closed_snapshot(sym)` first
    (Phase 3 fix — snapshot preserved before `_profit_states[sym]`
    delete in `profit_sniper._on_position_closed`).
  - Fallback: direct `_profit_states[sym]` read for race case.
  - ASYNC: `loop.create_task(_tias_async_task(record, m4_snapshot))`
- `manager.py:1762` — `coordinator.register_close_callback(_tias_close_callback)`
- `manager.py:1763` — `log.info("TIAS: trade context collector
  registered as close callback #9")`

**Sync vs async:** Trigger is registered as a synchronous
`coordinator.register_close_callback`. The callback grabs the M4
snapshot synchronously and then schedules the actual collect+save+analyze
as a background asyncio task. Phase 2 (DeepSeek analyze) runs as a
nested background task spawned from inside the Phase-1 task — so even
the Phase-1 DB write does not block the close path.

`coordinator.on_trade_closed` (src/core/trade_coordinator.py:405) is
the function that fires the callback registry.

---

## B. TIAS DeepSeek call — endpoint + timeout + retries

**File:** `src/tias/deepseek_client.py`
**Class:** `DeepSeekClient` (alias `OpenRouterClient`)

- `deepseek_client.py:75` — Default endpoint:
  `api_url: str = "https://openrouter.ai/api/v1/chat/completions"`
- `deepseek_client.py:76` — `http_referer: str =
  "https://github.com/trading-intelligence-mcp"`
- `deepseek_client.py:77` — `x_title: str = "TIAS-TradeAnalysis"`
- `deepseek_client.py:103` — Default `temperature: float = 0.3`
- `deepseek_client.py:104` — Default `max_tokens: int = 1500`
- `deepseek_client.py:105` — **Default `timeout_seconds: int = 45`**
- `deepseek_client.py:135` — `timeout = aiohttp.ClientTimeout(total=timeout_seconds)`
- `deepseek_client.py:147–155` — HTTP 429 / 503 raise `TIASAnalysisError(retryable=True)`
- `deepseek_client.py:198–203` — `aiohttp.ServerTimeoutError` → retryable=True
- `deepseek_client.py:204–208` — `aiohttp.ClientError` → retryable=True

**Effective timeout in production:** `[tias].timeout_seconds = 45` in
config.toml line 948 (the dataclass default and the config value match).
**The memory note "was 30s, now 60s" is INCORRECT — current value is 45s.**

**Retry policy:**
- `[tias].max_retries = 1` (config.toml line 949) — only one retry on
  retryable errors.
- Retry is implemented inside `TradeAnalyzer.analyze` (src/tias/analyzer.py),
  which calls `self._client.analyze(...)` once, then on `retryable=True`
  fails over to `fallback_model` (one shot). Non-retryable → no retry.

---

## C. trade_intelligence table schema

`sqlite3 /tmp/trading_snapshot_1777722335.db ".schema trade_intelligence"`
yields the migrations.py source:

`src/database/migrations.py:1113–1183` — CREATE TABLE:

```
trade_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Group A: Trade Outcome (always populated)
    symbol TEXT NOT NULL, direction TEXT NOT NULL, strategy_name TEXT NOT NULL,
    strategy_category TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
    closed_by TEXT NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
    pnl_pct REAL NOT NULL, pnl_usd REAL NOT NULL, win INTEGER NOT NULL,
    hold_seconds REAL NOT NULL,

    -- Group B: Entry Decision Context
    leverage REAL, position_size_usd REAL,
    claude_thesis TEXT, claude_signal TEXT, claude_confidence REAL,
    entry_score REAL, ensemble_votes TEXT,

    -- Group C: Market Conditions at Close
    regime TEXT, fear_greed_value INTEGER, fear_greed_label TEXT,

    -- Group D: Technical Indicators at Close
    rsi REAL, macd_hist REAL, macd_signal REAL, bollinger_pct REAL,
    ema_20 REAL, ema_50 REAL, stochastic_k REAL, stochastic_d REAL,
    adx REAL, atr_value REAL, atr_pct REAL, volume_ratio REAL, price_vs_vwap REAL,

    -- Group E: Mode4 Profit Tracking Data
    m4_peak_pnl_pct REAL, m4_ticks_in_profit INTEGER, m4_ticks_total INTEGER,
    m4_composite_score REAL, m4_hurst_value REAL, m4_momentum_decay REAL,
    m4_extension_score REAL, m4_ev_ratio REAL, m4_volume_div_score REAL,

    -- Group F: DeepSeek Analysis (Phase 2 — NULL until analyzed)
    ds_why TEXT, ds_what_worked TEXT, ds_what_failed TEXT,
    ds_lessons TEXT, ds_category TEXT, ds_confidence REAL,
    ds_analyzed_at TEXT,

    -- Group G: Metadata
    trade_id TEXT, trade_closed_at TEXT NOT NULL, captured_at TEXT NOT NULL
)
```

**Indices:**
- `idx_ti_symbol`, `idx_ti_win`, `idx_ti_ds_why`,
  `idx_ti_trade_closed_at`, `idx_ti_ds_category`

**v18 ALTER TABLE additions** (`migrations.py:1193–1209`):
`ds_correct_direction TEXT, ds_what_should_done TEXT,
ds_how_to_exploit TEXT, ds_optimal_direction TEXT,
ds_optimal_sl_pct REAL, ds_optimal_tp_pct REAL,
ds_optimal_size_usd REAL, ds_optimal_leverage INTEGER,
ds_raw_response TEXT, ds_response_time_ms INTEGER,
ds_input_tokens INTEGER, ds_output_tokens INTEGER, ds_cost_usd REAL,
ds_model TEXT`

(Plus later: `analysis_version`, `analysis_attempts`, `entry_regime`,
`entry_rsi`, `entry_macd_hist`, `entry_atr_pct`, and the apex_*
columns — see snapshot `PRAGMA table_info(trade_intelligence)` in N3
context). 94 columns total.

### Sample 10 most recent rows

```
SELECT id, symbol, direction, source, closed_by, regime, pnl_pct,
       leverage, ds_category, captured_at
FROM trade_intelligence
ORDER BY captured_at DESC LIMIT 10;
```

Result (from snapshot):
```
821 ONDOUSDT     Buy  claude_direct time_decay_p_win_low ranging      -0.104   2.0  REGIME_MISMATCH    2026-05-02T06:29:10
820 MANAUSDT     Buy  claude_direct time_decay_p_win_low dead         -0.052   3.0  REGIME_MISMATCH    2026-05-02T06:13:38
819 AXSUSDT      Buy  claude_direct mode4_p9             trending_up  -0.010   3.0  STOP_TOO_TIGHT     2026-05-02T06:05:17
818 DOGEUSDT     Sell claude_direct strategic_review:..  ranging      -0.134   2.0  (NULL)             2026-05-02T05:58:36
817 AXSUSDT      Buy  claude_direct mode4_p9             trending_up  -0.140   2.0  (NULL)             2026-05-02T05:35:14
816 DOGEUSDT     Sell claude_direct time_decay_p_win_low ranging      -0.049   2.0  (NULL)             2026-05-02T05:35:05
815 RENDERUSDT   Buy  claude_direct strategic_review:..  ranging      -0.009   3.0  (NULL)             2026-05-02T05:06:49
814 SANDUSDT     Sell claude_direct shadow_sl_tp         ranging      -0.129   5.0  (NULL)             2026-05-02T04:54:07
813 AXSUSDT      Buy  claude_direct mode4_p9             trending_up  -0.120   3.0  (NULL)             2026-05-02T04:51:39
812 HYPEUSDT     Buy  claude_direct time_decay_p_win_low ranging      -0.005   3.0  (NULL)             2026-05-02T04:29:30
```

(Total trade_intelligence rows in snapshot: 821. `source=claude_direct`
for all 24h rows. `closed_by` distribution heavily skewed to
`time_decay_p_win_low`, `mode4_p9`, `shadow_sl_tp`, `strategic_review`.)

---

## D. Coaching feedback loop — TIAS output → next prompt

**Wiring file:** `src/brain/strategist.py:565–571` (CALL_A) and
`strategist.py:1550–1557` (alternate path).

```python
# strategist.py:565
if enforcer and hasattr(enforcer, "get_coaching_text"):
    ...
    coaching = enforcer.get_coaching_text(structure_cache=_sc)
    if coaching:
        sections.append(f"## {coaching}")
```

The coaching text is generated by `PerformanceEnforcer.get_coaching_text`
inside `src/strategies/performance_enforcer.py`. It pulls aggregates
from `tias_repo` (recent loss summaries, win-rate-by-regime, per-coin
WR/PF) and emits a markdown block prepended at section position-2 of
the brain prompt.

**Live observation (last 24h):** every `STRAT_PROMPT_BUILD` log line
shows `coaching=0ms` — meaning the coaching block was either empty
(fast path) or cached and free to emit. NOT FOUND — explicit
COACHING_TEXT or COACH_BLOCK log lines (no specific log emitted on
build); inferred only via prompt size.

**Format:** prepended Markdown section starting `## …` (line 569);
visible inside the prompt right after coin briefings. Inserted via
`sections.append(f"## {coaching}")` so it lands as a top-level
section.

---

## E. TIAS Phase-3 data gaps — verification per memory note

The memory note states the following gaps exist. Each verified against
the current code+DB:

### 1. Claude directive text not stored at entry time
**STATUS: PARTIALLY FIXED.**
- `claude_thesis` column IS populated at TIAS save time
  (collector.py:243–244 — `if record.get("claude_directive"):
  result["claude_thesis"] = record["claude_directive"]`). Sample row 821
  has `claude_thesis = '[APEX OPTIMIZED] TIAS shows no history for
  ONDOUSDT...'` (non-NULL).
- However, the `claude_thesis` is read FROM `record` at trade-close
  time. The "directive at entry" text comes via the trade-coordinator
  record, which is set at order placement. Confirmed: claude_thesis
  for trades 817-821 contains both the "[APEX OPTIMIZED]…" reasoning
  and the "Claude:" original-thesis text concatenated.
- The `claude_signal` column (sample: "Claude: STRONG ensemble 76.7,
  highest buy consensus...") is also populated at save time from
  `record["claude_plan_view"]` (collector.py:246).
- **GAP STATUS: APPEARS RESOLVED — entry-time directive text IS
  present in current rows.**

### 2. Mode4 data deleted by ProfitSniper before TIAS reads
**STATUS: FIXED via Phase 3 snapshot mechanism.**
- `src/workers/profit_sniper.py:781–795` — snapshot is saved
  to `_closed_snapshots[symbol]` BEFORE `_profit_states.pop(symbol)`
  on line 803.
- `src/workers/manager.py:1736–1739` — `_tias_close_callback` reads
  via `profit_sniper.get_closed_snapshot(sym)` first (preferred
  path), then falls back to direct `_profit_states` read.
- **However:** sample rows show `m4_peak_pnl_pct=0.0` for trades
  817, 819, 820, 821 — only 818 has `0.00256`. Many m4_* fields
  appear unpopulated. The snapshot wiring exists but field
  values are still mostly zero/null in 24h sample, suggesting the
  snapshot is saved but the M4 statistics rarely accumulate
  meaningful values within the typical 2–3-min hold time.
- **GAP STATUS: WIRING FIXED, DATA STILL SPARSE.**

### 3. Entry-time market conditions lost
**STATUS: PARTIALLY FIXED.**
- Schema has `entry_regime`, `entry_rsi`, `entry_macd_hist`,
  `entry_atr_pct` (added in v18+). NOT FOUND — the values from
  query for ID 821: only `regime` (close-time), `fear_greed_value`
  (close-time) populated; entry_regime/entry_rsi NOT in 10-row
  sample (column likely null). collector.py reads close-time
  values from caches (`_collect_group_c`, `_collect_group_d`),
  not entry-time. The `entry_*` columns exist but are only
  populated if record carries them at close.
- **GAP STATUS: SCHEMA ADDED, COLLECTOR DOES NOT POPULATE
  entry-time values yet — close-time values stored as
  rsi/macd_hist/etc.**

### 4. Signal score + strategy name not forwarded
**STATUS: FIXED.**
- `collector.py:225` — `result["entry_score"] = st_row.get("score")`
  (reads from strategy_trades by trade_id).
- `collector.py:248` — `if record.get("signal_score") is not None:
  result["entry_score"] = record["signal_score"]` (record overrides
  if available).
- `collector.py:141` — `"strategy_name": record.get("strategy_name", "")`
  populated unconditionally from record.
- Sample rows: `strategy_name=claude_trader` (verified via N4 query
  earlier in this collection). `entry_score` not visible in the 10-
  row sample (queried subset doesn't include) — needs separate query.
- **GAP STATUS: WIRING PRESENT, populated for current 24h rows.**

### Summary of TIAS gaps (now)
| Gap | Memory note status | Current code/DB status |
|---|---|---|
| Claude directive at entry | broken | wiring fixed; values present in rows 817–821 |
| Mode4 deleted before read | broken | wiring fixed; data values still mostly 0.0 (short holds) |
| Entry-time market conds | broken | schema fixed; collector still reads close-time only |
| Signal score / strategy name | broken | wiring fixed; populated in 24h rows |
