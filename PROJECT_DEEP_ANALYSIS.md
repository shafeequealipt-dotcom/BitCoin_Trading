# PROJECT DEEP ANALYSIS — Trading Intelligence MCP

> The definitive "how it works" record for the Trading Intelligence MCP crypto trading bot.
> Source: full structured analysis of every subsystem plus end-to-end flow traces, cross-checked against live code.
> Generated 2026-06-13. Schema version 40. Default runtime mode `bybit_demo`. Brain CLI model `claude-opus-4-7`.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Process & Deployment Model](#2-process--deployment-model)
3. [End-to-End Trading Pipeline](#3-end-to-end-trading-pipeline)
4. [Trading Modes & Order Routing](#4-trading-modes--order-routing)
5. [Risk & Capital Controls](#5-risk--capital-controls)
6. [The AI Brain (Claude CLI + TIAS/DeepSeek)](#6-the-ai-brain-claude-cli--tiasdeepseek)
7. [Subsystem Reference](#7-subsystem-reference)
   - 7.1 [Entry / Process Model](#71-entry--process-model)
   - 7.2 [Config System](#72-config-system)
   - 7.3 [Core Transformer Routing](#73-core-transformer-routing)
   - 7.4 [Core Infrastructure](#74-core-infrastructure)
   - 7.5 [Workers Orchestration](#75-workers-orchestration)
   - 7.6 [Workers — Data Collectors](#76-workers--data-collectors)
   - 7.7 [Workers — Scanner & Signals](#77-workers--scanner--signals)
   - 7.8 [Brain Decision Engine](#78-brain-decision-engine)
   - 7.9 [Strategies (4-Layer Engine)](#79-strategies-4-layer-engine)
   - 7.10 [Strategies — Implementations](#710-strategies--implementations)
   - 7.11 [Analysis — Indicators](#711-analysis--indicators)
   - 7.12 [Analysis — Structure (X-RAY)](#712-analysis--structure-x-ray)
   - 7.13 [APEX — Sizing & Gating](#713-apex--sizing--gating)
   - 7.14 [Risk Management](#714-risk-management)
   - 7.15 [Fund Manager](#715-fund-manager)
   - 7.16 [Trading Services](#716-trading-services)
   - 7.17 [Execution Adapters](#717-execution-adapters)
   - 7.18 [Intelligence](#718-intelligence)
   - 7.19 [TIAS](#719-tias)
   - 7.20 [Database](#720-database)
   - 7.21 [Database — Schema & Migrations](#721-database--schema--migrations)
   - 7.22 [MCP Server](#722-mcp-server)
   - 7.23 [Telegram](#723-telegram)
   - 7.24 [Factory & Support](#724-factory--support)
   - 7.25 [Factory Lifecycle](#725-factory-lifecycle)
   - 7.26 [Shadow Sub-App](#726-shadow-sub-app)
   - 7.27 [Docs Reconciliation](#727-docs-reconciliation)
8. [Data & Persistence Model](#8-data--persistence-model)
9. [Configuration (config.toml + .env)](#9-configuration-configtoml--env)
10. [Operating Notes & Risks](#10-operating-notes--risks)

---

## 1. Executive Summary

The **Trading Intelligence MCP** is an autonomous crypto-futures trading bot. It continuously ingests live market data (price, klines, funding, open interest, fear & greed, news), runs a four-layer quantitative strategy engine (40+ self-registering strategies → scoring → ensemble voting → consensus), and then hands a curated briefing of the most interesting coins to **Claude** — running as a local `claude -p` CLI subprocess on an OAuth subscription (cost $0, not the billed API) — which acts as the actual decision-maker. Claude picks 2–5 genuine plays per cycle, a post-decision **APEX** optimizer (DeepSeek via OpenRouter) refines sizing/SL/TP, a 14-check safety **TradeGate** clamps everything to safe bounds, and the order is routed through a **Transformer** state machine to one of three interchangeable execution backends: a local paper exchange (**Shadow**), Bybit's demo venue (**bybit_demo**), or live Bybit.

Once open, a **PositionWatchdog** polls every 10 seconds and runs an exit ladder (deadline engine, trailing stops, hard stops, time-decay loss cutting, profit-taking), while a **ProfitSniper** owns an ATR Chandelier trailing stop for winners. Every closed trade triggers a **TIAS** post-trade autopsy (also DeepSeek) that writes a one-line lesson back into the brain's prompt, closing a learning loop.

The system runs as **four independent OS process families** under systemd on a Linux VM: **Shadow** (its own repo, paper exchange + data warehouse on port 9090), **workers** (the production heart — ~40 services and ~30 background workers, with the brain living *inside* it), an **MCP SSE server** (port 8080, exposing 43 trading tools to external Claude clients), and a daily **backup** timer. Everything logs to files (never stdout, because the MCP protocol owns stdio). Configuration is one giant typed `Settings` tree loaded from a 3,200-line `config.toml` plus secrets in `.env`. Persistence is a WAL-mode SQLite database (~70 tables, schema v40) behind a pooled async engine with protected-table guards.

**The single most important caveat:** the brain is a Linux-only subprocess (`os.setsid`, `fcntl`, `/usr/bin/claude`, `~/.claude/.credentials.json`), so on Windows the AI decision path **cannot run** — no trades are ever generated locally. The default runtime mode is also read from the database (`transformer_state`), not `config.toml`, defaulting to `shadow` on a fresh DB.

---

## 2. Process & Deployment Model

The system is deliberately decomposed into **four independent OS process families** under systemd so that one crash loop cannot take down the others. All units run as user `inshadaliqbal786`, `WorkingDirectory=.../trading-intelligence-mcp`, `EnvironmentFile=.env`, with `ProtectSystem=strict` + `ProtectHome=read-only` and `ReadWritePaths` carve-outs for `data/`, `~/.claude`, and `~/.cache/claude-cli-nodejs`.

### 2.1 The four process families

**1. Shadow (`shadow.py` → `shadow.service`)** is a wholly separate program living in its own repo/venv (`/home/inshadaliqbal786/shadow`). `main()` does an ordered 16-step boot: load `shadow/config.toml`, set up logging, open `data/shadow.db` (WAL), run migrations, `initialize_wallet(starting_balance=10000)`, `CoinSelector.select_top_coins(100)`, then spins up a WebSocket manager plus five collectors (kline / ticker / funding / OI / position-monitor), a virtual `OrderEngine`/`VirtualWallet`, an **aiohttp HTTP API on 127.0.0.1:9090**, and a Telegram bot. It is ordered FIRST; the main workers unit declares `After=` and `Wants=shadow.service` specifically to kill the boot-time burst of "Cannot connect to 127.0.0.1:9090" errors. Restart 5s, 200M cap.

**2. Workers (`workers.py` → `trading-workers.service`)** is the production heart. `workers.py` is thin: `Settings._load_fresh()` → `setup_logging()` → `_install_shutdown_hooks()` → build `DatabaseManager` + `WorkerManager` → `initialize()` / `start_all()`. The shutdown hooks (Phase 30 / Y-29) are the most interesting logic: an `atexit` handler plus explicit `SIGTERM`/`SIGINT` handlers that **synchronously `os.write(2,...)` AND append to `workers.log`** a `WORKER_SHUTDOWN`/`WORKER_SIGNAL` CRITICAL line — bypassing loguru's `enqueue=True` background queue which a SIGTERM could kill before flush (the fix for an unexplained 2h56m silent outage). **Brain v2 lives inside this process.** `MemoryMax=12G` (for the Claude CLI Node subprocesses), `Restart=always`, `RestartSec=15`.

**3. Brain v2 / Claude CLI subprocesses.** The brain does NOT call an API via SDK; `src/brain/claude_code_client.py` spawns the local `claude -p --output-format text --model claude-opus-4-7 --strict-mcp-config` CLI as a subprocess (with a prewarm pool to hide ~1–5s spawn latency). `_build_env()` is critical: it **pops `ANTHROPIC_API_KEY`** so the CLI uses OAuth credentials (`~/.claude/.credentials.json`) instead of the billed API, and force-injects HOME/PATH/LANG. `--strict-mcp-config` with no `--mcp-config` means brain calls load **zero** MCP servers — preventing each brain call from booting a second full app instance (the 2026-06-12 outage). The separate `trading-brain.service` runs the DEPRECATED Brain v1 (`brain.py`) and is operationally disabled.

**4. MCP SSE server (`server.py` → `trading-mcp-sse.service`)** runs `server.py --transport sse --port 8080`: a Starlette/uvicorn app with `/sse`, `/messages/`, `/health` routes, gated by `MCPAuth` bearer-token validation (`MCP_AUTH_TOKEN`). It is consumed by **external** Claude clients (Claude Code CLI / claude.ai), not the bot's own brain. When an external Claude spawns the stdio MCP server, `mcp_stdio_proxy.py` runs instead of a full stdio server: it forwards stdio JSON-RPC to the already-running SSE server, so heavy state lives once. 200M cap, `RestartSec=10`.

A **`trading-backup.timer`** (`Persistent=true`) fires `scripts/backup.sh` daily at 02:00 — an atomic `sqlite3 .backup` + config tar.gz with 7-archive retention.

### 2.2 Critical environment variables

The env vars are load-bearing:
- **`PATH`** includes the venv and `~/.local/bin` so the `claude` CLI is found.
- **`HOME`** lets the CLI locate `~/.claude/.credentials.json`.
- **`CLAUDE_CONFIG_DIR`** relocates ALL CLI state into a service-owned writable dir (`data/claude-config` for workers, `data/claude-config-mcp` for the SSE server — **must differ** so two processes don't share state), because under `ProtectHome=read-only` the CLI's default `~/.claude.json` is EROFS and silently wedges. Its `.credentials.json` is symlinked back to `~/.claude` to keep one OAuth lineage.
- **`DISABLE_UPDATES=1`** blocks `claude update`/`install` self-flips (both 06-09 and 06-10 outages began at unattended binary flips), complementing `.env`'s `DISABLE_AUTOUPDATER`.
- **`LANG=C.UTF-8`** for subprocess I/O.

### 2.3 Tooling

`scripts/setup.sh` bootstraps a fresh host (venv, deps, .env from example, mkdir data/logs/backups, migrations, validate_config). `install_services.sh` copies units, writes logrotate (daily, rotate 7, copytruncate, 50M), enables + starts with a 5s gap after workers. `start_all.sh` notably does NOT start `trading-brain` (Brain v2 runs in workers). `status.sh` renders a dashboard from `systemctl is-active`, `ps rss`, and `sqlite3` row counts. The `Makefile` maps `make start/stop/status/logs/health/monitor/backup/install/test` onto these scripts.

### 2.4 Local-Windows vs VM differences (CRITICAL)

| Concern | VM (production) | Windows (local) |
|---|---|---|
| Brain (`claude -p` CLI) | `/usr/bin/claude`, `os.setsid`, `os.killpg`, `fcntl`, `~/.claude/.credentials.json` | **BREAKS** — Unix-only paths/syscalls; `import fcntl` → `ModuleNotFoundError`. No trades generated. |
| Brain env | `HOME`, `:`-PATH, `C.UTF-8` | `USERPROFILE`, `;`-PATH — env malformed even if a binary existed |
| systemd / `systemctl restart` | exists; restart-based exchange switch works | **absent** — `subprocess.Popen(['systemctl',...])` raises `FileNotFoundError` (caught, returns failure) |
| Shadow service (:9090) | own systemd unit, started first | must be launched manually or paper-exchange calls fail |
| MCP SSE (:8080) | systemd unit | must be launched manually |
| Mode source | DB `transformer_state` row (set by ops) | fresh DB → defaults to `shadow` regardless of config.toml |
| SQLite file locking | Linux fcntl semantics | Windows locking differs; batch writes contend more |
| `start_new_session=True` | detaches child to survive systemd kill | no equivalent guarantee |

---

## 3. End-to-End Trading Pipeline

The full life of a trade spans four traced flows: decision → entry → monitoring → exit. The driver is `LayerManager` (`src/core/layer_manager.py`), NOT the brain package — it alternates a `_call_type` between **"A" (find new trades)** and **"B" (manage open positions)**.

### 3.1 Data ingestion → strategy funnel (Layers 1–3)

1. **`KlineWorker.tick`** (`src/workers/kline_worker.py`) calls `market_service.get_klines` and persists M5/H1/H4/D1 candles via the market repository.
2. **`StrategyWorker.tick`** (`src/workers/strategy_worker.py`) is the Layer 1–4 driver on a sweet-spot schedule. It checks the daily PnL gate (`pnl_manager.can_trade`), reads `settings.universe.watch_list`, reads cached per-coin regime, prefetches all klines in one batch (`market_repo.get_klines_batch`), runs `ta_engine.analyze` per coin, then:
   - **Layer 1:** `strategy.scan` per coin/regime → `RawSignal`s.
   - **Layer 2:** `scorer.score_batch` → `ScoredSetup`s (0–105 score + grade), writes `layer_manager._scorer_components`.
   - **Layer 3:** `ensemble.vote_batch` → `EnsembleResult` (STRONG/GOOD/WEAK/LEAN/CONFLICT consensus + opposing votes), persisted to `ensemble_votes`.
   - Writes per-symbol scores to `_score_cache`.
3. **`ScannerWorker.tick`** fires LAST in the 5-min window (sweet spot 4:00), computes per-coin `opportunity_score`, assembles a self-contained **`CoinPackage`** per coin (XRAY/strategies/signals/altdata/price blocks + state label + interestingness), and writes them into `layer_manager._coin_packages`.

### 3.2 The brain decision (Call A)

`LayerManager._run_brain_cycle` (call_type "A") → `ClaudeStrategist.create_trade_plan` (`src/brain/strategist.py`):
- **Short-circuits** with `STRAT_CALL_A_SKIPPED` if `layer_manager.get_coin_packages()` is empty (`brain.use_packages=True`).
- `_build_trade_prompt` renders the top-N coin packages (ranked via `reserve_slots_union`, default 6) with regime, consensus, signals, funding, F&G, X-RAY structure, and a "LESSONS FROM RECENT TRADES" block (fed by TIAS).
- `claude.send_message(prompt, system)` → **`ClaudeCodeClient`** spawns `claude -p`.
- `extract_json` → `_parse_trade_plan` → `StrategicPlan.new_trades` (list of trade dicts). The plan is returned, NOT executed here.

### 3.3 Optimization, gating, sizing, execution

Back in `LayerManager`, if Layer 3 is active and `new_trades` is non-empty, `_execute_trades_background` → `_execute_new_trades`:
1. Stamps `_claude_original_size_usd` and conviction signals from the CoinPackage.
2. **APEX optimize** (`src/apex/optimizer.py`) — assembles intelligence, calls DeepSeek via OpenRouter to refine SL/TP/size/leverage/direction. **Never blocks**: any failure falls back to Claude's params.
3. `_apply_apex_optimization` converts APEX percentage SL/TP to absolute prices.
4. **`TradeGate.validate`** (`src/apex/gate.py`) runs CHECK 0–14: size caps, leverage cap, concurrency, conviction-weighted capital ceiling (reads `fund_manager` + `tiered_capital`), reentry cooldown, breadth brake. Hard blocks set `trade['_gate_rejected']` → `LayerManager` skips with `TRADE_SKIP rsn=gate_rejected`.
5. **`StrategyWorker._execute_claude_trade`** — enforcer leverage clamp + survival/X-RAY gates, size derivation (treated as margin, capped by venue/usable pool), `fund_manager` equity read, `market_service.get_ticker` for current price, `qty = size_usd*leverage/price` floored to exchange qty step (min/max/min-notional conformance), volatility-scaled stop via `SLTPValidator`.
6. `order_svc.place_order(purpose="layer3_entry", layer_snapshot=...)` → this is the Transformer's **`_OrderProxy`**, which blocks during mode-switching, runs the bybit_demo safety guards, then delegates to `active_order_service.place_order`.
7. The active adapter fills and persists (`save_order`/`save_position` → `BYBIT_DEMO_PERSIST_OK`).
8. `trade_coordinator.register_trade`/`register_trade_plan` → `record_strategy_trade` DB row → `alert_manager.send_custom` Telegram alert.

Rejected orders (`OrderStatus.REJECTED`) short-circuit before coordinator/DB/Telegram.

### 3.4 Monitoring & exit (PositionWatchdog)

`PositionWatchdog.tick` runs every **10s** (`watchdog.check_interval_seconds`). Each tick:
1. **`_determine_mode()`** → passive (default), safety_net (Claude offline >10min / 3+ CLI failures / 5+ consecutive losses), or emergency (session PnL < threshold or hard-stops/hour ≥ threshold → close ALL).
2. **`get_positions_with_confirmation()`** — if NOT confirmed (transport error), preserve state and return (phantom-close avoidance).
3. `_reconcile_with_shadow_fast` + `_detect_and_record_closes` BEFORE the monitoring loop, so externally-closed positions (Shadow SL/TP, sniper) are recorded via `coordinator.on_trade_closed` first.
4. Per position (under a 3s `asyncio.wait_for`), after `is_immune`/`get_maturity` gates, `_monitor_position` runs the **exit ladder in order**:
   - CHECK1: plan expired → SENTINEL `DeadlineEngine.evaluate` (tighten / force-close / ride winner).
   - CHECK2/3: percentage trailing (activate/update/should_trail_exit) — **disabled** when `profit_fetching` subordinates the watchdog trail.
   - Hard **-3% stop** (unconditional, increments `hard_stops_this_hour`).
   - **Time-decay loser lane** (`_handle_time_decay`) — Bayesian p_win tighten/force-close.
   - Timeout close / +1.5% profit-take.
   - Smart trailing: lock 50% of peak / breakeven.
   - Passive-mode brain trigger → `urgent_queue` or `_ask_brain` (POSITION_REVIEW_PROMPT).

SL writes funnel through `_push_sl_to_shadow` → `SLGateway.apply` (tighten-only, min-distance, rate-limit) → `position_service.set_stop_loss`. **`ProfitSniper`** owns the ATR Chandelier trail (`_compute_trail_stop`) and competes via `_pf_select_stop` (highest-stop-wins). Closes go through `position_service.close_position`. Every self-initiated close calls `coordinator.resolve_authoritative_pnl` first (queries `get_last_close` for post-fee/post-slippage net PnL in shadow/bybit_demo modes, else `local_fallback`), then `risk_manager.on_trade_closed` + `coordinator.on_trade_closed` (which fans out to thesis manager, TIAS, learning, fund manager).

---

## 4. Trading Modes & Order Routing

Mode is a single string — `"shadow" | "bybit_demo" | "bybit"` — owned by the SQLite `transformer_state` table (singleton row `id=1`). Constants live in `src/core/modes.py`. **`config.toml [general] mode` is VALIDATED only** (`validators.py:_validate_mode`); it does NOT seed the DB. On a clean boot the row is absent, so `Transformer.__init__` defaults `_current_mode = MODE_SHADOW` and `initialize()` inserts a default `shadow` row. **Runtime routing is driven by the DB, not config.toml.**

### 4.1 The Transformer

`Transformer` (`src/core/transformer.py`) holds three service dicts (`_shadow_services`, `_bybit_services`, `_bybit_demo_services`), each `{order, position, account}`. `_services_for_mode(mode)` is the single 3-way dispatch; `_apply_mode()` points `_active_services` at the right set. `create_proxies()` returns `_OrderProxy`/`_PositionProxy`/`_AccountProxy` used by all workers — each reads `self._t.active_*_service` per-call, so a mode flip instantly redirects every order/position/account call.

Three behavioral branches:
- **`_OrderProxy.place_order`** returns a REJECTED sentinel while `is_switching`. When `current_mode == "bybit_demo"` it runs six `order_guards` gates (Layer-3, mandatory SL, leverage cap, position-size + max-loss, post-place SL verify) that the live `OrderService` enforces internally but the demo adapter would otherwise bypass. The Shadow path is intentionally ungated (permissive paper).
- **`_PositionProxy.close_position`** reserves the symbol in `Transformer._closing_inflight` *before any await* so two concurrent cutters can't double-close (`ClosingInProgressError`).
- **Shadow-only** local-vs-Shadow price divergence is observed (never mutated) for the strategist's PROMPT_DEFERRED gate.

### 4.2 Two switching mechanisms

- **Live Bybit → hot-swap** `Transformer.switch_to()`: validates target in `ADAPTER_MODES`, **requires `confirmed=True` for `MODE_BYBIT`** ("Real money at risk"), probes reachability, closes all positions on the current set, flips `_current_mode`, `_apply_mode`, persists, records `switch_history`, fires `_on_switch_callbacks` (one re-derives prompt mode via `TradingModeManager.refresh`).
- **Restart-based** `ExchangeSwitcher.execute_switch_with_restart()` for `RESTART_SWITCHABLE_MODES = (shadow, bybit_demo)` only (live excluded): close all positions → `persist_target_mode` (writes DB WITHOUT in-memory `_apply_mode`) → write `data/post_switch_sentinel.json` → `systemctl restart trading-workers trading-mcp-sse`. Next boot reads the new mode; `verify_post_switch` probes the new adapter and sends a Telegram confirmation.

Crash-recovery: if `is_switching` was set at boot, `initialize()` checks the old set for open positions; completes the switch only if zero remain, else cancels — records `startup_recovery`.

### 4.3 The live-trade safety gate

The order boundary is mode-dependent. In **bybit_demo** mode, the six `order_guards` run in `_OrderProxy`. In **live/bybit** mode, the parallel checks live inside `OrderService.place_order` (`_validate_stop_loss`, `_validate_leverage`, FIX2 position-size + 2% max-loss cap). The **Shadow path bypasses ALL order-boundary caps by design**.

Cross-process: the MCP server is a different process, so `MCPTransformerAdapter` reads `transformer_state` directly (5s cache) and routes its own per-mode service dict — fixing the pre-P9 bug where MCP tools silently hit the live cluster.

---

## 5. Risk & Capital Controls

There are **TWO risk subsystems**, and the most important finding is which one is live.

### 5.1 The dormant dedicated risk engine

The `src/risk` package (`RiskManager`, `TradeValidator`, `PositionSizer`, `DrawdownTracker`) is a complete pre-trade gate:
- `TradeValidator.validate_order` — mandatory SL, SL sanity (0.1%–20%), leverage cap, `max_position_size_pct`, `max_open_positions`, `max_total_exposure_pct`, available-balance/margin, duplicates. Hard ceilings config cannot exceed: `ABSOLUTE_MAX_LEVERAGE=10`, `ABSOLUTE_MAX_DAILY_LOSS_PCT=10`, `ABSOLUTE_MAX_POSITION_PCT=25`.
- `DrawdownTracker.check_circuit_breakers` — daily loss limit, max-drawdown (=2× daily limit), 5-consecutive-loss halt, `loss_cooldown_seconds`.

**But `RiskManager.validate_trade` is only called from `src/brain/brain_v2.py` (legacy).** The live `ClaudeStrategist → LayerManager` path NEVER calls it. **So on the live path there is NO daily-loss limit, NO max-drawdown circuit breaker, NO consecutive-loss halt, NO 80% exposure cap, and the `PositionSizer` is unused.** The `config.toml [risk]` values for those are effectively decorative for live trading. A second, harder gate at boot (`validators.py:_validate_risk`) still raises `ConfigError` if `mandatory_stop_loss` is false — it literally cannot be disabled.

### 5.2 The live enforcement points (in order)

1. **APEX TradeGate** (`src/apex/gate.py`) — 14 checks that mostly **CLAMP, not block**. CHECK 0 caps size at 1.5× Claude's original; CHECK 1 caps margin at `max_position_size_usd`/usable pool; CHECK 2 caps leverage; CHECK 3 aligns concurrency to tiered `max_positions`; **CHECK 4** is the capital authority (conviction-weighted ceiling from TIAS profit-factor + per-cycle margin reservation accumulator). The few **genuine blocks** set `_gate_rejected`: zero-conviction, structureless-high-score, reentry-cooldown (5-min per symbol+direction), and the OFF-by-default portfolio-directional-drawdown breaker.
2. **`StrategyWorker._execute_claude_trade`** — re-clamps leverage to 5 and size to a venue/usable cap; `SLTPValidator` (min SL distance, max 10% SKIP, headspace auto-fix, sl==tp/wrong-side SKIP); volatility stop scaling (widen + size haircut); qty conform (skips `qty_unconformable`/`qty_zero`).
3. **Order boundary** — mode-dependent (see §4.3). Mandatory SL is enforced in THREE places: `SLTPValidator` geometry, `order_guards.check_mandatory_sl_for_bybit_demo`, `OrderService._validate_stop_loss`. Per-trade max-loss = **2% of equity, hardcoded** (independent of `daily_loss_limit_pct`).

### 5.3 Capital budgeting

Single-sourced from `TieredCapitalManager.get_limits(equity, deployed)`: tier = 20/30/40% of equity by growth multiple (hysteresis bands), `usable_capital`, `max_positions` (4/6/8), `available_for_trades = usable - deployed`. APEX CHECK 1/3/4 and the strategy_worker venue cap all read this same `FundLimits`. The parallel 22-module `IntelligentFundManager.get_sizing_decision` exists but the LIVE path uses brain-proposed size + TieredCapitalManager + APEX gate. Under `brain_authoritative_sizing_enabled`, `size_usd` IS margin and is capped at `usable/max_positions`. **Many safety gates fail OPEN** (swallow exceptions and allow the trade) — risky on local runs where services may be unwired.

---

## 6. The AI Brain (Claude CLI + TIAS/DeepSeek)

### 6.1 Who drives the cycle

The production driver is `core/layer_manager.py`, toggling `_call_type` "A"/"B". **Call A** (`create_trade_plan`) finds NEW trades; **Call B** (`create_position_plan`) manages OPEN positions. Each cycle wraps the strategist call in try/finally so exactly one `BRAIN_CYCLE_X_DONE` pairs every START (catches `BaseException`/`CancelledError`). Call B is skipped entirely when there are no open positions. Plans merge field-by-field into a persistent `_current_plan`, executed only when Layer 3 is active, under a single-background-task lock.

### 6.2 ClaudeCodeClient — the CLI engine

This replaces API billing with `claude -p` over the existing Max OAuth subscription ($0). At init it resolves the binary (`_find_claude`: native installer first, validates the symlink TARGET is runnable), builds a hermetic env (`_build_env`: **pops `ANTHROPIC_API_KEY`** so OAuth wins; forces HOME/PATH/LANG), and assembles `_extra_cli_flags` — always `--strict-mcp-config` (no second MCP boot), plus a model pin (`--model claude-opus-4-7` by default, to avoid the slow CLI-default `opus-4-8[1m]` breaching the 300s deadline).

`send_message` flow: (1) auth-backoff gate (hot-reload credentials on `claude login` mtime change, else raise); (2) usage-quota gate; (3) rate-limit sleep on `_adaptive_interval`; (4) `_ensure_credentials_fresh` pre-flight (refresh if TTL inside `credential_refresh_margin_seconds`=600s, raise `CredentialRefreshError` if refresh fails, aborting before spawning a doomed subprocess); (5) a retry loop that cleans orphaned `claude.*-p` processes and calls `_execute_cli` in a thread executor.

`_subprocess_call` runs a canary, tries `_proc_pool.acquire(system_prompt)` only if `reuse_enabled()` (pool on AND canary healthy), else cold-spawns. The prompt is written to stdin then closed (EOF); `replenish_async` pre-spawns the next worker. `_stream_subprocess_io` polls non-blocking pipes every 50ms with a **first-byte deadline** (90s) and total `timeout` (300s), plus graduated stall buckets (60/120/240s) that capture `/proc/<pid>/{status,wchan}` and TCP socket state. A 3-layer auth recovery handles OAuth token refresh → credential hot-reload → exponential backoff (5/10/20/40/60 min) + one Telegram alert. The `_ClaudeWorkerPool` keeps at most ONE primed worker per system-prompt SHA-256 prefix, gated by a periodic out-of-band canary that self-disables the pool if the CLI re-introduces a parked-worker hang.

### 6.3 TIAS / DeepSeek autopsy loop

TIAS (Trade Intelligence Autopsy System) is a two-phase fire-and-forget pipeline that NEVER blocks trading. On trade close, `TradeCoordinator` fires `_tias_close_callback`, which synchronously snapshots ProfitSniper state then schedules a task:
- **Phase 1 (collector.py):** gathers Groups A–E + APEX context (outcome, thesis, per-coin regime, M5 TA indicators, sniper snapshot) into a `TradeIntelligence` row.
- **Phase 2 (analyzer.py):** builds the autopsy prompt + DeepSeek system prompt (with the 18-category taxonomy), calls `deepseek/deepseek-chat-v3-0324` via OpenRouter (primary→fallback only on retryable 429/503/timeout), maps the JSON to `ds_*` columns, writes via `update_analysis`.
- **Lesson bridge:** `compose_lesson_from_tias` distils one line into `trade_thesis.lesson`, which the strategist's Call-A "LESSONS FROM RECENT TRADES" block later surfaces — closing the loop from autopsy back into brain context.
- A 30-min backfill loop retries rows where `ds_why IS NULL` and `analysis_attempts < 3`.

APEX itself is the other DeepSeek consumer (post-decision optimizer); both share the OpenRouter `OPENROUTER_API_KEY`.

---

## 7. Subsystem Reference

This is the file-by-file record. Each subsection incorporates the full detailed write-up for that subsystem.

### 7.1 Entry / Process Model

**One-liner:** The boot/process layer — thin Python entry-point scripts (`server.py`, `workers.py`, `brain.py`, `shadow.py`, `mcp_stdio_proxy.py`) wrapped by five systemd units that run the bot as a fleet of long-lived OS processes, plus shell/Make tooling.

The system runs as four independent OS process families under systemd, deliberately decoupled so one crash loop cannot take down the others (see §2 for the full process topology and env-var detail). `WorkerManager.initialize()` (~3500 lines) is the real bootstrap: it connects+migrates the DB, then wires ~40 services into `self._services` — Bybit client/WS, intelligence (news/reddit/fear-greed/funding/OI), TA engine+cache, X-RAY StructureEngine (opens a read-only connection to Shadow's DB at `../shadow/data/shadow.db` via `ShadowKlineReader`), a `Transformer` routing orders to Shadow/Bybit/bybit_demo adapters, `ClaudeCodeClient`, `AlertManager`, `RiskManager`, `TradeCoordinator`, `LayerManager`, etc. `start_all()` registers asyncio SIGTERM/SIGINT handlers, launches every worker as an isolated `_run_worker` task (one crash → `WM_CRASH`, others survive), starts a 60s `_system_health_loop`, then **auto-starts the three layers DATA→BRAIN→EXECUTION** with 2s gaps (respecting a persisted `user_stopped` flag). This is where Brain v2 actually lives — there is no separate brain process in production.

**Key files:** `server.py`, `workers.py`, `brain.py` (deprecated v1), `shadow.py`, `mcp_stdio_proxy.py`, the five systemd units, `scripts/*.sh`, `Makefile`, `src/workers/manager.py`, `src/brain/claude_code_client.py`.

### 7.2 Config System

**One-liner:** Loads `config.toml` + `.env` into a deeply-nested, typed `Settings` dataclass tree (singleton), with secrets from env and fail-fast validation.

The whole system reads ONE typed object: `Settings`, a `@dataclass` whose ~45 fields are each a sub-config dataclass (`GeneralSettings`, `BybitSettings`, `BrainSettings`, `RiskSettings`, `APEXSettings`, `TIASSettings`, `StructureSettings`, …), built with `field(default_factory=...)` so a bare `Settings()` is fully valid. Entry is `Settings.load(...)` (cached singleton); `_load_fresh()` is the un-cached path used by tests and the real boot in `workers.py`.

`_load_fresh` order: (1) `load_dotenv(env_path, override=True)` — **`.env` wins** over pre-existing process env; (2) `config.toml` read with `tomllib` (missing file tolerated → all defaults); (3) ~45 `_build_<section>()` calls; (4) `Settings(...)`. Two builder styles: **explicit-arg** (`data.get(key, default)`; secrets via `_env("ENV_VAR", default)` so precedence is **env > toml > default**) and **`**dict`-filtering** (`Cls(**{k: data[k] for k in data if hasattr(Cls, k)})`). `_build_structure` switched from `hasattr` to `fields()` membership because `default_factory` fields set no class attribute (operator edits were silently ignored).

**Validation — two layers.** Layer 1 fail-fast in `__post_init__` (e.g. `DatabaseSettings` rejects `single_lock`; `SweetSpotsSettings` enforces strict chain ordering kline 0:30 < structure 0:45 < signal 1:00 < regime 1:15 < strategy 1:30 < scanner 4:00; `UniverseSettings` enforces watch_list ≥10 + the `^[A-Z0-9]+USDT$` regex + builds the lowercase `extraction_map`; `Stage2Settings` caps `top_n_to_brain` at 15; `VolatilityStopScalingSettings` enforces `0 < reference_stop_pct <= max_cap_pct`). Layer 2 `validate_config(settings)` (called once in `workers.py`): `_validate_mode`, `_validate_risk` (hard-fails if `mandatory_stop_loss` is False or leverage outside 1–100), `_validate_api_keys` (warns), `_validate_paths` (`makedirs`, fatal on OSError), `_validate_mcp`, `_validate_consistency`. `src/config` is imported by ~99 modules. The dynamic `SUPPORTED_SYMBOLS` registry is the one mutable shared piece — the scanner calls `.update()` each cycle, always preserving BTC/ETH.

### 7.3 Core Transformer Routing

**One-liner:** Execution-routing backbone — the Transformer state machine proxies all order/position/account calls to one of three adapter slots, persists the active mode in SQLite, and supports in-memory hot-swap and restart-based switching.

(Full detail in §4.) `Transformer.initialize` reads the singleton `transformer_state` row; if absent, INSERTs a `shadow` default. If `is_switching` was true at boot, it detected an interrupted switch and conservatively cancels (stays on old mode) unless zero positions remain. After applying mode it runs a health probe only for the active adapter (`_check_shadow_health` / `_check_bybit_demo_health`). `switch_to` validates target/confirmed/reachability, closes positions one-by-one (any failure aborts and reverts), captures shadow+bybit equity snapshots, flips mode, persists, records, fires callbacks. `set_switching_state`/`persist_target_mode`/`record_switch` are public so `ExchangeSwitcher` writes through the same surface; `persist_target_mode` deliberately does NOT call `_apply_mode`. Config read: `price.local_max_age_seconds` (10s staleness gate in `_get_local_price`), `price.divergence_override_pct` (0.5), `risk.max_leverage` (5). `_AccountProxy.get_wallet_balance` now writes `account_snapshots` for BOTH shadow and bybit_demo (HIGH-1 fix), tagging the `exchange_mode` column. `MCPTransformerAdapter` gives the MCP process a 5s-cached read-only view.

`modes.py`: `Final[str]` strings (not an enum, to keep DB/TOML/Python identical). `ADAPTER_MODES=(shadow,bybit,bybit_demo)`; `RESTART_SWITCHABLE_MODES=(shadow,bybit_demo)`; `ALL_VALID_MODES` adds legacy `paper`/`live`. `trading_mode.py` is a separate concern (prompt framing, not routing): `_derive_mode_from_state` resolves Transformer.is_shadow→SHADOW, else testnet→TESTNET, else MAINNET, and `get_claude_mode_instruction` returns mode-specific header text (SHADOW = opportunity-exploit, TESTNET = "prices are synthetic", MAINNET = "real capital, max caution").

### 7.4 Core Infrastructure

**One-liner:** Cross-cutting foundation — loguru file-only logging with component routing, the full exception hierarchy, shared enums/dataclasses, async retry/rate-limit/timed decorators, correlation-ID log context, and health probes. Imported by ~500 files.

**Logging** (`logging.py`): the defining constraint is that the MCP server speaks stdio, so ANY stdout/stderr write corrupts the protocol. `setup_logging()` calls `logger.remove()` first (deleting loguru's stderr sink) then adds only file sinks. Routing is data-driven: `COMPONENT_ROUTING` maps ~60 component names to four physical files (`mcp.log`, `workers.log`, `brain.log`, `general.log`); one sink per unique file via `_grouped_file_filter`; a catch-all `_default_filter` lands unrouted output in `general.log`. Sinks: ms timestamps, rotation 10MB, retention 7 days, `enqueue=True`, `backtrace=True`, `diagnose=False`. A CI test enforces that every `get_logger` component is in the routing table.

**Exceptions** (`exceptions.py`): all inherit `TradingMCPError` (message/details/UTC-timestamp, renders `[iso] ClassName: msg | details={}`). Typed sentinels encode control flow: `GroundTruthUnavailableError` (distinguish confirmed-zero from unknown — prevents phantom closes), `DuplicateOrderLinkIdError` (idempotent retry success), `Layer3DisabledError`/`Layer3RaceError`/`Layer3BootNotReadyError`, `ClosingInProgressError` (deliberately NOT retried).

**Types** (`types.py`): str-Enums + `SerializableMixin` dataclasses (OHLCV, Ticker, Order, Position, Signal, TradeRecord, AccountInfo, BrainDecision, WatchdogDecision). `to_dict`/`from_dict` walk fields handling datetime/Enum/Optional/UnionType/list/dict recursively. Frozen `PositionsQueryResult`/`BalanceQueryResult` carry a `confirmed` discriminator + reason so adapters preserve last-known state on API failure instead of treating empty as zero.

**Decorators** (`decorators.py`): `retry()` (async/sync exponential backoff, default 3 attempts/1.0s/2.0×; logs 'Retry exhausted' at WARNING), `rate_limit()` (per-qualname `_TokenBucket` with an asyncio.Lock), `timed()`, `validate_input()`. ~256 applications, concentrated in trading services and intelligence clients.

**Utils** (`utils.py`): `round_price` (nearest tick) vs `round_qty`/`quantize_qty_floor` (floor; the Decimal variant avoids float-drift that would round qty UP past pos.size and trip Bybit reduceOnly rejects), `format_price` magnitude ladder (>10→2dp, >1→4, >0.01→6, else 8). **log_context.py**: four ContextVar IDs (did/tid/wid/sid) + `tid_scope` (token-restore prevents stale-tid leakage). **log_tags.py** centralizes structured tag strings. **Health**: `SystemHealthMonitor.check()` (loop lag via `await asyncio.sleep(0)`, >100ms WARN / >500ms enumerates blockers; task count; RSS/CPU via lazy psutil; 60s loop), `WorkerLivenessTracker` (cycle-gate-aware `is_alive()` classifying HEALTHY/NEVER_TICKED/OVERDUE/IDLE_CYCLE_GATE), `FreshnessGuard`, `cache_freshness`, `PriceFormatter`, `ServiceContainer`.

### 7.5 Workers Orchestration

**One-liner:** `WorkerManager` is the bot's central composition root — instantiates and dependency-injects ~40 services and ~30 background workers, wires TradeCoordinator close-callbacks and Transformer switch-callbacks, then runs all workers as crash-isolated asyncio tasks under a sweet-spot/cycle-gated scheduling model.

`__init__` creates a `WorkerHealthMonitor`, a `SystemHealthMonitor`, a `WorkerLivenessTracker` (module singleton via `set_default_tracker` so BaseWorker/scheduler record into the same instance), and an empty `self._services` dict that becomes the de-facto DI container (looked up by `.get()` with None-tolerance everywhere). `initialize()` builds services in strict, comment-documented dependency order, each in its own try/except: DB connect+migrations → `transformer` (default SHADOW) → Bybit client/ws/market → intelligence stack (news, calendar, reddit [gated on `reddit.client_id`], fear_greed, funding, OI, onchain, aggregator, signal_gen) → TA wrapped in TACache(120s) → VolatilityProfiler → X-RAY StructureEngine/Cache/ShadowKlineReader. The pivotal T3 step constructs THREE parallel exchange service sets (live Bybit, Shadow adapters, optional Bybit-demo gated on `bybit_demo.enabled`+creds) → `transformer.set_services` → re-init → `create_proxies()` stores `position`/`order`/`account` proxies under canonical keys; downstream code only ever sees the proxies. Then brain (ClaudeCodeClient driven by ~20 `brain.*` knobs), price_formatter, AlertManager, RiskManager, FreshnessGuard, the central `TradeCoordinator` (heavily late-wired: `attach_transformer`, `attach_position_service`, `set_reentry_cooldown_seconds`, `set_close_exit_divergence_pct`, `recover_state_from_db`), SLGateway, ThesisManager, EnsembleStateCache, SLTPValidator, DataLakeWriter, EventBuffer, UrgentQueue, TradingModeManager, TieredCapitalManager ($168k fallback), ClaudeStrategist, RuleEngine (logged INACTIVE), LayerManager.

`_create_workers()` instantiates workers conditionally on service availability + settings flags (PriceWorker+TickerCacheBuffer, optional BybitDemoWSWorker, KlineWorker, News/Reddit/AltData/Signal, PositionWatchdog [~18 deps], Layer4ProtectionService, ProfitSniper, the strategy engine [MarketScanner, ScannerWorker, RegimeDetector+RegimeWorker, EnsembleVoter, StrategyWorker], StructureWorker, factory workers, portfolio services, interactive Telegram bot + price-alert + scheduled-report, EnforcerWorker, IntelligentFundManager+FundManagerWorker, reconcilers, CleanupWorker, WorkerLivenessWatchdog last). It late-binds LayerManager/CycleTracker onto cycle-gated/tier-tagged workers. `_wire_coordinator_callbacks()` registers ~15 close-callbacks (enforcer, fund_manager, strategy-perf, pnl_manager [+correction channel for win→loss flips], thesis [+reconcile], data_lake, bybit_demo trade_history, positions-cleanup, ghost-state invalidation, learning log, TIAS autopsy) — each spawned via `create_task` with `add_done_callback` surfacing `CLOSE_CB_FAIL`; it also builds APEX, SENTINEL, and registers transformer switch-callbacks. `start_all()` installs POSIX signal handlers, spawns one crash-isolated `_run_worker` task per worker (WM_START/STOP/CRASH), auto-starts layers DATA→BRAIN→EXECUTION (respecting `user_stopped`), awaits `FIRST_COMPLETED`. `BaseWorker.start()` is the per-worker engine: cycle gate (brain AND execution both on) + cold-start boundary gate, slow-tick markers, liveness recording, exponential backoff (`restart_delay*2^n` capped 60s) up to `max_consecutive_failures` before `WorkerCrashError`. `SweetSpotWorker` overrides only the sleep. `stop_all()` awaits each `stop()` with a 10s timeout, then disconnects bybit/ws/shadow_reader/claude warm-pool/db.

### 7.6 Workers — Data Collectors

**One-liner:** The Layer-1A background workers (price, kline, altdata, news, reddit) plus scheduled-report worker that continuously poll/stream external data and persist to SQLite + in-memory caches.

All set `worker_tier = LAYER1A`, `cycle_gated=False` — they ALWAYS run to keep data warm. **PriceWorker** (45s tick) is a connection-health loop, not a fetch: if the watch_list set changed it forces a reconnect (pybit has no unsubscribe). Data arrives on `_handle_ticker_update` (pybit thread), skips invalid (≤0) prices, updates `_ws_quotes[symbol]=(price, monotonic_ts)`, routes persistence to `TickerCacheBuffer.put` (or legacy `run_coroutine_threadsafe`). `get_ws_quote(symbol, max_age_s=5.0)` feeds `apex/assembler.py`. **TickerCacheBuffer** decouples ~100–200 WS msgs/sec from DB writes (latest-wins under a lock, optionally rejecting anomalous jumps >`price.spike_reject_pct`, 500ms drainer → `save_tickers_batch` executemany, ≤2 writes/sec). **KlineWorker** (sweet spot 0:30) loops 50 symbols × M5/H1/H4/D1 (cooldowns 60/60/300/3600s), classifies fetch quality (`total==0`→CRITICAL + 30s circuit breaker read by strategy_worker; <50%→ERROR; <90%→WARNING), tracks per-symbol consecutive failures (KLINE_STRAGGLER after 3), runs a chunked staleness scan (KLINE_WRITE_LAG 360s / KLINE_FRESHNESS_WARN 600s) + scheduled `PRAGMA wal_checkpoint` every N ticks (PASSIVE escalating to TRUNCATE). **AltDataWorker** (sweet spot 1:45) wakes once per 5-min window but each source has its own cadence via monotonic deadlines re-anchored to `t0` (avoids the historical 300s→600s OI drift bug): funding every tick, OI 300s, F&G 3600s, on-chain piggybacks funding; runs concurrently under `gather`; fills `_funding_cache`/`_oi_cache` exposed via `get_funding`/`get_oi`. **NewsWorker** (300s) → Finnhub + every-30-ticks economic calendar. **RedditWorker** (600s). **ScheduledReportWorker** (300s, short-circuits on `has_active()`). Scheduling math (`sweet_spot_scheduler.py`) anchors windows to wall-clock so a slow tick never compounds.

### 7.7 Workers — Scanner & Signals

**One-liner:** ScannerWorker reads the 7 data-worker warm caches once per 5-min cycle, scores/labels every watch-list coin, and emits the top-N CoinPackages that feed the brain; SignalWorker pre-computes per-coin directional Signals it consumes.

**ScannerWorker** (LAYER1D, cycle_gated, fires sweet spot 4:00 — the cycle trigger, NOT a data worker). `__init__` is mostly boot self-checks (regime-haircut mode, funding-boundary parity, labeller liveness); every accessor defensively returns None/0 when a service is missing. `_compute_opportunity_score` normalizes 6 components to [0,1] weighted by `[scanner.scoring_weights]` (structure 0.27, strategy 0.27, signal 0.13, regime 0.13, funding 0.10, rr 0.10): `struct_norm = (setup_score/100) * clamp(setup_type_confidence,[0.5,1.0])` so counter-setups (~0.35 conf) can't out-rank in-direction setups; funding saturates at 0.001; RR is direction-aware saturating at 3.0; regime maps trending→1/volatile→0.5/ranging-unknown→0/dead→-1 rescaled. `tick()` branches on `settings.scanner.mode` (default "briefing"). **Exclusion mode** runs `_qualifies` (5-criterion short-circuit: XRAY setup_type != none, ensemble in {STRONG,GOOD}, regime align with UNKNOWN ALLOWED, direction-aware RR ≥ `min_rr_ratio` 1.1, `_check_blockers`) — survivors + forced open positions ranked, top `max_selection` 15. **Briefing mode** (production) never excludes: scores EVERY coin, sorts by `interestingness_score`, `reserve_slots_union` draws alternately from top-by-opportunity and top-by-interestingness, pads to `min_briefing_packages` 12, force-includes open positions; F9 loss-cooldown can hold cooled-down symbols out.

`_build_package` assembles a `CoinPackage` from cached reads with getattr/get guards (missing data → `blockers_observed` note, never a crash): XrayBlock (setup_type/score/confidence/trade_direction/SL/TP/RR/MTF/session), StrategiesBlock (consensus/vote_count/scoring-regime), SignalsBlock (direction+confidence), AltDataBlock (funding, OI 24h delta, prefetched F&G), PriceDataBlock; derives ranker inputs (confidence/adx/choppiness/volume_ratio, position_in_range, MTF biases, OI delta — E9/E8 fixes that replaced hardcoded zeros), then `label_state()` → StateLabelBlock and `compute_interestingness()`. **state_labeler** (Phase 3, pure/never-raising): ~20 trigger predicates; regime mismatch applies a `counter_regime_confidence_haircut` (0.5); `_FUNDING_EXTREME_DECIMAL=0.001`; element-3 guard suppresses range-fade when range_breakout is non-empty (DYDX fix); ADVISORY labels surface but aren't candidates. **interestingness** (Phase 4) = weighted sum (weights sum 1.0) of cleanness/confluence/extremity/label_strength/structural_quality/mtf_alignment/open_position_floor. **SignalWorker** (LAYER1B, sweet spot 1:00) caches a `Signal` per coin via `SignalGenerator.generate_signal`; sentiment fully severed. Packages land in `layer_manager._coin_packages` + the `active_universe` table; the brain re-ranks to `stage2.top_n_to_brain` (6) and renders labels + ACTION_HINTS + interestingness into the Call-A prompt. `MarketScanner.scan_market()` (Bybit REST volume/volatility scorer + hysteresis) is now only the boot-time universe seeder.

### 7.8 Brain Decision Engine

(Full detail in §6.) **One-liner:** The AI decision engine that builds market/position prompts, spawns the Claude Code CLI as a $0 OAuth-backed subprocess with a pre-spawn pool + canary, streams/parses the JSON response, and drives the alternating Call-A/Call-B cycle.

`ClaudeStrategist.__init__` takes `(claude_client, services, settings)` and emits a wall of boot sentinels (STRAT_TRADE_PROMPT_VERSION etc.) so operators can grep which prompt version is in memory. `create_trade_plan` (Call A) pre-checks `brain.use_packages` and skips with STRAT_CALL_A_SKIPPED if packages are empty, selects TRADE_SYSTEM_PROMPT (or the zero-two variant when `stage2.enable_zero_two_contract`), runs `_resolve_prompt_calibration` to substitute `__DEAD_THIN_VOL_RATIO__`/`__HEAVY_ATTEMPTS_COUNT__` tokens (NOT `str.format` — templates contain literal JSON braces), then sends/extracts/parses and marks thesis events consumed. `create_position_plan` (Call B) defers with PROMPT_DEFERRED if `_has_blocking_price_divergence()` (drift > `price.divergence_block_prompt_pct` 1.0%). The TRADE_SYSTEM_PROMPT is a long "aggressive exploitation / quality-over-quota" instruction: per-coin regime is the direction authority (no global bias), F&G neutral-on-direction, returns 2–5 best genuine plays (fewer when the tape is dead/thin), strict JSON including `thesis_invalidation` (price_close_above/below | signal | none). `_parse_*` use `_safe_float`/`_safe_int` to tolerate nulls; Call B downgrades `tighten_stop` without `new_sl` (and `set_exit` without `exit_price`) to `hold`. `BrainV2.evaluate_setups` is the parallel legacy 4-layer single-call path (uses SETUP_REVIEW prompt, sizes via FundManager, executes via OrderService). `DecisionParser` extracts JSON (direct→fence→braces→array) for the single-decision/watchdog path. The CLI path is free (`ClaudeCodeCostTracker` always affordable); the Sonnet-priced `CostTracker` only governs the deprecated API `ClaudeClient`.

### 7.9 Strategies (4-Layer Engine)

**One-liner:** The 4-layer decision engine — 40 self-registering strategies are regime-filtered, scored 0–105, then put to a weighted ensemble vote whose consensus label and honest two-sided opposition tally are surfaced to Claude as truthful context — never a hard gate.

`StrategyRegistry` is an in-memory dict + parallel `StrategyPerformance` map. `register_all.py` instantiates all 40 (A1–K4), force-disables `E3_sentiment_momentum`/`G2_retail_fade` (sentiment severed 2026-06-10), registers `X1_always_trade` only on testnet. Each strategy subclasses `BaseStrategy` (ABC requiring name/category/applicable_regimes/timeframe + async `scan()` + sync `vote()`). Gating is driven by `category`: `get_active_for_regime(regime)` filters enabled strategies to categories in `REGIME_ACTIVE_CATEGORIES[regime]` (momentum/scalping in trending, mean_reversion/funding_arb in ranging, narrow funding_arb+microstructure in DEAD, broad union for UNKNOWN; kickstart in every regime so X1 always votes).

**RegimeDetector** pulls 200 H1 klines, classifies by ADX/DI/choppiness/ATR-percentile (trending_adx 20, dead_adx 12, volatile_atr_percentile 70): structure tested BEFORE the VOLATILE magnitude test (Phase 0a); missing data → explicit UNKNOWN (not a fabricated RANGING/0.30); the (ADX,choppiness) plane is fully tiled; per-symbol hysteresis (count 2). `breadth_sizing()` shrinks size (floor 0.40) when the per-coin regime distribution is lopsided (systemic correlation risk); never sets direction or roster.

**TradeScorer** produces 0–105 from base (30 + condition bonuses, cap 40) + confluence (0–25) + context (0–20: higher-TF agreement, F&G contrarian, funding, +2 category-in-regime) + quality (0–20). Quality uses `_xray_sr_score` when X-RAY data exists — a rich 0–8 sub-score (entry quality, RR, BOS/CHoCH, FVG/OB, SMC, POC, Fib, MTF, session) × `setup_type_confidence` (counter-setups floored at 0.5). Grade cutoffs config-centralized (A+≥80/A≥68/B≥56/C≥45); optional quality-floor cap. **EnsembleVoter** polls every active non-originator strategy, weighting `ensemble_weight × confidence (× regime factor)`, applies a `single_strategy_max_share=0.4` dominance cap, runs a SECOND opposite-direction poll (`ensemble_two_sided_vote=true`) for honest `opposing_votes`, classifies via config thresholds (STRONG agree≥4.0/opp≤1.5, GOOD ≥2.5/≤2.5, …) auto-corrected at boot. A regime-weighted shadow consensus is always logged; when `regime_weighting_enabled` it replaces the live values. **Crucially the ensemble is NOT a gate** (`passed=True` always; `size_multiplier` inert for sizing — only a display sort key). The `EnsembleResult` flows into the coin package; `strategist._format_consensus_context` renders it as truthful framing + `_opposition_tier`. `EnsembleStateCache` write-through lets PositionWatchdog read live consensus for flip detection. `StrategyWeightDeriver.refresh()` derives per-(strategy,regime) factors from `ensemble_votes JOIN trade_intelligence`, `clamp(1+sensitivity*avg_pnl, 0.3, 3.0)`, EMA-smoothed, cold-start-frozen at 1.0 until 20 trades. `PerformanceEnforcer`/`DailyPnLManager` apply orthogonal PnL-based leverage/size throttling.

### 7.10 Strategies — Implementations

**One-liner:** The ~43 concrete strategies (A1..K4, X1) plus the scorer, ensemble voter, regime weighter, and smart-leverage sizer.

Every strategy implements `scan(symbol, candles, ticker, ta_data, sentiment_data, altdata)` (reads ONLY pre-computed indicators — never makes API calls — returns a `RawSignal` when ALL entry conditions hold, else None) and `vote(symbol, direction, ...)` (direction-conditional confirmer returning the asked direction or NEUTRAL but never opposing — this asymmetry is why the ensemble adds a two-sided poll). Examples: **A1 RSI reversal** (RSI<25 + price≤BB lower + vol≥1.5 + stoch cross, not in ADX>30 downtrend; SL/TP tight pct), **B2 supertrend** (dir==1, price>SMA50, MACD>0, ADX≥25, RSI 50-70; SL = supertrend value, TP = price+2×ATR), **D1 funding fade** (contrarian short when funding>0.0004 + RSI>70 + F&G>70 + 24h change>5%), **E1 fear-greed** (buy extreme fear, F&G≤15, RSI<35, first green candle), **G1 stop-hunt** (wick pierces >0.2% beyond support, closes back above), **H4 order-flow** (3 consecutive same-color candles, accelerating volume, top/bottom 20% of range), **I1 kill-zone** (fixed UTC session windows, first 30 min), **J1 BTC-dominance**, **K1 conviction** (fires only on `altdata.k1_trigger` score>80 + STRONG consensus). K3/K4 are placeholders (always NEUTRAL 0.0); X1 always-trade is testnet-only. `conditions_strength` per-condition 0–1 feeds the base score. **SmartLeverage.calculate()** starts at max_leverage (5) and only ever clamps DOWN: confidence (<0.55→2x, <0.65→3x, <0.9→4x), coin tier, volatility percentile (>150→2x, >120→3x), regime (VOLATILE→4, DEAD→3), then +1 boost on STRONG consensus AND confidence>0.65, floored at 1.

### 7.11 Analysis — Indicators

**One-liner:** Pure-numpy technical-analysis layer — a classic-indicator/pattern TAEngine and a separate "X-RAY" structural engine that together produce signal scores and the structural context block fed to the strategist.

All indicator functions are stateless numpy operators returning same-length arrays with leading NaNs. Wilder smoothing for RSI/ATR/ADX (`avg=(avg*(p-1)+x)/p`); EMA seeded with an SMA. MACD = EMA(12)-EMA(26), signal = EMA(MACD,9). Supertrend builds ATR bands with trailing-band logic. `TAEngine._compute_overall_signal` is the scoring heart: adds/subtracts fixed weights (RSI oversold/overbought ±1.0, MACD histogram positive-and-rising ±1.0 else ±0.3, price vs SMA50/EMA20 ±0.5, Supertrend ±1.0, Bollinger position <0.2/>0.8 ±0.5, volume confirmation, candlestick ±0.3, chart patterns ±0.5), amplifies by 1.2 when ADX>25 / dampens 0.8 when ADX<20, normalizes score/5 clamped [-1,1], maps >0.5 STRONG_BUY / >0.2 BUY / <-0.5 STRONG_SELL / <-0.2 SELL / else NEUTRAL. Confidence = dominant/total indicator count, EMA-smoothed per-symbol (`ta.confidence_ema_alpha`=0.4) to stop flapping; both `confidence` and `confidence_raw` returned. `volume_ratio_use_closed_candle` drops the still-forming last bucket. `CandlestickDetector` (16 single/two/three-candle patterns, body/shadow ratio constants). `ChartPatternDetector` (double top/bottom, H&S, inverse H&S, triangles via order-N local extrema + geometric constraints). Cached via `TACache` (sym:tf, TTL). `VolatilityProfiler`/`vol_scale` provide per-coin ATR volatility-class profiles and pure scaling helpers. (X-RAY detail in §7.12.)

### 7.12 Analysis — Structure (X-RAY)

**One-liner:** The X-RAY structure engine runs a 10-phase numpy SMC pipeline per coin, fuses them into a 0–100 setup_score and a categorical setup_type + confidence, and feeds them into trade gating where `xray_authority_min_score=45` decides whether structure can override an opposing strategy ensemble.

**Phase orchestration:** each phase is independently try/excepted (`phases_ok` counter); `atr_pct_h1` (14-bar NATR) is computed up front so nearest-zone windows are volatility-scaled. Phases: S/R → market structure (HH/HL/LH/LL → uptrend/downtrend/ranging + BOS/CHoCH → suggested_direction) → structural SL/TP (computes BOTH directions, picks by trend/RR, clamps TP to a `tp_min_distance_pct` floor setting `is_structurally_invalid`, optional with-trend ATR continuation TP) → FVG (3-candle gap, fill tracking) → order blocks (last opposing candle before displacement, 0–100 strength) → liquidity (equal-high/low + round-number zones, sweep+reclaim) → volume profile (POC, value area) → fibonacci → MTF confluence (9 factors, 0–10).

**`_setup_score`** starts at base 50 with ADDITIVE modifiers (entry-position +25/+15, range_no_room_penalty -25, structure alignment +20/-15, **RR graded on the CHOSEN direction's rr** ≥3:+20/≥2:+10/<1:-40, BOS +10, CHoCH -15, SMC/VP/Fib/MTF bonuses), then the critical de-saturation: `score = 50 + setup_score_modifier_scale(0.5)*(score-50)` (compresses modifiers around 50 so coins spread across grades). AFTER scaling come UNSCALED hard caps: no placement→cap B; chosen-dir rr<0.5→SKIP(≤30); rr<1.0→C; rr<1.5→B; smc<10 AND mtf<3→cap B. Grades A+≥80/A≥65/B≥50/C≥35.

**`classify_setup`** is a top-down first-match tree returning (SetupType, confidence): BULLISH/BEARISH_FVG_OB (fresh in-direction FVG+OB + alignment + mtf ≥ fvg_ob_min_confluence 0.5) → FVG_OB_COUNTER (opposite zones, ×counter_mult 0.7) → STRUCTURAL_BREAK (BOS, ×0.8 for minor) → LIQUIDITY_SWEEP → RANGE_BREAKOUT/BREAKDOWN; no match → (NONE, 0.0). A **coherence gate** caps a NONE-typed setup to C and a sub-0.30-confidence matched setup to B — the producer-level guarantee that structureless coins can't present as A+. **`_compute_smc_confluence`** is GRADED, not binary (each component continuous in [0,weight]: FVG 25, OB 30, liquidity 15, sweep 30, summed to 100 — replaced a flat lump that pinned ~81% of coins at 70). The **support filter is load-bearing**: supports strictly below / resistances strictly above price, symmetric `min_touches=2`/`min_touches_resistance=2` (fixing a direction-bias bug that collapsed rr_long in downtrends); range-position reads the UNFILTERED swings.

**Live gating:** `xray_authority_min_score=45` mirrors the scorer's C/SKIP cutoff. `_xray_authority_weak(pkg, 45)` flags a read weak if setup_type contains 'counter' OR 0<setup_score<45 — when X-RAY disagrees with the ensemble and is weak, the disagreement note tells Claude NOT to treat structure as authoritative (the HBAR/HYPE wrong-side fix). `ShadowKlineReader` opens `shadow.db` read-only (aiosqlite) and aggregates 1-min candles into the requested timeframe.

### 7.13 APEX — Sizing & Gating

**One-liner:** APEX is the post-brain trade-optimization + safety layer — `TradeOptimizer` asks DeepSeek to refine SL/TP/size/leverage/direction (with code-enforced kill-switches), then `TradeGate` runs ~14 never-blocking-but-adjusting checks.

**TradeOptimizer.optimize()** — translates the brain dict, then `IntelligenceAssembler.assemble()` builds a 5-section package (directive, coin TA/Mode4/orderbook, TIAS symbol history, TIAS situation/regime, X-RAY structural). A three-tier data gate decides whether to call DeepSeek (Tier 1 `symbol_trades ≥ min_tias_trades`, Tier 2 `regime_trades ≥ 10` with a regime summary, Tier 3 → `_fallback()`); $0 price forces fallback. **APEX never blocks**: every failure returns `OptimizedTrade(is_fallback=True)` preserving Claude's params (with a T2-2 enhancement substituting a volatility-aware pct SL/TP when Claude's would exceed the validator cap). **Direction control** is layered: `_check_direction_lock()` computes a composite score (regime alignment, log(rr) structural, X-RAY direction, global WR, symbol flip-evidence WR) weighted by `apex_lock_*_weight`; below `apex_lock_score_threshold` (0.0) the brain direction is LOCKED. Kill-switches revert DeepSeek flips: `apex_dir_flip_enabled` (default OFF → all flips reverted), `apex_leverage_override_enabled` (default OFF → brain leverage stands), the lock override, counter-trade protection (`_counter` suffix), insufficient-data gate (`apex_min_trades_for_flip` 8, venue-isolated), `_enforce_flip_confidence` (asymmetric floors Buy→Sell 0.95 / Sell→Buy 0.70 + RR-weighted boost). `_apply_flip_resize_policy` caps any upsize on an accepted flip.

**Sizing authority** lives in `_apply_constraints`. Default `apex_size_override_enabled=False`: the brain's original size is restored verbatim (APEX_SIZING_AUTHORITATIVE — APEX cannot inflate; only the gate caps downstream). When ON, the J5 path: effective cap = max(`max_position_size_usd`, equity×`apex_size_cap_pct_of_equity`/100) from the late-bound account getter, scaled by `max(conviction_floor, confidence)`, floored at the brain's size if `brain_authoritative_sizing_enabled` (APEX optimizes but never SHRINKS). Leverage clamps [1, max_leverage]; SL/TP get per-class floors (0.6× recommended) + a class TP ceiling (`tp_cap_multiplier_by_class` dead 1.4 … extreme 2.0, bounded by `apex_tp_cap_hard_ceiling_pct` 5%). A boot self-check loud-errors if the three TP-cap maps diverge.

**TradeGate** ADJUSTS but mostly does not block. CHECK 0 caps size at `gate_apex_size_cap_mult`(1.5)×original; CHECK 1 caps to `max_position_size_usd` (or whole `tiered_capital.usable_capital` under brain-authoritative); CHECK 2 leverage; CHECK 3 concurrency + long/short skew + optional portfolio-DD breaker (skew≥0.80, dir open-loss ≤ -1.5% equity → reject) + reduce to 30% at the cap; **CHECK 4** the capital authority (reject paths zero-conviction + structureless-high-score; then `_get_conviction_weight()` maps TIAS profit-factor regime-filtered, 5-min cache, min 3 trades → 0.5–2.0×, modulated by setup score [A+ ≥80 → ×`gate_a_plus_size_mult`], X-RAY confidence, RR, clamped [0.5,2.5]; capital ceiling = brain-authoritative per-trade MARGIN = usable/max_positions minus an in-cycle reservation accumulator keyed on `_cycle_did`, OR legacy `available × clamp(0.4×weight,...)`); CHECK 4b breadth brake from `regime_detector.breadth_sizing()`; CHECK 5 halve on same-symbol position; CHECK 6 reentry cooldown (5-min per symbol+direction); CHECK 7 preserve small probes; CHECKS 8–12 APEX guardrails (TP floor, trail-activation floor 15% of TP, trail-distance floor 40%, mode override, confidence-based size scaling below 0.50); CHECK 13 reduce 75%/50% on zero/low RR; CHECK 14 fix identical TP/SL. All adjustments accumulate into `_gate_adjustments` for TIAS.

### 7.14 Risk Management

(Full detail in §5.) **One-liner:** Capital-protection layer — a central `RiskManager` orchestrates pre-trade validation, sizing, SL/TP, drawdown/circuit-breaker tracking and portfolio exposure, plus a Layer-4 in-trade exit-protection stack.

`RiskManager.validate_trade()` runs circuit breakers FIRST (daily realized loss ≥ `daily_loss_limit_pct`, drawdown ≥ 2× daily limit, `consecutive_losses ≥ 5` hardcoded, or active loss cooldown), then pulls account/positions/instrument and calls `TradeValidator.validate_order()` (13 checks). `PositionSizer` runs fixed_percentage/atr_based/kelly (quarter-Kelly) and returns the most conservative; `StopLossCalculator.recommend()` picks the tightest SL ≥0.5% and forces R:R ≥ 1.5. **But this entry path is wired only into legacy `brain_v2.py`** (see §5.1).

The Layer-4 exit stack is the most consequential live part. **`time_decay_sl.py`** is a stateless 5-model loser-lane engine: the combined budget is multiplicative (`atr_room(2×ATR) × time_factor(convex 1-(age/max)^1.5) × recovery_mult × momentum_mult × probability_mult`), floored at `min_allowed_loss_pct=0.15` and tighter-only. Model 5's `p_win` starts at a regime-weighted prior and is Bayesian-updated each tick (clamp [0.05,0.95]). Force-close (`return -1.0`) fires when `p_win < p_win_force_close 0.15` but is wrapped in a strict guard stack: grace window (per vol class) → `min_age_seconds=300` (suppresses BOTH force-close and tighten) → monotonic-grind → `mae_to_sl_ratio_threshold=0.5` → **structural-invalidation gate** (`structural_invalidation_required=true` — even a dead p_win won't close unless the caller passes `structural_invalidation=True`: XRAY confidence drop ≥0.40, setup drift, or regime inversion ≥0.60). Carve-outs yield for near-certain losers (`p_win ≤ near_certain_loser_p_win 0.10`). Fail-safe philosophy: "missing data ⇒ BLOCK the close." **`layer4_protection.py`** is the shared gate every close path consults (`is_protected()` — min-hold 300s unless allow-listed reason, profit/development guard, structural check via the verbatim-relocated `compute_structural_invalidation`), caching STRUCT_GUARD verdicts (60s TTL) so the ProfitSniper defers to the watchdog's structural view. **`wd_brain_scoring.py`** scores brain-initiated closes across 8 weighted factors against 6.0 → execute/reject/reject_and_tighten (advisory until `wd_brain_scoring_enforce`). `RiskManager` is consumed by `brain_v2` + `position_watchdog` (`on_trade_closed`); the Layer-4 trio by `position_watchdog` + `profit_sniper`.

### 7.15 Fund Manager

**One-liner:** A 22-module capital-management brain (`IntelligentFundManager`) plus a parallel `TieredCapitalManager` that decide whether to trade, how much margin to commit, which pool to draw from, and at what leverage — anchored to Bybit's authoritative wallet/margin state.

`update_state()` (every 60s) is the heart of balance tracking. The H3 fix (2026-05-16) is load-bearing: `state.in_use` is set DIRECTLY from `account.used_margin` (Bybit's `totalInitialMargin` = notional/leverage), NOT the old naive `sum(size*entry_price)` which over-counted leveraged notional and starved available. Priority: `bybit_wallet > leverage-aware position-derived fallback (sum(notional/max(1,leverage))) > previous value` (never silently 0); naive notional stashed in `in_use_notional`; emits FUND_INUSE_TRANSITION/RECONCILE/POOLS. `trading_capital = total_equity * unlock_pct/100`; `available = max(0, trading_capital - in_use)`. Read failures escalate WARNING→ERROR after 3 consecutive.

**Progressive allocation** (M1, `capital_allocator.py`): `LEVEL_CONFIG` ROOKIE/PROVEN/VETERAN/ELITE/MASTER → unlock 20/30/40/50/60%, max_leverage 3/4/5/5/5, max_positions 3/5/7/10/10, growth thresholds 1.0/1.5/2.0/3.0/5.0×. Demotion on 15% drawdown (→ROOKIE) / 3 losing days / 10% drop from level-up. **The LIVE path** is `tiered_capital.py` + `apex/gate.py`: tiers <2×=20%, 2–4×=30%, >4×=40% of equity (`usable_capital`, floor MIN_USABLE 25), 5% hysteresis bands (promote 2.05/4.10, demote 1.95/3.90), `max_single_trade=25%` of usable. Under `brain_authoritative_sizing`, `size_usd` IS margin → CHECK 4 caps each trade at `usable/max_positions` with a per-cycle reservation accumulator (because `fund_manager.in_use` is stale within a 60s cycle), CHECK 1 backstops at the whole usable pool, CHECK 3 aligns concurrency. The 22-module `get_sizing_decision()` is the alternate path: 4 gates (NUCLEAR weather/recovery/liquidity/available≤0) → pool routing (M3) → base_pct (M2 grade×level table) → 11 multipliers (streak, daily-pnl, volatility, consensus, correlation, time-of-day, weather, contrarian emotion, momentum, velocity, anti-fragile override) → cascade of mins (pool, level max_trade_pct, time-horizon pool, strategy budget, sector rotation, recovery, 2%-of-capital max-loss) → leverage → opportunity-cost EV shave. `FundReconciler` independently re-reads the wallet and alerts on drift. Profit ratchet (M14) locks 50% of new equity highs + 25% of trade profits into a rising floor.

### 7.16 Trading Services

**One-liner:** The Bybit trading domain layer — a REST client plus five services (Order, Position, Account, Market, Instrument) that wrap exchange calls, enforce layered safety gates, and translate raw Bybit JSON into typed dataclasses.

`BybitClient.call(method, **kwargs)` is the single chokepoint, decorated `@retry(3) @rate_limit(10/s) @timed`, looks up the pybit method by name, runs it in `asyncio.to_thread`, `_handle_response` returns `result` on `retCode==0` else raises from `BYBIT_ERROR_MAP` (10003/10004→Auth, 10006→RateLimit, 110012/110043→InsufficientBalance, 110072→DuplicateOrderLinkIdError; 10001 deliberately NOT mapped to InsufficientBalance — a corrected bug). A constructor assertion raises if `testnet=False` while `mode=='paper'`.

**OrderService.place_order** — the heart: (1) validate `purpose` against the closed set `_VALID_PURPOSES` (ValueError on typos); (2) generate one idempotent `orderLinkId` (`ti-<24 hex>`) BEFORE any logging/RPC, emit ORDER_ATTEMPT; (3) if `purpose` is gated (`layer3_entry`/`telegram_manual`/`mcp_tool`) run `_enforce_layer3_gate` (four rejection paths: LM-unattached past `lm_attach_deadline_sec` 60s → fail-close ALL; LM-unattached during boot + gated → `Layer3BootNotReadyError`; layer_snapshot L3 disagrees with live LM → `Layer3RaceError`; live L3 OFF → `Layer3DisabledError`, force=True bypasses only telegram/mcp). Every rejection emits both ORDER_REJECT_* and a unified `ORDER_BLOCKED` audit line with `actor=`. Then `_validate_symbol`, `_validate_stop_loss` (raises if mandatory_stop_loss and SL is None), `_validate_leverage` (RiskLimitExceededError above max_leverage), rounds qty/price, `validate_order_params`, two inline risk caps (HARD position-size cap to `equity * max_position_size_pct/100`, and a 2% max-loss cap as `sl_distance*qty*leverage`; both fail-open on error). The RPC goes through `_place_order_with_idempotent_retry` (one transient retry reusing the orderLinkId; business errors propagate; `DuplicateOrderLinkIdError` treated as success via `_recover_order_by_link_id`). On success builds an `Order` (NEW), persists via `TradingRepository.save_order`, runs the post-place SL verifier (sleeps 1.5s, fetches the position, calls `set_stop_loss` if naked, retries, logs `LOSS_ENTRY_SL_NAKED` CRITICAL on persistent failure rather than aborting the filled order). **PositionService** — `close_position` places an opposite-side `reduceOnly` Market order (`tic-` linkId, purpose default `layer4_close`), builds a `TradeRecord`, zeroes the position, notifies `coordinator.on_trade_closed`; `reduce_position` is the partial analog. `InstrumentService` caches rules 1h. `MarketService` caches single tickers 5s, bulk linear tickers 30s. `order_guards.py` re-exports the same gates as pure functions for the Transformer bybit_demo path; they fail OPEN and return REJECTED Orders rather than raise. The deprecated `BrainExecutor` is dead — brain_v2 calls `order_service.place_order(purpose='layer3_entry')` directly.

### 7.17 Execution Adapters

**One-liner:** The three swappable execution backends behind the Transformer (Bybit api-demo REST/WS, Shadow HTTP on :9090) plus the restart-based exchange-switching machinery, all presenting the identical interface and the "adapters never raise" sentinel contract.

**bybit_demo_client.py** — aiohttp over `api-demo.bybit.com`, V5 HMAC-SHA256 over `timestamp+api_key+recv_window+payload`, re-signs with a fresh timestamp every attempt (Bybit validates recv_window vs wall clock). Branch logic: 4xx-except-429 → permanent `BybitAPIError` (401/403 emit BYBIT_DEMO_AUTH_FAIL); 5xx/429/network → retried up to `retry_attempts` 5 with `0.2*2^(n-1)` backoff; 2xx with retCode≠0 → `_translate_ret_code` (110007/110045→InsufficientBalance, frozenset of 110xxx→InvalidOrder, 10006/10018→RateLimit, other 110xxx→OrderRejected). Critically **retCode 10002 (TIMESTAMP_FAIL)** is the ONE BybitAPIError retried in-loop with a fresh timestamp; on exhaustion the wrapped exception preserves `details.ret_code=10002` so the adapter can flag unknown-state. `_in_boot_grace()` (30s) demotes exhausted-retry logs. **The CLIENT raises; the ADAPTER catches and returns sentinels** — that split preserves the "never raises" contract.

**bybit_demo_adapter.py** — `_CATEGORY="linear"`, `_POSITION_IDX=0` (one-way only). `get_positions_with_confirmation` paginates `/v5/position/list` up to 5 pages, persists tagged `exchange_mode='bybit_demo'`, prunes stale rows only after TWO consecutive confirmed-empty ticks (a single transient empty can't wipe the cache), returns `confirmed=False` on 10002/mid-pagination/cap (phantom-close avoidance I1/F-26). `close_position` sends an opposite-side reduceOnly IOC Market order, resolves the real fill via 4×250ms polls, stamps `close_trigger` into `_recent_close_triggers` (60s TTL). `reduce_position` floor-quantizes to `lotSizeFilter.qtyStep` via the injected InstrumentService, downgrades to full close (REDUCE_FALLBACK) if it can't snap. `get_last_close` queries `/v5/position/closed-pnl` with up to 10×1s retries (Bybit's indexer is async, ~35% single-shot miss); `_select_close_row` picks by orderId > exit-price+freshness+qty > qty-only, never a stale rows[0]; `net_pnl_pct` derived from authoritative `closedPnl/notional` (F1 sign-fix). **AccountService** reads the USDT settlement coin's own equity (not the inflated all-coin totalEquity ~3.7×). **websocket_subscriber.py** — private demo stream; only execution fills with `closedSize>0 && leavesQty==0` are full closes; L1-dedups on `(symbol,orderId)` 5s; accumulates `execPnl/execFee` per orderId across laddered fills; maps `stopOrderType`→closed_by; bridges pybit's thread via `run_coroutine_threadsafe`. **shadow_adapter.py** — same three classes over `http://127.0.0.1:9090/api/*`; `_shadow_get_with_retry` mirrors the bybit_demo client; `confirmed=False` on transport failure (no 10002 since unsigned); set_leverage is a no-op True, modify/cancel are no-ops. **switching** — `ExchangeSwitcher.execute_switch_with_restart` validates target in RESTART_SWITCHABLE_MODES, refuses if positions>0 and not force, closes all concurrently (one retry), persists via `persist_target_mode`, writes the atomic sentinel (tmp+os.replace), pre-restart Telegram alert, `systemctl restart trading-workers trading-mcp-sse` (start_new_session). `verify_post_switch` reads+deletes the sentinel on boot, probes the new adapter, sends the "Restart complete" Telegram confirmation.

### 7.18 Intelligence

**One-liner:** Market intelligence layer — ingests Finnhub news, Reddit posts, F&G/funding/OI alt-data, scores with a keyword sentiment engine, and fuses F&G + funding + price-conditioned OI into per-coin trading Signals.

`FinnhubClient` wraps the sync SDK in `asyncio.to_thread` + `@retry(3) @rate_limit(1/s) @timed`, mapping SDK exceptions to `FinnhubError`. `NewsService.fetch_latest_news` pulls `max_articles_per_fetch` (50), drops >24h via a hard `now_utc()-24h` cutoff, drops empty headlines, dedups via `headline_exists`, scores `headline+summary`, runs `extract_symbols` against the config-driven alias map (word-boundary for short tickers, substring for long names), emits a `FINNHUB_COVERAGE` funnel log. `SentimentScorer` is keyword-based (6 lists, fixed weights ±0.3/±0.15/-0.25/+0.2, clamp). `SentimentAggregator.aggregate_for_symbol` averages news+reddit, normalizes F&G to `(fg-50)/50`, weights NEWS 0.35/REDDIT 0.30/F&G 0.20/MOMENTUM 0.15 (F&G amplified to 0.40 at fg<30/>70, 0.60 at <20/>80); forces `UNKNOWN` (not NEUTRAL) when a coin has no own data (~93% of coins), caches the verdict 30 min, suppresses per-coin log spam in degraded mode.

`FearGreedClient` (alternative.me via aiohttp, 1h cache + DB fallback logging FEAR_GREED_FALLBACK with age). `FundingRateTracker`/`OpenInterestTracker` go through `BybitClient.call`; OI fetches at a config-driven 5min interval (the 'Five-Fix' that broke a 50-min staircase plateau) and sources its 24h delta from `AltDataRepository` so all consumers share one honest value. **`SignalGenerator.generate_signal`** is sentiment-free (Fix 3, 2026-06-10): direction comes only from F&G, funding, OI. `_blend_oi_windows` price-conditions three windows: 15m and 1h drivers (weights 0.4/0.6), 24h context-only (0.0), each conditioned against its OWN matching kline price window — rising OI on falling price flips bearish (shorts piling in). `_evaluate_signal`: `s_fg=(50-fg)/fg_normalize_range` (contrarian, EXCLUDED from direction when `fg_direction_neutral=true` default — it was pinning ~100% buy), `s_funding=-funding/funding_normalize` (crowded longs bearish), blended `s_oi`; only components above `*_min_active` participate, weights renormalize, `direction_score` maps via `buy_threshold` 0.18/`strong_threshold` 0.55. `ConfidenceCalculator` combines agreement(0.40)/magnitude(0.25)/volume(0.20)/freshness(0.15). `CONFIDENCE_THRESHOLDS` enforced as a non-destructive hard gate (STRONG≥0.60, BUY/SELL≥0.40, else downgrade toward NEUTRAL preserving `original_signal_type`/`confidence_floor_failed`). Dense observability (SIG_OI_WINDOWS, SIG_GEN_INPUT, SIG_CLASSIFY, SIG_DOWNGRADE, SIG_GEN).

### 7.19 TIAS

(Full detail in §6.3.) **One-liner:** TIAS captures full context for every closed trade, then calls DeepSeek-via-OpenRouter to produce a structured autopsy that feeds APEX, the brain's "lessons" block, and Telegram dashboards. It NEVER blocks trading — everything runs in asyncio tasks scheduled from a close callback, every data source try/excepted.

Phase 1 (`collector.py`) assembles six field groups into a `TradeIntelligence` dataclass (field names == DB columns). Group A from the close record (incl. `exchange_mode` threaded from `transformer.current_mode` after a fix where every row silently defaulted to 'shadow'); Group B from `trade_thesis`/`strategy_trades` + entry-time overrides (claude_directive, signal_score with apex fallback, setup_id, entry RSI/MACD/ATR); Group C per-coin regime ONLY from `get_coin_regime()` — on a miss it records explicit 'unknown' with `regime_verified=0`, NOT the global BTC regime (per-coin authority); Group D M5 TA (RSI, MACD, stochastic, ADX, ATR, NATR, volume ratio, computed bollinger %B + price-vs-vwap); Group E ProfitSniper snapshot + latest `sniper_log`; APEX fields capture what the optimizer changed + `apex_tp_fill_rate`. `repo.save()` returns the rowid Phase 2 will UPDATE.

Phase 2 (`analyzer.py` + `deepseek_client.py` + `prompts.py`) — builds the user prompt + the static TIAS_SYSTEM_PROMPT (analyst persona + strict JSON schema + the 18-category-definitions block). `_call_with_fallback` tries the primary model (`deepseek-chat-v3-0324`), re-raises immediately if `retryable` is False, else retries ONCE with the fallback (`deepseek-chat`). `DeepSeekClient.analyze` POSTs to OpenRouter (response_format json_object, temp 0.3, 45s timeout); classifies 429/503/timeout retryable, others non-retryable; `_parse_json` strips markdown fences. `_map_response` normalizes the model's category via `normalize_category` (recognized→'ok'/'normalized', unknown→kept-but-logged 'invalid' so taxonomy drift is visible never silent), computes cost (input×0.27/1M + output×1.10/1M). `categories.py` — 18 categories, exactly two SUCCESS (CORRECT_ENTRY default for wins; CORRECT_EXIT only when exit was decisive); CORRECT_TRADE_BAD_LUCK is a correct-decision LOSS, never a win. `get_situation_stats` "common issues" filtered to `win=0` so success categories don't pollute the optimizer's failure list. A 30-min `TIASBackfillWorker` retries rows where `ds_why IS NULL` and `analysis_attempts < 3` (abandoned after 3).

### 7.20 Database

**One-liner:** Async SQLite persistence — a WAL-mode reader-pool/single-writer engine + idempotent migrations + a repository layer over a ~70-table schema organized into the 4-layer memory model, with protected-table guards.

`DatabaseManager` is a thin facade over `_PooledDatabaseEngine`: ONE dedicated writer connection guarded by an `asyncio.Lock` plus a `_ReaderPool` of N readers (queue-backed, grows to `hard_cap=2×size`, lazily under a `_grow_lock`, emitting CONN_POOL_GROW/EXHAUSTED). The legacy single-lock engine was removed; `concurrency_model='single_lock'` raises a `DatabaseError`. Every connection gets identical PRAGMAs via `_apply_pragmas` (`journal_mode=WAL`, `busy_timeout=10000`, `foreign_keys=ON`, `cache_size=-65536` 64MiB, `synchronous=NORMAL`, `wal_autocheckpoint=2000`, `journal_size_limit=100MiB`, `temp_store=MEMORY`, `mmap_size=256MiB`) — drift between readers and the writer is the failure mode this prevents. `execute`/`executemany`/`checkpoint`/`transaction` route through `writer_locked` (3-attempt retry on "locked", `0.5*(n+1)` backoff); `fetch_one`/`fetch_all` through `reader_acquired`. `checkpoint(mode)` forces WAL truncation that auto-checkpoint misses under sustained reader snapshots. Instrumentation (`_HolderInstrumentation`): a 1000-sample wait-time ring buffer + bounded per-caller counters; `WRITER_LOCK_WAIT` warns >1000ms, `CASCADE_DETECTED` >5000ms (pushes sniper/watchdog OVERDUE), `CONN_POOL_WAIT` >500ms; hourly `log_lock_histogram()` emits p50/p95/max + top callers.

`protected_tables.PROTECTED_TABLES` = {tias_results, tias_analyses, trade_intelligence, trade_log, trade_history, thesis_store, virtual_positions, sniper_log}. `assert_not_protected_destructive` regex-classifies the first statement; DELETE/TRUNCATE/DROP on a protected table raises `ProtectedTableViolation` (logging DB_PROTECT_BLOCKED with caller attribution) BEFORE the lock is taken (unless `force=True`). This exists because a prior cleanup regression wiped TIAS data and cost $19. `cleanup.py` asserts at import time that no protected table is in `RETENTION_POLICIES`. Repositories (Market/Trading/Learning/AltData/News/Sentiment/Context/Factory/Backtest/Portfolio/Telegram) return core dataclasses via `models.row_to_*` (with `_parse_json/_parse_dt/_safe_float/_safe_int` guards). MarketRepository chunks large kline/ticker writes (default 500, `asyncio.sleep(0)` between chunks) to avoid 12–20s lock holds, can read from a TickerCacheBuffer first. TradingRepository tags rows with `exchange_mode`. The DatabaseManager is imported in 121 files.

### 7.21 Database — Schema & Migrations

**One-liner:** A single declarative `MIGRATIONS` list (`SCHEMA_VERSION=40`) creates ~70 tables run idempotently on boot.

The entire schema is one module-level Python list of ~120 raw SQL strings, grown append-only across versions v4..v40; there is no per-version file — the list IS the schema. The header docstring states the intent: "Standard SQL syntax only for PostgreSQL migration readiness." `run_migrations` reads `MAX(version)` from `schema_version`; if `>= 40` it short-circuits. Otherwise it iterates, and for each statement matching `^ALTER TABLE x ADD COLUMN y` it consults a cached `PRAGMA table_info(x)` set and SKIPs if the column exists (SQLite has no `IF NOT EXISTS` for columns) — this replaced the prior catch-the-error approach that produced ~10,666 ERROR logs per restart. A belt-and-braces try/except still downgrades duplicate-column/already-exists to DEBUG. A recurring late-stage theme is `exchange_mode TEXT DEFAULT 'shadow'` bolted onto orders/account_snapshots/trade_history/positions/trade_intelligence (v29–v32) with idempotent backfill UPDATEs keyed on the 2026-05-08 shadow→bybit_demo cut-over timestamps; v31–v33 also adjust fear_greed/position_snapshots indexes to cut per-INSERT B-tree cost.

Tables group into the 4-layer model — **Market** (klines UNIQUE(symbol,timeframe,timestamp), ticker_cache PK symbol [99.7% of lock-wait holders], orderbook_snapshots); **Trading** (orders, positions PK symbol, trade_history, account_snapshots); **Intelligence** (news_articles, reddit_posts, aggregated_sentiment, economic_calendar, fear_greed_index, funding_rates, open_interest, signals); **Learning** (strategy_performance, signal_accuracy, pattern_log, brain_decisions [largely dead — superseded by claude_decisions], user_preferences, watchlists, active_strategies, session_log) — plus strategy-engine/factory/backtest/portfolio tables (active_universe, regime_history, strategy_trades, ensemble_votes, daily_pnl, discovered_patterns, generated_strategies, backtest_results, strategy_lifecycle, portfolio_allocations, …), Telegram tables (price_alerts, trade_journal, scheduled_reports, conversation_log), fund-manager tables (fund_manager_state/_log, capital_level_history, profit_ratchet_log), a **Data Lake** set (trade_thesis, market_snapshots, trade_log, position_snapshots, claude_decisions, event_log, daily_summary), `transformer_state` (singleton id=1, default 'shadow', seeded INSERT OR IGNORE), `switch_history`, `sniper_log` (+~30 ALTERs for Hurst/momentum/ATR/volume models), `trade_intelligence` (TIAS, +dozens of v18-v40 ALTERs), `coin_regime_history`, `cycle_metrics`, `thesis_events`.

### 7.22 MCP Server

**One-liner:** Dual-transport (stdio/SSE) MCP server exposing 43 trading-intelligence tools to Claude Code / claude.ai, wired to the bot's trading, intelligence, TA, risk, and exchange-switching services.

`server.py` loads `Settings._load_fresh()`, overrides transport with `--transport` (default stdio), builds `MCPServer`, runs `initialize()`, dispatches to `run_stdio()` or `run_sse(host, port)` in a try/finally that always calls `shutdown()`. `run_stdio()` writes NOTHING to stdout/stderr (that channel carries JSON-RPC framing). `run_sse()` builds a Starlette app with `/sse`, `/messages/`, `/health` served by uvicorn at log_level "warning"; the `/sse` handler is the only auth point (`MCPAuth.extract_token` → `validate_token` → 401 before `sse.connect_sse`). `MCPAuth.validate_token` returns True if no token configured (open server), else `hmac.compare_digest` (constant-time); `extract_token` reads `Authorization: Bearer` or a `?token=` query. The token comes from `MCP_AUTH_TOKEN` (env). `initialize` opens the DB, runs migrations, `_init_services()` (each service in its own try/except so one failing dependency degrades to a "not available" tool response — Trading, the `MCPTransformerAdapter` with a `services_per_mode` map, Intelligence, TAEngine, AlertManager), then `_register_tools()`: 9 `register_*` functions returning `(tools, handlers)` — trading 12, news 4, sentiment 5, altdata 5, analysis 5, risk 5, memory 4, system 3, exchange 3 = **43**. Notable inline thresholds: altdata bakes contrarian interpretation (F&G ≤25/≥75, funding ±0.5%, OI >2%); risk position size = `(balance*risk_pct)/|entry-stop|`. The `exchange_tools` read/switch via `MCPTransformerAdapter` (5s-cached `transformer_state` read) and `ExchangeSwitcher` (restricted to `RESTART_SWITCHABLE_MODES`; `switch_exchange_with_restart` closes positions, persists, sentinel, `systemctl restart`). `mcp_stdio_proxy.py` forwards stdio JSON-RPC to the persistent SSE server (`MCP_PROXY_UPSTREAM` default 8080) with `Authorization: Bearer $MCP_AUTH_TOKEN`, logs only to files, arms an `os._exit` watchdog. `MCPClientPool` is an opt-in warm-client lifecycle scaffold (default off, `_open_client`/`_ping` placeholders).

### 7.23 Telegram

**One-liner:** Two-way AI-powered Telegram trading terminal — a single long-polling bot exposing ~50 slash commands + inline dashboards + free-form Claude chat, plus a send-only alert path (AlertManager) sharing the same bot connection.

The subsystem has ONE Telegram bot connection serving TWO roles. `InteractiveTelegramBot` (`bot.py`) is the only thing that POLLS (`start_polling(drop_pending_updates=True)`); `AlertManager`/`TelegramBot` (`src/alerts/`) is SEND-ONLY and shares `app.bot` via `set_bot()` — the "ONE bot, ONE connection" design to avoid a Telegram `getUpdates` 409 conflict (only one poller per token). Shadow runs a SEPARATE bot with a distinct `SHADOW_TELEGRAM_BOT_TOKEN`, so it's isolated; the real internal risk is two writers to the same chat, mitigated by keeping a single `dashboard_msg_id` and always delete-then-resend.

`register_dashboard_handlers` copies the entire services dict into `app.bot_data`; on failure 9 commands are PRUNED from `set_my_commands`. Handler order matters (dashboard/control pattern handlers before the generic callback; free-text MessageHandler last). `MessageRouter.classify` runs priority-ordered regex (EMERGENCY → TRADE → QUICK_QUERIES → ai_question); `_normalize_symbol` maps `btc`→`BTCUSDT`, rejects NOISE_WORDS, falls back to `last_symbol`. The richer AI chat (`bot._handle_ai_question`) checks `cost_tracker.can_afford_call()`, assembles LIVE context (equity, positions with per-side PnL, daily PnL+mode, market regime via `get_last_regime()` cached-read to avoid advancing BTC hysteresis, symbol ticker+TA, 5-msg history) with a strict "you ARE connected, max 300 words, never mention MCP/tools" system prompt, tolerates str or dict returns, truncates 4000 chars. The **dashboard** (`dashboard_handler.py`, 2569 lines) is screen-reader-first (the operator is blind — every metric spelled out), pulls mode from `transformer.mode_label`, positions with TradeCoordinator plan data, risk thresholds (Caution < -2%, WARNING < limit×0.7, HALTED ≤ daily_loss_limit), Claude call stats, plus mode4/apex sections; `dashboard_callback` drives layer start/stop (`layer_manager.start_layer/stop_layer`), `dash_emergency_close`, mode toggle, and the multi-step exchange-switch flow (preview→confirm) driving `transformer.switch_to(...)` for live and `ExchangeSwitcher` for demo; `_safe_bot_send` wraps every send with 15s timeouts + a 2s/5s/10s retry ladder + parse-mode fallback; auto-refresh delete-then-resends every 60s. `TradingHandler` always shows a RiskChecker card first (size >10% equity, ≥5 positions, leverage >5x, NATR>2 suggests 2x); execution only in `execute_after_risk_check` (`purpose="telegram_manual"`). `/emergency` requires a second confirm. `/pause`/`/resume` flip `pnl_manager.is_manually_paused` (the single source of truth every gate consults).

### 7.24 Factory & Support

**One-liner:** Supporting layer — the Strategy Factory (AI pattern discovery → code generation → backtest → trial → lifecycle), the portfolio optimizer (Kelly/correlation/risk-budget), SENTINEL position guards (exit firewall, deadline engine, DeepSeek advisor), the Telegram alerting pipeline, and a loguru→Telegram observability relay.

**Factory:** `PatternDiscoverer.run_full_discovery` runs 7 heuristic analyzers (NOT ML) over SQLite klines — `single_variable` flags volume>3×-avg spikes producing >60% up moves over ≥10 spikes; `temporal` buckets forward returns by hour-of-day (>65% bias over ≥15 samples); `cross_asset` finds alts lagging a >1% BTC move; `micro` works on 1-min doji clusters. Hits become `DiscoveredPattern`s, filtered (`min_occurrences` 20, `min_win_rate` 0.55, `min_profit_factor` 1.2), ranked `wr*pf*log(occ+1)`, deduped (>80% condition overlap), top 20 saved. `StrategyGenerator.generate` (the ONLY Claude-calling step, budget-capped) writes `BaseStrategy` code, retries up to `max_generation_retries` 3 on syntax fail. `CodeValidator.validate` runs 4 gates (syntax compile, safety substring scan for os/subprocess/eval/open/print, interface BaseStrategy/scan/vote/RawSignal, logic). `BacktestEngine.run_on_trades` chains `MetricsCalculator` (WR/PF/EV, Sharpe/Sortino sqrt(365), drawdowns, streaks) → `WalkForwardValidator` (70/30, efficiency=oos_wr/is_wr) → `MonteCarloSimulator` (1000 shuffles, prob_profit, prob_ruin dd≥20%); `_grade` applies 7 pass gates + A+..F. `TradeSimulator` does realistic SL-before-TP candle fills with slippage/commission/funding. `StrategyLifecycleManager` enforces a VALID_TRANSITIONS state machine; `TrialManager` runs a 14-day probation; `LivePatternMonitor.check_emerging` flags patterns firing ≥3× baseline.

**Portfolio:** `KellyCalculator` (full/fractional 0.25/dynamic with streak+drawdown adjustments, <20 trades → 2% floor), `CorrelationTracker` (pairwise Pearson, DFS clusters above 0.7, per-strategy penalty), `DynamicAllocator` (performance score × Kelly × (1-corr_penalty), normalize to 100%-cash_reserve, tier budgets proven55/ai30/trial10, rebalance when change > `min_rebalance_change_pct` 2%), `RiskBudgetManager` (daily budget + can_trade gate, per-day reset), `StressTester` (7 hardcoded scenarios with survival flag), `PerformanceAnalytics` (PnL attribution).

**SENTINEL:** `firewall.should_allow_strategic_action` blocks `close`/`take_profit` from the default `strategic_review` source (exits belong to Watchdog/ProfitSniper/SL_TP — justified by documented 84% vs 0% win-rate data), bypasses for trusted `call_b`/`call_a_urgent`, but FIRST applies a phantom-close defense rejecting close/TP on symbols absent from `active_symbols`. `DeadlineEngine.evaluate` handles expired plans with 4 PnL tiers (profit≥0.5% → close win; breakeven to -0.3% → SL to entry + 5-min grace; small loss to -1.5% → tighten to -0.5%; big loss → cut). `PortfolioAdvisor` calls DeepSeek V3 with a strict "tighten-only, never close, require ≥0.50% profit" prompt, parsing JSON tighten_stop recommendations the watchdog drains each tick.

**Alerts:** `AlertManager` typed send_* methods gate on config flags, render HTML via `AlertTemplates`, then `_send` dedups via `normalized_content_hash` (replaces numbers with #NUM to collapse retry storms), rate-limits per rolling hour (CRITICAL bypasses), INFO fire-and-forget vs awaited CRITICAL/WARNING. `TelegramBot` uses a shared bot instance, 10-15s timeouts, a 2/5/10s retry ladder, parse-mode fallback, 3800-char truncation. `BybitDemoAlertRelay` registers a loguru sink filtered to bybit_demo/worker components, maps ~25 tag prefixes to send_error_alert/send_risk_warning, scheduled via `run_coroutine_threadsafe`, never raising out of the sink.

### 7.25 Factory Lifecycle

**One-liner:** An AI strategy R&D pipeline (discover → generate → validate → backtest → trial → promote) that is effectively isolated from the live trading plane.

Wired in `manager.py:1888` behind `if s.factory.enabled:` (creates DiscoveryWorker/LiveMonitorWorker/BacktestWorker/TrialMonitorWorker). `DiscoveryWorker.tick()` is polled every 7200s but no-ops unless `now.hour == discovery_schedule_hour_utc` (2 UTC) and it hasn't run today. Discovery is **pure SQL/statistics, not LLM** (the DISCOVERY_PROMPT is exported but never used). **Critically, the backtest and trial-deployment links are unimplemented stubs:** `BacktestWorker.tick()` only LOGS "would backtest {name}" — no real `BacktestEngine.run_on_trades`/`TradeSimulator` invocation. `TrialManager.deploy_to_trial()` (which would seed a 25%-size paper trial) is never called in production — only in tests — so nothing populates `trial_active`/`trial_performance`. And `src/strategies/register_all.py` registers a fixed hand-coded roster; there is NO dynamic import/exec of the AI-generated `GEN_*` code into the live `StrategyRegistry`. **Effect on live trading: essentially none.** The factory discovers patterns, writes/validates code, and tracks lifecycle state entirely within `factory.db`/logs, but its outputs never enter the live execution plane. (`ai_generated` only appears as a portfolio budget-tier label and a hold-duration default in `trade_coordinator.py`.)

### 7.26 Shadow Sub-App

**One-liner:** A standalone async service (`shadow/`) that warehouses Bybit market data in SQLite and runs a virtual perpetual-futures exchange exposed over an aiohttp HTTP API on 127.0.0.1:9090 for paper trading.

Self-contained at `shadow/`, own config/DB/logging/event-loop, started by `shadow.py` (16-step boot: load_config singleton, setup_logging, DatabaseManager.connect [WAL, asyncio.Lock serializing writes], run_migrations [v3], initialize_wallet at 10000, select coins, wire collectors+exchange+API+Telegram, launch 8 named tasks, await shutdown_event). `CoinSelector` ranks active LinearPerpetual USDT by turnover24h and upserts the top 100 into `tracked_coins` (dropped coins is_active=0, history preserved). `WebSocketManager` opens TWO Bybit linear WS connections (tickers.* and kline.1.*) in batches of 10 with exp-backoff reconnect; every ticker message DELTA-merges into `_latest_tickers` (the authoritative in-memory price cache). `KlineCollector.on_kline` buffers only `confirm=true` candles, flushing every 5s/50 rows via INSERT OR IGNORE; `backfill()` pages historical candles for gaps >2min on startup. TickerCollector (60s), OICollector (300s), FundingCollector (00/08/16 UTC +60s) batch-insert, skipping coins stale >300s. `get_price_data(symbol)` is the single price source shared by wallet/engine/monitor/API. `OrderEngine.place_order` fills at last_price ± slippage, computes notional/margin/entry_fee (taker 0.055%), checks `VirtualWallet.can_afford`, INSERTs a `virtual_positions` row, deducts the fee. The wallet derives `total_equity = starting + total_realized_pnl + unrealized_pnl - total_fees_paid`; `available = equity - SUM(margin_used)`. `PositionMonitor` (1s loop) recomputes live PnL, tracks peak/drawdown/time-in-profit, checks **STOP LOSS FIRST** then TP — SL fills at the live `current_price` (modeling gaps through the stop), TP fills optimistically at the exact target. On close it simulates exit slippage, computes gross/net PnL + exit_fee, applies via `wallet.apply_trade_close`, updates the position row, forwards a 43-field record to `TradeRecorder` → `trade_history` (never deleted); failed closes retry up to 3. `WalletSnapshotter` (60s) writes the equity curve; `DailyRollup` (midnight) computes `daily_summary` (win rate, PF, streaks, drawdown) then `RetentionEngine` compresses ticker/wallet snapshots to hourly/daily buckets via ROW_NUMBER dedup + ANALYZE + wal_checkpoint(TRUNCATE) + weekly VACUUM. The main project consumes Shadow two ways: `src/shadow/shadow_adapter.py` (drop-in Order/Position/Account services over the 9090 API, with a 30s boot-grace retry to avoid false zero-balance) and `src/analysis/structure/shadow_kline_reader.py` (read-only guest on `shadow.db`'s klines table). URL from `settings.general.shadow_api_url`.

### 7.27 Docs Reconciliation

**One-liner:** A four-tier documentation stack (README/CLAUDE → PROJECT_BIBLE → PROJECT_BLUEPRINT → SYSTEM_INVENTORY) where each doc is progressively staler than the live code; SYSTEM_INVENTORY is a self-aware reconciliation map flagging doc-vs-code conflicts, dead code, and open questions.

The docs converge on one architecture even as their numbers drift: an MCP server + autonomous trading bot organized as a 3-layer dependency chain (Data → Brain → Execution; Layer N can't start without N-1; state persists to `data/layer_state.json`). README states the cross-cutting invariants (no stdout, async-first, ~1GB RAM target [no pandas], config-driven with zero magic numbers, paper/shadow default). SYSTEM_INVENTORY IS the reconciliation subsystem — a regenerated, fully-measured snapshot whose value lives in four sections: config conflicts (e.g. three competing SL authorities), dead-code (29 of 65 tables have zero writers; backtest_worker is a logging stub), anomalies (two distinct `layer_manager.py` files; PROTECTED-table guard blocks DELETE/DROP/TRUNCATE but NOT UPDATE), and 30 open questions directed at the operator.

**Trust order is inverse to age:** live code/config > `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md` + `dev_notes/bybit_demo_adapter/*` > `SYSTEM_INVENTORY.md` > `PROJECT_BLUEPRINT.md` > `PROJECT_BIBLE.md`. The BIBLE describes a pre-APEX world (Anthropic SDK as the brain, 40 strategies) — historical intent, not current truth. `CLAUDE.md` is the only fully-authoritative doc because it governs the editing agent. **Confirmed drift against live code:** `SCHEMA_VERSION` is now **40** (BLUEPRINT says 23, INVENTORY says 24); ~27 workers registered (vs INVENTORY's 22, BIBLE's 24); `config.toml` is ~3,215 lines / 67 sections (vs the docs' 763 / 34–44); the whole `src/bybit_demo/` package + `[bybit_demo]` mode is absent from all three core docs (documented only in dev_notes); MCP tools are 43; `PROTECTED_TABLES` does NOT contain `trade_thesis` (deliberately removed so cleanup can prune closed theses past 60 days).

---

## 8. Data & Persistence Model

### 8.1 The main database

WAL-mode SQLite (`data/trading.db` by default), schema version **40**, ~70 tables behind the pooled async engine (§7.20/§7.21). Tables group into the **4-layer memory model**:
- **Market:** `klines`, `ticker_cache` (the hottest write — 99.7% of lock-wait holders), `orderbook_snapshots`.
- **Trading:** `orders`, `positions`, `trade_history`, `account_snapshots` (all `exchange_mode`-tagged).
- **Intelligence:** `news_articles`, `reddit_posts`, `aggregated_sentiment`, `fear_greed_index`, `funding_rates`, `open_interest`, `signals`, `economic_calendar`.
- **Learning:** `strategy_performance`, `signal_accuracy`, `pattern_log`, `brain_decisions` (largely superseded by `claude_decisions`), `discovered_patterns`, `generated_strategies`.
- **Data Lake:** `market_snapshots`, `trade_log`, `position_snapshots`, `claude_decisions`, `event_log`, `daily_summary` (60s compressed state + full trade context).
- **Control/state:** `transformer_state` (singleton id=1 — the runtime mode authority), `switch_history`, `fund_manager_state` (peak_equity, profit ratchet, starting_equity), `trade_thesis`, `thesis_events`, `ensemble_votes`, `trade_intelligence` (TIAS), `sniper_log`, `cycle_metrics`, `coin_regime_history`.

**Protected tables** (DELETE/TRUNCATE/DROP refused pre-lock): `tias_results`, `tias_analyses`, `trade_intelligence`, `trade_log`, `trade_history`, `thesis_store`, `virtual_positions`, `sniper_log`. Note the guard blocks destructive DELETE/DROP/TRUNCATE but NOT UPDATE.

### 8.2 The Shadow data warehouse

Separate `shadow.db` (schema v3, WAL) holding 12 tables: `klines`, `ticker_snapshots`, `funding_rates`, `open_interest_history`, `tracked_coins`, `virtual_wallet` (single row), `virtual_positions`, `trade_history` (43 columns, never deleted), `wallet_snapshots` (equity curve), `daily_summary`, `shadow_settings`, plus schema_version. The main project reads its `klines` table read-only via `ShadowKlineReader` for the X-RAY structure pipeline, and talks to its virtual exchange over the 9090 HTTP API. Retention compresses ticker/wallet snapshots to hourly/daily buckets via ROW_NUMBER window dedup; weekly VACUUM.

---

## 9. Configuration (config.toml + .env)

### 9.1 config.toml section map (~67 sections, 3,215 lines)

Precedence for the curated env-override set: **env > toml > hardcoded default**. Key sections:

| Section | Purpose | Notable keys |
|---|---|---|
| `[general]` | mode + globals | `mode="bybit_demo"`, `shadow_api_url`, `timezone`, `log_level`, `log_dir` |
| `[bybit]` | mainnet market data | `testnet=false`, `default_symbols`, `rate_limit_per_second`, `recv_window` |
| `[bybit_demo]` | paper exec adapter | `enabled`, `base_url`, `close_pnl_source` (ws_exec/gated/legacy), `close_pnl_reconcile_max_exit_divergence_pct` |
| `[brain]` | Claude autonomous trading | `claude_cli_model="claude-opus-4-7"`, `claude_cli_timeout_seconds=300`, `claude_cli_first_byte_timeout_seconds=90`, `strategic_interval`, `use_packages`, `quality_skip_*`, `[brain.cold_start_protection]` |
| `[risk]` | risk limits | `max_leverage=5`, `mandatory_stop_loss` (cannot be disabled), `default_stop_loss_pct`, `min_sl_distance_pct`, `[risk.flip_tp]`, `[risk.volatility_stop_scaling]` |
| `[apex]` | DeepSeek optimizer | `model`, `brain_authoritative_sizing_enabled`, `max_position_size_usd`, `apex_dir_flip_enabled`, `apex_leverage_override_enabled`, `reentry_cooldown_seconds` |
| `[analysis.structure]` | X-RAY | `swing_lookbacks`, `min_touches`/`min_touches_resistance`, `tp_min_distance_pct`, `setup_score_modifier_scale=0.5`, `[setup_types]`, `xray_authority_min_score=45` |
| `[analysis.ta]` | classic TA | `confidence_ema_alpha=0.4`, `volume_ratio_use_closed_candle` |
| `[scanner]` | scanning/briefing | `mode="briefing"`, `scoring_weights`, `briefing.interestingness_weights`, `labeller` |
| `[strategy_engine]` | ensemble | `min_ensemble_agreement(_strong)`, `single_strategy_max_share=0.4`, `regime_weighting_enabled`, grade thresholds |
| `[regime]` | regime detection | ADX/choppiness/ATR thresholds, hysteresis, breadth brake |
| `[fund_manager]` | capital | `check_interval_seconds=60`, `starting_unlock_pct=20`, pool splits, profit-lock, reconcile |
| `[signal_generator.multi_source]` | signals | `fg_direction_neutral(true)`, OI blend weights/windows, buy/strong thresholds |
| `[database]` | persistence | `path`, `concurrency_model`, `reader_pool_size`, `db_lock_wait_threshold_ms`, `kline_save_chunk_size` |
| `[mcp]` / `[mcp_pool]` | MCP | `transport`, `sse_port`, `auth_token` (env), pool warm settings |
| `[watchdog]` / `[time_decay]` / `[layer4_sniper]` / `[sl_gateway]` | exit | `check_interval_seconds`, `min_age_seconds`, `structural_invalidation_required`, `p_win_force_close` |
| `[tias]` | autopsy | `primary_model`, `fallback_model`, `analysis_version` |
| `[universe]` / `[telegram_interactive]` / `[alerts]` / `[sentiment]` / `[enforcer]` / `[workers.sweet_spots]` | misc | watch_list, sweet-spot chain (kline 0:30 … scanner 4:00), alert flags |

### 9.2 .env keys (secrets + non-secret overrides)

From `.env.example`:
- **Bybit:** `BYBIT_API_KEY`, `BYBIT_API_SECRET` (plus `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET` for the demo venue)
- **Finnhub:** `FINNHUB_API_KEY`
- **Reddit (PRAW):** `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`
- **Telegram:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (Shadow uses a separate `SHADOW_TELEGRAM_BOT_TOKEN`)
- **Anthropic:** `ANTHROPIC_API_KEY` (legacy SDK path only — the CLI brain deliberately POPS this to force OAuth)
- **MCP:** `MCP_AUTH_TOKEN`
- **OpenRouter:** `OPENROUTER_API_KEY` (shared by APEX and TIAS DeepSeek)
- **Non-secret overrides:** `TRADING_MODE`, `LOG_LEVEL`, `DATABASE_PATH`, `DATABASE_CONCURRENCY_MODEL`, plus `APEX_API_KEY`/`SENTINEL_API_KEY` (fall back to `OPENROUTER_API_KEY`)

---

## 10. Operating Notes & Risks

### 10.1 What must be true to run locally (Windows)

1. **The AI brain will NOT run on Windows.** `ClaudeCodeClient` uses `os.setsid` (preexec_fn), `os.killpg`/`os.getpgid`, `import fcntl` (Linux-only), `/usr/bin/claude` binary paths, `~/.claude/.credentials.json`, `:`-PATH and `C.UTF-8` — all of which fail on Windows. `create_trade_plan` will raise (`BRAIN_CYCLE_A_FAIL`) and **no trades are ever generated**. To exercise the AI path you need the Linux VM (or WSL with the `claude` CLI installed and OAuth-authenticated).
2. **Mode comes from the DB, not config.toml.** A fresh local DB has no `transformer_state` row, so the system boots `MODE_SHADOW` regardless of `config.toml mode="bybit_demo"`. Editing config.toml has NO effect on runtime routing.
3. **Localhost services must be up.** `shadow_api_url=http://127.0.0.1:9090` and the MCP SSE on `:8080`. If Shadow isn't running, close-detection, authoritative-PnL resolution, and SL/close wire-pushes through the Shadow PositionService all fail; trailing SL never persists.
4. **`systemctl restart` is absent on Windows** — the restart-based exchange switch raises `FileNotFoundError` (caught, returns failure) but only AFTER positions were closed and the DB target mode persisted, leaving routing on the old set until a manual restart. The sentinel path (`data/post_switch_sentinel.json`) is also relative to the process CWD, which differs between VM and local.

### 10.2 Fragile / load-bearing points

- **The dedicated risk engine is dormant on the live path.** Daily-loss limit, max-drawdown circuit breaker, consecutive-loss halt, and the 80% exposure cap are only enforced via `brain_v2.py` (legacy). On the live `ClaudeStrategist → LayerManager` path, the `[risk]` values for those are decorative. Live capital safety rests on the APEX gate + strategy_worker caps + order-boundary guards.
- **Many safety gates fail OPEN** (swallow exceptions and allow the trade) — risky where services may be unwired or the DB is unavailable. On a partial local boot, capital caps degrade to no-ops (e.g. `available` defaults to 1000.0, `max_concurrent` to 5).
- **The single-writer-SL invariant** depends on config flags (`profit_fetching.enabled` + `subordinate_watchdog_trail_exit`). Flipping either makes BOTH the watchdog percentage trail AND the ProfitSniper Chandelier trail write SL for the same symbol, racing for the gateway's 30s rate-limit slot.
- **The Shadow path bypasses ALL order-boundary caps** (mandatory SL, position-size, 2% max-loss, post-place SL verify). If a local run defaults to or is switched to shadow, those final caps disappear.
- **PnL truth is mode-dependent.** `resolve_authoritative_pnl` only gets accurate post-fee/post-slippage PnL when `get_last_close` is available (shadow at :9090, or bybit_demo closed-pnl). On live Bybit (no `get_last_close`) it books `local_fallback` using `pos.unrealized_pnl` — which the Transformer may have overwritten in shadow mode.
- **The Factory produces nothing live** — backtest and trial-deployment are stubs, and generated strategies are never loaded into the live registry.
- **TIAS / APEX / DeepSeek need OpenRouter connectivity** (`OPENROUTER_API_KEY`); without it APEX silently falls back (acceptable) and TIAS autopsy/lessons degrade. But TIAS can never produce a lesson if the brain (CLI) is dead, so the learning loop is starved upstream on Windows.
- **Emergency mode is sticky and one-directional** — once session PnL or hard-stop count trips, it closes everything and requires a manual restart; a transient bad hour on a local run leaves the bot halted.
- **`DrawdownTracker.update_equity` and `TradingModeManager.refresh`** use `asyncio.get_event_loop()` (deprecated 3.12+) for fire-and-forget persistence — best-effort, may be silently skipped.
- **`TieredCapitalManager` default `starting_equity=168000` is hardcoded** — if the DB has no `starting_equity` row (fresh local DB), tier/usable math is computed against $168k, badly mis-sizing limits for a small paper balance.
- **Per-position 3s timeout + 10s tick cadence** — on a slower local host, repeated `WD_MONITOR_TIMEOUT`/`WD_TICK_GAP` can skip exit checks and delay trailing/SL updates.

### 10.3 Provenance / what to trust

Trust live code over any markdown. The reconciliation hierarchy (newest first): live code/config → `dev_notes/COMPLETE_ARCHITECTURE_TRUTH.md` + `dev_notes/bybit_demo_adapter/*` → `SYSTEM_INVENTORY.md` → `PROJECT_BLUEPRINT.md` → `PROJECT_BIBLE.md`. `CLAUDE.md` governs the editing agent (grep-before-delete, zero-pending-work, plain-language git for the blind operator) and remains authoritative.

---

*End of PROJECT_DEEP_ANALYSIS.md*
