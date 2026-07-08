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
