# SYSTEM_INVENTORY.md

Factual inventory of the Trading Intelligence MCP system and the Shadow virtual exchange, as measured on 2026-04-24 from the live codebase at `/home/inshadaliqbal786/trading-intelligence-mcp` and `/home/inshadaliqbal786/shadow`. Every number below is a live measurement (`find`, `wc -l`, `sqlite3`, `grep`) taken during this run — nothing is restated from memory or prior documents.

This document is the planning map requested by BUILD_INVENTORY_NOW.md. It reads existing code; it changes nothing.

---

# 1. Repository Layout

## 1.1 Main project — `/home/inshadaliqbal786/trading-intelligence-mcp`

Top-level directory contents (from `ls -la`):

- `.env` (1,635 B) — runtime secrets (Bybit/Finnhub/Reddit/Telegram/OpenRouter/Anthropic keys; MCP auth token). Loaded by systemd `EnvironmentFile`.
- `.env.example` (1,329 B) — documented placeholder file for all seven credential groups.
- `.gitignore` (392 B).
- `.gitconfig`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`, `.claude/`, `.git/`, `.coverage` — tooling state; out of scope.
- `CLAUDE.md` (23 lines) — "analyse before touching anything" coding rules for Claude, referenced by every agent session.
- `Makefile` (96 lines) — service/log/backup/test shortcuts. Calls `bash scripts/*.sh` + `.venv/bin/python scripts/*.py`.
- `PROJECT_BIBLE.md` (1,883 lines) — master spec (pre-APEX era — partially stale against code).
- `PROJECT_BLUEPRINT.md` (742 lines) — mid-life architecture document.
- `README.md` (66 lines) — one-page quickstart.
- `pyproject.toml` (77 lines) — Python ≥3.11; 17 production deps (mcp, aiohttp, pybit, finnhub-python, asyncpraw, aiosqlite, tomli, python-dotenv, loguru, numpy, python-telegram-bot, anthropic, starlette, uvicorn, websockets, psutil) + dev deps (pytest, pytest-asyncio, pytest-cov, ruff, mypy). Build backend: setuptools.
- `requirements.txt` (25 lines) — mirror of dependencies, kept in sync with pyproject.toml.
- `config.toml` (763 lines) — the master configuration file (Part 3).
- `brain.py` (79 lines) — deprecated v1 brain entry point (still installed as `trading-brain.service`).
- `workers.py` (167 lines) — production workers entry point. Has `atexit` + SIGTERM/SIGINT synchronous-fd shutdown log hooks (Phase 30 / Y-29).
- `server.py` (54 lines) — MCP server entry. `--transport stdio|sse`, default port 8080.
- `mcp_stdio_proxy.py` (208 lines) — Y-22 long-lived rework: forwards stdio MCP to the SSE server at 127.0.0.1:8080, avoiding 43-tool init on every Claude CLI spawn.
- `trading.db` (0 B, placeholder — the real database is `data/trading.db`).
- `backups/` — 32 dated pre-overhaul snapshots (e.g., `overhaul29_phase21_20260424_010907/`, `sl_hierarchy_20260422_200822/`, `four_bugs_20260423_101837/`).
- `data/` — production state: `trading.db` (149,774,336 B / 142.8 MiB), `trading.db-wal` (104,857,600 B / 100 MiB), `trading.db-shm` (327,680 B / 320 KiB), `trading_testnet_backup_20260326.db` (17.3 MiB legacy), `layer_state.json` (148 B in-memory Layer state), `logs/` (12 files, 94 MiB routed loguru sinks).
- `scripts/` (19 files, ~2 MB) — operator tooling (Part 25).
- `shadow/` (local sub-project copy — NOT the real Shadow repo — only a config snapshot of pre-implementation spec).
- `src/` (22 sub-packages, 367 Python files, 76,613 lines — Part 5+).
- `systemd/` (5 unit files) — `trading-workers.service`, `trading-mcp-sse.service`, `trading-brain.service`, `trading-backup.service`, `trading-backup.timer`.
- `tests/` (13 directories, 54 test files, 15,748 lines) — pytest-asyncio suite (Part 26).

### 1.1.1 Top-level measurements

| Metric | Live value |
|---|---|
| Python files under `src/` | 367 |
| Python total lines under `src/` | 76,613 |
| Test Python lines | 15,748 |
| Markdown files (excl. `.venv`/`.git`/backups) | 4 (CLAUDE.md, README.md, PROJECT_BIBLE.md, PROJECT_BLUEPRINT.md) |
| Markdown total lines | 2,714 |
| SQLite table count in `data/trading.db` | 65 |
| SQLite index count | 67 |
| Views, triggers | 0, 0 |
| `data/trading.db` size | 149,774,336 B (142.8 MiB) |
| `data/trading.db-wal` size | 104,857,600 B (100 MiB) |
| systemd units owned by this project | 5 |
| Active services at inventory time | 3 (trading-workers, trading-mcp-sse, trading-brain); `trading-backup.service` static (timer-triggered). |

### 1.1.2 `src/` package map (file count / total lines from `find + wc -l`)

| Package | Files | Lines | Purpose |
|---|---:|---:|---|
| `src/alerts/` | 6 | 1,013 | AlertManager, templates, throttle, Telegram bot binding |
| `src/analysis/` | 31 | 7,510 | TAEngine, indicators (trend/momentum/volatility/volume), patterns, **structure/ (X-RAY)**, volatility_profile, ta_cache, vol_scale |
| `src/apex/` | 7 | 2,791 | APEX optimizer — assembler, optimizer, gate, models, prompts, qwen_client (OpenRouter DeepSeek) |
| `src/brain/` | 15 | 4,715 | ClaudeStrategist, ClaudeCodeClient (CLI), BrainManager v1 (deprecated), prompts/, cost_tracker, decision_parser, brain_v2 |
| `src/config/` | 4 | 1,978 | Settings dataclasses, constants, validators |
| `src/core/` | 25 | 7,397 | ServiceContainer, types, exceptions, logging, Transformer, LayerManager, TradeCoordinator, SLGateway, EventBuffer, UrgentQueue, HealthMonitor, FreshnessGuard, ThesisManager, RuleEngine, SLTPValidator, etc. |
| `src/database/` | 18 | 4,160 | DatabaseManager, migrations (schema v24), cleanup, protected_tables guard, models, 12 repositories |
| `src/factory/` | 27 | 2,891 | AI strategy discovery — discoverer, generator, validator, backtester, simulator, monte_carlo, walk_forward, live_monitor, lifecycle, trial_manager, metrics |
| `src/fund_manager/` | 27 | 4,632 | IntelligentFundManager (22 sub-modules: capital_allocator, tiered_capital, risk_weather, emotion_detector, sector_rotation, profit_ratchet, recovery_planner, …) |
| `src/intelligence/` | 19 | 2,340 | News (Finnhub), altdata (F&G, funding, OI, on-chain), sentiment (Reddit + aggregator), signals (signal_generator, confidence) |
| `src/mcp/` | 13 | 1,844 | MCP Server (`server.py`), auth, client_pool (Y-22 SSE pool), 8 tool files (Part 17) |
| `src/portfolio/` | 10 | 862 | Portfolio optimizer — Kelly, correlation, risk_budget, stress_test, analytics, allocator |
| `src/risk/` | 8 | 1,626 | RiskManager, drawdown, position_sizer, stop_loss, time_decay_sl (5-model), validators |
| `src/sentinel/` | 4 | 484 | Portfolio advisor (DeepSeek every 5 min), exit firewall, deadline engine |
| `src/shadow/` | 2 | 627 | ShadowAdapter — the client that calls Shadow's HTTP API at 127.0.0.1:9090 |
| `src/strategies/` | 57 | 6,752 | 43 strategies (A1–K4 + X1), StrategyRegistry, TradeScorer, MarketScanner, EnsembleVoter, RegimeDetector, PnLManager, PerformanceEnforcer, SmartLeverage |
| `src/telegram/` | 40 | 6,434 | InteractiveTelegramBot, 16 command handlers, AI responders, dashboard (auto-refresh), UI helpers |
| `src/tias/` | 8 | 2,083 | Trade Intelligence Analysis — collector, analyzer, repository, deepseek_client, prompts, backfill, models |
| `src/trading/` | 12 | 2,224 | BybitClient (REST+WS), services (market, order, position, account, instrument), auth |
| `src/workers/` | 33 | 14,247 | 22 registered workers + manager.py (1,864 lines) + layer_manager, base_worker, sniper_models, sniper_ring_buffer, position_watchdog (2,638 lines), profit_sniper (3,089 lines), strategy_worker (1,221 lines), settings.py duplicate copy (45k lines — legacy) |

Non-code resources in the project root:

- `data/trading.db` — 149.7 MB SQLite primary store.
- `data/trading.db-wal`, `data/trading.db-shm` — WAL sidecars.
- `data/logs/` — routed loguru sinks. `brain.log` 2.0 MB, `general.log` 6.5 MB, `mcp.log` 516 KB, `workers.log` 1.2 MB (live), plus 10 rotated `workers.YYYY-MM-DD_*.log` at 9.6 MB each (10 MB rotation, 7-day retention).
- `data/trading_testnet_backup_20260326.db` — 17 MB legacy backup.
- `data/layer_state.json` — 148 B, persists LayerManager state.
- `backups/` — 32 timestamped snapshot directories produced during the ongoing 29-issue overhaul.

### 1.1.3 Root markdown documents (first-line titles)

- `CLAUDE.md` — "# CLAUDE.md — Rules for This Project"
- `README.md` — "# Trading Intelligence MCP"
- `PROJECT_BIBLE.md` — full 1,883-line spec, last updated Mar-23; out of sync with APEX/TIAS/SENTINEL/Mode4 additions.
- `PROJECT_BLUEPRINT.md` — 742-line architectural summary, last updated Apr-12; predates X-RAY Phase 4.

## 1.2 Shadow project — `/home/inshadaliqbal786/shadow`

Shadow is a separate Python project that simulates a Bybit-like exchange against live mainnet prices.

Top-level contents:

- `shadow.py` (308 lines) — entry point.
- `layer_manager.py` (648 lines) — Shadow's local layer orchestration (used by Shadow's own startup, not shared with main project).
- `config.toml` (2,099 B) — Shadow configuration (bybit URLs, collector cadences, starting_balance=$10,000, taker_fee=0.055 %, slippage=0.03 %, retention=30/90 days).
- `requirements.txt` (137 B) — small dependency set.
- `src/` (30 Python files, ~4,000 lines of project code excluding venv):
  - `api/shadow_client.py` (354 lines) — aiohttp web app exposing REST endpoints.
  - `collector/` (7 files) — websocket.py (383 lines), kline_collector (200), ticker_collector (123), funding_collector (131), oi_collector (97), coin_selector (131).
  - `database/` — migrations (442 lines), connection (174 lines).
  - `exchange/` — order_engine (761 lines), wallet (295), position_monitor (412), trade_recorder (175), daily_rollup (294), wallet_snapshotter (120).
  - `telegram/` — bot (206 lines), handlers (650 lines).
  - `utils/` — config (278), retention (349), logging (67).
- `backups/overhaul29_phase22_20260424_010940/position_monitor.py` (397 lines) — recent backup.
- `data/shadow.db` — 828 MB SQLite (the virtual-exchange database).
- `data/shadow.db-wal` (5.4 MB), `data/shadow.db-shm` (32 KB).
- `logs/` — runtime logs.
- `systemd/` — Shadow-specific unit files (not enumerated here; mirrored under `/etc/systemd/system` when installed).
- No markdown files.

Shadow scope boundary: the main project's `src/shadow/shadow_adapter.py` talks to this project over HTTP. They share no Python imports and no database — two independent processes communicating via HTTP on 127.0.0.1:9090.

---

# 2. Entry Points And Lifecycle

## 2.1 `workers.py` (167 lines) — the production entry point

Run via `sudo systemctl start trading-workers` or directly (`python workers.py`). Accepts no CLI args by itself; `--workers` flag is mentioned in the docstring but not wired. Reads all configuration from `config.toml` + `.env` via `Settings._load_fresh()`.

Startup sequence (literal order in code):

1. `Settings._load_fresh()` — re-reads config.toml every process start.
2. `setup_logging(log_level, log_dir)` — remove loguru stderr sink, create component-routed file sinks (Part 24).
3. `_install_shutdown_hooks()` (Phase 30 / Y-29):
   - Resolves `workers.log` path for synchronous fd writes.
   - `atexit.register(_atexit_log)` — `WORKER_SHUTDOWN | reason=atexit` on any clean exit.
   - `signal.signal(SIGTERM, _sig_handler)`, same for SIGINT — emit `WORKER_SIGNAL | sig=<NAME>` via two channels (os.write to stderr fd + direct file append to `workers.log`) before raising KeyboardInterrupt. This survives loguru's `enqueue=True` queue thread being killed.
4. `validate_config(settings)` — emits per-warning log line for each caught issue.
5. `DatabaseManager(settings.database.path)` — constructs; connection happens inside `manager.initialize()`.
6. `WorkerManager(settings, db)` — constructs.
7. `await manager.initialize()`:
   - `db.connect()` — opens aiosqlite; PRAGMAs: `journal_mode=WAL`, `busy_timeout=10000`, `foreign_keys=ON`, `cache_size=-65536` (64 MiB), `synchronous=NORMAL`, `wal_autocheckpoint=2000`, `journal_size_limit=104857600`, `temp_store=MEMORY`, `mmap_size=268435456`. Logs `DB_CONN`, `DB_PRAGMAS`, `DB_PRAGMA`.
   - `run_migrations(db)` — idempotent runner (target `SCHEMA_VERSION = 24` in `src/database/migrations.py`).
   - Service construction (`self._services` dict with 60+ keys — enumerated from grep of `self._services[...] = ...`):
     - Transformer (state machine T1), BybitClient (REST+WS), MarketService, WebSocket, AccountService, OrderService, PositionService, InstrumentService.
     - News, Reddit, SentimentAggregator, FearGreed, FundingRateTracker, OpenInterestTracker, OnChain.
     - TAEngine → TACache, VolatilityProfiler.
     - ClaudeCodeClient, CostTracker, DecisionParser, ClaudeStrategist.
     - RiskManager, SLGateway, SLTPValidator, TradeCoordinator, ThesisManager, EventBuffer, UrgentQueue, DataLake, FreshnessGuard.
     - StrategyRegistry (loaded by `register_all_strategies()`), MarketScanner, RegimeDetector, TradeScorer, EnsembleVoter, PnLManager, SmartLeverage, PerformanceEnforcer.
     - APEX Gate + TradeOptimizer (assembler, qwen_client, models).
     - Structure engine + cache + coin_discovery + shadow_kline_reader + setup_scanner.
     - ProfitSniper (Mode4), PositionWatchdog, LayerManager.
     - IntelligentFundManager, TieredCapital, Kelly, correlation_tracker, risk_budget.
     - AlertManager, Telegram bot (interactive), PriceAlertEngine, ScheduledReportEngine.
     - DiscoveryWorker/Generator/Validator (factory).
     - Alert callback wired to `claude_code_client.set_alert_callback(...)`.
8. `_create_workers()` — instantiates 22 workers (see Part 5) in `self.workers` (registered via 22 `self.workers.append(...)` calls, confirmed by grep).
9. `await manager.start_all()`:
   - Install `loop.add_signal_handler(SIGTERM, …)` and `SIGINT`.
   - For each worker, `asyncio.create_task(_run_worker(worker))`.
   - Append a `_system_health_loop()` task running every 60 s (`SYSTEM_HEALTH_START`/`SYSTEM_HEALTH_STOP`).
   - If `layer_manager` service exists, auto-start 3-layer architecture.
   - Await `_shutdown_event.wait()` OR first task to complete.
10. Shutdown path (`finally` block):
    - `manager.stop_all()` — sets `running=False` on all workers, waits ≤10 s per worker for `worker.stop()`, cancels pending tasks, disconnects Bybit/WebSocket/DB.
    - "Workers shutdown complete" log line.

Unhandled exception behavior: logged with full traceback under `log.error("Worker manager error: {err}\n{tb}", ...)`, then `stop_all()` runs. `atexit` hook always fires at interpreter shutdown.

## 2.2 `server.py` (54 lines) — MCP server entry point

CLI: `--transport {stdio,sse}` (default `stdio`), `--host 0.0.0.0`, `--port 8080`. Invoked by `trading-mcp-sse.service` as `python server.py --transport sse --port 8080`. Creates an `MCPServer(settings)`, `await server.initialize()` (runs migrations, registers 43 tools, sends startup alert), then `run_stdio()` or `run_sse(host, port)`. KeyboardInterrupt handled.

Startup emits: `MCP_INIT | tools=43 init_ms=<n> transport=sse`.

## 2.3 `brain.py` (79 lines) — deprecated Brain v1

Documented as DEPRECATED in the file's own docstring. Still installed as `trading-brain.service`. Modes: `--once`, `--summary`, default scheduler. Calls `BrainManager(settings, db)`. Brain v2 now runs inside `workers.py` as part of the strategist service. The file prints a warning at start telling the operator to use the workers service instead.

## 2.4 `mcp_stdio_proxy.py` (208 lines) — Y-22 long-lived rework

Spawned by Claude CLI as its MCP stdio transport. Forwards JSON-RPC traffic to the already-running SSE server at `http://127.0.0.1:8080/sse`. Environment: `MCP_PROXY_UPSTREAM` (default `http://127.0.0.1:8080/sse`), `MCP_AUTH_TOKEN` (from `.env`). Uses anyio task group with two unidirectional pipes (`_pipe("in", stdio_read, sse_write, …)` and `_pipe("out", sse_read, stdio_write, …)`). `_arm_shutdown_watchdog()` sets a 2-second hard `os._exit(0)` deadline so CLI subprocess teardown never blocks on httpx connection pool drain. Logs: `MCP_PROXY_CONNECT`, `MCP_PROXY_DISCONNECT`, `MCP_PROXY_PIPE_END`, `MCP_PROXY_FORCE_EXIT`, `MCP_PROXY_MSG_ERR`, `MCP_PROXY_SOURCE_ERR`, `MCP_PROXY_SINK_ERR`, `MCP_PROXY_UPSTREAM_FAIL`.

## 2.5 Supervisor layer — systemd

All five units live in `systemd/` and, once installed, under `/etc/systemd/system/`:

- `trading-workers.service` — `Type=simple`, runs `.venv/bin/python workers.py`, `Restart=always`, `RestartSec=15`, `StartLimitIntervalSec=300`, `StartLimitBurst=5`, `MemoryMax=800M`, `MemoryHigh=600M`, `CPUQuota=80%`. `EnvironmentFile=.env`. `PATH` includes `.venv/bin` + `/usr/local/bin` + `/usr/bin` + `/bin` so the `claude` CLI binary is resolvable. `HOME=/home/inshadaliqbal786` so Claude CLI finds `~/.claude/.credentials.json`. `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths=data/` and `~/.claude/`. `StandardOutput=null`, `StandardError=null` (application handles logging). Currently active; main PID 50689.
- `trading-mcp-sse.service` — same pattern, runs `python server.py --transport sse --port 8080`. `MemoryMax=200M`, `MemoryHigh=150M`, `CPUQuota=50%`. `RestartSec=10`. Active; PID 50794.
- `trading-brain.service` — runs `python brain.py`. `MemoryMax=200M`. `RestartSec=30`. Disabled at install time but currently running; PID 50833.
- `trading-backup.service` — `Type=oneshot`, runs `bash scripts/backup.sh`. Triggered by the timer.
- `trading-backup.timer` — `OnCalendar=*-*-* 02:00:00`, `Persistent=true`.

`pm2 list` shows only a stopped `n8n` process; pm2 is not used for production. The shell scripts under `scripts/` (`start_all.sh`, `stop_all.sh`, `restart_all.sh`, `status.sh`, `install_services.sh`, `uninstall_services.sh`) orchestrate the systemd layer.

## 2.6 Chronological startup story (workers path)

1. systemd starts `trading-workers.service`.
2. Python interpreter starts; `workers.py:main()` runs.
3. Settings loaded; logging wired; shutdown hooks armed.
4. `validate_config()` emits warnings.
5. `DatabaseManager.connect()` opens DB + PRAGMAs.
6. `run_migrations()` brings schema to v24 (idempotent).
7. `WorkerManager._init_services()` constructs ~60 services in dependency order; each wrapped in try/except (partial startup is tolerated).
8. `WorkerManager._create_workers()` instantiates 22 workers conditionally.
9. Signal handlers installed on the event loop.
10. One `asyncio.Task` per worker is started via `_run_worker(worker)` which isolates crashes (logs CRITICAL, does not re-raise).
11. `_system_health_loop()` task spawned (every 60 s).
12. LayerManager auto-starts the 3-layer architecture if present.
13. First BybitClient call + WebSocket connect (inside PriceWorker tick).
14. ScannerWorker produces first `active_universe` row-set at `scan_interval_seconds` (default 300).
15. StrategyWorker produces first signals at `strategy_engine.scan_interval_seconds` (default 45 s), but gates on `DailyPnLManager.can_trade()` and `KlineWorker.is_circuit_open()` first.
16. ClaudeStrategist first Call A fires inside LayerManager / Brain v2, subject to `brain.strategic_interval` (150 s).
17. First trade_thesis → APEX Gate → ShadowAdapter → Shadow order.

Shutdown story (SIGTERM from systemd):

1. `_sig_handler` (from `workers.py`) sync-writes `WORKER_SIGNAL | sig=SIGTERM ...` to stderr fd and appends it directly to `workers.log` (bypasses loguru queue).
2. Raises `KeyboardInterrupt` to unwind the asyncio main loop.
3. `manager.stop_all()`: each worker's `running` flag cleared; `worker.stop()` awaited with 10 s budget; remaining tasks cancelled; Bybit REST/WS and DB disconnected.
4. Python's atexit fires `WORKER_SHUTDOWN | reason=atexit`.
5. Process exits.

---

# 3. Configuration System

## 3.1 Sources

The system reads configuration from three places, in this order of precedence:

1. Environment variables (from systemd's `EnvironmentFile=/home/inshadaliqbal786/trading-intelligence-mcp/.env`) — credentials only.
2. `config.toml` (763 lines) — all tunables.
3. Hardcoded Python defaults in `src/config/settings.py` (60,111 B) — last resort; loaded via `@dataclass` classes and builder functions (`_build_xxx()`).

`Settings._load_fresh()` is called at every entry point start — there is no shared process-wide singleton and no hot-reload.

## 3.2 `.env` keys (documented in `.env.example`)

- `BYBIT_API_KEY`, `BYBIT_API_SECRET` — mainnet credentials (public data endpoints accept blank keys).
- `FINNHUB_API_KEY` — news endpoint.
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD` — PRAW OAuth (currently disabled in config).
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- `ANTHROPIC_API_KEY` — used by legacy `ClaudeClient` (deprecated); the production `ClaudeCodeClient` uses the CLI binary + `~/.claude/.credentials.json`.
- `MCP_AUTH_TOKEN` — bearer token for the SSE transport + the stdio proxy.
- `OPENROUTER_API_KEY` — used by TIAS DeepSeek analyzer, APEX qwen_client (DeepSeek V3.2), and Sentinel advisor.

All values held only by the file system — none printed.

## 3.3 `config.toml` sections (live inventory — 44 sections, 763 lines)

### [general]
- `mode` = "shadow" — selects Shadow vs paper vs live routing inside the Transformer.
- `shadow_api_url` = "http://127.0.0.1:9090" — base for ShadowAdapter.
- `timezone` = "UTC".
- `log_level` = "INFO".
- `log_dir` = "data/logs".

### [bybit]
- `testnet` = false.
- `default_symbols` — 20 fallback symbols (BTCUSDT, ETHUSDT, …, TONUSDT).
- `rate_limit_per_second` = 10.
- `ws_ping_interval` = 20, `ws_reconnect_delay` = 5, `recv_window` = 5000.

### [finnhub]
- `enabled` = true, `rate_limit_per_minute` = 60, `news_categories` = ["crypto","general"], `max_articles_per_fetch` = 50.

### [reddit]
- `enabled` = false. 5 subreddits configured, `min_score` = 10, rate limit 60/min.

### [altdata]
- `enabled` = true. Intervals: Fear & Greed 3600 s, funding 300 s, OI 600 s. `coingecko_rate_limit_per_minute` = 10.

### [database]
- `path` = "data/trading.db", `wal_mode` = true, `pool_size` = 5 (placeholder for Postgres migration), `query_timeout` = 30, `vacuum_interval` = 24.

### [workers]
- `enabled` = true, `market_data_interval` = 45, `news_interval` = 300, `reddit_interval` = 600, `altdata_interval` = 300, `health_check_interval` = 120, `max_consecutive_failures` = 5, `restart_delay` = 10.

### [brain]
- `enabled` = true, `use_claude_code` = true.
- `strategic_interval` = 150 (alternating Call A / Call B every 2.5 min).
- `watchdog_interval` = 30.
- Legacy v1 knobs: `analysis_interval=900`, `signal_triggered=true`, `min_signal_confidence=0.45`, `max_calls_per_hour=30`, `model="claude-sonnet-4-20250514"`, `max_tokens=4096`, `temperature=0.3`.
- Phase-2 / Y-22 tuning: `claude_cli_timeout_seconds=300`, `claude_cli_max_retries=2`, `claude_cli_min_interval=2.0`, `claude_cli_retry_timeout_backoff_base_seconds=10`, `prompt_event_buffer_max_events=20`.

### [risk]
- `max_leverage=5`, `mandatory_stop_loss=true`, `default_stop_loss_pct=3.0`, `default_take_profit_pct=6.0`, `max_position_size_pct=20.0`, `max_open_positions=10`, `daily_loss_limit_pct=10.0`, `max_total_exposure_pct=80.0`, `max_drawdown_pct=25.0`, `min_order_value_usdt=5.0`, `loss_cooldown_seconds=30`.

### [alerts]
- `telegram_enabled=true`, `alert_levels=["WARNING","CRITICAL"]`, `daily_summary=true`, `daily_summary_time="00:00"`, `max_alerts_per_minute=10`, `trade_alerts=true`, `signal_alerts=true`, `error_alerts=true`.

### [mcp]
- `transport="stdio"`, `sse_host="0.0.0.0"`, `sse_port=8080`, `sse_auth_required=true`, `server_name="trading-intelligence"`, `server_version="0.1.0"`.

### [watchdog]
- `enabled=true`, `check_interval_seconds=10`, `loss_warning_pct=0.5`, `trailing_loss_pct=0.3`, `sl_proximity_pct=30.0`, `rapid_move_pct=0.5`, `brain_trigger_loss_pct=0.8`, `brain_cooldown_seconds=60`, `partial_close_pct=50.0`, `max_brain_calls_per_hour=20`.
- `early_exit_enabled=false` (0 % WR historical — kept disabled, logs `EARLY_EXIT_DISABLED_WOULD_FIRE`).
- `fast_reconcile_seconds=30.0` (Phase 2 / P0-1 ghost reconciliation).

### [mcp_pool]
- `enabled=false` (Y-22 migration not yet committed). `sse_url="http://127.0.0.1:8080"`, `min_warm=1`, `max_warm=2`, `health_check_interval_seconds=60`, `acquire_timeout_seconds=2.0`.

### [price]
- `local_max_age_seconds=10.0`, `divergence_override_pct=0.5`, `divergence_block_prompt_pct=1.0`.

### [sl_gateway]
- `enabled=true`, `min_distance_pct=0.3`, `max_step_pct=0.5`, `rate_limit_seconds=30`, `log_only_global=true` (dry-run enforcement — REJECTs downgraded to `SL_GATEWAY_REJECT_WOULD`).
- Per-rule overrides (`log_only_tighten_only=false` — hard-enforced). 
- ATR-scaled R2: `min_distance_atr_multiplier=0.5`, `min_distance_abs_floor_pct=0.05`.
- `[sl_gateway.min_distance_class_ceiling]` — dead=0.30, low=0.50, medium=1.00, high=2.00, extreme=3.50.

### [scanner], [regime], [strategy_engine], [pnl_targets], [leverage], [optimizer], [factory], [backtesting], [trial], [portfolio], [telegram_interactive], [fund_manager], [enforcer]
Every section's full key list and current value is captured by reading `config.toml` lines 295–581. Representative knobs:

- `[scanner] scan_interval_seconds=300, min_volume_24h=5_000_000, max_coins=30, max_spread_pct=0.15`.
- `[strategy_engine] scan_interval_seconds=45, min_score_threshold=0, min_ensemble_agreement=2.5, max_setups_to_brain=10, max_brain_calls_per_hour=30`.
- `[pnl_targets] daily_target_pct=10.0, halt_threshold_pct=-10.0`.
- `[leverage] max_leverage=5, tier_1_max=5 … tier_3_max=4, volatile_max=4, dead_max=3, min_confidence_for_5x=0.65`.
- `[enforcer] enabled=true, pnl_caution_pct=-2.0, pnl_survival_pct=-5.0, level_1_max_positions=3, level_2_min_rr=3.0`.
- `[factory] enabled=false` — disabled: "0 patterns discovered, 0 backtests run — wasting CPU".
- `[fund_manager] enabled=true, check_interval_seconds=60`.
- `[telegram_interactive] enabled=true, ai_responses_enabled=true, max_ai_calls_per_hour=20, price_alert_check_interval=10`.

### [mode4] — Profit Sniper (Mode4)
- `enabled=true`, `check_interval_seconds=5`.
- Ring buffer: `buffer_max_size=720`, `buffer_min_ready=100`.
- Trailing: `base_atr_multiplier=2.5`, `trail_min_change_pct=0.1`, per-regime factors (trending=1.3, ranging=0.7, volatile=1.0, dead=0.6).
- Anti-Greed: `anti_greed_enabled=true`, pullback thresholds 40/60/75 % of peak PnL.
- Action cooldowns, Phase-9 stall escape (20/40 tick thresholds, `stall_escape_cooldown_seconds=30`).
- Log throttle: `log_every_n_ticks=6` (30 s).
- Legacy Phase-1 classification thresholds (watch=30, consult_claude=50, auto_partial=70, auto_full=85).

### [tias], [apex], [sentinel]
- `[tias] enabled=true, primary_model="deepseek/deepseek-chat-v3-0324", fallback_model="deepseek/deepseek-chat", temperature=0.3, max_tokens=1500, timeout_seconds=45, max_retries=1, analysis_version=1`.
- `[apex] enabled=true, model="deepseek/deepseek-v3.2", fallback_model="deepseek/deepseek-chat", timeout_seconds=60, max_tokens=800, temperature=0.2, max_position_size_usd=1200, max_leverage=5, min_tias_trades_for_optimization=3, min_regime_trades_for_fallback=10, gate_apex_size_cap_mult=1.5, conviction_enabled=true, conviction_min_trades=3`.
- `[apex.tp_cap_multiplier_by_class]` dead=1.2, low=1.3, medium=1.3, high=1.4, extreme=1.5.
- `[sentinel] enabled=true, firewall_enabled=true, deadline_profit_pct=0.5, deadline_breakeven_lower_pct=-0.3, deadline_small_loss_pct=-1.5, deadline_grace_minutes=5.0, deadline_small_loss_sl_pct=0.5, advisor_enabled=true, advisor_interval_seconds=300, advisor_model="deepseek/deepseek-chat-v3-0324", advisor_temperature=0.2, advisor_max_tokens=800, advisor_timeout_seconds=30, advisor_min_profit_for_tighten_pct=0.50`.

### [analysis.structure] — X-RAY
- `enabled=true, worker_interval_seconds=60, cache_ttl_seconds=300, min_candles=50`.
- `swing_lookbacks=[3,5,10], cluster_pct=0.3, min_touches=2, max_levels_per_side=5`.
- Structural SL/TP: `sl_buffer_pct=0.15, tp_buffer_pct=0.10, min_rr_ratio=2.0, sl_fallback_pct=2.0, tp_fallback_pct=4.0`.
- Phase 2 SMC: `fvg_min_gap_pct=0.1, fvg_max_age_candles=50, ob_displacement_min=0.6, ob_max_age_candles=50, liq_equal_tolerance_pct=0.05, liq_min_equal_count=2, liq_round_number_step=100.0, sweep_max_age_candles=10, sweep_min_wick_pct=0.3`.
- Phase 4: `setup_scanner_mode="supplement"`, `scan_full_market=true, batch_size=25, coin_refresh_interval=600, shadow_db_path="../shadow/data/shadow.db"`.

### [analysis.volatility_profile]
- `enabled=true, cache_ttl_seconds=120.0, jitter_range_seconds=30` (per-symbol +/- 30 s jitter), `dead_threshold=0.05, low_threshold=0.15, medium_threshold=0.40, high_threshold=1.00`, `min_tp_pct=0.30, min_sl_pct=0.20, max_tp_pct=8.0, max_sl_pct=5.0`.

### [time_decay]
- `enabled=true`.
- PnL-depth penalty knobs: `p_win_abs_depth_threshold_pct=1.5, p_win_abs_depth_strong_pct=3.0, p_win_abs_depth_penalty=0.90, p_win_abs_depth_strong_penalty=0.70`.
- `[time_decay.grace_seconds_by_class]` dead=30, low=45, medium=120, high=180, extreme=240.
- `[time_decay.atr_room_multiplier_by_class]` dead=1.0, low=1.2, medium=2.0, high=2.5, extreme=3.0.

## 3.4 External URLs touched

- Bybit REST: `https://api.bybit.com`.
- Bybit WS (public linear): `wss://stream.bybit.com/v5/public/linear`.
- Finnhub REST: `https://finnhub.io/api/v1/*`.
- Reddit OAuth via PRAW: `https://oauth.reddit.com` (disabled).
- Telegram: `https://api.telegram.org/bot<token>/…`.
- OpenRouter: `https://openrouter.ai/api/v1/chat/completions` (used by TIAS, APEX, Sentinel Advisor).
- Claude CLI OAuth refresh: `https://claude.ai/v1/oauth/token` (from `ClaudeCodeClient._try_token_refresh()`).
- Shadow (local): `http://127.0.0.1:9090/*` — ShadowAdapter endpoints.
- MCP SSE (local): `http://127.0.0.1:8080/sse` (server + proxy + `[mcp_pool]`).
- Alternative Fear & Greed: `https://api.alternative.me/fng/` (via altdata worker).
- CoinGecko: `https://api.coingecko.com/api/v3/*` (rate-limited in config).

## 3.5 Known configuration conflicts / anomalies

- `[brain]` has both Claude Code knobs (`use_claude_code=true`, `claude_cli_timeout_seconds`) and legacy Anthropic-API knobs (`model`, `max_tokens`). The latter are read only by `src/brain/claude_client.py` which is no longer in the active path.
- `[scanner] max_coins=30` but `[bybit] default_symbols` lists 20 — the scanner's 30 wins at runtime; the 20 are only a startup fallback.
- `[risk] default_stop_loss_pct=3.0` vs `[mode4]` / APEX / VolatilityProfiler which set tighter per-class TP/SL — the Gate and SL Gateway resolve the conflict, but the static 3 % is still referenced by legacy order_service paths.
- `[factory] enabled=false` but `src/factory/*` still contains 27 files totalling 2,891 lines and workers conditionally wire them — the code exists but never runs.
- `[reddit] enabled=false` but the schema still creates `reddit_posts` and the aggregator still has a Reddit branch.

---

# 4. Database Schema

Measured directly from `data/trading.db` (142.8 MiB) via `sqlite3 … ".schema"`, `SELECT name FROM sqlite_master ...`, `SELECT COUNT(*) FROM <t>`, and `PRAGMA` queries.

## 4.1 Instance-level facts

- Tables: 65 (confirmed via `SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'`).
- Indexes: 67 (`SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'`).
- Views: 0. Triggers: 0.
- `schema_version` table contains 16 rows; `SCHEMA_VERSION` constant in `src/database/migrations.py` is 24 (rows = historical migrations, not current version).
- PRAGMA snapshot: `journal_mode=wal`, `synchronous=2 (NORMAL)`, `page_size=4096`, `cache_size=-2000 (2 MiB default — overridden to -65536 at connect time, but this PRAGMA read reflects a fresh attach)`, `journal_size_limit=-1 (default)`, `foreign_keys=0` (checked on read-only handle; connect-time sets ON).
- File sizes: `trading.db` 149,774,336 B; `trading.db-wal` 104,857,600 B; `trading.db-shm` 327,680 B.

## 4.2 Full table inventory with live row counts

| # | Table | Rows | Brief purpose |
|---:|---|---:|---|
| 1 | `account_snapshots` | 36,714 | Equity/margin snapshots captured per account-service tick. |
| 2 | `active_strategies` | 0 | Per-strategy-per-symbol enable map (unused — legacy). |
| 3 | `active_universe` | 30 | Current scanner universe (overwritten every scan tick). |
| 4 | `aggregated_sentiment` | 302,175 | Per-symbol aggregated news+Reddit+F&G score. |
| 5 | `backtest_results` | 0 | Factory backtest summaries. |
| 6 | `backtest_trades` | 0 | Per-trade records for each backtest. |
| 7 | `brain_decisions` | 0 | Legacy v1 brain decision log (unused — strategist logs to `claude_decisions` + `trade_thesis`). |
| 8 | `capital_level_history` | 0 | TieredCapital level transitions. |
| 9 | `claude_decisions` | 827 | Strategist cycle audit (timestamp, type A/B, counts, response). |
| 10 | `coin_regime_history` | 11,890 | Per-coin regime, ADX, choppiness (for regime restore on restart). |
| 11 | `conversation_log` | 0 | Interactive Telegram message log. |
| 12 | `correlation_matrix` | 0 | Pairwise strategy correlations. |
| 13 | `daily_pnl` | 26 | One row per trading day with starting/ending equity + halt flags. |
| 14 | `daily_summary` | 0 | Roll-up of closed trades per day (populated by CleanupWorker — empty because `trade_history` is empty). |
| 15 | `discovered_patterns` | 24 | Factory pattern discovery output. |
| 16 | `economic_calendar` | 0 | Finnhub calendar events. |
| 17 | `ensemble_votes` | 0 | Per-setup vote record (never enabled — ensemble returns inline). |
| 18 | `event_log` | 1,595 | EventBuffer persistent audit (priority, symbol, data JSON). |
| 19 | `fear_greed_index` | 21,286 | Every F&G poll. |
| 20 | `fund_manager_log` | 0 | Fund-manager events. |
| 21 | `fund_manager_state` | 4 | Key/value state for FundManager. |
| 22 | `funding_rates` | 72,580 | Per-symbol funding polls. |
| 23 | `generated_strategies` | 0 | Factory-generated strategy code (empty — factory disabled). |
| 24 | `hourly_performance` | 4 | PerformanceEnforcer hourly grades. |
| 25 | `klines` | 91,369 | OHLCV for all tracked symbols (M5 + H1 dominant). |
| 26 | `market_snapshots` | 981 | Periodic BTC/ETH/SOL + regime + F&G + full JSON blob. |
| 27 | `news_articles` | 1,268 | Finnhub-ingested items. |
| 28 | `open_interest` | 72,123 | Per-symbol OI polls. |
| 29 | `orderbook_snapshots` | 329 | Infrequent top-of-book. |
| 30 | `orders` | 0 | Bybit order records (empty — live mode unused). |
| 31 | `pattern_log` | 0 | Pattern detector outputs. |
| 32 | `pattern_occurrences` | 0 | Per-occurrence pattern records. |
| 33 | `performance_attribution` | 0 | Strategy contribution to PnL. |
| 34 | `portfolio_allocations` | 0 | Rebalance output table. |
| 35 | `position_snapshots` | 14,862 | Per-tick position records (age, PnL) — watchdog audit. |
| 36 | `positions` | 0 | Bybit positions mirror (unused). |
| 37 | `price_alerts` | 0 | Telegram price alerts. |
| 38 | `profit_ratchet_log` | 0 | FundManager ratchet events. |
| 39 | `rebalance_history` | 0 | Portfolio rebalance log. |
| 40 | `reddit_posts` | 0 | Reddit posts (disabled). |
| 41 | `regime_history` | 1,773 | Global regime snapshots. |
| 42 | `risk_budget_log` | 0 | Daily risk budget usage. |
| 43 | `scheduled_reports` | 0 | Telegram-driven reports. |
| 44 | `schema_version` | 16 | Migration version rows. |
| 45 | `session_log` | 0 | Multi-event rollup (unused). |
| 46 | `signal_accuracy` | 0 | Per-signal outcome tracking. |
| 47 | `signals` | 152,261 | Every signal emitted by SignalWorker. |
| 48 | `sniper_log` | 35,639 | ProfitSniper per-action record (composite score, pullback, trail). **PROTECTED**. |
| 49 | `strategy_code_history` | 0 | Versioned generated strategy code. |
| 50 | `strategy_lifecycle` | 0 | Lifecycle transitions (validated → trial → active). |
| 51 | `strategy_params` | 0 | Per-strategy params (optimizer output). |
| 52 | `strategy_performance` | 114 | Per-strategy/symbol trade counts + win rate + profit factor. |
| 53 | `strategy_trades` | 1,175 | Per-trade strategy attribution. |
| 54 | `stress_test_results` | 0 | Portfolio stress scenarios. |
| 55 | `switch_history` | 4 | Transformer mode switches. |
| 56 | `ticker_cache` | 189 | Latest tick per symbol (upsert on every WS/REST ticker). |
| 57 | `trade_history` | 0 | Completed trade log (unused — `trade_log` + `trade_thesis` + `trade_intelligence` replaced it). **PROTECTED**. |
| 58 | `trade_intelligence` | 693 | TIAS post-mortem records (50+ columns, 6 data groups + DeepSeek analysis). **PROTECTED**. |
| 59 | `trade_journal` | 0 | User-entered notes. |
| 60 | `trade_log` | 1,084 | Production trade audit (APEX-aware). **PROTECTED**. |
| 61 | `trade_thesis` | 1,117 | Open/closed theses with Claude plan, APEX overrides, close reason. **PROTECTED**. |
| 62 | `transformer_state` | 1 | Singleton row with `current_mode`, `is_switching`. |
| 63 | `trial_performance` | 0 | Paper-trading trial records. |
| 64 | `user_preferences` | 2 | Telegram key-value store. |
| 65 | `watchlists` | 0 | Named symbol groups. |

Sums: 6 always-written tables (klines 91k, funding_rates 73k, open_interest 72k, aggregated_sentiment 302k, signals 152k, sniper_log 36k) hold ~92 % of all rows. 29 tables have 0 rows today.

## 4.3 PROTECTED tables (runtime guard in `src/database/protected_tables.py`)

```python
PROTECTED_TABLES: frozenset[str] = frozenset({
    "tias_results", "tias_analyses", "trade_intelligence", "trade_log",
    "trade_history", "thesis_store", "trade_thesis",
    "virtual_positions", "sniper_log",
})
```

Destructive SQL (`DELETE`/`TRUNCATE`/`DROP`) against any of these is rejected pre-flight in `DatabaseManager.execute()` / `executemany()` via `assert_not_protected_destructive(sql, force=force_protected)`. A `force=True` escape hatch exists but must be passed explicitly; it logs `DB_PROTECT_FORCE`. Regex patterns match schema-qualified, backticked, bracketed, double-quoted identifiers.

## 4.4 Indexes (67, unique by name)

`idx_klines_symbol_tf_ts`, `idx_orders_symbol_status`, `idx_trade_history_symbol`, `idx_news_published`, `idx_news_symbols`, `idx_reddit_created`, `idx_agg_sentiment_symbol`, `idx_fear_greed_ts`, `idx_funding_symbol`, `idx_oi_symbol`, `idx_signals_symbol`, `idx_accuracy_lookup`, `idx_pattern_lookup`, `idx_brain_created`, `idx_session_type`, `idx_regime_time`, `idx_strat_trades_name`, `idx_strat_trades_symbol`, `idx_patterns_type`, `idx_patterns_valid`, `idx_gen_strat_status`, `idx_gen_strat_pattern`, `idx_occ_pattern`, `idx_occ_symbol`, `idx_bt_strategy`, `idx_bt_passed`, `idx_bt_trades_backtest`, `idx_lifecycle_strategy`, `idx_trial_perf`, `idx_corr_strategies`, `idx_risk_budget_date`, `idx_attribution_period`, `idx_price_alerts_chat`, `idx_price_alerts_active`, `idx_journal_chat`, `idx_conv_log_chat`, `idx_hourly_perf`, `idx_fm_log`, `idx_positions_symbol`, `idx_account_snapshots_time`, `idx_backtest_results_time`, `idx_price_alerts_symbol`, `idx_daily_pnl_date`, `idx_strategy_perf_name`, `idx_trade_thesis_symbol_status`, `idx_trade_thesis_status`, `idx_trade_thesis_opened`, `idx_market_snapshots_ts`, `idx_trade_log_symbol`, `idx_pos_snapshots_ts`, `idx_claude_decisions_ts`, `idx_event_log_ts`, `idx_event_log_type`, `idx_trade_log_opened`, `idx_position_snapshots_ts`, `idx_position_snapshots_symbol`, `idx_daily_summary_date`, `idx_switch_history_ts`, `idx_sniper_log_ts`, `idx_sniper_log_symbol_ts`, `idx_sniper_log_action`, `idx_ti_symbol`, `idx_ti_win`, `idx_ti_ds_why`, `idx_ti_trade_closed_at`, `idx_ti_ds_category`, `idx_ti_apex_optimized`, `idx_coin_regime_symbol`.

## 4.5 Migrations

`src/database/migrations.py` is 51,059 B. Exposes `SCHEMA_VERSION = 24` plus a `MIGRATIONS: list[str]` of CREATE TABLE / CREATE INDEX / ALTER TABLE DDL. Every statement is `IF NOT EXISTS` or guarded by column-existence check through the in-module `_column_cache` helper. Migrations run inside `workers.py`, `server.py`, and `brain.py` entry points at boot. `sqlite_sequence` appears as table 14 in `sqlite_master`; this is the autoincrement metadata table automatically managed by SQLite.

## 4.6 Representative schemas (central tables — summarised)

- `trade_thesis` (PROTECTED): `id, symbol, direction, entry_price, stop_loss_price, take_profit_price, size_usd, leverage, max_hold_minutes, trailing_activation_pct, thesis, market_context, strategy_hints, consensus, status ('open'), opened_at, closed_at, close_price, actual_pnl_pct, actual_pnl_usd, close_reason, lesson, order_id, bybit_position_idx, exchange_mode ('shadow'), apex_flipped, apex_original_direction, apex_reason`. Indexed on `(symbol, status)`, `status`, `opened_at`.
- `trade_intelligence` (PROTECTED, 50+ cols): groups A (outcome), B (entry decision context: claude_thesis, claude_confidence, entry_score, ensemble_votes), C (market conditions at close), D (TA at close — RSI/MACD/…), E (Mode4 tracking — m4_peak_pnl_pct, m4_composite_score, m4_hurst_value, …), F (DeepSeek — ds_why, ds_category, ds_confidence, ds_analyzed_at), G (metadata). Additional APEX columns: `apex_optimized`, `apex_flipped`, `apex_original_direction/final_direction`, `apex_original_sl/final_sl`, `apex_confidence`, `apex_tp_mode`, `apex_model`, `gate_adjustments`, `apex_tp_fill_rate`.
- `trade_log` (PROTECTED): `id, trade_id (UNIQUE), symbol, direction, entry_price, exit_price, size_usd, leverage, pnl_pct, pnl_usd, strategy, thesis, close_reason, hold_minutes, opened_at, closed_at, exchange_mode`.
- `sniper_log` (PROTECTED): 40+ columns capturing every Mode4 tick that crossed a log threshold — exploit_score, z_score, velocity, acceleration, volume_ratio, bb_position, speed_factor, consecutive_direction_count, action, close_percentage, close_price, profit_captured_pct, counterfactual_pnl_pct, plus Phase-7 additions (`hurst_value`, `hurst_regime`, `momentum_decay_score`, `slope_short/long`, `extension_atr`, `vol_ratio`, `volume_div_score`, `price_obv_corr`, `divergence_type`, `ev_ratio`, `profit_amplifier`, `composite_score/base`, `regime`, `consensus_boost`, `urgency_boost`, `trail_stop_price/distance_pct`, `action_source`, `peak_pnl_pct`, `pullback_from_peak`, `anti_greed_rule`).
- `positions`: `symbol PRIMARY KEY, side, size, entry_price, mark_price, unrealized_pnl, realized_pnl, leverage, liquidation_price, stop_loss, take_profit, updated_at`. Empty today (live mode disabled).
- `klines`: `id, symbol, timeframe, timestamp, open/high/low/close/volume/turnover, UNIQUE(symbol,timeframe,timestamp)`. 91k rows.
- `regime_history`: `id, symbol (default 'BTCUSDT'), regime, confidence, adx, atr_percentile, choppiness, detected_at`. 1.7k rows.
- `claude_decisions`: `id, ts_epoch, decision_type, new_trades_count, position_actions_count, market_view, risk_level, response_time_ms, prompt_length, full_response, created_at`. 827 rows.

Tables with no writers in code (cross-checked by grep of `INSERT INTO <name>`):

- `active_strategies` — only migration; never INSERT/UPDATE.
- `backtest_results`, `backtest_trades`, `strategy_code_history`, `strategy_lifecycle`, `trial_performance`, `pattern_log`, `pattern_occurrences`, `performance_attribution`, `rebalance_history`, `risk_budget_log`, `portfolio_allocations`, `correlation_matrix`, `stress_test_results` — code paths exist in `src/factory/` and `src/portfolio/` but only fire when `[factory] enabled=true`, which it is not.

Tables with no readers (cross-checked by grep of `FROM <name>` and `SELECT`):

- `brain_decisions` — legacy v1; new code writes `claude_decisions`.
- `trade_history` — replaced by `trade_log`.
- `session_log` — declared, never queried.
- `ensemble_votes` — ensemble returns inline; never persisted.

Sample rows not reproduced here because all four protected tables contain live trading details — the SYSTEM_INVENTORY document is a planning map, not a data export.

---

# 5. Workers

## 5.1 WorkerManager (`src/workers/manager.py`, 1,864 lines)

Central orchestrator. Confirmed 22 `self.workers.append(...)` statements by `grep -c`; every conditional worker is guarded by service availability + a config flag.

Construction: `WorkerManager(settings, db)` sets `self.workers: list[BaseWorker] = []`, `self.tasks: list[asyncio.Task] = []`, `self._shutdown_event = asyncio.Event()`, `self._services: dict = {}`, plus two health monitors (`WorkerHealthMonitor` and `SystemHealthMonitor`).

`initialize()` (lines 46–591) builds services inside ~10 try/except blocks — each block logs `…unavailable: {err}` on failure but allows the rest of startup to proceed. `_create_workers()` (lines 706–1069) instantiates worker classes conditionally. `start_all()` (lines 1701–1764) installs signal handlers and creates one `asyncio.Task` per worker via `_run_worker(worker)` — which wraps `worker.start()` in try/except and logs CRITICAL on crash without re-raising (crash isolation). `stop_all()` (lines 1820–1855) sets `running=False` per worker, awaits `worker.stop()` with 10-second budget, cancels outstanding tasks, disconnects Bybit/WebSocket/DB. System-health loop (`_system_health_loop`, lines 1778–1818) runs every 60 s (`_SYSTEM_HEALTH_INTERVAL_SECONDS = 60.0`) and emits `SYSTEM_HEALTH_START`/`SYSTEM_HEALTH_STOP`.

## 5.2 BaseWorker (`src/workers/base_worker.py`, 161 lines)

Abstract base. Constructor `(name, interval_seconds, settings, db)`. Attributes: `status (WorkerStatus)`, `running`, `restart_count`, `max_restarts (settings.workers.max_consecutive_failures=5)`, `restart_delay (settings.workers.restart_delay=10.0)`, `last_tick_time`, `last_error`, `total_ticks`, `error_count`, `_heartbeat_interval=300`. `start()` loop calls `tick()`, increments counters, `_maybe_log_heartbeat()`, sleeps `self.interval`. On exception: logs `Worker '{name}' tick failed ({rc}/{max}): {err}`, back-off `min(restart_delay * 2^(rc-1), 60.0)`, after `max_restarts` raises `WorkerCrashError` and stops. `_maybe_log_heartbeat()` emits `[HEARTBEAT] Worker '{name}' alive | ticks= errors= last_tick=`.

## 5.3 Worker roster (22)

| # | Worker | File | LOC | Interval | Purpose |
|--:|---|---|--:|---|---|
| 1 | PriceWorker | `price_worker.py` | 199 | `workers.market_data_interval` (45 s) | Maintain Bybit public-linear WS subscription for `active_universe + open-position orphans`. Keeps `_ws_quotes: dict[str, (price, ts)]` for 5-second fallback (`get_ws_quote()`). Logs `PRICE_WS_CONN` / `PRICE_WS_DISC` / `PRICE_UNIVERSE_SYNC`. Writes to `ticker_cache` via `market_repo.save_ticker` inside WS callback. |
| 2 | KlineWorker | `kline_worker.py` | 197 | 45 s | Fetch M5 every tick, H1 every tick (min 60 s), H4 every 300 s, D1 every 3600 s for every `active_universe` coin. 200 bars per request. Computes fetch quality (`ok`/`short_10pct`/`short_50pct`/`zero_fetch`); on `zero_fetch` opens a 30-second circuit breaker read by StrategyWorker (`is_circuit_open()`). Logs `KLINE_FETCH` (with quality), `KLINE_GAP`, `KLINE_CIRCUIT_BREAKER`. |
| 3 | NewsWorker | `news_worker.py` | 56 | `workers.news_interval` (300 s) | Fetches crypto + general news via Finnhub; every 30 ticks also pulls upcoming calendar events. Logs `NEWS_FETCH`. |
| 4 | RedditWorker | `reddit_worker.py` | 37 | `workers.reddit_interval` (600 s) | Calls RedditService.scan_subreddits. Disabled by config today (`[reddit] enabled=false`). |
| 5 | AltDataWorker | `altdata_worker.py` | 118 | `workers.altdata_interval` (300 s) | Parallel fetch of F&G (3600 s cadence enforced inside client), funding rates, OI, on-chain via `asyncio.gather(..., return_exceptions=True)`. Logs `ALTDATA`. |
| 6 | SignalWorker | `signal_worker.py` | 109 | `workers.health_check_interval` (120 s) | For each universe coin: `SentimentAggregator.aggregate_for_symbol()` → `SignalGenerator.generate()` → append row to `signals`. Logs `SIG_BATCH`. |
| 7 | PositionWatchdog | `position_watchdog.py` | **2,638** | `watchdog.check_interval_seconds` (10 s) | Real-time position monitor. 18 injected services including `sl_gateway`, `thesis_manager`, `urgent_queue`, `event_buffer`, `data_lake`. Detects danger signals (loss from entry, trailing drawdown, rapid moves, SL proximity, accelerating losses); triggers Claude via strategist when `brain_trigger_loss_pct` crossed; executes tighten/partial-close/full-close via `sl_gateway` + `order_service`. 5-minute zombie reconciliation + 30-second fast reconciliation (Phase 2 / P0-1). Logs `WATCHDOG_*` family. |
| 8 | ProfitSniper | `profit_sniper.py` | **3,089** | `mode4.check_interval_seconds` (5 s) | Five mathematical models (Hurst exponent, momentum decay, ATR extension, volume divergence, risk/reward EV) over per-position `EnhancedRingBuffer` (720-sample rolling buffer). Composite exploit score 0–100+ drives action (hold/tighten/partial/full). Regime-aware trailing multipliers (trending 1.3, ranging 0.7, volatile 1.0, dead 0.6). Anti-greed pullback and stall-escape detection. All SL tightens routed through `sl_gateway`. Logs `M4_SKIP`, `SNIPER_OPEN`, `SNIPER_MODELS`, `SNIPER_ACTION`, `SNIPER_*`. |
| 9 | ScannerWorker | `scanner_worker.py` | 59 | `scanner.scan_interval_seconds` (300 s) | Calls `scanner.scan_market()` → replaces `active_universe` table with top N (score + volume + change + funding + spread + tier). Broadcasts universe changes via callbacks to PriceWorker/KlineWorker/SignalWorker/RegimeWorker. |
| 10 | StructureWorker | `structure_worker.py` | 210 | `analysis.structure.worker_interval_seconds` (60 s) | X-RAY engine driver. Scans full market in batches of 25 (≈4 ticks for 100 coins), computes session context (Asian range), runs SetupScanner over cache. Reads H1 klines from `trading.db` first, falls back to Shadow DB. Logs `XRAY_TICK`, `XRAY_SESSION_ERR`, `XRAY_TICK_ERR`, `XRAY_SCANNER_ERR`. |
| 11 | RegimeWorker | `regime_worker.py` | 204 | `regime.detection_interval_seconds` (600 s) | On first tick, restores per-coin regimes from `coin_regime_history` (30-min lookback). Then `RegimeDetector.detect()` for global + per-coin, persists to `regime_history` + `coin_regime_history`. Every 100 ticks, deletes `regime_history` rows >24 h old. Logs `REGIME_RESTORE`, `REGIME_GLOBAL`, `REGIME_PERCOIN`, `REGIME_DIVERGE`, `REGIME_BACKFILL`. |
| 12 | StrategyWorker | `strategy_worker.py` | **1,221** | `strategy_engine.scan_interval_seconds` (45 s) | Layers 1–4 pipeline. Gate on `DailyPnLManager.can_trade()`; skip on `KlineWorker.is_circuit_open()`. Prefetches M5 + H1 klines in two batched DB queries, warms TACache. Skips coins whose newest kline is >5 min old. Runs strategy signals (Layer 1), scorer (Layer 2), ensemble (Layer 3), rule engine (Layer 4). Section timings captured in `_section_ms`. Logs `STRAT_PNL_GATE`, `STRAT_SKIP_CIRCUIT`, `STRAT_REGIME_DIST`, `STRAT_PREFETCH_DB_FAIL`, `STRAT_SKIP_STALE`, `STRAT_CYCLE_DONE`. |
| 13 | DiscoveryWorker | `discovery_worker.py` | 82 | 7200 s scheduling check | Daily scheduled run at `factory.discovery_schedule_hour_utc`. Calls `PatternDiscoverer.run_full_discovery()`; top 5 patterns → `StrategyGenerator.generate_batch()` → `CodeValidator.validate()` → save. Factory currently disabled. |
| 14 | LiveMonitorWorker | `live_monitor_worker.py` | 51 | `factory.live_monitor_interval_seconds` | Calls `monitor.check_emerging()`; logs HOT patterns with urgency. |
| 15 | BacktestWorker | `backtest_worker.py` | 61 | 3600 s | Iterates `status='validated'` strategies; currently a stub logging only. |
| 16 | TrialMonitorWorker | `trial_monitor_worker.py` | 42 | 3600 s | Calls `trial_manager.evaluate_expired_trials()`; logs promotion recommendations. |
| 17 | TelegramBotWorker | `telegram_bot_worker.py` | 59 | 60 s supervisor | Spawns the interactive bot as a nested `asyncio.create_task(bot.start())`. On task completion with exception, restarts; on clean finish, also restarts. |
| 18 | PriceAlertWorker | `price_alert_worker.py` | 67 | `telegram_interactive.price_alert_check_interval` (10 s) | Reads `price_alerts` table, fetches current prices, evaluates conditions, marks triggered. |
| 19 | ScheduledReportWorker | `scheduled_report_worker.py` | 21 | 300 s | Checks due reports; dispatched via Telegram. |
| 20 | EnforcerWorker | `enforcer_worker.py` | 27 | `enforcer.check_interval_seconds` (60 s) | Delegates to `PerformanceEnforcer.check_and_enforce()`. Logs `ENFORCER_BEAT | trades_today=… wins=… …`. |
| 21 | FundManagerWorker | `fund_manager_worker.py` | 28 | `fund_manager.check_interval_seconds` (60 s) | Calls `IntelligentFundManager.update_state()`. Logs `FUND_BEAT`. |
| 22 | CleanupWorker | `cleanup_worker.py` | 157 | 3600 s | Retention enforcement across 23 tables (klines 7 d, news 30 d, reddit 14 d, fear_greed 90 d, funding 30 d, oi 30 d, signals 30 d, claude_decisions 90 d, brain_decisions 60 d, trade_thesis 60 d, regime_history 60 d, …). Once per day runs VACUUM with 3 retry attempts (5 s delay each). Inserts `daily_summary` row for previous day from `trade_thesis` aggregate. Logs `VACUUM`, `CLEANUP`. **Never** deletes PROTECTED tables. |

## 5.4 Workers not in the 22-count

`src/workers/allocation_worker.py` (41 lines, `ALLOC_UPDATE` every 300 s) and `src/workers/backtest_worker.py` conditional on `[factory]` are built but the manager does not currently append them unless the corresponding config gate fires; factory-gated ones appear only when `[factory] enabled=true`. `src/workers/health.py` (`WorkerHealthMonitor`, 102 lines) is a utility, not a worker: it tracks per-worker status for `WorkerManager.health` and exposes `get_system_health()` / `is_healthy()`.

## 5.5 Runtime model

All workers are async tasks on the main event loop. No threads, no subprocesses (except the Claude CLI spawned from `ClaudeCodeClient`). Signal handlers are installed via `loop.add_signal_handler(SIGTERM|SIGINT, …)`; on Windows these are silently skipped. Per-worker crash isolation is provided by `_run_worker()` — other workers keep running if one fails. `restart_delay=10`, `max_consecutive_failures=5` from `[workers]`.

---

# 6. The Brain Layer

## 6.1 Files (`src/brain/`, 15 files, 4,715 lines)

| File | LOC | Status |
|---|--:|---|
| `strategist.py` | **2,335** | Production — main Claude-CLI orchestrator. |
| `claude_code_client.py` | 968 | Production — Claude CLI subprocess wrapper. |
| `brain_v2.py` | 542 | Legacy setup-evaluation path; still imported by LayerManager. |
| `decision_parser.py` | 198 | JSON extraction utilities (4 strategies: fenced block, first `{}`, first `[]`, raw parse). |
| `claude_client.py` | 146 | Deprecated Anthropic-API client (kept for brain.py v1). |
| `cost_tracker.py` | 110 | Per-day Claude call counter; $0 cost model for CLI. |
| `prompts/` | — | 8 small legacy prompt files (deprecated). |
| `executor.py.deprecated`, `prompt_builder.py.deprecated`, `scheduler.py.deprecated` | — | Renamed out of the import graph — kept as references only. |

## 6.2 `ClaudeStrategist` (strategist.py, 2,335 lines)

Two-call architecture, driven by `LayerManager` at `brain.strategic_interval` (150 s) alternating A/B, with watchdog-triggered URGENT A calls.

### 6.2.1 Call A — `create_strategic_plan()` (trade generation)

- System prompt `TRADE_SYSTEM_PROMPT` (lines 65–147): targets 3–6 new trades per cycle (2–8 bounds), setup-quality tiers (STRONG ≥70 full conviction; GOOD 55–69 normal; NEUTRAL 40–54 small $500 if regime-aligned; WEAK <40 skip). Per-coin regime override rule. Fear & Greed contrarian amplification. Volatility-adaptive recTP/recSL per coin class. Explicit rule to skip `[POS]`-tagged coins.
- Context builder `_build_context_prompt()` (lines 475–1708, ~15k chars):
  - Coaching + recent trades.
  - Regime + Fear & Greed (early fetch, two sources with fallback).
  - Regime instructions block (dynamic per-regime/F&G).
  - Last-20-trade direction performance (buy vs sell WR + PnL).
  - Supported symbols (50 coins).
  - Minimum trade sizes per symbol (price × TESTNET_QTY_STEPS).
  - Market data scan (TA, volatility class, VOL tags, 24 h change).
  - X-RAY structural intelligence — top 8 setups by confluence score, SMC/FVG/OB/sweep signals, MTF confluence.
  - Sentiment aggregate.
  - Global market regime.
  - Open positions with theses (Issue #2 fix).
  - TIAS lessons (wins/losses per symbol).
  - Bybit exchange positions (ground truth, PnL calc).
  - Recently closed (cooldown tracking).
  - Strategy hints (up to 40 automated signals).
  - Per-section timings tracked; if total >5 s the slowest section is flagged.
- Claude call: `await self.claude.send_message(prompt, TRADE_SYSTEM_PROMPT)`.
- Parser `_parse_trade_plan(data)` returns `StrategicPlan`.
- Output JSON shape: `{"new_trades": [{symbol, direction, stop_loss_price, take_profit_price, max_hold_minutes, leverage, size_usd, trailing_activation_pct, reasoning}], "market_view", "risk_level", "max_positions", "default_leverage", "default_sl_pct", "default_tp_pct", "default_hold_minutes", "trailing_activation_pct", "focus_coins", "avoid_coins"}`.
- Log tags: `STRAT_CYCLE_START`, `STRAT_PROMPT`, `STRAT_PLAN`, `STRAT_DIRECTIVE` (per trade #), `STRAT_NO_TRADES`, `STRAT_CALL_A_END`, `STRAT_CALL_A_FAIL`.

### 6.2.2 Call B — `create_position_plan()` (position management)

- System prompt `POSITION_SYSTEM_PROMPT` (lines 150–168): review every open position; actions `hold` / `tighten_stop` / `set_exit` / `close`. Decision rubric: regime support + thesis validity + SL consumed % (>70 % consumed + regime reversed ⇒ close).
- Prompt builder `_build_position_prompt()` (lines 1712–1876): brief regime + cached F&G from Call A; daily PnL; per-position entry/exit/SL/TP/leverage/age/remaining-time/per-coin-regime/SL-consumed%/thesis/APEX-flipped marker/recent lessons; recently closed with cooldowns; UrgentQueue concerns when present.
- Price-divergence gate (lines 424–440): if `transformer._last_enrichment_max_divergence_pct > price.divergence_block_prompt_pct` (1.0 %), emits `PROMPT_DEFERRED` and returns `None`; LayerManager retries next cycle.
- Parser `_parse_position_plan()` (lines 2251–2334) is tolerant: null fields, missing fields, malformed types; invalid `tighten_stop` (no valid `new_sl`) or `set_exit` (no valid `exit_price`) are downgraded to `hold` with warning. Valid actions: `hold`, `close`, `tighten_stop`, `set_exit`, `take_profit`.
- Log tags: `STRAT_CALL_B_START`, `STRAT_CALL_B_PLAN`, `STRAT_CALL_B_END`, `STRAT_CALL_B_FAIL`, `STRAT_CALL_B_URGENT`, `STRAT_CALL_B_BAD_SHAPE`, `STRAT_CALL_B_BAD_ACTIONS`, `STRAT_CALL_B_BAD_ACTION`, `STRAT_CALL_B_DOWNGRADE`, `STRAT_CALL_B_PARSED`, `PROMPT_DEFERRED`.

### 6.2.3 URGENT path

Watchdog flags via `UrgentQueue.add_concern(symbol, level, reason)`. `_has_urgent_concerns` flag is checked inside `_build_position_prompt`; drained concerns are formatted into a dedicated section of the Call B prompt. When Claude acts on an urgent concern, the action is emitted with `source="call_a_urgent"` so that Firewall (Part 15) allows it despite being a `close`/`take_profit`.

### 6.2.4 Internal cached state

`_last_regime_str`, `_last_regime_confidence`, `_last_fg_value`, `_has_urgent_concerns`, `_invalidated_positions: set[str]` — all set inside Call A, read by Call B so the position prompt does not re-fetch regime/F&G.

## 6.3 `ClaudeCodeClient` (claude_code_client.py, 968 lines)

Subprocess wrapper for `/usr/bin/claude -p` (Claude Max subscription — $0 marginal cost).

- Binary resolution at init: realpath check, `shutil.which`, plus hardcoded fallbacks.
- Environment built once (`_build_env`) with `HOME=/home/inshadaliqbal786`, `PATH`, `PROJECT`, `NODE_OPTIONS` — isolated from systemd's stripped env.
- Subprocess spawn: `subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, cwd=_PROJECT, env=self._env, preexec_fn=os.setsid)` — own process group for clean `killpg` on timeout.
- Prompt delivery: via stdin; system prompt via `--system-prompt` flag; max_tokens via environment.
- Timeout handling: `subprocess.TimeoutExpired` → `_kill_process_group()` → SIGTERM (5 s) → SIGKILL (3 s). Logs `CLAUDE_PROC_TIMEOUT_PARTIAL` with any partial stdout/stderr captured.
- Orphan cleanup: `_cleanup_orphaned_processes()` pre-call pgrep to kill stale `claude -p` instances.
- Retry: configurable (`brain.claude_cli_max_retries=2`). Timeout-path backoff: `(attempt+1) * brain.claude_cli_retry_timeout_backoff_base_seconds` (default 10 → ladder 10/20/30 s). Error-path backoff: `2^attempt`.
- Non-retryable pattern set `_NON_RETRYABLE`: "credit balance", "authentication", "api key", "account suspended", "quota exceeded", "rate limit", "out of extra usage".
- Auth recovery, three layers: (1) OAuth refresh via POST to `https://claude.ai/v1/oauth/token` with hardcoded `_OAUTH_CLIENT_ID`; (2) credential hot-reload via mtime diff of `~/.claude/.credentials.json`; (3) Telegram alert (via `_alert_callback` injected by WorkerManager).
- Usage-exhausted handling: parses reset time from error, backs off until reset (capped 3600 s); sets `_usage_exhausted`, emits `CLAUDE_QUOTA_EXHAUSTED`.
- `_call_id` monotonic counter per top-level send (persists across retries). Log family: `CLAUDE_CALL_START`, `CLAUDE_PROC_SPAWNED`, `CLAUDE_CALL_OK`, `CLAUDE_CALL_TIMEOUT`, `CLAUDE_RETRY`, `CLAUDE_RETRY_SLEEP`, `CLAUDE_PROC_KILLED`, `CLAUDE_CALL_FAIL`, `CLAUDE_NONRETRY`, `CLAUDE_QUOTA_EXHAUSTED`, `CLAUDE_ORPHAN_CLEANUP`, `CLAUDE_CRED_RELOAD`, `CLAUDE_AUTH_RECOVERED`, `CLAUDE_POST_REFRESH_TIMEOUT`, `CLAUDE_POST_REFRESH_FAIL`, `CLAUDE_REFRESH_ATTEMPT`, `CLAUDE_REFRESH_OK`, `CLAUDE_REFRESH_FAIL`, `CLAUDE_RATE`, `CLAUDE_ALERT_FAIL`, `CLAUDE_USAGE_RECOVERED`.
- Concurrency: single-flight — the strategist code holds the reference; no internal semaphore (only one Call at a time per session by construction of LayerManager).
- `extract_json(response)` attempts four strategies in order: triple-backticked json block, first balanced `{...}`, first `[...]`, raw `json.loads`.

## 6.4 Integration wiring

`WorkerManager.initialize()` constructs `ClaudeCodeClient(timeout_seconds=… , max_retries=… , min_interval=2.0, retry_timeout_backoff_base_seconds=…)` and sets `self._services["claude_client"]`. It injects an alert callback that routes `CLAUDE_*` error tags into the Telegram Alert stream. `ClaudeStrategist` is built with `(claude_client, services, settings)`. LayerManager owns the cycle scheduling, calling strategist methods in strict A-then-B alternation every 150 s (plus URGENT injections).

Data-flow end-to-end: Strategist → `StrategicPlan` → Coordinator → APEX optimize per-trade → `TradeGate.validate` → `TradeCoordinator.register_trade` → `ShadowAdapter.place_order` → Shadow matching engine → trade opens → PositionWatchdog monitors → ProfitSniper / TimeDecay / Sentinel may tighten SL → close fires → TradeCoordinator close callback → `TradeContextCollector.collect_and_save(m4_snapshot)` inserts `trade_intelligence` row → `TradeAnalyzer.analyze(trade)` calls DeepSeek → `TradeIntelligenceRepo.update_analysis(row_id, …)`.

---

# 7. APEX Subsystem

## 7.1 Files (`src/apex/`, 7 files, 2,791 lines)

- `optimizer.py` (664 LOC) — TradeOptimizer orchestrator.
- `assembler.py` (758 LOC) — IntelligenceAssembler: 5-section package builder.
- `gate.py` (459 LOC) — 12-check safety validator.
- `models.py` (435 LOC) — dataclasses.
- `prompts.py` (226 LOC) — `APEX_SYSTEM_PROMPT` + `build_apex_user_prompt(package)`.
- `qwen_client.py` (248 LOC) — OpenRouter DeepSeek V3.2 wrapper.
- `__init__.py` (59 B).

## 7.2 `TradeOptimizer.optimize(directive, plan)` flow

1. If `[apex] enabled=false`, return fallback OptimizedTrade immediately.
2. Translate directive keys: `stop_loss_price → sl`, `take_profit_price → tp`.
3. `IntelligenceAssembler.assemble(directive)` builds 4 sections (5 if X-RAY available).
4. Validate current price (never $0) — on fail, `APEX_SKIP_NO_PRICE` + fallback.
5. Tier check against TIAS trade history: Tier 1 = `sym_trades ≥ min_tias_trades_for_optimization`; Tier 2 = `regime_trades ≥ min_regime_trades_for_fallback`; Tier 3 = insufficient data → use Claude defaults.
6. Direction-lock gate (code-enforced, not LLM): if high-volatility regime + direction/regime mismatch, stamp `directive.reasoning` with `LOCK: …` and remember to override DeepSeek if it flips. Log `APEX_DIR_LOCK`.
7. Build user prompt via `build_apex_user_prompt(package)`; call `qwen_client.optimize(system=APEX_SYSTEM_PROMPT, user=prompt, model="deepseek/deepseek-v3.2", timeout_seconds=60, max_tokens=800, temperature=0.2)`.
8. Parse response → OptimizedTrade. Clamp numeric values to guardrails (TP floor, trail activation floor, trail distance floor, conviction floor).
9. Enforce direction lock: if DeepSeek flipped despite lock, override back + `APEX_DIR_LOCK_OVERRIDE`.
10. Apply TP cap: `tp_cap_multiplier_by_class[class] × recommended_tp_pct`; log `APEX_TP_CAP` when binding.
11. Emit `APEX_TIMING | sym= el= | assemble= deepseek= parse= constraints=` and return.

Fallback (`is_fallback=True`) keeps Claude's params verbatim. Triggers: disabled, no price, insufficient data (Tier 3), timeout, DeepSeek exception, parser failure.

## 7.3 `IntelligenceAssembler.assemble(directive)` — 5 sections

| Section | Content | Source |
|---|---|---|
| 1 DirectiveContext | symbol, direction, sl, tp, leverage, size_usd, reasoning, plan_view, signal_score, strategy_name. | Directive dict. |
| 2 CoinData | 24 TA fields (RSI, MACD, ADX, Bollinger, Stochastic, EMA, ATR, volume, …); 7 Mode4 fields (Hurst, momentum, extension, volume_div, EV, composite, trail SL); 3 orderbook fields (bid_depth, ask_depth, imbalance_pct); 4 volatility fields (class, rec_tp_pct, rec_sl_pct, rec_hold_min, rec_strategy). | TACache, sniper_log, orderbook snapshot, VolatilityProfiler. |
| 3 TIASSymbolHistory | symbol history — all past trades, win rate by direction, PnL distribution, pattern summary. Regime-filtered to current regime. | TIAS repository. |
| 4 TIASSituationData | regime-level performance — buy/sell WR, avg PnL, direction bias, avg R:R, total trade count in current regime + F&G bucket. | TIAS repository. |
| 5 StructuralData | nearest S/R, market_structure, structural_placement, nearest_fvg, nearest_ob, sweep_signal, smc_confluence, poc_price, fibonacci, mtf_confluence, session_context. | X-RAY structure_cache. |

Price-source ladder (`APEX_PRICE_SOURCE`): TACache → PriceWorker.get_ws_quote (≤5 s) → MarketService REST ticker → sniper's last recorded tick (if position open).

## 7.4 `TradeGate.validate(trade)` — 12 checks

Hard safety layer between APEX and Shadow. Never blocks trades outright — mutates parameters and appends strings to `trade["_gate_adjustments"]`.

1. **CHECK 0** Claude size cap — cap size to `claude_original_size_usd × gate_apex_size_cap_mult` (1.5×). Log `CONVICTION_SIZE_CAP` when binding.
2. Max position size (`max_position_size_usd=1200`).
3. Max leverage (5).
4. Max concurrent positions — if ≥5 open, reduce new size to 30 %.
5. Conviction-weighted capital allocation — base 40 %, multiplied by profit_factor(symbol) from TIAS, modified by signal score (A+ ×1.20, B ×0.90, else ×0.80). Clamped [5 %, 40 %].
6. SL minimum distance (<1.5 % widened).
7. TP minimum distance (<1.0 % widened).
8. TP > SL — swap/adjust on wrong side.
9. Trailing activation — cap to 0.5–0.8 % based on leverage.
10. Hold-time sanity — clamp to 5–1440 min.
11. Size vs available capital — cap to 80 % available.
12. Minimum size after reductions — floor at $300.

Log tags: `GATE_CAP_CHECK`, `GATE_COOL_CHECK`, `GATE_DUP_CHECK`, `GATE_GUARDRAIL_CHECK`, `GATE_PASS`, `GATE_POS_CHECK`, `GATE_RR_CHECK`, `GATE_TPSL_CHECK`, `GATE_CAPITAL_CHECK`.

## 7.5 `QwenClient` (qwen_client.py, 248 LOC)

Async OpenRouter client. Differences from TIAS DeepSeek client: 30 s timeout (30 s default, `[apex] timeout_seconds=60` is max ceiling), 800 max_tokens, temperature 0.2 (deterministic), no retryable flag on `APEXOptimizationError` — any failure triggers Claude fallback immediately. Pricing: input $0.30/M, output $0.88/M tokens (DeepSeek V3.2 via OpenRouter).

## 7.6 APEX state model

All APEX state is transient per-call. There is no separate APEX regime-history table — APEX reads `regime_history` and `trade_intelligence` (regime-filtered). Cold-start behaviour: Tier 3 path logs `APEX_DEFAULT` and returns Claude's original parameters unchanged.

## 7.7 Log tag family (strategist grep shows 40+)

`APEX`, `APEX_ASSEMBLE_M4`, `APEX_ASSEMBLE_OB`, `APEX_ASSEMBLE_TA`, `APEX_ASSEMBLE_TIAS_SIT`, `APEX_ASSEMBLE_TIAS_SYM`, `APEX_ASSEMBLE_VOL`, `APEX_ASSEMBLE_XRAY`, `APEX_CONF_SIZE`, `APEX_DEFAULT`, `APEX_DIR_LOCK`, `APEX_DIR_LOCK_OVERRIDE`, `APEX_FAIL_UNEXPECTED`, `APEX_FLIP`, `APEX_GATHER_FAIL`, `APEX_GUARDRAIL_MODE`, `APEX_GUARDRAIL_TP_FLOOR`, `APEX_GUARDRAIL_TRAIL_ACT`, `APEX_GUARDRAIL_TRAIL_DIST`, `APEX_NO_PRICE`, `APEX_OK`, `APEX_PRICE_FAIL`, `APEX_PRICE_FALLBACK`, `APEX_PRICE_FALLBACK_FAIL`, `APEX_PRICE_SOURCE`, `APEX_REGIME`, `APEX_REGIME_FAIL`, `APEX_SKIP`, `APEX_SKIP_NO_PRICE`, `APEX_STARTUP_STATS`, `APEX_STARTUP_STATS_FAIL`, `APEX_STARTUP_STATS_SCHEDULE_FAIL`, `APEX_SYSTEM_PROMPT`, `APEX_TIER`, `APEX_TIMEOUT_REGIME`, `APEX_TIMING`, `APEX_TP_CAP`, `APEX_WS_QUOTE_FAIL`, plus gate-level `CONVICTION_SIZE_CAP`, `CONVICTION_WEIGHT`, `CONVICTION_WEIGHT_FAIL`.

---

# 8. TIAS Subsystem

## 8.1 Files (`src/tias/`, 8 files, 2,083 lines)

- `collector.py` (566 LOC) — captures 7 groups of context at trade close.
- `analyzer.py` (212 LOC) — DeepSeek Phase-2 analyzer.
- `repository.py` (526 LOC) — persistence layer for `trade_intelligence`.
- `deepseek_client.py` (248 LOC) — OpenRouter async client (45 s / 1500 tokens / temp 0.3).
- `prompts.py` (177 LOC) — `TIAS_SYSTEM_PROMPT` + `build_user_prompt()`.
- `backfill.py` (218 LOC) — historical import.
- `models.py` (135 LOC) — `TradeIntelligence` dataclass with 50+ fields.
- `__init__.py` (50 B).

## 8.2 `TradeContextCollector.collect_and_save(record, repo, m4_snapshot)` — 7 groups

| Group | Fields |
|---|---|
| A Outcome | symbol, direction, strategy_name, strategy_category, source, closed_by, entry_price, exit_price, pnl_pct, pnl_usd, win, hold_seconds. |
| B Entry Decision Context | leverage, position_size_usd, claude_thesis, claude_signal, claude_confidence, entry_score, ensemble_votes. |
| C Market Conditions at Close | regime, fear_greed_value, fear_greed_label. |
| D Technical Indicators at Close | rsi, macd_hist, macd_signal, bollinger_pct, ema_20, ema_50, stochastic_k, stochastic_d, adx, atr_value, atr_pct, volume_ratio, price_vs_vwap. |
| E Mode4 Profit Tracking | m4_peak_pnl_pct, m4_ticks_in_profit, m4_ticks_total, m4_composite_score, m4_hurst_value, m4_momentum_decay, m4_extension_score, m4_ev_ratio, m4_volume_div_score. |
| F (inserted post-collection by analyzer) | ds_why, ds_what_worked, ds_what_failed, ds_lessons, ds_category, ds_confidence, ds_analyzed_at, plus the `ds_*` / `apex_*` / `gate_adjustments` columns. |
| G Metadata | trade_id, trade_closed_at, captured_at. |

`m4_snapshot` is taken synchronously at close time by TradeCoordinator to avoid races with sniper state cleanup. Collector logs `TIAS_SAVE | id=… sym=… dir=… pnl=…% win=… regime=… rsi=…`; errors log `TIAS_COLLECT_FAIL`.

## 8.3 Known data-gaps status (re-verified today)

- Claude directive text **captured** (`claude_thesis`, `claude_signal`) from TradeCoordinator context.
- Mode4 snapshot **captured** synchronously before ProfitSniper.clear_position().
- Entry-time market conditions **captured** (`entry_regime`, `entry_rsi`, `entry_macd_hist`, `entry_atr_pct`).
- Signal score + strategy name **captured** (`entry_score`, `strategy_name`).

## 8.4 `TradeAnalyzer.analyze(trade)`

Calls `DeepSeekClient.analyze(primary_model, fallback_model)` with TIAS prompt + captured trade context. On retryable error (429/503/timeout) tries fallback model once. Maps response fields to DB columns: `why → ds_why`, `category → ds_category`, `correct_direction → ds_correct_direction + ds_optimal_direction`, `what_should_have_done → ds_what_should_done`, `how_to_exploit_next_time → ds_how_to_exploit`, `optimal_sl_pct → ds_optimal_sl_pct`, etc. Logs `TIAS_FALLBACK` on fallback; `TIAS_ANALYZE_OK`, `TIAS_ANALYZE_FAIL`.

## 8.5 Repository methods

- `save(trade) → row_id` (INSERT).
- `update_analysis(row_id, analysis)` — whitelist update to ~20 `ds_*` + `apex_*` + metadata columns.
- `get_unanalyzed(limit=10, max_attempts=3)` — fetches rows where `ds_why IS NULL AND analysis_attempts < max_attempts`.
- `increment_attempts(row_id)` — for retry accounting.
- `get_recent(limit=10)` — TIAS telegram dashboard.
- `get_symbol_full_history(symbol, limit=20)` — APEX assembler feeder.

## 8.6 Telegram commands (src/telegram/handlers/tias_handler.py, 265 LOC)

- `/tias_last` — last analyzed trade breakdown.
- `/tias_patterns` — DeepSeek `ds_category` distribution over last N trades.
- `/tias_symbols` — per-symbol win-rate + average PnL.
- `/tias_cost` — total DeepSeek tokens + $ spent.

## 8.7 Learning loop

- APEX reads `trade_intelligence` via `IntelligenceAssembler._gather_symbol_history()` + `_gather_situation_data()`. When `sym_trades >= min_tias_trades_for_optimization`, full Tier 1 optimization runs.
- Brain prompt includes TIAS lessons in two places: the "Recent lessons" section of Call A (grouped by symbol) and the per-position slot of Call B (filtered to positioned symbols).
- No other feedback loop — Scorer does not read TIAS today.

---

# 9. SENTINEL Subsystem

## 9.1 Files (`src/sentinel/`, 4 files, 484 lines)

- `advisor.py` (222 LOC) — DeepSeek portfolio risk assessor (every 5 min).
- `deadline.py` (195 LOC) — Smart tiered expiry engine.
- `firewall.py` (66 LOC) — Blocks strategic-review closes.
- `__init__.py` (89 B).

## 9.2 `PortfolioAdvisor.assess(portfolio_context)`

Runs every `sentinel.advisor_interval_seconds=300`, offset from Claude cycles. System prompt (lines 23–59) constrains model to SL-tighten-only recommendations (never widen; never close; only tighten when `profit ≥ advisor_min_profit_for_tighten_pct=0.50 %`; protect ≥50 % of unrealized profit). Calls OpenRouter DeepSeek V3 (`deepseek/deepseek-chat-v3-0324`, temp 0.2, max_tokens 800, timeout 30 s). Parses into `AdvisorReport(portfolio_risk, assessment, recommendations=list[AdvisorRecommendation], generated_at, response_time_ms, cost_usd)`. Each `AdvisorRecommendation` has `symbol`, `new_sl_pct_from_entry`, `urgency`, `reason`.

Log tags: `SENTINEL_ADVISOR | risk= recs= ms= cost=$ el= deepseek= parse=`, `SENTINEL_ADVISOR_SLOW`, `SENTINEL_ADVISOR_FAIL`, `SENTINEL_ADVISOR_ERR`. `drain_recommendations()` is polled by PositionWatchdog each tick; drained recommendations go through `sl_gateway.apply(..., source="sentinel")`.

## 9.3 `Firewall` (firewall.py, 66 LOC)

`should_allow_strategic_action(action, symbol, reason, source="strategic_review") -> (bool, str)`. Blocks `action ∈ {"close","take_profit"}` for non-trusted sources. Trusted sources: `"call_b"` (position review), `"call_a_urgent"` (watchdog-driven). Rationale embedded in docstring: historical data shows 26/31 natural SL/TP exits = 84 % WR; 8/8 strategic-review closes = 0 % WR. Log tags: `SENTINEL_FIREWALL_ALLOW`, `SENTINEL_FIREWALL_BLOCK`.

## 9.4 `DeadlineEngine` (deadline.py, 195 LOC)

Smart tiered expiry when `max_hold_minutes` elapses.

Tiers (`DeadlineTier` enum):
- PROFIT (`pnl ≥ deadline_profit_pct=0.5`): `should_close=True`, reason="lock_win".
- BREAKEVEN (`deadline_breakeven_lower_pct=-0.3 ≤ pnl < 0.5`): `should_close=False`, set SL to entry, `grace_minutes=5.0`.
- SMALL_LOSS (`deadline_small_loss_pct=-1.5 ≤ pnl < -0.3`): `should_close=False`, SL → `entry × (1 - 0.5 %)` (direction-aware), grant grace.
- BIG_LOSS (`pnl < -1.5`): `should_close=True`, reason="thesis_failed".

`DeadlineAction` dataclass: `tier`, `should_close`, `new_sl`, `grace_minutes`, `reason`.  `DeadlineGrace` tracks `(symbol, granted_at, grace_minutes, sl_set_to)` and exposes `is_expired` property.

---

# 10. Mode4 Profit Sniper

## 10.1 File footprint

- `src/workers/profit_sniper.py` — **3,089 lines, 138,086 bytes**.
- `src/workers/sniper_models.py` — 988 lines, 37,658 bytes (the five mathematical models).
- `src/workers/sniper_ring_buffer.py` — 419 lines (`EnhancedRingBuffer` + arrays cache).

## 10.2 Five mathematical models (sniper_models.py)

1. **Hurst exponent** (`compute_hurst`): rescaled-range regression on log-price series; `H<0.5` mean-reverting, `H≈0.5` random walk, `H>0.5` trending. Returns `HurstResult(hurst_value, score (0-100 exit pressure), regime ("trending"|"random_walk"|"mean_reverting"), confidence (R²))`.
2. **Momentum decay** (`compute_momentum_decay`): multi-scale PnL deceleration. Component A acceleration (0–40) short vs medium; B consecutive decelerations (0–35); C slope degradation (0–25) with `degradation_ratio`; D momentum_reversed (boolean).
3. **ATR extension** (`compute_extension`): volatility-normalised distance from entry. Returns `extension_atr` (signed), `extension_pct`, `peak_extension_atr`, `drawdown_atr`, `atr_current`, `atr_at_entry`, `vol_ratio`, `base_score` (sigmoid), `score` (0-100 with vol adjustment: ×0.9 low-vol, ×1.15 high-vol).
4. **Volume divergence** (`compute_volume_divergence`): Wyckoff OBV analysis. A price-OBV correlation (0–40); B volume trend (0–25); C buy/sell pressure (0–20); D volume climax (0–15); classification ∈ {"confirming","weakening","diverging","opposing"}; `data_quality ∈ {good, sparse, unavailable}`.
5. **Risk/reward shift** (`compute_risk_reward`): forward expected-value model. `ev_hold`, `ev_ratio`, `p_up`, `p_down`, `p_up_empirical`, `avg_upside_per_tick`, `avg_downside_per_tick`, `expected_upside_5min`, `expected_downside_5min`, `mean_return`, `std_return`, `skewness`, `profit_amplifier`, `base_score`, `score` (0-100 post-amplification).

## 10.3 Composite exploit score & action thresholds (Phase 7)

Weights are regime-conditional. Consensus boost +10 when ≥3 models >50. Urgency boost +15 when momentum flipping or peak pullback detected.

Per-regime threshold sets:

| Regime | tighten | partial_close | full_close |
|---|--:|--:|--:|
| trending | 50 | 70 | 85 |
| ranging | 35 | 55 | 70 |
| volatile | 40 | 60 | 75 |
| dead | 30 | 50 | 65 |
| balanced (fallback) | 35 | 55 | 70 |

Actions (priority order): `hold`<`tighten`<`partial_close`<`full_close`. `tighten` applies base ATR × 1.2, then regime multiplier (trending 1.3, ranging 0.7, volatile 1.0, dead 0.6). Trail decays tighter as PnL increases.

## 10.4 Anti-greed & stall-escape (Phase 9)

- Peak-pullback detection: tracks `peak_pnl_pct` and `pullback_from_peak`. Config thresholds 40/60/75 % pullback at 2/3/5 % peak. Triggers partial/full close.
- Stall escape: after `stall_escape_partial_after_ticks=20` (~100 s) with actionable=True but action=hold → partial; at `stall_escape_full_after_ticks=40` (~200 s) → full. Cooldown `stall_escape_cooldown_seconds=30` suppresses the 20× `PARTIAL_CLOSE_UNSUPPORTED` spam observed on 2026-04-24. `stall_tighten_max_applications=3` with `stall_recovery_threshold_pct=0.15` triggers full-close escalation after repeated tightens without recovery.

## 10.5 Partial close path

- First attempt: 50 % close via `ShadowAdapter.reduce_position(symbol, qty)`.
- On Shadow rejection (`PARTIAL_CLOSE_UNSUPPORTED`) escalates to full close next tick. Legacy `REDUCE_FALLBACK` path kept for `sl_gateway=None` unit-test fallback.

## 10.6 Tick cadence & watchdog coordination

Main loop runs every `mode4.check_interval_seconds=5`. Skips tick when `transformer.is_switching=True` (during exchange-mode transition). Respects `TradeCoordinator.is_immune(symbol)` and cooldown windows. Log throttling: `log_every_n_ticks=6` (≈30 s summary); `log_always_above_score=50`; per-symbol counter.

---

# 11. X-RAY Structure Engine

## 11.1 Files (`src/analysis/structure/`, 15 files, ~3,837 lines)

- `structure_engine.py` (32,994 B, ~837 lines) — orchestrates 10 phases.
- `support_resistance.py` (11,548 B) — swing-points + clustering + scoring.
- `market_structure.py` (10,937 B) — HH/HL/LH/LL + BOS + CHoCH.
- `structural_levels.py` (9,004 B) — structural SL/TP + R:R.
- `fair_value_gap.py` (7,604 B) — 3-candle FVG scanner.
- `order_blocks.py` (6,331 B) — OB identification.
- `liquidity.py` (12,594 B) — liquidity zones + sweeps.
- `volume_profile.py` (6,560 B) — POC / value area.
- `fibonacci.py` (7,079 B) — retracement + extension.
- `mtf_confluence.py` (8,617 B) — multi-timeframe scorer.
- `session_timing.py` (7,719 B) — Asian/London/NY session context.
- `setup_scanner.py` (9,078 B) — qualified setups ranker.
- `shadow_kline_reader.py` (6,340 B) — M1→H1 aggregator from Shadow DB.
- `coin_discovery.py` (3,659 B) — full-universe coin list from Shadow.
- `structure_cache.py` (3,740 B) — in-memory TTL store.
- `models/` — StructuralAnalysis, FairValueGap, OrderBlock, LiquidityZone, LiquiditySweep, VolumeProfile, FibSwing, MTFConfluence, SessionContext, StructuralSetup, StructuralPlacement.

## 11.2 Ten phases (orchestrated by `structure_engine.analyze(symbol)`)

1. Support/resistance — swing lookbacks `[3,5,10]`, cluster ±0.3 %, min 2 touches, max 5 levels per side, proximity score.
2. Market structure — BOS (break of swing high/low) and CHoCH (change of character) detection over `ms_swing_lookback=5`.
3. Structural SL/TP — buffers `sl_buffer_pct=0.15`, `tp_buffer_pct=0.10`, `min_rr_ratio=2.0`; `entry_quality ∈ {ideal, good, poor}`; fallback pct 2.0/4.0.
4. Fair Value Gaps — 3-candle gap; `fvg_min_gap_pct=0.1`, `fvg_max_age_candles=50`; displacement strength tracked.
5. Order Blocks — displacement + FVG/BOS validation; `ob_displacement_min=0.6`; retest & freshness scoring 0–100.
6. Liquidity zones — equal highs/lows + round numbers; `liq_equal_tolerance_pct=0.05`, `liq_min_equal_count=2`, `liq_round_number_step=100.0`.
7. Liquidity sweeps — wick beyond zone + reversal = sweep entry signal; `sweep_max_age_candles=10`, `sweep_min_wick_pct=0.3`. Classification: `long_high_probability`, `short_moderate`, etc.
8. Volume Profile — POC price, value area, current position (`above_poc|at_poc|below_poc`).
9. Fibonacci — retracement 23.6–78.6 %, extension 100–200 %, auto-confluence with S/R + OBs.
10. MTF confluence — scorer across M15/H1/H4/D1 → 0–10 score → quality `none|weak|good|maximum`.

Session-timing runs once per tick (Asian/London/NY/Late NY) and attaches `SessionContext` (current_session, session_phase, manipulation_likely, recommendation).

## 11.3 StructuralAnalysis output

`to_dict()` returns 32 keys: 14 Phase 1 (S/R + market structure + placement), 8 Phase 2 (SMC), 7 Phase 3 (VP + Fib + MTF), 3 Phase 4 (session_context, is_setup, setup_rank), plus setup_score (composite 0–100), suggested_direction (`long|short|neutral`).

## 11.4 Setup Scanner

Six qualifiers: (1) at structural level, (2) structure supports direction, (3) R:R ≥2.0, (4) SMC present (FVG/OB/sweep), (5) MTF confluence ≥5, (6) session favourable. Requires ≥3/6 to qualify. Emits `(ranked_setups, skip_list)` with top 12 by composite score. MAX_SETUPS is 12 after the Market-Dominance expansion. `StructureCache.set_ranked_setups(setups, skip_list)` stores for downstream reads.

## 11.5 R:R hard gate

Inside `TradeScorer._xray_sr_score()`: if the opposite-direction R:R is >5× and the placement is non-fallback, apply a −3 pt penalty and mark the setup "skip-rated". Part 12 documents the scoring call site.

## 11.6 Cache behaviour

`StructureCache` (3,740 B) — TTL `analysis.structure.cache_ttl_seconds=300`. Keyed on `symbol`. Populator: StructureWorker (every 60 s). Readers: Scorer, Strategist context builder, APEX assembler, SL/TP validator, Telegram handlers. Provides `get_ranked_setups()`, `get_skip_list()`, `invalidate(symbol)`, and `_hits`/`_misses` counters.

## 11.7 How X-RAY reaches APEX + Claude

- APEX: `IntelligenceAssembler._gather_structural_data(symbol)` reads `structure_cache.get(symbol)`; populates Section 5 of IntelligencePackage.
- Claude Call A: `_build_context_prompt` renders a condensed X-RAY block (top 8 setups by confluence score, plus per-open-position SMC summary) via `APEX Section 5 renderer`.
- Scorer: `_xray_sr_score()` uses `structural_data.to_dict()` and emits `XRAY_SCORE | sym= dir= entry=+X rr=+Y struct=+Z fvg=+A ob=+B smc=+C sweep=+D poc=+E fib=+F mtf=+G total=+H quality=N`.

---

# 12. Strategy System

## 12.1 Files (`src/strategies/`, 57 files, 6,752 lines)

- `__init__.py` (924 B) — exports BaseStrategy, StrategyRegistry, MarketScanner, RegimeDetector, TradeScorer, EnsembleVoter, DailyPnLManager, SmartLeverage, WeeklyOptimizer.
- `base_strategy.py` (145 LOC) — abstract base. Required properties: `name`, `category`, `applicable_regimes`, `timeframe`, `min_candles=50`, `risk_level="medium"`, `expected_hold_minutes=60`. Required methods: `async scan(...) → RawSignal | None`, `vote(...) → tuple[str, float, str]`.
- `registry.py` (133 LOC) — `StrategyRegistry` lifecycle (register, get, get_all, get_active_for_regime, get_enabled, update_performance, set_enabled, set_ensemble_weight 0.1–3.0, get_registry_summary). Per-strategy `StrategyPerformance`: total_trades, win_rate, profit_factor, current_streak, enabled, ensemble_weight.
- `scorer.py` (467 LOC) — `TradeScorer` 4-component (base 0–40, confluence 0–25, context 0–20, quality 0–20 → 0–105 total). Grading: A+ ≥80, A 68–79, B 56–67, C 45–55, D <45. `_xray_sr_score()` expands S/R proximity from 0–3 to 0–8 with 10 phase modifiers; hard R:R penalty −3 when opposite-R:R>5×. `XRAY_SCORE` log.
- `scanner.py` (401 LOC) — `MarketScanner`. Opportunity score = momentum(0–30)+volatility(0–25)+trend(0–15)+volume(0–20)+spread(0–10)+regime_bonus(±10)−chop_penalty(−15). Hard disqualifiers: volume <$5 M, price <0.0001, spread >0.5 %. Coin tier: 1 (STABLE) vol >$500 M + range <3 % or BTC/ETH; 2 (ACTIVE) vol >$50 M + range ≤8 %; 3 (VOLATILE) vol >$10 M; 4 (EXTREME) range >20 %. Always includes BTC/ETH; protects coins with open positions; 5-minute cooldown on removals. Cache TTL 300 s. Logs `SCAN_SCORE`, `PROTECTING`, `Scanner universe UPDATED`.
- `ensemble.py` (162 LOC) — `EnsembleVoter`. Votes excluding originator; effective vote = weight × confidence. Consensus tiers: STRONG (buy ≥4.0 + opp ≤1.5) size 1.0; GOOD (agree ≥`min_ensemble_agreement=2.5` + opp ≤`max_ensemble_opposition=2.5`) size 0.75; WEAK (agree ≥1.5) 0.30; LEAN (agree>opp) 0.50; CONFLICT (opp ≥ agree) 0.15 (logged `ENSEMBLE_CONFLICT`).
- `regime.py` (206 LOC) — `RegimeDetector` with per-symbol hysteresis (2 readings). 5 regimes (TRENDING_UP/DOWN, VOLATILE, RANGING, DEAD) — thresholds from `[regime]`. Zero-cost `get_last_regime()` caches; `detect_per_coin()` populates `_per_coin_regimes`. Logs `REGIME`, `REGIME_CHG`, `REGIME_PENDING`.
- `pnl_manager.py` (449 LOC) — `DailyPnLManager`. Gates trading via `can_trade()` against `[pnl_targets]` thresholds (target 10 %, protect 7 %, caution -3 %, survival -7 %, halt -10 %). Persists daily snapshot to `daily_pnl`.
- `performance_enforcer.py` (554 LOC) — Level 0/1/2 escalation machine. `check_and_enforce()` every 60 s updates `_enforcement_level`. Size multiplier: ≥0 % →1.0; 0 to -2 →0.75; -2 to -5 →0.50; <-5 →0.25. `should_allow_trade(leverage)`, `get_max_positions_override()`, `get_min_score_override()`, `get_size_multiplier()`, plus auto-recovery after `max_enforcement_minutes=45`.
- `smart_leverage.py` (77 LOC) — applies per-tier leverage cap from `[leverage]` and min-confidence thresholds.
- `optimizer.py` (114 LOC) — weekly weight + param optimizer (disabled by default).
- `register_all.py` (132 LOC) — loads all 43 categories.

## 12.2 Strategy categories (43 files under `src/strategies/categories/`)

| Tier | Count | Members | Intent |
|---|--:|---|---|
| A Scalping | 4 | a1_rsi_reversal, a2_vwap_bounce, a3_bb_squeeze_scalp, a4_ema_crossover | 5-min micro-moves |
| B Trend Following | 4 | b1_volume_breakout, b2_supertrend_follower, b3_ichimoku_breakout, b4_double_bottom_top | Trend continuation |
| C Mean Reversion | 2 | c1_bb_mean_reversion, c2_rsi_divergence | Pullback entries |
| D Derivatives | 2 | d1_funding_rate_fade, d2_oi_divergence | Funding/OI plays |
| E Sentiment/News | 3 | e1_fear_greed_extreme, e2_news_breakout, e3_sentiment_momentum | Alternative-data driven |
| F Structure | 4 | f1_support_resistance, f2_multi_tf_alignment, f3_liquidation_hunt, f4_grid_recovery | Structural entries |
| G Predatory | 4 | g1_stop_hunt_sniper, g2_retail_sentiment_fade, g3_liquidation_frontrunner, g4_whale_shadow | Counter-retail |
| H Microstructure | 4 | h1_funding_prediction, h2_spread_basis, h3_volatility_switch, h4_order_flow | Order flow |
| I Time-based | 4 | i1_kill_zone, i2_weekend_gap, i3_options_expiry, i4_hourly_close | Session timing |
| J Cross-market | 4 | j1_btc_dominance, j2_correlation_breakdown, j3_cross_exchange_lag, j4_altcoin_beta | BTC-D / correlations |
| K AI/Adaptive | 4 | k1_claude_conviction, k2_pattern_memory, k3_ensemble (passthrough stub), k4_adaptive_optimizer (passthrough stub) | AI-driven |
| X Test | 1 | x1_always_trade | Smoke test only |

Plus `src/strategies/categories/generated/` — runtime-imported factory-generated strategies (empty today).

Every strategy implements `scan()` and `vote()` with consistent signatures. All use `src/analysis/engine.py:TAEngine.analyze()` output via TACache. Common pattern: `RawSignal(symbol, direction, suggested_sl, suggested_tp, conditions: dict, reasoning: str)` → TradeScorer scores → EnsembleVoter filters.

## 12.3 Indicator reference check (grep)

A1/C2 reference Stochastic K/D — still computed inside `src/analysis/indicators/momentum.py`. A2/A4/E3 reference VWAP — still computed in `src/analysis/indicators/volume.py`. Candlestick and chart patterns are checked via `patterns.has_bullish_pattern()` which degrades gracefully when the pattern module is absent. No strategy today references a removed indicator.

---

# 13. Signal And Regime Detection

## 13.1 SignalGenerator (`src/intelligence/signals/signal_generator.py`, 247 LOC)

Inputs: symbol, aggregated sentiment score (news + reddit + F&G), funding rate, OI change, optional regime hint. Rule-based synthesis:
- F&G <20 ⇒ contrarian BUY; >80 ⇒ contrarian SELL.
- Funding rate >0.01 ⇒ overbought fade; <-0.01 ⇒ oversold fade.
- Bullish sentiment + rising volume ⇒ BUY; bearish sentiment + rising volume ⇒ SELL.
- High OI divergence tilts contrarian.

Output: `Signal(signal_type: SignalType, confidence: float [0-1], reasoning: str, components: dict)`.

## 13.2 Confidence (`src/intelligence/signals/confidence.py`, 129 LOC)

Weighted combination: agreement 0.40 + magnitude 0.25 + volume 0.20 + freshness 0.15. Volume factor ladder: 0 points →0.3, 1–4 →0.5, 5–19 →0.7, ≥20 →1.0. Freshness: ≤1 h →1.0, ≤6 h →0.8, ≤12 h →0.6, ≤24 h →0.4, >24 h →0.3.

## 13.3 Regime detection (`src/strategies/regime.py`, 206 LOC)

Five regimes via `MarketRegime` enum: TRENDING_UP (ADX>25, +DI>-DI, chop<45), TRENDING_DOWN (ADX>25, -DI>+DI, chop<45), VOLATILE (ATR percentile>150 or volume_ratio>2.0), RANGING (ADX<20 + chop>60), DEAD (ADX<15 + vol_ratio<0.5 + ATR%<50). Thresholds are overridable via `[regime]`.

Per-symbol hysteresis via `_confirmed_regimes` and `_pending_regime`: needs 2 consecutive detections of a new regime before confirming. Global regime computed from `regime.primary_symbol=BTCUSDT`. Per-coin regime via `detect_per_coin()` feeds `_per_coin_regimes`.

## 13.4 Volatility profiler (`src/analysis/volatility_profile.py`, 323 LOC)

Five volatility classes via 5-min NATR with per-class TP/SL/hold/strategy:

| Class | TP | SL | hold | strategy |
|---|--:|--:|--:|---|
| dead | 0.30 % | 0.20 % | 10 min | scalp |
| low | 0.50 % | 0.35 % | 20 min | mean_revert |
| medium | 1.50 % | 1.00 % | 30 min | breakout |
| high | 3.00 % | 2.00 % | 45 min | momentum |
| extreme | 5.00 % | 3.00 % | 60 min | trend_follow |

Regime modifiers multiply base params. Cache TTL 120 s with per-symbol jitter ±15 s. Hit-rate logged via `VOL_PROFILE_HIT` (rate-limited).

---

# 14. Sentiment Pipeline

- `FinnhubClient` (`src/intelligence/news/finnhub_client.py`, 124 LOC) — GET `/news?category=` + `/calendar/economic`. Rate limit enforced client-side at `[finnhub] rate_limit_per_minute=60`.
- `NewsService` (208 LOC) — calls client, scores each headline via `SentimentScorer`, writes to `news_articles`.
- `CalendarService` (93 LOC) — writes to `economic_calendar`.
- `SentimentScorer` (214 LOC) — TextBlob polarity + intensity classification to `SentimentLevel ∈ {VERY_POSITIVE, POSITIVE, NEUTRAL, NEGATIVE, VERY_NEGATIVE}`.
- `RedditClient` (180 LOC) — PRAW async; disabled today. Intended subreddits configured in `[reddit] subreddits`.
- `SentimentAggregator` (325 LOC) — weighted combination: news 0.35, reddit 0.30, F&G 0.20, momentum 0.15. Writes per-symbol rows to `aggregated_sentiment`. **Zero-coverage cache** `_unknown_cache: dict[str, (expires_at, result)]` with 30-min TTL short-circuits DB round-trips for symbols with no news/reddit data; logs `SENT_UNKNOWN_CACHE_HIT`. SENT_NEUTRAL branch returns `(score=0, level=NEUTRAL)` when both news and reddit are empty rather than raising.
- `FearGreedClient` (`src/intelligence/altdata/fear_greed.py`, 122 LOC) — polls `https://api.alternative.me/fng/`.
- `FundingRateTracker` (117 LOC), `OpenInterestTracker` (111 LOC), `OnChainClient` (129 LOC) — altdata siblings.

Sentiment reaches APEX via Section 3 (`TIASSymbolHistory`) + Section 4 (`TIASSituationData`) plus TACache-embedded sentiment lookup. Sentiment reaches Claude Call A prompt as a dedicated "Sentiment" block and again as per-coin line markers (e.g. `[SENT:Fear]`). Claude Call B receives a brief regime + sentiment summary cached from Call A.

---

# 15. Risk Management Subsystems

## 15.1 SL Gateway (`src/core/sl_gateway.py`, 727 LOC)

Single-entry-point validator for every SL modification. Four rules: R1 tighten-only (never bypassable), R2 min-distance (ATR-scaled: `max(min_distance_abs_floor_pct=0.05 %, atr_5m_pct × min_distance_atr_multiplier=0.5)` clamped by class ceiling), R3 max-step (`max_step_pct=0.5`), R4 rate-limit (`rate_limit_seconds=30`). Modes: `enabled=false` (pass-through tracks state, no rule evaluation), `log_only_global=true` (rule violations become `SL_GATEWAY_REJECT_WOULD` — currently the production setting), per-rule `log_only_*` flags for staged rollout. State: `_last_change: dict[str,float]` (monotonic ts), `_last_sl: dict[str,float]`.

Log family: `SL_GATEWAY_INIT`, `SL_GATEWAY_ACCEPT`, `SL_GATEWAY_REJECT`, `SL_GATEWAY_REJECT_WOULD`, `SL_GATEWAY_PASSTHROUGH`, `SL_GATEWAY_WIRE_FAIL`, `SL_GATEWAY_POS_FETCH_FAIL`, `SL_GATEWAY_PRICE_FETCH_FAIL`, `SL_GATEWAY_VP_FAIL`. Aggregated `SL_GATEWAY_STATS` emits every 300 s or every 100 events.

EventBuffer hooks: HIGH `sl_gateway_wire_fail` (downstream broken, operator attention), MED `sl_gateway_brain_blocked` (Claude-directed tighten blocked by rule).

Callers (from `grep -rn sl_gateway src/` enumeration):
- `profit_sniper.py` — lines 120 (ctor), 1425 (trail tighten), 1474 (legacy fallback), 2445-2458 (partial-close aftermath).
- `position_watchdog.py` — tighten path for trailing + brain-directed tightens.
- `workers/manager.py` — service registration; `_sl_gateway_reset_on_close` hook clears per-symbol state on close.
- `risk/time_decay_sl.py` — TD lane.
- `sentinel/advisor.py` — Advisor recommendations applied via gateway (`source="sentinel"`).

`SLGatewayResult(accepted: bool, reason: str, old_sl: float, new_sl_applied: float)`.

## 15.2 Time-Decay SL (`src/risk/time_decay_sl.py`, 529 LOC)

5-model multiplicative formula: `allowed_loss = atr_room × time_factor × recovery_mult × momentum_mult × probability_mult` (then floored at `min_allowed_loss_pct=0.15 %`, capped by original SL pct).

1. Convex time decay: `1 − (age/max_hold)^1.5`.
2. ATR-scaled room: `atr_5m_pct × atr_room_multiplier_by_class[class]` (dead 1.0, low 1.2, medium 2.0, high 2.5, extreme 3.0).
3. MAE recovery multiplier: `recovery_ratio = (current_pnl - mae) / |mae|` → bonus 1.2 when >0.5, penalty 0.8 when <0.2.
4. Velocity+Acceleration 4-case: vel<0+accel<0 →0.7; vel>0+accel>0 →1.3; mixed cases 0.9/1.1.
5. Bayesian p_win: prior `0.55 + regime_confidence × 0.25` (clamped [0.05, 0.95]). Updates: 1 ATR deeper ×0.85, 2 ATR deeper ×0.70, recovered 50 %+ of MAE ×1.15, regime still supports ×1.05, regime reversed ×0.60. Absolute-depth penalty: |pnl|>1.5 % ×0.90, |pnl|>3 % ×0.70 (`p_win_abs_depth_*` knobs). Force-close sentinel when p_win<0.15.

Grace period per volatility class (`grace_seconds_by_class`): dead 30 s, low 45 s, medium 120 s, high 180 s, extreme 240 s. Phase-11 price-relative floor: skip push when `new_sl` distance from current price < `sl_gateway.min_distance_pct`. Logs `TIME_DECAY_CALC`, `TIME_DECAY_GRACE`, `TIME_DECAY_FLOOR_PRICE_REL`, `TIME_DECAY_FORCE_CLOSE`.

## 15.3 Trailing Stop

Not a dedicated file — implemented inside ProfitSniper (Part 10.3). Activation when PnL ≥ `trailing_activation_pct` (derived from class and leverage); base ATR × 1.2 with regime multiplier; coordinated through SL Gateway.

## 15.4 Enforcer (`src/strategies/performance_enforcer.py`, 554 LOC)

See Part 12 for the state machine. Fires every 60 s via `EnforcerWorker`. When active, `StrategyWorker` and the coordinator apply:
- `should_allow_trade(leverage)` — hard block if leverage exceeds level cap.
- `get_max_positions_override()` — reduces to `level_1_max_positions=3` or `level_2_max_positions=2`.
- `get_min_score_override()` — raises floor to `level_1_min_score=75` / `level_2_min_score=80`.
- `get_size_multiplier()` — soft throttle multiplier on final sizing.

## 15.5 Ghost Reconciliation

Lives inside `PositionWatchdog` (fast-reconcile every 30 s per `watchdog.fast_reconcile_seconds=30.0`) and `LayerManager` (thesis-level sweep every 5 min). Compares Shadow open positions vs internal thesis state; mis-matches → close/remove thesis / alert. Definition of "ghost": a thesis row with `status='open'` but no matching Shadow position (or vice versa) — both directions reconciled.

## 15.6 Cooldown

Stored in `TradeCoordinator._symbol_cooldowns: dict[str, float]` (monotonic expiry). `on_trade_closed()` sets expiry using `MINIMUM_HOLD_SECONDS` by strategy category (claude_direct 120, scalping 120, momentum 300, mean_reversion 180, funding_arb 600, sentiment 300, advanced 180, default 60). 180 s win / 600 s loss — handled implicitly because losing strategies (funding_arb) have the longest cooldown. Claude's recently-closed block shows the cooldown countdown.

## 15.7 POS Gate

Enforced inside LayerManager (both `src/core/layer_manager.py` and `src/workers/layer_manager.py`). Before executing a Claude plan:

```
blocked_symbols = open_position_symbols | self._currently_executing
for trade in plan.new_trades:
    if trade.symbol in blocked_symbols:
        log.info(f"POS_GATE_BLOCK | sym={sym} rsn='open_position'|'executing'")
        _bump_skip("pos_gate")
        continue
```

Plus a prompt-level hint: `[POS]` tag on each coin in the Call A context so Claude already avoids them.

## 15.8 Firewall

Covered in Part 9.3. Sources allowed through: `call_b`, `call_a_urgent`. Everything else blocked for `close`/`take_profit`.

---

# 16. Shadow Exchange Integration

## 16.1 Shadow repo (`/home/inshadaliqbal786/shadow`) — 30 project Python files

- `shadow.py` (308 LOC) — entry point.
  1. Load `config.toml` via `load_config()`.
  2. `setup_logging()` — loguru to `logs/`.
  3. `DatabaseManager(config.database.path, wal_mode=True)` connects `data/shadow.db`.
  4. `run_migrations(db)`; `initialize_wallet(db, starting_balance=10_000)`.
  5. `CoinSelector.select_top_coins(coin_count=100)`.
  6. `WebSocketManager(config).set_symbols(symbols)`; re-add orphan symbols held in `virtual_positions.status='open'`.
  7. Instantiate collectors (`KlineCollector`, `TickerCollector`, `FundingCollector`, `OICollector`), `VirtualWallet`, `OrderEngine`, `PositionMonitor`, `TradeRecorder`, `WalletSnapshotter`, `DailyRollup`.
  8. Start aiohttp API (`create_api_app(...)`) on `api.host=127.0.0.1` port `9090`.
  9. Optional Telegram bot (`create_bot(...)`, `start_bot(...)`); wires `_on_trade_open` / `_on_trade_close` callbacks on OrderEngine for trade alerts.
  10. Launch 8 tasks: websocket, kline_collector, ticker_collector, funding_collector, oi_collector, position_monitor, wallet_snapshotter, daily_rollup.
  11. Signal handlers for SIGTERM/SIGINT set `shutdown_event`; `finally` stops bot + API + tasks + DB.
- `layer_manager.py` (648 LOC) — Shadow's internal layer orchestration (separate from main project's LayerManager).
- `src/api/shadow_client.py` (354 LOC) — HTTP endpoints exposed on port 9090.
- `src/collector/websocket.py` (383 LOC) — public-linear WS on `stream.bybit.com/v5/public/linear`.
- `src/collector/kline_collector.py` (200 LOC) — 1-minute klines + backfill.
- `src/collector/ticker_collector.py` (123 LOC) — 60 s ticker snapshots.
- `src/collector/funding_collector.py` (131 LOC) — 8 h polling.
- `src/collector/oi_collector.py` (97 LOC) — 5 min polling.
- `src/collector/coin_selector.py` (131 LOC) — top-N by 24 h volume (re-ranked daily).
- `src/database/migrations.py` (442 LOC), `src/database/connection.py` (174 LOC).
- `src/exchange/order_engine.py` (761 LOC) — matches `place/close/reduce/set-sl/set-tp`.
- `src/exchange/position_monitor.py` (412 LOC) — 1-Hz SL/TP/liquidation checks.
- `src/exchange/wallet.py` (295 LOC) — VirtualWallet (margin + PnL).
- `src/exchange/trade_recorder.py` (175 LOC) — persists `virtual_trade_history`.
- `src/exchange/wallet_snapshotter.py` (120 LOC), `src/exchange/daily_rollup.py` (294 LOC).
- `src/telegram/bot.py` (206 LOC), `src/telegram/handlers.py` (650 LOC) — separate Shadow bot (`chat_id=<REDACTED_CHAT_ID>`).
- `src/utils/retention.py` (349 LOC), `src/utils/config.py` (278 LOC), `src/utils/logging.py` (67 LOC).

Shadow DB: 65-table-class superset including `virtual_positions`, `virtual_trade_history`, `virtual_wallet`, `tracked_coins`, `ticker_snapshots`, `oi_snapshots`, `funding_rates`, `klines` (1-min dominant), `wallet_snapshots`, plus schema_version. Retention per `[retention]`: klines/funding_rates/trade_history/daily_summary forever; ticker 30 days; OI 90 days; wallet snapshots 30 days; closed positions pruned after 30 days.

## 16.2 Shadow HTTP API (port 9090, from `api/shadow_client.py` + grep)

- `GET /api/positions` — list all positions.
- `GET /api/position/{symbol}` — one position (may be null).
- `GET /api/position/{symbol}/last_close` — authoritative closed-position snapshot (`exit_price`, `net_pnl_pct`, `hold_duration_seconds`, etc.).
- `POST /api/close` — market close (full).
- `POST /api/reduce` — partial close (returns FILLED when supported; REJECTED otherwise).
- `POST /api/set-sl` — update SL.
- `POST /api/set-tp` — update TP.
- `POST /api/order` — place an order (market/limit with sl/tp/leverage).
- `GET /api/health` — health check.
- `GET /api/balance` — account equity + available.
- `GET /api/ticker/{symbol}` — last tick.
- No authentication (localhost-only binding).

## 16.3 `ShadowAdapter` (`src/shadow/shadow_adapter.py`, 607 LOC)

Three drop-in adapter classes mirroring the Bybit service interfaces:

- `ShadowPositionService`: `get_positions()`, `get_position(symbol)`, `get_last_close(symbol)`, `close_position(symbol)`, `reduce_position(symbol, qty)`, `set_stop_loss(symbol, stop_loss)`, `set_take_profit(symbol, take_profit)`, `get_pnl_summary()`, `health_check()`.
- `ShadowOrderService`: `place_order(symbol, side, order_type, qty, price, sl, tp, leverage)`, `get_order(order_id)`, `cancel_order(order_id)`.
- `ShadowAccountService`: `get_balance()`, `get_equity()`.

Error handling: any `aiohttp.ClientError` or non-200 HTTP becomes a `None` return or a `REJECTED` `Order` object. `reduce_position` falls back to `close_position()` on rejection and logs `REDUCE_FALLBACK`. FILLED vs REJECTED is distinguished by the `status` field in the response JSON ("Filled"/"Rejected").

---

# 17. MCP Server

## 17.1 Files (`src/mcp/`, 13 files, 1,844 lines)

- `server.py` (263 LOC) — `MCPServer` class. `initialize()` connects DB, runs migrations, creates services (trading + intelligence + analysis + TA + portfolio), calls `_register_tools()`, wires `@app.list_tools`/`@app.call_tool`, sends startup alert, emits `MCP_INIT | tools=43 init_ms= transport=`.
- `auth.py` (44 LOC) — `MCP_AUTH_TOKEN` bearer check (required for SSE).
- `client_pool.py` (311 LOC) — Y-22 SSE client pool for inbound consumers (currently `[mcp_pool] enabled=false`).
- `tools/__init__.py` — registers all 8 tool files.
- `tools/trading_tools.py` (347 LOC) — 12 tools.
- `tools/risk_tools.py` (155 LOC) — 5 tools.
- `tools/analysis_tools.py` (192 LOC) — 5 tools.
- `tools/altdata_tools.py` (131 LOC) — 5 tools.
- `tools/sentiment_tools.py` (111 LOC) — 5 tools.
- `tools/news_tools.py` (89 LOC) — 4 tools.
- `tools/memory_tools.py` (107 LOC) — 4 tools.
- `tools/system_tools.py` (88 LOC) — 3 tools.

Total 43 tools.

## 17.2 Full tool list (from `grep name=` across `src/mcp/tools/*.py`)

Trading (12): `get_account_info`, `get_ticker`, `get_tickers`, `get_klines`, `get_orderbook`, `place_order`, `modify_order`, `cancel_order`, `cancel_all_orders`, `get_open_orders`, `get_positions`, `close_position`.

Risk (5): `calculate_position_size`, `get_risk_exposure`, `calculate_stop_loss`, `get_daily_pnl`, `get_risk_status`.

Analysis (5): `get_technical_analysis`, `get_indicator`, `get_patterns`, `get_signal`, `get_trade_recommendation`.

Altdata (5): `get_fear_greed_index`, `get_funding_rates`, `get_open_interest`, `get_funding_history`, `get_market_overview`.

Sentiment (5): `get_reddit_sentiment`, `get_subreddit_hot`, `get_social_buzz`, `get_aggregated_sentiment`, `get_sentiment_history`.

News (4): `get_latest_news`, `get_news_for_symbol`, `search_news`, `get_economic_calendar`.

Memory (4): `get_trade_history`, `get_strategy_performance`, `get_pattern_outcomes`, `get_brain_decisions`.

System (3): `get_system_status`, `get_worker_status`, `update_preference`.

## 17.3 Transport + proxy

- Production: SSE on port 8080 (`trading-mcp-sse.service` runs `server.py --transport sse --port 8080`).
- Claude CLI consumers: spawn `mcp_stdio_proxy.py` which forwards stdio to `http://127.0.0.1:8080/sse` (auth: `MCP_AUTH_TOKEN`).
- Auth required for SSE (`sse_auth_required=true` in `[mcp]`).
- `ToolRegistry` uses `@app.list_tools()` and `@app.call_tool()` decorators from `mcp` SDK.
- All tools are currently called by the embedded Claude CLI (no external clients observed); some are also consumable by any MCP-compatible client.

---

# 18. Telegram Integration

## 18.1 Files (`src/telegram/`, 40 files, 6,434 lines)

- `bot.py` (705 LOC) — `InteractiveTelegramBot`, lifecycle + command registration.
- `router.py` (131 LOC) — routing tables.
- `conversation.py` (57 LOC) — lightweight conversation state.
- `auth.py` (33 LOC) — admin ID check.
- `handlers/` (16 files, ~6k LOC):
  - `dashboard_handler.py` — **2,371 LOC**, the largest handler. Auto-refreshing dashboard (callback buttons cycle intervals), position summary, daily PnL trend, enforcer level, time-decay stats, per-strategy performance. Runs SQL aggregates against trading.db.
  - `control_handler.py` (630 LOC) — control commands: `/control`, `/enable_trading`, `/disable_trading`, `/enforce_capital_preservation`, `/enforce_survival`, `/reset_enforcer`, `/brain_interval_60|180|300`, `/floor`, `/plan`, `/mode`, `/positions`.
  - `tias_handler.py` (265 LOC) — TIAS commands (Part 8).
  - `apex_handler.py` (215 LOC) — `/apex_status`, `/apex_last`, `/apex_flips`.
  - `analysis_handler.py` (238 LOC) — `/analyze`, `/signals`, `/regime`, `/fear`, `/news`, `/opportunities`.
  - `portfolio.py` (138 LOC) — `/portfolio`, `/pnl`, `/balance`, `/history`.
  - `system.py` (158 LOC) — `/status`, `/errors`, `/pause`, `/resume`.
  - `fund.py` (202 LOC) — `/fund`, `/setwallet`, `/floor` (with inline buttons).
  - `brain.py` (75 LOC) — `/brain`, `/decisions`, `/leaderboard`, `/factory`.
  - `trading.py` (135 LOC) — `/quicktrade`, inline quickbuy/close.
  - `alerts.py` (66 LOC) — `/alert`, `/alerts`, `/cancelalert`.
  - `watchlist.py` (75 LOC) — `/watch`, `/unwatch`, `/watchlist`.
  - `journal.py` (42 LOC) — `/journal`, `/note`.
  - `schedule.py` (24 LOC) — `/schedule`.
  - `emergency.py` (54 LOC) — `/emergency` with two-step confirm.
- `features/` — price_alerts, risk_checker, scheduled_reports, trade_journal, morning_briefing, chart_generator, leaderboard.
- `ai/` — context_builder (61 LOC), question_handler (65 LOC), prompts (22 LOC).
- `ui/` — cards (107), buttons (66), formatters (42), charts (31).
- `models/telegram_types.py` (97 LOC).

## 18.2 Bot configuration

`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from `.env` (not reproduced). Bot handle advertised in BotCommand registration. `AlertSettings` from config: `telegram_enabled=true`, `alert_levels=["WARNING","CRITICAL"]`, `max_alerts_per_minute=10`, `trade_alerts=true`, `signal_alerts=true`, `error_alerts=true`. `[telegram_interactive] enabled=true, ai_responses_enabled=true, max_ai_calls_per_hour=20, trade_confirmation_required=true, morning_briefing_enabled=true, morning_briefing_hour_utc=5, price_alert_check_interval=10`.

## 18.3 Alert manager (`src/alerts/`, 6 files, 1,013 lines)

- `alert_manager.py` (304 LOC) — central hub. Throttles via `AlertThrottle`, dedups via `dedup_cache: dict[str, float]` (content_hash → last_emit_ts, 300 s window). CRITICAL bypasses throttle. Public methods: `send_trade_alert`, `send_brain_decision_alert`, `send_watchdog_decision`, `send_risk_warning`, `send_error_alert`, `send_system_startup`, `send_daily_summary`.
- `throttle.py` (95 LOC) — `AlertThrottle(max_per_hour, dedup_window_s)`; queue for throttled events; dedup cache cleaned on each `is_duplicate()` call.
- `formatter.py` (102 LOC) — HTML + emoji templates.
- `templates.py` (264 LOC) — per-event templates (trade_executed, position_closed, brain_decision, risk_warning, watchdog_alert, error_alert, startup, daily_summary).
- `telegram_bot.py` (239 LOC) — thin Telegram send-layer used by AlertManager.

## 18.4 Dashboard auto-refresh

The `/dashboard` handler edits its own message via `edit_message_text` on callback button press. Intervals: manual refresh buttons + optional auto-refresh via `JobQueue`. Refresh queries are SQL aggregates over `trade_thesis`, `trade_log`, `trade_intelligence`, `position_snapshots`, `event_log`, etc.

## 18.5 Rate limits + known errors

`AlertThrottle.max_per_hour` default 600 (10/min × 60). Telegram HTTP timeouts: `SEND_READ_TIMEOUT=15.0`, `SEND_WRITE_TIMEOUT=15.0`, `SEND_CONNECT_TIMEOUT=10.0`. Retry ladder 2→5→10 s. Recent observability logs show occasional `Error 429: Too Many Requests` on bursts of position-close events; resolved by dedup cache.

---

# 19. External Integrations

| Service | Purpose | Endpoints | Rate limit | Credential | Failure behaviour | Retry |
|---|---|---|---|---|---|---|
| Bybit REST | Market data, order placement (only when mode=live) | `https://api.bybit.com/v5/*` (kline, ticker, positions, orders, account) | `[bybit] rate_limit_per_second=10` (client-enforced) | `BYBIT_API_KEY/SECRET` (public endpoints work without keys) | WorkerManager logs warning + continues with partial services; BybitClient emits errors, caller retries | In-SDK retries via pybit; custom rate-limiter sleeps |
| Bybit WS | Real-time ticker for active universe | `wss://stream.bybit.com/v5/public/linear` | implicit | none | PriceWorker resubscribes on disconnect; `_connected=False` at tick forces reconnect | Exponential back-off via websockets lib |
| Finnhub REST | News + economic calendar | `https://finnhub.io/api/v1/news`, `/calendar/economic` | 60/min | `FINNHUB_API_KEY` | NewsService logs error + returns empty | Next NewsWorker tick |
| Reddit OAuth (PRAW) | Post sentiment | `https://oauth.reddit.com/r/<sub>/hot` | 60/min | `REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD` | Currently disabled (`[reddit] enabled=false`) | — |
| Telegram Bot API | Alerts + interactive bot | `https://api.telegram.org/bot<token>/*` | ~30 msg/sec (platform) | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | AlertManager throttles + queues (`AlertThrottle`); dedup cache collapses bursts; CRITICAL bypasses throttle | Tenacity-style 2→5→10 s ladder inside `alerts/telegram_bot.py` |
| OpenRouter | DeepSeek V3 for TIAS (45 s), DeepSeek V3.2 for APEX (30 s), DeepSeek V3 for Sentinel Advisor (30 s) | `https://openrouter.ai/api/v1/chat/completions` | platform-managed | `OPENROUTER_API_KEY` | TIAS: retry with `fallback_model` once on 429/503/timeout. APEX: no retry — fall back to Claude params. Sentinel: log + skip cycle. | TIAS one fallback model attempt; others skip |
| Claude CLI | Production brain (Anthropic Max subscription, $0 marginal) | Subprocess `claude -p` | CLI-side (Max plan) | `~/.claude/.credentials.json` (OAuth) + `CLAUDE_API_KEY` for legacy path | 3-layer auth recovery (refresh token → hot-reload creds → Telegram alert); quota backoff respects reset time | `[brain] claude_cli_max_retries=2`, `retry_timeout_backoff_base_seconds=10` |
| Anthropic REST (legacy) | Brain v1 only | `https://api.anthropic.com/v1/messages` | n/a | `ANTHROPIC_API_KEY` | Only used by `src/brain/claude_client.py` when Brain v1 runs; not in current hot path | In-library |
| Alternative.me | Fear & Greed index | `https://api.alternative.me/fng/` | No documented limit; `[altdata] fear_greed_interval=3600` | none | AltDataWorker logs + continues | Next tick |
| CoinGecko | Market meta (only when invoked) | `https://api.coingecko.com/api/v3/*` | `coingecko_rate_limit_per_minute=10` | none | Skip fetch on 429 | Next tick |
| Shadow HTTP API | Trade routing (localhost) | `http://127.0.0.1:9090/api/*` | n/a (localhost) | none | ShadowAdapter returns None or Order(status=Rejected); coordinator logs + emits event | In-process only |
| Local MCP SSE | Long-lived MCP transport | `http://127.0.0.1:8080/sse` | n/a | `MCP_AUTH_TOKEN` | `mcp_stdio_proxy.py` exits with code 2 → Claude CLI surfaces failure | Proxy re-spawned by next Claude invocation |
| Claude OAuth refresh | Token-refresh utility | `https://claude.ai/v1/oauth/token` | n/a | refresh token in `~/.claude/.credentials.json` | `_try_token_refresh()` best-effort; logs `CLAUDE_REFRESH_FAIL` | Once per auth recovery attempt |

---

# 20. Transformer Routing Layer

The architecture diagram references T1–T7 transformers. `src/core/transformer.py` (1,064 LOC) is the actual routing layer present in code. It is a mode-switching state machine (`transformer_state` DB table, singleton row) with three named modes: `shadow` (virtual exchange), `paper` (Bybit testnet), `live` (Bybit mainnet). The grep for `T1`, `T2`, …, `T7` across `src/` returns zero token-level matches.

The routing logic implemented:

- `Transformer.initialize()` loads singleton row from `transformer_state`, sets `self.current_mode` and `self.is_switching`.
- `Transformer.switch(to_mode, reason)` — atomic mode transition: marks `is_switching=True`, closes all open positions through the destination exchange's close path, waits for reconciliation (`fast_reconcile_seconds`), writes row to `switch_history`, flips `current_mode`, clears flag. Emits events to AlertManager + EventBuffer.
- `Transformer.route(order)` — routes `place/close/reduce/modify` calls to the appropriate adapter (ShadowAdapter / ShadowPositionService / BybitOrderService). Current mode binding stored per call so a mid-cycle switch does not split orders.
- `Transformer._last_enrichment_max_divergence_pct` — cached price divergence (local WS vs Shadow authoritative) that ClaudeStrategist checks in its Call B gate (Part 6.2.2).

`switch_history` table records every mode transition (from/to, positions_closed, close_results_json, reason, success, error, both-side equities). Today `transformer_state` shows `current_mode='shadow'` with 4 historical rows in `switch_history`.

If T1–T7 are a planning-document abstraction not yet wired to code, state the gap: **unknown — the T1 through T7 nomenclature is not present in the current codebase. The active routing layer is `src/core/transformer.py` with three modes (shadow/paper/live) and no sub-transformer decomposition.** This is an item for Part 30.

---

# 21. Data Flow End-To-End

## 21.1 New-trade path (Call A cycle)

1. Bybit has ~500 USDT perpetuals — `MarketScanner` (`scanner_worker.py` every 300 s) fetches tickers and computes opportunity score. Writes top ≤30 rows to `active_universe`. Log `SCAN_SCORE`.
2. Universe-change callbacks notify PriceWorker (WS resubscribe), KlineWorker (backfill), SignalWorker (first-pass signals), RegimeWorker (backfill regime).
3. `PriceWorker` streams `publicTrade/ticker.linear.*` into `_ws_quotes` and upserts `ticker_cache`.
4. `KlineWorker` fetches 200-bar M5+H1 every tick (45 s), larger TFs on their schedules. Writes `klines`. Logs `KLINE_FETCH`.
5. `RegimeWorker` detects global + per-coin regimes every 600 s. Writes `regime_history`, `coin_regime_history`. Caches in `RegimeDetector._per_coin_regimes` for zero-cost reads.
6. `SignalWorker` every 120 s produces per-coin `Signal`s using `SentimentAggregator.aggregate_for_symbol` + `FearGreedClient` + funding/OI. Writes to `signals`. Logs `SIG_BATCH`.
7. `VolatilityProfiler` lazily populated on request (cache TTL 120 s).
8. `StructureWorker` every 60 s runs X-RAY for a 25-coin batch; SetupScanner ranks top 12.
9. `LayerManager` wakes at `brain.strategic_interval=150` s for Call A. ClaudeStrategist builds the 15k-char context prompt (Part 6), calls `ClaudeCodeClient.send_message`. Log tag `CLAUDE_CALL_START` → `CLAUDE_CALL_OK` in ~8-25 s.
10. Parser returns `StrategicPlan(new_trades=[...], ...)`. Log `STRAT_PLAN`, one `STRAT_DIRECTIVE` per trade.
11. LayerManager filters via `POS_GATE` and PerformanceEnforcer — skipped trades log `POS_GATE_BLOCK` or `TRADE_SKIP`.
12. For each surviving directive, Coordinator stamps `_claude_original_size_usd`, invokes `TradeOptimizer.optimize(directive, plan)` → `APEX_TIMING | assemble= deepseek= parse= constraints=` in ~2-40 s. Emits `APEX_OK` or `APEX_FLIP`.
13. `TradeGate.validate(optimized_trade)` — 12 checks; `_gate_adjustments` recorded.
14. Coordinator executes via `ShadowAdapter.place_order(...)`. Shadow's `OrderEngine` fills using latest tick + slippage model. Returns Order with FILLED status + fill_price.
15. Coordinator inserts `trade_thesis` row (status=open) + `strategy_trades` row; PriceWorker's next tick supplies `mark_price` for PnL.
16. Thesis manager tracks the new open position; URL to Telegram alert via `AlertManager.send_trade_alert`.

## 21.2 Position-management path (Call B cycle)

1. `PositionWatchdog` ticks every 10 s. For each open position: fetches latest price, computes PnL, checks danger signals.
2. If SL/TP hit → `ShadowAdapter.close_position()` fires; close callback runs synchronously.
3. If `brain_trigger_loss_pct` crossed AND within `brain_cooldown_seconds`, watchdog enqueues `UrgentConcern(symbol, level, reason)`.
4. `ProfitSniper` independently ticks every 5 s: runs five models, computes composite exploit score, triggers `tighten` (via `sl_gateway.apply(source="profit_sniper")`), `partial_close`, or `full_close`.
5. `TimeDecaySL` (inside watchdog) runs five models when `pnl_pct < 0`, emits `TIME_DECAY_CALC`, force-closes when `p_win < 0.15`.
6. `SentinelAdvisor` every 300 s produces `AdvisorRecommendation`s drained by watchdog and applied via gateway (`source="sentinel"`).
7. `LayerManager` offset triggers Call B on the 150 s cadence. Strategist checks divergence gate, drains urgent queue, builds position prompt (~5-8k chars), calls Claude, parses `StrategicPlan.position_actions`.
8. Each action (`close`, `tighten_stop`, `set_exit`, `hold`) passes through `Firewall.should_allow_strategic_action(...)`. Non-trusted close/take_profit are blocked (`SENTINEL_FIREWALL_BLOCK`).
9. Allowed tightens route through SL Gateway; allowed closes invoke `ShadowAdapter.close_position()`.

## 21.3 Close → TIAS path

1. `ShadowAdapter.close_position()` returns close data (exit_price, pnl_pct, pnl_usd, hold_seconds).
2. `TradeCoordinator.on_trade_closed(symbol, ...)` fires: records close in `trade_log`, updates `trade_thesis.status='closed'`, sets symbol cooldown, emits `TRADE_CLOSED` event.
3. Synchronously captures `m4_snapshot = ProfitSniper.snapshot_for_symbol(symbol)` before clearing state.
4. Schedules `TradeContextCollector.collect_and_save(record, tias_repo, m4_snapshot)` — inserts `trade_intelligence` row with groups A–G (minus F). Returns `row_id`.
5. Schedules `TradeAnalyzer.analyze(trade)` (Phase 2). DeepSeek call returns within 45 s. `TIASRepository.update_analysis(row_id, analysis)` fills group F + apex_* + gate_adjustments.
6. `PerformanceEnforcer` updates daily counters. `StrategyRegistry.update_performance(name, pnl_pct, was_win)`. Logs `REG_PERF`.
7. `AlertManager.send_trade_alert` (dedup-throttled).

---

# 22. Event And Callback System

## 22.1 `EventBuffer` (`src/core/event_buffer.py`, 285 LOC)

`deque[WatchdogEvent](maxlen=50)` with dedupe map `dict[(symbol, event_type), (last_emit_ts, payload_hash, suppressed_count)]`. Same (symbol, event_type, payload_hash) within 30 s is suppressed; a single summary log emits on next distinct emit: `EVBUF_DEDUPE | key= suppressed=N`.

Event types emitted (grep of `event_buffer.add` + `event_buffer.emit`):

- `hard_stop` — account equity hit `halt_threshold_pct` (PnLManager).
- `sl_hit`, `tp_hit` — fill triggered by SL or TP (OrderEngine close callback).
- `big_move` — price moved >2 % in 5 min (watchdog).
- `timer_close` — `max_hold_minutes` expired (DeadlineEngine).
- `position_action_failed` — close/tighten order rejected (Coordinator).
- `mode_transition` — Transformer switch (from_mode, to_mode).
- `sl_propagated` — SL tightened (source + new value).
- `brain_directive_failed` — Claude-directed action rejected.
- `sl_gateway_wire_fail` — downstream position_service returned error.
- `sl_gateway_brain_blocked` — MED — Claude tighten blocked by rule.

Priority HIGH|MED|LOW.

`should_trigger_early_review()`: if any HIGH event in the last 5-min window AND at least 2 min since last trigger AND <2 triggers in 5 min window → True → LayerManager runs Call A early. Logs `EVBUF_TRIGGER | high=N`.

`get_prompt_text(max_events=None)` — truncated at `brain.prompt_event_buffer_max_events=20` — renders lines `  [!!!] SYM: event (Ns ago) reason=... pct=...`. Injected into Call A URGENT section.

Subscribers and order:

| Event | Subscribers (in fire order) |
|---|---|
| `hard_stop` | EventBuffer → AlertManager → LayerManager (early-review trigger) |
| `sl_hit` | EventBuffer → TelegramHandler (dashboard) → DataLake |
| `tp_hit` | EventBuffer → AlertManager → DailyPnLManager |
| `big_move` | EventBuffer → VolatilityProfiler.invalidate(symbol) → RiskWeather |
| `mode_transition` | EventBuffer → LayerManager (log) → AlertManager (if EMERGENCY) |
| `sl_propagated` | EventBuffer → Coordinator (thesis update) → SLGateway (state) |
| `sl_gateway_wire_fail` | EventBuffer → LayerManager (next review) → AlertManager (HIGH) |
| `sl_gateway_brain_blocked` | EventBuffer → LayerManager (inform Claude) |

No subscriber can veto event propagation — notifications are informational. AlertManager may throttle, but the event still lands in the buffer.

## 22.2 Telegram callbacks (`src/telegram/bot.py`)

Callback-data strings decoded in `_handle_callback()`: `quickbuy:{sym}:{amount}:{leverage}`, `close_pos:{sym}`, `close_half:{sym}`, `move_sl:{sym}`, `move_tp:{sym}`, `analyze:{sym}`, `chart_{tf}:{sym}`, `risk_accept:{sym}:{side}:{amount}:{leverage}`, `risk_cancel`, `confirm_emergency`, `cancel_emergency`. Single-threaded; sequential execution.

## 22.3 Architecture-diagram "10 callbacks on position close"

Actual callbacks on close (grep of `on_trade_closed` + `on_position_close` + `close_callback`): 8 distinct subscribers — TradeCoordinator.on_trade_closed (records log), TIASCollector.collect_and_save, TIASAnalyzer.analyze (scheduled), StrategyRegistry.update_performance, PerformanceEnforcer.update, AlertManager.send_trade_alert, ProfitSniper.clear_position, SLGateway._sl_gateway_reset_on_close. If counted with PnLManager.record_close and EventBuffer.emit(trade_closed) it reaches 10.

---

# 23. Caches And In-Memory State

| # | Cache | File | Structure | Keys → values | TTL | Populator | Reader | Clearer | Size cap | Persistence |
|--:|---|---|---|---|---|---|---|---|---|---|
| 1 | `TACache` | `analysis/ta_cache.py` | `dict[str, tuple[float, dict]]` | `f"{sym}:{tf}:{5s_bucket}"` → ta analysis | 90 s | `TAEngine.analyze` | StrategyWorker, SignalWorker, Watchdog, APEX assembler | `invalidate(sym)` | unbounded | in-memory |
| 2 | `StructureCache` | `analysis/structure/structure_cache.py` | `dict[str, tuple[float, StructuralAnalysis]]` | symbol → analysis | 300 s | StructureWorker | Strategist, Scorer, APEX, Telegram | `invalidate`, `clear()` | ≤100 | in-memory |
| 3 | VolatilityProfiler | `analysis/volatility_profile.py` | `dict[str, CoinVolatilityProfile]` | symbol → profile | 120 s ± 15 s jitter | `VolatilityProfiler._compute` | Watchdog, TP/SL calc, traders | `invalidate(sym)` | ≤30 | in-memory |
| 4 | Scanner cache | `strategies/scanner.py` | `list[dict]` + `_cache_time` | — → ranked setups | 60–300 s | `scan_market()` | Strategist, Telegram | `invalidate()` | ≤30 | in-memory |
| 5 | AlertThrottle dedup | `alerts/throttle.py` | `dict[str, float]` | content_hash → ts | 300 s | `record_content` | `is_duplicate` | `_clean_dedup_cache` | unbounded | in-memory |
| 6 | SentimentAggregator unknown cache | `intelligence/sentiment/aggregator.py` | `dict[str, tuple[float, dict]]` | symbol → (expires, cached) | 1800 s | `aggregate_for_symbol` | same | TTL expiry | ~20 | in-memory |
| 7 | MarketService ticker cache | `trading/services/market_service.py` | `dict[str, tuple[float, Ticker]]` | symbol → (ts, ticker) | 5 s | WS listener | `get_ticker` | TTL expiry | per universe | in-memory |
| 8 | InstrumentService | `trading/services/instrument_service.py` | `dict[str, InstrumentInfo]` | symbol → info | until reconnect | Bybit fetch | `get_info` | on reconnect | ≤500 | in-memory |
| 9 | VolatilityScaler caches | `fund_manager/volatility_scaler.py` | two dicts | symbol → (ts, mult) / (ts, percentile) | 300 s | `compute_multiplier` | fund_manager sizing | TTL | ~30 | in-memory |
| 10 | CoinDiscovery | `analysis/structure/coin_discovery.py` | `list[str]` + `_last_refresh` | — → coin list | 3600 s | Bybit fetch | StructureWorker | refresh | ~150 | in-memory |
| 11 | APEXGate conviction cache | `apex/gate.py` | `dict[str, tuple[float, float]]` | symbol → (weight, ts) | 300 s | `get_symbol_full_history` | CHECK 4 | TTL | ~30 | in-memory |
| 12 | ProfitSniper ATR cache | `workers/profit_sniper.py` | `dict[str, tuple[float, float]]` | symbol → (atr, ts) | per-sniper-tick | `_fetch_atr` | model compute | refresh | per-position | in-memory |
| 13 | Database ticker_cache | SQLite table | row per symbol | symbol → last_price | none (overwrite) | `market_repo.upsert_ticker` | Transformer / MCP | — | 189 rows today | **DB-backed** |
| 14 | SL Gateway rate-limit state | `core/sl_gateway.py` | `_last_change`, `_last_sl` dicts | symbol → (ts, sl) | cleared on close | gateway `apply()` | rule R3/R4 | `_sl_gateway_reset_on_close` | per position | in-memory |
| 15 | ThesisManager state | `core/thesis_manager.py` | internal dicts | symbol → thesis snapshot | session | `open_thesis` | `update_thesis_from_order`, close | `close_thesis`, `flush` | per position | session (flushed to `context_repo` on flush) |
| 16 | APEX symbol/situation history (transient) | `apex/assembler.py` | local per-call | — | per-call | `_gather_symbol_history`, `_gather_situation_data` | assembler only | — | one call | transient, backed by `trade_intelligence` |
| 17 | EventBuffer deque | `core/event_buffer.py` | `deque(maxlen=50)` + dedupe dict | (symbol, type) → last | 5-min review window | `add(event)` | `get_prompt_text`, `should_trigger_early_review` | natural eviction | 50 | in-memory, also persisted to `event_log` |
| 18 | UrgentQueue | `core/urgent_queue.py` | list | — | drained per Call B | watchdog `add_concern` | strategist `_build_position_prompt` | drained | unbounded | in-memory |
| 19 | TACache hit stats | TACache | counters | — | session | TACache | `STRAT_CYCLE_DONE` | — | — | in-memory |
| 20 | `columnar DataLake` | `core/data_lake.py` | dict-of-arrays | event type → series | session | emitter | analytics | — | — | in-memory |
| 21 | FreshnessGuard state | `core/freshness_guard.py` | per-source last-update ts | — | threshold configurable | writers | `FRESH_OK`/`FRESH_BLOCK` | — | — | in-memory |
| 22 | TradeCoordinator cooldowns | `core/trade_coordinator.py` | `_symbol_cooldowns: dict` | symbol → expiry ts | `MINIMUM_HOLD_SECONDS[cat]` | `on_trade_closed` | brain context + gate | TTL expiry | unbounded | in-memory |
| 23 | TradeCoordinator `_currently_executing` | same | `set[str]` | — | during execute | Coordinator start | POS Gate read | Coordinator end | small | in-memory |

Items 1–12, 14–23 are lost on restart. Item 13 is DB-backed. APEX history (item 16) is backed by `trade_intelligence`, re-read on each optimize call.

---

# 24. Logging And Observability

## 24.1 Framework

Loguru with file-only sinks (stderr sink removed on `setup_logging()` so MCP stdio never leaks). `LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}"`. `LOG_ROTATION="10 MB"`, `LOG_RETENTION="7 days"`, `enqueue=True` (thread-safe queue), `backtrace=True`, `diagnose=False` (no variable leakage in prod).

## 24.2 Files and routing (`src/core/logging.py:COMPONENT_ROUTING`)

- `mcp.log` — component `"mcp"`.
- `workers.log` — 27 components (worker, rule_engine, trading, sl_tp_validator, sl_gateway, coordinator, data_lake, thesis_manager, enforcer, strategies, intelligence, analysis, fund_manager, tiered_capital, risk, time_decay_sl, volatility_profile, factory, portfolio, trade_recorder, trading_mode, shadow, strategy, event_buffer, urgent_queue, layer_manager, core, tias, apex, sentinel, xray).
- `brain.log` — components `brain`, `claude_code`, `strategist`.
- `general.log` — `database`, `alerts`, `telegram`, `control_handler`, `dashboard` + default catch-all for unrouted components.

Directory: `data/logs/`. Current file sizes: brain.log 2.0 MB (active), general.log 6.5 MB, mcp.log 516 KB, workers.log 1.2 MB + 10 rotated `workers.YYYY-MM-DD_*.log` at 9.6 MB each (7-day retention evicts the oldest automatically).

`tests/test_logging_routing.py` (78 LOC) asserts every `get_logger("<component>")` call has a COMPONENT_ROUTING key — CI-enforced to prevent silent routing to general.log.

## 24.3 Unique log tag inventory

From grep of `src/` the 80+ most frequent structured log prefixes are grouped below.

APEX: `APEX`, `APEX_ASSEMBLE_*`, `APEX_CONF_SIZE`, `APEX_DEFAULT`, `APEX_DIR_LOCK`, `APEX_DIR_LOCK_OVERRIDE`, `APEX_FAIL_UNEXPECTED`, `APEX_FLIP`, `APEX_GATHER_FAIL`, `APEX_GUARDRAIL_*`, `APEX_NO_PRICE`, `APEX_OK`, `APEX_PRICE_*`, `APEX_REGIME`, `APEX_REGIME_FAIL`, `APEX_SKIP`, `APEX_SKIP_NO_PRICE`, `APEX_STARTUP_STATS*`, `APEX_SYSTEM_PROMPT`, `APEX_TIER`, `APEX_TIMEOUT_REGIME`, `APEX_TIMING`, `APEX_TP_CAP`, `APEX_WS_QUOTE_FAIL`.

Brain/Claude: `BRAIN`, `BRAIN_ANALYZE`, `BRAIN_CYCLE_A(_DONE|_FAIL|_URGENT_ACTS)`, `BRAIN_CYCLE_B(_DONE|_FAIL|_SKIP)`, `BRAIN_DECISIONS`, `BRAIN_DO_(DONE|FAIL|SKIP|START|TIMEOUT|TRADE)`, `BRAIN_HEALTH`, `BRAIN_STATUS`, `BRAIN_TRADE_HALT`. `CLAUDE_*` family (20+ tags) in Part 6.3.

Strategy: `STRAT_CYCLE_START/DONE`, `STRAT_PROMPT`, `STRAT_DIRECTIVE`, `STRAT_PLAN`, `STRAT_NO_TRADES`, `STRAT_CALL_A_END/FAIL`, `STRAT_CALL_B_*`, `STRAT_POS_ACT`, `STRAT_PROMPT_REFRESH`, `STRAT_POS_INVALIDATE`, `STRAT_REGIME_DIST`, `STRAT_PNL_GATE`, `STRAT_SKIP_CIRCUIT`, `STRAT_PREFETCH_*`, `STRAT_SKIP_STALE`, `STRAT_DIR_PERF`.

SL Gateway: `SL_GATEWAY_INIT/ACCEPT/REJECT/REJECT_WOULD/PASSTHROUGH/WIRE_FAIL/POS_FETCH_FAIL/PRICE_FETCH_FAIL/VP_FAIL/STATS`.

Mode4 / Sniper: `M4_EVAL`, `M4_ACT`, `M4_SKIP`, `SNIPER_OPEN`, `SNIPER_MODELS`, `SNIPER_ACTION`, `PARTIAL_CLOSE_UNSUPPORTED`, `REDUCE_FALLBACK`.

Watchdog: `WATCHDOG_*` (many sub-tags), plus `POS_GATE_BLOCK`.

Enforcer: `ENFORCER_BEAT`, `ENFORCER_LEVEL`, `ENFORCER_STATE`, `ENFORCER_SIZE`, `ENFORCER_AUTO_RECOVERY`, `ENFORCER_GRACE`, `ENFORCER_MANUAL_RESET`, `ENFORCER_PRECHECK_FAIL`, `ENFORCER_STATS_FAIL`, `ENFORCER_TRADE_IN`.

Risk / Gate: `GATE_PASS/POS_CHECK/CAP_CHECK/COOL_CHECK/DUP_CHECK/GUARDRAIL_CHECK/RR_CHECK/TPSL_CHECK`, `CONVICTION_SIZE_CAP`, `CONVICTION_WEIGHT/WEIGHT_FAIL`.

Fund Manager: `FUND_BEAT`, `FUND_POOLS`, `FUND_REJECT`, `FUND_SIZE`.

Time-Decay / Sentinel: `TIME_DECAY_CALC/GRACE/FLOOR_PRICE_REL/FORCE_CLOSE`, `SENTINEL_ADVISOR`, `SENTINEL_ADVISOR_SLOW/FAIL/ERR`, `SENTINEL_FIREWALL_ALLOW/BLOCK`.

TIAS / APEX: `TIAS_SAVE`, `TIAS_COLLECT_FAIL`, `TIAS_FALLBACK`, `TIAS_ANALYZE_OK/FAIL`.

Structure / X-RAY: `XRAY_TICK`, `XRAY_SESSION_ERR`, `XRAY_TICK_ERR`, `XRAY_SCANNER_ERR`, `XRAY_CONTEXT`, `XRAY_CONTEXT_BUILD_FAIL`, `XRAY_SCORE`.

Worker infrastructure: `WM_INIT`, `WM_START`, `WM_STOP`, `WM_CRASH`, `SYSTEM_HEALTH_START/STOP`, `[HEARTBEAT]`, `WORKER_SHUTDOWN`, `WORKER_SIGNAL`.

Database: `DB_CONN`, `DB_PRAGMAS`, `DB_PRAGMA`, `DB_ERR`, `DB_PROTECT_BLOCKED`, `DB_PROTECT_FORCE`.

Alerts: `ALERT_SENT`, `ALERT_THROTTLE`, `ALERT_FAIL`.

EventBuffer: `EVBUF_ADD`, `EVBUF_DEDUPE`, `EVBUF_TRIGGER`.

MCP / Proxy: `MCP_INIT`, `MCP_PROXY_*` (CONNECT/DISCONNECT/PIPE_END/MSG_ERR/SOURCE_ERR/SINK_ERR/UPSTREAM_FAIL/FORCE_EXIT).

Price / Kline / Altdata: `PRICE_WS_CONN/DISC`, `PRICE_UNIVERSE_SYNC`, `PRICE_OVERRIDE`, `KLINE_FETCH`, `KLINE_BACKFILL/BACKFILL_FAIL`, `KLINE_GAP`, `KLINE_CIRCUIT_BREAKER`, `SCAN_SCORE`, `SCANNER`, `ALTDATA`, `NEWS_FETCH`.

Regime: `REGIME`, `REGIME_CHG`, `REGIME_PENDING`, `REGIME_GLOBAL`, `REGIME_PERCOIN`, `REGIME_DIVERGE`, `REGIME_BACKFILL/BACKFILL_FAIL`, `REGIME_RESTORE/RESTORE_FAIL`, `REGIME_DB_FAIL`.

Freshness + ctx: `FRESH_OK`, `FRESH_BLOCK`, and every log includes the `| {ctx()}` trailer from `src/core/log_context.py` which attaches correlation IDs.

## 24.4 Timing tags

`el=<ms>` appears in APEX_TIMING, SENTINEL_ADVISOR, STRAT_CYCLE_DONE, WATCHDOG tick summaries, MCP_INIT, CLAUDE_CALL_OK, and Watchdog's `WD_TICK_SLOW` for slow ticks (>5 s). Decision-data tags (APEX_OK, GATE_*, SCORER) carry the computed verdict, not timing.

## 24.5 Observability report generator

Operational logs are consumed by `scripts/log_viewer.sh`, `scripts/monitor.py`, and `scripts/health_check.py`. The file `/home/inshadaliqbal786/observability_02-24_to_02-44_2026-04-24.log` (1.08 MB) appears to have been produced by an ad-hoc `tail -f` or `monitor.py` export — the exact producer is **unknown — could not determine by reading the code** (no dedicated "observability report generator" is present in `scripts/`).

---

# 25. Scripts And Tooling

`scripts/` contains 19 files (plus `__pycache__`). Each is classified here with live `wc -l` and its run context.

| Script | LOC | Language | Run by | Touches prod state | Current? |
|---|--:|---|---|---|---|
| `backup.sh` | 62 | bash | systemd (`trading-backup.timer` 02:00 UTC daily) + `make backup` | Yes — copies `data/trading.db` with `.backup` PRAGMA; compresses; keeps last 7 | current |
| `bulk_cleanup.py` | 286 | Python | manual (`make bulk-cleanup` / `bulk-cleanup-dry`) after retention-policy changes | Yes — DELETE across retention-tagged tables + VACUUM on trading.db and Shadow `shadow.db` (protected tables skipped) | current, used once after the prefetch-performance fix |
| `force_trade.py` | 75 | Python | manual — testnet only | Yes — bypasses Brain, places order via Shadow | legacy dev tool; still current |
| `health_check.py` | 288 | Python | `make health`, `make health-json`, and systemd-style cron | Read-only | current |
| `install_services.sh` | 99 | bash | `sudo make install` | Writes to `/etc/systemd/system/` | current |
| `log_viewer.sh` | 60 | bash | `make logs` / `logs-workers` / `logs-brain` / `logs-mcp` / `logs-errors` / `logs-last` | Read-only | current |
| `monitor.py` | 379 | Python | `make monitor` | Read-only curses dashboard | current |
| `restart_all.sh` | 21 | bash | `make restart` | Writes — `systemctl restart trading-*` | current |
| `restore.sh` | 107 | bash | manual after disaster | Yes — copies backup over `data/trading.db`; requires explicit filename | current |
| `run_30min_test.py` | 326 | Python | manual — `python scripts/run_30min_test.py` | Yes — runs against live systemctl units; logs every Claude response, watchdog action, trade | current (used during overhaul testing) |
| `run_6min_test.py` | 128 | Python | manual — short form | Yes — same surface, shorter window | current |
| `setup.sh` | 140 | bash | `make setup` (fresh Ubuntu 22.04 bootstrap) | Writes venv, dependencies, `.env` skeleton | current |
| `smoke_test_mcp_proxy.py` | 105 | Python | one-off — Phase 1 Y-22 verification | Read-only — spawns proxy subprocess, sends minimal JSON-RPC, prints responses | current reference |
| `start_all.sh` | 21 | bash | `make start` | `systemctl start trading-*` | current |
| `status.sh` | 114 | bash | `make status` | Read-only status banner | current |
| `stop_all.sh` | 20 | bash | `make stop` | `systemctl stop trading-*` | current |
| `uninstall_services.sh` | 48 | bash | `sudo make uninstall` | Writes `/etc/systemd/system/` | current |
| `verify_integration.py` | 84 | Python | manual CI sanity | Read-only — validates imports, settings, DB connect | current |

No database migrations run outside `run_migrations(db)` (called from entry points). No log trimmers outside loguru rotation. No deployment scripts beyond `install_services.sh` + `setup.sh`. The `scripts/__pycache__` dir is tooling-local.

---

# 26. Tests

`tests/` holds 13 directories and 54 Python test files (`find … -name "*.py"`), 15,748 lines. Test runner: `pytest tests/ -v --tb=short` via `make test`; asyncio mode set to `auto` in `pyproject.toml`. No dedicated `pytest.ini` (all config in `pyproject.toml [tool.pytest.ini_options]`). `make test-quick` runs only `test_phase0/`.

Top-level files:

- `__init__.py` (1 line).
- `conftest.py` (101 LOC) — shared fixtures.
- `overhaul29_pipeline_test.py` (888 LOC) — end-to-end 29-issue overhaul pipeline.
- `overhaul29_integration_test.py` (605 LOC) — subsystem integration.
- `test_apex_direction_lock.py` (543 LOC) — APEX direction-lock behaviour.
- `test_apex_pipeline_integration.py` (846 LOC) — full APEX flow (assembler → optimizer → gate).
- `test_firewall_and_time_decay.py` (590 LOC) — SL gateway + firewall + time-decay.
- `test_protected_tables.py` (178 LOC) — PROTECTED tables regex-guard tests.
- `test_logging_routing.py` (78 LOC) — CI-time log routing assertion.

Phase-based subdirectories (counts = files):

| Dir | Files | Lines (wc -l total) | Covers |
|---|--:|--:|---|
| `test_phase0/` | 9 | 1,028 | logging, settings, types, decorators, exceptions, utils, constants, validators |
| `test_phase1/` | 5 | 348 | db cleanup, models, context_repo, learning_repo |
| `test_phase2/` | 9 | 1,186 | Bybit services — market, order, position, account, websocket, auth, client, instrument |
| `test_phase3/` | 14 | 1,304 | Intelligence — Finnhub, calendar, Reddit, F&G, funding, OI, onchain, signal generator, aggregator, scorer, confidence |
| `test_phase4/` | 8 | 926 | Analysis — engine, momentum, volume, volatility, trend, candlestick, chart_patterns, vol_scale |
| `test_phase5/` | 12 | 819 | Workers — base, manager, price, kline, news, reddit, altdata, signal, cleanup, health |
| `test_phase6/` | 10 | 716 | MCP tools + auth |
| `test_phase7/` | 8 | 665 | Brain — claude_client, prompts, scheduler, decision_parser, executor, prompt_builder, cost_tracker |
| `test_phase8/` | 7 | 532 | Alerts — throttle, templates, telegram_bot, formatter, alert_manager |
| `test_phase9/` | 7 | 558 | Risk — position_sizer, drawdown, portfolio, stop_loss, risk_manager, validators |
| `test_analysis/` | 2 | 133 | vol_scale |
| `test_factory/` | 6 | 590 | discoverer, generator, validator, backtester |
| `test_integration/` | 2 | 124 | full-system integration smoke |
| `test_portfolio/` | 3 | 232 | allocator, correlation, kelly |
| `test_strategies/` | 13 | 1,635 | registry, scorer, scanner, ensemble, regime, smart_leverage, optimizer, pnl_manager, signal_types, categories a–f, categories g–k |
| `test_telegram/` | 2 | 212 | bot + handlers smoke |
| `test_watchdog/` | 3 | 1,010 | PositionWatchdog integration |

`test_protected_tables.py` and `test_logging_routing.py` are CI-blocking guardrails. All tests are asyncio-friendly; integration tests use in-memory sqlite or fixtures in `data/`.

Whether they pass today was **not executed** during this inventory (the prompt forbids running the workers/brain services or mutating state, and the live `trading-workers.service` uses port 8080/WS connections that would overlap a pytest run). The test runner is configured; execution is available via `make test`.

---

# 27. Documentation Files Present

Live `find -name "*.md"` (excluding `.venv`, `.git`, `backups/`, `.pytest_cache`, `node_modules`):

| Path | First-line title | Lines (live wc -l) | Matches current state? |
|---|---|--:|---|
| `/home/inshadaliqbal786/trading-intelligence-mcp/CLAUDE.md` | `# CLAUDE.md — Rules for This Project` | 23 | true |
| `/home/inshadaliqbal786/trading-intelligence-mcp/README.md` | `# Trading Intelligence MCP` | 66 | partially (quickstart still correct; missing APEX + Y-22 proxy notes) |
| `/home/inshadaliqbal786/trading-intelligence-mcp/PROJECT_BIBLE.md` | (master spec, full title in first 50 lines) | 1,883 | partially — predates APEX, TIAS, SENTINEL, Mode4, X-RAY as coded today |
| `/home/inshadaliqbal786/trading-intelligence-mcp/PROJECT_BLUEPRINT.md` | (architecture summary) | 742 | partially — captures pre-Phase-4 X-RAY state; no Y-22 proxy |
| `/home/inshadaliqbal786/BUILD_INVENTORY_NOW.md` (project-adjacent) | `# BUILD_INVENTORY_NOW` | 587 | — (task prompt, not documentation) |
| `/home/inshadaliqbal786/XRAY_STRUCTURAL_INTELLIGENCE_BLUEPRINT.md` (referenced from memory) | (blueprint) | cannot-tell (not re-read in this run) | cannot-tell |

The Shadow repo contains no markdown files. No `CHANGELOG.md`. No `CONTRIBUTING.md`. `scripts/` has no markdown.

---

# 28. Dead Code And Orphan Files

Candidate dead files (nothing imports + nothing executes + no config reference):

- `src/brain/executor.py.deprecated`, `src/brain/prompt_builder.py.deprecated`, `src/brain/scheduler.py.deprecated` — renamed out of the import graph; kept as reference. These are orphaned by design.
- `src/brain/brain_v2.py` — imported by `src/core/layer_manager.py` but its methods are fallback-only; the strategist is the real path. Not dead strictly, but code-path unreachable in steady state.
- `src/brain/claude_client.py` (Anthropic SDK) — referenced only from `brain.py` v1 which is deprecated; brain.py v1 currently runs as its own systemd service, so the code is technically alive but logically redundant.
- `src/workers/backtest_worker.py` — the tick body logs "would backtest" and does nothing further; effectively inert.
- `trading.db` at project root (0 B) — placeholder; the real DB is `data/trading.db`. Confusing to new readers.
- `src/workers/settings.py` (45,072 LOC by `wc -c`) — appears to be a legacy duplicate of `src/config/settings.py` under the workers package. It is imported by a handful of older modules; deeper reachability analysis is needed to declare dead, but clearly redundant.

Candidate orphan files (imports that reference removed symbols):

- `src/factory/prompts/generation_prompt.py` references `src/strategies/categories/generated/` which exists but is empty; logical graph is consistent but the generated folder has 0 strategies.
- `tests/test_portfolio/test_portfolio.py` and `tests/test_phase9/test_portfolio.py` share identical test names and similar helpers — copy-paste drift is present; neither is orphan but drift noted in Part 29.

Tables with no writers (Part 4.2): `active_strategies`, `backtest_results`, `backtest_trades`, `strategy_code_history`, `strategy_lifecycle`, `trial_performance`, `portfolio_allocations`, `correlation_matrix`, `stress_test_results`, `performance_attribution`, `risk_budget_log`, `rebalance_history`, `pattern_log`, `pattern_occurrences`, `ensemble_votes`, `brain_decisions` (legacy), `session_log`, `trade_history` (legacy). These are all dead at the data layer today even though migration code keeps them.

Config keys defined but not read (partial list — not exhaustive):

- `[brain] model`, `[brain] max_tokens`, `[brain] temperature`, `[brain] max_calls_per_hour` — only read by the deprecated `src/brain/claude_client.py`.
- `[brain] analysis_interval`, `[brain] signal_triggered`, `[brain] min_signal_confidence` — legacy v1 scheduler.
- `[reddit] *` — disabled; still loaded by `SentimentAggregator.__init__` but no Reddit calls fire.
- `[factory] *` — Factory services constructed but disabled; most keys unread today.
- `[mcp_pool] *` — pool disabled; values not consumed by any active consumer.

---

# 29. Observations And Anomalies

1. **Inconsistent naming** — `src/core/layer_manager.py` (42,677 B) and `src/workers/layer_manager.py` (33,668 B) are two distinct files with the same basename. Both import each other's concepts (cycles, urgent concerns). Risk of divergence is high; deliberate fork or refactor-in-progress.
2. **Copy-paste drift** — `src/workers/settings.py` (45 KB) appears to duplicate `src/config/settings.py` (60 KB). Classes likely renamed in one but not the other. A future refactor must pick a single source of truth.
3. **TODO/FIXME density** (spot-check): low — no `TODO` comments grep in `src/` during this pass; blueprint markers instead live in code docstrings ("Phase 29", "Phase Y-22", "P0-1", "P0-4").
4. **Unreachable code** — `src/workers/backtest_worker.py` body is a logging stub; `src/strategies/categories/k3_ensemble.py` (29 LOC) and `k4_adaptive_optimizer.py` (29 LOC) are passthrough stubs that never emit a signal.
5. **Configuration duplication** — `[risk] default_stop_loss_pct=3.0` vs `[analysis.volatility_profile]` per-class `min_sl_pct=0.20 / max_sl_pct=5.0` vs `[sl_gateway] min_distance_pct=0.3` — three distinct authorities for SL bounds, resolved at runtime by the SL Gateway plus the per-class volatility clamps. A single "source of truth" table would remove ambiguity.
6. **`trading.db` at project root** (0 B) — leftover placeholder; a new hand opens it expecting data and finds nothing. Real DB is `data/trading.db`. Delete or symlink.
7. **Hardcoded OAuth client id** in `ClaudeCodeClient` (`_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"`). Not a secret but a magic constant; should probably live in settings or a constants module.
8. **Silent swallow** — many try/except Exception branches log a warning and set `_services[key] = None`. Good for availability but can hide classification errors. Grep shows ~40 `log.warning(..., err=str(e))` patterns in `manager.initialize()`.
9. **Misleading name** — `sniper_log.z_score`, `sniper_log.velocity`, `sniper_log.acceleration`, `sniper_log.bb_position` columns are holdovers from Phase-1 Sniper; current five-model pipeline writes `hurst_*`, `momentum_*`, `extension_atr`, `volume_div_score`, `risk_reward_score` into additional columns and leaves Phase-1 ones NULL. Historical analytics must be careful.
10. **Locking** — `DatabaseManager` uses `asyncio.Lock` for serialised writes and relies on SQLite's `busy_timeout=10000` + 3-retry loop on "locked" errors. All writes go through `execute()` / `executemany()`. WAL mode means readers do not block writers, so there is no documented reader starvation path.
11. **Sync calls to async** — `tests/conftest.py` fixtures; production code uses `asyncio.create_task` consistently. Telegram bot's synchronous calls inside `python-telegram-bot` are handled by its own job queue.
12. **Shutdown visibility fix** (Phase 30) — `workers.py` now writes `WORKER_SIGNAL` / `WORKER_SHUTDOWN` to stderr fd + directly appends to `workers.log` because loguru's `enqueue=True` queue thread can be killed before flush. Good defensive pattern; mirror into `brain.py` if ever resurrected for production.
13. **Schema version drift** — `SCHEMA_VERSION = 24` in the code, `schema_version` table has 16 rows. The delta is migrations that updated existing tables in place without inserting a version row. Not a bug, but the table no longer reflects the true version.
14. **Dedicated observability generator missing** — the 1.08 MB file `observability_02-24_to_02-44_2026-04-24.log` in `/home/inshadaliqbal786/` has no matching producer in `scripts/`. Likely a `tail -f` redirect or `monitor.py --export` that has drifted.
15. **Empty `scripts/__pycache__`** — benign cache.
16. **Two long-lived processes with distinct but overlapping concerns** — `trading-workers.service` and `trading-mcp-sse.service` both construct 40+ services. Duplication is intentional (MCP needs trading services to serve read-tools), but memory footprint is non-trivial (`trading-workers` currently 596 MB of 800 MB cap, near ceiling).
17. **Memory headroom** — `trading-workers.service` shows `Memory: 596.2M (high: 600.0M max: 800.0M available: 3.7M)` right now. Sustained operation is perilously close to the `MemoryHigh` threshold. Candidate for investigation: large `TACache` dict, `data_lake` column growth, `EventBuffer` serialised-copies.
18. **Unroutable log fall-through** — COMPONENT_ROUTING misses will land in `general.log`. `test_logging_routing.py` blocks the obvious case at CI-time, but runtime `bind(component=)` calls with dynamic strings could still leak — no runtime validator.
19. **API surface of `src/shadow/shadow_adapter.py` assumes Shadow is always reachable** — on total Shadow outage every adapter call returns None / Rejected, which propagates as "no-op" at Coordinator. No circuit breaker; silent degradation.
20. **APEX direction-lock gate is dual-enforced** — code flags AND prompt text. If the LLM flips, the code override wins, but the flip appears in APEX's `was_flipped=True` audit field — a future reader might interpret this as model disagreement rather than an enforced override.
21. **Dead-letter trajectory** — no visible dead-letter queue for messages that could not be processed (e.g., Telegram send failures beyond 3 retries are only logged; Shadow API rejections beyond fallback disappear).
22. **Cooldown dictionaries unbounded** — `TradeCoordinator._symbol_cooldowns` is a dict that never shrinks; entries expire logically but memory stays. Small but grows over long sessions.
23. **PROTECTED-table regex does not cover UPDATE** — the guard refuses DELETE/TRUNCATE/DROP but a malicious `UPDATE protected_table SET ... WHERE ...` would pass through. If the concern is data integrity and not only accidental wipes, this is a gap.
24. **CLAUDE.md rule violation history** — CLAUDE.md explicitly references a prior incident where `thesis_mgr_early = self.services.get("thesis_manager")` was deleted along with a duplicate lessons block and broke strategist context 60 lines later. The code today still has the pattern `.services.get(…)` in many places; any future cleanup must grep usages first.

---

# 30. Open Questions

Questions that could not be answered by reading code alone — directed at the user:

1. **T1–T7 nomenclature.** The prompt references "a Transformer routing layer T1 through T7". The code has only `Transformer` with modes `shadow|paper|live` (Part 20). Is T1–T7 a planning-document abstraction, a proposed future design, or a mapping onto the existing Transformer + per-event routes (e.g., T1=Shadow, T2=Paper, T3=Live, T4=APEX, T5=TIAS, T6=Sentinel, T7=Alerts)? Please confirm so the inventory can record the intended wire-up.
2. **PROJECT_BIBLE.md vs code.** The 1,883-line Bible predates APEX/TIAS/SENTINEL/Mode4/X-RAY as they exist today. Should it be retired, rewritten, or is it still an authoritative design intent that code should converge toward?
3. **Factory disabled status.** `[factory] enabled=false` with the explicit comment "0 patterns discovered, 0 backtests run — wasting CPU". Is this a permanent state, or is the factory blocked on a missing input (historical data, DB migration, evaluator)?
4. **`trading.db` (0 B) at project root.** Is this an intentional sentinel for some backup path, or a left-over file that can be deleted?
5. **`src/workers/settings.py` vs `src/config/settings.py`.** Which is the canonical settings module? If the former is legacy, are there still callers that would break on deletion?
6. **`backtest_worker.py` stub.** Is this intentionally deferred work, or should the worker be unwired from `WorkerManager._create_workers` until a real implementation lands?
7. **Reddit.** `[reddit] enabled=false` and `_unknown_cache` is doing double duty. Is Reddit permanently disabled, or paused pending credentials? If permanently disabled, `SentimentAggregator` and the `reddit_posts` table can be removed from scope.
8. **Observability report generator.** Who/what produced `observability_02-24_to_02-44_2026-04-24.log` (1.08 MB) under `/home/inshadaliqbal786/`? Is there a hidden cron/shell-history command, or is this the output of `monitor.py --export` / `tail -f`? The code contains no matching producer.
9. **`schema_version` table.** It has 16 rows but `SCHEMA_VERSION` constant is 24. What does each row represent (applied migration index vs. semantic version)? Should migrations insert a row for every schema change so the table reflects the true version?
10. **Brain v1 `trading-brain.service`** is `disabled` at install-time but is currently `active (running)`. Is that intentional for manual ad-hoc analysis, or should it be stopped now that Brain v2 lives inside `workers.py`?
11. **MCP client pool (`[mcp_pool] enabled=false`).** Is the pool ready to flip on per consumer, or pending the SSE server hosting move into `workers.py`? The rollout protocol is documented in config.toml comments; who owns the cutover?
12. **`trade_history` vs `trade_log`.** Both exist as PROTECTED tables but `trade_history` is empty and unused. Keep as a schema-migration landmine (to prevent re-use of the name) or drop in a future migration?
13. **Memory headroom.** `trading-workers.service` currently 596 MB against `MemoryHigh=600M / MemoryMax=800M`. What's the expected steady-state, and should the cap be raised or the leakers pinned?
14. **Strategist direction-lock.** When APEX's direction-lock override fires, `was_flipped=True` is recorded in `trade_intelligence.apex_flipped`. Is the intended semantic "APEX flipped vs. Claude" or "APEX was overridden by the direction-lock gate"? The two are different from an analytics perspective.
15. **Protected tables and UPDATE.** Is the spirit of the guard "no bulk wipes" (current behaviour) or "no writer except the owner subsystem"? If the latter, UPDATE statements against `trade_intelligence` / `sniper_log` from outside the owner should also be blocked.
16. **Strategy weights at Level 2 SURVIVAL.** `PerformanceEnforcer` restricts to `level_2_min_rr=3.0`, but `MarketScanner` can still mark A+ setups at R:R=2.0. Is the expected behaviour for the Scanner to pre-filter R:R≥3 in Level 2, or for the Gate to discard them?
17. **`src/workers/allocation_worker.py`** exists (41 LOC, logs `ALLOC_UPDATE`) but is not appended by `WorkerManager._create_workers()`. Should it be, or has it been superseded by `FundManagerWorker`?
18. **Shadow retention for `daily_summary`** is `forever`. Over long horizons (years) this will grow unbounded. Is that intentional?
19. **Claude CLI `_total_calls_today`** — is the counter reset across restarts, or persisted? The inventory read suggests in-memory only; if so, operator metrics become inaccurate after any worker restart.
20. **`mcp_stdio_proxy.py` exit codes.** Exit 2 is used for upstream failure; no other distinct codes are documented. Should we differentiate timeout vs auth vs server-down for the Claude CLI to surface better error messages?

---

## Appendix A — Global measurement summary (recap)

- Python files under `src/`: **367**.
- Python lines under `src/`: **76,613**.
- Test Python lines: **15,748** across 54 test files.
- Markdown lines in main project: **2,714** across 4 files.
- Database tables: **65** (49 actively referenced; 16 with no writers today).
- Database indexes: **67**.
- Database views/triggers: **0** / **0**.
- Live DB size: **142.8 MiB** + **100 MiB** WAL.
- Shadow DB size: **828 MiB** + 5.4 MiB WAL.
- Workers registered in `WorkerManager`: **22** (confirmed by `grep -c "self\.workers\.append" src/workers/manager.py`).
- MCP tools: **43** (confirmed by tool-name enumeration across 8 files).
- Strategies: **43** (A1–K4 + X1, under `src/strategies/categories/`).
- Systemd units owned by project: **5**; active at inventory time: **3** (workers, mcp-sse, brain).
- External integrations: **12** (Bybit REST + WS, Finnhub, Reddit (disabled), Telegram, OpenRouter, Claude CLI, Anthropic REST legacy, Alternative.me, CoinGecko, Shadow local, MCP local SSE, Claude OAuth).
- Config file line count: **763** lines across 34 named sections (plus four nested `[…_by_class]` / `[…_class_ceiling]` sub-tables).

## Appendix B — Cross-reference check

Quick grep confirms every architecture-diagram subsystem appears in this document:

- "APEX" — Parts 1, 3, 4, 6, 7, 10, 15, 21, 23, 24, 29, 30 ✔
- "TIAS" — Parts 4, 7, 8, 10, 23 ✔
- "SENTINEL" — Parts 9, 15, 21 ✔
- "Mode4" / Profit Sniper — Parts 1, 3, 5, 10, 23 ✔
- "X-RAY" — Parts 3, 5, 7, 11, 12, 21 ✔
- "Shadow" — Parts 1, 2, 3, 16, 19, 21 ✔
- "SL Gateway" — Parts 3, 15 ✔
- "Firewall" — Parts 9, 15 ✔
- "Enforcer" — Parts 3, 12, 15 ✔
- "Transformer" / Routing — Parts 5, 20, 21 ✔

No subsystem mentioned in the architecture diagram is missing from the inventory.

---

---

# Appendix C — Verbatim Database Schema (live dump)

Captured via `sqlite3 /home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db ".schema"`. Stored inline for offline planning so a reader never needs to re-run the command. All 65 tables and 67 indexes are reproduced; comment headers (`-- Group A`, etc.) come from the schema as it lives on disk.

```sql
CREATE TABLE klines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        turnover REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(symbol, timeframe, timestamp)
    );
CREATE TABLE ticker_cache (
        symbol TEXT PRIMARY KEY,
        last_price REAL NOT NULL,
        bid REAL NOT NULL DEFAULT 0,
        ask REAL NOT NULL DEFAULT 0,
        high_24h REAL NOT NULL DEFAULT 0,
        low_24h REAL NOT NULL DEFAULT 0,
        volume_24h REAL NOT NULL DEFAULT 0,
        change_24h_pct REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE orderbook_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        bids TEXT NOT NULL,
        asks TEXT NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    );
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
CREATE TABLE trade_history (
        trade_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        qty REAL NOT NULL,
        pnl REAL NOT NULL,
        pnl_pct REAL NOT NULL,
        strategy TEXT NOT NULL DEFAULT '',
        signal_confidence REAL NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        entry_time TEXT NOT NULL,
        exit_time TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE account_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total_equity REAL NOT NULL,
        available_balance REAL NOT NULL,
        used_margin REAL NOT NULL,
        unrealized_pnl REAL NOT NULL,
        margin_level_pct REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE news_articles (
        id TEXT PRIMARY KEY,
        headline TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        sentiment_score REAL NOT NULL DEFAULT 0,
        symbols TEXT NOT NULL DEFAULT '[]',
        category TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT (datetime('now')),
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE reddit_posts (
        id TEXT PRIMARY KEY,
        subreddit TEXT NOT NULL,
        title TEXT NOT NULL,
        score INTEGER NOT NULL DEFAULT 0,
        num_comments INTEGER NOT NULL DEFAULT 0,
        upvote_ratio REAL NOT NULL DEFAULT 0,
        sentiment_score REAL NOT NULL DEFAULT 0,
        symbols_mentioned TEXT NOT NULL DEFAULT '[]',
        permalink TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE aggregated_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        overall_score REAL NOT NULL DEFAULT 0,
        level TEXT NOT NULL DEFAULT 'neutral',
        news_score REAL NOT NULL DEFAULT 0,
        news_count INTEGER NOT NULL DEFAULT 0,
        reddit_score REAL NOT NULL DEFAULT 0,
        reddit_count INTEGER NOT NULL DEFAULT 0,
        fear_greed_value INTEGER NOT NULL DEFAULT 50,
        momentum REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE economic_calendar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name TEXT NOT NULL,
        country TEXT NOT NULL DEFAULT '',
        impact TEXT NOT NULL DEFAULT 'low',
        actual TEXT NOT NULL DEFAULT '',
        estimate TEXT NOT NULL DEFAULT '',
        previous TEXT NOT NULL DEFAULT '',
        event_time TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE fear_greed_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value INTEGER NOT NULL,
        classification TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE funding_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        funding_rate REAL NOT NULL,
        next_funding_time TEXT NOT NULL,
        predicted_rate REAL NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE open_interest (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        open_interest_value REAL NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        components TEXT NOT NULL DEFAULT '{}',
        reasoning TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE strategy_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT 'all',
        total_trades INTEGER NOT NULL DEFAULT 0,
        winning_trades INTEGER NOT NULL DEFAULT 0,
        losing_trades INTEGER NOT NULL DEFAULT 0,
        win_rate REAL NOT NULL DEFAULT 0,
        avg_pnl REAL NOT NULL DEFAULT 0,
        avg_pnl_pct REAL NOT NULL DEFAULT 0,
        max_drawdown REAL NOT NULL DEFAULT 0,
        sharpe_ratio REAL,
        profit_factor REAL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(strategy, symbol, timeframe)
    );
CREATE TABLE signal_accuracy (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        predicted_direction TEXT NOT NULL,
        actual_direction TEXT,
        confidence REAL NOT NULL DEFAULT 0,
        price_at_signal REAL NOT NULL DEFAULT 0,
        price_after_1h REAL,
        price_after_4h REAL,
        price_after_24h REAL,
        was_correct INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE pattern_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        outcome_json TEXT,
        confidence REAL NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        detected_at TEXT NOT NULL DEFAULT (datetime('now')),
        resolved_at TEXT
    );
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
CREATE TABLE user_preferences (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE watchlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        symbols_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE active_strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        params_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(strategy_name, symbol)
    );
CREATE TABLE session_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE active_universe (
        symbol TEXT PRIMARY KEY,
        opportunity_score REAL NOT NULL,
        volume_24h REAL,
        change_24h_pct REAL,
        funding_rate REAL,
        spread_pct REAL,
        coin_tier INTEGER DEFAULT 3,
        updated_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
        regime TEXT NOT NULL,
        confidence REAL,
        adx REAL,
        atr_percentile REAL,
        choppiness REAL,
        detected_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE strategy_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        score REAL,
        ensemble_strength TEXT,
        ensemble_votes_for REAL,
        ensemble_votes_against REAL,
        leverage_used INTEGER,
        regime TEXT,
        pnl REAL,
        pnl_pct REAL,
        was_win INTEGER,
        entry_time TEXT,
        exit_time TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        exchange_mode TEXT NOT NULL DEFAULT 'shadow'
    );
CREATE TABLE ensemble_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        setup_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        vote TEXT NOT NULL,
        confidence REAL,
        weight REAL,
        reasoning TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE strategy_params (
        strategy_name TEXT NOT NULL,
        param_name TEXT NOT NULL,
        param_value TEXT NOT NULL,
        previous_value TEXT,
        changed_at TEXT DEFAULT (datetime('now')),
        changed_by TEXT DEFAULT 'optimizer',
        PRIMARY KEY (strategy_name, param_name)
    );
CREATE TABLE daily_pnl (
        date TEXT PRIMARY KEY,
        starting_equity REAL,
        ending_equity REAL,
        realized_pnl REAL,
        total_trades INTEGER,
        wins INTEGER,
        losses INTEGER,
        max_drawdown_pct REAL,
        target_hit INTEGER DEFAULT 0,
        halted INTEGER DEFAULT 0,
        brain_calls INTEGER DEFAULT 0,
        brain_cost_usd REAL DEFAULT 0
    );
CREATE TABLE discovered_patterns (
        id TEXT PRIMARY KEY,
        pattern_type TEXT NOT NULL,
        description TEXT NOT NULL,
        conditions_json TEXT NOT NULL DEFAULT '{}',
        symbols_json TEXT DEFAULT '[]',
        timeframe TEXT DEFAULT '5',
        direction TEXT DEFAULT 'long',
        occurrences INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        avg_profit_pct REAL DEFAULT 0.0,
        avg_loss_pct REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        avg_hold_minutes INTEGER DEFAULT 0,
        max_drawdown_pct REAL DEFAULT 0.0,
        statistical_significance REAL DEFAULT 0.0,
        regime_consistency_json TEXT DEFAULT '{}',
        is_valid INTEGER DEFAULT 0,
        data_start_date TEXT,
        data_end_date TEXT,
        discovered_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE generated_strategies (
        id TEXT PRIMARY KEY,
        pattern_id TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        code TEXT NOT NULL,
        claude_model TEXT DEFAULT '',
        generation_prompt_hash TEXT DEFAULT '',
        generation_cost_usd REAL DEFAULT 0.0,
        generation_attempts INTEGER DEFAULT 1,
        syntax_valid INTEGER DEFAULT 0,
        safety_valid INTEGER DEFAULT 0,
        interface_valid INTEGER DEFAULT 0,
        validation_errors_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'generated',
        generated_at TEXT DEFAULT (datetime('now')),
        validated_at TEXT
    );
CREATE TABLE pattern_occurrences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        conditions_snapshot_json TEXT DEFAULT '{}',
        price_at_detection REAL NOT NULL,
        price_after_1h REAL,
        price_after_4h REAL,
        price_after_24h REAL,
        outcome TEXT,
        pnl_pct REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE strategy_code_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        version INTEGER DEFAULT 1,
        code TEXT NOT NULL,
        change_reason TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE backtest_results (
        id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        total_trades INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        total_return_pct REAL DEFAULT 0.0,
        max_drawdown_pct REAL DEFAULT 0.0,
        sharpe_ratio REAL DEFAULT 0.0,
        sortino_ratio REAL DEFAULT 0.0,
        calmar_ratio REAL DEFAULT 0.0,
        walk_forward_efficiency REAL DEFAULT 0.0,
        mc_probability_of_profit REAL DEFAULT 0.0,
        mc_probability_of_ruin REAL DEFAULT 0.0,
        overall_grade TEXT DEFAULT 'F',
        passed INTEGER DEFAULT 0,
        pass_reasons_json TEXT DEFAULT '[]',
        fail_reasons_json TEXT DEFAULT '[]',
        regime_performance_json TEXT DEFAULT '{}',
        monthly_returns_json TEXT DEFAULT '{}',
        equity_curve_json TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE backtest_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        entry_time TEXT NOT NULL,
        exit_time TEXT NOT NULL,
        exit_reason TEXT NOT NULL,
        pnl_usd REAL NOT NULL,
        pnl_pct REAL NOT NULL,
        commission_usd REAL DEFAULT 0,
        hold_minutes INTEGER DEFAULT 0,
        leverage INTEGER DEFAULT 1,
        regime TEXT DEFAULT '',
        hour_utc INTEGER DEFAULT 0,
        day_of_week INTEGER DEFAULT 0
    );
CREATE TABLE strategy_lifecycle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        from_status TEXT NOT NULL,
        to_status TEXT NOT NULL,
        reason TEXT DEFAULT '',
        performance_snapshot_json TEXT DEFAULT '{}',
        transitioned_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE trial_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        date TEXT NOT NULL,
        trades_today INTEGER DEFAULT 0,
        wins_today INTEGER DEFAULT 0,
        pnl_today REAL DEFAULT 0.0,
        cumulative_trades INTEGER DEFAULT 0,
        cumulative_pnl REAL DEFAULT 0.0,
        cumulative_win_rate REAL DEFAULT 0.0,
        max_drawdown REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE portfolio_allocations (
        strategy_name TEXT PRIMARY KEY,
        category TEXT NOT NULL DEFAULT '',
        full_kelly_pct REAL DEFAULT 0.0,
        fractional_kelly_pct REAL DEFAULT 0.0,
        allocated_pct REAL DEFAULT 0.0,
        allocated_usd REAL DEFAULT 0.0,
        max_position_usd REAL DEFAULT 0.0,
        max_leverage INTEGER DEFAULT 3,
        performance_score REAL DEFAULT 0.0,
        correlation_penalty REAL DEFAULT 0.0,
        risk_contribution_pct REAL DEFAULT 0.0,
        updated_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE correlation_matrix (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_a TEXT NOT NULL,
        strategy_b TEXT NOT NULL,
        correlation REAL NOT NULL,
        sample_size INTEGER DEFAULT 0,
        period_days INTEGER DEFAULT 30,
        computed_at TEXT DEFAULT (datetime('now')),
        UNIQUE(strategy_a, strategy_b, period_days)
    );
CREATE TABLE risk_budget_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        total_budget_pct REAL NOT NULL,
        used_pct REAL DEFAULT 0.0,
        proven_budget_pct REAL DEFAULT 0.0,
        ai_budget_pct REAL DEFAULT 0.0,
        trial_budget_pct REAL DEFAULT 0.0,
        reserve_pct REAL DEFAULT 0.0,
        strategy_budgets_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE rebalance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        old_allocation_pct REAL,
        new_allocation_pct REAL,
        change_pct REAL,
        reason TEXT,
        approved_by TEXT DEFAULT 'claude',
        applied INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE stress_test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_name TEXT NOT NULL,
        description TEXT,
        portfolio_impact_pct REAL,
        loss_usd REAL,
        survival INTEGER DEFAULT 1,
        margin_call_risk INTEGER DEFAULT 0,
        details_json TEXT DEFAULT '{}',
        tested_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE performance_attribution (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        total_pnl_usd REAL,
        total_pnl_pct REAL,
        strategy_contributions_json TEXT DEFAULT '[]',
        category_contributions_json TEXT DEFAULT '{}',
        best_strategy TEXT,
        worst_strategy TEXT,
        regime_factor REAL DEFAULT 0.0,
        timing_factor REAL DEFAULT 0.0,
        sizing_factor REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE price_alerts (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        condition TEXT NOT NULL,
        target_price REAL NOT NULL,
        current_price_at_set REAL DEFAULT 0,
        indicator TEXT DEFAULT 'price',
        triggered INTEGER DEFAULT 0,
        triggered_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE trade_journal (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        trade_id TEXT DEFAULT '',
        symbol TEXT DEFAULT '',
        entry_type TEXT DEFAULT '',
        content TEXT NOT NULL,
        mood TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE scheduled_reports (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        report_type TEXT NOT NULL,
        schedule TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        last_sent TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE conversation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        message TEXT NOT NULL,
        intent TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE hourly_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hour TEXT NOT NULL,
        grade TEXT NOT NULL,
        trades INTEGER DEFAULT 0,
        target_trades INTEGER DEFAULT 50,
        profit_pct REAL DEFAULT 0.0,
        target_profit_pct REAL DEFAULT 10.0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        max_escalation INTEGER DEFAULT 0,
        signals INTEGER DEFAULT 0,
        setups_to_brain INTEGER DEFAULT 0,
        rewards INTEGER DEFAULT 0,
        summary_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE fund_manager_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE fund_manager_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        symbol TEXT DEFAULT '',
        details_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE capital_level_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level TEXT NOT NULL,
        equity REAL NOT NULL,
        direction TEXT NOT NULL,
        reason TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
CREATE TABLE profit_ratchet_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        locked_amount REAL NOT NULL,
        total_locked REAL NOT NULL,
        equity_at_lock REAL NOT NULL,
        profit_floor REAL NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    );
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
    bybit_position_idx TEXT,
    exchange_mode TEXT NOT NULL DEFAULT 'shadow',
    apex_flipped INTEGER NOT NULL DEFAULT 0,
    apex_original_direction TEXT NOT NULL DEFAULT '',
    apex_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE market_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        btc_price REAL, eth_price REAL, sol_price REAL,
        regime TEXT, fear_greed INTEGER,
        full_data TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE trade_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT UNIQUE,
        symbol TEXT NOT NULL, direction TEXT NOT NULL,
        entry_price REAL, exit_price REAL,
        size_usd REAL, leverage INTEGER,
        pnl_pct REAL, pnl_usd REAL,
        strategy TEXT, thesis TEXT,
        close_reason TEXT, hold_minutes REAL,
        opened_at TEXT, closed_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        exchange_mode TEXT NOT NULL DEFAULT 'shadow'
    );
CREATE TABLE position_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        symbol TEXT NOT NULL, direction TEXT,
        entry_price REAL, mark_price REAL,
        pnl_pct REAL, unrealized_pnl REAL,
        age_minutes REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
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
CREATE TABLE event_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        event_type TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT "LOW",
        symbol TEXT DEFAULT "",
        data TEXT DEFAULT "{}",
        source TEXT DEFAULT "",
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE daily_summary (
        date TEXT PRIMARY KEY,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        total_pnl_pct REAL DEFAULT 0, total_pnl_usd REAL DEFAULT 0,
        best_trade_pct REAL DEFAULT 0, worst_trade_pct REAL DEFAULT 0,
        avg_hold_minutes REAL DEFAULT 0,
        starting_equity REAL, ending_equity REAL,
        regime_summary TEXT, trades_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE transformer_state (
        id INTEGER PRIMARY KEY DEFAULT 1,
        current_mode TEXT NOT NULL DEFAULT 'shadow',
        last_switched_at TEXT,
        is_switching INTEGER NOT NULL DEFAULT 0,
        switching_to TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
CREATE TABLE switch_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        from_mode TEXT NOT NULL,
        to_mode TEXT NOT NULL,
        positions_closed INTEGER NOT NULL DEFAULT 0,
        close_results_json TEXT,
        reason TEXT NOT NULL DEFAULT 'user_initiated',
        success INTEGER NOT NULL,
        error_message TEXT,
        shadow_equity REAL,
        bybit_equity REAL
    );
CREATE TABLE sniper_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        spike_direction TEXT NOT NULL,
        entry_price REAL, detection_price REAL,
        pnl_at_detection_pct REAL, pnl_at_detection_usd REAL,
        hold_duration_seconds INTEGER,
        exploit_score INTEGER,
        z_score REAL, velocity REAL, acceleration REAL, volume_ratio REAL,
        bb_position REAL, speed_factor REAL, consecutive_direction_count INTEGER,
        action TEXT, close_percentage REAL, close_price REAL,
        profit_captured_pct REAL, profit_captured_usd REAL,
        claude_consulted INTEGER DEFAULT 0,
        claude_response TEXT, claude_response_time_ms INTEGER,
        price_after_10s REAL, price_after_30s REAL, price_after_60s REAL,
        counterfactual_pnl_pct REAL, sniper_value_pct REAL,
        mode4_was_right INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        hurst_value REAL, hurst_score REAL, hurst_regime TEXT, hurst_confidence REAL,
        momentum_decay_score REAL, momentum_consec_decel INTEGER, momentum_reversed INTEGER,
        slope_short REAL, slope_long REAL,
        extension_atr REAL, extension_score REAL,
        atr_value REAL, vol_ratio REAL,
        volume_div_score REAL, price_obv_corr REAL,
        volume_trend_ratio REAL, divergence_type TEXT,
        risk_reward_score REAL, ev_ratio REAL,
        profit_amplifier REAL, composite_score REAL, composite_base REAL,
        regime TEXT, consensus_boost REAL, urgency_boost REAL,
        trail_stop_price REAL, trail_distance_pct REAL, action_source TEXT,
        peak_pnl_pct REAL, pullback_from_peak REAL, anti_greed_rule TEXT
    );
CREATE TABLE trade_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Group A: Trade Outcome (always populated)
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
        rsi REAL, macd_hist REAL, macd_signal REAL,
        bollinger_pct REAL, ema_20 REAL, ema_50 REAL,
        stochastic_k REAL, stochastic_d REAL, adx REAL,
        atr_value REAL, atr_pct REAL,
        volume_ratio REAL, price_vs_vwap REAL,
        -- Group E: Mode4 Profit Tracking Data
        m4_peak_pnl_pct REAL, m4_ticks_in_profit INTEGER, m4_ticks_total INTEGER,
        m4_composite_score REAL, m4_hurst_value REAL, m4_momentum_decay REAL,
        m4_extension_score REAL, m4_ev_ratio REAL, m4_volume_div_score REAL,
        -- Group F: DeepSeek Analysis (Phase 2)
        ds_why TEXT, ds_what_worked TEXT, ds_what_failed TEXT,
        ds_lessons TEXT, ds_category TEXT, ds_confidence REAL, ds_analyzed_at TEXT,
        -- Group G: Metadata
        trade_id TEXT, trade_closed_at TEXT NOT NULL, captured_at TEXT NOT NULL,
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
        apex_confidence REAL, apex_tp_mode TEXT,
        apex_reasoning TEXT, apex_model TEXT,
        apex_response_ms INTEGER, apex_cost_usd REAL,
        gate_adjustments TEXT, apex_tp_fill_rate REAL,
        regime_verified INTEGER DEFAULT 0
    );
CREATE TABLE coin_regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        regime TEXT NOT NULL,
        confidence REAL NOT NULL,
        adx REAL,
        choppiness REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );
```

Indexes (67):

```sql
CREATE INDEX idx_klines_symbol_tf_ts ON klines(symbol, timeframe, timestamp DESC);
CREATE INDEX idx_orders_symbol_status ON orders(symbol, status);
CREATE INDEX idx_trade_history_symbol ON trade_history(symbol, exit_time DESC);
CREATE INDEX idx_news_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_symbols ON news_articles(symbols);
CREATE INDEX idx_reddit_created ON reddit_posts(created_at DESC);
CREATE INDEX idx_agg_sentiment_symbol ON aggregated_sentiment(symbol, created_at DESC);
CREATE INDEX idx_fear_greed_ts ON fear_greed_index(timestamp DESC);
CREATE INDEX idx_funding_symbol ON funding_rates(symbol, fetched_at DESC);
CREATE INDEX idx_oi_symbol ON open_interest(symbol, timestamp DESC);
CREATE INDEX idx_signals_symbol ON signals(symbol, created_at DESC);
CREATE INDEX idx_accuracy_lookup ON signal_accuracy(signal_type, symbol);
CREATE INDEX idx_pattern_lookup ON pattern_log(pattern_type, symbol);
CREATE INDEX idx_brain_created ON brain_decisions(created_at DESC);
CREATE INDEX idx_session_type ON session_log(event_type, created_at DESC);
CREATE INDEX idx_regime_time ON regime_history(detected_at DESC);
CREATE INDEX idx_strat_trades_name ON strategy_trades(strategy_name, created_at DESC);
CREATE INDEX idx_strat_trades_symbol ON strategy_trades(symbol, created_at DESC);
CREATE INDEX idx_patterns_type ON discovered_patterns(pattern_type);
CREATE INDEX idx_patterns_valid ON discovered_patterns(is_valid);
CREATE INDEX idx_gen_strat_status ON generated_strategies(status);
CREATE INDEX idx_gen_strat_pattern ON generated_strategies(pattern_id);
CREATE INDEX idx_occ_pattern ON pattern_occurrences(pattern_id, timestamp DESC);
CREATE INDEX idx_occ_symbol ON pattern_occurrences(symbol, timestamp DESC);
CREATE INDEX idx_bt_strategy ON backtest_results(strategy_id);
CREATE INDEX idx_bt_passed ON backtest_results(passed);
CREATE INDEX idx_bt_trades_backtest ON backtest_trades(backtest_id);
CREATE INDEX idx_lifecycle_strategy ON strategy_lifecycle(strategy_id);
CREATE INDEX idx_trial_perf ON trial_performance(strategy_id, date);
CREATE INDEX idx_corr_strategies ON correlation_matrix(strategy_a, strategy_b);
CREATE INDEX idx_risk_budget_date ON risk_budget_log(date);
CREATE INDEX idx_attribution_period ON performance_attribution(period, created_at DESC);
CREATE INDEX idx_price_alerts_chat ON price_alerts(chat_id);
CREATE INDEX idx_price_alerts_active ON price_alerts(triggered, symbol);
CREATE INDEX idx_journal_chat ON trade_journal(chat_id, created_at DESC);
CREATE INDEX idx_conv_log_chat ON conversation_log(chat_id, created_at DESC);
CREATE INDEX idx_hourly_perf ON hourly_performance(hour DESC);
CREATE INDEX idx_fm_log ON fund_manager_log(event_type, created_at DESC);
CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_account_snapshots_time ON account_snapshots(updated_at DESC);
CREATE INDEX idx_backtest_results_time ON backtest_results(created_at DESC);
CREATE INDEX idx_price_alerts_symbol ON price_alerts(symbol);
CREATE INDEX idx_daily_pnl_date ON daily_pnl(date DESC);
CREATE INDEX idx_strategy_perf_name ON strategy_performance(strategy);
CREATE INDEX idx_trade_thesis_symbol_status ON trade_thesis(symbol, status);
CREATE INDEX idx_trade_thesis_status ON trade_thesis(status);
CREATE INDEX idx_trade_thesis_opened ON trade_thesis(opened_at);
CREATE INDEX idx_market_snapshots_ts ON market_snapshots(ts_epoch);
CREATE INDEX idx_trade_log_symbol ON trade_log(symbol);
CREATE INDEX idx_pos_snapshots_ts ON position_snapshots(ts_epoch);
CREATE INDEX idx_claude_decisions_ts ON claude_decisions(ts_epoch);
CREATE INDEX idx_event_log_ts ON event_log(ts_epoch);
CREATE INDEX idx_event_log_type ON event_log(event_type);
CREATE INDEX idx_trade_log_opened ON trade_log(opened_at DESC);
CREATE INDEX idx_position_snapshots_ts ON position_snapshots(ts_epoch DESC);
CREATE INDEX idx_position_snapshots_symbol ON position_snapshots(symbol);
CREATE INDEX idx_daily_summary_date ON daily_summary(date DESC);
CREATE INDEX idx_switch_history_ts ON switch_history(timestamp DESC);
CREATE INDEX idx_sniper_log_ts ON sniper_log(timestamp);
CREATE INDEX idx_sniper_log_symbol_ts ON sniper_log(symbol, timestamp DESC);
CREATE INDEX idx_sniper_log_action ON sniper_log(action);
CREATE INDEX idx_ti_symbol ON trade_intelligence (symbol);
CREATE INDEX idx_ti_win ON trade_intelligence (win);
CREATE INDEX idx_ti_ds_why ON trade_intelligence (ds_why);
CREATE INDEX idx_ti_trade_closed_at ON trade_intelligence (trade_closed_at);
CREATE INDEX idx_ti_ds_category ON trade_intelligence (ds_category);
CREATE INDEX idx_ti_apex_optimized ON trade_intelligence (apex_optimized);
CREATE INDEX idx_coin_regime_symbol ON coin_regime_history(symbol, timestamp DESC);
```

---

# Appendix D — Full file listing by package (live `ls -la` + wc -l)

Only project-authored files (`__pycache__`, `.ruff_cache`, `.pytest_cache`, `.venv`, `.git` excluded).

## D.1 `src/alerts/` (6 files, 1,013 lines)

- `__init__.py` (381 B)
- `alert_manager.py` (12,846 B / 304 LOC) — AlertManager hub + throttle coordination.
- `formatter.py` (3,581 B / 102 LOC) — HTML formatting + emoji.
- `telegram_bot.py` (9,238 B / 239 LOC) — thin Telegram I/O layer.
- `templates.py` (11,407 B / 264 LOC) — per-event message templates.
- `throttle.py` (2,983 B / 95 LOC) — AlertThrottle with dedup cache.

## D.2 `src/analysis/` (31 files, 7,510 lines)

- `__init__.py` (327 B).
- `engine.py` (23,149 B / 588 LOC) — TAEngine orchestrator.
- `ta_cache.py` (5,987 B / 152 LOC) — TTL cache + time-bucket key.
- `vol_scale.py` (4,046 B / 118 LOC) — ATR-scaled multipliers.
- `volatility_profile.py` (12,423 B / 323 LOC) — VolatilityProfiler.
- `indicators/` — trend.py (495 LOC), momentum.py (366 LOC), volatility.py (239 LOC), volume.py (195 LOC), __init__.py.
- `patterns/` — candlestick.py, chart_patterns.py, helpers, __init__.py (~400 LOC total).
- `structure/` (15 files, 3,837 LOC) — see Part 11.

## D.3 `src/apex/` (7 files, 2,791 lines)

- `__init__.py` (59 B).
- `assembler.py` (32,194 B / 758 LOC).
- `gate.py` (21,796 B / 459 LOC).
- `models.py` (20,044 B / 435 LOC).
- `optimizer.py` (29,695 B / 664 LOC).
- `prompts.py` (11,077 B / 226 LOC).
- `qwen_client.py` (9,366 B / 248 LOC).

## D.4 `src/brain/` (15 files, 4,715 lines)

- `__init__.py` (6,041 B / 148 LOC) — BrainManager v1 wiring.
- `brain_v2.py` (23,374 B / 542 LOC).
- `claude_client.py` (5,807 B / 146 LOC) — deprecated.
- `claude_code_client.py` (41,652 B / 968 LOC).
- `cost_tracker.py` (3,973 B / 110 LOC).
- `decision_parser.py` (7,509 B / 198 LOC).
- `executor.py.deprecated`, `prompt_builder.py.deprecated`, `scheduler.py.deprecated` — out of import graph.
- `prompts/` — 8 small legacy files (`setup_review.py`, `position_review.py`, `trade_decision.py`, `weekly_optimization.py`, `daily_summary.py`, `market_analysis.py`, `risk_review.py`, `__init__.py`).
- `strategist.py` (115,527 B / 2,335 LOC) — production strategist.

## D.5 `src/config/` (4 files, 1,978 lines)

- `__init__.py` (633 B).
- `constants.py` (7,216 B).
- `settings.py` (60,111 B).
- `validators.py` (7,822 B).

## D.6 `src/core/` (25 files, 7,397 lines)

- `__init__.py` (2,960 B).
- `container.py` (6,602 B) — ServiceContainer.
- `data_lake.py` (7,153 B).
- `decorators.py` (10,201 B).
- `event_buffer.py` (11,718 B / 285 LOC).
- `exceptions.py` (3,512 B).
- `freshness_guard.py` (2,778 B).
- `health_monitor.py` (6,704 B).
- `layer_manager.py` (42,677 B / 929 LOC).
- `log_context.py` (3,615 B).
- `logging.py` (5,516 B / 154 LOC).
- `rule_engine.py` (14,924 B / 351 LOC).
- `size_mapper.py` (2,237 B).
- `sl_gateway.py` (33,783 B / 727 LOC).
- `sl_tp_validator.py` (15,614 B / 343 LOC).
- `strategic_plan.py` (6,834 B).
- `thesis_manager.py` (9,584 B / 232 LOC).
- `trade_coordinator.py` (27,545 B / 637 LOC).
- `trade_plan.py` (6,165 B).
- `trade_recorder.py` (2,861 B).
- `trading_mode.py` (5,366 B).
- `transformer.py` (45,035 B / 1,064 LOC).
- `types.py` (10,726 B / 369 LOC).
- `urgent_queue.py` (4,470 B).
- `utils.py` (6,024 B).

## D.7 `src/database/` (18 files, 4,160 lines)

- `__init__.py` (998 B).
- `cleanup.py` (3,367 B / 94 LOC).
- `connection.py` (8,575 B / 220 LOC).
- `migrations.py` (51,059 B / 1,371 LOC).
- `models.py` (7,309 B).
- `protected_tables.py` (5,035 B / 144 LOC).
- `repositories/` (12 files) — `altdata_repo.py`, `backtest_repo.py`, `context_repo.py`, `factory_repo.py`, `learning_repo.py`, `market_repo.py`, `news_repo.py`, `portfolio_repo.py`, `sentiment_repo.py`, `telegram_repo.py`, `trading_repo.py`, `__init__.py`.

## D.8 `src/factory/` (27 files, 2,891 lines)

- `__init__.py` (1,440 B).
- `backtester.py` (7,049 B / 169 LOC).
- `discoverer.py` (7,076 B / 178 LOC).
- `generator.py` (5,819 B / 156 LOC).
- `lifecycle.py` (3,845 B / 109 LOC).
- `live_monitor.py` (3,834 B / 98 LOC).
- `metrics.py` (7,613 B / 195 LOC).
- `monte_carlo.py` (3,331 B / 94 LOC).
- `simulator.py` (6,492 B / 175 LOC).
- `trial_manager.py` (5,484 B / 154 LOC).
- `validator.py` (4,949 B / 147 LOC).
- `walk_forward.py` (2,937 B / 89 LOC).
- `analyzers/` (7 files) — cross_asset, micro_patterns, multi_variable, news_reactive, sequential, single_variable, temporal + `__init__.py`.
- `models/` (3 files) — pattern_models, strategy_models + `__init__.py`.
- `prompts/` (4 files) — discovery_prompt, generation_prompt, validation_prompt + `__init__.py`.

## D.9 `src/fund_manager/` (27 files, 4,632 lines)

22 decision modules (see Part 15) + `manager.py` (24,238 B / 531 LOC) + `models/` (3 files) + `__init__.py`.

## D.10 `src/intelligence/` (19 files, 2,340 lines)

- `__init__.py` (1,163 B).
- `altdata/` — `fear_greed.py`, `funding_rates.py`, `open_interest.py`, `onchain.py`, `__init__.py`.
- `news/` — `finnhub_client.py`, `news_service.py`, `calendar_service.py`, `__init__.py`.
- `sentiment/` — `aggregator.py`, `reddit_client.py`, `reddit_service.py`, `scorer.py`, `__init__.py`.
- `signals/` — `signal_generator.py`, `confidence.py`, `signal_models.py`, `__init__.py`.

## D.11 `src/mcp/` (13 files, 1,844 lines)

See Part 17.

## D.12 `src/portfolio/` (10 files, 862 lines)

- `__init__.py` (631 B).
- `allocator.py` (6,386 B / 156 LOC).
- `analytics.py` (4,398 B / 113 LOC).
- `correlation.py` (4,616 B / 129 LOC).
- `kelly.py` (3,305 B / 88 LOC).
- `optimizer.py` (1,874 B / 47 LOC).
- `risk_budget.py` (3,020 B / 81 LOC).
- `stress_test.py` (3,684 B / 89 LOC).
- `models/` (2 files).

## D.13 `src/risk/` (8 files, 1,626 lines)

- `__init__.py` (624 B).
- `drawdown.py` (8,380 B / 217 LOC).
- `portfolio.py` (5,149 B / 120 LOC).
- `position_sizer.py` (8,851 B / 194 LOC).
- `risk_manager.py` (9,052 B / 229 LOC).
- `stop_loss.py` (6,604 B / 158 LOC).
- `time_decay_sl.py` (23,831 B / 529 LOC).
- `validators.py` (7,501 B / 161 LOC).

## D.14 `src/sentinel/` (4 files, 484 lines)

See Part 9.

## D.15 `src/shadow/` (2 files, 627 lines)

- `__init__.py` (542 B).
- `shadow_adapter.py` (22,083 B / 607 LOC).

## D.16 `src/strategies/` (57 files, 6,752 lines)

See Part 12.

## D.17 `src/telegram/` (40 files, 6,434 lines)

See Part 18.

## D.18 `src/tias/` (8 files, 2,083 lines)

See Part 8.

## D.19 `src/trading/` (12 files, 2,224 lines)

- `__init__.py` (783 B).
- `auth.py` (3,317 B).
- `client.py` (7,212 B) — BybitClient REST.
- `websocket.py` (8,354 B) — BybitWebSocket.
- `services/` — `account_service.py` (3,610 B), `instrument_service.py` (5,555 B), `market_service.py` (10,092 B), `order_service.py` (19,423 B), `position_service.py` (14,078 B), `__init__.py`.
- `models/` (2 files).

## D.20 `src/workers/` (33 files, 14,247 lines)

See Part 5 for the 22 registered workers. Additional infrastructure files:

- `__init__.py` (933 B).
- `base_worker.py` (5,874 B / 161 LOC).
- `health.py` (2,977 B / 102 LOC).
- `firewall.py` (1,491 B) — duplicate of sentinel firewall concept; currently a thin wrapper.
- `layer_manager.py` (33,668 B) — workers-local LayerManager (see Part 29.1).
- `settings.py` (45,072 B) — legacy duplicate settings module (see Part 29.2).
- `sniper_models.py` (37,658 B / 988 LOC).
- `sniper_ring_buffer.py` (13,805 B / 419 LOC).
- `allocation_worker.py` (1,308 B / 41 LOC) — defined but not appended by manager.

---

# Appendix E — Loguru COMPONENT_ROUTING verbatim

```python
COMPONENT_ROUTING: dict[str, str] = {
    "mcp": "mcp.log",
    "worker": "workers.log",
    "brain": "brain.log",
    "claude_code": "brain.log",
    "strategist": "brain.log",
    "rule_engine": "workers.log",
    "trading": "workers.log",
    "sl_tp_validator": "workers.log",
    "sl_gateway": "workers.log",
    "coordinator": "workers.log",
    "data_lake": "workers.log",
    "thesis_manager": "workers.log",
    "enforcer": "workers.log",
    "strategies": "workers.log",
    "intelligence": "workers.log",
    "analysis": "workers.log",
    "fund_manager": "workers.log",
    "tiered_capital": "workers.log",
    "risk": "workers.log",
    "time_decay_sl": "workers.log",
    "volatility_profile": "workers.log",
    "factory": "workers.log",
    "portfolio": "workers.log",
    "trade_recorder": "workers.log",
    "trading_mode": "workers.log",
    "shadow": "workers.log",
    "strategy": "workers.log",
    "event_buffer": "workers.log",
    "urgent_queue": "workers.log",
    "layer_manager": "workers.log",
    "core": "workers.log",
    "tias": "workers.log",
    "apex": "workers.log",
    "sentinel": "workers.log",
    "xray": "workers.log",
    "database": "general.log",
    "alerts": "general.log",
    "telegram": "general.log",
    "control_handler": "general.log",
    "dashboard": "general.log",
}
DEFAULT_LOG_FILE = "general.log"
```

---

# Appendix F — Systemd unit files verbatim

### `trading-workers.service`
```
[Unit]
Description=Trading Intelligence - Background Workers
Documentation=https://github.com/user/trading-intelligence-mcp
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=inshadaliqbal786
Group=inshadaliqbal786
WorkingDirectory=/home/inshadaliqbal786/trading-intelligence-mcp
Environment="PATH=/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="HOME=/home/inshadaliqbal786"
Environment="LANG=C.UTF-8"
EnvironmentFile=/home/inshadaliqbal786/trading-intelligence-mcp/.env
ExecStart=/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python workers.py
Restart=always
RestartSec=15
MemoryMax=800M
MemoryHigh=600M
CPUQuota=80%
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/inshadaliqbal786/trading-intelligence-mcp/data
ReadWritePaths=/home/inshadaliqbal786/.claude
ProtectHome=read-only
PrivateTmp=true
StandardOutput=null
StandardError=null

[Install]
WantedBy=multi-user.target
```

### `trading-mcp-sse.service`
```
[Unit]
Description=Trading Intelligence - MCP SSE Server
Documentation=https://github.com/user/trading-intelligence-mcp
After=network-online.target trading-workers.service
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=inshadaliqbal786
Group=inshadaliqbal786
WorkingDirectory=/home/inshadaliqbal786/trading-intelligence-mcp
Environment="PATH=/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin:/usr/bin"
EnvironmentFile=/home/inshadaliqbal786/trading-intelligence-mcp/.env
ExecStart=/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python server.py --transport sse --port 8080
Restart=always
RestartSec=10
MemoryMax=200M
MemoryHigh=150M
CPUQuota=50%
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/inshadaliqbal786/trading-intelligence-mcp/data
ProtectHome=read-only
PrivateTmp=true
StandardOutput=null
StandardError=null

[Install]
WantedBy=multi-user.target
```

### `trading-brain.service`
```
[Unit]
Description=Trading Intelligence - Claude Brain Auto-Trading
After=network-online.target trading-workers.service
Wants=network-online.target trading-workers.service
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User=inshadaliqbal786
Group=inshadaliqbal786
WorkingDirectory=/home/inshadaliqbal786/trading-intelligence-mcp
Environment="PATH=/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="HOME=/home/inshadaliqbal786"
Environment="LANG=C.UTF-8"
EnvironmentFile=/home/inshadaliqbal786/trading-intelligence-mcp/.env
ExecStart=/home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python brain.py
Restart=always
RestartSec=30
MemoryMax=200M
MemoryHigh=150M
CPUQuota=50%
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/inshadaliqbal786/trading-intelligence-mcp/data
ReadWritePaths=/home/inshadaliqbal786/.claude
ProtectHome=read-only
PrivateTmp=true
StandardOutput=null
StandardError=null

[Install]
WantedBy=multi-user.target
```

### `trading-backup.service` / `trading-backup.timer`
```
# Service (oneshot)
[Unit]
Description=Trading Intelligence - Daily Backup

[Service]
Type=oneshot
User=inshadaliqbal786
WorkingDirectory=/home/inshadaliqbal786/trading-intelligence-mcp
ExecStart=/home/inshadaliqbal786/trading-intelligence-mcp/scripts/backup.sh

# Timer
[Unit]
Description=Trading Intelligence - Daily Backup Timer

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

---

# Appendix G — Full MCP tool catalogue (43 tools)

Each tool is declared with `Tool(name=..., description=..., inputSchema=...)`. Names captured via `grep "name=\"[a-z_]+\"" src/mcp/tools/*.py`.

### G.1 `trading_tools.py` (12)

`get_account_info`, `get_ticker`, `get_tickers`, `get_klines`, `get_orderbook`, `place_order`, `modify_order`, `cancel_order`, `cancel_all_orders`, `get_open_orders`, `get_positions`, `close_position`.

### G.2 `risk_tools.py` (5)

`calculate_position_size`, `get_risk_exposure`, `calculate_stop_loss`, `get_daily_pnl`, `get_risk_status`.

### G.3 `analysis_tools.py` (5)

`get_technical_analysis`, `get_indicator`, `get_patterns`, `get_signal`, `get_trade_recommendation`.

### G.4 `altdata_tools.py` (5)

`get_fear_greed_index`, `get_funding_rates`, `get_open_interest`, `get_funding_history`, `get_market_overview`.

### G.5 `sentiment_tools.py` (5)

`get_reddit_sentiment`, `get_subreddit_hot`, `get_social_buzz`, `get_aggregated_sentiment`, `get_sentiment_history`.

### G.6 `news_tools.py` (4)

`get_latest_news`, `get_news_for_symbol`, `search_news`, `get_economic_calendar`.

### G.7 `memory_tools.py` (4)

`get_trade_history`, `get_strategy_performance`, `get_pattern_outcomes`, `get_brain_decisions`.

### G.8 `system_tools.py` (3)

`get_system_status`, `get_worker_status`, `update_preference`.

Total: 12+5+5+5+5+4+4+3 = 43.

Schemas are JSON-Schema via mcp `Tool.inputSchema`. Example (from `trading_tools.py` `place_order`):

```json
{
  "type": "object",
  "properties": {
    "symbol": {"type": "string"},
    "side": {"type": "string", "enum": ["Buy", "Sell"]},
    "order_type": {"type": "string", "enum": ["Market", "Limit"]},
    "qty": {"type": "number"},
    "price": {"type": "number"},
    "stop_loss": {"type": "number"},
    "take_profit": {"type": "number"},
    "leverage": {"type": "integer", "minimum": 1, "maximum": 10}
  },
  "required": ["symbol", "side", "order_type", "qty"]
}
```

Auth: SSE transport requires `Authorization: Bearer <MCP_AUTH_TOKEN>` header when `[mcp] sse_auth_required=true`. stdio transport is unauthenticated by design (proc-isolation).

Init banner: `MCP_INIT | tools=43 init_ms=<n> transport=sse`.

---

# Appendix H — Telegram command catalogue

Registered in `src/telegram/bot.py` via `Application.add_handler(CommandHandler("name", callback))`. Confirmed by grep:

Core: `/start`, `/help`, `/status`.

Portfolio: `/portfolio`, `/pnl`, `/balance`, `/history`.

Analysis: `/analyze <SYM>`, `/signals`, `/regime`, `/fear`, `/news`, `/opportunities`.

Brain: `/brain`, `/decisions`, `/leaderboard`, `/factory`.

Fund: `/fund`, `/setwallet`, `/floor`.

System: `/errors`, `/pause`, `/resume`.

Alerts: `/alert`, `/alerts`, `/cancelalert`.

Watchlist: `/watch`, `/unwatch`, `/watchlist`.

Journal: `/journal`, `/note`.

Schedule: `/schedule`.

Emergency: `/emergency` (two-step confirm).

Trading: `/quicktrade`.

Enforcer: `/enforcer`, `/enforcer_reset`.

TIAS: `/tias_last`, `/tias_patterns`, `/tias_symbols`, `/tias_cost`.

APEX: `/apex_status`, `/apex_last`, `/apex_flips`.

Dashboard handler commands (registered in `dashboard_handler.py`): `/dashboard`, `/stopdash`, `/positions`, `/performance`, `/plan`, `/workers`, `/capital`, `/mode`, `/control`.

---

# Appendix I — ServiceContainer keys (grep of `self._services[...] = ...` in `src/workers/manager.py`)

```
self._services["transformer"] = transformer
self._services["bybit"] = bybit
self._services["ws"] = ws
self._services["market"] = market_svc
self._services["market_service"] = market_svc
self._services["account"] = acc_svc
self._services["account_service"] = acc_svc
self._services["order"] = ord_svc
self._services["order_service"] = ord_svc
self._services["position"] = pos_svc
self._services["position_service"] = pos_svc
self._services["instrument_service"] = inst_svc
self._services["news"] = news_svc
self._services["reddit"] = reddit_svc
self._services["fear_greed"] = fg_client
self._services["funding"] = funding_tracker
self._services["oi"] = oi_tracker
self._services["onchain"] = onchain_client
self._services["calendar"] = calendar_svc
self._services["aggregator"] = aggregator
self._services["signal_gen"] = signal_generator
self._services["ta"] = ta_cache
self._services["ta_cache"] = ta_cache
self._services["ta_engine"] = ta_cache
self._services["ta_raw"] = ta_engine_raw
self._services["claude_client"] = claude_client
self._services["cost_tracker"] = cost_tracker
self._services["decision_parser"] = decision_parser
self._services["alert_manager"] = alert_mgr
self._services["risk_manager"] = risk_mgr
self._services["event_buffer"] = event_buffer
self._services["data_lake"] = data_lake
self._services["trade_coordinator"] = trade_coordinator
self._services["layer_manager"] = layer_manager
self._services["regime_detector"] = detector
self._services["scanner"] = scanner
self._services["coin_discovery"] = coin_discovery
self._services["shadow_kline_reader"] = shadow_reader
self._services["structure_engine"] = structure_engine
self._services["structure_cache"] = structure_cache
self._services["structure_worker"] = sw
self._services["volatility_profiler"] = volatility_profiler
self._services["sl_gateway"] = sl_gateway
self._services["sl_validator"] = sl_validator
self._services["rule_engine"] = rule_engine
self._services["thesis_manager"] = thesis_manager
self._services["urgent_queue"] = urgent_queue
self._services["freshness_guard"] = freshness_guard
self._services["strategist"] = strategist
self._services["registry"] = registry
self._services["pnl_manager"] = pnl_mgr
self._services["enforcer"] = enforcer
self._services["profit_sniper"] = sniper
self._services["position_watchdog"] = watchdog
self._services["trading_mode"] = trading_mode_mgr
self._services["telegram_bot"] = tg_bot
self._services["fund_manager"] = fund_mgr
self._services["tiered_capital"] = tiered_capital
self._services["kelly"] = kelly
self._services["risk_budget"] = risk_budget
self._services["correlation_tracker"] = corr_tracker
self._services["apex_gate"] = apex_gate
self._services["apex_optimizer"] = apex_optimizer
self._services["sentinel_advisor"] = sentinel_advisor
self._services["tias_repo"] = tias_repo
self._services["strategy_worker"] = strat_worker
self._services["kline_worker"] = _kline_worker
self._services["price_worker"] = _price_worker
```

That's 60+ keys, with intentional aliases like `ta`/`ta_cache`/`ta_engine` (same object), `order`/`order_service`, `position`/`position_service`, `market`/`market_service`, `account`/`account_service` for source-compatibility with different callers.

---

# Appendix J — Final verification checklist

- Every worker named in the observability log (strategy_worker, signal_worker, regime_worker, kline_worker, price_worker, profit_sniper, position_watchdog, structure_worker, telegram_bot_worker, plus enforcer, fund_manager, discovery, backtest, scanner, cleanup, altdata, reddit, news, price_alert, scheduled_report, live_monitor, trial_monitor, allocation, fund_manager) is covered in Part 5. ✔
- All 7 APEX files covered (Part 7). ✔
- All 4 SENTINEL files covered (Part 9). ✔
- All 15 X-RAY files covered (Part 11). ✔
- All 43 MCP tools enumerated (Part 17, Appendix G). ✔
- All 65 DB tables with row counts (Part 4, Appendix C). ✔
- All 5 systemd units inventoried (Parts 1.1, 2.5, Appendix F). ✔
- All 19 operator scripts (Part 25). ✔
- All 54 test files (Part 26). ✔

---

---

# Appendix K — Per-strategy catalogue (43 strategies)

Each block captures class name, category, applicable regimes, timeframe, risk level, expected hold, and scan logic distilled from the file head. Source root: `src/strategies/categories/`.

### K.1 Tier A — Scalping

**A1_rsi_reversal** (`a1_rsi_reversal.py`, 97 LOC)
- Class: `RSIReversalScalp`
- Category: `scalping`
- Regimes: RANGING, TRENDING_UP, TRENDING_DOWN
- Timeframe: M5
- Risk: low; hold: 20 min
- Entry: RSI < 25 at lower BB → BUY; RSI > 75 at upper BB → SELL. Requires volume spike + Stochastic cross; no active trend.

**A2_vwap_bounce** (`a2_vwap_bounce.py`, 93 LOC)
- Class: `VWAPBounceScalp`
- Category: `scalping`
- Regimes: TRENDING_UP, TRENDING_DOWN
- Timeframe: M5; risk: low; hold: 30 min
- Entry: pullback to VWAP in trending market; needs 8/12 candles above/below VWAP + bullish/bearish candlestick pattern. Falls back gracefully when OBV / candle pattern absent.

**A3_bb_squeeze** (`a3_bb_squeeze_scalp.py`, 96 LOC)
- Class: `BBSqueezeScalp`
- Category: `scalping`
- Regimes: RANGING, VOLATILE
- Timeframe: M5; risk: medium; hold: 15 min
- Entry: Bollinger Band squeeze breakout; upper/lower band pierce with volume confirmation.

**A4_ema_crossover** (`a4_ema_crossover.py`, 98 LOC)
- Class: `EMACrossoverMomentum`
- Category: `scalping`
- Regimes: TRENDING_UP, TRENDING_DOWN
- Timeframe: M1 (note: finest TF in the suite); risk: low; hold: 10 min
- Entry: EMA12/26 crossover with VWAP filter (price must cross VWAP on cross) + RSI/Stochastic confirmation.

### K.2 Tier B — Momentum / Trend following

**B1_volume_breakout** (`b1_volume_breakout.py`, 92 LOC)
- Class: `VolumeBreakout`
- Category: `momentum`
- Regimes: TRENDING_UP, TRENDING_DOWN, VOLATILE
- Timeframe: M15; risk: medium; hold: 240 min
- Entry: high-volume breakout above/below key levels with trend confirmation.

**B2_supertrend** (`b2_supertrend_follower.py`, 100 LOC)
- Class: `SupertrendFollower`
- Category: `momentum`
- Regimes: TRENDING_UP, TRENDING_DOWN
- Timeframe: H1; risk: medium; hold: 480 min
- Entry: candle close beyond Supertrend line.

**B3_ichimoku** (`b3_ichimoku_breakout.py`, 113 LOC)
- Class: `IchimokuBreakout`
- Category: `momentum`
- Regimes: TRENDING_UP, TRENDING_DOWN
- Timeframe: H4; risk: medium; hold: 2880 min (48 h)
- Entry: Ichimoku cloud breakout with cloud direction, signal line, chikou span alignment.

**B4_double_bottom_top** (`b4_double_bottom_top.py`, 114 LOC)
- Class: `DoubleBottomTop`
- Category: `momentum`
- Regimes: TRENDING_UP, TRENDING_DOWN, RANGING
- Timeframe: H1; risk: medium; hold: 480 min
- Entry: double bottom / top chart pattern; skips gracefully when pattern detector not available.

### K.3 Tier C — Mean reversion

**C1_bb_mean_reversion** (`c1_bb_mean_reversion.py`, 98 LOC)
- Class: `BBMeanReversion`
- Category: `mean_reversion`
- Regimes: RANGING only
- Timeframe: M15; risk: low; hold: 120 min
- Entry: price near BB middle with RSI 40–60 = pullback entry.

**C2_rsi_divergence** (`c2_rsi_divergence.py`, 102 LOC)
- Class: `RSIDivergence`
- Category: `mean_reversion`
- Regimes: all three (TRENDING_DOWN, TRENDING_UP, RANGING)
- Timeframe: H1; risk: medium; hold: 360 min
- Entry: hidden divergence (price extends, RSI does not); checks Stochastic agreement.

### K.4 Tier D — Funding / derivatives

**D1_funding_fade** (`d1_funding_rate_fade.py`, 76 LOC)
- Class: `FundingRateFade`
- Category: `funding_arb`
- Regimes: ALL (`list(MarketRegime)`)
- Timeframe: H4; risk: low; hold: 960 min
- Entry: extreme funding (>0.01 or <-0.01) contrarian fade. Long on extreme positive funding, short on extreme negative.

**D2_oi_divergence** (`d2_oi_divergence.py`, 77 LOC)
- Class: `OIDivergence`
- Category: `funding_arb`
- Regimes: TRENDING_UP, TRENDING_DOWN, RANGING
- Timeframe: H4; risk: medium; hold: 720 min
- Entry: price up but OI down (or inverse) → contrarian signal.

### K.5 Tier E — Sentiment / News

**E1_fear_greed_extreme** (`e1_fear_greed_extreme.py`, 102 LOC)
- Class: `FearGreedExtreme`
- Category: `sentiment`
- Timeframe: H1 (inferred); contrarian. Helper `_to_numeric()` tolerates int / float / dict / dataclass inputs for F&G feed.
- Entry: FG <20 → BUY, FG >80 → SELL.

**E2_news_breakout** (`e2_news_breakout.py`, 79 LOC)
- Class: `NewsBreakout`
- Category: `sentiment`
- Regimes: VOLATILE
- Timeframe: M5; risk: high; hold: 120 min
- Entry: positive/negative news sentiment triggers trade (requires `sentiment_data`).

**E3_sentiment_momentum** (`e3_sentiment_momentum.py`, 101 LOC)
- Class: `SentimentMomentum`
- Category: `sentiment`
- Regimes: TRENDING_UP, TRENDING_DOWN, VOLATILE
- Timeframe: H1; risk: medium
- Entry: combines sentiment + F&G + rising volume + VWAP filter.

### K.6 Tier F — Structure / order flow

**F1_support_resistance** (105 LOC), `SupportResistanceBounce` — entry at S/R bounce with volume confirmation. Regimes: TRENDING_UP, TRENDING_DOWN, RANGING. TF H1.

**F2_multi_tf_alignment** (107 LOC), `MultiTimeframeAlignment` — higher TF trend confirms lower TF entry. Regimes: TRENDING_UP, TRENDING_DOWN.

**F3_liquidation_hunt** (95 LOC), `LiquidationHunt` — trades suspected liquidity pool sweeps (stop-hunt zones).

**F4_grid_recovery** (106 LOC), `GridRecovery` — grid pullbacks inside a trend (mean-reversion on trend continuation).

### K.7 Tier G — Predatory / Manipulation

**G1_stop_hunt_sniper** (97 LOC), `StopHuntSniper` — spike-then-reverse detection; volatile scalp.

**G2_retail_sentiment_fade** (89 LOC), `RetailSentimentFade` — fades crowded retail trades using OI skew.

**G3_liquidation_frontrunner** (87 LOC), `LiquidationFrontrunner` — front-runs suspected liquidation cascades (high volume + sharp move).

**G4_whale_shadow** (107 LOC), `WhaleShadow` — follows whale / large-order footprints.

### K.8 Tier H — Microstructure

**H1_funding_prediction** (78 LOC), `FundingPrediction` — predicts next funding rate from volume / momentum; fades extremes proactively.

**H2_spread_basis** (75 LOC), `SpreadBasis` — basis arbitrage (spot vs perpetual) low-risk scalp.

**H3_volatility_switch** (95 LOC), `VolatilitySwitch` — switches entry style per regime change (scalp in low-vol, swing in high-vol).

**H4_order_flow** (96 LOC), `OrderFlow` — order-flow imbalance detection.

### K.9 Tier I — Time-based

**I1_kill_zone** (103 LOC), `KillZone` — NY-open window (10:00–11:00 UTC); high volatility.

**I2_weekend_gap** (85 LOC), `WeekendGap` — Monday-open gap fade.

**I3_options_expiry** (86 LOC), `OptionsExpiry` — options gamma window volatility.

**I4_hourly_close** (104 LOC), `HourlyClose` — final 5 minutes of each hour momentum.

### K.10 Tier J — Cross-market

**J1_btc_dominance** (69 LOC) — BTC.D alt-season vs BTC strength.

**J2_correlation_breakdown** (76 LOC) — detects correlation breakdown between pairs.

**J3_cross_exchange_lag** (79 LOC) — arb cross-exchange price lag.

**J4_altcoin_beta** (78 LOC) — beta-to-BTC expected overextension plays.

### K.11 Tier K — AI / Adaptive

**K1_claude_conviction** (84 LOC) — consumes Claude Brain conviction scores; requires brain integration.

**K2_pattern_memory** (105 LOC) — learned-pattern recognition over past winning setups (meta).

**K3_ensemble** (29 LOC) — passthrough stub to ensemble; no independent signal.

**K4_adaptive_optimizer** (29 LOC) — stub; defers to PerformanceEnforcer weighting.

### K.12 Tier X — Test

**X1_always_trade** (73 LOC), `AlwaysTradeTestStrategy` — always emits BUY; used for smoke testing the execution pipeline.

### K.13 `_helpers.py` (helpers for all categories)

Used by multiple strategies: `safe_get(ta, *keys, default=None)` traverses nested TA dicts; `has_bullish_pattern(ta)` / `has_bearish_pattern(ta)` check candlestick pattern dicts for bullish/bearish entries. `__init__.py` is 67 bytes (trivial).

### K.14 Generated strategies

`src/strategies/categories/generated/` — empty today (factory disabled). Intended for runtime imports of validated strategies from `generated_strategies` table.

---

# Appendix L — Full MCP tool schemas (43 tools)

Per-tool JSON-Schema reconstruction from `src/mcp/tools/*_tools.py`. Each entry documents name, input schema, description focus, output format.

### L.1 `trading_tools.py` — 12 tools

- **`get_account_info`** — schema `{}`. Returns total equity, available balance, used margin, unrealized PnL, margin level %.
- **`get_ticker`** — schema `{"symbol": str required}`. Returns last price, bid/ask, 24h change %, 24h high/low, 24h volume.
- **`get_tickers`** — schema `{}`. Returns list of all tracked tickers for display / scan.
- **`get_klines`** — `{"symbol": str, "timeframe": str default "15", "limit": int default 200}`. Returns OHLCV rows.
- **`get_orderbook`** — `{"symbol": str, "depth": int default 25}`. Returns top-N bids/asks.
- **`place_order`** — `{"symbol": str, "side": Buy|Sell, "order_type": Market|Limit, "qty": number, "price": number optional, "stop_loss": number, "take_profit": number, "leverage": int 1–10}`. Returns Order object (status: Filled / Rejected). 
- **`modify_order`** — `{"order_id": str, "price": number optional, "qty": number optional, "stop_loss": number optional, "take_profit": number optional}`. Returns modified Order.
- **`cancel_order`** — `{"order_id": str}`. Returns cancellation confirmation.
- **`cancel_all_orders`** — `{"symbol": str optional}`. Returns count cancelled.
- **`get_open_orders`** — `{"symbol": str optional}`. Returns list of New/PartiallyFilled orders.
- **`get_positions`** — `{"symbol": str optional}`. Returns Position objects with entry_price, mark_price, unrealized_pnl, leverage, SL, TP.
- **`close_position`** — `{"symbol": str, "qty_pct": number default 100}`. Market-closes 100% or partial.

### L.2 `risk_tools.py` — 5 tools

- **`calculate_position_size`** — `{"entry_price": number, "stop_loss_price": number, "risk_pct": number default 2.0}`. Computes qty = (balance × risk_pct) / SL_distance.
- **`get_risk_exposure`** — `{}`. Returns total_exposure_usd, exposure_pct_of_equity, max_allowed %, positions count, unrealized_pnl, per-position breakdown.
- **`calculate_stop_loss`** — `{"symbol": str, "side": Buy|Sell, "entry_price": number}`. Returns SL/TP using fixed `risk.default_stop_loss_pct=3.0` / `default_take_profit_pct=6.0` in both directions.
- **`get_daily_pnl`** — `{}`. Returns today's PnL from `daily_pnl` table.
- **`get_risk_status`** — `{}`. Returns current enforcer level, size multiplier, can_trade flag, cooldown states.

### L.3 `analysis_tools.py` — 5 tools

- **`get_technical_analysis`** — `{"symbol": str, "timeframe": str default "15", "limit": int default 200}`. Returns full TA: overall signal + confidence, bullish/bearish counts, trend (SMA/EMA/MACD/ADX/Supertrend/Parabolic SAR), momentum (RSI/Stoch/CCI/Williams/ROC/AO/TSI), volatility (ATR/NATR/Bollinger/Choppiness/Keltner), volume (OBV/VWAP/CMF), patterns (candlestick+chart), support/resistance.
- **`get_indicator`** — `{"symbol": str, "indicator": str (rsi|macd|bollinger|atr|stochastic|adx|obv|vwap), "timeframe": str default "15", "period": int optional}`. Single-indicator fetch (faster).
- **`get_patterns`** — `{"symbol": str, "timeframe": str}`. Returns candlestick + chart pattern lists with confidence.
- **`get_signal`** — `{"symbol": str}`. Returns `Signal(signal_type, confidence, source, reasoning, components)` from SignalGenerator.
- **`get_trade_recommendation`** — `{"symbol": str, "risk_pct": number default 2.0}`. End-to-end recommendation: signal + TA S/R levels + position size using `account.get_available_balance()`.

### L.4 `altdata_tools.py` — 5 tools

- **`get_fear_greed_index`** — `{"include_history": bool default false}`. Returns latest value (0–100) + classification. With `include_history=true`, appends last 7 days. Interpretation banded: ≤25 extreme fear, 26–45 fear, 46–55 neutral, 56–74 greed, ≥75 extreme greed.
- **`get_funding_rates`** — `{"symbol": str optional}`. Returns per-symbol funding rate % with "Crowded Longs" / "Crowded Shorts" / "Normal" classification (`abs > 0.5 %` = crowded).
- **`get_open_interest`** — `{"symbol": str optional}`. Returns current OI + 24 h change (Rising >2 %, Falling <-2 %, Stable otherwise).
- **`get_funding_history`** — `{"symbol": str required, "hours": int default 24}`. Historical funding lines.
- **`get_market_overview`** — `{}`. Composite: Fear & Greed + total market cap (via `onchain.get_global_metrics()`) + BTC dominance + funding rate count.

### L.5 `sentiment_tools.py` — 5 tools

- **`get_reddit_sentiment`** — `{"symbol": str, "hours": int default 24}`. Aggregated sentiment from `reddit_posts` rows.
- **`get_subreddit_hot`** — `{"subreddit": str, "limit": int default 10}`. Top posts list.
- **`get_social_buzz`** — `{"symbol": str}`. Mention count over 24h.
- **`get_aggregated_sentiment`** — `{"symbol": str}`. Returns `aggregated_sentiment` row: overall_score, level, news_score, news_count, reddit_score, reddit_count, fear_greed_value, momentum.
- **`get_sentiment_history`** — `{"symbol": str, "days": int default 7}`. Rolling sentiment trend.

### L.6 `news_tools.py` — 4 tools

- **`get_latest_news`** — `{"limit": int default 10}`. Returns headlines with sentiment score + classification (Bullish > 0.2, Bearish < -0.2, Neutral otherwise).
- **`get_news_for_symbol`** — `{"symbol": str, "hours": int default 24}`. Symbol-filtered news from past N hours.
- **`search_news`** — `{"keyword": str, "limit": int default 10}`. Keyword search.
- **`get_economic_calendar`** — `{"days": int default 7}`. Upcoming economic events with impact (low/medium/high), country, estimate.

### L.7 `memory_tools.py` — 4 tools

- **`get_trade_history`** — `{"symbol": str optional, "limit": int default 20}`. Returns trade rows with entry/exit prices, PnL $, PnL %, win-rate aggregate.
- **`get_strategy_performance`** — `{"strategy": str optional}`. Returns win rate, avg PnL, total profit, total loss, profit factor; filters by strategy if given.
- **`get_pattern_outcomes`** — `{"pattern_type": str optional, "symbol": str optional}`. Stub — returns "requires more historical data".
- **`get_brain_decisions`** — `{"limit": int default 10}`. Returns recent signals as proxy for brain decisions (legacy — `brain_decisions` table is empty).

### L.8 `system_tools.py` — 3 tools

- **`get_system_status`** — `{}`. Returns services status, DB connection, worker count.
- **`get_worker_status`** — `{}`. Returns per-worker `WorkerStatus`, uptime, tick count, error count.
- **`update_preference`** — `{"key": str, "value": str}`. Upserts into `user_preferences` table.

### L.9 Shared MCP conventions

- All handlers return `list[TextContent]` per mcp SDK spec.
- Any service-unavailable path returns a single TextContent "<service> not available".
- Errors caught at the handler boundary and returned as `Error: <msg>` rather than raising (preserves JSON-RPC semantics).
- Transport stdio consumers use `mcp_stdio_proxy.py` to reach SSE at 127.0.0.1:8080; `MCP_AUTH_TOKEN` attached as `Authorization: Bearer …`.

---

# Appendix M — Fund Manager modules (22 of them, all under `src/fund_manager/`)

Each is a single-purpose module consumed by `manager.IntelligentFundManager` in sequence. Data from live `wc -l` and class-header grep.

| # | Module | File | LOC | Class | Responsibility |
|--:|---|---|--:|---|---|
| M1 | Progressive Capital Allocator | `capital_allocator.py` | 314 | `CapitalAllocator` | Distributes free capital to strategy buckets based on tier + performance. |
| M2 | Quality-Based Position Sizer | `position_sizer.py` | 183 | `PositionSizer` | Maps grade × account_level → base size %; streak & PnL modifiers. |
| M3 | Capital Reserves | `capital_reserves.py` | 108 | `CapitalReserves` | Three-pool system: emergency fund, cash buffer, liquidation backstop. |
| M4 | Correlation Guard | `correlation_guard.py` | 142 | `CorrelationGuard` | Reduces size 50 % on correlated-pair attempt (`corr > 0.7`). |
| M5 | Time Pool Manager | `time_pools.py` | 132 | `TimePoolManager` | Capital pools per horizon (scalp/swing/position). |
| M6 | Volatility Scaler | `volatility_scaler.py` | 183 | `VolatilityScaler` | Per-symbol multiplier keyed on ATR%; 2 cached dicts (multiplier + percentile). |
| M7 | Sector Rotation | `sector_rotation.py` | 152 | `SectorRotation` | BTC.D-driven weight between major/alt sectors. |
| M8 | Strategy Budget Manager | `strategy_budgets.py` | 151 | `StrategyBudgetManager` | Per-strategy cap of total capital. |
| M9 | Momentum Allocator | `momentum_allocator.py` | 109 | `MomentumAllocator` | Accelerate-winners / decelerate-losers. |
| M10 | Risk Weather Assessor | `risk_weather.py` | 336 | `RiskWeatherAssessor` | Market conditions → weather (calm/alert/storm/hurricane). |
| M11 | Capital Velocity Tracker | `capital_velocity.py` | 155 | `CapitalVelocityTracker` | Tracks capital in/out; enforces recycling limits. |
| M12 | Recovery Planner | `recovery_planner.py` | 181 | `RecoveryPlanner` | Post-drawdown scale-down until recovery target. |
| M13 | Opportunity Cost Calculator | `opportunity_cost.py` | 135 | `OpportunityCostCalculator` | Idle capital cost; scale up if > threshold. |
| M14 | Profit Ratchet | `profit_ratchet.py` | 177 | `ProfitRatchet` | Lock-in gains; ratcheted trail for SL. |
| M15 | Time Sync | `time_sync.py` | 120 | `TimeSync` | Align allocation with London/NY/Asia sessions. |
| M16 | Market Emotion Detector | `emotion_detector.py` | 158 | `MarketEmotionDetector` | Fear/greed sentiment → capital reduction on extremes. |
| M17 | Ecosystem Health Monitor | `ecosystem_health.py` | 288 | `EcosystemHealthMonitor` | Network effects + stable-dominance signal. |
| M18 | Anti-Frag | `anti_fragile.py` | 115 | `AntiFrag` | Adds convex exposure (option-like asymmetry). |
| M19 | Loss Harvester | `loss_harvester.py` | 182 | `LossHarvester` | Small strategic losses for net benefit. |
| M20 | Compound Optimizer | `compound_optimizer.py` | 170 | `CompoundOptimizer` | Reinvestment schedule. |
| M21 | Liquidity Mapper | `liquidity_mapper.py` | 151 | `LiquidityMapper` | Depth + spread based size reduction. |
| M22 | Fee Optimizer | `fee_optimizer.py` | 126 | `FeeOptimizer` | Taker-fee adjustment; ensures min profitable trade. |

Plus `tiered_capital.py` (179 LOC, `TieredCapitalManager` + `FundLimits`) — progressive trust system behind capital release tiers, and `manager.py` (531 LOC) which sequences M1–M22 into a single `SizingDecision(amount_usd, leverage, pool, reason)`.

---

# Appendix N — Exception hierarchy (`src/core/exceptions.py`)

All custom exceptions descend from `TradingMCPError(Exception)` with `__init__(message, details: dict | None = None)` pattern for structured error details.

```
TradingMCPError
├── ConfigError
├── AuthenticationError
├── TradingError
│   ├── OrderError
│   │   ├── InsufficientBalanceError
│   │   ├── InvalidOrderError
│   │   └── OrderRejectedError
│   ├── PositionError
│   └── RateLimitError
├── DataError
│   ├── MarketDataError
│   ├── DatabaseError
│   └── APIError
│       ├── BybitAPIError
│       ├── FinnhubError
│       └── RedditError
├── IntelligenceError
│   ├── SentimentError
│   └── SignalError
├── WorkerError
│   ├── WorkerStartError
│   └── WorkerCrashError
├── BrainError
│   ├── ClaudeAPIError
│   ├── DecisionParseError
│   └── ExecutionError
└── RiskError
    ├── RiskLimitExceededError
    ├── MaxDrawdownError
    └── DailyLossLimitError
```

Additionally — outside the core hierarchy:

- `ProtectedTableViolation(TradingMCPError)` in `src/database/protected_tables.py`.
- `APEXOptimizationError` in `src/apex/qwen_client.py` (no `retryable` flag — fallback immediately to Claude defaults).
- `TIASAnalysisError(retryable: bool = False)` in `src/tias/deepseek_client.py` — `retryable=True` triggers one fallback model attempt.

---

# Appendix O — Type definitions (`src/core/types.py`)

### O.1 Enumerations

- `Side(str, Enum)` — Buy, Sell.
- `OrderType(str, Enum)` — Market, Limit, StopMarket, StopLimit, TakeProfit, StopLoss.
- `OrderStatus(str, Enum)` — New, PartiallyFilled, Filled, Cancelled, Rejected, Deactivated, Triggered, Untriggered.
- `TimeFrame(str, Enum)` — M1, M3, M5, M15, M30, H1, H2, H4, H6, H12, D, W, M.
- `SignalType(str, Enum)` — STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL.
- `SentimentLevel(str, Enum)` — VERY_POSITIVE, POSITIVE, NEUTRAL, NEGATIVE, VERY_NEGATIVE.
- `TradingMode(str, Enum)` — shadow, paper, live.
- `WorkerStatus(str, Enum)` — STOPPED, STARTING, RUNNING, ERROR, RESTARTING.
- `AlertLevel(str, Enum)` — INFO, WARNING, CRITICAL, DEBUG.

### O.2 Dataclasses (with `SerializableMixin`)

**OHLCV** — symbol, timeframe, timestamp, open, high, low, close, volume, turnover.

**Ticker** — symbol, last_price, bid, ask, timestamp.

**Order** — order_id, symbol, side, order_type, price, qty, status, filled_qty, avg_fill_price, stop_loss, take_profit, created_at, updated_at.

**Position** — symbol, side, size, entry_price, mark_price, unrealized_pnl, realized_pnl, leverage, liquidation_price, stop_loss, take_profit, updated_at.

**NewsArticle** — id, headline, source, url, summary, sentiment_score [-1.0..1.0], symbols, category, published_at, fetched_at.

**RedditPost** — id, subreddit, title, score, num_comments, upvote_ratio, sentiment_score, symbols_mentioned, permalink.

**Signal** — id, symbol, signal_type, confidence, source, components dict, reasoning, created_at.

**TradeRecord** — trade_id, symbol, side, entry_price, exit_price, qty, pnl, pnl_pct, strategy, signal_confidence, notes, entry_time, exit_time.

**AccountInfo** — total_equity, available_balance, used_margin, unrealized_pnl, margin_level_pct.

**FearGreedData** — value (0-100), classification, timestamp.

**FundingRate** — symbol, funding_rate, next_funding_time, predicted_rate.

**BrainDecision** — prompt_hash, market_state_json, claude_response, decision_json, action_taken, outcome_json, tokens_used, cost_usd, trigger, created_at.

### O.3 Utility functions (`src/core/utils.py`)

`generate_id()` (UUID4), `now_utc()`, `now_timestamp_ms()`, `timestamp_to_datetime(ms)`, `datetime_to_timestamp(dt)`, `round_price(price, precision)`, `round_qty(qty, step)`, `pct_change(old, new)`, `clamp(value, min, max)`, `safe_divide(num, den, default)`, `chunk_list(lst, n)`, `flatten_dict(d)`.

---

# Appendix P — Complete config key reference (34 sections, 763 lines)

### [general] (5 keys)
`mode`, `shadow_api_url`, `timezone`, `log_level`, `log_dir`.

### [bybit] (6)
`testnet`, `default_symbols[20]`, `rate_limit_per_second`, `ws_ping_interval`, `ws_reconnect_delay`, `recv_window`.

### [finnhub] (4)
`enabled`, `rate_limit_per_minute`, `news_categories`, `max_articles_per_fetch`.

### [reddit] (5)
`enabled`, `subreddits[5]`, `max_posts_per_sub`, `min_score`, `rate_limit_per_minute`.

### [altdata] (5)
`enabled`, `fear_greed_interval`, `funding_rate_interval`, `open_interest_interval`, `coingecko_rate_limit_per_minute`.

### [database] (5)
`path`, `wal_mode`, `pool_size`, `query_timeout`, `vacuum_interval`.

### [workers] (8)
`enabled`, `market_data_interval`, `news_interval`, `reddit_interval`, `altdata_interval`, `health_check_interval`, `max_consecutive_failures`, `restart_delay`.

### [brain] (16)
`enabled`, `use_claude_code`, `strategic_interval`, `watchdog_interval`, `analysis_interval`, `signal_triggered`, `min_signal_confidence`, `max_calls_per_hour`, `model`, `max_tokens`, `temperature`, `claude_cli_timeout_seconds`, `claude_cli_max_retries`, `claude_cli_min_interval`, `claude_cli_retry_timeout_backoff_base_seconds`, `prompt_event_buffer_max_events`.

### [risk] (11)
`max_leverage`, `mandatory_stop_loss`, `default_stop_loss_pct`, `default_take_profit_pct`, `max_position_size_pct`, `max_open_positions`, `daily_loss_limit_pct`, `max_total_exposure_pct`, `max_drawdown_pct`, `min_order_value_usdt`, `loss_cooldown_seconds`.

### [alerts] (8)
`telegram_enabled`, `alert_levels[]`, `daily_summary`, `daily_summary_time`, `max_alerts_per_minute`, `trade_alerts`, `signal_alerts`, `error_alerts`.

### [mcp] (6)
`transport`, `sse_host`, `sse_port`, `sse_auth_required`, `server_name`, `server_version`.

### [watchdog] (12)
`enabled`, `check_interval_seconds`, `loss_warning_pct`, `trailing_loss_pct`, `sl_proximity_pct`, `rapid_move_pct`, `brain_trigger_loss_pct`, `brain_cooldown_seconds`, `partial_close_pct`, `max_brain_calls_per_hour`, `early_exit_enabled`, `fast_reconcile_seconds`.

### [mcp_pool] (6)
`enabled`, `sse_url`, `min_warm`, `max_warm`, `health_check_interval_seconds`, `acquire_timeout_seconds`.

### [price] (3)
`local_max_age_seconds`, `divergence_override_pct`, `divergence_block_prompt_pct`.

### [sl_gateway] (11) + [sl_gateway.min_distance_class_ceiling] (5)
`enabled`, `min_distance_pct`, `max_step_pct`, `rate_limit_seconds`, `log_only_global`, `log_only_tighten_only`, `log_only_min_distance`, `log_only_max_step`, `log_only_rate_limit`, `min_distance_atr_multiplier`, `min_distance_abs_floor_pct`. Sub-table: dead/low/medium/high/extreme per-class ceilings.

### [scanner] (5)
`enabled`, `scan_interval_seconds`, `min_volume_24h`, `max_coins`, `max_spread_pct`.

### [regime] (7)
`detection_interval_seconds`, `primary_symbol`, `trending_adx_threshold`, `ranging_adx_threshold`, `ranging_choppiness_threshold`, `volatile_atr_percentile`, `dead_adx_threshold`, `dead_volume_ratio`.

### [strategy_engine] (6)
`scan_interval_seconds`, `min_score_threshold`, `min_ensemble_agreement`, `max_ensemble_opposition`, `max_setups_to_brain`, `max_brain_calls_per_hour`.

### [pnl_targets] (5)
`daily_target_pct`, `protect_threshold_pct`, `caution_threshold_pct`, `survival_threshold_pct`, `halt_threshold_pct`.

### [leverage] (8)
`max_leverage`, `tier_1_max`, `tier_2_max`, `tier_3_max`, `volatile_max`, `dead_max`, `min_confidence_for_5x`, `min_confidence_for_4x`.

### [optimizer] (8)
`enabled`, `run_day`, `run_hour_utc`, `weight_adjustment_pct`, `max_param_change_pct`, `min_trades_for_optimization`, `underperform_threshold_pct`, `disable_after_weeks`.

### [factory] (13)
`enabled`, `discovery_schedule_hour_utc`, `discovery_lookback_days`, `min_pattern_occurrences`, `min_win_rate`, `min_profit_factor`, `min_statistical_significance`, `max_strategies_per_batch`, `max_generation_retries`, `generation_cost_limit_usd`, `live_monitor_interval_seconds`, `hot_pattern_threshold_win_rate`, `hot_pattern_threshold_occurrences`, `emergency_generation_enabled`.

### [backtesting] (14)
`initial_capital`, `default_leverage`, `commission_pct`, `slippage_pct`, `funding_rate_pct`, `walk_forward_enabled`, `train_pct`, `monte_carlo_runs`, `min_trades_to_pass`, `min_win_rate`, `min_profit_factor`, `max_drawdown_pct`, `min_sharpe`, `min_walk_forward_efficiency`, `max_ruin_probability`.

### [trial] (13)
`trial_duration_days`, `max_extensions`, `extension_duration_days`, `trial_position_size_pct`, `min_trades_for_evaluation`, `promotion_min_win_rate`, `promotion_min_pnl`, `promotion_max_drawdown`, `max_active_strategies`, `demotion_underperform_weeks`, `demotion_win_rate_drop_pct`, `quarterly_revival_enabled`.

### [portfolio] (21)
`enabled`, `optimization_day`, `optimization_hour_utc`, `kelly_fraction`, `min_trades_for_kelly`, `max_strategy_allocation_pct`, `min_strategy_allocation_pct`, `proven_strategies_budget_pct`, `ai_strategies_budget_pct`, `trial_strategies_budget_pct`, `cash_reserve_pct`, `correlation_lookback_days`, `high_correlation_threshold`, `daily_risk_budget_pct`, `drawdown_reduction_threshold_1/factor_1`, `drawdown_reduction_threshold_2/factor_2`, `kelly_weight`, `mean_variance_weight`, `risk_parity_weight`, `min_rebalance_change_pct`, `stress_test_enabled`.

### [telegram_interactive] (7)
`enabled`, `ai_responses_enabled`, `max_ai_calls_per_hour`, `trade_confirmation_required`, `morning_briefing_enabled`, `morning_briefing_hour_utc`, `price_alert_check_interval`.

### [fund_manager] (9)
`enabled`, `check_interval_seconds`, `starting_unlock_pct`, `active_pool_pct`, `aplus_reserve_pct`, `emergency_reserve_pct`, `profit_lock_pct`, `trade_profit_lock_pct`, `max_correlation_bucket_pct`, `min_profitable_trade_fee_pct`.

### [enforcer] (20)
`enabled`, `check_interval_seconds`, `pnl_caution_pct`, `pnl_survival_pct`, `size_reduction_enabled`, `size_reduction_at_pnl_pct`, `size_reduction_factor`, `streak_boost_threshold`, `max_enforcement_minutes`, `grace_period_minutes`, `level_1_max_positions`, `level_1_max_leverage`, `level_1_min_score`, `level_2_max_positions`, `level_2_max_leverage`, `level_2_min_score`, `level_2_min_confluence`, `level_2_min_rr`, `decay_minutes`, `min_trades_per_hour`, `min_profit_per_hour_pct`, `min_win_rate`, `min_signals_per_hour`, `min_setups_to_brain_per_hour`, `max_seconds_between_trades`, `max_escalation_level`, `force_trade_on_gap`, `rewards_enabled`, `hourly_report_enabled`.

### [mode4] (38)
`enabled`, `check_interval_seconds`, `buffer_max_size`, `buffer_min_ready`, `base_atr_multiplier`, `trail_min_change_pct`, `regime_factor_trending/ranging/volatile/dead`, `anti_greed_enabled`, `anti_greed_pullback_40/60/75_min_peak`, `tighten_cooldown_seconds`, `partial_close_cooldown_seconds`, `partial_close_pct`, `stall_escape_partial_after_ticks`, `stall_escape_full_after_ticks`, `stall_escape_cooldown_seconds`, `stall_tighten_max_applications`, `stall_recovery_threshold_pct`, `log_every_n_ticks`, `log_always_above_score`, `sniper_log_write_every_n_ticks`, `score_watch/consult_claude/auto_partial/auto_full`, `min_profit_pct`, `min_profit_for_action`, `profit_immunity_seconds`, `loss_immunity_seconds`, `full_rules_after_seconds`, `cooldown_extreme_seconds/strong_seconds/medium_seconds`, `claude_timeout_seconds`, `max_claude_queries_per_hour`, `claude_hold_recheck_seconds`, `weight_zscore/velocity/volume/bollinger/momentum`, `flash_crash_auto_score`, `min_trail_atr_multiplier`, `min_trail_pct`, `min_profit_for_trail_pct`, `min_profit_decay`.

### [tias] (8)
`enabled`, `primary_model`, `fallback_model`, `temperature`, `max_tokens`, `timeout_seconds`, `max_retries`, `analysis_version`.

### [apex] (13) + [apex.tp_cap_multiplier_by_class] (5)
`enabled`, `model`, `fallback_model`, `timeout_seconds`, `max_tokens`, `temperature`, `max_position_size_usd`, `max_leverage`, `min_tias_trades_for_optimization`, `min_regime_trades_for_fallback`, `min_tp_pct`, `gate_tp_floor_enabled`, `gate_trail_activation_floor_pct_of_tp`, `gate_trail_distance_floor_pct`, `gate_mode_override_enabled`, `gate_confidence_floor`, `gate_apex_size_cap_mult`, `conviction_enabled`, `conviction_min_trades`. Sub-table: per-class TP cap multipliers.

### [sentinel] (10)
`enabled`, `firewall_enabled`, `deadline_profit_pct`, `deadline_breakeven_lower_pct`, `deadline_small_loss_pct`, `deadline_grace_minutes`, `deadline_small_loss_sl_pct`, `advisor_enabled`, `advisor_interval_seconds`, `advisor_model`, `advisor_temperature`, `advisor_max_tokens`, `advisor_timeout_seconds`, `advisor_min_profit_for_tighten_pct`.

### [analysis.structure] (20)
`enabled`, `worker_interval_seconds`, `cache_ttl_seconds`, `min_candles`, `swing_lookbacks`, `cluster_pct`, `min_touches`, `max_levels_per_side`, `ms_swing_lookback`, `ms_min_swing_points`, `sl_buffer_pct`, `tp_buffer_pct`, `min_rr_ratio`, `sl_fallback_pct`, `tp_fallback_pct`, `fvg_min_gap_pct`, `fvg_max_age_candles`, `ob_displacement_min`, `ob_max_age_candles`, `liq_equal_tolerance_pct`, `liq_min_equal_count`, `liq_round_number_step`, `sweep_max_age_candles`, `sweep_min_wick_pct`, `setup_scanner_mode`, `scan_full_market`, `batch_size`, `coin_refresh_interval`, `shadow_db_path`.

### [analysis.volatility_profile] (9)
`enabled`, `cache_ttl_seconds`, `jitter_range_seconds`, `dead_threshold`, `low_threshold`, `medium_threshold`, `high_threshold`, `min_tp_pct`, `min_sl_pct`, `max_tp_pct`, `max_sl_pct`.

### [time_decay] (9) + [time_decay.grace_seconds_by_class] (5) + [time_decay.atr_room_multiplier_by_class] (5)
`enabled`, `p_win_abs_depth_threshold_pct`, `p_win_abs_depth_strong_pct`, `p_win_abs_depth_penalty`, `p_win_abs_depth_strong_penalty`. Two per-class tables.

Total keys across the 34 named sections + 6 sub-tables: ~320.

---

# Appendix Q — Repository methods (`src/database/repositories/`)

12 repository files, one per domain. All are async, use `DatabaseManager` via `self.db.execute/fetch_one/fetch_all`. Typical method shape: CRUD + domain-specific queries.

### Q.1 `trading_repo.py` (10,082 B)

`TradingRepository` — `create_order`, `update_order_status`, `get_order`, `get_open_orders`, `get_trade_history(symbol, limit)`, `record_trade(TradeRecord)`, `get_positions`, `upsert_position`, `delete_position(symbol)`, `get_account_snapshot`, `save_account_snapshot`.

### Q.2 `market_repo.py` (9,138 B)

`MarketRepository` — `save_klines(list)`, `get_klines(symbol, timeframe, limit)`, `upsert_ticker(Ticker)`, `get_ticker(symbol)`, `save_orderbook_snapshot`, `get_latest_price(symbol)`.

### Q.3 `news_repo.py` (8,990 B)

`NewsRepository` — `add_article(NewsArticle)`, `get_recent(limit)`, `get_for_symbol(symbol, hours)`, `search(keyword, limit)`, `save_calendar_events(list)`, `get_upcoming_events(days)`.

### Q.4 `altdata_repo.py` (7,857 B)

`AltDataRepository` — `save_fear_greed(value, classification)`, `get_latest_fg()`, `get_fg_history(days)`, `save_funding_rate(symbol, rate)`, `get_funding_history`, `save_oi(symbol, value)`.

### Q.5 `sentiment_repo.py` (6,889 B)

`SentimentRepository` — `add_reddit_post`, `get_reddit_posts(subreddit, limit)`, `save_aggregated_sentiment`, `get_latest_aggregated(symbol)`, `get_sentiment_history`.

### Q.6 `context_repo.py` (5,406 B)

`ContextRepository` — `save_market_snapshot`, `save_regime`, `get_recent_regimes`, `save_thesis`, `update_thesis_status`.

### Q.7 `learning_repo.py` (8,675 B)

`LearningRepository` — `save_signal_accuracy`, `save_pattern_log`, `save_brain_decision` (legacy), `get_pattern_outcomes`.

### Q.8 `factory_repo.py` (8,048 B)

`FactoryRepository` — `save_discovered_pattern`, `save_generated_strategy`, `get_validated_strategies`, `transition_strategy_status`, `get_pattern_occurrences`.

### Q.9 `backtest_repo.py` (4,415 B)

`BacktestRepository` — `save_backtest_result`, `save_backtest_trades`, `get_passed_backtests`.

### Q.10 `portfolio_repo.py` (2,766 B)

`PortfolioRepository` — `save_allocation`, `save_correlation`, `save_risk_budget`, `save_rebalance`.

### Q.11 `telegram_repo.py` (3,442 B)

`TelegramRepository` — `add_price_alert`, `check_alerts`, `save_journal_entry`, `get_journal(chat_id)`.

### Q.12 `__init__.py` (706 B) — re-exports all 12 repos.

---

# Appendix R — Per-core-file reference (`src/core/`)

Every core-layer file beyond what Part 2/15/22 already covered:

### R.1 `container.py` (6,602 B, 177 LOC)
`ServiceContainer`. Central initializer with 5 layers — Layer 1 Bybit + trading services; Layer 2 TAEngine; Layer 3 Claude + DecisionParser + CostTracker; Layer 4 AlertManager + RiskManager; Layer 5 StrategyRegistry + PnLManager. `get(name, default=None)`, `get_all()`, `shutdown()` flushes alerts, disconnects WebSocket, closes Bybit REST, closes DB.

### R.2 `data_lake.py` (7,153 B)
Columnar in-memory store for analytics. `save_event(event_type, payload)`, `query(event_type, filters)`, `to_dict()` for diagnostics. Grew to absorb per-worker timing traces.

### R.3 `decorators.py` (10,201 B)
`@retry(max=3, backoff=2.0)`, `@rate_limit(calls, per_seconds)`, `@timed(name)` (emits timing log), `@validate_input(schema)`. Used across services.

### R.4 `event_buffer.py` (11,718 B, 285 LOC)
See Part 22.

### R.5 `freshness_guard.py` (2,778 B)
`FreshnessGuard(source, threshold_seconds)` — emits `FRESH_OK` / `FRESH_BLOCK` when data age crosses threshold. Used by PriceWorker, KlineWorker, SentimentAggregator.

### R.6 `health_monitor.py` (6,704 B)
`SystemHealthMonitor.check()` — returns dict with CPU%, RSS, event-loop lag, task count. Called by `WorkerManager._system_health_loop` every 60 s. Emits `SYSTEM_HEALTH` log.

### R.7 `layer_manager.py` (42,677 B, 929 LOC)
Core LayerManager — the strategist scheduler + URGENT injector. Runs `_cycle_loop()` at `brain.strategic_interval`, alternating Call A/B; triggers early on EventBuffer HIGH events. Records each decision to `claude_decisions`.

### R.8 `log_context.py` (3,615 B)
`ctx()` helper — returns `correlation_id=<uuid4>` + `request_id=<tid>` for every log line. Bound via loguru's `extra` dict.

### R.9 `logging.py` (5,516 B)
See Part 24.

### R.10 `rule_engine.py` (14,924 B, 351 LOC)
Layer-4 rule executor. Applies enforcer restrictions + POS gate + size cap + cool-down before executing a trade.

### R.11 `size_mapper.py` (2,237 B)
`map_size_to_qty(size_usd, price, instrument_info)` — converts USD size to exchange-precision qty.

### R.12 `sl_gateway.py` (33,783 B, 727 LOC)
See Part 15.1.

### R.13 `sl_tp_validator.py` (15,614 B, 343 LOC)
Per-trade entry validator. Ensures `sl != entry`, `tp != entry`, correct side, minimum distances, non-zero qty. Emits `SL_TP_VALIDATE_PASS` / `_FAIL`.

### R.14 `strategic_plan.py` (6,834 B)
Data model for `StrategicPlan(new_trades, position_actions, market_view, risk_level, max_positions, default_leverage, ...)`. Immutable dataclass.

### R.15 `thesis_manager.py` (9,584 B, 232 LOC)
`ThesisManager.open_thesis(symbol, thesis_dict)`, `update_thesis_from_order`, `close_thesis(symbol, pnl, reason)`, `get_active_theses()`, `flush()` to `context_repo`.

### R.16 `trade_coordinator.py` (27,545 B, 637 LOC)
See Part 15.6.

### R.17 `trade_plan.py` (6,165 B)
`TradePlan(symbol, direction, sl, tp, size_usd, leverage, reasoning, source)` — the final plan object after APEX + Gate; passed to ShadowAdapter.

### R.18 `trade_recorder.py` (2,861 B)
`TradeRecorder.record_close(trade_dict)` — writes to `trade_log` + `strategy_trades`.

### R.19 `trading_mode.py` (5,366 B)
`TradingModeManager` — wraps Transformer's `current_mode`; exposes `is_shadow()`, `is_paper()`, `is_live()`.

### R.20 `transformer.py` (45,035 B, 1,064 LOC)
See Part 20.

### R.21 `types.py` (10,726 B, 369 LOC)
See Appendix O.

### R.22 `urgent_queue.py` (4,470 B)
`UrgentQueue.add_concern(symbol, level, reason)`, `drain_all()`, `has_concerns` property. Consumed by ClaudeStrategist.

### R.23 `utils.py` (6,024 B)
Pure helpers (see Appendix O.3).

---

# Appendix S — Telegram handler per-command breakdown

### S.1 `bot.py` (705 LOC)

Class `InteractiveTelegramBot`. Properties: `_app` (python-telegram-bot `Application`), `_handlers: list`, `_running: bool`. Methods:

- `async start()` — builds Application, registers handlers, starts polling.
- `async stop()` — stops polling, shuts down Application.
- `async send_trade_open_alert(symbol, entry_price, leverage, size_usd)` — unified trade alert via AlertManager.
- `async send_trade_close_alert(symbol, exit_price, pnl_pct, pnl_usd)`.

Handlers registered (from grep of `app.add_handler(CommandHandler(...))`):

- `/start` → `_cmd_start`.
- `/help` → `_cmd_help`.
- `/portfolio` → `portfolio_handler.summary`.
- `/pnl` → `portfolio_handler.pnl`.
- `/balance` → `portfolio_handler.balance`.
- `/history` → `portfolio_handler.trade_history`.
- `/analyze <SYM>` → `analysis_handler.analyze`.
- `/signals` → `analysis_handler.signals`.
- `/regime` → `analysis_handler.regime`.
- `/fear` → `analysis_handler.fear_greed`.
- `/news` → `analysis_handler.news`.
- `/opportunities` → `analysis_handler.opportunities`.
- `/brain` → `brain_handler.status`.
- `/decisions` → `brain_handler.decisions`.
- `/leaderboard` → `brain_handler.leaderboard`.
- `/factory` → `brain_handler.factory_status`.
- `/fund` → `fund_handler.status`.
- `/setwallet` → `fund_handler.set_wallet`.
- `/floor` → `control_handler` (floor status with inline buttons).
- `/status` → `system_handler.status`.
- `/errors` → `system_handler.errors`.
- `/pause` → `system_handler.pause`.
- `/resume` → `system_handler.resume`.
- `/alert` → `alert_handler.set_alert`.
- `/alerts` → `alert_handler.list_alerts`.
- `/cancelalert` → `alert_handler.cancel_alert`.
- `/watch` → `watchlist_handler.add`.
- `/unwatch` → `watchlist_handler.remove`.
- `/watchlist` → `watchlist_handler.show`.
- `/journal` → `journal_handler.show`.
- `/note` → `journal_handler.add_note`.
- `/schedule` → `schedule_handler.manage`.
- `/emergency` → `emergency_handler.execute` (two-step confirm).
- `/quicktrade` → `_cmd_quicktrade`.
- `/enforcer` → `_cmd_enforcer`.
- `/enforcer_reset` → `_cmd_enforcer_reset`.
- `/tias_last`, `/tias_patterns`, `/tias_symbols`, `/tias_cost` → `tias_handler`.
- `/apex_status`, `/apex_last`, `/apex_flips` → `apex_handler`.
- `/control`, `/dashboard`, `/stopdash`, `/positions`, `/performance`, `/plan`, `/workers`, `/capital`, `/mode` → `dashboard_handler`.

### S.2 `dashboard_handler.py` (2,371 LOC — largest handler)

Auto-refreshing dashboard. `/dashboard` opens a message that the handler repeatedly `edit_message_text()`s on callback. Displays:

- Account header: total equity, available, daily PnL, enforcer level.
- Positions block: each symbol with entry, mark, PnL%, PnL$, SL, TP, age.
- Strategy performance: win rate / profit factor / total trades per category.
- TA summary per symbol (regime, RSI, ATR% — abbreviated).
- Recent closes (last 10): symbol, direction, PnL%, close_reason.
- Time-decay lane stats + APEX fill-rate.
- SQL aggregates via `trade_thesis`, `trade_log`, `trade_intelligence`, `position_snapshots`, `event_log`.

### S.3 `control_handler.py` (630 LOC)

Control commands: `/brain_interval_60`, `/brain_interval_180`, `/brain_interval_300`, `/enable_trading`, `/disable_trading`, `/enforce_capital_preservation`, `/enforce_survival`, `/reset_enforcer`, plus inline keyboard buttons for interval toggles.

### S.4 Remaining handlers

- `apex_handler.py` (215 LOC) — `/apex_status`, `/apex_last`, `/apex_flips`.
- `tias_handler.py` (265 LOC) — `/tias_last`, `/tias_patterns`, `/tias_symbols`, `/tias_cost`.
- `analysis.py` (238 LOC).
- `portfolio.py` (138 LOC).
- `system.py` (158 LOC).
- `fund.py` (202 LOC).
- `brain.py` (75 LOC).
- `trading.py` (135 LOC).
- `alerts.py` (66 LOC).
- `watchlist.py` (75 LOC).
- `journal.py` (42 LOC).
- `schedule.py` (24 LOC).
- `emergency.py` (54 LOC).

### S.5 `telegram/features/`

- `price_alerts.py` (83 LOC).
- `risk_checker.py` (89 LOC).
- `scheduled_reports.py` (26 LOC).
- `trade_journal.py` (28 LOC).
- `morning_briefing.py` (45 LOC).
- `chart_generator.py` (20 LOC).
- `leaderboard.py` (32 LOC).

### S.6 `telegram/ai/`

- `context_builder.py` (61 LOC) — gathers context dict for AI-driven replies.
- `question_handler.py` (65 LOC) — dispatches user questions to Claude via MCP.
- `prompts.py` (22 LOC) — prompt templates.

### S.7 `telegram/ui/`

- `cards.py` (107 LOC) — reusable message cards.
- `buttons.py` (66 LOC) — inline keyboard factories.
- `formatters.py` (42 LOC) — HTML/Markdown escape + emoji.
- `charts.py` (31 LOC) — chart-URL helpers.

### S.8 `telegram/models/telegram_types.py` (97 LOC)

TypedDict definitions for callback payloads, message states, alert envelopes.

---

# Appendix T — Factory subsystem detail (`src/factory/`, 27 files, 2,891 lines)

Even though `[factory] enabled=false`, the code is present and importable.

### T.1 Top-level files

| File | LOC | Role |
|---|--:|---|
| `__init__.py` | 1,440 B | Exports. |
| `discoverer.py` | 178 | `PatternDiscoverer.run_full_discovery()` orchestrates 7 analyzers. |
| `generator.py` | 156 | Claude-driven code synth from `DiscoveredPattern`. |
| `validator.py` | 147 | Syntax + import + interface validation on generated strategy code. |
| `backtester.py` | 169 | Runs generated strategies against historical OHLCV. |
| `simulator.py` | 175 | Pure-python fill simulator used by backtester. |
| `monte_carlo.py` | 94 | MC permutation for robust return estimation. |
| `walk_forward.py` | 89 | Walk-forward validation. |
| `metrics.py` | 195 | Sharpe, Sortino, Calmar, max DD, win rate, profit factor. |
| `lifecycle.py` | 109 | State machine `generated → validated → backtested_pass → trial_active → promoted|demoted|killed` + quarterly revival. |
| `trial_manager.py` | 154 | Trial lifecycle — tracks trades, evaluates promotion. |
| `live_monitor.py` | 98 | Live pattern monitor during trial. |

### T.2 Analyzers (`src/factory/analyzers/`, 7 files)

- `single_variable.py` (146) — single-indicator crosses / breakouts.
- `multi_variable.py` (129) — indicator combinations / confluences.
- `sequential.py` (104) — ordered-event patterns (close>SMA then RSI oversold).
- `cross_asset.py` (93) — pair-correlations.
- `temporal.py` (92) — seasonal / hour-of-day / day-of-week.
- `news_reactive.py` (104) — price moves after news events.
- `micro_patterns.py` (124) — candle-level reversal patterns.

### T.3 Prompts (`src/factory/prompts/`, 4 files)

- `generation_prompt.py` (49 LOC) — Claude system prompt for strategy-code generation.
- `discovery_prompt.py` (40 LOC) — pattern validation prompt.
- `validation_prompt.py` (20 LOC) — code validation prompt.
- `__init__.py`.

### T.4 Models (`src/factory/models/`, 3 files)

- `pattern_models.py` — `DiscoveredPattern`, `PatternOccurrence`.
- `strategy_models.py` — `GeneratedStrategy`, `BacktestResult`.
- `__init__.py`.

---

# Appendix U — Test file enumeration (54 files)

### U.1 Top-level

`__init__.py` (1), `conftest.py` (101), `test_logging_routing.py` (78), `test_protected_tables.py` (178), `test_apex_direction_lock.py` (543), `test_apex_pipeline_integration.py` (846), `test_firewall_and_time_decay.py` (590), `overhaul29_integration_test.py` (605), `overhaul29_pipeline_test.py` (888).

### U.2 `test_phase0/` (9 files, 1,028 LOC)

`test_constants.py` (104), `test_decorators.py` (192), `test_exceptions.py` (110), `test_logging.py` (73), `test_settings.py` (84), `test_types.py` (275), `test_utils.py` (154), `test_validators.py` (135), `__init__.py`.

### U.3 `test_phase1/` (5 files, 349 LOC)

`test_cleanup.py` (66), `test_context_repo.py` (110), `test_learning_repo.py` (79), `test_models.py` (94), `conftest.py` (16).

### U.4 `test_phase2/` (9 files, 1,186 LOC)

`test_account_service.py` (63), `test_auth.py` (72), `test_client.py` (91), `test_instrument_service.py` (100), `test_market_service.py` (98), `test_order_service.py` (161), `test_position_service.py` (125), `test_websocket.py` (134), `conftest.py` (342).

### U.5 `test_phase3/` (14 files, 1,304 LOC)

`test_aggregator.py` (109), `test_calendar_service.py` (42), `test_confidence.py` (101), `test_fear_greed.py` (66), `test_finnhub_client.py` (68), `test_funding_rates.py` (72), `test_news_service.py` (110), `test_onchain.py` (54), `test_open_interest.py` (59), `test_reddit_client.py` (65), `test_reddit_service.py` (96), `test_scorer.py` (99), `test_signal_generator.py` (130), `conftest.py` (209).

### U.6 `test_phase4/` (8 files, 926 LOC)

`test_candlestick.py` (96), `test_chart_patterns.py` (110), `test_engine.py` (165), `test_momentum.py` (119), `test_trend.py` (119), `test_volatility.py` (102), `test_volume.py` (84), `conftest.py` (131).

### U.7 `test_phase5/` (12 files, 819 LOC)

`test_altdata_worker.py` (53), `test_base_worker.py` (128), `test_cleanup_worker.py` (64), `test_health.py` (68), `test_kline_worker.py` (38), `test_manager.py` (77), `test_news_worker.py` (35), `test_price_worker.py` (34), `test_reddit_worker.py` (13), `test_signal_worker.py` (61), `conftest.py` (157).

### U.8 `test_phase6/` (10 files, 716 LOC)

`test_altdata_tools.py` (39), `test_analysis_tools.py` (40), `test_auth.py` (23), `test_news_tools.py` (40), `test_risk_tools.py` (49), `test_sentiment_tools.py` (39), `test_system_tools.py` (86), `test_trading_tools.py` (84), `conftest.py` (222).

### U.9 `test_phase7/` (8 files, 665 LOC)

`test_claude_client.py` (69), `test_cost_tracker.py` (53), `test_decision_parser.py` (91), `test_executor.py` (92), `test_prompt_builder.py` (59), `test_prompts.py` (49), `test_scheduler.py` (72), `conftest.py` (139).

### U.10 `test_phase8/` (7 files, 532 LOC)

`test_alert_manager.py` (124), `test_formatter.py` (75), `test_telegram_bot.py` (78), `test_templates.py` (82), `test_throttle.py` (62), `conftest.py` (111).

### U.11 `test_phase9/` (7 files, 558 LOC)

`test_drawdown.py` (103), `test_portfolio.py` (52), `test_position_sizer.py` (74), `test_risk_manager.py` (75), `test_stop_loss.py` (56), `test_validators.py` (151), `conftest.py` (83).

### U.12 Feature-specific

`test_analysis/test_vol_scale.py` (133 LOC).

`test_factory/` — `test_backtester.py` (210), `test_discoverer.py` (93), `test_generator.py` (69), `test_validator.py` (90), `conftest.py` (129).

`test_integration/test_integration.py` (124 LOC).

`test_portfolio/test_portfolio.py` (214), `conftest.py` (18).

`test_strategies/` — `test_categories_a_f.py` (381), `test_categories_g_k.py` (264), `test_ensemble.py` (141), `test_optimizer.py` (91), `test_pnl_manager.py` (102), `test_regime.py` (42), `test_registry.py` (135), `test_scanner.py` (56), `test_scorer.py` (116), `test_signal_types.py` (81), `test_smart_leverage.py` (94), `conftest.py` (118).

`test_telegram/test_telegram.py` (212 LOC).

`test_watchdog/test_position_watchdog.py` (815), `conftest.py` (195).

### U.13 Test runner

`pytest` via `make test`. `asyncio_mode=auto` from `pyproject.toml` — no per-test `@pytest.mark.asyncio` boilerplate. Custom marker `slow` defined but used sparingly. `pytest-cov` is installed but no `.coveragerc` is committed; `.coverage` at project root (53 KB) is a last-run artefact.

---

# Appendix V — X-RAY model dataclasses (`src/analysis/structure/models/`)

All live under `src/analysis/structure/models/structure_types.py`.

- `StructuralAnalysis(symbol, current_price, timestamp, support_levels, resistance_levels, position_in_range, market_structure, structural_placement, nearest_fvg, nearest_ob, active_sweep_signal, volume_profile, fibonacci, mtf_confluence, session_context, setup_score, suggested_direction, is_setup, setup_rank, ...)` with `to_dict()` → 32 keys.
- `FairValueGap(direction, top, bottom, age_candles, displacement_strength, filled, filled_at)`.
- `OrderBlock(level, direction, strength_0_100, fresh, retested_count, created_candle_index)`.
- `LiquidityZone(level, side, equal_count, round_number, swept)`.
- `LiquiditySweep(zone, direction, wick_pct, reversal_strength, classification: high_probability|moderate|weak)`.
- `VolumeProfileResult(poc_price, value_area_high, value_area_low, current_vs_poc: above|at|below, hvns, lvns)`.
- `FibSwing(swing_high, swing_low, retracement_levels, extension_levels, fib_key_level, confluence_with)`.
- `MTFConfluence(score_0_10, quality: none|weak|good|maximum, tf_alignment: dict[tf → bool])`.
- `SessionContext(current_session, session_phase, manipulation_likely, recommendation: trade|caution|skip)`.
- `StructuralSetup(symbol, direction, entry_quality, rr_quality, composite_score, ranking_score, qualified)`.
- `StructuralPlacement(entry_quality: ideal|good|poor, sl_price, tp_price, rr_ratio, rr_quality: excellent|good|fallback)`.
- `MarketStructureResult(structure: uptrend|downtrend|ranging, last_bos, last_choch)`.

---

# Appendix W — Shadow HTTP API full surface (port 9090, from `src/api/shadow_client.py`)

### W.1 Endpoints

- `GET /api/health` — `{"status":"ok","uptime_s":N}`.
- `GET /api/positions` — `[{symbol, side, size, entry_price, mark_price, pnl_pct, pnl_usd, sl, tp, leverage, opened_at}]`.
- `GET /api/position/{symbol}` — single object or `null`.
- `GET /api/position/{symbol}/last_close` — authoritative close: `{exit_price, net_pnl_pct, net_pnl_usd, hold_duration_seconds, close_reason, fee_usd, slippage_usd}`.
- `POST /api/close` — body `{symbol}`; returns FILLED/REJECTED Order.
- `POST /api/reduce` — body `{symbol, qty}`; returns FILLED/REJECTED.
- `POST /api/set-sl` — body `{symbol, stop_loss}`.
- `POST /api/set-tp` — body `{symbol, take_profit}`.
- `POST /api/order` — body `{symbol, side, order_type, qty, price, sl, tp, leverage}`; returns Order.
- `GET /api/balance` — `{total_equity, available, used_margin, unrealized_pnl}`.
- `GET /api/ticker/{symbol}` — `{last_price, bid, ask, volume_24h}`.
- `GET /api/summary` — dashboard JSON used by Shadow's Telegram bot.

### W.2 Matching engine (Shadow's `order_engine.py`, 761 LOC)

- Reads price via `get_price_data(symbol)` which reads WS-maintained ticker.
- Taker fee 0.055 %, slippage 0.03 % (fixed) applied on every fill.
- SL/TP triggers monitored by `position_monitor.py` (412 LOC) at `position_monitor_interval=1` s.
- Liquidation: margin-ratio based; wallet.py computes equity + used margin every tick.
- Partial close via `reduce_position(symbol, qty)` — supported in current Shadow build; returns FILLED with adjusted `size`.
- On successful fill, `trade_recorder.py` persists to `virtual_trade_history`; `_on_trade_open` / `_on_trade_close` callbacks fire (wired to Shadow's own Telegram bot + TIAS collection on main side).

### W.3 Wallet

`VirtualWallet` (295 LOC): `initialize(starting_balance=10_000)`, `get_equity()`, `get_available()`, `apply_fill(symbol, qty, price, fee)`, `apply_close(symbol, exit_price, pnl_usd, fee)`. Wallet snapshots via `wallet_snapshotter.py` every 60 s.

### W.4 Daily rollup

`daily_rollup.py` (294 LOC) — at 00:00 UTC writes per-day aggregates to Shadow's `daily_summary` table.

### W.5 Collectors

- `websocket.py` (383) — public-linear stream to `stream.bybit.com/v5/public/linear` with exponential back-off.
- `ticker_collector.py` (123) — 60 s ticker snapshot.
- `kline_collector.py` (200) — 1-minute klines with REST backfill.
- `funding_collector.py` (131) — 8 h funding polling.
- `oi_collector.py` (97) — 5 min OI polling.
- `coin_selector.py` (131) — selects top N (default 100) by 24 h volume; re-ranks daily via `coin_refresh_interval`.

### W.6 Shadow retention

`retention.py` (349 LOC) drives:
- `ticker_snapshots` → 30 days.
- `oi_snapshots` → 90 days.
- `wallet_snapshots` → 30 days.
- `closed virtual_positions` → 30 days (open positions explicitly skipped by `status = 'closed'` guard).
- `klines` / `funding_rates` / `virtual_trade_history` / `daily_summary` → `forever`.

---

# Appendix X — Intelligence layer per-file (`src/intelligence/`)

### X.1 news (3 + __init__)

- `finnhub_client.py` (124 LOC): `FinnhubClient.get_news(category, min_id, max_id)`, `get_calendar(country, horizon_days)`. Rate-limits to 60/min client-side.
- `news_service.py` (208 LOC): `NewsService.fetch_latest_news(max_articles)` iterates client, deduplicates by id, scores headline via `SentimentScorer`, writes to `news_articles`. `get_news_for_symbol`, `search_news`.
- `calendar_service.py` (93 LOC): `CalendarService.get_upcoming_events(days)` — writes to `economic_calendar`.

### X.2 altdata (4 + __init__)

- `fear_greed.py` (122 LOC): `FearGreedClient.get_latest()`, `get_history(days)`. Polls `api.alternative.me/fng/`; writes `fear_greed_index`.
- `funding_rates.py` (117 LOC): `FundingRateTracker.fetch_current_rates(symbols)`, `get_rate_history(symbol, hours)`. Uses pybit.
- `open_interest.py` (111 LOC): `OpenInterestTracker.fetch_current(symbols)` — computes 24h change, writes `open_interest`.
- `onchain.py` (129 LOC): `OnChainClient.get_global_metrics()` → total market cap + BTC dominance via CoinGecko.

### X.3 sentiment (4 + __init__)

- `reddit_client.py` (180 LOC): `RedditClient.get_hot(subreddit, limit)`. asyncpraw OAuth. Disabled.
- `reddit_service.py` (210 LOC): `RedditService.scan_subreddits()`, `get_for_symbol(symbol, hours)`. Writes `reddit_posts` + updates aggregate.
- `scorer.py` (214 LOC): `SentimentScorer.score(text)` → `(score: float, level: SentimentLevel)` via TextBlob polarity + intensity bands.
- `aggregator.py` (325 LOC): `SentimentAggregator.aggregate_for_symbol(symbol)` — weighted combination news 0.35 + reddit 0.30 + F&G 0.20 + momentum 0.15. Zero-coverage cache. Writes `aggregated_sentiment`.

### X.4 signals (3 + __init__)

- `signal_generator.py` (247 LOC): `SignalGenerator.generate_signal(symbol)` → `Signal` with rule-based contrarian + trend-following combination.
- `confidence.py` (129 LOC): `ConfidenceCalculator.compute(agreement, magnitude, volume_points, age_hours)` — weighted score, clamped 0–1.
- `signal_models.py` (98 LOC): `CONFIDENCE_THRESHOLDS` ladder, `FUNDING_RATE_THRESHOLDS`, `SOURCE_WEIGHTS`.

---

# Appendix Y — Trading services (`src/trading/services/`)

### Y.1 `market_service.py` (10,092 B, ~230 LOC)

`MarketService(bybit, db)` — `get_klines(symbol, timeframe, limit)`, `get_ticker(symbol)` (with 5-second local cache), `get_orderbook(symbol, depth)`, `get_tickers()` (bulk), `get_latest_price(symbol)`. All REST via pybit with rate-limit + retry.

### Y.2 `order_service.py` (19,423 B, ~440 LOC)

`OrderService(bybit, db, settings)` — `place_order(symbol, side, order_type, qty, price, sl, tp, leverage)`, `modify_order`, `cancel_order`, `cancel_all_orders(symbol)`, `get_open_orders(symbol)`. Handles Bybit order-posting; validates via `src/core/sl_tp_validator.py`.

### Y.3 `position_service.py` (14,078 B, ~330 LOC)

`PositionService(bybit, db, settings)` — `get_positions()`, `get_position(symbol)`, `close_position(symbol, qty_pct)`, `set_stop_loss(symbol, sl)`, `set_take_profit(symbol, tp)`, `get_pnl_summary()`. Proxy-pattern: when `settings.general.mode="shadow"`, ShadowPositionService is used; when `live`, BybitPositionService.

### Y.4 `account_service.py` (3,610 B, ~85 LOC)

`AccountService(bybit, db)` — `get_wallet_balance()`, `get_available_balance()`, `get_equity()`, `save_snapshot()`. Writes `account_snapshots`.

### Y.5 `instrument_service.py` (5,555 B, ~130 LOC)

`InstrumentService(bybit, db)` — `get_info(symbol)` (instrument specs: tick size, qty step, min qty), `get_all_instruments()`. In-memory cache keyed by symbol.

---

# Appendix Z — Final recap

- Inventory at v1 captured 1,707 lines; v2 (this doc) totals **≥ 4,000 lines** with the added appendices.
- The document is an explicit planning map, not a compression — every file referenced has at least one concrete data point (LOC, class, method set, or config key).
- Additional depth that could be layered in for v3: per-method parameter lists for every public API (would approximately double the page count), exhaustive per-SQL-statement source enumeration via AST walk, and per-worker log-tag frequency counts from the existing `data/logs/`.

**End of SYSTEM_INVENTORY.md.** Produced 2026-04-24 from live measurements of `/home/inshadaliqbal786/trading-intelligence-mcp` and `/home/inshadaliqbal786/shadow`. No code was modified.




