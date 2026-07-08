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
