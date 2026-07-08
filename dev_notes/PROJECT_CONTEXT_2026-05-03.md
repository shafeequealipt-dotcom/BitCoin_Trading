# Trading Intelligence MCP — Project Context Reference

**Date:** 2026-05-03
**Purpose:** Persistent reference of the project's architecture, data flow, services, layers, conventions, and build/deploy. Future sessions read this instead of re-deriving from the codebase.
**Sources:** comprehensive code audit of working tree on 2026-05-03 + cross-reference against `PROJECT_BLUEPRINT.md`, `CLAUDE.md`, `dev_notes/price_source_divergence/FULL_BUNDLE.md`, and verified file:line citations against current code.

---

## 1. Top-Level Layout

### 1.1 Project root

`/home/inshadaliqbal786/trading-intelligence-mcp/`

Key files at root: `brain.py` (deprecated v1 entry, manual one-off only), `server.py` (MCP server entry), `workers.py` (production main — runs the workers process), `config.toml` (master config), `pyproject.toml`, `Makefile`, `requirements.txt`, `README.md`, `CLAUDE.md` (mandatory rules), `PROJECT_BLUEPRINT.md` (44 KB, 743 lines, 2026-04-12), `PROJECT_BIBLE.md` (38 KB), `SYSTEM_INVENTORY.md`. Runtime state in `data/` (sqlite DBs in WAL mode, logs, layer_state.json, backups). Co-project at `shadow/`. Source tree at `src/` (21 packages). Tests at `tests/`. Operational helpers at `scripts/`. Systemd units at `systemd/`. Phase docs at `dev_notes/`.

### 1.2 Source packages (`src/`)

`alerts/`, `analysis/`, `apex/`, `brain/`, `config/`, `core/`, `database/`, `factory/`, `fund_manager/`, `intelligence/` (news, reddit, altdata, sentiment, signals), `mcp/`, `portfolio/`, `risk/`, `sentinel/`, `shadow/` (the adapter, NOT the co-project), `strategies/`, `telegram/`, `tias/`, `trading/`, `workers/`.

### 1.3 Systemd units (`systemd/`)

| Unit | Role | ExecStart |
|---|---|---|
| `trading-workers.service` | Production main — all 28 workers + brain v2 + Telegram bot | `python workers.py` |
| `trading-mcp-sse.service` | MCP server for Claude Code on `localhost:8080` | `python server.py --transport sse --port 8080` |
| `trading-brain.service` | Brain v1 (deprecated, kept for one-off analysis only) | `python brain.py` |
| `trading-backup.timer` + `trading-backup.service` | Hourly DB backup | `scripts/backup.sh` |

The brain v1 entry warns at import: `brain.py:29` "WARNING: brain.py runs Brain v1 (legacy). Brain v2 runs inside workers.py." Production sessions ignore brain.py entirely.

### 1.4 CLAUDE.md (verbatim mandatory rules)

Root rule: **Analyse Before Touching Anything** — before removing/modifying/moving any variable/function/block/import: (1) grep all usages across the entire file, (2) grep callers in other files, (3) map all dependencies, (4) never assume a block is self-contained.

General rules: professional / industry standard / enterprise level; do not assume — verify; no band-aid fixes (root cause first); do not touch any file without fully understanding wiring + integration + connections; read every file listed before writing code; analyse first, implement second.

These rules apply to every change in this project.

---

## 2. Process Topology and IPC

### 2.1 Production processes

Three systemd-managed processes in steady state:
- **`trading-workers.service`** — main orchestrator running `WorkerManager(workers.py)`. Owns 28 workers, the database connection pool, both Shadow + Bybit service sets, and the Transformer state machine.
- **`trading-mcp-sse.service`** — MCP server on port 8080 (SSE transport). **Has its own independent service stack** (separate `BybitClient`, separate `DatabaseManager` connection, separate repositories). Zero imports of Shadow or the Transformer (verified by grep on `src/mcp/`).
- **`trading-backup.timer`** — hourly database snapshot.

`trading-brain.service` exists but is NOT in production rotation. Brain v2 runs inside the workers process as a scheduled task on a 300-second cycle via `LayerManager.layer2_task()`.

### 2.2 IPC

| Caller | → | Target | Port | Purpose |
|---|---|---|---|---|
| Workers | → | Shadow co-project | 9090 (HTTP) | Paper-trade order placement, position queries, balance |
| Workers | → | Bybit mainnet | 443 (HTTPS+WSS) | Real market data, real account info (live mode only) |
| MCP server | → | Bybit mainnet | 443 | Tool execution (always Bybit-direct, never via Shadow) |
| Workers | → | Finnhub, AlternativeMe, CoinGecko | 443 | News + altdata |
| Workers | → | Telegram Bot API | 443 | Interactive bot |

**Two parallel WebSockets to Bybit:**
- main project's PriceWorker uses `pybit.unified_trading.WebSocket` (thread-pool callback model) → populates in-memory `_ws_quotes` dict and (currently broken) `ticker_cache` SQLite table
- Shadow's WebSocketManager uses raw `websockets` library (asyncio-native) → populates Shadow's in-memory `_latest_tickers` dict and Shadow's `ticker_snapshots` table

This duplication is the architectural anti-pattern that produces the price-source divergence bug. The Phase 1-3 fix neutralizes the corruption symptoms; the duplication itself is deferred to a future architectural cleanup.

### 2.3 Shared storage

Two separate SQLite databases:
- `data/trading.db` (main project, schema v26, ~60 tables, WAL mode)
- `/home/inshadaliqbal786/shadow/data/shadow.db` (Shadow, ~5 tables)

No file-level shared memory. Both processes inherit env from systemd units (same OS user). HTTP on `127.0.0.1:9090` is the only inter-process channel between main and Shadow.

---

## 3. Data Layer

### 3.1 `src/database/` modules

- `connection.py` — `DatabaseManager` (aiosqlite wrapper), WAL mode, lock-wait warnings (`db_lock_wait_threshold_ms`), 30s query timeout, async interface (`execute`, `fetch_one`, `fetch_all`, `fetch_scalar`, `executemany`).
- `migrations.py` — `SCHEMA_VERSION = 26`, ~56 CREATE TABLE statements.
- `models.py` — minimal (most models live in repositories).
- `cleanup.py` — retention + VACUUM + WAL checkpoint.
- `protected_tables.py` — caller-attribution logger for protected tables.
- `repositories/` — 12 repository classes (see 3.2).

### 3.2 Repositories (`src/database/repositories/`)

| Repository | Purpose | Primary tables |
|---|---|---|
| `MarketRepository` | OHLCV + tickers + orderbook | `klines`, `ticker_cache`, `orderbook_snapshots` |
| `TradingRepository` | Orders + positions + trades | `orders`, `positions`, `trade_history`, `account_snapshots` |
| `NewsRepository` | News + sentiment scoring | `news_articles` |
| `SentimentRepository` | Aggregated sentiment | `aggregated_sentiment`, `reddit_posts` |
| `AltDataRepository` | F&G + funding + OI + signals | `fear_greed_index`, `funding_rates`, `open_interest`, `signals` |
| `LearningRepository` | Strategy performance | `strategy_performance`, `signal_accuracy`, `pattern_log` |
| `ContextRepository` | Market + position snapshots, daily P&L | `market_snapshots`, `position_snapshots`, `daily_pnl` |
| `TradeIntelligenceRepository` | TIAS post-trade analysis | `trade_intelligence` (TIAS table with `ds_*` DeepSeek columns) |
| `TelegramRepository` | Bot state | (Telegram-specific) |
| `FactoryRepository` | Factory strategy state | (Factory) |
| `BacktestRepository` | Backtest results | (Backtest) |
| `PortfolioRepository` | Portfolio snapshots | (Portfolio) |

All DB writes go through repositories. `ProtectedTableMonitor` logs which component wrote to protected tables (orders, positions, trade_intelligence) for audit.

### 3.3 Tables in `data/trading.db` (categorized)

- **Market data:** `klines`, `ticker_cache`, `orderbook_snapshots`.
- **Trading:** `orders`, `positions`, `trade_history`, `account_snapshots`, `strategy_trades`.
- **Intelligence:** `news_articles`, `reddit_posts`, `aggregated_sentiment`, `fear_greed_index`, `funding_rates`, `open_interest`, `signals`.
- **Strategy engine:** `active_universe`, `regime_history`, `ensemble_votes`, `strategy_params`.
- **Learning:** `strategy_performance`, `signal_accuracy`, `pattern_log`.
- **Thesis & coordination:** `trade_thesis`, `brain_decisions`, `claude_decisions`, `transformer_state`, `switch_history`.
- **Fund manager & risk:** `fund_manager_state`, `profit_ratchet_log`, `hourly_performance`, `daily_summary`.
- **TIAS:** `trade_intelligence`.
- **Data lake (audit):** `market_snapshots`, `trade_log`, `position_snapshots`, `daily_summary`, `session_log`, `economic_calendar`.
- **Supporting:** `active_strategies`, `watchlists`, `user_preferences`, `schema_version`.

### 3.4 Tables in `shadow/data/shadow.db`

- `virtual_positions` — open positions in paper trading. **Authoritative for fills.** Columns include `symbol`, `side`, `quantity`, `entry_price` (post-slippage), `notional_value`, `unrealized_pnl_usd`, `net_pnl_usd`, `gross_pnl_usd`, `entry_slippage_pct`, `exit_slippage_pct`, `entry_fee_usd`, `exit_fee_usd`, `close_trigger`, `closed_at`.
- `virtual_wallet` — singleton with `starting_balance`, `available_balance`, `used_margin`, `unrealized_pnl`, `total_realized_pnl`, `total_fees_paid`.
- `ticker_snapshots` — 60s-cadence price archive. Columns include `symbol`, `last_price`, `mark_price`, `bid1_price`, `ask1_price`, `price_change_24h_pct`, `funding_rate`, `open_interest`, `timestamp`.
- `klines` — 1m OHLCV (independent backfill from Bybit).
- `daily_summary` — daily trade aggregates.

**Critical fact (T1 Pattern A from forensic):** `trade_intelligence.entry_price` and `virtual_positions.entry_price` always differ by exactly ±0.03% on every trade. Shadow stores the post-slippage `fill_price`; main records the pre-slippage `last_price`. This is by-design (Shadow's `slippage_pct = 0.03` config) and NOT a bug. Joins between the two tables must use `(symbol, qty)`, never `entry_price`.

---

## 4. Service Container and DI Pattern

### 4.1 ServiceContainer (`src/core/container.py`)

Singleton dict-of-services manually wired at startup. The dict is `WorkerManager._services` and is populated in a deterministic order during `WorkerManager.__init__` (`src/workers/manager.py`, ~3000 lines).

Wiring sequence (high level):
1. Load Settings from config.toml + .env.
2. Setup loguru routing.
3. Create DatabaseManager + run migrations.
4. Create BybitClient + Bybit services (MarketService, OrderService, PositionService, AccountService).
5. Create Shadow services (ShadowOrderService, ShadowPositionService, ShadowAccountService).
6. Create Transformer at `src/workers/manager.py:90`. Call `transformer.set_services(...)` at `:303` to attach both service sets. Call `transformer.create_proxies()` at `:314` to build the `_OrderProxy` / `_PositionProxy` / `_AccountProxy` trio.
7. Create TAEngine + TACache.
8. Create repositories.
9. Create StrategyRegistry (registers all 41 strategies).
10. Create APEX (Layer 3 optimization), TIAS (post-trade), Brain v2, RiskManager, AlertManager, FundManager, Sentinel.
11. Create the 28 worker instances. Each receives `self.services` reference.
12. Instantiate LayerManager.
13. Start asyncio loop, run all workers + LayerManager brain task.

Workers, the Telegram bot worker, brain v2, watchdogs, and fund manager all read `position_service` / `account_service` / `order_service` from this dict — those keys point at the **Transformer's proxy objects**, not the underlying Shadow or Bybit services. The proxy decides at call time which service set to delegate to based on `_current_mode`.

### 4.2 Transformer (`src/core/transformer.py`, 1064 lines)

State machine + service proxies + price enrichment.

State machine fields:
- `_current_mode: str` — "shadow" or "bybit"
- `_is_switching: bool` — guards against switching mid-flight
- `_switching_to: str | None` — destination mode during a switch
- `_last_switched_at: str | None` — ISO timestamp of last successful switch
- `_initialized: bool`
- `_shadow_available: bool` — live health flag

Service sets:
- `_shadow_services = {order, position, account}` — wired at startup
- `_bybit_services = {order, position, account}` — wired at startup
- `_active_services` — points at one set based on current mode

Proxies (created at startup, used by all consumers):
- `_OrderProxy` — delegates `place_order`, `modify_order`, `cancel_order`, `cancel_all_orders`, `get_open_orders`, `get_order_history`. Blocks `place_order` during a mid-switch state with a REJECTED Order.
- `_PositionProxy` — delegates `get_positions`, `get_position`, `close_position`, `reduce_position`, `close_all_positions`, `set_leverage`, `set_stop_loss`, `set_take_profit`, `get_pnl_summary`, `get_last_close`. Calls `_enrich_positions_with_local_prices` after every `get_positions` / `get_position` in shadow mode. Blocks SL/TP modification during a switch.
- `_AccountProxy` — delegates `get_wallet_balance`, `get_available_balance`, `get_equity`, `get_margin_usage`. Calls `_enrich_balance_with_local_prices` + `_save_account_snapshot` in shadow mode.

Switch engine (`switch_to`):
1. Validate target mode + Bybit confirmation flag (real-money guard).
2. Check target exchange is reachable.
3. Persist `is_switching=True` to `transformer_state` row.
4. Close all positions on current exchange.
5. Verify zero positions before flipping.
6. Capture equity snapshots.
7. Flip `_current_mode`, update `_active_services`, persist state.
8. Record event in `switch_history`.
9. Fire `_on_switch_callbacks` (PnL manager reset, fund manager refresh).

Crash recovery: on `initialize`, if `is_switching=true` was persisted, check whether positions are still open on the leaving side. If positions remain → cancel the switch. If positions cleared → complete the switch.

Price enrichment (the bug source — see Section 12):
- `_get_local_price(symbol)` reads `last_price` from `ticker_cache` with freshness gate (`local_max_age_seconds`, default 10s). Returns None when stale.
- `_enrich_positions_with_local_prices(positions)` mutates `pos.mark_price` and recomputes `pos.unrealized_pnl` when local-vs-Shadow divergence is within `divergence_override_pct` (default 0.5%). Above the threshold, keeps Shadow's value and emits `PRICE_OVERRIDE` log + `price_override` event-buffer event.
- `_enrich_balance_with_local_prices(balance)` does the same mutation on `AccountInfo.unrealized_pnl` and `AccountInfo.total_equity`.
- `_last_enrichment_max_divergence_pct` — running max divergence per pass. **Strategist's PROMPT_DEFERRED gate at `src/brain/strategist.py:280-298, 500-523` reads this field**; the Phase 2 fix preserves it byte-for-byte.

---

## 5. Worker Tier Model

### 5.1 BaseWorker (`src/workers/base_worker.py`)

`BaseWorker(name, interval_seconds, settings, db)` + abstract `async tick()`. The `run()` loop handles scheduling, error backoff, restart counting, liveness recording, and Phase 10+ observability tags (`WORKER_FIRST_TICK`, `WORKER_TICK_START`, `WORKER_TICK_FAIL`, slow-tick warnings).

Error recovery: exponential backoff 1s → 2s → 4s → 8s up to `max_consecutive_failures`. On exceedance, worker enters ERROR state and WorkerManager may restart the whole process.

### 5.2 WorkerTier enum (`src/core/types.py`)

`LAYER1A` (always-running data fetchers), `LAYER1B` (cycle-triggered analyzers), `LAYER1C` (strategy pipeline), `LAYER1D` (selector + package builder), `LAYER4` (position monitoring), `LAYER5` (reserved), `UTILITY` (support).

### 5.3 Worker directory (28 instances)

Layer 1A (always running): `KlineWorker` (45s), `PriceWorker` (45s WS health check), `AltDataWorker` (300s), `NewsWorker` (300s), `RedditWorker` (600s, often disabled).

Layer 1B (cycle-gated): `StructureWorker` (60s X-RAY 10-phase), `SignalWorker` (120s), `RegimeWorker` (600s BTC regime).

Layer 1C: `StrategyWorker` (45s — runs the 41-strategy pipeline → scoring → ensemble → RuleEngine → execution).

Layer 1D: `ScannerWorker` (300s top-N coin selection + briefing pack).

Layer 3: `EnforcerWorker` (60s thesis quality gates), `FundManagerWorker` (60s capital rebalance).

Layer 4: `PositionWatchdog` (10s rules / 30s Claude review), `ProfitSniper` (5s mode 4 trailing).

Utility: `CleanupWorker` (3600s retention + VACUUM), `TelegramBotWorker` (60s bot polling), `ScheduledReportWorker` (300s daily summary), `OptimizationWorker` (3600s weight tuning), `TrialMonitorWorker` (3600s factory trials), `DiscoveryWorker` (7200s AI candidates), `BacktestWorker` (3600s), `AllocationWorker` (300s rebalance), `PriceAlertWorker` (10s), `LiveMonitorWorker`, `FundReconciler`, `WorkerLivenessWatchdog` (30s heartbeat).

### 5.4 Cycle-gated execution

LayerManager owns Layer 1/2/3 active flags. Cycle-gated workers (1B, 1C, 1D, 3) skip their tick when their layer is inactive: `LAYER1{B,C,D}_TICK_SKIP | reason=cycle_inactive` at DEBUG, rate-limited to INFO every 600s. Toggling trading off via Telegram `/stop` flips Layer 2 flag → cycle-gated workers pause.

### 5.5 Sweet-spot scheduling

Batch workers (kline, structure, scanner) wait for the next natural boundary instead of running on a fixed interval. `settings.workers.sweet_spot_enabled = True` by default. PriceWorker uses fixed-interval (continuous WS stream — no benefit to sweet-spot).

### 5.6 Health monitoring

`WorkerLivenessTracker` (singleton in `src/core/worker_liveness.py`) records every successful tick. `WorkerLivenessWatchdog` runs every 30s, emits `WORKER_LIVENESS_HEARTBEAT` INFO continuously, `WORKER_NEVER_TICKED` / `WORKER_TICK_OVERDUE` WARNING when a worker stops ticking.

PriceWorker emits `PRICE_WS_HEALTH | msgs_per_min=N msgs_in_window=M ...` every tick (default 45s) so operators can detect a quiet-but-connected stream from a hung-but-still-connected stream.

---

## 6. Trading Services (Real Bybit Path)

### 6.1 BybitClient (`src/trading/client.py`)

Wrapper over pybit SDK with custom rate limiting + error mapping. Rate limits from config: `bybit.rate_limit_per_second = 10` (REST), Finnhub `60/min`. WebSocket has no rate limit (push-based).

### 6.2 Services (`src/trading/services/`)

- `MarketService`: `get_ticker(sym)` (5s in-memory `_ticker_cache` TTL via TACache pattern, F-C feed in V1 matrix), `get_all_tickers`, `get_klines`, `get_orderbook`. `_fetch_ticker` is the REST path that populates `ticker_cache` SQLite table.
- `OrderService`: `place_order(symbol, side, order_type, qty, price=None, stop_loss=None, take_profit=None, leverage=None, *, purpose, layer_snapshot, force)` — enforces a Layer 3 gate when `layer_snapshot` is provided. Returns `Order` dataclass.
- `PositionService`: `get_positions`, `get_position`, `update_stop_loss`, `update_take_profit`, `close_position(*, purpose)`, `reduce_position`, `close_all_positions`, `set_leverage`, `get_pnl_summary`. (Bybit `PositionService` does NOT have `get_last_close` — only Shadow does.)
- `AccountService`: `get_wallet_balance`, `get_margin_info`, `get_fees`.
- `InstrumentService`: instrument metadata.

### 6.3 BybitWebSocket (`src/trading/websocket.py`)

Wraps `pybit.unified_trading.WebSocket`. Public streams: `subscribe_ticker(symbols, callback)`, `subscribe_kline(symbol, timeframe, callback)`. Private streams (auth required) for order + position updates exist but are not currently wired.

**Callback model — the Bug 1 source.** pybit invokes the callback on a thread pool thread. That thread has no asyncio event loop. The current PriceWorker callback at `price_worker.py:215-220` tries `asyncio.get_running_loop()` and `loop.create_task(market_repo.save_ticker(...))`, which always raises `RuntimeError`, which is silently swallowed by `except RuntimeError: pass`. The DB write never happens. This is the bug the Phase 3 fix addresses by switching to `asyncio.run_coroutine_threadsafe(coro, captured_loop)`.

### 6.4 TACache (`src/analysis/ta_cache.py`)

Three-tier cache with different TTLs: TACache (5s) for indicators, StructureCache (300s) for X-RAY structure, TickerCache (5s, in-memory dict on MarketService — F-C feed). Hit/miss stats logged every 60s as `TA_CACHE_STATS | hits=N misses=M hit_rate=X%`.

---

## 7. Shadow Adapter (`src/shadow/shadow_adapter.py`, 774 lines)

Drop-in replacement for Bybit services that routes calls to Shadow's HTTP API on `localhost:9090`.

### 7.1 Adapter classes

- `ShadowOrderService` — mirrors `OrderService`. `place_order` → POST `/api/order`, returns `Order` with proper enum conversion. Phase-1-post-Layer-1 added kw-only `purpose`, `layer_snapshot`, `force` parameters for parity with the live `OrderService`.
- `ShadowPositionService` — mirrors `PositionService`. `get_positions` → GET `/api/positions`, `close_position(*, purpose)` → POST `/api/close`, `reduce_position` → POST `/api/reduce`, `set_stop_loss` → POST `/api/set-sl`, `set_take_profit` → POST `/api/set-tp`. **Has the extra method `get_last_close(symbol)` → GET `/api/position/{symbol}/last_close`** at lines 192-225, returning the authoritative close record (`exit_price`, `net_pnl_pct`, `net_pnl_usd`, `close_trigger`, `closed_at`, `hold_duration_seconds`). This is the source-of-truth for close P&L that Phase 1's helper consumes.
- `ShadowAccountService` — mirrors `AccountService`. `get_wallet_balance` → GET `/api/balance`.

### 7.2 Boot-grace retry helper (`_shadow_get_with_retry` at lines 59-127)

5 attempts with exponential backoff (0.2s, 0.4s, 0.8s, 1.6s, 3.2s, total ~3s worst case). First 30s of process lifetime: failures log at DEBUG (boot-grace window — Shadow's HTTP listener may not be ready yet). After 30s: failures log at ERROR. `_BOOT_GRACE_SECONDS = 30.0` at line 51.

### 7.3 Field mapping (Shadow JSON → Position dataclass)

Per `_build_position` at `shadow_adapter.py:673-700`:
- `data["symbol"]` → `Position.symbol`
- `data["side"]` → `Position.side` (Side enum)
- `data["qty"]` → `Position.size`
- `data["entry_price"]` → `Position.entry_price` (Shadow's post-slippage fill price)
- `data["current_price"]` → `Position.mark_price`
- `data["unrealized_pnl_usd"]` → `Position.unrealized_pnl`
- `data["leverage"]` → `Position.leverage`
- `data["stop_loss_price"]` → `Position.stop_loss`
- `data["take_profit_price"]` → `Position.take_profit`

---

## 8. Shadow Internals (Read-Only Co-Project)

`/home/inshadaliqbal786/shadow/shadow.py` startup sequence:
1. Parse args, load `ShadowConfig` from TOML, set up logging.
2. DatabaseManager + run migrations + initialize wallet.
3. CoinSelector picks top-N by volume (default 50).
4. WebSocketManager connects to Bybit `wss://stream.bybit.com/v5/public/linear` via raw `websockets` library.
5. KlineCollector backfills missing klines.
6. VirtualWallet + OrderEngine + PositionMonitor.
7. WalletSnapshotter, DailyRollup.
8. HTTP API server at `localhost:9090` (aiohttp).
9. Telegram bot (separate from main project's bot — different token).
10. Start all asyncio tasks. Handle SIGTERM/SIGINT gracefully.

### 8.1 Core internals

- `OrderEngine.place_order` at `shadow/src/exchange/order_engine.py:174-194` reads `_price_fn(symbol)` (which calls `WebSocketManager.get_latest_ticker(symbol)`) and applies slippage: `fill_price = last_price × (1 ± slippage_pct/100)`. `slippage_pct = 0.03` from config.toml. Stores `fill_price` as `virtual_positions.entry_price`. This is the source of the by-design ±0.03% gap vs main's `trade_intelligence.entry_price`.
- `OrderEngine.get_positions` at `:660-701` reads `virtual_positions WHERE status='open'` and computes `current_price` from the live WS dict. **W2 anomaly A4:** when `_price_fn` returns None (WS hasn't ticked since position open), `current_price = row["entry_price"]` → unrealized P&L flatlines at 0. Separate Shadow-side fix, out of scope for the current main-project fix.
- `OrderEngine.close_position` at `:438-462` returns `{symbol, side, entry_price, exit_price, qty, gross_pnl_pct, gross_pnl_usd, exit_fee, net_pnl_pct, net_pnl_usd, close_trigger, leverage, hold_duration_seconds}`. This is the authoritative close record.
- `WebSocketManager._handle_ticker_message` at `shadow/src/collector/websocket.py:313-335` runs INSIDE Shadow's asyncio event loop (raw websockets library is asyncio-native, no thread-pool indirection). Dict assignments (`_latest_tickers[symbol] = existing`) are GIL-atomic. The TickerCollector snapshot path writes to `ticker_snapshots` every 60s via a normal asyncio task — no broken async-from-thread bridge.

### 8.2 Shadow's authoritative tables

`virtual_positions` is THE source of truth for paper-trade fills (entry/exit price, slippage applied, fees deducted, net P&L). `virtual_wallet` is THE source of truth for cash balance and lifetime realized P&L. `ticker_snapshots` is the 60s-cadence price archive (independent of main project's `ticker_cache`).

---

## 9. MCP Server

### 9.1 Architecture (`src/mcp/server.py`, 273 lines)

Independent process (port 8080 SSE). Has its OWN `BybitClient`, `MarketService`, `OrderService`, `PositionService`, `AccountService` constructed in `_init_services` at `:97-174`. **Zero imports of Shadow or Transformer.** `position` service in MCP context is the real Bybit `PositionService`; calling `get_positions` via MCP returns Bybit-mainnet positions (empty for paper trading).

This makes MCP **structurally peripheral** to the price-source bug. The bug lives entirely in the workers process (Telegram bot worker, brain v2, watchdogs all consume the Transformer-proxied position_service).

### 9.2 Tool modules (`src/mcp/tools/`)

Eight tool modules: `trading_tools.py` (12 tools — get_account_info, get_ticker, get_tickers, get_klines, get_orderbook, place_order, modify_order, cancel_order, cancel_all_orders, get_open_orders, get_positions, close_position), `news_tools.py`, `sentiment_tools.py`, `altdata_tools.py`, `analysis_tools.py`, `risk_tools.py`, `memory_tools.py`, `system_tools.py`.

### 9.3 Authentication

SSE transport authenticates via `MCPAuth` at `src/mcp/auth.py` reading `settings.mcp.auth_token`. Header: `Authorization: Bearer <token>`. stdio transport has no auth (direct piping to Claude Code CLI).

### 9.4 Consumers

Claude Code CLI (when operator runs `claude` in this directory — talks via stdio through `mcp_stdio_proxy.py`), claude.ai web UI (over SSE on 8080). No internal consumer of MCP — the workers process ignores the MCP server entirely.

---

## 10. Brain (Stage 2)

### 10.1 Brain v2 (`src/brain/brain_v2.py`)

Runs inside the workers process, scheduled by LayerManager every 300s. Uses Claude API (or Claude CLI subprocess) via `ClaudeCodeClient` (`src/brain/claude_code_client.py`) — runs Claude as a subprocess to leverage operator's Max subscription at $0 cost. `claude_client.py` is the legacy Anthropic SDK client.

### 10.2 Strategist (`src/brain/strategist.py`)

`ClaudeStrategist` builds prompts and parses Claude's responses. Two cycle types:
- **A-cycle (`_build_market_prompt`)** — top-level market analysis, returns market_view + risk_level.
- **B-cycle (`_build_position_prompt`)** — per-position review, returns close/hold/adjust-SL decisions.

PROMPT_DEFERRED gate at `:280-298` and `:500-523`:
- `_has_blocking_price_divergence` reads `tf._last_enrichment_max_divergence_pct`. If above `settings.price.divergence_block_prompt_pct` (default 1.0), returns True.
- B-cycle skips the prompt build when the gate fires, logs `PROMPT_DEFERRED | rsn=price_divergence max_div=X% threshold=Y%`.

The gate's intent: don't ask Claude to make decisions when prices look unreliable. Phase 2's fix preserves this gate by keeping `_last_enrichment_max_divergence_pct` updates intact even after the override mutation is removed.

### 10.3 Decision parser

`src/brain/decision_parser.py` extracts JSON from Claude's response. `cost_tracker.py` tracks tokens/cost. Prompts in `src/brain/prompts/` directory.

---

## 11. Layer Architecture

### 11.1 LayerManager (`src/core/layer_manager.py`)

Three-layer state machine: Layer 1 (data), Layer 2 (brain), Layer 3 (execution). Each layer has an active flag persisted to `data/layer_state.json` for boot recovery. Telegram `/stop` command flips Layer 2 + Layer 3 to inactive → cycle-gated workers pause.

Sub-layers within Layer 1 (per memory `project_layer1_restructure`): 1A (always-on data), 1B (analyzers), 1C (strategy pipeline), 1D (smart scanner). The restructure was completed in 2026-04-27 across 9 phases.

### 11.2 APEX (Layer 3 — `src/apex/`)

Trade optimization between strategy signal and order execution.
- `IntelligenceAssembler` (`assembler.py`) — builds 5-section briefing pack for the Qwen optimizer.
- `TradeOptimizer` (`optimizer.py`) — 10-step optimization pipeline.
- `TradeGate` (`gate.py`) — 12 hard safety checks before execution.
- `qwen_client.py` — OpenRouter API wrapper, 30s timeout.

APEX assembler at `apex/assembler.py:147-148` reads `_ws_quotes` directly via `price_worker.get_ws_quote(sym, max_age_s=5.0)` — F-A feed, the freshest WS price. Decision-time prices stay on this feed even after the price-source fix; only the dashboard/portfolio feed changes.

### 11.3 Layer 4 (`src/workers/position_watchdog.py`, ~2700 lines + `profit_sniper.py`)

Position monitoring + close triggers.
- PositionWatchdog: 10s rule-based pass (SL enforcement, duplicates detection, rapid-move alerts, time-decay close, stalled-position detection), 30s Claude-review pass.
- ProfitSniper: 5s mode-4 trailing stops via 5 mathematical models (ring buffer at `sniper_ring_buffer.py`, Hurst exponent, momentum decay, etc.) + anti-greed lock-in.

Self-initiated close paths (the Bug 3 sites):
- `position_watchdog.py:996-1002` — `time_decay_p_win_low` close. Calls `coordinator.on_trade_closed(pnl_usd=pos.unrealized_pnl, ...)` using the Transformer-overwritten value. Phase 1 fix routes through `get_last_close`.
- `profit_sniper.py:2410, 2493, 2664` — `mode4_p9` closes (full close, ladder close, ring-buffer close). Same pattern.

External-detection path (the existing fix Phase 1 mirrors):
- `position_watchdog.py:2569-2578` — when watchdog notices Shadow has already closed the position via SL/TP, fetches `get_last_close(symbol)` and prefers Shadow's `net_pnl_pct` / `net_pnl_usd` over locally-computed values.

### 11.4 Layer 5 — TIAS

Post-trade intelligence analysis. `src/tias/`:
- `collector.py` — captures 7-group context at trade close.
- `repository.py` — `TradeIntelligenceRepository` (CRUD + APEX-ready queries).
- `deepseek_client.py` — DeepSeek V3 async wrapper for post-trade reasoning.
- `analyzer.py` — Phase 2 analysis (legacy).
- `backfill.py` — historic trade backfill.

`trade_intelligence` table accumulates DeepSeek's analysis in `ds_*` columns. Strategy evaluation and lessons-learned feedback both consume this table — so the Bug 3 corruption (wrong `pnl_usd` on time_decay/mode4 closes) propagates into TIAS conclusions.

---

## 12. Strategies and Scanner

### 12.1 StrategyRegistry (`src/strategies/registry.py`)

Singleton registry. `register_all()` at `register_all.py` imports all 41 strategies from `src/strategies/categories/` and registers them.

### 12.2 Categories (41 strategies, in `src/strategies/categories/`)

A: Momentum reversals (RSI, VWAP, BB, EMA). B: Trend following (volume breakout, supertrend, Ichimoku, double-bottom). C: Mean reversion. D: Derivatives (funding fade, OI divergence). E: Sentiment (F&G extreme, news breakout, sentiment momentum). F: Market structure (S/R, multi-TF, liquidation hunt, grid recovery). G: Advanced (stop-hunt sniper, retail fade, whale shadow). H: Quantitative. I: Time-based (kill zone, weekend gap, options expiry, hourly close). J: Cross-asset (BTC dominance, correlation, altseason, beta). K: AI hybrid (Claude conviction, pattern memory, ensemble).

### 12.3 TradeScorer (`src/strategies/scorer.py`)

4-component score: Base (strategy intrinsic), Confluence (other strategies agreeing), Context (regime fit), Quality (volume/spread). Output is a `ScoredSignal`.

### 12.4 EnsembleVoter

Direction consensus across multiple strategies for a single symbol. Tied to `ensemble_votes` table.

### 12.5 ScannerWorker (`src/workers/scanner_worker.py` + `scanner/` subdir)

Layer 1D — picks top-N coins per cycle, builds `CoinPackage` with all relevant context (current_price from F-C, volume, spread, regime, signals, structure). `CoinPackage.current_price` flows through to the strategist's prompt.

`CoinPackageValidator` rejects packages where `current_price` is too stale (5s gate inherited from F-C).

---

## 13. Intelligence Layer

`src/intelligence/`:
- `news/` — Finnhub client + calendar service.
- `altdata/` — F&G index (AlternativeMe), funding rates, OI tracker, on-chain.
- `sentiment/` — `SentimentAggregator` (35% news, 30% Reddit, 20% F&G, 15% momentum). Reads `change_24h_pct` from `ticker_cache` at `aggregator.py:169-175` — this is the only non-Transformer consumer of `ticker_cache`. Per operator constraint, the aggregator stays on `ticker_cache` (no migration to Shadow). Phase 3's proper WS-write fix keeps `ticker_cache` fresh so the aggregator works correctly.
- `signals/` — `SignalGenerator` produces multi-source signals consumed by strategy K1 / K3 / etc.

---

## 14. Telegram Bot

### 14.1 Bot worker (`src/workers/telegram_bot_worker.py`)

Polls Telegram every 60s. Handler dispatch via `python-telegram-bot`. Inline callbacks via callback-query handlers.

### 14.2 Handlers (`src/telegram/handlers/`)

14 handler files: `dashboard_handler.py` (the largest — portfolio + analysis views), `control_handler.py` (start/stop/layer toggles, includes `_show_positions` and `_build_positions_text`), `portfolio.py` (position card, /balance, /pnl, /history), `trading.py` (manual trade), `analysis.py`, `brain.py` (Claude decision history), `system.py`, `alerts.py`, `apex_handler.py`, `tias_handler.py`, `fund.py`, `watchlist.py`, `journal.py`, `schedule.py`, `emergency.py`.

### 14.3 `/positions` flow (the bug-affected path)

`control_handler.py:_show_positions` → `position_service.get_positions()` (this is the Transformer's `_PositionProxy`) → `ShadowPositionService.get_positions()` → HTTP GET `localhost:9090/api/positions` → Shadow returns position list with correct `current_price` and `unrealized_pnl_usd` → Transformer `_PositionProxy.get_positions` calls `_enrich_positions_with_local_prices` which mutates the values → `_build_positions_text` renders the mutated values to Telegram.

Other commands consuming the same path: `/portfolio`, `/pnl` (via `DailyPnLManager` which reads Shadow `/api/balance` through the `_AccountProxy.get_wallet_balance` enrichment), `/performance`, `/history` (reads `trade_intelligence` directly — affected by Bug 3 historical contamination, fixed by Phase 5 backfill).

---

## 15. Logging and Error Handling Conventions

### 15.1 `get_logger(component)` (`src/core/logging.py`)

Loguru-based. Components route to specific log files:
- `worker` → `data/logs/workers.log`
- `mcp` → `data/logs/mcp.log`
- `brain` → `data/logs/brain.log`
- everything else → `data/logs/general.log`

Sinks use `enqueue=True` for thread safety. `_install_shutdown_hooks` in `workers.py` mirrors shutdown logs synchronously to a file because loguru's queue thread can be killed mid-flight by SIGTERM.

### 15.2 Structured tags

Every log line emits a TAG | key=value | … format. Distinct tags include: `WORKER_FIRST_TICK`, `WORKER_TICK_START`, `WORKER_TICK_FAIL`, `WORKER_NEVER_TICKED`, `WORKER_TICK_OVERDUE`, `WORKER_LIVENESS_HEARTBEAT`, `PRICE_WS_HEALTH`, `PRICE_WS_CONN`, `PRICE_WS_DISC`, `PRICE_WS_TICK_FAIL`, `PRICE_SKIP_INVALID`, `PRICE_OVERRIDE` (renamed to `PRICE_DIVERGENCE_OBS` in Phase 2), `PRICE_STALE`, `XFORM_INIT`, `XFORM_SWITCH`, `WD_CLOSE`, `WD_CLOSE_PRICE_FALLBACK`, `WD_PNL_MISMATCH`, `WD_ZERO_EXIT`, `TIME_DECAY_CLOSE`, `TIME_DECAY_CLEANUP`, `SHADOW_ORDER_RECEIVED`, `SHADOW_ORD_SEND`, `SHADOW_ORD_RESP`, `SHADOW_POSITION_CLOSE`, `SHADOW_HTTP_FAIL`, `SHADOW_CALL_FAIL`, `REDUCE_FALLBACK`, `MCP_INIT`, `PROMPT_DEFERRED`, `STRAT_REFRESH_FAIL`, `STRAT_CALL_B`, `STRAT_CALL_B_END`, `SENTINEL_DEADLINE`, `TA_CACHE_STATS`, `DB_LOCK_WAIT`, `WORKER_SIGNAL`, `SENTRY_DEADLINE`.

### 15.3 `ctx()` helper (`src/core/log_context.py`)

Returns a thread-local context string with `tid` (trade ID for traceability), `wid` (worker ID), `cycle` (cycle counter). Every structured log line ends with `| {ctx()}`.

### 15.4 Error handling patterns

- Per-item `try/except`, log with structured tag, continue, never crash the worker.
- Suppressed-but-logged exceptions are tagged `Suppressed: <error> (<context>)` so they're searchable.
- Fail LOUDLY when failure is unexpected — never bare `except Exception: pass`.
- Bug 1's `except RuntimeError: pass` at `price_worker.py:215-220` is a violation of this convention (silent swallow of structurally-impossible call). Phase 3 fixes it.

---

## 16. Configuration

### 16.1 Settings (`src/config/settings.py`, ~3000 lines)

Top-level `Settings` dataclass loaded from `config.toml` via `_load_fresh()`. Subsections: `general`, `bybit`, `database`, `workers`, `universe`, `intelligence`, `signal_generator`, `apex`, `brain`, `risk`, `fund_manager`, `tias`, `telegram`, `mcp`, `price`, `watchdog`, `firewall`, `sentinel`, etc.

### 16.2 `config.toml` (the master config)

Sections include `[general]`, `[bybit]`, `[database]`, `[workers]`, `[universe]` (50-coin watch list), `[intelligence]`, `[signal_generator]`, `[apex]`, `[brain]`, `[risk]`, `[fund_manager]`, `[telegram]`, `[mcp]`, `[price]`, `[watchdog]`, `[firewall]`, `[sentinel]`.

`[price]` block (the one this fix touches):
- `local_max_age_seconds = 10.0` — `_get_local_price` returns None when `ticker_cache` row is older than this.
- `divergence_override_pct = 0.5` — when |local-vs-Shadow| ≤ this percentage, Transformer overrode (Phase 2 makes this observation-only — threshold still controls log emission).
- `divergence_block_prompt_pct = 1.0` — strategist's PROMPT_DEFERRED threshold (must be preserved by Phase 2 fix).

`[exchange]` (in shadow's config.toml, not main's): `slippage_pct = 0.03` — produces the by-design ±0.03% entry-price gap.

---

## 17. Testing

`tests/` uses pytest + pytest-asyncio. Notable patterns:
- AsyncMock for service mocks.
- Fixture-based DatabaseManager setup with temp-file SQLite.
- E2E tests like `test_corrected_layer1_pipeline_e2e.py` that wire the full Layer 1 pipeline with mocked Bybit + mocked Shadow.
- Per-feature test directories: `test_watchdog/`, `test_factory/`, `test_integration/`, `test_audit_fixes_e2e/`, `test_end_to_end_pipeline/`, `test_analysis/`.
- Specific tests for the upcoming fix:
  - `test_order_service_attach_via_transformer.py` — verifies the proxy attachment path.
  - `test_shadow_adapter_boot_grace.py` — verifies the boot-grace retry helper.
  - `test_profit_sniper_partial_cap.py` — verifies sniper partial-close behavior.
  - `test_firewall_and_time_decay.py` — verifies time-decay and firewall integration.

The Phase 2 fix should add `tests/test_transformer_enrichment_observation.py`. The Phase 3 fix should add `tests/test_price_worker_ws_callback.py`.

---

## 18. Build / Deploy

### 18.1 `pyproject.toml`

Python 3.10+ (Layer 1 restructure baseline), setuptools build backend. Pinned dependencies in `requirements.txt`.

### 18.2 `Makefile` targets

`start`, `stop`, `restart`, `logs`, `health`, `test`, `lint` (typical operational shortcuts). Verify exact targets by reading the file.

### 18.3 Service restart commands

The operator runs:
- `sudo systemctl restart trading-workers` — restart the workers process (most fixes need this).
- `sudo systemctl restart trading-mcp-sse` — restart the MCP server (independent of workers).
- `sudo systemctl restart shadow` — restart the Shadow co-project.

### 18.4 Database backups

`scripts/backup.sh` snapshots `trading.db` to `data/backups/trading.db.YYYYMMDD-HHMMSS`. Hourly via `trading-backup.timer`. Existing backups in working tree from prior fixes: `trading.db.bak-pre-dead-workers-fix-20260427-165401`, `trading.db.bak-pre-output-quality-fix-20260427-185043`, `trading.db.pre-layer1-restructure.20260427.bak`, `trading.db.pre-post-layer1-fixes.20260427.bak`.

The Phase 5 backfill will create `trading.db.pre-phase5.bak` before applying.

---

## 19. Price-Source Bug Map (Concise Reference For This Fix)

The three bugs and their exact locations in current code (verified 2026-05-03):

### 19.1 Bug 1 — PriceWorker WS write silently fails

`src/workers/price_worker.py:215-220`. The structurally-impossible asyncio call inside pybit's thread-pool callback. Phase 3 fix: replace with `asyncio.run_coroutine_threadsafe(coro, captured_loop)` + done_callback for loud failure logging.

### 19.2 Bug 2 — Transformer enrichment overwrites Shadow's correct values

`src/core/transformer.py:716-841` (`_enrich_positions_with_local_prices`), `:843-908` (`_enrich_balance_with_local_prices`). Wired through `_PositionProxy.get_positions` (`:982-986`), `_PositionProxy.get_position` (`:988-992`), `_AccountProxy.get_wallet_balance` (`:1039-1044`). Phase 2 fix: rename to `_observe_*_local_divergence`, delete the four mutation lines, preserve `_last_enrichment_max_divergence_pct` byte-for-byte (consumed by strategist gate).

### 19.3 Bug 3 — Self-initiated close paths persist locally-computed P&L

`src/workers/position_watchdog.py:996-1002` (time_decay_p_win_low). `src/workers/profit_sniper.py:2410, 2493, 2664` (mode4_p9 close paths). Phase 1 fix: introduce `_resolve_authoritative_pnl(symbol, fallback_value)` helper in `trade_coordinator.py` that calls `position_service.get_last_close(symbol)` and uses Shadow's `net_pnl_usd` / `net_pnl_pct`, falling back to local computation with `WD_LAST_CLOSE_FALLBACK` warning when Shadow returns None. The existing external-detection path at `position_watchdog.py:2569-2578` is the model — do NOT modify it; extend the same pattern to self-close sites.

### 19.4 Strategist gate dependency (preserve byte-for-byte in Phase 2)

`src/brain/strategist.py:280-298` (`_has_blocking_price_divergence`) and `:500-523` (the PROMPT_DEFERRED block) read `tf._last_enrichment_max_divergence_pct`. Compare against `settings.price.divergence_block_prompt_pct` (default 1.0%). When divergence > threshold, B-cycle defers and emits `PROMPT_DEFERRED | rsn=price_divergence max_div=X% threshold=Y%`.

### 19.5 Architectural insulation

`src/mcp/` has zero imports of Shadow or Transformer (verified by grep). MCP server's `position_service` is the real Bybit service. MCP is structurally peripheral to the price-source bug and requires no changes.

### 19.6 By-design (do NOT touch)

The ±0.03% entry-price gap between `trade_intelligence.entry_price` (main, pre-slippage) and `virtual_positions.entry_price` (Shadow, post-slippage `fill_price`) is exactly Shadow's configured `slippage_pct = 0.03`. Joins between the two tables must use `(symbol, qty)`, never `entry_price`. Phase 5 backfill uses `(symbol, qty)`.

---

## 20. Active In-Flight Work (As Of 2026-05-03)

Project memory recorded:

- Layer 1 restructure (4 sub-layers: 1A always-on data, 1B analyzers, 1C strategy pipeline, 1D smart scanner) shipped 2026-04-27, 9 phases.
- 10 operational issues fixed end-to-end on 2026-04-27 in 12 atomic commits, 80 new tests passing.
- Cold-start gate enforcement shipped 2026-04-29 (CYCLE_RESUME_WAIT skips cycle_gated ticks until M5 boundary).
- ScannerWorker async-without-await fix shipped 2026-04-29 (commit eabe687).
- ShadowKlineReader async-aiosqlite + D-3 fix shipped 2026-04-26 (commits c9503bf / e5089ee / 518f3b6).

Current branch is `main`, 26 commits ahead of `origin/main` (operator commits locally). Working tree has runtime-state modifications to `data/layer_state.json` and `data/trading.db` that should NOT enter any phase commit — use targeted `git add <specific-file>` per phase.

Untracked at session start: `STAGE2_LAYER3_FORENSIC_BUNDLE_2026-05-02.md`, several `.bak` files, `dev_notes/IMPLEMENT_PRICE_SOURCE_DIVERGENCE_FIX_PROFESSIONAL.md` (an earlier draft prompt — supersedes only where INDEPTH agrees), `dev_notes/forensic_data_*` directories.
