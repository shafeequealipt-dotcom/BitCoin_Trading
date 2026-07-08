# Trade-Management Pipeline — Deep Analysis

This document maps, in depth, every system that activates once a trade is OPEN and
actively watches and manages it for profit and loss — the complete pipeline from the
moment a position exists to the moment it closes. It was produced by reading the
actual code line by line across the trade-management stack. Each part below covers one
system: what it is, when it activates and how often, exactly how it runs profit and
how it runs loss, what it reads, what it writes (a stop, a force-close, or only advice),
how it is wired and dependency-injected, and its real weaknesses. Part 1 is the
end-to-end pipeline that ties everything together; read it first for the map, then the
individual systems. The final part records how the pipeline actually behaves against
measured live data, not just how it is designed to behave.

All claims are cited as file_path:line. Generated 2026-06-15.

## Contents

1. End-to-End Pipeline and Orchestration — the connective tissue
2. Profit Sniper (Mode 4) — the profit-fetching side
3. Profit Sniper (Mode 4) — the loss-cutting side
4. Position Watchdog — the per-position monitor
5. Time-Decay Loss Engine — the five-model loss intelligence
6. SL Gateway and the Trade-State Owner Switch — the single stop-write chokepoint
7. Sentinel, Brain Review, and Trade Coordinator — deadlines, Claude review, the close path
8. How it actually behaves — the measured reality


---

# Part 1 — End-to-End Pipeline and Orchestration

## 1. What it is and its single responsibility

This "system" is not a single class but the **connective tissue** of the trade-management pipeline: the `WorkerManager` (`src/workers/manager.py`, `class WorkerManager` at line 23), the `TradeCoordinator` (`src/core/trade_coordinator.py`), the bybit_demo exchange adapter and WebSocket subscriber (`src/bybit_demo/`), and the `ThesisManager` (`src/core/thesis_manager.py`). Its single responsibility is to **construct every trade-management service, dependency-inject them into one another, run their loops on independent cadences, route every stop-loss write through one chokepoint, and detect/record every close exactly once** through a fan-out of registered callbacks.

The three "active managers" (PositionWatchdog, ProfitSniper) are the subjects of other sections. This section covers how they are wired, what feeds them, and the precise per-tick and per-close sequence that ties them together.

## 2. When it activates — cadences and triggers

The manager builds all workers in `initialize()` and launches them concurrently in `start_all()` (`manager.py:3316`). Each is its own asyncio task created at `manager.py:3348-3351` (`asyncio.create_task(self._run_worker(w), name=w.name)`); a crash in one does not stop others. Each `BaseWorker` runs a `while self.running:` loop that calls `tick()` then `await asyncio.sleep(self.interval)` (`src/workers/base_worker.py:224`, `:419`).

The trade-management cadences, all confirmed from `config.toml`:

- **PositionWatchdog: 10 seconds.** `interval_seconds=settings.watchdog.check_interval_seconds` (`position_watchdog.py:149`); `config.toml:789` `check_interval_seconds = 10`. A congestion detector warns `WD_TICK_GAP` if the gap exceeds 30s and `WD_POLL_LAG` if it exceeds 2x configured (`position_watchdog.py:705-714`).
- **ProfitSniper: 5 seconds.** `[mode4] check_interval_seconds = 5` (`config.toml:1740`; default `Mode4Settings.check_interval_seconds: int = 5` at `settings.py:429`). Its ring buffer holds 720 points = 60 minutes at 5s (`config.toml` buffer_max_size).
- **BybitDemoWSWorker: 60 seconds**, but this is only a health-check/reconnect tick. Real close events arrive **push-driven** via pybit thread callbacks, not on this tick (`bybit_demo_ws_worker.py:1-14`, constructed with `interval_seconds=60.0` at `manager.py:1504`).
- **Watchdog sub-cadences inside its 10s tick**: zombie thesis reconcile every 300s (`position_watchdog.py:771`, `_last_reconcile_at >= 300.0`); fast set-diff reconcile `fast_reconcile_seconds = 30.0` (`config.toml:809`).

The watchdog and sniper both **gate** their work on positions actually being open: the sniper detects opens/closes by set-diffing `current_symbols` against `self._tracked` each tick (`profit_sniper.py:483-498`); the watchdog reads `get_positions_with_confirmation()` and skips all mutation if ground truth is unconfirmed (`position_watchdog.py:747-758`).

## 3. How it manages PROFIT (orchestration role)

The orchestration layer does not itself decide profit logic; it **provides the shared substrate** the profit managers use:

- **Peak tracking is centralized in the coordinator.** `TradeCoordinator.update_peak_pnl(symbol, current_pnl_pct)` (`trade_coordinator.py:2400-2404`) keeps `state.peak_pnl_pct` as a monotonic max. The watchdog calls it every tick (`position_watchdog.py:1005`, `:2862`) and the sniper reads `state.peak_pnl_pct` for its anti-greed pullback logic. This single peak is the shared truth both profit managers anchor their trailing floors to.
- **The SLGateway is the single profit-locking chokepoint.** Every trailing tighten from both the watchdog (`_push_sl_to_shadow` → `sl_gateway.apply`, `position_watchdog.py:1291-1292`) and the sniper (`sl_gateway.apply` at `profit_sniper.py:1296`, `:3023`, `:3531`, `:5230`) converges here. The gateway's R1 (tighten-only) rule guarantees a profit-locking stop can only move toward price, never away (`sl_gateway.py` rule R1). Config keys (`config.toml:920` `[sl_gateway]`): `min_distance_pct = 0.3` (R2), `max_step_pct = 0.25` (R3, "let profits run"), `rate_limit_seconds = 30` (R4), all enforced hard (`log_only_global = false`, all per-rule flags false at `config.toml:957-963`). ATR-scaled min-distance via `min_distance_atr_multiplier = 0.5`, `min_distance_abs_floor_pct = 0.05`.
- **Breakeven carve-out** `r2_breakeven_floor_enabled = true` lets the R2 clamp hold a stop at breakeven (entry) instead of rewriting it below, for armed ladder floors (`sl_gateway.py:492-502`, `config.toml`).

## 4. How it manages LOSS (orchestration role)

- **Force-close authority is real, not advisory.** The watchdog directly calls `position_service.close_position(...)` on its loss/time-decay paths (`position_watchdog.py:869` `wd_emergency`, `:912` `wd_dup_close`, `:1863` `win_prob_force_close`/`time_decay_force_close`). The sniper closes via `_execute_full_close` → `position_service.close_position(...)` then `trade_coordinator.on_trade_closed(...)` (`profit_sniper.py:4837`, `:4934`, `:4974`, `:5123`). So both loss managers WRITE the close; the orchestration layer's job is to ensure that write is recorded exactly once.
- **The exchange itself is a loss manager.** Stops placed via the gateway → `set_stop_loss` sit on Bybit as native `slTriggerBy=LastPrice` orders (`bybit_demo_adapter.py:1278-1287`). When Bybit triggers one, the close arrives via the WS execution stream tagged `closed_by="bybit_sl_hit"` (`bybit_demo_websocket_subscriber.py:478-479`).
- **Double-close guard** is the loss-side integrity guard: `on_trade_closed` pops `state` and, if already `None`, logs `COORD_DOUBLE_CLOSE` and returns (`trade_coordinator.py:1369-1378`). This prevents a race between watchdog poll, sniper, and WS SL-hit from booking the same loss twice.
- **Re-entry cooldown** is armed on close (`trade_coordinator.py:1812-1824`): in loss-only mode the cooldown arms only when `pnl_usd < 0`, keyed `(symbol, direction)`, preventing immediate re-entry into a losing setup.

## 5. Inputs (what the orchestration reads and from where)

- **Live positions and unrealized PnL** come from the position service (`BybitDemoPositionService.get_positions`, `bybit_demo_adapter.py:187`; `get_positions_with_confirmation` at `:209`). Each `Position` carries `unrealized_pnl` parsed from Bybit's `unrealisedPnl` field (`bybit_demo_adapter.py:2233`, `:2341`). Both managers read `pos.unrealized_pnl` directly each tick.
- **Live prices** come from `MarketService.get_ticker(symbol)` with a 5-second in-memory cache (`src/trading/services/market_service.py:45-67`, `self._CACHE_TTL = 5.0`). The watchdog reads it at `position_watchdog.py:984`, `:2212`; the sniper at `profit_sniper.py:1405`.
- **WS position/order snapshots** arrive via `_handle_position` (`bybit_demo_websocket_subscriber.py:268`) emitting `BYBIT_DEMO_WS_POS_UPDATE` with `unrealized_pnl`, `mark_price`, `sl_price`, `tp_price` per state change — observability only; it does **not** trigger closes (`:271-274`).
- **Supporting analytical inputs** registered in `_services` and injected: `volatility_profiler` (`manager.py:215`, ATR via `VolatilityProfiler.get_profile` → `recommended_sl_pct`, `atr_pct_5m`, `src/analysis/volatility_profile.py:146`); `structure_cache` (`manager.py:231`, X-RAY `StructureCache.get`/`invalidate`, `src/analysis/structure/structure_cache.py:58`,`:148`); `regime_detector` (`RegimeDetector.detect`, `src/strategies/regime.py:163`).

## 6. Outputs / writes

- **Stop writes** flow exclusively through `SLGateway.apply` → `_wire_push` → `position_service.set_stop_loss(symbol, new_sl)` (`sl_gateway.py:996-1019`, `:1004`). `accepted=True` from the gateway guarantees the wire push succeeded (`sl_gateway.py:511-513`).
- **Force-closes** write via `position_service.close_position(...)` (watchdog and sniper, cited above).
- **Close records** are written by the `TradeCoordinator` via the close-callback fan-out (`trade_coordinator.py:1789-1797`) — see section 7.
- **Thesis writes**: `ThesisManager.save_thesis` on open (`thesis_manager.py:108`, emits `THESIS_OPEN` at `:226`); `close_thesis` on close (emits `THESIS_CLOSE` at `:494`).

## 7. Wiring — the dependency graph

Construction order in `manager.initialize()`:

1. **`TradeCoordinator` built first** (`manager.py:610`), stored as `_services["trade_coordinator"]`. It is the hub; everything else attaches to it.
2. Adapter services (`BybitDemoPositionService`/`OrderService`) get the coordinator via `attach_coordinator(trade_coordinator)` (`manager.py:737`, `:746`) so the pre-order cross-direction guard can read `_trades`.
3. **`SLGateway` constructed (~756)** with `settings`, `position_service`, `market_service`, `event_buffer`, and `volatility_profiler` (`manager.py:756-767`). A close callback `_sl_gateway_reset_on_close` is registered (`:774-781`) to clear per-symbol rate-limit/step state on close. EventBuffer is late-wired at `:862-864`.
4. **`ThesisManager` built (~792)**, with transformer (`:798`) and position service (`:805`) attached.
5. **`PositionWatchdog` constructed (~1560)** with the full injection set: `position`, `market`, `order`, `account`, `claude_client`, `trade_coordinator`, `event_buffer`, `data_lake`, `transformer`, `regime_detector`, `urgent_queue`, `volatility_profiler`, **`sl_gateway`**, **`thesis_manager`**, **`structure_cache`**, `ensemble_state_cache` (`manager.py:1560-1593`).
6. **`Layer4ProtectionService` built AFTER the watchdog (~1616)** so it can reuse the watchdog's `TimeDecaySLCalculator` (`manager.py:1610-1622`); post-init-assigned back onto the watchdog (`:1626`).
7. **`ProfitSniper` constructed (~1644)** with the same shared services including **`sl_gateway`**, `layer4_protection`, and `structure_cache` (`manager.py:1644-1670`).
8. **`BybitDemoWebSocketSubscriber` constructed (~1492)** with `coordinator=coord_for_ws` and the running loop, wrapped in **`BybitDemoWSWorker`** (`manager.py:1492-1508`).
9. **`regime_detector` late-wired** into watchdog, profiler, scanner, layer4 after its own construction (`manager.py:1777-1817`).

**Close-callback registration order** on the coordinator (all `register_close_callback`, fired in registration order at `trade_coordinator.py:1789`):
sl_gateway reset (`manager.py:779`) → thesis close (`:2340`) + thesis reconcile (`:2370`) → data_lake (`:2423`) → trade_history (`:2569`) → positions-table cleanup (`:2677`) → **sniper unsubscribe** `_sniper_unsubscribe_on_close` calling `sniper._on_position_closed(sym)` (`:2686-2701`) → event_buffer clear (`:2718`) → transformer cache clear (`:2735`) → strategist invalidate (`:2752`) → urgent_queue clear (`:2775`) → plus enforcer/fund/perf/registry/pnl callbacks (`:2169`-`:2262`).

## 8. End-to-end pipeline for one open trade

**Open.** The strategy worker calls `coordinator.register_trade(...)` (`strategy_worker.py:3598`) creating a `TradeState` with `entry_price`, `side`, `size`, `order_id`, `peak_pnl_pct=0`, and `opened_at`. It then calls `thesis_mgr.save_thesis(...)` (`strategy_worker.py:3839`) → `THESIS_OPEN` row with the invalidation contract (`thesis_manager.py:108`,`:226`). The order goes to Bybit; the entry fill and any attached SL appear on the WS streams.

**Per tick (interleaved 5s sniper / 10s watchdog), for one open symbol:**

1. **Read positions** — watchdog: `get_positions_with_confirmation()` (`position_watchdog.py:747`); sniper: `_get_positions()` (`profit_sniper.py:478`). Each `Position` carries `unrealized_pnl` and `mark_price` straight from Bybit.
2. **Read price** — `market_service.get_ticker(symbol)` (5s-cached) for the mark used in distance math.
3. **Update shared peak** — watchdog calls `coordinator.update_peak_pnl(symbol, pnl_pct)` (`position_watchdog.py:1005`), advancing `state.peak_pnl_pct`; the sniper reads that same value.
4. **Each manager computes a stop candidate** independently: the sniper from its 5-model + Chandelier/ATR trail (`profit_sniper.py:_apply_trail_stop`, reads `volatility_profiler` ATR and `structure_cache` X-RAY); the watchdog from its trailing-drawdown, time-decay (consulting `layer4_protection`/`structure_cache`/`regime_detector`), and proximity rules.
5. **Candidates converge at the gateway.** Both call `sl_gateway.apply(symbol, new_sl, source=..., direction=..., current_price=..., entry_price=...)`. The gateway applies R1 tighten-only, R2 ATR-scaled min-distance (breakeven carve-out), R3 max-step 0.25%, R4 30s rate-limit, and the owner-switch gate, then `_wire_push` → `set_stop_loss` to Bybit (`sl_gateway.py:444`, `:1004`). Rejects are logged `SL_GATEWAY_REJECT`; brain-sourced rejects surface to the EventBuffer (`sl_gateway.py:1038-1042`).
6. **Force-close branch** (loss/time-decay/anti-greed-full/stall-escape): the manager calls `position_service.close_position(...)` then `coordinator.on_trade_closed(...)`.

**Close detection (three converging paths, deduped):**

- **Native SL/TP hit** → Bybit WS execution stream → `_process_execution` maps `stopOrderType` to `closed_by` (`bybit_sl_hit`/`bybit_tp_hit`/`bybit_external`, `bybit_demo_websocket_subscriber.py:478-492`). Only fully-flatting fills (`closed_size>0`, `leaves_qty==0`) proceed (`:435`,`:457`); an L1 dedup gate (`_is_duplicate_close`, 5s TTL) blocks duplicates (`:465`,`:568`). It then calls `_call_coordinator_close` → `coordinator.close_with_authoritative_pnl(...)` scheduled on the project loop (`:784`), passing `exit_price` with `pnl_pct=0` as the **sentinel** — the coordinator back-derives `pnl_pct` from `entry_price`+`exit_price`+`side` and `pnl_usd` from size, flipping `was_win` (`trade_coordinator.py:1390-1419`).
- **Watchdog/sniper system close** → direct `on_trade_closed(...)` with `closed_by="wd_*"`/sniper reason.
- **Zombie reconciler** (watchdog, every 300s) → `thesis_manager.reconcile_with_shadow(shadow_syms)` (`position_watchdog.py:769-775`) catches any close that skipped the coordinator.

**Recording (single fan-out).** Whichever path wins, `on_trade_closed` pops `state` (double-close guard at `trade_coordinator.py:1369`), builds the full `record` dict with `symbol, pnl_pct, pnl_usd, was_win, closed_by, hold_seconds, entry_price, close_price, order_id, trade_id, opened_at, closed_at, size, direction` and APEX/TIAS context (`:1699-1773`), appends to `_closed_trades`, then fires every registered close callback in order (`:1789-1797`). This single fan-out: resets the gateway's symbol state, closes the thesis (`thesis_manager.close_thesis`, scoped by `(symbol, order_id)`, emitting `THESIS_CLOSE` at `thesis_manager.py:494`), writes the data lake and trade_history rows, unsubscribes the sniper (`sniper._on_position_closed`), clears event/urgent/transformer caches, and arms the re-entry cooldown. The PnL reconciler later re-fires the **reconcile** channel (`fire_reconcile`, `:2268`) to correct idempotent sinks once Bybit's authoritative `closedPnl` is available, and a genuine win↔loss flip triggers `PNL_PHANTOM_CORRECTION` plus the correction channel for stateful consumers (`:2300-2340`).

## Known weaknesses / failure modes

- **Three independent close sources, one dedup window.** The WS subscriber's `_is_duplicate_close` TTL is only 5 seconds (`_DEDUP_TTL_SECONDS`, `bybit_demo_websocket_subscriber.py:579`). The coordinator's `on_trade_closed` double-close guard (`trade_coordinator.py:1373`) is the real backstop, but it is keyed on `_trades.pop(symbol)` — if a watchdog poll and a WS SL-hit for the **same** symbol both fire before the first pops state, the second correctly no-ops, but a fast same-symbol re-entry between the two events could in principle let the second close hit the new trade's state (mitigated, not eliminated, by the order_id scoping in `close_thesis`).
- **Back-derived PnL is gross until reconciliation.** The WS close path passes `pnl_pct=0` sentinel and the coordinator back-derives a fee-free gross figure (`trade_coordinator.py:1419`, subscriber comment at `:757`). Until the PnL reconciler fires `fire_reconcile`, every downstream consumer (data lake, thesis, enforcer streak) holds a gross number; a phantom win can briefly skip the loss cooldown until the correction channel arms it (`:2312-2327`).
- **Peak is per-process and not persisted.** `state.peak_pnl_pct` lives only in the coordinator's in-memory `_trades`. A restart resets the peak to 0, so every trailing manager loses its anchor and the trailing floor effectively re-arms from current PnL after a crash.
- **Late-wired regime detector.** `regime_detector` is injected into the watchdog at construction as `None` and patched post-hoc at `manager.py:1777-1785`. If detector construction fails, the watchdog and Layer4 fall back to inline structural invalidation (`manager.py:1631-1637`), silently degrading loss-side structural exits.
- **WS staleness is only checked every 60s.** Close events are push-driven, but if the private WS silently dies, `is_stale()` (120s threshold) is only evaluated on the 60s health tick (`bybit_demo_ws_worker.py:108`); in that window the system relies entirely on the watchdog's 10s poll + 30s/300s reconcilers to notice closes, adding latency to loss recording.

**Key file:line references:** `src/workers/manager.py` (orchestration: 610, 756, 792, 1492, 1560, 1644, 2340, 3348); `src/core/trade_coordinator.py` (close hub: 606, 1334, 1369, 1699, 1789, 2238, 2268, 2400); `src/bybit_demo/bybit_demo_websocket_subscriber.py` (close detection: 268, 432, 478, 568, 784); `src/bybit_demo/bybit_demo_adapter.py` (1236 set_stop_loss, 187 get_positions, 2233 unrealized_pnl); `src/core/sl_gateway.py` (444 apply, 996 _wire_push); `src/core/thesis_manager.py` (108 save_thesis, 412 close_thesis); `src/trading/services/market_service.py:45` (ticker cache).

---

# Part 2 — Profit Sniper — Profit-Fetching Side

## 1. What it is and its single responsibility

The Profit Sniper is **Mode 4** of the trading system, implemented in `src/workers/profit_sniper.py` (5,892 lines). It is one of four parallel position-management modes (Mode 1 strategic Claude review every 30s, Mode 2 rule-based safety net, Mode 3 emergency, Mode 4 profit protection every 5s — see the module docstring at `profit_sniper.py:14-18`).

The **profit-fetching side** is the half of the worker that owns a position **once it is in profit (or has been in profit)**. Its single responsibility is to **ratchet a protective stop-loss upward toward the live price as profit accumulates, so a winning trade's gains are progressively locked in and cannot fully round-trip into a loss**. It does this through two cooperating stop-raising tools — a **stepped break-even ladder** (`_compute_ladder_floor`) and an **ATR/Chandelier trailing stop** (`_compute_trail_stop`) — reconciled by a **highest-stop-wins spine** (`_pf_select_stop` / `_pf_apply_spine`) that performs the single gateway write per tick. A separate **score-based action engine** (`_determine_action`) decides whether to additionally tighten, partial-close, or full-close based on the five-model composite score and an anti-greed backstop.

The authority split is explicit (`profit_sniper.py:2163` config comment, `2608-2620` code): **PnL ≥ 0 / graduated → the profit-fetching system manages; PnL < 0 / not yet graduated → the companion Loss-Cutting system manages.** Graduation is a one-way latch tied to the ladder arm threshold.

## 2. When it activates — cadence and triggers

**Cadence.** The worker's `tick()` (`profit_sniper.py:457`) runs every `check_interval_seconds = 5` (`config.toml:1740`). This value is passed to `BaseWorker.__init__` as `interval_seconds` (`profit_sniper.py:171`), and `BaseWorker.run()` calls `await self.tick()` then `await asyncio.sleep(self.interval)` in its loop (`base_worker.py:330`, `419`). So every tracked position is re-evaluated on a 5-second drumbeat.

**Per-tick flow inside `tick()`:**
- Skips entirely during an exchange-transformer switch (`profit_sniper.py:473`).
- `_get_positions()`; detects new/closed positions; `_update_position()` for each tracked position which feeds the ring buffer and updates `peak_pnl_pct` / `peak_price` (`profit_sniper.py:486-498`, `1487-1492`).
- A buffer-warmup guard skips any symbol with fewer than 12 buffer points (`profit_sniper.py:545-554`); models need ≥ 12 points, and `eval_tier=2` needs ≥ 100 (`555`).
- **Profit-tool computation** (lines `631-718`): only when a `_trail_state` (PositionProfitState) exists AND `self._pf.enabled` is true (`profit_fetching.enabled = true`, `config.toml:1975`). It resolves the time dial, the effective ATR, then computes `trail_result = _compute_trail_stop(...)` (`681`) and `ladder_result = _compute_ladder_floor(...)` (`692`). These are stored as `tracked["last_trail"]` / `tracked["last_ladder"]` (`872-874`).
- **Spine application** (the M5 loop, `928-951`): for every tracked position, if `self._pf.enabled or self._lc.enabled`, it calls `await self._pf_apply_spine(...)`. This runs **every tick regardless of the score action** so a climbing winner's stop keeps rising (comment at `931-937`).
- **Score-based action execution** (`953-987`): only fires when `last_action.action != "hold"`.

**The conditions that arm each profit tool:**
- The **ladder** arms when `state.peak_pnl_pct >= floor_arm` (`profit_sniper.py:2020`), where `floor_arm = min(micro_floor_arm_pct, min_profit_to_arm_ladder_pct)` — effectively the micro-arm at **0.10%** (raised to the fee-clearance level when the fee-aware switch is on, see §3).
- The **Chandelier trail** arms when `state.peak_pnl_pct >= self._pf.min_profit_for_trail_pct = 0.2` (`profit_sniper.py:1918`, `config.toml:2020`).
- The **graduation latch** (`_graduated`) flips when `peak_pnl_pct >= min_profit_to_arm_ladder_pct = 0.2` (`profit_sniper.py:2619-2620`). This is the monotonic high-water boundary that hands authority from the loss system to the profit system for the rest of the trade's life (`GRADUATION_LATCH` log at `2624-2629`).
- The **score action engine** is gated behind a profit gate: it returns `hold` when `current_pnl <= 0` (source `profit_gate`, `profit_sniper.py:3682`) and when `current_pnl < min_profit_for_action = 0.10` (source `below_min_profit`, `3703`, `config.toml:1852`).

## 3. How it manages PROFIT — every mechanism, threshold, config key + value

### 3a. The stepped break-even ladder — `_compute_ladder_floor` (`profit_sniper.py:1941-2153`)

As the high-water profit (`state.peak_pnl_pct`) climbs past successive rungs, the stop locks a rising guaranteed-profit floor a fixed offset behind the rung just crossed. It is driven by **peak** PnL, so it is strictly monotonic (tighten-only).

- **Step spacing and lock offset are age-dialed** by the `TimeDial` (`time_dial.py:136-141`), linearly interpolating young→old anchors by the trade's age fraction:
  - `ladder_step_pct_young = 0.6`, `ladder_step_pct_old = 0.4` (`config.toml:1996-1997`)
  - `lock_offset_pct_young = 0.3`, `lock_offset_pct_old = 0.2` (`config.toml:2000-2001`)
- **Rung and lock math** (`profit_sniper.py:2047-2048`): `level = floor(peak/step) * step`; `lock_pct = level - offset`. Example from the blueprint: at step 0.5 / offset 0.3, a +0.5% crossing locks +0.2%.
- **Arm threshold:** `min_profit_to_arm_ladder_pct = 0.2` (`config.toml:2015`). This is the graduation arm.
- **Decoupled micro-floor arm:** `micro_floor_arm_pct = 0.10` (`config.toml:2060`). The breakeven/dead-band floor arms at this LOWER threshold so the small green most losers reach (median ~+0.07%, below the 0.2% graduation arm) can be locked, while the GRADUATION_LATCH still reads 0.2% and loss-cutting authority is retained until genuine +0.2% (`profit_sniper.py:1966-1999`, `2129-2137` `MICRO_FLOOR_ARM` log). It is clamped to never exceed the graduation arm via `floor_arm = min(_micro_arm, arm)` (`1999`).
- **Fee-aware micro-floor arm (F2):** `micro_floor_arm_fee_aware_enabled = true` (`config.toml:2078`). When on, the effective arm becomes `max(micro_floor_arm_pct, ladder_lock_fee_clearance_pct)` still clamped to the graduation arm (`profit_sniper.py:1993-1999`). Because the micro arm (0.10%) and breakeven lock (0.05%) both sit below the ~0.11% round-trip taker fee, this prevents arming a sub-fee breakeven that a tiny pullback would tap for a guaranteed net fee loss. Observable via `MICRO_FLOOR_FEE_SUPPRESS` (`2036-2042`).
- **Zero-crossing break-even floor (Finding 6):** `ladder_breakeven_lock_pct = 0.05` (`config.toml:2031`). When the trade is armed but the step lock is at/below break-even (`lock_pct <= 0`), it guarantees at least this much locked profit (entry plus a sliver), so the stop ratchets to at least breakeven while price is still up (`profit_sniper.py:2063-2079`). `breakeven_floor = be_lock > 0.0 and lock_pct <= 0.0` (`2064`). A value ≤ 0 disables it.
- **Dead-band give-back trail (Fix 3):** `ladder_deadband_giveback_pct = 0.10` (`config.toml:2037`). For a peak in the `[arm, first_step)` dead band where `level=0`, instead of locking only the sliver and giving back the whole modest peak, it trails a floor this far below the high-water peak: `lock_pct = max(be_lock, peak - giveback)` (`profit_sniper.py:2075-2079`). Monotonic and bounded below by `be_lock`.
- **Fee-aware lock lift (Finding A):** `ladder_lock_fee_clearance_pct = 0.13` (`config.toml:2047`, must be ≥ the 0.11% round-trip taker fee). When a sub-fee floor would lock AND the peak has cleared the fee, the floor is lifted: `if _fee_clear > 0 and lock_pct < _fee_clear and peak > _fee_clear: lock_pct = _fee_clear` (`profit_sniper.py:2099-2101`). Bounded by peak, so it never sits above the high-water the trade reached. Step locks (~≥0.3%) already clear the fee. When the peak never cleared the fee, the existing sub-fee floor is kept (caps loss near the fee) but cannot be made net-positive.
- **Ladder stop price** (`profit_sniper.py:2103-2109`): long → `entry * (1 + lock_pct/100)`; short → `entry * (1 - lock_pct/100)`. `is_tighter` requires it to beat the current SL (tighten-only).
- **Graduation latch (`_graduated`)** at `profit_sniper.py:2619-2620`: `_graduated = _state.peak_pnl_pct >= _arm` where `_arm = min_profit_to_arm_ladder_pct = 0.2`. Logged once per position as `GRADUATION_LATCH` (`2624`). After graduation the entire loss-cutting block below `if not _graduated` is skipped (`2687`), except the always-on spike catastrophe stop which was deliberately hoisted out of the graduation gate (`2652-2686`).
- **First-lock jumps** (rate-limit bypass on the arming tick): `ladder_floor_jump_on_arm = true` (`config.toml:2093`) lets the zero-crossing breakeven floor land immediately on the not-armed→armed transition (one-shot, `profit_sniper.py:2893-2899`, `LADDER_FLOOR_JUMP` log `3065`). `ladder_first_lock_jump_enabled = true` (`config.toml:2107`) does the same for the first real step-rung lock on a fast young pop (`profit_sniper.py:2914-2922`, `LADDER_FIRST_LOCK_JUMP` log `3080`). Both bypass only R4 (rate-limit); R1 tighten-only and R2 min-distance still apply.

### 3b. The Chandelier / ATR trail — `_compute_trail_stop` (`profit_sniper.py:1790-1939`)

Trail distance is measured **from the peak price** (ratchet effect, never from current price), and the stop only tightens.

`trail_distance = base_atr_mult × ATR × regime_factor × profit_decay × momentum_factor` (`profit_sniper.py:1861`).

- **`base_atr_mult` is age-dialed:** `atr_multiple_young = 3.0` → `atr_multiple_old = 1.0` (`config.toml:1979-1980`), interpolated by the time dial (`time_dial.py:133-135`) and passed in as `_dialed.atr_multiple` (`profit_sniper.py:687`). Wide young (let the move breathe), tight old (protect gains).
- **ATR source:** `trail_live_m5_atr_enabled = true` (`config.toml:1991`) routes the trail to the warm-seeded live Wilder M5 ATR via `_get_current_atr` (`profit_sniper.py:659-660`); else it uses the cold ring-buffer `extension_result.atr_current` (`662`). `_pf_effective_atr` (`1715-1734`) provides the never-zero fallback chain: **live → entry-ATR → percent-of-price floor** = `atr_zero_fallback_pct = 0.5` (`config.toml:2132`). Non-live source emits `SNIPER_ATR_FALLBACK` (`profit_sniper.py:669-680`).
- **Regime factor** (`REGIME_TRAIL_FACTORS`, `profit_sniper.py:49-55`, mirrored in `config.toml:1749-1752`): trending 1.3, ranging 0.7, volatile 1.0, dead 0.6, balanced 0.85 (default). Wider in trends, tighter in ranges/dead regimes.
- **Profit decay** (`profit_sniper.py:1845-1847`): `profit_decay = 1/(1 + 0.2 × extension_atr)`, floored at `min_profit_decay = 0.50` (`config.toml:1888`). The further into profit (more ATR units extended), the tighter the trail.
- **Momentum factor** (`profit_sniper.py:1850-1858`): momentum-decay score < 20 → 1.1 (wider, let it run); < 50 → 1.0; < 75 → 0.8 (decaying, tighter); ≥ 75 → 0.6 (dying, very tight).
- **Micro-trail floor** (anti-suicidal-trail, `profit_sniper.py:1863-1889`): `min_trail = max(atr × min_trail_atr_multiplier, entry × min_trail_pct/100)` with `min_trail_atr_multiplier = 1.5` and `min_trail_pct = 0.30` (`config.toml:1878-1879`). If the computed distance is below this, it is widened to the floor; emits `M4_TRAIL_FLOOR`.
- **Breakeven floor on the trail** (`profit_sniper.py:1894`/`1898`): long → `trail_stop = max(peak − dist, entry)`; short → `min(peak + dist, entry)`. The trail can never sit worse than break-even.
- **Activation:** `in_profit = state.peak_pnl_pct >= self._pf.min_profit_for_trail_pct = 0.2` (`profit_sniper.py:1918`, `config.toml:2020`). This supersedes the deprecated `mode4.min_profit_for_trail_pct = 0.50` (`config.toml:1884-1887`).
- **Min-change throttle:** only applies if `change_pct > trail_min_change_pct = 0.1` (`profit_sniper.py:1904-1906`, `config.toml:1748`), to avoid flooding the gateway with tiny SL mods.
- `should_apply = in_profit and is_tighter and meets_threshold` (`profit_sniper.py:1920`).

There is also a separate from-current-price trail floor (mean-reversion guard) used in the legacy `_apply_trail_stop` path: `trail_floor_from_price_atr_multiplier = 0.75`, `_min_pct = 0.20`, `_max_pct = 1.50` (`config.toml:1903-1905`).

### 3c. The score-based action engine — `_determine_action` (`profit_sniper.py:3656-3901`)

Combines the regime-aware composite score with an anti-greed pullback backstop, then takes the more aggressive of the two by `ACTION_PRIORITY` (`profit_sniper.py:65-67`: hold 0 < tighten 1 < partial_close 2 < full_close 3).

- **Regime-aware score thresholds** (`THRESHOLD_SETS`, `profit_sniper.py:58-64`): tighten/partial/full are trending 50/70/85, ranging 35/55/70, volatile 40/60/75, dead 30/50/65, balanced 35/55/70. `score_action` is chosen at `3721-3728`.
- **Anti-greed pullback backstop** (`profit_sniper.py:3762-3777`, `anti_greed_enabled = true`): `pullback_pct = (peak − current)/peak × 100` (only when `peak > 0.1`). Rules: peak ≥ `anti_greed_pullback_75_min_peak = 5.0` AND pullback ≥ 75% → full_close; peak ≥ `anti_greed_pullback_60_min_peak = 3.0` AND pullback ≥ 60% → partial_close; peak ≥ `anti_greed_pullback_40_min_peak = 2.0` AND pullback ≥ 40% → tighten (`config.toml:1756-1758`).
- **Cooldowns** (`profit_sniper.py:3812-3859`): `tighten_cooldown_seconds = 15`; `min_seconds_between_actions = 60` (max'd with `tighten_cooldown_seconds`) gates partials, downgrading a cooled partial to tighten; `min_seconds_before_close = 180` (max'd with `partial_close_cooldown_seconds = 120`) gates score-branch full_close, downgrading to tighten. **Anti-greed full_close bypasses the cooldown** (`3849`, `3857-3859`). Config at `config.toml:1766-1767`, `1783-1784`.

### 3d. The profit guard (blocks sub-fee / small-profit exits)

Two gates block exits that would book a fee-eaten scratch:
- **P9 close gate** (`profit_sniper.py:3733-3740`): a score `full_close` with `current_pnl < min_profit_for_close = 0.50` is downgraded to `tighten` (so the SL protects while the TP runs). `min_profit_for_close` is not present in `config.toml`, so it takes the dataclass default `0.50` (`settings.py:2607`). Emits `P9_CLOSE_GATE`.
- **Partial profit gate** (`profit_sniper.py:3752-3760`): a score `partial_close` with `current_pnl < min_profit_for_partial_pct = 0.0` is downgraded to `hold` (requires at least break-even). Emits `M4_GATED ... reason=profit_gate`. Config at `config.toml:1788`.
- The fee-aware ladder lift and fee-aware micro-arm (§3a) are the structural profit guards that keep the *locked stop* itself net-positive rather than a sub-fee scratch.
- **Partial closes are globally disabled:** `sniper_partial_close_enabled = false` (`config.toml:1776`). Any score/greed/stall partial is downgraded to a winner-trail tighten in `_determine_action` (`profit_sniper.py:3867-3870`) and again hard-blocked at execution (`3968-3981`, `SNIPER_PARTIAL_CLOSE_DISABLED`). So in practice the profit side exits a winner only via the trailed/laddered SL or a full_close.

## 4. How it manages LOSS (from the profit side's perspective)

The profit-fetching side is deliberately **not** the loss manager — `_determine_action`'s profit gate returns `hold` for any `current_pnl <= 0` (`profit_sniper.py:3682`) precisely because "Mode4 exists to PROTECT PROFIT" (comment `3675-3681`). Loss management is delegated:

- **To the companion Loss-Cutting system**, invoked inside the same `_pf_apply_spine` (`profit_sniper.py:2608-2839`) but only while a trade is **not graduated** (`if not _graduated`, `2687`): the sacred dollar cap force-close, the stall exit, the structure stop, the ATR/cap candidate, and the final-phase recovery trail.
- **The always-on spike catastrophe stop** (`profit_sniper.py:2652-2686`) is hoisted OUTSIDE the graduation gate, so even a graduated winner that suddenly craters into loss is force-closed (`LOSS_SPIKE_STOP` → `_execute_full_close(..., closed_by="loss_spike_force", check_min_hold=False)`).
- **The breakeven floors** on both the ladder (`profit_sniper.py:1894`, `2104`) and the trail (`1894`/`1898`) are the profit side's own loss guard: once armed, the stop can never sit worse than entry, so a winner cannot round-trip into a loss below break-even.
- **The safety stop / naked-position sweeper** (`profit_sniper.py:2579-2606`): always a candidate when the position is naked, and re-asserted on any too-loose stop when `safety_floor_reassert = true` (`config.toml:2151`). Distance = `safety_stop_pct = 2.5` off entry (`config.toml:2127`), clamped just inside live price if it would sit on the wrong side. Source `safety_sweeper`.
- **The anti-greed backstop** (§3c) is technically a give-back/loss-prevention mechanism on a peaked winner.

So the profit side's loss handling is: never let a protected winner fall below break-even, hand genuine red trades to the loss system, and keep the spike catastrophe stop always live.

## 5. Inputs — what it reads and from where

- **Open positions:** `_get_positions()` via `self.position_service` (`profit_sniper.py:478`).
- **Per-position ring buffer** (`EnhancedRingBuffer`, `buffer_max_size = 720`, `buffer_min_ready = 100`, `config.toml:1743-1744`): prices, timestamps, volumes, buy/sell volume estimates, spreads, `atr_current`, and a per-tick `PositionProfitState` carrying `peak_pnl_pct`, `peak_price`, `trough_pnl_pct`, `trough_price`, `ticks_in_profit`, `profit_ratio` (`sniper_ring_buffer.py:305-342`). Peak/trough are updated each tick in `state.update()` (`profit_sniper.py:1491`, `sniper_ring_buffer.py:316-329`).
- **The five models** (`SniperModels`): Hurst, Momentum Decay, ATR Extension, Volume Divergence, Risk/Reward (`profit_sniper.py:566-617`), combined into a regime-aware composite (`_compute_composite_score`, `625`).
- **Per-coin regime** via `self.regime_detector` / `_get_regime` (`profit_sniper.py:624`).
- **Live M5 Wilder ATR** via `_get_current_atr` (`profit_sniper.py:660`, cached 30s) and entry-ATR from `state.atr_at_entry`.
- **Trade age & deadline** via `_pf_age_and_deadline` (`profit_sniper.py:1682-1713`): prefers the brain's `TradePlan.opened_at` + `max_hold_minutes` from `self.trade_coordinator`; falls back to `first_seen_at` + `default_deadline_minutes = 50.0` (`config.toml:2115`). Drives the `TimeDial`.
- **Current SL** via `_get_current_sl(pos)` reading `pos.stop_loss` (`profit_sniper.py:1676-1679`).
- **Gateway state:** `next_eligible_in_seconds`, `peek_owner`, `state_enforcement_active` from `self.sl_gateway` (`profit_sniper.py:2861`, `2933`).
- **Config:** `self._pf = settings.profit_fetching`, `self._lc = settings.loss_cutting` (`profit_sniper.py:227`, `268`); two `TimeDial` instances built from each (`228`, `269`).

## 6. Outputs / writes — does it write the stop, force-close, or only advise?

**It writes.** The profit side has exactly one capital-affecting write path per tick plus the close path:

- **The single gateway stop write** — `_pf_apply_spine` calls `await self.sl_gateway.apply(...)` exactly once per tick per position (`profit_sniper.py:3023-3034`). This is the authoritative SL writer; the legacy score-driven `_apply_trail_stop` path is explicitly skipped when `self._pf.enabled` (`3943`, `3973`, `4023`), so there is a **single stop-writer**. On accept it mirrors the applied SL onto the local `TradePlan.stop_loss_price` via `self.trade_coordinator` (`3088-3091`) and emits `SL_PROPAGATED` (`3101-3106`). The gateway enforces R1 tighten-only, R2 min-distance (`min_distance_pct = 0.3`, ATR-scaled via `min_distance_atr_multiplier = 0.5`), R3 max-step (`max_step_pct = 0.25`), R4 rate-limit (`rate_limit_seconds = 30`) (`sl_gateway.py:16-25`, `config.toml:960-970`). Profit-side sources `profit_sniper_ladder`, `profit_sniper_trail`, and `safety_sweeper` bypass **R3 only** (`profit_sniper.py:2997-3002`); urgent sources (naked fix, breakeven-floor/first-lock jumps, emergency cap) additionally pass `bypass_rate_limit=True` to skip R4 (`3032`). The breakeven floor passes `breakeven_floor_price = entry` so R2 clamps it at-or-above break-even (`3011-3020`).
- **Full close** — `_execute_full_close` (`profit_sniper.py:4837`) calls `self.position_service.close_position(...)`, but only after consulting `Layer4ProtectionService.is_protected(...)`; if the service is unwired it **refuses to close** (fail-loud, `4911-4918`). Records via `event_buffer`, `trade_coordinator.on_trade_closed`, `set_close_reason`.
- **Partial close** is disabled (`sniper_partial_close_enabled = false`), so no reduce-only fills occur from the profit side in current config.

So the profit side **writes the stop and can force-close** — it is not advisory.

## 7. Wiring — construction, callers, callees (dependency graph)

- **Constructed in** `src/workers/manager.py:1644-1670` as `ProfitSniper(settings, db, position_service, market_service, order_service, account_service, claude_client, alert_manager, transformer, trade_coordinator, event_buffer, ta_cache, regime_detector, volatility_profiler, sl_gateway, layer4_protection, structure_cache)`. Appended to `self.workers` and stored as `self._services["profit_sniper"]` (`1671-1672`). Only built when `mode4.enabled = true` and position/market services exist (`1675-1676`).
- **Driven by** `BaseWorker.run()` calling `tick()` every `interval_seconds = 5` (`base_worker.py:224-419`).
- **`tick()` calls** → `_get_positions`, `_update_position`, the five `SniperModels` methods, `_compute_composite_score`, `_get_regime`, `_pf_age_and_deadline`, `_time_dial.resolve`, `_pf_effective_atr`, `_get_current_atr`, `_compute_trail_stop`, `_compute_ladder_floor`, `_determine_action`, `_apply_anti_greed`, `_classify_score`, `_stall_escape_action`, then in the M5 loop `_pf_apply_spine` and `_execute_action`.
- **`_pf_apply_spine` calls** → `_pf_safety_floor`, the loss-cutting candidate builders (`_lc_spike_triggered`, `_lc_stall_decision`, `_lc_structure_stop`, `_lc_recovery_candidate`), `_pf_select_stop`, then `self.sl_gateway.apply` / `self.sl_gateway.peek_owner` / `next_eligible_in_seconds`, and on a spike/cap breach `_execute_full_close`.
- **`_execute_action` calls** → `_apply_trail_stop` (only when `not self._pf.enabled`), `_execute_partial_close` (disabled), `_execute_full_close`, `_send_mode4_alert`.
- **External dependencies:** `self.sl_gateway` (`src/core/sl_gateway.py`, the single SL chokepoint shared with the watchdog), `self.trade_coordinator` (trade plans, authoritative PnL, close booking), `self.position_service` (positions + close), `self.layer4_protection` (close veto), `self.regime_detector`, `self.structure_cache`, `self.event_buffer`, `self.alert_manager`.

## 8. How it fits the end-to-end pipeline

Once a trade opens, Mode 4 begins tracking it (`_on_position_opened`). Every 5 seconds it updates the peak, runs the models, and:

1. **Below the arm/graduation threshold (peak < 0.2%):** the **Loss-Cutting system owns the trade** through the same spine (`if not _graduated`), while the **micro-floor** (arm 0.10%) can still lock a small green if reachable. The graduation latch has not fired.
2. **At graduation (peak ≥ 0.2%):** `GRADUATION_LATCH` fires (one-way); loss-cutting yields and the **profit-fetching system owns the trade**. The ladder and Chandelier trail now both compute candidates each tick.
3. **The spine reconciles** ladder, trail, safety floor, and any still-eligible loss candidates under **highest-stop-wins** (`_pf_select_stop`, tightest = highest for long / lowest for short, must beat current SL) and writes the winner once through the gateway. Under owner-switch enforcement (only when `owner_switch_enabled` AND `owner_switch_enforce` — both off by default per `state_enforcement_active`, `sl_gateway.py:1209-1211`), `offer_profit`/`offer_loss` suppress the wrong owner's candidates; otherwise every candidate competes.
4. **The score action engine runs in parallel**, layering an additional tighten or (gated) full_close when the composite score or anti-greed backstop demands it, subject to the profit guards and cooldowns.

**Hand-offs:** it coordinates with the **SL Gateway** (the shared single-writer it hands every stop to, alongside the watchdog/SENTINEL/time-decay writers it now subordinates per `subordinate_watchdog_trail_exit = true`, `ride_winner_past_deadline = true`, `subordinate_profit_take = true`, `config.toml:2139-2144`); with the **Trade Coordinator** (mirrors the applied SL onto the plan, books closes); with **Layer4ProtectionService** (which can veto any full close); and with the **Loss-Cutting system** (the other half of the same engine, sharing the spine and gateway, separated only by the graduation latch). On a full close it terminates the pipeline for that symbol via `_execute_full_close` → `position_service.close_position` → `trade_coordinator.on_trade_closed`.

## Known weaknesses / failure modes

- **Rate-limit lag on the first lock.** R4 is 30s. Outside the one-shot `ladder_floor_jump_on_arm` / `ladder_first_lock_jump_enabled` bypasses, the first profit lock can lag up to 30s; the code itself documents that a fast pop can fade back through that gap before the lock is written ("the choppy-capture collapse", `profit_sniper.py:2900-2906`). The jumps are one-shot per position, so a *second* fast rung within 30s is still rate-limited.
- **Sub-fee scratch risk is config-coupled.** The 0.10% micro arm and 0.05% breakeven lock both sit below the ~0.11% round-trip taker fee. Protection from a net-fee-loss scratch depends entirely on `micro_floor_arm_fee_aware_enabled = true` and `ladder_lock_fee_clearance_pct = 0.13`; flip either off and the ladder will arm a sub-fee breakeven that a tiny pullback taps for a guaranteed net loss (the documented sub-2-minute fee-scratch mechanism, `config.toml:2062-2078`).
- **R2 can clamp the breakeven floor away on high-vol coins.** `min_distance_pct = 0.3` is larger than the 0.05% breakeven lock; the `breakeven_floor_price` pass-through (`profit_sniper.py:3011-3020`) is a mitigation, but on a coin whose min-distance exceeds the lock the floor can still be clamped (the `_sl_clamped` branch logs this but does not re-raise it).
- **Peak-anchored trail gives back the full leash.** The Chandelier trail measures from peak with a leash of up to `3.0 × ATR` young (`atr_multiple_young`), so a young winner can give back a large fraction of an unrealized peak before the stop triggers — by design (let it breathe), but a real exposure on a sharp reversal.
- **Single-writer-per-tick starvation.** The spine writes at most one stop per tick (`_pf_apply_spine` returns after the first accept/force-close). On a tick where a loss force-close path (spike/cap) returns early, the per-tick profit ratchet for that symbol is skipped that tick.
- **Profit side cannot act on red trades at all.** `_determine_action`'s hard `current_pnl <= 0 → hold` gate means the score engine is blind to any loss; all loss handling is delegated, and if the Loss-Cutting system is disabled (`loss_cutting.enabled = false`) only the spike stop, safety sweeper, and watchdog backstops remain.
- **Close depends on an external service being wired.** `_execute_full_close` refuses to close when `layer4_protection is None` (`profit_sniper.py:4911-4918`). A boot-order or wiring regression that leaves it unwired silently disables every profit-side and loss-side full close, leaving only the trailed SL and the -3% watchdog hard stop.
- **Partial closes are entirely disabled** (`sniper_partial_close_enabled = false`), so the anti-greed 60%-pullback partial and score-partial branches never reduce a position — they degrade to tightens. Realized give-back protection therefore rests solely on the trailed/laddered SL and the full_close path.

---

# Part 3 — Profit Sniper — Loss-Cutting Side

## 1. What it is and its single responsibility

The Loss-Cutting System is the protective half of the Profit Sniper worker (`src/workers/profit_sniper.py`), wired as a "second TimeDial" companion to the profit-fetching dial. It is constructed at sniper init from `settings.loss_cutting` and a dedicated loss-side time dial:

- `self._lc = settings.loss_cutting` (`profit_sniper.py:268`)
- `self._loss_dial = TimeDial(self._lc)` (`profit_sniper.py:269`)

Its single responsibility is to **own loss management for any open position that has NOT graduated to the profit side** — i.e. whose peak PnL never crossed the ladder-arm threshold — and to do so via one catastrophe force-close that is always on, plus a graduation-gated block of one more force-close, one stall force-close, and three tighten-only stop-loss candidates that compete in the shared "spine" selection. The authority split is stated explicitly in the block header at `profit_sniper.py:2608-2616`: while the trade has not graduated, the loss system contributes its cut/close decisions and its tighten-only SL candidates; once peak crosses the arm threshold the profit-fetching system owns the position.

Critically, the loss system never invents a second close timer or a second exchange call of its own. It either (a) calls the shared `_execute_full_close` to flatten the position, or (b) appends a candidate stop to `_loss_candidates` that competes in `_pf_select_stop` and is written through the shared `sl_gateway`. Both paths are described in sections 6 and 8.

## 2. When it activates — cadence and triggers

**Cadence.** The whole sniper monitors open positions on a fixed tick. The class docstring states "every 5 seconds" (`profit_sniper.py:123`), and the worker is constructed with `interval_seconds=float(settings.mode4.check_interval_seconds)` (`profit_sniper.py:171`); the live value is `check_interval_seconds = 5` in the `[mode4]` section of `config.toml` (config line 1740). Every loss mechanism therefore evaluates once per ~5-second tick per tracked symbol.

**Entry point.** On each tick the per-symbol loop at `profit_sniper.py:928-946` calls `_pf_apply_spine(symbol, pos, tracked, current_price)` for every tracked position with a buffer latest price, but only when `self._pf.enabled or self._lc.enabled` (`profit_sniper.py:938`). The entire loss-authority block lives inside `_pf_apply_spine`.

**Master gate.** The loss block is entered only when `self._lc.enabled and _state is not None and _entry > 0` (`profit_sniper.py:2618`). Config: `[loss_cutting] enabled = true`.

**The graduation latch.** Inside the block:

- `_arm = self._pf.min_profit_to_arm_ladder_pct` and `_graduated = _state.peak_pnl_pct >= _arm` (`profit_sniper.py:2619-2620`). This is a monotonic high-water latch on **peak** PnL, so it cannot flap around zero — once the peak crosses the arm threshold, the trade is "graduated" for life.
- The **spike catastrophe force-close runs OUTSIDE the latch** (always-on), gated only by `enable_spike_stop` (`profit_sniper.py:2664`). It is deliberately evaluated *before* the graduation branch so that even a graduated winner that suddenly crashes into loss is still cut (`profit_sniper.py:2652-2663`).
- Everything else — the sacred cap force-close, the stall exit, and the cap/structure/recovery SL candidates — is gated by `if not _graduated or tracked.get("_lc_crater_rearmed")` (`profit_sniper.py:2687`).

**The crater re-arm.** Because graduation is one-way, a winner that fully evaporates would otherwise permanently lose its tightening cap. The crater re-arm restores it once per position: when a graduated trade's current PnL drops to `<= -graduation_crater_loss_pct`, it sets `tracked["_lc_crater_rearmed"] = True` (`profit_sniper.py:2636-2651`). Config (both default-OFF / conservative): `graduation_crater_rearm_enabled = false`, `graduation_crater_loss_pct = 0.5`. With the flag false today, a graduated trade does NOT get the loss block back even on a crater — only the always-on spike protects it.

## 3. How it manages LOSS — the five techniques plus the always-on spike

All of these are loss-side mechanisms; there is no profit management on this side. I cover each technique, its exact trigger, and its config keys with current values.

### 3.0 Volatility-spike catastrophe force-close (always-on, outside the latch)

- **Where:** `profit_sniper.py:2664-2686`; detector `_lc_spike_triggered` at `2314-2378`.
- **Trigger:** only evaluated when current PnL is already negative (`if _spike_pnl_pct < 0`, `profit_sniper.py:2666`). The detector measures the adverse excursion over the recent ring-buffer window: for a long, `adverse = max(recent) - current_price` (drop from the recent high); for a short, `adverse = current_price - min(recent)` (`profit_sniper.py:2363-2366`). It fires when `adverse >= _mult * atr_value` (`profit_sniper.py:2377`).
- **Opening-seconds carve-out (Problem 3.4):** for the first `spike_young_opening_seconds` of the trade's life it uses the wider `spike_atr_move_mult_opening` so a young settling wiggle is not misread as a crash, then reverts to `spike_atr_move_mult` (`profit_sniper.py:2367-2376`). Age comes from `state.age_seconds`.
- **Time-independence:** deliberately outside the time dial (Rule 8) and closes with `check_min_hold=False` (`profit_sniper.py:2685`), so it can flatten a trade younger than the 5-minute settling contract and at any age.
- **Output:** an immediate `_execute_full_close(..., closed_by="loss_spike_force", check_min_hold=False)` (`profit_sniper.py:2681-2686`).
- **Config (current values):** `enable_spike_stop = true`, `spike_atr_move_mult = 2.5`, `spike_window_seconds = 30.0`, `spike_young_opening_seconds = 12.0`, `spike_atr_move_mult_opening = 3.8`.
- **Fail-safe:** if the buffer is missing or price is non-positive it returns `(False, 0.0, 0.0, 0.0)` (`profit_sniper.py:2334-2342`); if effective ATR is zero it cannot fire (`profit_sniper.py:2347-2348`); fewer than two points in the window means "still filling" and no fire (`profit_sniper.py:2361-2362`). In that cold window the legacy -3% watchdog hard stop is the backstop.

### 3.1 The sacred hard-cap force-close (the inviolable outer wall)

- **Where:** `profit_sniper.py:2719-2770`.
- **The cap dollars:** `_cap_dollars = _lc_net_cap_dollars( min(cap_dollar_ceiling, _notional * _ld.cap_pct / 100.0), _notional )`, only when `enable_hard_cap and _size > 0 and _notional > 0` (`profit_sniper.py:2726-2736`). So the gross cap is **min(fixed dollar ceiling, age-dialed percent of notional)** — the percent is normally the binding constraint, and the $75 ceiling only bounds the catastrophic worst case on a very large position (config comment, lines 2192-2204).
- **Net-aware cap (`_lc_net_cap_dollars`, `profit_sniper.py:1230-1249`):** subtracts the round-trip taker fee from the gross budget — `gross - notional * fee_pct/100` — so the realized NET loss lands at/under the ceiling rather than overshooting it by fees. It tightens only (floored at 0) and `fee_pct <= 0` is the clean off-switch. Applied to BOTH the force-close threshold and the placed cap SL.
- **`cap_pct` time dial:** `_ld.cap_pct` glides linearly from `cap_pct_of_notional_young` to `cap_pct_of_notional_old` across the trade's deadline (`time_dial.py:171-175`), so the cap tightens inward as the trade ages.
- **Trigger:** fires when `enable_hard_cap and force_close_when_cap_unplaceable and _cap_dollars > 0 and _loss_usd >= _cap_dollars` (`profit_sniper.py:2737-2740`), where `_loss_usd = (-_pnl_pct/100) * _notional` for a red trade (`profit_sniper.py:2693-2695`). This holds even when the cap distance is too tight to place as an SL on a high-vol coin — it is the wall that holds where the cap SL is un-placeable.
- **Slippage observability:** when realized loss is meaningfully past the cap (`_cap_overshoot > _cap_dollars * 0.02`) it logs `CAP_SLIPPAGE_OBSERVED` (`profit_sniper.py:2742-2757`) to surface a fast-gap fill that blew through between ticks.
- **Output:** `_execute_full_close(..., closed_by="loss_cap_force", check_min_hold=False)` (`profit_sniper.py:2765-2770`) — inviolable at any age (Rule 7).
- **Config (current values):** `enable_hard_cap = true`, `cap_dollar_ceiling = 75.0`, `cap_pct_of_notional_young = 2.5`, `cap_pct_of_notional_old = 1.0`, `cap_round_trip_fee_pct = 0.11`, `force_close_when_cap_unplaceable = true`, `cap_slippage_buffer_pct = 0.5`.

### 3.2 The stall-exit with the signs-of-life veto

- **Where:** `_lc_stall_decision` at `profit_sniper.py:2420-2557`; called at `2774-2778`.
- **Signs-of-life tracking (every tick):** it always appends `state.peak_pnl_pct` to `_lc_peak_hist` and `pnl_pct` to `_lc_pnl_hist`, both trimmed to `stall_signs_of_life_lookback_ticks`, and records `_lc_pnl_prev` (`profit_sniper.py:2438-2454`). This runs even when not stalled.
- **Stall gating:** returns early (no cut) unless `enable_stall_exit` is on, `pnl_pct < 0` (loss side only), `age_fraction >= stall_min_age_fraction` (dialed), and `age_fraction < stall_tail_yield_fraction` (`profit_sniper.py:2456-2463`). The dialed `stall_min_age_fraction` glides young→old (`time_dial.py:186-190`); with `young = 1.1` a young trade is never stall-cut, and `old = 0.55` lets it fire past ~55% of the deadline. Past `stall_tail_yield_fraction = 0.95` it yields to the watchdog's 95%-time loser timeout so the two never race.
- **The veto (the late-bloomer spare):** any one of three conditions vetoes the cut (`profit_sniper.py:2496`):
  - `_building` — the in-profit ratio `>= stall_signs_of_life_profit_ratio` (windowed last-N ratio if `stall_veto_windowed_profit_ratio_enabled` else cumulative `state.profit_ratio`) (`profit_sniper.py:2470-2474`).
  - `_improving` — current PnL above the lowest of the prior N ticks by `improving_floor_bps` if the sustained-improving flag is on, else simply `pnl_pct > _pnl_prev` (`profit_sniper.py:2481-2490`).
  - `_peak_rise` — `_hist[-1] - _hist[0] >= stall_signs_of_life_peak_improve_pct` over the lookback (`profit_sniper.py:2491-2495`).
  - On a veto it logs `LOSS_STALL_VETO` (~once/min), counts the sparings, and emits a one-shot `LOSS_STALL_VETO_BUDGET` warning at the budget (`profit_sniper.py:2496-2528`).
- **Yields:** even past the veto it defers to a same-tick `stall_escape` action (`profit_sniper.py:2530-2533`) and to a fresh "stable" struct-guard verdict (`< 60s` old) from `layer4_protection.get_struct_guard_verdict` (`profit_sniper.py:2535-2543`).
- **Output:** if none of the above spares it, `_execute_full_close(..., closed_by="loss_stall", check_min_hold=True)` (`profit_sniper.py:2552-2557`) — honoring the 5-minute settling contract. Returns True to short-circuit the tick (`profit_sniper.py:2774-2778`).
- **Config (current values):** `enable_stall_exit = true`, `stall_min_age_fraction_young = 1.1`, `stall_min_age_fraction_old = 0.55`, `stall_signs_of_life_peak_improve_pct = 0.15`, `stall_signs_of_life_profit_ratio = 0.25`, `stall_signs_of_life_lookback_ticks = 24`, `stall_veto_windowed_profit_ratio_enabled = false`, `stall_signs_of_life_sustained_improving_enabled = false`, `stall_signs_of_life_improving_lookback_ticks = 3`, `stall_signs_of_life_improving_floor_bps = 2.0`, `stall_veto_budget_warn = 8`, `stall_tail_yield_fraction = 0.95`.

### 3.3 The cap as a tighten-only SL candidate

- **Where:** `profit_sniper.py:2779-2806`.
- **Distance (`_lc_cap_stop_distance`, `profit_sniper.py:1211-1228`):** converts the net-aware cap dollars to a price distance `cap_dollars / size`, then pulls the trigger `cap_slippage_buffer_pct` percent INSIDE the ceiling — `raw * (1 - buf)`, with `buf` clamped to `[0, 0.99)` — so a market-stop's slipped fill still lands within the cap. This is placement only; the force-close in 3.1 stays at the true ceiling.
- **Stop price:** `_entry - _cap_dist` for a long, `_entry + _cap_dist` for a short (`profit_sniper.py:2787-2790`), source `"loss_cap"`.
- **Emergency variant:** if the computed cap SL would sit on the wrong side of live price (price already through it), it clamps to a just-inside-price stop at `current_price * (1 ± atr_zero_fallback_pct/100)` and flags the source `"loss_cap_emergency"` (`profit_sniper.py:2791-2805`). The emergency source is special-cased downstream as urgent (`_urgent_source`, `profit_sniper.py:2925`) so it bypasses the rate limit, and it remains a candidate even under owner enforcement (it is in the Head-floor allowlist, see section 8).
- **Output:** appends `("cap", _cap_stop, _cap_src)` to `_loss_candidates` (`profit_sniper.py:2806`). Never closes directly — it advises a stop that competes in the spine.

### 3.4 The structure stop just beyond X-RAY invalidation

- **Where:** `_lc_structure_stop` at `profit_sniper.py:2380-2418`; called at `2807-2823`.
- **Construction-time pre-prune:** offered only when `enable_structure_stop and self.structure_cache is not None and (not _state_enforce or _pnl_pct < 0)` (`profit_sniper.py:2811-2814`). The `_pnl_pct < 0` clause (a cheap early prune) keeps a loss stop off a green trade when owner enforcement is active; the authoritative suppressor is the spine's `offer_loss` gate (section 8).
- **Logic:** reads the shared `StructureCache` for the symbol, takes `market_structure.invalidation_level`, and places the stop a dialed ATR buffer beyond it — `inv - buf` below for a long, `inv + buf` above for a short, where `buf = structure_buffer_atr * atr_value` (`profit_sniper.py:2395-2417`). The buffer is `_ld.structure_buffer_atr`, glided young→old (`time_dial.py:181-185`), so it tightens with age.
- **Fail-safe:** returns None on cache miss, no invalidation level (`inv <= 0`), or a wrong-side computed stop (`profit_sniper.py:2399-2417`) — so the ATR/cap candidates still protect and a stale/wrong-side stop is never placed.
- **Output:** appends `("structure", _struct, "loss_structure")` to `_loss_candidates` (`profit_sniper.py:2820-2823`). Advises only.
- **Config (current values):** `enable_structure_stop = true`, `structure_buffer_atr_young = 0.50`, `structure_buffer_atr_old = 0.10`.

### 3.5 The final-phase history-aware recovery bounce trail

- **Where:** `_lc_recovery_candidate` at `profit_sniper.py:2246-2312`; called at `2824-2839`.
- **Gating:** offered only when `enable_history_recovery and _pnl_pct < 0 and _ld.age_fraction >= recovery_final_fraction` (`profit_sniper.py:2828-2832`) — i.e. red, and in the trade's last phase.
- **Logic (Chandelier on the loss side):** it tracks the recovery extreme since the worst trough (`_lc_recovery_ext`), resetting it whenever a new worse trough is made, and trails it toward live price (`profit_sniper.py:2263-2270`). It returns None until a real bounce off the trough has begun (`profit_sniper.py:2271-2276`). The trail distance depends on the trade's life history: a mostly-profit-side struggler (`profit_ratio >= recovery_profit_side_ratio`) earns the WIDER `recovery_bounce_trail_atr_profit_side`; a mostly-loss-side struggler gets the TIGHT `recovery_bounce_trail_atr_loss_side` to capture near least-loss (`profit_sniper.py:2284-2289`). The stop is `_rec - _dist` (long) / `_rec + _dist` (short), with wrong-side and non-positive checks (`profit_sniper.py:2292-2299`).
- **Output:** appends `("recovery", _rec_stop, "loss_recovery")` to `_loss_candidates` (`profit_sniper.py:2836-2839`). Advises only; the cap candidate wins if tighter, so the recovery always stays inside the cap.
- **Config (current values):** `enable_history_recovery = true`, `recovery_final_fraction = 0.80`, `recovery_profit_side_ratio = 0.50`, `recovery_bounce_trail_atr_profit_side = 1.5`, `recovery_bounce_trail_atr_loss_side = 0.40`.

### The loss-side time dial

There is a single linear clock shared with the profit side. `_pf_age_and_deadline(symbol)` (`profit_sniper.py:1682-1713`) returns `(age_minutes, deadline_minutes)` — preferring the brain's TradePlan `opened_at + max_hold_minutes`, falling back to the sniper's `first_seen_at` + `default_deadline_minutes` for externally-opened positions. That feeds `self._loss_dial.resolve_loss(_age_min, _deadline_min)` (`profit_sniper.py:2690-2691`), which lerps `cap_pct`, `atr_initial_multiple`, `structure_buffer_atr`, `stall_min_age_fraction`, and `winprob_cut_threshold` from their `*_young` anchors to their `*_old` anchors as `age_fraction` rises 0→1 (`time_dial.py:148-199`). The fraction saturates at 1.0 past the deadline, so all dialed values sit at their tight "old" anchors at/after the deadline. The spike parameters are deliberately NOT dialed (`time_dial.py:56-60`).

Two related notes: the `winprob_cut_threshold` is dialed here for observation/coordination only — the actual p_win force-close lives in the watchdog (config comment lines 2284-2293; `enable_winprob_observe = true`, `winprob_cut_threshold_young = 0.10`, `winprob_cut_threshold_old = 0.20`). The `atr_initial_multiple` dial drives the initial ATR stop (Technique 1) placed once at open via `_lc_place_initial_atr_stop` (`profit_sniper.py:1251-1303`), which is a placement event at tracking time, not part of the per-tick loss-authority block; `atr_initial_multiple_young = 3.0`, `atr_initial_multiple_old = 1.0`, `enable_atr_initial_stop = true`.

## 4. How it manages PROFIT

It does not. By the authority split (`profit_sniper.py:2608-2616`) the loss block contributes only cut/close decisions and tighten-only loss stops, and yields entirely to the profit-fetching system once `peak_pnl_pct >= min_profit_to_arm_ladder_pct`. Every loss SL candidate is tighten-only (enforced both in `_pf_select_stop` at `profit_sniper.py:2239-2243` and again by the gateway's R1), so it can never loosen a profit stop. The only "profit-adjacent" behaviors are protective: the stall veto deliberately spares a building late-bloomer rather than cutting it, and the recovery trail captures near least-loss on a red trade in its final minutes — both reduce realized loss, neither manages a winner.

## 5. Inputs (what data it reads, and from where)

- **Position object** (`pos`): `entry_price`, `size`, `side` — from `tracked["position"]`, sourced via `position_service` (`profit_sniper.py:939, 2688`, `_calculate_pnl_pct` at `4284-4299`).
- **Current price**: `tracked["buffer"].get_latest()["price"]`, the per-position ring buffer fed from the `market_service` ticker cache (`profit_sniper.py:940-945`).
- **PositionProfitState** (`_state` = `self._profit_states[symbol]`): `peak_pnl_pct`, `trough_pnl_pct`, `trough_price`, `profit_ratio`, `atr_at_entry`, `age_seconds`, `direction`, `entry_price` (used throughout: graduation at `2620`, spike at `2371`, recovery at `2264-2284`, stall at `2440-2473`).
- **Age and deadline**: `_pf_age_and_deadline` reads the brain TradePlan via `trade_coordinator.get_trade_plan` or falls back to `first_seen_at`/`default_deadline_minutes` (`profit_sniper.py:1682-1713`).
- **Effective ATR**: `_get_current_atr(symbol)` (live TA cache, last-good cache, then entry-ATR/pct floor via `_pf_effective_atr`) — used by spike, structure, recovery, and the initial ATR stop.
- **Structure cache**: `self.structure_cache.get(symbol).market_structure.invalidation_level` for Technique 3 (`profit_sniper.py:2396-2402`).
- **Gateway owner state**: `sl_gateway.state_enforcement_active` and `sl_gateway.peek_owner(...)` for the offer gating (`profit_sniper.py:2713-2718, 2856-2865`).
- **Struct-guard verdict**: `layer4_protection.get_struct_guard_verdict(symbol)` (stall yield, `profit_sniper.py:2537-2543`).
- **Per-position scratch** (`tracked` dict): the histories `_lc_peak_hist`, `_lc_pnl_hist`, `_lc_pnl_prev`, the recovery extreme `_lc_recovery_ext` / `_lc_trough_pnl_seen`, the latch flags `_lc_graduated_logged` / `_lc_crater_rearmed`, the ring buffer, and the veto counters.

## 6. Outputs / writes

There are exactly two output surfaces:

**Force-close (flatten the position).** The spike (`loss_spike_force`), the sacred cap (`loss_cap_force`), and the stall (`loss_stall`) all call `_execute_full_close(symbol, pos, score_data, closed_by=..., check_min_hold=...)` (`profit_sniper.py:4837-...`). That method first consults `layer4_protection.is_protected(...)` and **refuses to close if protected or if the protection service is unwired** (fail-loud/fail-safe, `profit_sniper.py:4880-4918`), then records the real exit reason on the coordinator via `trade_coordinator.set_close_reason` (`4928-4929`), and finally calls `position_service.close_position(symbol, close_trigger=str(closed_by)[:40])` (`4934-4937`), followed by `event_buffer` and coordinator bookkeeping. The spike and cap pass `check_min_hold=False` so they can flatten a sub-5-minute trade; the stall passes `check_min_hold=True`.

**Advise a stop (tighten-only SL candidate).** The cap-SL, structure, and recovery techniques only append to `_loss_candidates`. They do not write directly — the chosen candidate is written through `sl_gateway.apply(...)` after the spine selection (`profit_sniper.py:3021-3034`). Every loss source is in the R3-bypass list (`loss_cap`, `loss_cap_emergency`, `loss_atr_initial`, `loss_structure`, `loss_recovery`; `profit_sniper.py:2997-3002`), so it bypasses only the max-step cap; R1 tighten-only, R2 min-distance, and R4 rate-limit still apply. `loss_cap_emergency` is additionally flagged urgent (`_urgent_source`, `2923-2926`) so it bypasses the rate limit.

## 7. Wiring — the dependency graph

- **Constructed:** in `WorkerManager` at `src/workers/manager.py:1643-1672` as `ProfitSniper(... sl_gateway=..., layer4_protection=..., structure_cache=..., trade_coordinator=..., position_service=..., market_service=..., event_buffer=..., ta_cache=...)`, stored as `self._services["profit_sniper"]`.
- **Loss objects built at init:** `self._lc = settings.loss_cutting`, `self._loss_dial = TimeDial(self._lc)` (`profit_sniper.py:268-269`), with a boot sentinel `LOSS_CUTTING_CONFIG_LOADED` (`271-297`).
- **Called by:** the sniper's own per-tick loop (`profit_sniper.py:928-946`) → `_pf_apply_spine` → the loss-authority block.
- **Calls:** `_lc_spike_triggered`, `_lc_stall_decision`, `_lc_structure_stop`, `_lc_recovery_candidate`, `_lc_cap_stop_distance`, `_lc_net_cap_dollars`, `_calculate_pnl_pct`, `_pf_age_and_deadline`, `_loss_dial.resolve_loss`, `_get_current_atr` / `_pf_effective_atr`; then `_pf_select_stop` and `sl_gateway.apply`, or `_execute_full_close` → `layer4_protection.is_protected` → `position_service.close_position` + `trade_coordinator.set_close_reason` + `event_buffer.add_event`.
- **Dependencies that fail-safe when missing:** `structure_cache` None → Technique 3 simply not offered (`profit_sniper.py:2811-2813`); `sl_gateway` None → no SL write path; `layer4_protection` None → `_execute_full_close` refuses to close (`4911-4918`).

## 8. How it fits the end-to-end pipeline

On every tick `_pf_apply_spine` builds a candidate list. The profit side appends `ladder` and `chandelier`, the floor appends `safety`, and the loss block appends `cap`/`structure`/`recovery` to `_loss_candidates`. They all flow into `_pf_select_stop` (`profit_sniper.py:2866-2871`), which picks the **tightest** stop — `max` of the candidate prices for a long, `min` for a short (`profit_sniper.py:2231-2235`) — then re-checks tighten-only vs the current SL (`2239-2243`). The winner is written once through `sl_gateway.apply` (`3021-3034`).

The handoff between profit and loss is enforced two ways. First, construction-time: under owner enforcement the loss block pre-prunes its structure/recovery candidates on green trades via `_pnl_pct < 0` (`profit_sniper.py:2814, 2830`). Second, the authoritative gate: `_offer_loss = not (_sp_enforce and _sp_owner == "green")` from `sl_gateway.peek_owner` (`2860-2865`), passed into `_pf_select_stop`, where a non-green-owned trade drops every loss candidate EXCEPT the sacred cap — `loss_cap` and `loss_cap_emergency` compete in BOTH states as the Head floor and are never suppressed (`profit_sniper.py:2224-2228`). So even on a green-owned trade the cap remains the catastrophe backstop; it simply sits so far out it never wins a healthy green spine.

The two force-close families coordinate with the rest of the system by exiting early (the spike at `2681`, the cap at `2765`, the stall via `return True` at `2778`), so a closing tick never also writes an SL. Provenance is stamped via `closed_by` (`loss_spike_force` / `loss_cap_force` / `loss_stall`) and `set_close_reason`, which the watchdog and WS subscriber later read. The stall explicitly yields to the watchdog's 95%-time timeout (`stall_tail_yield_fraction`), to a same-tick `stall_escape`, and to a fresh struct-guard "stable" verdict, so it never double-cuts. The dialed `winprob_cut_threshold` is handed to the watchdog as the single owner of the p_win cut; the sniper adds no second cutter.

## Known weaknesses / failure modes

- **Graduated trades have a single point of protection.** Once `peak_pnl_pct >= arm`, the entire loss block (cap force-close, stall, cap/structure/recovery SLs) is gated off by `if not _graduated or _lc_crater_rearmed`. With `graduation_crater_rearm_enabled = false` today, a graduated winner that slowly bleeds back into a deep loss is protected ONLY by the always-on spike — and the spike only fires on a *violent* move (`adverse >= 2.5 * ATR` over 30s). A slow grind down past the cap is not caught by the loss block on a graduated trade; it relies on the profit side's trail/floor and the watchdog.
- **Spike blindness in the cold-buffer window.** `_lc_spike_triggered` needs a populated ring buffer and a non-zero ATR; on a brand-new or externally-detected position with `< 2` in-window points or ATR 0 it returns no-fire (`profit_sniper.py:2334-2362`), leaning entirely on the -3% watchdog hard stop during that bridge.
- **Cap slippage is bounded only by a fixed buffer.** The placed cap SL is pulled in `cap_slippage_buffer_pct = 0.5%` of the cap distance, but the force-close threshold remains at the true ceiling and a market-stop can still gap through between 5-second ticks. The code itself acknowledges this ("a fast-gap fill can still overshoot", config line 2225) and only logs `CAP_SLIPPAGE_OBSERVED` after the fact.
- **Default-OFF tighteners leave known leniencies live.** `stall_veto_windowed_profit_ratio_enabled = false` means the "building" veto still uses the cumulative lifetime profit ratio, so stale early profit can keep a now-dying trade alive (the documented flat-fader-to-deadline failure, config lines 2243-2252). `stall_signs_of_life_sustained_improving_enabled = false` means a single noise up-tick (`pnl_pct > _pnl_prev`) can still grant the "improving" reprieve.
- **Fail-safe-to-close-nothing.** `_execute_full_close` refuses to close when `layer4_protection` is None or throws (`profit_sniper.py:4896-4918`). This is intentionally fail-safe, but it means a misconfigured/unwired protection service silently disables every loss force-close (spike, cap, stall), leaving only the SL-candidate path — which itself depends on a present `sl_gateway`.
- **Structure stop depends on an external cache that can be stale.** It reads `invalidation_level` from a TTL-bounded `StructureCache`; on a miss it returns None and is simply absent. A stale-but-present level within TTL could place the structure stop off an outdated swing, though wrong-side and tighten-only checks prevent a pathological write.

Relevant files: `/root/trading-intelligence-mcp/src/workers/profit_sniper.py`, `/root/trading-intelligence-mcp/src/core/time_dial.py`, `/root/trading-intelligence-mcp/src/workers/manager.py`, `/root/trading-intelligence-mcp/config.toml` (`[loss_cutting]` lines 2165-2320, `[mode4] check_interval_seconds` line 1740).

---

# Part 4 — Position Watchdog

## 1. What it is and its single responsibility

The Position Watchdog (`class PositionWatchdog(BaseWorker)`, defined at `position_watchdog.py:92`) is the system's real-time, code-driven monitor and exit executor for every open position. Once a trade is open, the watchdog is the worker that polls the exchange/Shadow position set on a fixed cadence and, per position, decides whether to tighten the stop, hand off trailing to the sniper spine, force-close on a danger rule, or do nothing — and it is the system's reconciler for positions that closed externally (SL/TP/liquidation).

Its single responsibility is enforcement and detection at code cadence: it owns the deterministic, non-LLM exit rules (hard stop, timeout, profit-take, trailing, time-decay loser-lane), it is the single chokepoint through which several SL-modifying subsystems push their stop changes to the wire (`_push_sl_to_shadow` at `:1074`), and it is the authoritative close-detector that fans a close out to the rest of the system (`_detect_and_record_closes` at `:4405`). It does NOT do the long-horizon Claude strategic review itself in passive mode — it queues concerns to the brain and executes brain/SENTINEL decisions that others produced.

The docstring at `:92-112` states this directly: "Monitors all open positions every N seconds, detects when trades are going against us, sends Telegram alerts, and triggers Claude Brain for smart exit decisions."

## 2. When it activates — cadence and triggers

The base worker drives `tick()` on a loop: `BaseWorker.run` calls `await self.tick()` then `await asyncio.sleep(self.interval)` (`base_worker.py:330`, `:419`). The interval is set at construction from `settings.watchdog.check_interval_seconds` (`__init__` passes `interval_seconds=settings.watchdog.check_interval_seconds` at `:149`).

- Config key: `[watchdog] check_interval_seconds`. Current value in `config.toml` is `10` (default in `WatchdogSettings` is `10.0`, `settings.py:1313`). So one full monitoring cycle runs roughly every 10 seconds.
- A congestion detector inside `tick()` (`:703-716`) emits `WD_POLL_LAG` when the actual gap exceeds `check_interval_seconds * 2`, and `WD_TICK_GAP` when the gap exceeds 60s — these are diagnostics, not behavior changes.

`tick()` runs unconditionally each cycle, but several conditions short-circuit it or specific positions:
- It returns early if an exchange switch is in progress (`transformer.is_switching`, `:719-722`, logs `WD_PAUSED`).
- It uses `get_positions_with_confirmation()` (`:747`); if the result is not `confirmed` (a `TIMESTAMP_FAIL`/transport error), it logs `WD_GROUND_TRUTH_UNKNOWN` and returns without mutating any state, so an API error cannot be mistaken for "all positions vanished" (`:750-758`).
- Per-position, monitoring is skipped by the immunity gate (`coordinator.is_immune`, `:944`) and the maturity gate (`coordinator.get_maturity`, the 0-120s newborn grace, `:994-1003`). When the coordinator is absent it falls back to `MINIMUM_HOLD_SECONDS` per strategy category (`:599-605`, `:953-962`).
- Each `_monitor_position` call is wrapped in `asyncio.wait_for(..., timeout=3.0)` (`:1020`) so one slow position cannot starve the rest (`WD_MONITOR_TIMEOUT`).

The deadline-tied subsystems activate on conditions within a tick rather than on the clock: SENTINEL deadline logic fires only when `plan.is_expired` is True (`:2254`); the time-decay loser-lane runs only when `pnl_pct < 0 and plan is not None` (`:2674`); the trailing checks run only while a plan is present and profit thresholds are crossed.

The watchdog also runs a **3-mode state machine** (`_determine_mode`, `:607`) recomputed every tick at `:726`:
- `passive` (default): observe and queue to brain; Claude is boss.
- `safety_net`: triggered when Claude is offline >10min (heartbeat staleness check `:678-684`), `claude_client._consecutive_failures >= 3` (`:685-687`), or `self._consecutive_losses >= 5` (`:688`). The hardcoded danger rules still fire because they live in `_monitor_position` regardless of mode.
- `emergency`: triggered when `_session_pnl_pct < settings.watchdog.emergency.session_pnl_threshold_pct` (default and config value `-5.0`, `config.toml [watchdog.emergency]`) OR `_hard_stops_this_hour >= settings.watchdog.emergency.hard_stops_per_hour_threshold` (config value `5`, `:650`). In emergency mode `tick()` closes ALL positions with `close_trigger="wd_emergency"` (`:854-897`) and requires manual restart.

## 3. How it manages PROFIT

The watchdog has several winner-management mechanisms, all of which are now mostly subordinated to the Profit Sniper spine when Profit-Fetching is enabled.

**Profit-Fetching subordination gate.** `self._pf = settings.profit_fetching` (`:181`). With `[profit_fetching] enabled = true` and the three sub-switches all true in config:
- `_pf_trail_off = self._pf.enabled and self._pf.subordinate_watchdog_trail_exit` (`:2240`). Config `subordinate_watchdog_trail_exit = true` → the watchdog's own trailing is fully disabled.
- `_pf_pt_off = self._pf.enabled and self._pf.subordinate_profit_take` (`:2778`). Config `subordinate_profit_take = true` → the +1.5% profit-take is disabled.
- `ride_winner_past_deadline = true` → a still-profitable expired trade is NOT force-closed.

**Mechanism A — TradePlan percentage trail (sources `trail_activation`, `trail_update`).** In `_monitor_position`, CHECK 2 (`:2407`) activates trailing when `not plan.trailing_active and pnl_from_plan >= plan.trailing_activation_pct and not _pf_trail_off`. The activation percent defaults to `1.0%` with a hard floor of `1.0%` enforced in `TradePlan.__post_init__` (`trade_plan.py:47`, `:55-56`). CHECK 3 (`:2430`) calls `plan.update_trailing(current_price)` and pushes the new SL via `_push_sl_to_shadow(..., source="trail_update")` only if the trail price changed. The trail distance is `trailing_distance_pct` (default 50%) of profit-from-entry, floored at 0.5% of price (`trade_plan.py:33`, `:58-64`, `:104-124`). `should_trail_exit` (`:2443`) closes with `close_trigger="wd_trail"` / `closed_by="trailing_stop"` if price crosses the trail. With config as-is, this entire block is dead (gated off by `_pf_trail_off`).

**Mechanism B — lock-peak and breakeven trail (sources `watchdog_lock_peak`, `watchdog_breakeven`).** A second autonomous winner-trail at `:2861`, gated `if self.coordinator and pnl_pct > 0.5 and not _pf_trail_off`:
- Lock-peak (`:2864`): when `peak_pnl_pct > 4.0 and pnl_pct < peak_pnl_pct * 0.6`, it locks 50% of peak profit — `new_sl = entry * (1 ± peak_pnl_pct*0.5/100)` — pushed with `source="watchdog_lock_peak"` only if it tightens.
- Move-to-breakeven (`:2892`): when `peak_pnl_pct > 2.0 and pnl_pct < peak_pnl_pct * 0.5`, it sets SL to entry (`breakeven_sl = pos.entry_price`), pushed with `source="watchdog_breakeven"`, guarded so it only fires when breakeven is genuinely tighter than the current SL (`:2898-2904`). These thresholds (4.0/0.6, 2.0/0.5) are hardcoded constants, not config keys. Also fully gated off by `_pf_trail_off`.

**Mechanism C — time-of-day profit-take.** At `:2779`: `if plan and pnl_pct > 1.5 and plan.max_hold_minutes > 0 and not _pf_pt_off` and `time_used_pct > 50`, it closes with `close_trigger="wd_profit_take"` / `closed_by="profit_take"`. The +1.5% and 50%-of-hold thresholds are hardcoded. Gated off by `_pf_pt_off`.

**Mechanism D — SENTINEL deadline profit tier.** When `plan.is_expired` and the deadline engine returns the `profit` tier (`pnl_pct >= deadline_profit_pct`, config `0.5`), the legacy behavior was to lock the win by closing. With `ride_winner_past_deadline = true` (`:2293-2305`), a profitable expired trade logs `SNIPER_DEADLINE_RIDE` and returns without closing, letting the sniper trail capture it. The binary-fallback path (SENTINEL disabled) has the same ride logic at `:2350-2361`.

**Brain-driven profit actions.** The brain/strategic path can emit `take_profit` and `set_exit` (TP) actions, executed in `_execute_strategic_actions` (`:4166`, `:4197`).

## 4. How it manages LOSS

This is the watchdog's most active domain. Mechanisms in `_monitor_position` order:

**Hard stop (catastrophic floor).** `:2596`: `if pnl_pct < -3.0` → immediate `close_position(close_trigger="wd_hard_stop")`, `closed_by="hard_stop"`, increments `_hard_stops_this_hour`, fires a HIGH event and Telegram alert. The `-3.0` threshold is hardcoded (not a config key); the profit-fetching `safety_stop_pct = 2.5` sits inside this as the sniper's non-climber cap.

**Time-Decay loser-lane (source `time_decay`).** `:2674`: only when `pnl_pct < 0 and plan is not None`, calls `_handle_time_decay` (`:1555`). This is the pnl<0 pre-gate. The method:
- Returns early if `self._time_decay is None` (disabled), or if the coordinator reports the symbol in any cooldown (`is_symbol_in_any_cooldown`, `:1585-1593`).
- Lazy-inits per-symbol `TimeDecayState` on the first loser tick (`:1597-1757`), pulling ATR/volatility-class from the profiler, regime confidence, original SL %, and entry-time XRAY/regime anchors from `coordinator._trades` or a `trade_thesis` SELECT. It inherits the MAE high-water mark from `_td_mae_high_water` (the T1-2 preservation mechanism, `:1701`).
- Calls `td_observe` then `self._time_decay.calculate(...)` (`:1801`) with structural-invalidation evidence from `layer4_protection.compute_structural_invalidation` (or the inline `_compute_structural_invalidation` fallback at `:1433`).
- Three outcomes: `None` → no-op (grace/not-tighter); `-1.0` → **force-close** with the truthful reason from `state.force_close_reason` (e.g. `win_prob_force_close`, `monotonic_grind_cut`), set as `close_trigger`, `closed_by`, and event name (`:1834-1927`); any other float → a tighter SL pushed via `_push_sl_to_shadow(source="time_decay")` (`:1929-1939`).

The full TimeDecayConfig is built in `__init__` (`:274-435`) from `[time_decay]` settings — dozens of keys including `p_win_force_close`, `grace_seconds`, `atr_room_multiplier`, `min_age_seconds` (default 300.0), `mae_to_sl_ratio_threshold` (0.5), `structural_invalidation_required` (True), `slow_bleed_cumulative_force_close_enabled`, `monotonic_grind_cut_enabled`, and `near_certain_loser_p_win` (default 0.10, optionally sourced from `[loss_cutting] winprob_cut_threshold_young`, `:255-273`). The min-price-relative-distance floor is pulled from `settings.sl_gateway.min_distance_pct` so it shares the gateway's source of truth (`:428-434`).

**Timeout (out-of-time loser).** `:2713`: `if plan and pnl_pct < 0 and plan.max_hold_minutes > 0` and `time_used_pct > timeout_threshold_pct` (config-readable `[watchdog] timeout_threshold_pct`, default `95.0`, `:2714`). If nearly flat (`pnl_pct >= -0.5`) and not already extended, it grants a one-time +10min extension (`TIMEOUT_EXTEND`, `:2719-2733`, capturing `_original_max_hold_minutes` for the sniper's frozen dial); otherwise closes with `close_trigger="wd_timeout"` / `closed_by="timeout"`.

**SENTINEL deadline loss tiers.** On `plan.is_expired`, `DeadlineEngine.evaluate` (`deadline.py:78`) returns tiered actions read from `[sentinel]` config: `deadline_profit_pct=0.5`, `deadline_breakeven_lower_pct=-0.3`, `deadline_small_loss_pct=-1.5`, `deadline_grace_minutes=5.0`, `deadline_small_loss_sl_pct=0.5`. Tier 2 (breakeven zone, pnl >= -0.3) sets SL to entry and grants a 5-min grace (`should_close=False`, SL pushed via `source="sentinel_deadline"`). Tier 3 (small loss, pnl >= -1.5) tightens SL to -0.5% from entry. Tier 4 (big loss, pnl < -1.5) closes immediately. The watchdog applies any recommended SL (`:2271-2284`) and closes with `closed_by="sentinel_deadline_{tier}"` when `should_close` (`:2306-2341`).

**Early-exit (regime-aware, currently disabled).** CHECK 4 (`:2483`): for `pnl_from_plan < -1.0` past 50% of hold time, with three suppression gates (brain-said-hold, regime-aligned, SL-buffer < 70%). Even when all gates fail it does not fire because `[watchdog] early_exit_enabled = false` (0% historical win rate, 24/24 losses) — it only logs `EARLY_EXIT_DISABLED_WOULD_FIRE` (`:2530-2544`).

**Loss alerts / brain triggers.** Warnings are collected (`:2927-2961`) against `loss_warning_pct` (config `0.5`), `trailing_loss_pct` (config `0.3`), `rapid_move_pct` (config `0.5`, suppressed when choppiness > 60), `sl_proximity_pct` (config `30.0`). In passive mode, if `pnl_pct < -brain_trigger_loss_pct` (config `0.8`) or `len(warnings) >= 2`, it calls `_maybe_trigger_brain` (`:3007-3009`).

**Brain-vote loss handling (wd_brain_scoring path).** When the brain queues a `close`/`take_profit` strategic action, `_execute_strategic_actions` runs the multi-factor close score (`compute_brain_close_score`, `:4064`) before executing. Config: `wd_brain_scoring_enabled = true`, `wd_brain_scoring_enforce = true` (so it is in enforce mode in production), `wd_brain_scoring_threshold = 6.0`, `wd_hard_risk_floor_sl_pct = 85.0`. In enforce mode (`:4126-4151`):
- `composite >= 6.0` → `execute` (fall through to close).
- `0 <= composite < 6.0` → `reject` → `_scoring_skip_close=True`, the close is suppressed (`WATCHDOG_CLOSE_REJECTED`).
- `composite < 0` → `reject_and_tighten` → calls `_tighten_sl_breakeven_30pct` (`:1378`, source `wd_brain_scoring`) which moves SL 30% of the remaining SL→entry distance toward entry, then suppresses the close (`WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`).
- Hard-risk-floor override (`:4090`, `:4112`): if `_sl_consumption >= 85.0`, the close fires regardless of composite (`WATCHDOG_HARD_FLOOR_HIT`). This catches the case where the position is burning its risk budget but the composite was below threshold.

The 300s min-hold guard (`strategic_action_min_hold_seconds = 300.0`) blocks young discretionary closes unless the reason matches `strategic_action_allowed_early_close_reasons` (`:3715-3756`).

## 5. Inputs (what it reads, and from where)

- **Open positions**: `position_service.get_positions_with_confirmation()` / `get_positions()` (`tick`, `:747-761`); per-symbol `get_position()` for re-verification in strategic/sentinel paths.
- **Live prices/ticker**: `market_service.get_ticker(symbol)` (`_monitor_position` `:2212`; maturity gate `:984`).
- **Trade plans + trade state**: `coordinator.get_trade_plan(symbol)`, `coordinator._trades`, `coordinator.is_immune`, `get_maturity`, `update_peak_pnl`, `get_age_seconds`, `get_age_context_for_prompt`, `get_trade_info`, `is_symbol_in_any_cooldown` (throughout).
- **M5 klines (batched)**: `market_repo.get_klines_batch(syms, "5", 60)` prefetched once per tick (`:816`), consumed by `ta_engine.analyze` for choppiness.
- **Volatility/regime/structure**: `volatility_profiler.get_profile`, `regime_detector.get_coin_regime`, `structure_cache.get(symbol)` (time-decay init and brain scoring).
- **Entry-time anchors / recovery rows**: `trade_thesis` and `orders` tables via `db.fetch_one` (time-decay anchors `:1677`; close-recovery `:4571`, `:4602`).
- **Account context**: `account_service.get_wallet_balance()` (for brain prompts).
- **Queued advice**: `coordinator.drain_strategic_actions()` (brain/LayerManager actions), `_sentinel_advisor.drain_recommendations()`, `_sentinel_deadline.evaluate()`, `urgent_queue` concerns.
- **SENTINEL config**: `settings.sentinel.advisor_min_profit_for_tighten_pct` (config `0.50`).

## 6. Outputs / writes

**The single SL-write chokepoint: `_push_sl_to_shadow` (`:1074`) → `sl_gateway.apply` (`:1292`).** Every SL change from the watchdog flows through this one helper. It performs consumer-side pre-checks (no-op guard `:1127`; R4 rate-limit pre-check via `next_eligible_in_seconds` `:1166`; per-source coalescing windows of 10s for `time_decay`, `trail_*`, and `sentinel_*` `:1183-1239`; a trail-only step-clamp to `max_step_pct` `:1262-1288`), then delegates to `sl_gateway.apply(symbol, new_sl, source, direction, plan, current_sl, entry_price)`. The gateway is the actual writer — it enforces R1 tighten-only, R2 min-distance, R3 max-step, R4 rate-limit, and calls `position_service.set_stop_loss` (`sl_gateway.py:16-24`). On accept, the helper mirrors the applied SL onto `plan.stop_loss_price` (`:1320-1321`) and logs `SL_PROPAGATED`. A legacy non-gateway path (`:1334-1376`) replicates tighten-only and writes directly for unit tests.

**The complete set of `source=` strings the watchdog writes** (`grep source="`):
- `trail_activation`, `trail_update` — TradePlan percentage trail.
- `watchdog_lock_peak`, `watchdog_breakeven` — lock-peak and move-to-breakeven trails.
- `time_decay` — loser-lane dynamic SL.
- `sentinel_deadline` — deadline-engine SL tightening on expiry.
- `sentinel_advisor` — SENTINEL Portfolio Advisor stop-tightening.
- `brain_tighten` — strategic-action `tighten_stop` from the brain/LayerManager.
- `watchdog_tighten` — brain `WatchdogDecision.tighten_stop` (`_execute_tighten_stop`).
- `wd_brain_scoring` — the 30%-toward-breakeven tighten when a brain close is overridden.

All of these are classified as ADVISORY sources in the gateway's owner-switch buckets (`settings.py:1532-1537`).

**Force-close / full-close writes** go through `position_service.close_position(close_trigger=...)`, not the SL gateway. The complete trigger set: `wd_hard_stop`, `wd_timeout`, `wd_profit_take`, `wd_trail`, `wd_early_exit`, `wd_dl_action`, `wd_plan_timer`, `wd_full_close`, `wd_claude_action`, `wd_emergency`, `wd_dup_close`, plus the time-decay reason-stamped triggers (`win_prob_force_close`, `monotonic_grind_cut`, etc.). Partial close uses `position_service.reduce_position` (`:3395`); TP uses `set_take_profit` (`:4198`).

**Coordinator close fan-out.** Every close calls `coordinator.resolve_authoritative_pnl(...)` then `coordinator.on_trade_closed(..., closed_by=...)` with the `closed_by` strings: `hard_stop`, `timeout`, `profit_take`, `trailing_stop`, `early_exit`, `plan_timer`, `sentinel_deadline_{tier}`, `watchdog`, the force-close reason, and (for externally-detected closes) the popped `close_reason`. `on_trade_closed` is the system-wide broadcast that drives thesis close, trade_log, daily_pnl, sniper cleanup, and the strategist.

**Advisory-only writes (no SL, no close).** `_detect_ensemble_flip` (`:1946`) and `_monitor_thesis_state` (`:2081`) only queue `ensemble_flip` / `thesis_invalidation` events for the brain's next CALL_A/CALL_B; they explicitly do not force-close or modify the stop. Telegram alerts via `alert_manager` and event-buffer entries are observational outputs.

## 7. Wiring — construction and dependency graph

**Construction.** `WorkerManager` builds the watchdog at `manager.py:1559-1595`, gated on `s.watchdog.enabled` and the presence of position+market services. It is injected with: `position_service`, `market_service`, `order_service`, `account_service`, `claude_client`, `cost_tracker`, `decision_parser`, `risk_manager`, `alert_manager`, `ta_engine`, `trade_coordinator`, `event_buffer`, `data_lake`, `transformer`, `regime_detector`, `urgent_queue`, `volatility_profiler`, `sl_gateway`, `thesis_manager`, `structure_cache`, and `ensemble_state_cache`. It is registered as `self._services["position_watchdog"]`.

**Post-init wiring (built after the watchdog):**
- `Layer4ProtectionService` is constructed AFTER the watchdog because it reuses the watchdog's `_time_decay` calculator; the manager then sets `watchdog.layer4_protection = layer4_protection` (`manager.py:1616-1626`).
- SENTINEL `DeadlineEngine` is assigned to `watchdog._sentinel_deadline` (`manager.py:3169`), gated on `settings.sentinel.enabled`.
- SENTINEL `PortfolioAdvisor` is assigned to `watchdog._sentinel_advisor` (`manager.py:3187`) and runs its own background assessment loop every `advisor_interval_seconds` (config 300), draining into the watchdog's `_execute_sentinel_recommendations`.

**What it calls (dependency graph):**
- Down to the broker: `position_service` (positions, close, reduce, set_stop_loss via gateway, set_take_profit, get_last_close) and `sl_gateway.apply`.
- To the coordinator: trade plans, peaks, immunity/maturity, strategic-action drain, authoritative-pnl resolution, `on_trade_closed` fan-out.
- To the time-decay engine: `TimeDecaySLCalculator` (`src/risk/time_decay_sl.py`) and `Layer4ProtectionService` for structural-invalidation.
- To the brain stack: `claude_client.send_message`, `cost_tracker`, `decision_parser`, `urgent_queue`, and `wd_brain_scoring.compute_brain_close_score`.
- To SENTINEL: `_sentinel_deadline.evaluate`, `_sentinel_advisor.drain_recommendations`.
- Observability: `alert_manager`, `event_buffer`, `data_lake.write_position_snapshot`.

**Who calls it:** `BaseWorker.run`'s loop is the only caller of `tick()`. The manager's worker-start ordering at `manager.py:1230` lists `position_watchdog` between `strategy_worker` and `profit_sniper`.

## 8. How it fits the end-to-end pipeline

The watchdog is the per-tick code-rules executor that sits between the slower brain/SENTINEL advisors and the broker, and it coordinates closely with the Profit Sniper:

- **Hands trailing to the sniper.** When `[profit_fetching]` is enabled with the three subordination switches on, the watchdog deliberately disables its own trail (`_pf_trail_off`), skips its profit-take (`_pf_pt_off`), and rides winners past the deadline. The sniper's ladder + Chandelier spine becomes the sole trailing-SL writer (single-writer invariant). The watchdog keeps the non-climber backstops: the -3% hard stop, the loser timeout, time-decay loser-lane, and the SENTINEL big-loss cut.
- **Executes others' decisions.** It drains and executes brain/LayerManager strategic actions (`_execute_strategic_actions`) and SENTINEL advisor recommendations (`_execute_sentinel_recommendations`), applying its own scoring/min-hold guards before letting a brain close fire.
- **Feeds the brain.** In passive mode it queues `WatchdogConcern` to the `urgent_queue` and `ensemble_flip`/`thesis_invalidation` events to the buffer for the brain's next CALL_A/CALL_B.
- **Coordinates the STRUCT_GUARD handshake.** It records the structural-invalidation verdict via `layer4_protection.record_struct_guard_verdict` (`:1821-1827`) so the sniper can defer its stall-escape when structure still holds.
- **Owns close-detection for the whole system.** `_detect_and_record_closes` (`:4405`) is the authoritative external-close detector: per tick it diffs `_last_known_symbols` against the live set, and for every vanished symbol it resolves the authoritative exit price (Shadow `get_last_close` with identity hints → ticker → last-tick cache), back-derives PnL, infers `sl_hit`/`tp_hit`/`exchange_match`, and fires `coordinator.on_trade_closed` once, which fans out to thesis close, trade_log, daily_pnl, sniper, transformer, and strategist. The fast set-diff reconciler (`_reconcile_with_shadow_fast`, `:4343`, cadence `fast_reconcile_seconds = 30.0`) and the 5-min zombie thesis reconciler (`thesis_manager.reconcile_with_shadow`, `:769-779`) are the safety nets that catch Shadow-side closes between ticks and orphan theses whose position vanished. It hands its close events to every downstream consumer; nothing downstream re-detects closes independently of this path.

## Known weaknesses / failure modes

- **Heavy reliance on the coordinator and Shadow truth.** Most close-bookkeeping, authoritative-PnL resolution, and cooldown logic flow through `self.coordinator`; when it is absent, PnL falls back to `_last_pnls` / locally back-derived notional (`:4667-4674`), and the 300s min-hold guard fail-closes a young close (`_age_sec=0` → blocked). External-close PnL is only fee-accurate when Shadow returns `exchange_authoritative` data; otherwise the watchdog back-derives a gross figure that ignores fees/funding (`WD_CLOSE_PRICE_FALLBACK`).

- **Degraded-data commit blocking can stall a trade.** When `entry == exit` and the price source is non-authoritative, the close commit is blocked and retried up to `_PNL_MISMATCH_RETRY_LIMIT = 5` ticks (`:4809`), after which it force-commits a corrupted row (`WD_PNL_MISMATCH_FORCED`). A persistently broken close path therefore books a defective record after ~50s rather than never.

- **Config can silently neuter whole mechanisms.** With current config (`subordinate_*` all true, `early_exit_enabled=false`), the entire TradePlan trail, the lock-peak/breakeven trails, the +1.5% profit-take, and the early-exit gate are dead code paths — winner management depends entirely on the sniper spine being healthy. If the sniper is disabled or failing, the watchdog's only remaining winner protection is the SENTINEL deadline tiers, leaving running winners largely unmanaged below the -3% hard stop.

- **Hardcoded magic thresholds.** The -3% hard stop, the +1.5% profit-take, the lock-peak ratios (4.0/0.6, 2.0/0.5), and the breakeven ratios are constants in code, not config keys, so tuning them requires a code change despite the rest of the system being config-driven.

- **Per-position 3s timeout can drop monitoring.** A position whose `_monitor_position` exceeds 3s is abandoned for that tick (`WD_MONITOR_TIMEOUT`); under DB contention a slow position can repeatedly miss its time-decay/trail evaluation, and the only backstop is the next tick.

- **Fail-open philosophy on the structural gate.** When structure-cache or entry anchors are missing, the structural-invalidation gate fail-safes to block force-close (preferring false-negative invalidations over false-positive force-closes). This is deliberate but means a genuine bleeder whose anchors were lost across a restart can ride longer than intended, protected only by the timeout and -3% stop.

- **Advisory bucket demotion looming.** All watchdog SL sources are ADVISORY in the gateway's owner-switch buckets; if `owner_switch_enforce`/`advisory_enforce` are ever turned on (currently false), every watchdog tighten on a green trade would be deferred to the Head/green-owner, materially changing behavior without any code change in this file.

---

# Part 5 — Time-Decay Loss Engine

## 1. What it is and its single responsibility

The Time-Decay SL engine is a **stateless five-model calculator** that lives in `src/risk/time_decay_sl.py`. Its single responsibility is to make the loss-side stop-loss decision for an *already-open, underwater* position: each tick it either (a) computes a **tighter** stop-loss price to push, (b) returns a **force-close sentinel** to kill a statistically-dead trade, or (c) **no-ops** when a guard blocks it or the new budget would not be tighter than the last one.

The module's own docstring states the design contract explicitly: "Pure math. Stateless calculator + per-symbol state dataclass. The watchdog owns IO (volatility profile fetch, regime fetch, push_sl_to_shadow, close)." (`time_decay_sl.py:3-4`). The engine never touches the exchange, the database, or the regime detector itself — it reads a `TimeDecayState` dataclass passed in by the caller, mutates running metrics on it, and returns one of three values. All side effects on the world (pushing the stop, closing the position) are performed by the `PositionWatchdog`.

The "5 models" are combined multiplicatively into an *allowed-loss budget* (`time_decay_sl.py:6-16`):

```
allowed_loss = atr_room * time_factor * recovery_multiplier * momentum_multiplier * probability_multiplier
allowed_loss = max(allowed_loss, MIN_ALLOWED_LOSS_PCT)   # 0.15% floor
allowed_loss = min(allowed_loss, original_sl_pct)        # never widen the stop
```

Model 1 is the convex time decay, Model 2 the ATR-scaled base room, Model 3 the MAE-recovery multiplier, Model 4 the velocity/acceleration switch, Model 5 the Bayesian win-probability `p_win`. On top of the budget sit a stack of independent force-close *gates and carve-outs* (age, MAE-to-SL ratio, structural invalidation, near-certain-loser, slow-bleed, monotonic-grind, recovery guard).

The two main entry points are `observe()` (a free function, `time_decay_sl.py:1198-1217`) and `TimeDecaySLCalculator.calculate()` (`time_decay_sl.py:421-990`). `create_state()` (`time_decay_sl.py:338-419`) seeds a fresh per-position state.

## 2. When it activates — cadence and triggers

**Cadence.** The engine runs on the `PositionWatchdog` monitor tick. The watchdog's `check_interval_seconds` defaults to `10.0` (`src/config/settings.py:1313`) and is set to `10` in production (`config.toml:789`). The tick interval is propagated into the state as `tick_seconds` (`position_watchdog.py:1646`, `tick_s = float(self.settings.watchdog.check_interval_seconds)`), which the velocity/acceleration math uses. (Comments in the engine sometimes say "~10 s each" for ticks; the dataclass default `tick_seconds=5.0` at `time_decay_sl.py:66` is only a fallback for direct construction.)

**Entry trigger.** In `_monitor_position` the engine is reached only when **the position is losing AND a plan exists** (`position_watchdog.py:2674-2677`):

```python
if pnl_pct < 0 and plan is not None:
    closed = await self._handle_time_decay(pos, plan, pnl_pct, current_price)
    if closed:
        return
```

So the *loser lane* is gated strictly by `pnl_pct < 0`. As soon as a position crosses back to profit, the watchdog pops the Time-Decay state and **hands off** to the SENTINEL / trailing / profit-take lane (`position_watchdog.py:2689-2704`, `TIME_DECAY_HANDOFF`), snapshotting the MAE high-water mark first.

**Per-tick suppression conditions inside `calculate()`**, in order:

1. **Per-class grace period** (`time_decay_sl.py:457-466`): if `position_age_seconds < grace` the call returns `None` and emits `TIME_DECAY_GRACE`. Grace is looked up per volatility class from `grace_seconds_by_class`, falling back to the flat `grace_seconds`.
2. **Minimum-age guard** (`time_decay_sl.py:542-559`): `min_age_seconds = 300`. Below 300s, *both* the force-close and the tighter-SL push are suppressed; returns `None`, emits `TIME_DECAY_AGE_GUARD`.
3. **MAE-to-SL ratio gate** (`time_decay_sl.py:625-642`): if worst drawdown hasn't reached `mae_to_sl_ratio_threshold` of the original SL, returns `None`, emits `TIME_DECAY_MAE_GUARD`.

A `cooldown` check in the caller (`position_watchdog.py:1585-1593`, `is_symbol_in_any_cooldown`) skips the whole engine if the symbol was just closed, preventing a re-init/re-fire on an already-closing position.

The **first** loser tick on a symbol only *seeds* state and returns `False` (`position_watchdog.py:1757`); no action is taken until the second tick when `observe()` has a previous PnL to compute velocity from.

## 3. How it manages PROFIT

This is a **loss-side engine** — it is only ever invoked while `pnl_pct < 0`. It does not manage open profit; the moment PnL turns positive the watchdog removes its state and hands the position to the trailing/SENTINEL lane (`position_watchdog.py:2685-2704`). Therefore the engine has no profit-target, trailing-profit, or take-profit mechanism of its own.

The one place "profit-like" logic appears is the **recovery-responsive tightening (Issue 3)** at `time_decay_sl.py:891-920`. When an underwater trade bounces *toward* breakeven, this captures the recovered ground by tightening the stop close under the current price, rather than leaving the stop pinned at the wide budget set during the worst dip:

- Gate: `mae_recovery_tighten_enabled = true` (config.toml:2975) AND `state.mae_pct <= -0.10` (`time_decay_sl.py:903-906`).
- Recovery ratio `_recov_ratio = (current_pnl_pct - state.mae_pct) / abs(state.mae_pct)`; if it is `>= mae_tightening_recovery_threshold = 0.75` (config.toml:2976), it places the stop at `-current_pnl_pct + recovery_tightening_buffer_pct`, where `recovery_tightening_buffer_pct = 0.3` (config.toml:2977), so the stop sits 0.3% under the recovered price (`time_decay_sl.py:914-920`). Emits `TIME_DECAY_RECOVERY_TIGHTEN`.

This is still a *loss-reduction* mechanism (it locks in a smaller loss / near-breakeven), not a profit mechanism. Everything else the engine does is loss management.

## 4. How it manages LOSS — every mechanism, guard, threshold, and config value

### 4.1 The allowed-loss budget (the five multiplicative models)

**Model 1 — Convex time decay** (`time_decay_sl.py:844-846`):
`time_frac = min(age / max_hold, 1.0)`; `time_factor = 1 - time_frac**time_decay_exponent`. As the position ages toward `max_hold_seconds`, `time_factor` shrinks toward 0, squeezing the budget. `time_decay_exponent = 1.5` (dataclass default; not overridden in config.toml, so the live value is the `TimeDecaySettings` default at settings.py:3608).

**Model 2 — ATR-scaled base room** (`time_decay_sl.py:848-857`): `atr_room = atr_5m_pct * mult`, where `mult` is per-class from `atr_room_multiplier_by_class`: dead=1.0, low=1.2, medium=2.0, high=2.5, extreme=3.0 (config.toml:3103-3107). The flat fallback `atr_room_multiplier = 2.0` (settings.py:3613) applies when class is unknown.

**Model 3 — MAE-recovery multiplier** (`_recovery_multiplier`, `time_decay_sl.py:1047-1066`): neutral (1.0) when `mae_pct > -0.10`. Otherwise `recovery = (current - mae)/|mae|`; `> mae_recovery_threshold (0.5)` returns `mae_bonus (1.2)`; `< mae_stagnation_threshold (0.2)` returns `mae_penalty (0.8)`. All four are dataclass defaults (settings.py:3619-3622).

**Model 4 — Velocity/acceleration 4-case switch** (`_momentum_multiplier`, `time_decay_sl.py:1068-1078`): `momentum_favorable=1.3` (vel>0,accel>0), `momentum_slow_rise=1.1` (vel>0,accel<0), `momentum_slow_fall=0.9` (vel<0,accel>0), `momentum_danger=0.7` (vel<0,accel<0); exactly-zero → neutral 1.0. Defaults at settings.py:3639-3642. Velocity/acceleration come from `observe()` (`time_decay_sl.py:1212-1213`): `velocity = (pnl - last_pnl)/tick_seconds`, `acceleration = velocity - prev_velocity`.

**Model 5 — Bayesian probability multiplier** (`time_decay_sl.py:868-873`): discrete bands on `p_win` — `< p_win_tight (0.40)` → `p_win_tight_mult (0.7)`; `> p_win_loose (0.60)` → `p_win_loose_mult (1.2)`; else 1.0 (defaults settings.py:3669-3672).

**Combine + floor + cap** (`time_decay_sl.py:875-889`): multiply all five; floor at `min_allowed_loss_pct = 0.15` (settings.py:3707); cap at `original_sl_pct` (never widen).

**Tighter-only ratchet** (`time_decay_sl.py:922-932`): if `allowed_loss >= state.last_allowed_loss` the engine returns `None` (`no_tighten`). The budget can only monotonically tighten over the life of the trade. The new SL is then derived direction-aware (`time_decay_sl.py:934-938`): Buy = `entry*(1 - allowed/100)`, Sell = `entry*(1 + allowed/100)`.

**Price-relative floor** (`time_decay_sl.py:940-968`): if `min_price_relative_distance_pct > 0`, an SL whose distance from the derived current price is below the SL Gateway's min-distance is skipped (returns `None`, `TIME_DECAY_FLOOR_PRICE_REL`), so the gateway doesn't reject every TD push.

### 4.2 The Bayesian p_win update (`_update_p_win`, time_decay_sl.py:1080-1164)

Prior at creation: `p_win = p_win_prior_base + regime_confidence * p_win_prior_regime_weight`, i.e. `0.55 + conf*0.25`, clamped to `[0.05, 0.95]` (`time_decay_sl.py:391-394`; note the module docstring at line 19 still says base 0.40, but the live default is 0.55 per settings.py:3650). Per-tick multiplicative updates:

- **Deepened this tick** (`current_pnl < prev_pnl`, `time_decay_sl.py:1102-1109`): if drawdown `> 2 ATR` → `*= p_win_atr2_penalty (0.70)`; elif `> 1 ATR` → `*= p_win_atr1_penalty (0.85)`.
- **Absolute-depth penalty** (`time_decay_sl.py:1118-1122`), for slow bleeders that never trip the ATR-relative test: `|pnl| > p_win_abs_depth_strong_pct (3.0)` → `*= 0.70`; elif `|pnl| > p_win_abs_depth_threshold_pct (1.5)` → `*= 0.90` (config.toml:3067-3070). Compounds with the ATR penalties.
- **Recovery bonus** (`time_decay_sl.py:1124-1128`): if MAE meaningful and recovered >50% → `*= p_win_recovery_bonus (1.15)`.
- **Regime** (`time_decay_sl.py:1136-1152`): supports → reset streak and `*= p_win_regime_bonus (1.05)`; not-supporting → `*= p_win_regime_penalty (0.60)`. With `smooth_p_win_enabled` + edge-trigger the penalty applies only after `p_win_regime_penalty_sustained_ticks (3)` consecutive mismatches; smoothing is **off** in production (config.toml:3081), so the penalty is unconditional per tick.
- Clamp to `[p_win_min 0.05, p_win_max 0.95]` (`time_decay_sl.py:1155-1158`); append to a bounded 32-entry `recent_pnl` history (`time_decay_sl.py:1162-1164`).

### 4.3 MAE monotonic high-water hold (`_assign_mae_monotonic`, time_decay_sl.py:994-1045)

MAE is the worst (most-negative) PnL excursion and is **strictly monotonic** — it may only deepen. This is the *sole* assignment site for `state.mae_pct`. A candidate more negative than the prior deepens it and returns `True`; a candidate *less* negative is rejected, the prior is HELD, and `TIME_DECAY_MAE_MONOTONIC_HOLD` is logged (`time_decay_sl.py:1022-1045`). MAE is measured **above** the age guard (`time_decay_sl.py:489-506`) so worst-PnL during the 0–300s immunity window is not lost (the T1-1 fix). It can be seeded across state recreation via `prior_mae_pct` (`time_decay_sl.py:415-418`), inherited from `_td_mae_high_water` (`position_watchdog.py:1701`).

### 4.4 The force-close gate stack (loss-cutting guards), in execution order

1. **Per-class grace** (`time_decay_sl.py:457-466`): dead=30, low=45, medium=120, high=180, extreme=240s (config.toml:3092-3096); flat fallback `grace_seconds = 120` (settings.py:3703).

2. **Min-age guard** (`time_decay_sl.py:542-559`): `min_age_seconds = 300` (config.toml:2989). Below 300s, returns `None` — suppresses force-close AND tighten. This deliberately mirrors `watchdog.strategic_action_min_hold_seconds = 300` so the calculator's direct close path is held to the same min-hold policy.

3. **Standalone monotonic-grind force-close** (`time_decay_sl.py:561-614`) — placed *after* min-age and *before* the MAE-to-SL gate, and **p_win-independent**. Fires when ALL hold: `monotonic_grind_cut_enabled` (= **true** in config.toml:3037, the one carve-out that is LIVE), `mae_pct < 0`, `near_trough_streak >= monotonic_grind_sustained_ticks (24)`, and `|pnl| >= monotonic_grind_min_loss_pct (0.30)`. Then a recovery-ratio veto: `_grind_recov_off_trough = (pnl - mae)/|mae|`; only if `<= monotonic_grind_max_recovery_ratio (0.20)` does it return `-1.0` with `force_close_reason = "monotonic_grind_cut"` (`time_decay_sl.py:587-602`, `TIME_DECAY_MONOTONIC_GRIND_CUT`). If the recovery ratio exceeds the cap, the trade has bounced off the trough and is **spared** (`TIME_DECAY_MONOTONIC_GRIND_SPARED`, `time_decay_sl.py:607-614`). The `near_trough_streak` is tracked every tick at `time_decay_sl.py:521-527`: a tick is "pinned" when `(current_pnl - mae) <= monotonic_grind_near_trough_band_pct (0.05)`; any bounce out of the band resets it to 0. The discriminator is the *sustained stall*, not a new-low fraction.

4. **MAE-to-SL ratio gate** (`time_decay_sl.py:625-642`): `mae_ratio = |mae| / original_sl_pct`; if `< mae_to_sl_ratio_threshold (0.5)` (config.toml:3001) returns `None`. A position must have drawn down at least half its original SL before TD can force-close.

5. **Structural-invalidation gate** (`time_decay_sl.py:651-738`): `structural_invalidation_required = true` (config.toml:3015). When `p_win < p_win_force_close` AND the caller's `structural_invalidation` flag is `False`, force-close is **blocked** (`TIME_DECAY_STRUCT_GUARD`, returns `None`) — *unless* one of two carve-outs yields:
   - **Near-certain-loser carve-out (H1)** (`time_decay_sl.py:695-706`): if `p_win <= near_certain_loser_p_win (0.10)` the guard YIELDS and falls through to force-close (`TIME_DECAY_STRUCT_GUARD_YIELD`). The effective threshold `_eff_ncl` is age-aware when `winprob_age_aware_band_enabled` (= **false**, config.toml:3056) — it would rise from `_young (0.10)` to `_old (0.13)` past `age_threshold_to_raise_p_win_seconds (600)`; with it off, the flat 0.10 is used (`time_decay_sl.py:669-676`).
   - **Slow-bleed cumulative carve-out (Issue 2.6)** (`time_decay_sl.py:707-726`): if `slow_bleed_cumulative_force_close_enabled` (= **false**, config.toml:3022) AND `pnl <= -slow_bleed_cumulative_loss_pct (2.5)` the guard yields (`TIME_DECAY_SLOW_BLEED_CUT`). Currently inert.

6. **Force-close sentinel** (`time_decay_sl.py:740-842`): reached when `p_win < p_win_force_close (0.15)` (settings.py:3652) and the structural gate let it through. Before firing, the **recovery guard** (`time_decay_sl.py:750-772`) — gated on `smooth_p_win_enabled` (= **false**) — would HOLD the cut if the trade is within `p_win_recovery_guard_be_band_pct (0.5)` of breakeven and making a new local high over `p_win_recovery_guard_n_ticks (3)`; inert in production. The sentinel emits `TIME_DECAY_FORCE_CLOSE_TRACE`, `TIME_DECAY_STRUCT_INVALIDATED`, `TIME_DECAY_FORCE_CLOSE`, stamps `force_close_reason` as `"win_prob_near_certain"` (if `p_win <= _eff_ncl`) or `"win_prob_force_close"` (`time_decay_sl.py:837-841`), and returns **-1.0**.

## 5. Inputs

The engine reads everything from the `TimeDecayState` it is handed plus the keyword args to `calculate()`. The watchdog (`_handle_time_decay`) gathers them:

- **PnL** (`pnl_pct`) and **age** (`position_age_seconds = plan.age_minutes * 60`, `position_watchdog.py:1778`).
- **ATR and volatility class**: `self.volatility_profiler.get_profile(symbol)` → `atr_pct_5m`, `volatility_class` (`position_watchdog.py:1604-1614`), with a `0.5` ATR / `medium` fallback.
- **Regime confidence + alignment**: `self.regime_detector.get_coin_regime(symbol)` → `confidence` for the prior (`position_watchdog.py:1620-1622`) and `regime_still_supports` (up/Buy or down/Sell match, `position_watchdog.py:1762-1775`).
- **Original SL %**: derived from `plan.stop_loss_price` vs `plan.entry_price`, fallback 3% (`position_watchdog.py:1626-1643`).
- **Max hold**: `plan.max_hold_minutes * 60` (`position_watchdog.py:1645`).
- **Entry-time structural anchors** (XRAY confidence, setup type, regime-at-open, regime confidence): from `coordinator._trades[symbol]` (preferred) or a `trade_thesis` DB SELECT fallback (`position_watchdog.py:1657-1695`).
- **`structural_invalidation` + reason**: computed by `Layer4ProtectionService.compute_structural_invalidation` (or the inline `_compute_structural_invalidation` fallback) before the call (`position_watchdog.py:1791-1800`).
- **velocity/acceleration**: produced by `td_observe(state, pnl_pct)` immediately before `calculate()` (`position_watchdog.py:1760`).
- **Prior MAE**: from `self._td_mae_high_water` (`position_watchdog.py:1701`).

## 6. Outputs / writes

The engine itself **writes nothing to the world** — it returns one of three values (`time_decay_sl.py:433-440`):

- **`float > 0`** — a tighter SL price. The watchdog routes it through `_push_sl_to_shadow(..., source="time_decay")` (`position_watchdog.py:1932-1939`), which is the *single point of truth* for SL propagation: it delegates to the SL Gateway for tighter-only / min-distance / max-step / rate-limit validation and the wire push, then mirrors the new SL onto the local plan (`position_watchdog.py:1084-1113`). So the engine **advises**; the gateway/Shadow actually writes the stop.
- **`-1.0`** — the force-close sentinel. The watchdog calls `self.position_service.close_position(symbol, close_trigger=_fc_reason)` (`position_watchdog.py:1863-1864`), where `_fc_reason` is the engine-stamped `force_close_reason`. It also records the close reason on the coordinator, sends an alert, adds a HIGH event, resolves authoritative PnL, and calls `coordinator.on_trade_closed` (`position_watchdog.py:1858-1904`). The MAE is snapshotted into `_td_mae_high_water` before the state is popped (`position_watchdog.py:1916-1926`).
- **`None`** — no-op; the watchdog returns `False` and lets the position fall through to the loser-timeout fallback (`position_watchdog.py:1830-1831`, `2674-2677`).

The only mutations the engine performs are on the in-memory `TimeDecayState` (MAE, p_win, last_allowed_loss, last_sl_sent, near_trough_streak, force_close_reason, recent_pnl, velocity history).

## 7. Wiring — the dependency graph

- **Constructed** in `PositionWatchdog.__init__` (`position_watchdog.py:275-276`): `self._time_decay = TimeDecaySLCalculator(TimeDecayConfig(...))`, populated field-by-field from `td_settings` (the `[time_decay]` settings) only if `enabled` (`position_watchdog.py:274`). Keys absent from config.toml fall back to the `TimeDecaySettings` dataclass defaults.
- **Per-symbol state** is held in `self._td_states: dict[str, TimeDecayState]` (`position_watchdog.py:468`) and the MAE-preservation map `self._td_mae_high_water` (`position_watchdog.py:481`).
- **Called by** `PositionWatchdog._handle_time_decay` (`position_watchdog.py:1555`), which is called by `_monitor_position` (`position_watchdog.py:2675`) on each loser tick.
- **The engine calls** (internally): `observe`/`td_observe`, `_assign_mae_monotonic`, `_update_p_win`, `_recovery_multiplier`, `_momentum_multiplier`, `_maybe_log`. It calls **no external service** — by design.
- **It also appears** in `src/risk/layer4_protection.py:51,106` (`Layer4ProtectionService` accepts a `time_decay_calculator` reference) and is referenced in `src/workers/manager.py:1601`, but the live calculate-and-act path is the watchdog's `_handle_time_decay`.

## 8. How it fits the end-to-end pipeline

Within the watchdog's `_monitor_position` tick, the Time-Decay engine is the **loser-lane owner**. It coordinates with:

- **Upstream feeders**: VolatilityProfiler (ATR/class), RegimeDetector (regime), TradeCoordinator + `trade_thesis` (entry anchors), Layer4ProtectionService (structural-invalidation verdict). The watchdog records the STRUCT_GUARD verdict back to Layer 4 so the ProfitSniper's stall-escape can defer when structure holds (`position_watchdog.py:1812-1827`).
- **Downstream actors**: the SL Gateway / Shadow via `_push_sl_to_shadow` for tighten advice; `position_service.close_position` for the force-close; the TradeCoordinator for close-reason provenance and PnL booking.
- **Handoff partner**: the SENTINEL / trailing / profit-take lane. When the trade recovers to profit, Time-Decay state is popped and the position is handed to that lane (`position_watchdog.py:2685-2704`); when it returns to losing, state is re-created (inheriting the preserved MAE).
- **Fallback below it**: the **loser timeout** path. When Time-Decay no-ops (returns `None`/`False`), the watchdog falls through to the timeout block (the ultimate time-based exit), so a tightened-but-not-fired SL or a guard-blocked force-close still has a deadline backstop.

It is one of several institutional loss systems; the close-reason split (`win_prob_near_certain`, `win_prob_force_close`, `monotonic_grind_cut`) keeps its cuts separable from deadline force-closes in leak attribution (`time_decay_sl.py:91-102`, `position_watchdog.py:1836-1852`).

## Known weaknesses / failure modes

- **Most loss-cutting carve-outs are disabled in production.** Of the aggressive force-close levers, only `monotonic_grind_cut_enabled = true` (config.toml:3037) is live. `slow_bleed_cumulative_force_close_enabled` (3022), `winprob_age_aware_band_enabled` (3056), and `smooth_p_win_enabled` (3081) are all **false**. So the slow-bleed cut, the age-aware near-certain band, the regime-penalty smoothing, AND the recovery guard are all inert — meaning the unconditional per-tick `p_win_regime_penalty (0.60)` can still collapse `p_win` on a single regime flicker (the exact over-cut Problem 3.1 was meant to smooth), and there is no recovery guard to hold a near-breakeven recoverer.

- **The structural gate can hold a clear loser to its stop.** With `structural_invalidation_required = true` and the slow-bleed carve-out off, a trade with `p_win` in the `(0.10, 0.15)` band and stable structure is *blocked* from force-closing and rides to its original stop. Only the LIVE monotonic-grind cut (which requires a 24-tick pinned-at-trough stall, ≈4 minutes, and `|pnl| >= 0.30%`) or `p_win <= 0.10` can rescue that case.

- **The min-age + MAE-to-SL gates create a 300s/half-SL blind window.** Below 300s age OR below 50% of the original SL drawdown, *no* tightening and *no* force-close can occur (except the grind cut, which itself needs age ≥ min_age and a 24-tick streak). A fast, deep adverse move inside the first five minutes is left entirely to the hard-stop/timeout backstop.

- **MAE-monotonic hold can over-widen the budget through a recovery.** Because MAE only deepens, the `recovery_multiplier (1.2)` keeps widening the budget as a trade recovers; without the recovery-tighten branch (which needs a strong ≥0.75 recovery ratio) the tighter-only ratchet pins the stop at the wide level set during the worst dip — a moderate recovery keeps a loose stop.

- **Structural-anchor dependence is fragile.** If `entry_xray_confidence <= 0` (no anchor — coordinator state lost on restart AND `trade_thesis` row missing), the structural gate fail-safe **blocks** force-close (`no_data` path), so a genuinely dead trade with a missing anchor and `p_win` above 0.10 will not be cut by this engine and depends entirely on the timeout/hard-stop.

- **Velocity/acceleration are noise-sensitive.** `observe()` computes them from a single tick-over-tick PnL delta (`time_decay_sl.py:1212-1213`) with no smoothing, so the Model-4 momentum multiplier (0.7–1.3) can swing on per-tick price noise, and `acceleration` is a raw first difference of velocity.

Source files: `/root/trading-intelligence-mcp/src/risk/time_decay_sl.py`, `/root/trading-intelligence-mcp/src/workers/position_watchdog.py`, `/root/trading-intelligence-mcp/src/config/settings.py`, `/root/trading-intelligence-mcp/config.toml`.

---

# Part 6 — SL Gateway and the Trade-State Owner Switch

## 1. What it is and its single responsibility

The SL Gateway is the single chokepoint through which every stop-loss write in the system must pass. Its module docstring states the problem directly: roughly six-to-ten independent systems (Claude entry SL, APEX override, SENTINEL Advisor, Profit Sniper trail, Time-Decay tightening, watchdog trailing, the Loss-Cutting engine, the Profit-Fetching ladder/chandelier) all modify the stop-loss of an open position, and without coordination "last-write wins and multiple systems can collide on the same symbol within seconds" (`sl_gateway.py:5-11`). The class `SLGateway` exposes one public mutation method, `apply(...)` (`sl_gateway.py:444`), and it is "the single place that calls `position_service.set_stop_loss`" (`sl_gateway.py:59-60`). Every caller invokes `await gateway.apply(...)` in place of calling `set_stop_loss` directly (`sl_gateway.py:182-186`).

Its single responsibility is arbitration and validation of stop writes: it decides whether a proposed new stop is admissible (by trade-state ownership and four mechanical rules), clamps it to the closest valid value where possible, and is the only component that physically pushes the resulting stop to the exchange. It is explicitly domain-agnostic: callers handle their own post-accept side effects (plan mirror, `SL_PROPAGATED` log, coordinator notifications) — the gateway "stays domain-agnostic" (`sl_gateway.py:61-62`).

State is per-symbol and in-memory; because the event loop is single-threaded, no locks are used. The "no await between check and state-update" discipline is enforced by fetching `current_sl` and `current_price` upfront (the only awaits) and only updating `_last_change` / `_last_sl` after the wire push returns True (`sl_gateway.py:63-68`, enforced at `sl_gateway.py:976-977`).

## 2. When it activates — cadence and triggers

The gateway has no clock of its own. It is purely reactive: it activates each time one of the ~20 writer sources calls `apply(...)`. The cadence is therefore set by the callers — the Profit Sniper spine and the Position Watchdog tick the position and call `apply` when they want to move the stop. The only time-based behavior internal to the gateway is its R4 rate-limit window (`rate_limit_seconds`, currently 30s; `sl_gateway.py:938-958`) and its statistics emitter, which emits an `SL_GATEWAY_STATS` summary every 300 seconds OR every 100 events, whichever comes first (`STATS_INTERVAL_SECONDS = 300`, `STATS_EVENT_THRESHOLD = 100`; `sl_gateway.py:197-198`, `sl_gateway.py:1072-1092`).

To avoid being called when it will only reject, the gateway publishes `next_eligible_in_seconds(symbol)` (`sl_gateway.py:259-295`). Callers consult it first: the Profit Sniper checks it at `profit_sniper.py:2933` and `profit_sniper.py:3510`, and the watchdog at `position_watchdog.py:1167`, skipping their own call (and logging `SNIPER_RATE_LIMIT_AWARE_SKIP`) rather than reaching the gateway and being rejected with `REASON_RATE_LIMIT`.

The order of evaluation inside a single `apply` call is: input sanity (`sl_gateway.py:518-529`), resolve `current_sl` (`sl_gateway.py:535-551`), pass-through short-circuit if `enabled=false` (`sl_gateway.py:556-583`), resolve `current_price` (`sl_gateway.py:586-600`), the OWNER GATE (`sl_gateway.py:616-671`), then rules R1 → R2 → R3 → final tighten-only re-check → wrong-side guard → R4 → wire push (`sl_gateway.py:673-992`).

## 3. How it manages PROFIT

The gateway does not itself decide when to lock profit — that is the green-owner engine's job (the Profit Sniper). Its profit-related role is to permit and protect profit-locking moves that the mechanical rules would otherwise mangle, and to keep the profit engine in sole control of a winning trade. The mechanisms:

**Tighten-only ratchet (R1).** Every accepted write must move the stop towards current price (Buy: higher; Sell: lower) (`sl_gateway.py:673-692`). This is what makes profit-locking monotonic — a stop can be raised under a long but never lowered. R1 is never bypassable, even on breakeven moves (`sl_gateway.py:489-491`).

**R3 max-step bypass allowlist for profit-locking moves.** The base R3 cap is `max_step_pct = 0.25%` per write. Legitimate profit-locking moves frequently need a larger single jump (a ladder rung is ~0.5%; a fast-runner chandelier raise can be ~1.4%). The `_BREAKEVEN_BYPASS_SOURCES` frozenset (`sl_gateway.py:210-255`) is a code-level allowlist of sources permitted to bypass R3 (only R3 — R1, R2, R4 still apply) when they pass `bypass_step_cap_for_breakeven=True`. The profit members are `profit_sniper_lock`, `profit_sniper_breakeven`, `profit_sniper_ladder`, and `profit_sniper_trail`. The `profit_sniper_trail` entry (`sl_gateway.py:238-251`) documents the live AAVE finding where the peak-anchored Chandelier raw 64.197 was being clamped to 63.298, lagging the peak ~1.4%/write; bypassing R3 lets the floor reach `high_water - leash` at full speed. The bypass is honored only when the source is in the allowlist (`sl_gateway.py:828-832`), and every large-step bypass emits `SL_GATEWAY_BREAKEVEN_OVERRIDE` (`sl_gateway.py:845-851`).

**R2 breakeven floor (`r2_breakeven_floor_enabled = true`).** When a trusted breakeven source supplies `breakeven_floor_price`, the R2 min-distance clamp may move the stop toward price only down to breakeven, never past it — `max(be, boundary)` for a long, `min(be, boundary)` for a short (`sl_gateway.py:743-783`). This prevents a high-volatility coin's large `eff_min` from rewriting an armed ladder floor to a sub-breakeven price (documented as confirmed live 68× across 30 symbols, `sl_gateway.py:746-748`). Worst case becomes a zero-loss exit. A guard (Issue 2.1) refuses to hold the floor on the wrong side of price (`sl_gateway.py:765-780`).

**R2/R3 clamp-and-apply instead of reject.** The single most important profit-protection change: a stop that violates R2 or R3 is no longer rejected wholesale — it is clamped to the closest valid value and applied (`sl_gateway.py:722-815` for R2, `sl_gateway.py:852-888` for R3). The comments record that pre-fix the wholesale reject "froze the ladder/chandelier spine at a ~1.7% accept rate and let winners round-trip to a loss" (`sl_gateway.py:725-732`). So a trail can always ratchet up incrementally even when it asked to move too far.

**Owner switch profit-priority (Option A).** With `head_only_seizes_green = true`, a running GREEN trade may be written only by the Head (catastrophic cap) or its own green owner; the loss engine and all advisory writers are deferred (`sl_gateway.py:1320-1347`). "Nothing but catastrophe interrupts a running winner" (`sl_gateway.py:1339-1343`). This is what "lets a winner run."

Profit config keys and current values (from `config.toml [sl_gateway]` and `settings.py:1450-1540`): `min_distance_pct = 0.3`, `max_step_pct = 0.25`, `min_distance_atr_multiplier = 0.5`, `min_distance_abs_floor_pct = 0.05`, `r2_breakeven_floor_enabled = true`, `head_only_seizes_green = true`, `green_sources = ["profit_sniper_ladder", "profit_sniper_trail", "profit_sniper_lock", "profit_sniper_breakeven", "micro_floor"]`.

## 4. How it manages LOSS

**R2 min-distance — the anti-strangulation guard.** A new stop must be at least the effective minimum distance from current price (`sl_gateway.py:694-815`). This guards against placing a stop on bid-ask noise — the original motivating incident was Profit Sniper's trail "jumping SL 2.5% in one step to 0.08% from current price, strangling a position on 29s of normal market noise" (`sl_gateway.py:8-11`). The effective minimum is ATR-scaled when the volatility profiler is wired: `eff_min = max(min_distance_abs_floor_pct, atr_5m_pct * min_distance_atr_multiplier)`, clamped by a per-class ceiling, falling back to the static `min_distance_pct` when ATR is cold (`sl_gateway.py:706-720`; formula in `vol_scale.py:50-97`). Dead coins (ATR ~0.04%) land near the 0.05% floor instead of 0.30% base, unblocking trails that would otherwise be rejected 160/160 (`sl_gateway.py:702-705`). The per-class ceilings are `{dead: 0.30, low: 0.50, medium: 1.00, high: 2.00, extreme: 3.50}` (`settings.py:1481-1484`).

**R3 max-step — the anti-jump guard.** A new stop must not move more than `max_step_pct = 0.25%` from the previously accepted SL per modification (`sl_gateway.py:817-888`). This guards against aggressive "jumps" (named incidents RIVERUSDT and the 4.519% BSBUSDT activation step, `sl_gateway.py:22`, `config.toml`). Lowered from 0.5 → 0.25 on 2026-05-05 so each tighten only moves a quarter of the remaining distance, cutting peak give-back.

**R1 tighten-only as a loss guard.** R1 also prevents accidental loosening — no system can move a stop further from price (which would expose more capital). Never bypassable (`sl_gateway.py:673-692`).

**R4 rate-limit — the anti-thrash guard.** At least `rate_limit_seconds = 30` must elapse between accepted modifications on the same symbol (`sl_gateway.py:938-958`). Guards against multiple writers thrashing the same stop. Bypassable via `bypass_rate_limit=True` for urgent loss-cutting / breakeven force-exits (e.g. `loss_atr_initial` passes it at sniper line 1296+).

**Clamp-noop and wrong-side terminal guards.** After R2/R3 clamps, a final tighten-only re-check holds the existing stop as a no-op (no wire) if the best valid stop does not improve on it (`REASON_CLAMP_NOOP`, `sl_gateway.py:898-910`) — this stops the post-retrace re-spam loop that produced the NEAR `SL_GATEWAY_WIRE_FAIL` cascade. A terminal wrong-side guard (Issue 2.1) refuses to wire any stop still on the wrong side of price (`REASON_WRONG_SIDE`, `sl_gateway.py:912-932`), naming the ~150×/16min BLUR retry-spam cascade it prevents.

**Owner switch red-owner / Head bucketing.** The loss engine writes only when the trade is red. The Head (catastrophic cap, `head_sources = ["loss_cap", "loss_cap_emergency"]`) is always admitted and only tightens (`sl_gateway.py:1320-1331`). The bypass allowlist's loss members (`loss_cap`, `loss_cap_emergency`, `loss_atr_initial`, `loss_structure`, `loss_recovery`, `safety_sweeper`) let protective tightens be placed at their true distance in one move (`sl_gateway.py:233-237`).

Loss config keys and current values: `min_distance_pct = 0.3`, `min_distance_atr_multiplier = 0.5`, `min_distance_abs_floor_pct = 0.05`, `max_step_pct = 0.25`, `rate_limit_seconds = 30`, `head_sources = ["loss_cap", "loss_cap_emergency"]`, `red_sources = ["time_decay", "loss_structure", "loss_recovery"]`, `faded_winner_rearm_red = false`.

Note (`sl_gateway.py:252-254`): the volatility-spike catastrophe stop force-CLOSES the position (`closed_by=loss_spike_force`) — it never writes an SL, so it does not pass through the gateway at all.

## 5. The trade-state OWNER SWITCH (2026-06-14)

Above the four mechanical rules sits an OWNER GATE that resolves multi-writer collisions into one authority hierarchy (`sl_gateway.py:27-43`, `_owner_gate` at `sl_gateway.py:1259-1374`). It runs BEFORE R1-R4, fails OPEN on any error, and is currently ENFORCING (`owner_switch_enabled = true`, `owner_switch_enforce = true` in config.toml).

**State computation + breakeven deadband.** `_compute_trade_state` (`sl_gateway.py:1234-1257`) computes PnL% from entry vs price and returns `green` when `pnl_pct >= +breakeven_deadband_pct`, `red` when `pnl_pct <= -breakeven_deadband_pct`, and `neutral` inside the band. `breakeven_deadband_pct = 0.05`. When entry or price is missing it returns None and the gate fails open. Entry comes from the `entry_price` kwarg, falling back to `breakeven_floor_price` (`sl_gateway.py:622-626`).

**Owner resolution + hysteresis.** In `_owner_gate` (`sl_gateway.py:1290-1316`): green state → owner green and the monotonic `_ever_green[symbol]` latch is set; red state → owner red UNLESS `faded_winner_rearm_red=false` and the trade was ever green, in which case it stays green-owned (the faded-winner / graduation-latch rule); neutral → hold the last definite owner (`_last_owner`), with a brand-new position defaulting to red so the opening floor is the baseline. A definite owner transition logs `SL_GATEWAY_OWNER_HANDOFF` (`sl_gateway.py:1309-1314`).

**The four-plus-one buckets** (`_classify_bucket`, `sl_gateway.py:1215-1232`; admit logic `sl_gateway.py:1320-1360`):
- HEAD (`loss_cap`, `loss_cap_emergency`): always admitted; if it seizes a green trade it logs `SL_GATEWAY_HEAD_OVERRIDE` (`sl_gateway.py:1326-1331`).
- GREEN owner: admitted only when `owner == "green"`.
- RED owner: admitted only when `owner == "red"`.
- ADVISORY (`brain_tighten`, `watchdog_tighten`, `wd_brain_scoring`, `sentinel_advisor`, `sentinel_deadline`, `sentinel_breakeven`, `watchdog_lock_peak`, `watchdog_breakeven`, `trail_activation`, `trail_update`): on a green trade with `head_only_seizes_green=true`, deferred; otherwise admitted unless `advisory_enforce` (currently false, so advisory writers currently pass when not on a green trade) (`sl_gateway.py:1338-1347`).
- ALWAYS (`loss_atr_initial`, `safety_sweeper`): always admitted (the opening stop + naked-position sweeper) (`sl_gateway.py:1332-1333`).
- unclassified: fails open, logs `SL_GATEWAY_OWNER_UNCLASSIFIED`, and surfaces a MED EventBuffer signal (`sl_gateway.py:1348-1360`).

**Log-only vs enforce.** When `_og_admit` is False (`sl_gateway.py:628-671`): under `owner_switch_enforce=true` the write is deferred and rejected (`REASON_WRONG_OWNER` for engines, `REASON_ADVISORY_DEFER` for advisory writers, logging `SL_GATEWAY_WRONG_OWNER` / `SL_GATEWAY_ADVISORY_DEFERRED`, and routing the advisory's proposed stop to the owner via a MED EventBuffer signal); under log-only it emits the `_WOULD` variant and lets the write continue.

**`peek_owner`.** The sniper spine reads `peek_owner(symbol, is_long, entry, price)` (`sl_gateway.py:1376-1425`) so its candidate selection agrees with the gate and never starves a trade. It mirrors `_owner_gate`'s logic but mutates only the monotonic `_ever_green` latch — it never logs and never touches `_last_owner`, so it cannot double-log a hand-off or diverge from the gate. The sniper gates this on `state_enforcement_active` (`sl_gateway.py:1197-1211`; consumed at `profit_sniper.py:2716`, `2858-2861`), so log-only mode never changes stop selection.

**Boot inconsistency guard.** If `faded_winner_rearm_red=true` but `loss_cutting.graduation_crater_rearm_enabled=false`, the constructor logs `SL_GATEWAY_OWNER_SWITCH_INCONSISTENT` (`sl_gateway.py:421-433`) — a faded winner would hand to a red owner with no tools. Inert in the shipped default (both off).

The gate fails open on any exception (`sl_gateway.py:1362-1374`), logging `SL_GATEWAY_OWNER_ERROR` and surfacing a HIGH EventBuffer signal so a persistently-throwing gate cannot silently turn enforcement into a no-op.

## 6. Inputs

Per call (`apply` signature, `sl_gateway.py:444-460`): `symbol`, `new_sl`, `source`, `direction`, optional `plan`, `current_sl`, `current_price`, `reason`, the three bypass flags, `breakeven_floor_price`, `entry_price`.

Data it reads from services:
- `current_sl` resolution order: caller arg → `self._last_sl` cache → live `position_service.get_position(symbol).stop_loss` (`sl_gateway.py:535-551`).
- `current_price`: caller arg → `market_service.get_ticker(symbol).last_price` (`sl_gateway.py:586-595`).
- ATR/volatility class: `volatility_profiler.get_profile(symbol)` → `.atr_pct_5m`, `.volatility_class`, fed to `vol_scale.min_distance_for_class` (`sl_gateway.py:709-716`). The profiler has a 60s TTL so this await is amortized near-free (`sl_gateway.py:309-313`).
- Config: `self._settings.sl_gateway` (and `self._settings.loss_cutting` for the boot inconsistency check) (`sl_gateway.py:339`, `421`).

## 7. Outputs / writes

The gateway is the only component that writes the stop to the broker, via `await self._position_service.set_stop_loss(symbol, new_sl)` inside `_wire_push` (`sl_gateway.py:996-1019`). `set_stop_loss` (`position_service.py:414-432`) issues a Bybit `set_trading_stop` call (`category="linear"`, `stopLoss=str(...)`, `positionIdx=0`). In production the wired service is the Shadow / bybit-demo adapter (`shadow_adapter.py:415`, `bybit_demo_adapter.py:1236`).

It writes the stop — it does not advise. Advisory writers are deferred and their proposals are surfaced (not mechanically applied) to the owning engine via the EventBuffer (`sl_gateway.py:646-660`). It does NOT force-close positions; force-closes (`loss_spike_force`, `loss_cap_force`) bypass the gateway entirely (`sl_gateway.py:252-254`, config note).

After a successful wire push it updates `_last_change[symbol]` and `_last_sl[symbol]` (`sl_gateway.py:976-977`) and returns an `SLGatewayResult` (`sl_gateway.py:154-172`) carrying `accepted`, `reason`, `old_sl`, and `new_sl_applied` (the post-clamp value the caller mirrors into the plan). On wire failure it does NOT advance state and surfaces a HIGH `sl_gateway_wire_fail` EventBuffer event (`sl_gateway.py:962-974`). Brain-sourced rejects (`brain_tighten`, `watchdog_tighten`) surface a MED `sl_gateway_brain_blocked` event (`sl_gateway.py:1025`, `1038-1042`).

## 8. Wiring — dependency graph

**Construction.** Built once in `WorkerManager` (`manager.py:756-768`) as `SLGateway(settings, position_service=pos_svc, market_service=market_svc, event_buffer=services.get("event_buffer"), volatility_profiler=services.get("volatility_profiler"))`, stored as `self._services["sl_gateway"]`. Because EventBuffer is constructed in a later DI layer, the gateway also supports late-wiring via `set_event_buffer` (`sl_gateway.py:1427-1440`), called at `manager.py:864`. On every position close, `trade_coordinator.register_close_callback` invokes `sl_gateway.reset_symbol(sym)` (`manager.py:774-781`, `reset_symbol` at `sl_gateway.py:1188-1195`) so a new trade does not inherit the prior trade's rate-limit budget, step baseline, or owner memory.

**Callers (the writer graph).** The gateway is injected into the Profit Sniper, the Position Watchdog, and Time-Decay. The `source` strings and their `apply` call sites:
- Profit Sniper (`profit_sniper.py`): `loss_atr_initial` (line 1299), `profit_sniper_trail` (3534), `profit_sniper_lock` (5233), and a parameterized loss-cutting site passing `source` dynamically (3023-3036).
- Position Watchdog (`position_watchdog.py`): `time_decay` (1938), `wd_brain_scoring` (1423), `sentinel_deadline` (2278), `trail_activation` (2426), `trail_update` (2440), `watchdog_lock_peak` (2889), `watchdog_breakeven` (2918), `watchdog_tighten` (3376), `brain_tighten` (4190), `sentinel_advisor` (4306).
- These are the ~20 source identities the gateway arbitrates (the full set is enumerated in the five bucket lists at `settings.py:1522-1540`).

**What it calls:** `position_service.get_position` / `set_stop_loss`, `market_service.get_ticker`, `volatility_profiler.get_profile`, `vol_scale.min_distance_for_class`, and `event_buffer.add_event`.

## 9. How it fits the end-to-end pipeline

The gateway is the convergence funnel at the bottom of the trade-management pipeline. Upstream, three engines decide WHAT the stop should be: the Profit Sniper (green owner — ladders, chandelier trail, profit locks), the Position Watchdog (advisory trails, sentinel/brain tighten directives, time-decay), and the Loss-Cutting engine (Head cap, structure/recovery stops). Each computes a candidate and calls `apply`. The gateway then (a) consults the owner switch to decide whose write is currently authoritative given green/red state, (b) clamps/validates via R1-R4, and (c) performs the single physical write. It hands authority — not data — between engines: the owner switch ensures the green owner controls a winner and the red owner controls a loser, with the Head able to override either. The sniper spine pre-aligns with this via `peek_owner` and `state_enforcement_active` so selection and gating never disagree. Callers own all post-accept bookkeeping (plan `stop_loss_price` mirror, `SL_PROPAGATED` log — e.g. `profit_sniper.py:3546-3568`), keeping the gateway a thin, auditable wire contract.

## Known weaknesses / failure modes

- **Pervasive fail-open posture.** The owner gate fails open on missing entry/price and on any exception (`sl_gateway.py:1285-1287`, `1362-1374`), and unclassified sources are admitted (`sl_gateway.py:1348-1360`). A new writer added without being added to a bucket silently bypasses ownership arbitration (it still hits R1-R4), surfacing only as `SL_GATEWAY_OWNER_UNCLASSIFIED`. This is deliberate ("a gate bug can never block a protective write") but means ownership enforcement is best-effort, not guaranteed.

- **Allowlist drift is a code-change risk.** `_BREAKEVEN_BYPASS_SOURCES` (R3 bypass) and the five config bucket lists must stay coordinated with the actual source strings emitted by callers. A typo in a `source=` kwarg (e.g. `"profit_sniper_trail"`) silently demotes that writer to unclassified/non-bypassed — the gateway has no validation that emitted sources are members of any bucket.

- **`current_sl`/`current_price` staleness.** When the caller omits these, the gateway falls back to a cache then a live fetch (`sl_gateway.py:535-595`). A stale `_last_sl` (e.g. if the broker stop was changed out-of-band) can make R1/R3 evaluate against a wrong baseline. There is no reconciliation against the live broker stop on every call — only a last-resort `get_position` when no prior SL is known.

- **No-op holds can mask a needed move.** The clamp-noop path (`sl_gateway.py:898-910`) intentionally performs no wire when the best valid stop does not improve on the current SL. After a fast retrace this is correct (avoids the BLUR re-spam), but it also means a position whose price has moved past every min-distance-respecting stop simply keeps its existing stop — the gateway will never tighten further until price recovers, relying on the always-on force-close twins (which it does not control) for catastrophe protection.

- **Coupled config flags.** `faded_winner_rearm_red` must be flipped in tandem with `loss_cutting.graduation_crater_rearm_enabled`, or a faded winner is handed to a red owner with no tools. The code only warns at boot (`SL_GATEWAY_OWNER_SWITCH_INCONSISTENT`); it does not auto-correct, so a misconfigured operator can leave a faded winner protected only by the resting stop and the spike force-close.

- **Rate-limit is global per symbol, not per-owner.** R4 (`sl_gateway.py:938-958`) throttles all writers on a symbol to one accepted write per 30s. A legitimate urgent move from one owner can be rate-blocked by a recent accepted write from another owner unless it passes `bypass_rate_limit`; the bypass is granted at the caller's discretion, not enforced by bucket.

`enabled = true`, `log_only_global = false`, all per-rule `log_only_*` flags false, `owner_switch_enabled = true`, `owner_switch_enforce = true`, `advisory_enforce = false` — so the gateway currently runs in full hard-enforcement of R1-R4 and the owner switch, with advisory demotion still in log-only/pass mode.

---

# Part 7 — Sentinel, Brain Review, and Trade Coordinator

This section documents three coupled subsystems that activate once a trade is open: the SENTINEL suite (Deadline Engine, Portfolio Advisor, Exit Firewall), the BRAIN's position-management path (Claude Call B review plus the watchdog STRAT_ACTION handoff), and the Trade Coordinator's close path. All three are read by, or write through, the Position Watchdog, which is the single executor that touches the live stop-loss and issues closes.

A structural fact that ties them together: none of these three systems writes the exchange directly. The Deadline Engine, the Portfolio Advisor, and the BRAIN all produce advice or queued instructions that the Position Watchdog drains on its tick and pushes through one funnel — `_push_sl_to_shadow` (which delegates to the SL Gateway) for stop changes, or `position_service.close_position` followed by `coordinator.on_trade_closed` for closes. The Trade Coordinator is the bookkeeping authority that gates immunity, records close reasons, fans out close callbacks, and arms re-entry cooldowns.

## System A: SENTINEL (Deadline Engine, Portfolio Advisor, Exit Firewall)

### A.1 What it is and its single responsibility

SENTINEL is a three-part exit-governance layer defined in `src/sentinel/` (`src/sentinel/__init__.py:1` describes it as "Strategic Exit Normalization, Timed Intelligence, Natural Exit Logic"). Its three parts have distinct single responsibilities:

- **Deadline Engine** (`src/sentinel/deadline.py`): when a trade plan's hold timer expires, decide a tiered action based on current PnL instead of a binary close. It is the "smart expiry" replacement for the old "close when `max_hold_minutes` expires" rule (`src/sentinel/deadline.py:1-8`).
- **Portfolio Advisor** (`src/sentinel/advisor.py`): a DeepSeek V3 model that periodically assesses whole-portfolio risk and recommends stop-loss tightening only. It explicitly "CANNOT close positions. You can ONLY recommend tightening stop-losses" (`src/sentinel/advisor.py:27`, reinforced in the class docstring `src/sentinel/advisor.py:82-94`).
- **Exit Firewall** (`src/sentinel/firewall.py`): a pure-function gate that blocks the BRAIN's untrusted strategic-review path from force-closing or take-profiting positions, while letting trusted Claude-decided sources through and rejecting phantom closes on symbols no longer active (`src/sentinel/firewall.py:1-23`).

Note there are two copies of the firewall: `src/sentinel/firewall.py` (the live one, imported by both `layer_manager` and the watchdog) and an older `src/workers/firewall.py`. They differ: only the sentinel copy has the trusted-source bypass, the `source` parameter, and the `active_symbols` phantom-close guard (confirmed by diffing the two files; `src/workers/firewall.py:20-29` lacks the `source`/`active_symbols` parameters). The wired-in path uses `src.sentinel.firewall` (`src/core/layer_manager.py:1263`).

### A.2 When it activates — cadence and triggers

- **Deadline Engine**: activates inside the Position Watchdog's per-position monitor, only when a registered trade plan has expired. The trigger is `plan.is_expired` at `src/workers/position_watchdog.py:2254`, which fires the `_sentinel_deadline.evaluate(...)` call at `src/workers/position_watchdog.py:2258`. The watchdog tick cadence is `watchdog.check_interval_seconds`, set to `10` seconds in `config.toml:789` (dataclass default `10.0` at `src/config/settings.py:1313`). So the engine is re-evaluated at most once per ~10 s per expired position. It only runs for positions that have passed the immunity/newborn gates (`src/workers/position_watchdog.py:944-997`), since `_monitor_position` is called after those gates.
- **Portfolio Advisor**: runs on its own background loop, not the watchdog tick. The loop is constructed in `src/workers/manager.py:3192-3229`. It sleeps 150 s on startup to offset from Claude's cycle (`src/workers/manager.py:3194`), then loops every `advisor_interval_seconds` = `300` (5 minutes) — config `config.toml`, dataclass `src/config/settings.py:3141`. Each iteration builds a text portfolio snapshot of all open positions and calls `sentinel_advisor.assess(portfolio_text)` (`src/workers/manager.py:3220`). The recommendations it produces are consumed separately by the watchdog tick via `_execute_sentinel_recommendations` (`src/workers/position_watchdog.py:927, 4208`).
- **Exit Firewall**: a synchronous gate invoked once per non-hold position action during a BRAIN strategic-action dispatch (`src/core/layer_manager.py:1262-1269`). It has no cadence of its own.

### A.3 How it manages PROFIT

**Deadline Engine — Tier 1 (PROFIT):** When the timer expires and `pnl_pct >= deadline_profit_pct` (default `0.5`%, config `config.toml`, dataclass `src/config/settings.py:3133`), it returns `DeadlineAction(tier=PROFIT, should_close=True)` to "lock the win" (`src/sentinel/deadline.py:139-144`). The watchdog acts on `should_close` by closing the position (`src/workers/position_watchdog.py:2306-2308`).

A profit-specific override exists in the watchdog: under Profit-Fetching Phase 5, a still-profitable expired trade is NOT hard-closed; it "rides the sniper's maximally-tightened trail" instead, gated on `self._pf.enabled and self._pf.ride_winner_past_deadline and tier == "profit"` (`src/workers/position_watchdog.py:2293-2305`). This is a hand-off to the profit-sniper, not a SENTINEL action.

**Deadline Engine — grace-period profit capture:** if a position is in an active breakeven grace window and turns profitable (`pnl_pct >= deadline_profit_pct`), the grace is popped and the trade is closed as a PROFIT tier (`src/sentinel/deadline.py:101-112`).

**Portfolio Advisor — profit protection:** its system prompt instructs it to recommend protecting at least 50% of unrealized profit and to only tighten when there is at least 0.50% unrealized profit, treating sub-0.50% as noise (`src/sentinel/advisor.py:51-58`). On the consumer side, the watchdog independently re-checks this: a recommendation is blocked unless the position's live PnL ≥ `advisor_min_profit_for_tighten_pct` (default `0.50`%, config `config.toml`, dataclass `src/config/settings.py:3149`), logging `SENTINEL_ADVISOR_BLOCK` and the message "Trade needs room to breathe" (`src/workers/position_watchdog.py:4264-4272`). This is the "TRADE LIBERATION" guard against stopping out winners on micro-gains.

### A.4 How it manages LOSS

**Deadline Engine tiers (loss side), `src/sentinel/deadline.py:146-187`:**

- **Tier 2 (BREAKEVEN)**: `pnl_pct >= deadline_breakeven_lower_pct` (default `-0.3`%, `src/config/settings.py:3134`). Sets `new_sl = entry_price` (SL to breakeven), does NOT close, and grants a grace period of `deadline_grace_minutes` (default `5.0` min, `src/config/settings.py:3136`). It records a `DeadlineGrace` in the in-memory `_graces` dict (`src/sentinel/deadline.py:147-164`). When grace expires it closes the trade (`src/sentinel/deadline.py:126-136`).
- **Tier 3 (SMALL_LOSS)**: `pnl_pct >= deadline_small_loss_pct` (default `-1.5`%, `src/config/settings.py:3135`). Tightens SL to a reduced distance: for longs `entry * (1 - deadline_small_loss_sl_pct/100)`, for shorts `entry * (1 + .../100)` where `deadline_small_loss_sl_pct` default is `0.5`% (`src/config/settings.py:3137`). Does NOT close — "let it recover" (`src/sentinel/deadline.py:166-180`).
- **Tier 4 (BIG_LOSS)**: any PnL below the small-loss floor. Returns `should_close=True` — "the thesis failed. Cut immediately" (`src/sentinel/deadline.py:182-187`).

**Portfolio Advisor (loss side):** its prompt allows tightening on losing positions only if loss exceeds -1% (`src/sentinel/advisor.py:52`). It never closes (`src/sentinel/advisor.py:27`).

**Exit Firewall (loss containment via gatekeeping):** `_BLOCKED_ACTIONS = {"close", "take_profit"}` (`src/sentinel/firewall.py:32`). Untrusted sources (default `"strategic_review"`) cannot close or take-profit (`src/sentinel/firewall.py:96-102`), so the watchdog/sniper/SL-TP remain the only exit owners for that path. The firewall's docstring cites the data justification: all 8 historical strategic-review closes were losses (`src/sentinel/firewall.py:6-7`). The phantom-close guard rejects close/take_profit on any symbol not in `active_symbols`, even for trusted sources, logging `PHANTOM_CLOSE_REJECTED` (`src/sentinel/firewall.py:75-87`).

### A.5 Inputs

- **Deadline Engine** reads: `symbol`, `pnl_pct` (computed by the watchdog from the trade plan as `pnl_from_plan`, `src/workers/position_watchdog.py:2247-2251`), `entry_price`, and `direction` — all passed in by the watchdog from the coordinator's trade plan (`src/workers/position_watchdog.py:2258-2263`). It keeps grace state in-memory (`_graces`, `src/sentinel/deadline.py:62`).
- **Portfolio Advisor** reads: a formatted portfolio context string built in the manager loop from `position_service.get_positions()` — per-position side, entry, mark, computed PnL%, current SL, and size (`src/workers/manager.py:3198-3219`). It calls DeepSeek via `DeepSeekClient.analyze` through OpenRouter (`src/workers/manager.py:3177-3182`, `src/sentinel/advisor.py:119-126`).
- **Exit Firewall** reads: `action`, `symbol`, `reason`, `source`, and `active_symbols` — all passed by `layer_manager` (`src/core/layer_manager.py:1264-1267`).

### A.6 Outputs / writes

- **Deadline Engine** returns a `DeadlineAction` dataclass; it writes nothing itself. The watchdog applies it: SL tightening via `_push_sl_to_shadow(..., source="sentinel_deadline")` (`src/workers/position_watchdog.py:2272-2279`); closes via `position_service.close_position(symbol, close_trigger="wd_dl_action")` followed by `coordinator.remove_trade_plan` and `coordinator.on_trade_closed(closed_by=f"sentinel_deadline_{tier}")` (`src/workers/position_watchdog.py:2308-2338`).
- **Portfolio Advisor** writes only its in-memory `_pending_recommendations` list (`src/sentinel/advisor.py:134`). These are drained by the watchdog via `advisor.drain_recommendations()` (`src/sentinel/advisor.py:200-204`, called at `src/workers/position_watchdog.py:4214`) and applied as SL tightens via `_push_sl_to_shadow(..., source="sentinel_advisor")` (`src/workers/position_watchdog.py:4300-4307`). Before pushing, the watchdog also clamps any oversized step to the gateway `max_step_pct` (default `0.5`) to avoid wholesale rejection (`SENTINEL_STEP_CLAMP`, `src/workers/position_watchdog.py:4282-4297`).
- **Exit Firewall** returns a `(allowed, explanation)` tuple only (`src/sentinel/firewall.py:40-104`). It advises; it does not write.

### A.7 Wiring (dependency graph)

All three are constructed in `WorkerManager` under the SENTINEL init block (`src/workers/manager.py:3160-3247`), gated by `sentinel.enabled` (`config.toml`/`src/config/settings.py:3127`):

- `DeadlineEngine(sentinel_cfg)` is built and injected onto the watchdog as `watchdog._sentinel_deadline` (`src/workers/manager.py:3165-3170`).
- `PortfolioAdvisor(sentinel_client, sentinel_cfg)` is built only when `advisor_enabled` and an API key are present (`src/workers/manager.py:3173-3183`), stored as `self._services["sentinel_advisor"]`, injected as `watchdog._sentinel_advisor` (`src/workers/manager.py:3186-3187`), and driven by the background `_sentinel_advisor_loop` (`src/workers/manager.py:3192-3229`). In `config.toml` `advisor_enabled = true` (overriding the dataclass default of `False` at `src/config/settings.py:3140`).
- The **Exit Firewall** is imported lazily at `src/core/layer_manager.py:1263` and called at `:1264`. It is also referenced at `src/core/trade_coordinator.py:554` as the contract for `active_symbols()`.

The deadline engine depends on the trade plan (from `coordinator.get_trade_plan`), the watchdog's `_push_sl_to_shadow`, and the coordinator's close path. The advisor depends on `DeepSeekClient`, `position_service`, and (on the consumer side) the watchdog and SL gateway.

### A.8 Fit in the end-to-end pipeline

The Deadline Engine hands off to the Position Watchdog as its executor and to the Trade Coordinator for close bookkeeping. On a profit-tier expiry under Profit-Fetching it hands off to the profit sniper trail (`src/workers/position_watchdog.py:2293-2305`). The Portfolio Advisor hands its recommendations to the watchdog, which routes them through the SL Gateway; the advisor never coordinates closes. The Exit Firewall sits between the BRAIN (System B) and the Trade Coordinator's `queue_strategic_action`, deciding which Claude actions are allowed to reach the queue.

### A.9 Known weaknesses / failure modes

- The Deadline Engine's grace state (`_graces`) is purely in-memory (`src/sentinel/deadline.py:62`); a process restart loses all active grace windows, so a breakeven-tier position that was awaiting grace expiry would be re-evaluated from scratch on the next expired tick.
- The advisor's `_estimate_cost` hardcodes DeepSeek V3 pricing ($0.27/$1.10 per million in/out tokens, `src/sentinel/advisor.py:196-197`); a model swap via `advisor_model` would silently mis-cost.
- The advisor loop builds its own PnL using mark vs entry and a side flip (`src/workers/manager.py:3201-3206`); this duplicates PnL logic that lives authoritatively in the coordinator, a drift risk flagged elsewhere in the codebase.
- Both `sentinel_advisor` and `sentinel_deadline` are listed as "advisory" SL write sources (`config.toml:1033`), and the watchdog coalesces them with a 10 s window (`src/workers/position_watchdog.py:1228-1239`) and is subject to the gateway's 30 s per-symbol rate limit — so a deadline tighten can be silently skipped if another writer raced the same symbol within the window.

## System B: BRAIN Position Management (Claude Call B + STRAT_ACTION handoff)

### B.1 What it is and its single responsibility

The BRAIN's position-management path is Claude reviewing every open position on a recurring cycle and emitting strategic actions (hold / close / tighten_stop / set_exit / take_profit). Its single responsibility is strategic, context-rich position management — distinct from the mechanical, fast-loop management of the watchdog/sniper. The reasoning lives in `src/brain/strategist.py` (the "strategist"), the prompt contract in `src/brain/prompts/position_review.py` and the `POSITION_SYSTEM_PROMPT` at `src/brain/strategist.py:421-437`, and the orchestration in `src/core/layer_manager.py`.

### B.2 When it activates — cadence and triggers

The brain runs an alternating two-call loop. `brain_interval_seconds = 150` (2.5 min), and the loop alternates Call A (find trades) and Call B (manage positions), giving each call type effectively a 5-minute cadence (`src/core/layer_manager.py:85-87`, loop sleep at `:732`). Call B is the position-management cycle (`src/core/layer_manager.py:912-979`).

Two distinct trigger paths reach the firewall-gated dispatcher `_execute_position_actions`:

- **Call B (`source="call_b"`)**: the dedicated position-review cycle. It calls `strategist.create_position_plan()` (`src/core/layer_manager.py:938`), and if Layer 3 is active, dispatches via `_execute_position_actions(plan, source="call_b")` (`src/core/layer_manager.py:962`). It is skipped entirely when there are no open positions (`src/core/layer_manager.py:919-927`).
- **Call A urgent (`source="call_a_urgent"`)**: when a Call A (trade-finding) cycle also returns `position_actions` (watchdog-escalated concerns), they dispatch via `_execute_position_actions(plan, source="call_a_urgent")` (`src/core/layer_manager.py:863-869`).

Call B additionally defers itself if any open position has price divergence above `divergence_block_prompt_pct` (default 1.0%), logging `PROMPT_DEFERRED` (`src/brain/strategist.py:1746-1759`) so Claude does not reason on stale prices.

### B.3 How it manages PROFIT

The decision framework in `POSITION_SYSTEM_PROMPT` is profit-maximizing, not preservation: "maximize the development of each position. Aggressive opportunity exploitation, not capital preservation" (`src/brain/strategist.py:421`). Concretely (`src/brain/strategist.py:432-434`): if PnL > +1.5% and structure suggests give-back risk → `tighten_stop` to lock gains; if PnL > +3% and aging → tighten aggressively or `set_exit` at the next strong level; otherwise hold. The `tighten_stop` action requires a `new_sl` price; `set_exit` requires an `exit_price` (`src/brain/strategist.py:428-429`). The watchdog position-review prompt similarly prefers `tighten_stop` over closing on profitable positions and forbids closing a profitable position because "it might reverse" (`src/brain/prompts/position_review.py:20, 22`).

### B.4 How it manages LOSS

Claude's close path is the loss-management lever, but it is deliberately constrained by multiple downstream guards:

1. **Prompt constraint**: close only on "genuine structural invalidation, SL approach with no recovery, or TP approach"; do NOT close on regime alignment alone or recency bias (`src/brain/strategist.py:434-435`).
2. **Parse-time downgrade**: a `tighten_stop` with invalid `new_sl`, or a `set_exit` with invalid `exit_price`, is downgraded to `hold` with `STRAT_CALL_B_DOWNGRADE` (`src/brain/strategist.py:7408-7419`); unknown action strings are coerced to `hold` (`src/brain/strategist.py:7398-7403`).
3. **Firewall**: for untrusted sources, close/take_profit are blocked (`src/sentinel/firewall.py:96-102`); `call_b`/`call_a_urgent` are trusted (`src/sentinel/firewall.py:37, 89-94`) but still subject to the phantom-close guard.
4. **Min-hold guard (watchdog)**: close/take_profit on positions younger than `strategic_action_min_hold_seconds` (default `300.0`, `config.toml:816`, `src/config/settings.py:1337`) are blocked with `STRAT_ACTION_CLOSE_BLOCKED` unless the reason matches an allowed hard-stop token (e.g. "stop loss hit", "structure invalidated", "regime change", "manual close") from `strategic_action_allowed_early_close_reasons` (`src/workers/position_watchdog.py:3715-3756`; allowed list `config.toml:817`).
5. **Brain-close multi-factor scoring**: discretionary closes that pass the min-hold guard are intercepted by `compute_brain_close_score` (`src/workers/position_watchdog.py:3789-4074`). With `wd_brain_scoring_enabled=true` and `wd_brain_scoring_enforce=true` (`config.toml:841-842`), a composite below `wd_brain_scoring_threshold` (default `6.0`, `config.toml:843`) yields one of three outcomes: `execute` (close proceeds), `reject` (`WATCHDOG_CLOSE_REJECTED`, close skipped), or `reject_and_tighten` (`WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` — the watchdog instead tightens the SL 30% toward breakeven via `_tighten_sl_breakeven_30pct` and skips the close) (`src/workers/position_watchdog.py:4133-4151`, `:1378-1431`). A hard risk floor overrides all of this: when SL consumption ≥ `wd_hard_risk_floor_sl_pct` (default `85.0`, `config.toml:851`) the close fires regardless of composite (`WATCHDOG_HARD_FLOOR_HIT`, `src/workers/position_watchdog.py:4085-4125`).

### B.5 Inputs

The strategist's `create_position_plan` builds a compact position prompt with no market scan (`src/brain/strategist.py:1761`, `_build_position_review_prompt` at `:7006`), sends it to Claude via `self.claude.send_message(prompt, POSITION_SYSTEM_PROMPT)` (`src/brain/strategist.py:1766`), and parses the JSON via `_parse_position_plan` (`src/brain/strategist.py:1773, 7357`). It also reads re-entry cooldowns from the coordinator (`get_active_reentry_cooldowns`, `src/core/trade_coordinator.py:2202-2207`) so it does not propose blocked re-entries. The watchdog scoring intercept additionally reads live PnL, SL consumption/proximity, time remaining, age, velocity, and XRAY structural match (`src/workers/position_watchdog.py:3826-4074`).

### B.6 Outputs / writes

Claude's actions are parsed into `plan.position_actions` (`PositionAction` objects, `src/brain/strategist.py:7425-7431`). `_execute_position_actions` writes them to the coordinator's queue via `coordinator.queue_strategic_action(...)` (`src/core/layer_manager.py:1284-1290`) and, for closes, records the reason via `coordinator.set_close_reason(symbol, "strategic_review")` (`src/core/layer_manager.py:1282`). The actual exchange writes happen later in the watchdog's `_execute_strategic_actions`: close/take_profit via `close_position(close_trigger="wd_claude_action")` + `STRAT_ACTION_CLOSE` (`src/workers/position_watchdog.py:4166-4168`); tighten via `_push_sl_to_shadow(..., source="brain_tighten")` + `STRAT_ACTION_SL` (`src/workers/position_watchdog.py:4184-4193`); set_exit via `set_take_profit` + `STRAT_ACTION_TP` (`src/workers/position_watchdog.py:4197-4199`). So the BRAIN advises and queues; the watchdog writes.

### B.7 Wiring (dependency graph)

The strategist is constructed by `WorkerManager`/`LayerManager`. The brain review loop lives in `LayerManager._run_brain_review`/`_run_brain_cycle` (`src/core/layer_manager.py` around `:751-979`), calling `strategist.create_position_plan()` (Call B) or the trade-finding path (Call A). Dispatch flows: strategist → `plan.position_actions` → `layer_manager._execute_position_actions` → firewall gate → `coordinator.queue_strategic_action` → (next watchdog tick) `coordinator.drain_strategic_actions` → `watchdog._execute_strategic_actions` → position_service / SL gateway. The watchdog drains the queue at the top of each tick (`src/workers/position_watchdog.py:924`, before the per-position immunity loop, so STRAT_ACTIONs are not immunity-gated — they have their own 300 s min-hold guard).

### B.8 Fit in the end-to-end pipeline

The BRAIN is the slow, strategic layer (5-minute Call B cadence). It coordinates with the watchdog (the STRAT_ACTION queue is the handoff channel), with the Exit Firewall (which gates its closes), with the brain-close scoring system (which can veto or convert its closes into tightens), and with the Trade Coordinator (which it queries for cooldowns and to which it writes close reasons and queued actions).

### B.9 Known weaknesses / failure modes

- There is a deliberate but lossy handoff latency: Call B runs roughly every 5 minutes, and the watchdog only drains the queue on its next ~10 s tick, with a re-verification that skips any action whose position closed during the brain cycle (`POS_ACTION_SKIP`, `src/workers/position_watchdog.py:3685-3693`). A fast-moving position can be closed by the watchdog/sniper before Claude's instruction lands.
- The brain-close score reads `pos.stop_loss` (current, possibly trailed) while the brain prompt historically read entry-time SL; the codebase ships a `WD_SL_PCT_DIVERGENCE` diagnostic precisely because these diverge after trailing (`src/workers/position_watchdog.py:3853-3960`) — a latent source of brain-vs-watchdog disagreement.
- The scoring intercept fail-softs on any exception (`WD_BRAIN_SCORE_FAIL`, `src/workers/position_watchdog.py:4152-4160`), meaning a bug in scoring silently falls through to the original close — the guard is best-effort, not a hard gate.

## System C: Trade Coordinator Close Path (`src/core/trade_coordinator.py`)

### C.1 What it is and its single responsibility

The Trade Coordinator is the central bookkeeping authority for the lifecycle of every open trade. Its close-path responsibility: when any component closes a position, record the outcome exactly once, fan it out to all registered consumers, reset per-symbol state (including the SL gateway), and arm the re-entry cooldown. It also owns immunity/min-hold gating, the strategic-action queue, and close-reason attribution.

### C.2 When it activates — cadence and triggers

It is purely event-driven; it has no tick. The close path activates when any executor calls `on_trade_closed(...)` (`src/core/trade_coordinator.py:1334`). Callers include the watchdog deadline path (`closed_by="sentinel_deadline_*"`, `src/workers/position_watchdog.py:2331`), the watchdog STRAT_ACTION close, the profit sniper, the SL/TP exits, and the Bybit demo WS subscriber. The strategic-action queue is drained on every watchdog tick (`drain_strategic_actions`, `src/core/trade_coordinator.py:564`).

### C.3 How it manages PROFIT

The coordinator does not decide profit-taking; it books the result. On close it computes `was_win` and the realized `pnl_usd`/`pnl_pct`. Profit-relevant logic: if `pnl_pct == 0` but a valid `exit_price` exists (the WS "sentinel-zero" contract), it back-derives `pnl_pct` and flips `was_win` accordingly (`src/core/trade_coordinator.py:1419-1434`); if `pnl_usd == 0` it derives it from size × entry × pnl% (`src/core/trade_coordinator.py:1437-1448`). Under the loss-only cooldown mode (see C.4), a winning close arms NO re-entry cooldown — the symbol stays immediately re-tradeable (`REENTRY_COOLDOWN_SKIP_WIN`, `src/core/trade_coordinator.py:1825-1830`).

### C.4 How it manages LOSS

The coordinator's loss-management is structural, not price-based:

- **Immunity / min-hold**: on registration, a position is granted `immunity_seconds` from the per-category `MINIMUM_HOLD_SECONDS` map (`src/core/trade_coordinator.py:683`; map at `:127-143`, e.g. `claude_direct`=120 s, `momentum`=300 s, `funding_arb`=600 s, `default`=60 s). During immunity the watchdog skips the position (`is_immune`, `src/core/trade_coordinator.py:832-850`; consumed at `src/workers/position_watchdog.py:944-950`). A universal 120 s "newborn" grace also blocks closing via `get_maturity` (`src/core/trade_coordinator.py:852-875`, consumed at `src/workers/position_watchdog.py:994-997`). This prevents premature loss realization from fees/noise on fresh trades.
- **Re-entry cooldown**: after a close, a per-(symbol, direction) cooldown is armed for `_reentry_cooldown_seconds` (`src/core/trade_coordinator.py:1815-1817`). The default is `_DEFAULT_REENTRY_COOLDOWN_SECONDS = 300` (`src/core/trade_coordinator.py:166`), overridden at boot from `[apex].reentry_cooldown_seconds` = `1200` (20 min) via `set_reentry_cooldown_seconds` (`src/workers/manager.py:650-653`; config `config.toml:2356`). With `[apex].loss_cooldown_enabled = true` (`config.toml:2346`, wired via `set_loss_cooldown_enabled`, `src/workers/manager.py:656-659`), the cooldown is armed ONLY on a real loss (booked net `pnl_usd < 0`); wins and scratches skip it (`src/core/trade_coordinator.py:1806-1830`). Opposite-direction re-entry on the same symbol is allowed by design. The gate is queried via `is_reentry_blocked` (`src/core/trade_coordinator.py:2081-2128`), which lazily clears expired entries.
- **Double-close protection**: `on_trade_closed` pops the symbol's `TradeState`; if it is already `None`, the close is a duplicate (a race between watchdog/sniper/SENTINEL) and is skipped with `COORD_DOUBLE_CLOSE` and an early return (`src/core/trade_coordinator.py:1369-1378`). The queue side has its own phantom-close guard: `queue_strategic_action` rejects close/take_profit on a symbol not in `_trades` with `PHANTOM_CLOSE_REJECTED | layer=coordinator` (`src/core/trade_coordinator.py:537-542`).

### C.5 Inputs

`on_trade_closed` receives `symbol`, `pnl_pct`, `pnl_usd`, `was_win`, `closed_by`, optional authoritative `exit_price`/`price_source`, and reconcile reference fields (`src/core/trade_coordinator.py:1334-1351`). It reads its own `_trades` (TradeState, including `opened_at`, `entry_price`, `side`, `size`), `_trade_info`, and `_trade_plans`. Close reasons are set via `set_close_reason` (`src/core/trade_coordinator.py:570-580`) and resolved via `pop_close_reason` (`src/core/trade_coordinator.py:582-600`).

### C.6 Outputs / writes

The coordinator does not write the exchange. It: appends a close record to `_closed_trades` (`src/core/trade_coordinator.py:1775`); pops per-symbol caches (`_last_brain_context`, `_trade_plans`, `_trade_info`, `src/core/trade_coordinator.py:1785-1787`); fans the record out to every registered close callback (`src/core/trade_coordinator.py:1789-1797`); and arms the re-entry cooldown (`src/core/trade_coordinator.py:1815-1824`). `POSITION_CLOSE_REASON` is emitted at INFO from `set_close_reason` the moment a close is decided (`src/core/trade_coordinator.py:578-580`).

The close-callback fan-out is the key output channel. Among the callbacks registered in the manager is the **SL gateway reset**: `_sl_gateway_reset_on_close` calls `sl_gateway.reset_symbol(sym)` so a new trade on the same symbol does not inherit the prior trade's rate-limit budget or step-size baseline (`src/workers/manager.py:774-781`). Other registered callbacks include the performance enforcer, fund manager, registry, PnL manager, thesis manager, data lake, trade history, positions-table cleanup, sniper-unsubscribe, event-buffer clear, transformer-cache clear, strategist position-invalidate, urgent-queue clear, and TIAS (`src/workers/manager.py:2168-2977`). The total count is logged at boot (`TradeCoordinator: {n} close callbacks registered`, `src/workers/manager.py:3249-3250`).

### C.7 Wiring (dependency graph)

The coordinator is constructed early in `WorkerManager` and shared as `self._services["trade_coordinator"]`, injected into the watchdog (`src/workers/manager.py:1573`), the layer manager, and the strategist. Callbacks are registered via `register_close_callback` (`src/core/trade_coordinator.py:2238-2240`). The cooldown duration and loss-only mode are wired from `[apex]` settings (`src/workers/manager.py:650-659`). The SL gateway reset callback is registered immediately after the gateway is constructed (`src/workers/manager.py:768-781`).

### C.8 Fit in the end-to-end pipeline

The coordinator is the convergence point for every close: SENTINEL deadline closes, BRAIN strategic closes, profit-sniper exits, time-decay force-closes, and exchange SL/TP fills all terminate in `on_trade_closed`. It hands the closed-trade record to ~15 downstream consumers (learning, accounting, thesis, data lake, dashboards) and resets the SL gateway so the per-symbol stop-management state is clean for the next trade. On the entry side it gates immunity and re-entry cooldown, which the watchdog and the trade-selection layers consult.

### C.9 Known weaknesses / failure modes

- The double-close guard relies on a non-atomic `_trades.pop`; the comment itself notes the race between Watchdog/ProfitSniper/SENTINEL (`src/core/trade_coordinator.py:1371-1372`). Two truly-simultaneous closers could both observe a non-None state before either pops if the calls interleave at the await boundary, though in practice the executors are serialized on the event loop.
- Close-callback exceptions are caught per-callback and only logged (`COORD_CB_FAIL`, `src/core/trade_coordinator.py:1794-1797`); a failing callback (e.g., the SL gateway reset) does not abort the close, so a silently-failing `reset_symbol` could let a new same-symbol trade inherit a stale rate-limit baseline.
- The re-entry cooldown is keyed on `time.monotonic()` and held in-memory (`src/core/trade_coordinator.py:1816, 202`); a process restart clears all cooldowns, allowing immediate re-entry on a symbol that just took a loss.
- `register_trade` overwrites an existing `TradeState` silently (warning-only `COORD_DUPLICATE_REGISTER`, `src/core/trade_coordinator.py:694-703`); if the upstream cooldown gate ever fails, a duplicate registration would reset immunity and lose the prior trade's open timestamp.

---

# Part 8 — How It Actually Behaves: The Measured Reality

The systems documented above are sophisticated and individually well-built. But a
pipeline must be judged by its output, and the live data from 2026-06-15 (24 trades
closed since the 01:39 restart) exposes that the whole, as currently tuned, loses money.

## The measured numbers

Win rate 33 percent (8 of 24). Realized total minus 2.05 percent (net minus 1.05
dollars). Average win plus 0.086 percent against average loss minus 0.171 percent — the
losses are roughly twice the size of the wins, so the reward-to-risk is inverted. Zero
take-profit targets were hit; every exit was a stop-out or a scratch. The biggest single
loss was minus 1.35 percent (OPUSDT). Twenty of the twenty-four trades reached green
(peaked at or above plus 0.10 percent), so the entries do catch a move — but the average
peak was only plus 0.26 percent, and seventeen of those twenty winners gave the peak back
to at or below half. That is the clip, measured.

## The three failure modes the pipeline does not currently overcome

First, edge quality at entry. Every trade goes green but only to a tiny, reversing peak
(plus 0.26 percent average). The position-management systems in this document can only
manage what the entry hands them; they cannot manufacture a trend. The ceiling is set at
entry, and on this evidence the entries are catching noise-level moves rather than
sustained ones. The entry and coin/side selection pipeline is upstream of everything here
and is the real ceiling on profitability.

Second, the profit-fetching calibration. The ladder arms at plus 0.10 to plus 0.20
percent and locks at plus 0.05 to plus 0.13 percent (Parts 2 and 6). On a winner that
only peaks plus 0.26 percent, that pins the stop just above breakeven, and the normal
pullback takes it out before the Chandelier trail can ever develop. The arm, lock, and
trail values are too tight for the move sizes the entries actually produce. This is the
single most direct lever on the give-back, and it is a calibration change, not a
structural one.

Third, the loss-to-win ratio. The loss engine (Parts 3 and 5) does cut grinds and stalls,
but the average loss is twice the average win and some losses ride to the hard stop
(OPUSDT minus 1.35 percent). Even with the entries and the clip unchanged, a system that
loses twice what it wins on a 33 percent win rate cannot be profitable.

## On the recently-added owner switch (Part 6)

The trade-state owner switch resolved the multi-writer collision over the stop and is
correct and safe. But the live data is unambiguous that the collision was not the
dominant driver of the losses: across every enforced trade, zero caging writers were ever
blocked, because none were trying to cage the winners. The clip is caused by the lock
calibration above, not the collision. The owner switch is a sound foundation; it is not
the lever that turns this profitable.

## The honest conclusion

The pipeline is not an undifferentiated failure — its individual systems work as designed
and its protections are real. But as a whole it is unprofitable for three concrete,
separable reasons, in order of impact: weak entries (small reversing moves), a profit
lock calibrated far too tight for those move sizes, and a loss-to-win ratio that is upside
down. Fixing the give-back requires the arm/lock/trail calibration; lifting the ceiling
requires improving entry/selection quality; and surviving requires tightening loss-cutting
so losses stop exceeding wins. No single change in this document's scope fixes all three.

