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
