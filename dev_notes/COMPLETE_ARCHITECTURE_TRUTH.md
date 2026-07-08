# COMPLETE_ARCHITECTURE_TRUTH

**Generated:** 2026-04-26 (UTC)
**Scope:** trading-intelligence-mcp + shadow projects on the GCP VM
**Method:** Raw data collection per `COLLECT_COMPLETE_ARCHITECTURE_TRUTH.md`. No interpretations, no assumptions, no fixes. File:line references throughout.
**Inventory reference:** `/home/inshadaliqbal786/trading-intelligence-mcp/SYSTEM_INVENTORY.md`

---

## TABLE OF CONTENTS

1. [Process Topology](#section-1--process-topology)
2. [Stage and Layer Map](#section-2--stage-and-layer-map)
3. [Data Workers](#section-3--data-workers)
4. [XRAY / Structure Layer](#section-4--xray--structure-layer)
5. [Stage 1 — Strategy Pipeline](#section-5--stage-1-strategy-pipeline)
6. [Stage 2 — Strategist / Prompt Builder](#section-6--stage-2-strategist--prompt-builder)
7. [Layer 3 — Execution Pipeline](#section-7--layer-3-execution-pipeline)
8. [Layer 4 — Position Monitoring](#section-8--layer-4-position-monitoring)
9. [Layer 5 — TIAS Post-Trade Intelligence](#section-9--layer-5-tias-post-trade-intelligence)
10. [Storage Layer](#section-10--storage-layer)
11. [Wiring Graph](#section-11--wiring-graph)
12. [Live Behavior Right Now](#section-12--live-behavior-right-now)
13. [Layers Summary Diagram](#section-13--layers-summary-diagram)
14. [Open Questions & Contradictions](#section-14--open-questions--contradictions)

---

# Section 1 — Process Topology

## 1.1 Number of separate Python processes: 3 (currently running)

Confirmed via `ps -ef | grep -iE "(workers\.py|server\.py|shadow|telegram)" | grep -v grep`:

```
inshada+   392  /home/inshadaliqbal786/shadow/.venv/bin/python shadow.py
inshada+   400  /home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python workers.py
inshada+   401  /home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python server.py --transport sse --port 8080
```

A 4th unit (`trading-brain.service`) is installed but not running (Brain v1 deprecated; Brain v2 lives inside `workers.py`). A timer unit (`trading-backup.timer`) fires the `trading-backup.service` daily at 02:00.

## 1.2 Each process

### Process A — workers.py (PID 400)
- **Entry point:** `/home/inshadaliqbal786/trading-intelligence-mcp/workers.py:163` (`if __name__ == "__main__"`)
- **systemd unit:** `trading-workers.service` (`/etc/systemd/system/trading-workers.service`)
- **Port:** none directly; spawns Claude CLI subprocesses; reads localhost:9090 (Shadow) and localhost:8080 (MCP SSE — but via stdio proxy when called as MCP)
- **Purpose:** Hosts every background worker (data fetchers, scanner, structure, strategy, watchdog, sniper, fund manager, TIAS) and the in-process strategist/Claude integration.
- **Resource limits:** `MemoryMax=800M MemoryHigh=600M CPUQuota=80%` (`trading-workers.service:35-37`).
- **Dependency:** `After=network-online.target shadow.service`, `Wants=network-online.target shadow.service` (`:10-11`). Sequenced after Shadow so the boot-time fund-manager balance probe doesn't see ECONNREFUSED on `127.0.0.1:9090`.

### Process B — server.py (PID 401)
- **Entry point:** `/home/inshadaliqbal786/trading-intelligence-mcp/server.py:44` (`if __name__ == "__main__"`)
- **systemd unit:** `trading-mcp-sse.service` (`/etc/systemd/system/trading-mcp-sse.service`)
- **Port:** `8080` (SSE, configured in unit `ExecStart`: `server.py --transport sse --port 8080`)
- **Purpose:** MCP server hosting 43 tools, exposed over SSE for Claude Desktop / claude.ai. Spawned independently from workers; does its own DI bootstrap.
- **Resource limits:** `MemoryMax=200M MemoryHigh=150M CPUQuota=50%` (`trading-mcp-sse.service:21-23`).
- **Dependency:** `After=network-online.target trading-workers.service` (`:4`).

### Process C — shadow.py (PID 392)
- **Entry point:** `/home/inshadaliqbal786/shadow/shadow.py:314` (`if __name__ == "__main__"`)
- **systemd unit:** `shadow.service` (`/etc/systemd/system/shadow.service`)
- **Port:** `9090` (HTTP API, from `shadow/config.toml [api] port = 9090`); also opens Bybit public WebSocket (`wss://stream.bybit.com/v5/public/linear`).
- **Purpose:** Market data warehouse + virtual exchange simulator. Subscribes to Bybit WS for the 50-coin watch_list (read from `workers_config_path = /home/inshadaliqbal786/trading-intelligence-mcp/config.toml`), records klines/tickers/funding/OI to `shadow.db`, emulates a virtual wallet, and exposes a paper-trading order-engine over HTTP.
- **Resource limits:** `MemoryMax=200M MemoryHigh=150M` (`shadow.service:24-25`).
- **Dependency:** `After=network-online.target` only — no soft dep on workers.

### Process D — brain.py (DEPRECATED, present but unused)
- **Entry point:** `/home/inshadaliqbal786/trading-intelligence-mcp/brain.py:69`
- **systemd unit:** `trading-brain.service` installed in `/etc/systemd/system/`, **NOT running** (confirmed by `systemctl list-units --type=service --state=running` — only shadow, trading-workers, trading-mcp-sse listed).
- **In-file marker (lines 24-31):** `"NOTE: Brain v1 (this file) is DEPRECATED. Brain v2 runs inside workers.py."`

### Process E — mcp_stdio_proxy.py (spawned by Claude CLI on demand)
- **Entry point:** `/home/inshadaliqbal786/trading-intelligence-mcp/mcp_stdio_proxy.py:207`
- **systemd unit:** none
- **Port:** consumes upstream `http://127.0.0.1:8080/sse`
- **Purpose:** Forwards the MCP stdio protocol from the Claude CLI subprocess to the long-lived SSE MCP server (PID 401). Holds no DB / no Telegram bot / no services. Source comments cite `observability_02-24_to_02-44_2026-04-24.log` as the rationale (full per-call init was costing 2-5s and creating 4 Telegram reconnects per session).
- **Hard-exit watchdog:** `os._exit` after `_SHUTDOWN_GRACE_S = 2.0` seconds (`mcp_stdio_proxy.py:54`).

### Process F — trading-backup.service (systemd timer, oneshot)
- **Trigger:** `trading-backup.timer` (`OnCalendar=*-*-* 02:00:00`, `/etc/systemd/system/trading-backup.timer:5`)
- **Command:** `/home/inshadaliqbal786/trading-intelligence-mcp/scripts/backup.sh` (`trading-backup.service:8`)
- **Type:** `oneshot` — fires daily at 02:00 UTC.

## 1.3 Inter-process communication

- **workers (400) → Shadow (392):** HTTP REST on `http://127.0.0.1:9090` (Shadow's `[api]` host:port). Routed via `src.api.shadow_client` for paper orders/wallet/positions. Source: `workers.py` registers a `ShadowClient` service when `[general] mode = "shadow"` (`config.toml:2`).
- **workers (400) → Bybit:** REST + WebSocket (`pybit.unified_trading.HTTP` and `BybitWebSocket`) for live ticker, klines (REST), order placement when mode=live, and funding/OI history.
- **Shadow (392) → Bybit:** Public WebSocket only (`wss://stream.bybit.com/v5/public/linear`, `shadow/config.toml:14`).
- **MCP server (401) → workers (400):** none directly; both processes own independent DB connections to the same `data/trading.db`. The MCP server re-bootstraps a parallel DI container.
- **Claude CLI subprocess (spawned by 400) → MCP server (401):** stdio pipe via `mcp_stdio_proxy.py`, which forwards JSON-RPC frames to `http://127.0.0.1:8080/sse` with `Authorization: Bearer ${MCP_AUTH_TOKEN}` (token loaded from `.env`).
- **Shadow → Telegram:** uses `SHADOW_TELEGRAM_BOT_TOKEN` (separate token from workers, `/home/inshadaliqbal786/shadow/.env`).
- **workers → Telegram:** uses `TELEGRAM_BOT_TOKEN` from `/home/inshadaliqbal786/trading-intelligence-mcp/.env`.
- **All processes ↔ DB:** SQLite WAL-mode files (`data/trading.db`, `shadow/data/shadow.db`). Workers also hold a read-only handle to Shadow's DB via `ShadowKlineReader` (`/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/shadow_kline_reader.py:108` opens with `mode=ro`).

## 1.4 Process dependency graph

```
[network-online.target]
        │
        ▼
[shadow.service] ────► (port 9090 HTTP, Bybit WS)
        │
        ▼ (Wants= + After=)
[trading-workers.service] ────► (Bybit, Shadow @ 9090, MCP @ 8080, Claude CLI subprocess)
        │
        ▼ (After=trading-workers.service)
[trading-mcp-sse.service] ────► (port 8080 SSE for Claude CLI / claude.ai)

[timer]
[trading-backup.timer] ──fires daily 02:00──► [trading-backup.service]
                                              ↓ runs scripts/backup.sh
```

`trading-brain.service` is installed but inactive: `Brain v2 runs inside workers.py` (`brain.py:24`). The MCP stdio proxy (`mcp_stdio_proxy.py`) is invoked by the Claude CLI as a transient subprocess each time the in-workers strategist makes a Claude call — it is not its own systemd unit.

---

# Section 2 — The Complete Stage and Layer Map

## 2.1 Stages identified (in order from data ingest to execution)

| # | Stage name in code | File defining it | Aliases used | Upstream | Downstream |
|---|---|---|---|---|---|
| 0 | (External APIs) | n/a | "Layer 0" in narrative | Bybit, Finnhub, OpenRouter, Anthropic CLI, CoinGecko, Reddit, alternative.me F&G | Shadow, all data workers |
| 1A | DATA WORKERS (price, kline, signal, structure, regime, altdata, strategy) | `src/workers/*_worker.py` | "the 7 workers", Layer 1 (DATA) per `core/layer_manager.py:3` | Bybit, Shadow.db (klines fallback), watch_list config | TACache, StructureCache, signal_cache, score_cache, regime cache, funding cache |
| 1B | ScannerWorker | `src/workers/scanner_worker.py:30` (`class ScannerWorker(SweetSpotWorker)`) | "cycle trigger" (per docstring `:5-15`) | Reads warm caches from 1A workers via `services` dict | Writes `active_universe` table + `MarketScanner._active_universe` (30-coin focus) |
| 1.x (within strategy_worker) | Layer 1 Strategy Scanner / Layer 2 Trade Scorer / Layer 3 Ensemble Voter | `src/workers/strategy_worker.py`; canonical layer doc `src/strategies/__init__.py:3-7` | "the 4-layer architecture" | watch_list (50 coins), TACache, sentiment, regime | Stage 2 hints (`layer_manager._strategy_hints`), `_score_cache` for ScannerWorker |
| 2 | STRATEGIST (CALL_A "trade plan" / CALL_B "position plan") | `src/brain/strategist.py:174` (`class ClaudeStrategist`); calls at `:339` (`create_trade_plan`) and `:412` (`create_position_plan`) | "Brain", "Stage 2", "Layer 2 (BRAIN)" per `core/layer_manager.py:4` | Strategy hints, X-RAY ranked setups, market data, urgent queue | Strategic plan (new_trades + position_actions) |
| 3 | EXECUTION (APEX → TradeGate → OrderService) | `src/apex/optimizer.py:36`, `src/apex/gate.py:29`, `src/trading/services/order_service.py:62`, orchestrated by `src/core/layer_manager.py` (also duplicated at `src/workers/layer_manager.py`) | "Layer 3 (EXECUTION)" per `core/layer_manager.py:5` | Claude directives (Stage 2 output) | Bybit / Shadow paper orders |
| 4 | POSITION MONITORING (ProfitSniper a.k.a. Mode 4, PositionWatchdog, RecoveryPlanner) | `src/workers/profit_sniper.py:75`, `src/workers/position_watchdog.py:75`, `src/fund_manager/recovery_planner.py:31` | "Mode 4", "watchdog" | Open positions, prices | Tightened SL, partial/full closes; close events fed to TIAS |
| 5 | TIAS POST-TRADE | `src/tias/collector.py:30` (collector) + `src/tias/analyzer.py:34` (DeepSeek analyzer) | "post-trade intelligence" | Closed-trade callback record, Mode4 snapshot | `trade_intelligence` table; ds_lessons consumed by future Strategist prompts |

## 2.2 Conceptual layers (operator's mental model)

The codebase contains **two overlapping layer numbering schemes**:

### Scheme A — `src/core/layer_manager.py:3-9` (operator-facing, used for /layer telegram commands and `layer_state.json`):

- **Layer 1 — DATA**: data workers, scanner, regime, TA.
- **Layer 2 — BRAIN**: Claude strategic review every ~3 min.
- **Layer 3 — EXECUTION**: rule engine + watchdog actually act on Claude's plan.

These three are toggled via `data/layer_state.json`. Current state (as of 2026-04-26 18:10:21Z):

```json
{"layer_active":{"1":true,"2":false,"3":false},"user_stopped":true,
 "timestamp":"2026-04-26T18:10:21.315319+00:00"}
```

### Scheme B — `src/strategies/__init__.py:3-7` (strategy-internal, *inside* `strategy_worker.py`):

- **Layer 1 — Strategy Scanner**: 40 strategies emit `RawSignal`.
- **Layer 2 — Trade Scorer**: 0-105 scores into `ScoredSetup`.
- **Layer 3 — Ensemble Voter**: consensus → `EnsembleResult`.
- **Layer 4 — Claude Brain v2**: `TradeDecision`.

### Scheme C — `src/core/container.py:39-116` (DI container, historical):

- **Layer 1**: Bybit client + trading services.
- **Layer 2**: Analysis engine.
- **Layer 3**: Brain services (Claude Code CLI).
- **Layer 4**: Risk + Alerts.
- **Layer 5**: Strategy system.

These three numbering schemes coexist. Section 14.1 records this.

## 2.3 Mapping between stages and layers

| File | Stage role | core/layer_manager scheme | strategies/ scheme |
|---|---|---|---|
| `workers/price_worker.py` | DATA WORKER | Layer 1 | — |
| `workers/kline_worker.py` | DATA WORKER | Layer 1 | — |
| `workers/structure_worker.py` | DATA WORKER (X-RAY) | Layer 1 | — |
| `workers/signal_worker.py` | DATA WORKER (sentiment + signals) | Layer 1 | — |
| `workers/regime_worker.py` | DATA WORKER | Layer 1 | — |
| `workers/altdata_worker.py` | DATA WORKER (funding/OI/F&G) | Layer 1 | — |
| `workers/strategy_worker.py` | DATA + ANALYTIC (runs Layers 1-3 internally + emits hints) | Layer 1 | Layers 1-3 |
| `workers/scanner_worker.py` | CYCLE TRIGGER (reads caches → active_universe) | Layer 1 | — |
| `brain/strategist.py` | STRATEGIST (CALL_A, CALL_B) | Layer 2 | Layer 4 |
| `apex/optimizer.py`, `apex/gate.py`, `trading/services/order_service.py` | EXECUTION | Layer 3 | — |
| `workers/profit_sniper.py`, `workers/position_watchdog.py` | POSITION MONITORING | (runs continuously regardless of layer toggle) | — |
| `fund_manager/recovery_planner.py` | RECOVERY OVERLAY | (independent of layer toggle) | — |
| `tias/*` | POST-TRADE INTELLIGENCE | (independent of layer toggle, fires on close) | — |

---

# Section 3 — The 7 (or however many) Data Workers

## 3.1 All worker files in `src/workers/`

```
allocation_worker.py      1308 bytes
altdata_worker.py         9487 bytes
backtest_worker.py        2605 bytes
base_worker.py           16539 bytes
cleanup_worker.py        12165 bytes
discovery_worker.py       3975 bytes
enforcer_worker.py        1331 bytes
firewall.py               1491 bytes
fund_manager_worker.py     940 bytes
health.py                 2977 bytes
kline_worker.py          18567 bytes
layer_manager.py         33668 bytes  (DUPLICATE of src/core/layer_manager.py)
live_monitor_worker.py    2055 bytes
manager.py               95195 bytes
news_worker.py            2721 bytes
optimization_worker.py    2146 bytes
position_watchdog.py    127222 bytes
price_alert_worker.py     2275 bytes
price_worker.py          10799 bytes
profit_sniper.py        138086 bytes
reddit_worker.py          1235 bytes
regime_worker.py         12438 bytes
scanner_worker.py        14143 bytes
scheduled_report_worker.py 1246 bytes
settings.py              45072 bytes  (NOTE: also in src/config/, see Section 14)
signal_worker.py          7469 bytes
sniper_models.py         37658 bytes
sniper_ring_buffer.py    13805 bytes
strategy_worker.py       62670 bytes
structure_worker.py      11891 bytes
sweet_spot_scheduler.py   9233 bytes
telegram_bot_worker.py    2088 bytes
trial_monitor_worker.py   1698 bytes
```

## 3.2 Per-worker classification table

| Worker file | Class name (file:line) | Type | Universe source | Tick interval | Primary output |
|---|---|---|---|---|---|
| price_worker.py | `PriceWorker(BaseWorker)` (`:26`) | DATA | `settings.universe.watch_list` (50) (`:85`) | `settings.workers.market_data_interval` (45s, `:46`) — fixed | `_ws_quotes` in-memory + `ticker_cache` table |
| kline_worker.py | `KlineWorker(SweetSpotWorker)` (`:52`) | DATA | `settings.universe.watch_list` (`:142`) | sweet spot `0:30` per 5-min window (`:75`) | `klines` table; `_circuit_breaker_until` (gates strategy_worker) |
| structure_worker.py | `StructureWorker(SweetSpotWorker)` (`:28`) | DATA (X-RAY) | `settings.universe.watch_list`, batched 25/tick (`:230,77`) | sweet spot `0:45` per 5-min window (`:56`) | `StructureCache` (cache + ranked setups + skip list) |
| signal_worker.py | `SignalWorker(SweetSpotWorker)` (`:27`) | DATA (sentiment+signals) | `settings.universe.watch_list` (`:72`) | sweet spot `1:00` per 5-min window (`:51`) | `_signal_cache` in-memory; rows in `signals` and `aggregated_sentiment` |
| regime_worker.py | `RegimeWorker(SweetSpotWorker)` (`:22`) | DATA | `settings.universe.watch_list` (`:60`) | sweet spot `1:15` per 5-min window (`:42`) | `RegimeDetector._per_coin_regimes` + `regime_history` + `coin_regime_history` tables |
| altdata_worker.py | `AltDataWorker(SweetSpotWorker)` (`:29`) | DATA | `settings.universe.watch_list` (`:98`) | sweet spot `1:45` (funding); OI every `open_interest_minutes`; F&G every `fear_greed_minutes` (`:79-83`) | `_funding_cache` + `funding_rates`/`open_interest`/`fear_greed_index` tables |
| strategy_worker.py | `StrategyWorker(SweetSpotWorker)` (`:34`) | ANALYTIC + hint producer | `settings.universe.watch_list` (`:149`) | sweet spot `1:30` per 5-min window | `_score_cache`; pushes top-20 hints + per-coin consensus into `layer_manager._strategy_hints` |
| scanner_worker.py | `ScannerWorker(SweetSpotWorker)` (`:30`) | CYCLE TRIGGER (NOT a data worker per its docstring `:5`) | `settings.universe.watch_list` (`:223`) | sweet spot `4:00` per 5-min window (`:55`) | `active_universe` table (full DELETE+INSERT) + `MarketScanner._active_universe` |
| news_worker.py | `NewsWorker(BaseWorker)` (`:16`) | DATA | n/a (Finnhub global) | `settings.workers.news_interval` (300s, `:35`) | `news_articles` table |
| reddit_worker.py | `RedditWorker(BaseWorker)` | DATA (DISABLED) | configured subreddits | `settings.workers.reddit_interval` | `reddit_posts` table — but `[reddit] enabled = false` |
| position_watchdog.py | `PositionWatchdog(BaseWorker)` (`:75`) | LAYER 4 monitor | open positions | `settings.watchdog.check_interval_seconds` (10s, `:123`) | SL tightens, partial/full closes, urgent queue concerns |
| profit_sniper.py | `ProfitSniper(BaseWorker)` (`:75`) | LAYER 4 monitor (Mode 4) | open positions | `settings.mode4.check_interval_seconds` (5s, `:124`) | SL trails, partial/full closes; `sniper_log` rows |
| enforcer_worker.py | `EnforcerWorker(BaseWorker)` | UTILITY | n/a | 60s default | runs `PerformanceEnforcer.check_and_enforce()` |
| fund_manager_worker.py | `FundManagerWorker(BaseWorker)` | UTILITY | n/a | 60s default | runs `IntelligentFundManager.update_state()` |
| allocation_worker.py | `AllocationWorker(BaseWorker)` | UTILITY | n/a | 5 min | risk budget updates |
| cleanup_worker.py | `CleanupWorker(BaseWorker)` | UTILITY | n/a | 1 hr | DB pruning + VACUUM |
| price_alert_worker.py | `PriceAlertWorker(BaseWorker)` | UTILITY | user-defined alerts | 10s | Telegram alerts |
| backtest_worker.py | `BacktestWorker(BaseWorker)` | UTILITY (factory) | validated strategies | hourly | `backtest_results` table |
| discovery_worker.py | `DiscoveryWorker(BaseWorker)` | UTILITY (factory) | n/a | daily | `discovered_patterns` table |
| live_monitor_worker.py | `LiveMonitorWorker(BaseWorker)` | UTILITY (factory) | recent klines | 5 min | pattern occurrence rows |
| trial_monitor_worker.py | `TrialMonitorWorker(BaseWorker)` | UTILITY | trial strategies | hourly | promote/demote events |
| optimization_worker.py | `OptimizationWorker(BaseWorker)` | UTILITY | n/a | weekly | portfolio optimization output |
| scheduled_report_worker.py | `ScheduledReportWorker(BaseWorker)` | UTILITY | n/a | 5 min | scheduled Telegram reports |
| telegram_bot_worker.py | `TelegramBotWorker(BaseWorker)` | UTILITY | n/a | runs bot poller | Telegram interactions |

## 3.3 The "data workers" (those fetching external data for the universe)

Confirmed seven, matching the `SCANNER_TICK_SUMMARY` docstring at `scanner_worker.py:5-15`:

1. **PriceWorker** — Bybit WebSocket tickers
2. **KlineWorker** — Bybit REST klines
3. **StructureWorker** — X-RAY structural analysis (consumes klines from `trading.db` + Shadow `shadow.db` fallback)
4. **SignalWorker** — sentiment aggregation + per-coin signals
5. **RegimeWorker** — global + per-coin regime
6. **AltDataWorker** — funding rates / open interest / Fear & Greed / on-chain
7. **StrategyWorker** — runs all 40 strategies, scores, ensembles; technically also produces the per-coin `_score_cache`

ScannerWorker is the **cycle trigger** that reads outputs of the seven and writes `active_universe`; it is explicitly **not** counted as one of the seven (`scanner_worker.py:5-6`).

## 3.4 The "analytical workers"

- StrategyWorker (when treated as Layer 1-3 strategy pipeline rather than a data worker)
- ScannerWorker (composite scoring)

## 3.5 The "utility workers"

CleanupWorker, EnforcerWorker, FundManagerWorker, AllocationWorker, BacktestWorker, DiscoveryWorker, LiveMonitorWorker, TrialMonitorWorker, OptimizationWorker, ScheduledReportWorker, PriceAlertWorker, TelegramBotWorker.

## 3.6 The seven data workers per the operator's terminology — confirmed

- **Count:** 7
- **Names:** PriceWorker, KlineWorker, StructureWorker, SignalWorker, RegimeWorker, AltDataWorker, StrategyWorker.
- **Universe scope:** all 7 read `settings.universe.watch_list` (50 coins). The previous "30 coin filter" path was removed in the corrected Layer 1 architecture (per worker docstrings citing `LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md`).
- **Active universe (30 coins):** materialised by ScannerWorker into `active_universe` table; this is what Stage 2 (`scanner.get_active_universe()`) reads.

## 3.7 Universe-change callbacks

Grep `on_universe_change|universe_change_callback|register_callback` against `src/workers/`:

```
(no matches in src/workers/ as of this collection)
```

But — the live log shows callbacks are wired through `MarketScanner.get_subscribers_snapshot()` from `scanner_worker.py:304`, with empty subscribers normally. PriceWorker and KlineWorker do define `_on_universe_change` methods that execute when the legacy `MarketScanner.scan_market` path runs (visible in live logs: `PRICE_UNSUB | coins=2 sample=[FILUSDT,ADAUSDT]` at 21:32:01.262 and `KLINE_STATE_CLEANUP` at 21:32:01.271).

---

# Section 4 — The XRAY / Structure Layer

## 4.1 XRAY conceptual identity

X-RAY is **separate from** the file `structure_worker.py`. The worker file is the scheduler/runner; the X-RAY computation lives in `src/analysis/structure/structure_engine.py` (`class StructureEngine`, `:46`) and 13 sub-engine files.

**Code paths where the term "XRAY" appears (file:line):**
- `core/logging.py:67` — `"xray": "workers.log"` (loguru routing).
- 11 X-RAY phase files at `src/analysis/structure/` (phase markers in their docstrings).
- `analysis/structure/structure_cache.py:1` — "X-RAY structural analysis cache".
- `apex/models.py:242,387` — `StructuralData` consumer in APEX.
- `brain/strategist.py:732,1428` — X-RAY context section in prompts.
- `strategies/scorer.py:7,43,249,266,322` — `_xray_sr_score` and X-RAY-aware scoring.
- `strategies/performance_enforcer.py:154,166,478` — "Quality-gate for SURVIVAL mode trades using X-RAY data".
- `core/sl_tp_validator.py:150-242` — X-RAY-aware SL/TP validation.
- `analysis/structure/shadow_kline_reader.py:28,108,122,278,292` — X-RAY's Shadow-DB fallback.
- `workers/structure_worker.py:25` — `log = get_logger("xray")`.

## 4.2 The X-RAY phases (per docstrings of the analysis files)

| Phase # | Name | File | Output |
|---|---|---|---|
| 1 | Support & Resistance | `support_resistance.py:1` (class `SupportResistanceEngine` at `:22`) | `support_levels, resistance_levels, swing_data` |
| 2 | Market Structure (BOS/CHoCH) | `market_structure.py:1` (class `MarketStructureDetector` at `:25`) | `MarketStructureResult` |
| 3 | Structural SL/TP Placement | `structural_levels.py:1` (class `StructuralLevelCalculator` at `:18`) | `StructuralPlacement` |
| 3a | Volume Profile | `volume_profile.py:1` (class `VolumeProfileCalculator` at `:26`) | `volume_profile` |
| 3b | Fibonacci | `fibonacci.py:1` (class `FibonacciCalculator` at `:33`) | `fibonacci` |
| 3c | MTF Confluence Scorer | `mtf_confluence.py:1` (class `MTFConfluenceScorer` at `:27`) | `mtf_confluence` |
| 4 | Fair Value Gap (FVG) | `fair_value_gap.py:1` (class `FairValueGapDetector` at `:32`) | `fvgs` |
| 5 | Order Block (OB) | `order_blocks.py:1` (class `OrderBlockDetector` at `:26`) | `order_blocks` |
| 6+7 | Liquidity Zone mapping + Sweep detection | `liquidity.py:1` (class `LiquidityMapper` at `:44`) | `liquidity_zones`, `recent_sweeps` |
| 11 | Smart Coin Selection (Setup Scanner) | `setup_scanner.py:1` (class `SetupScanner` at `:22`) | top-12 ranked setups, skip_list |
| 12 | Institutional Session Timing | `session_timing.py:1` (class `SessionTimer` at `:35`) | `SessionContext` |

`structure_engine.py` invokes phases 1-10 in `analyze()` (lines `203-459`). Phase 11 (`SetupScanner`) is invoked by `structure_worker.py:138-147` after the analysis loop. Phase 12 (`SessionTimer`) is invoked at `structure_worker.py:88-97`.

## 4.3 setup_scanner

- **Input:** dict of `{symbol: StructuralAnalysis}` from `StructureCache.get_all()`, plus optional `SessionContext`.
- **Scoring formula** (`setup_scanner.py:213-263`, `_calc_ranking_score`, verbatim quote):

```python
score += analysis.setup_score * 0.25
mtf = analysis.mtf_confluence
if mtf:
    score += mtf.score * 2.5
smc = 0.0
if analysis.nearest_ob and analysis.nearest_ob.fresh: smc += 10
if analysis.nearest_fvg: smc += 8
if analysis.active_sweep_signal: smc += 7
score += min(25.0, smc)
sp = analysis.structural_placement
if sp:
    if sp.rr_ratio >= 4.0: score += 15
    elif sp.rr_ratio >= 3.0: score += 10
    elif sp.rr_ratio >= 2.0: score += 5
if session:
    sess = session.current_session; phase = session.session_phase
    if sess == "new_york" and phase == "mid": score += 5
    elif sess == "london" and phase == "mid": score += 5
    elif sess == "late_ny": score -= 5
    elif sess == "london" and phase == "early" and session.manipulation_likely: score -= 10
```

- **Output count:** `MAX_SETUPS = 12` (`setup_scanner.py:18`).
- **Skip list logic:** must pass at least `MIN_QUALIFYING_CRITERIA = 3` of 6 criteria (`setup_scanner.py:19,58-65`); coins below threshold OR ranked >12 go to `skip_list`.
- **The 6 qualification criteria** (`_evaluate_qualification`, `:91-135`): `at_level`, `structure_aligned`, `rr_adequate (≥2.0)`, `smc_present (FVG or fresh OB or sweep)`, `confluence_good (mtf.score ≥ 5)`, `session_favorable (not manipulation_likely AND not late_ny)`.

## 4.4 XRAY's input universe

`structure_worker._get_universe()` reads `settings.universe.watch_list` directly (`structure_worker.py:230,242`). Batched at `batch_size=25` per tick (`structure_worker.py:77`, `config.toml [analysis.structure] batch_size = 25`). With 50 coins and batch_size 25, a full sweep takes 2 ticks (~10 min via two sweet-spot fires).

The runtime `XRAY_TICK_SUMMARY` lines confirm batches=2 (e.g. `21:30:10.868 ... batch=0/2 symbols=5 analyzed=5 errors=0 cached=37`). The `cached=37` signals 37 distinct symbols in the rolling cache — but the live universe is 30 coins per `active_universe` (Section 12.3); 37 - 30 = 7 stale entries from prior universes still within the 5-min TTL.

## 4.5 XRAY's output consumers

```
strategies/performance_enforcer.py:152-460     get_ranked_setups + get(symbol)
brain/strategist.py:484-836                    get_ranked_setups + get_top_setups + get_all (CALL_A)
brain/strategist.py:1430-1511                  same set of methods (CALL_B)
apex/gate.py:297                               services.get("structure_cache")
apex/assembler.py:682                          services.get("structure_cache")
telegram/handlers/analysis.py:37-56            get_ranked_setups (display)
telegram/handlers/analysis.py:185-205          get_top_setups(n=8) (display)
strategies/scorer.py:268,322                  _xray_sr_score(structural_data)  (Layer 2 scorer)
core/sl_tp_validator.py:150-242                XRAY_SL_ADJUST / XRAY_SLTP / XRAY_TP_NOTE
```

## 4.6 XRAY direction veto (XRAY_DIR_BLOCK)

- **Computed:** `src/workers/strategy_worker.py:825` — `f"XRAY_DIR_BLOCK | sym={symbol} chosen={direction} ..."`
- **Skip emission:** `strategy_worker.py:830` — `f"TRADE_SKIP | sym={symbol} rsn=xray_dir_block ..."`
- **Return value:** `strategy_worker.py:834` — `return (False, "xray_dir_block")`
- **What it blocks:** the strategy_worker's own emitted setup (signals never reach Stage 2 hints when this fires). It does not block Claude itself if Claude independently picks the same coin from market data.

---

# Section 5 — Stage 1 (Strategy Pipeline)

## 5.1 Stage 1 components — the 4 internal layers

Per `src/strategies/__init__.py:3-7`:

- **Layer 1 — Strategy Scanner**: 40 strategies emit `RawSignal` objects. Files in `src/strategies/categories/` (40 .py files, see 5.2).
- **Layer 2 — Trade Scorer**: `src/strategies/scorer.py:1` — `class TradeScorer`. Scores 0-105 with breakdown:
  - Base (0-40): Conditions strength
  - Confluence (0-25): Multiple indicator agreement
  - Context (0-20): Higher TF, sentiment, F&G, funding, regime
  - Quality (0-20): Spread, volume, S/R + X-RAY structure
- **Layer 3 — Ensemble Voter**: `src/strategies/ensemble.py:1`. All active strategies vote on each scored setup; the originating strategy is excluded. Consensus levels: STRONG (size_mult=1.0), GOOD (0.75), WEAK (0.3), CONFLICT (0.15). All setups pass the gate (consensus determines size, not eligibility) per `EnsembleResult.passed` default `True` (`signal_types.py:91`).
- **Layer 4 — Claude Brain v2**: handed off to `ClaudeStrategist` via `layer_manager._strategy_hints`. The strategy worker stores hints; the strategist reads them next cycle.

## 5.2 Strategy registry

- **Total registered:** **40 strategies** (A1-K4 = 19 + 21 = 40), plus `X1_AlwaysTradeStrategy` only on testnet.
- **Registry file:** `src/strategies/registry.py` (class `StrategyRegistry`).
- **Bulk register call:** `src/strategies/register_all.py:112` — `register_all_strategies(registry)` calls `register_strategies_a_to_f` and `register_strategies_g_to_k`.
- **Live runtime confirmation:** worker log shows `STRAT_L1 | signals=2 strategies=39 coins=5 el=2ms` at 21:31:14.592 — 39 active because the testnet-only X1 is not registered (mainnet).
- **The 40 strategy names (file → class):**

| Group | File | Class |
|---|---|---|
| A1 | a1_rsi_reversal.py | RSIReversalScalp |
| A2 | a2_vwap_bounce.py | VWAPBounceScalp |
| A3 | a3_bb_squeeze_scalp.py | BBSqueezeScalp |
| A4 | a4_ema_crossover.py | EMACrossoverMomentum |
| B1-B4 | b1_volume_breakout, b2_supertrend_follower, b3_ichimoku_breakout, b4_double_bottom_top | VolumeBreakout, SupertrendFollower, IchimokuBreakout, DoubleBottomTop |
| C1-C2 | c1_bb_mean_reversion, c2_rsi_divergence | BBMeanReversion, RSIDivergence |
| D1-D2 | d1_funding_rate_fade, d2_oi_divergence | FundingRateFade, OIDivergence |
| E1-E3 | e1_fear_greed_extreme, e2_news_breakout, e3_sentiment_momentum | FearGreedExtreme, NewsBreakout, SentimentMomentum |
| F1-F4 | f1_support_resistance, f2_multi_tf_alignment, f3_liquidation_hunt, f4_grid_recovery | SupportResistanceBounce, MultiTFAlignment, LiquidationHunt, GridRecovery |
| G1-G4 | g1_stop_hunt_sniper, g2_retail_sentiment_fade, g3_liquidation_frontrunner, g4_whale_shadow | StopHuntSniper, RetailSentimentFade, LiquidationFrontrunner, WhaleShadow |
| H1-H4 | h1_funding_prediction, h2_spread_basis, h3_volatility_switch, h4_order_flow | FundingPrediction, SpreadBasisExploit, VolatilitySwitch, OrderFlowImbalance |
| I1-I4 | i1_kill_zone, i2_weekend_gap, i3_options_expiry, i4_hourly_close | KillZoneTrading, WeekendGapExploit, OptionsExpiryPlay, HourlyCloseMomentum |
| J1-J4 | j1_btc_dominance, j2_correlation_breakdown, j3_cross_exchange_lag, j4_altcoin_beta | BTCDominanceRotation, CorrelationBreakdown, CrossExchangeLag, AltcoinBetaAmplification |
| K1-K4 | k1_claude_conviction, k2_pattern_memory, k3_ensemble, k4_adaptive_optimizer | ClaudeConviction, PatternMemory, MultiStrategyEnsemble, AdaptiveOptimizer |
| X1 (testnet only) | x1_always_trade | AlwaysTradeStrategy |

## 5.3 Stage 1 input universe

`strategy_worker.py:149` — `universe = list(self.settings.universe.watch_list)` (50 coins).
But: pre-fetch step trims to coins with fresh M5 klines (≤300s old). Live log at 21:31:06 shows ~22 of 50 coins skipped via `STRAT_SKIP_STALE` — actual processed cohort was 5 coins per `STRAT_L1 | signals=2 strategies=39 coins=5`.

## 5.4 Stage 1 output

- **Data structure:** `_score_cache: dict[str, float]` (per-symbol total_score), `_strategy_hints` (top-20 list on `layer_manager`), `_strategy_consensus` (per-coin consensus dict).
- **Storage:** in-memory; persisted view via `record_strategy_trade()` to `strategy_trades` table.
- **Consumers:**
  - `ScannerWorker._get_strategy_score(coin)` (`scanner_worker.py:74-81`).
  - `ClaudeStrategist._build_trade_prompt` (`strategist.py:1582-1594`) — STRATEGY HINTS section.

## 5.5 Stage 1 timing

- **Cadence:** sweet spot `1:30` per 5-min window.
- **Tick elapsed example:** `STRAT_CYCLE_DONE | coins=30 signals=12 scored=12 hints=6 urg=1 el=7232ms | gate=1ms prefetch=6218ms ... L1=38ms L2=230ms L3=20ms L4=0ms misc=726ms | sid=s-...` (from log 18:08:29.809). Recent log (21:31:14) showed `prefetch_slow el=35733ms` due to DB contention — confirming the D-3 SQLite lock-contention bottleneck noted in the project memory.
- **Slow-tick threshold:** `_TICK_SLOW_PER_WORKER["strategy_worker"] = 10.0` seconds (`base_worker.py:38`).

---

# Section 6 — Stage 2 (Strategist / Prompt Builder)

## 6.1 Strategist / Stage 2 entry point

- **File:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/strategist.py`
- **Class:** `ClaudeStrategist` (`:174`).
- **Two entry methods:**
  - `create_trade_plan()` (`:339-410`) — CALL A.
  - `create_position_plan()` (`:412-472`) — CALL B.
- **Triggered by:** `core/layer_manager._brain_review_loop` (`:231`) calls `_run_brain_cycle` (`:259`) on a schedule, alternating CALL A and CALL B every `strategic_interval = 150 s` (`config.toml [brain] strategic_interval = 150`).

## 6.2 Prompt structure (CALL_A vs CALL_B)

### CALL A (`_build_trade_prompt`, `strategist.py:1141-1766`) — find new trades

Sections appended in this order (each verbatim `sections.append(...)` call):

1. `coaching` — from `enforcer.get_coaching_text(structure_cache=_sc)` (`:485,1162`).
2. `regime_instructions` — `_build_regime_instructions()` (`:1214`).
3. `dir_perf` — `_build_direction_performance()` (`:1225`).
4. Trading-mode instruction — `trading_mode_mgr.mode.get_claude_mode_instruction()` (`:1235`).
5. `TRADEABLE COINS THIS CYCLE (...): ... (50)` — from `await scanner.get_active_universe()` (`:1255`).
6. `## MARKET DATA` header (`:1269`).
7. Per-coin market data lines (price, RSI, MACD, ADX, 24h%, [POS] tag) (`:1336-1360`).
8. `## SESSION: <session.upper()>...` if X-RAY session context (`:1446`).
9. `## X-RAY STRUCTURAL SETUPS (ranked by confluence)` — uses `structure_cache.get_top_setups(n=8)` (`:1507`).
10. `## SENTIMENT` + `Fear & Greed: <value>` (`:1520-1524`).
11. `## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)` (`:1530-1542`).
12. `HELD SYMBOLS (already have positions)...` (`:1561`).
13. `## STRATEGY HINTS (automated signals)` (`:1575-1577`) + per-strategy hint lines (`:1582`) + `CONSENSUS PER COIN` (`:1589-1594`).
14. `## ACCOUNT` + `Equity:` + `Available:` (`:1602-1608`).
15. Tiered-capital limits (`:1627`).
16. `## TODAY'S PERFORMANCE` + `Daily PnL` + `Trades today` (`:1632-1637`).
17. EVENT BUFFER (watchdog events from `event_buffer`) (`:1647-1680`).
18. `[URGENT WATCHDOG ALERTS (injected): N concerns]` (`:1683`).
19. Trim notice if size gate triggered (`:1750-1754`).

**System prompt:** `TRADE_SYSTEM_PROMPT` (`:65-147`) — defines target 3-6 trades/cycle, 8 max, JSON-only response shape `{"new_trades": [...], "market_view": "...", ...}`. If `_has_urgent_concerns`, an OVERRIDE addendum is appended instructing Claude to also include `position_actions`.

### CALL B (`_build_position_prompt`, `strategist.py:1770-1934`) — manage open positions

1. `## MARKET REGIME: <cached>` (`:1780`).
2. `## SENTIMENT: Fear & Greed = <cached>` (`:1783`).
3. `## TODAY: PnL=...` (`:1789`).
4. `## YOUR OPEN POSITIONS — Review each...` (`:1794`).
5. Per-position details: symbol, side, entry/current price, PnL%, leverage, regime, thesis, age (`:1819-1900`).
6. URGENT QUEUE injection (`:1908-1927`).

**System prompt:** `POSITION_SYSTEM_PROMPT` (`:149-168`) — JSON-only `{"position_actions": {SYMBOL: {action, new_sl, exit_price, reasoning}}}`. Actions: `hold|tighten_stop|set_exit|close`. **Position-age rules:** "Positions UNDER 5 minutes old: ALWAYS choose 'hold'" (`prompts/position_review.py:3-27`).

## 6.3 Each prompt section's data source

| Section | Source service | Method |
|---|---|---|
| COACHING | enforcer | `get_coaching_text()` |
| REGIME | `regime_detector` | `get_last_regime()` / `detect()` |
| DIRECTION PERF | (internal) | `_build_direction_performance()` |
| MARKET DATA | `market` + `ta_cache` | `get_ticker()`, `get_all_linear_tickers()`, `analyze()` |
| X-RAY | `structure_cache` | `get_ranked_setups()`, `get_top_setups(n=8)` |
| SENTIMENT (F&G) | `fear_greed` | `get_latest()` |
| HELD SYMBOLS | `position_service` | `get_positions()` |
| STRATEGY HINTS | `layer_manager` | `_strategy_hints`, `_strategy_consensus` |
| ACCOUNT | `account_service` | `get_wallet_balance()` |
| TIERED CAPITAL | `tiered_capital` | `get_limits()` |
| DAILY PnL | `pnl_manager` | `current_pnl_pct`, `_trades_today` |
| EVENT BUFFER | `event_buffer` | `get_prompt_text()` |
| URGENT QUEUE | `urgent_queue` | `drain_concerns()`, `format_for_prompt()` |

## 6.4 Claude subprocess invocation

- **File:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/claude_code_client.py`
- **Class:** `ClaudeCodeClient` (`:68`).
- **Subprocess command** (`:847-867`):

```python
cmd = [self._claude_path, "-p", "--output-format", "text"]
if system_prompt:
    cmd += ["--system-prompt", system_prompt]
proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=False, cwd=_PROJECT, env=self._env,
    preexec_fn=os.setsid,
)
```

- **Binary:** `_claude_path` resolves to `/usr/bin/claude` → `/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js` (per docstring `:7`).
- **Timeout:** `timeout_seconds: int = 90` default (`:83`); configurable via `[brain] claude_cli_timeout_seconds = 300` (config.toml — but the in-call default still 90 unless overridden by caller).
- **Stall log interval:** `_STALL_LOG_EVERY_S = 60` (`:821`).
- **Input handling:** prompt encoded UTF-8 and sent via `proc.stdin.write` then `close()` (`:878-890`).
- **Output parsing:** `stdout.strip()` returned as raw response (`:911`); structured JSON extraction via `extract_json()` (`:471-512`) using 3 strategies (direct `json.loads`, markdown-fence regex, brace-finding).
- **Auth:** OAuth via `~/.claude/.credentials.json` (subscriptionType "max"); pre-flight refresh via `_ensure_credentials_fresh` (`:566-611`); HTTP token refresh in `_try_token_refresh` (`:612-693`).
- **Cost:** `$0 per call` (the file's docstring header) — uses Max subscription, not API.

## 6.5 Output of Stage 2 (Claude's directives)

- **Format:** JSON object.
  - CALL_A response shape: `{"new_trades": [{symbol, direction, stop_loss_price, take_profit_price, max_hold_minutes, leverage, size_usd, trailing_activation_pct, reasoning}, ...], "market_view": "...", "risk_level": "normal|cautious|aggressive", "max_positions": N, "default_leverage": N, "default_sl_pct": N, "default_tp_pct": N, "default_hold_minutes": N, "trailing_activation_pct": N, "focus_coins": [...], "avoid_coins": [...]}` plus optional `position_actions` when urgent override is in effect.
  - CALL_B response shape: `{"position_actions": {"SYMBOL": {"action": "hold|close|tighten_stop|set_exit", "new_sl": price_or_null, "exit_price": price_or_null, "reasoning": "..."}}}`.
- **Parsing logic:** `_parse_trade_plan` (`strategist.py:2267`) for CALL_A; `_parse_position_plan` (`:2309`) for CALL_B. JSON extraction via `claude_client.extract_json()` (`claude_code_client.py:471-512`) or fallback `json.loads`.
- **Validation:** `decision_parser.py:164-199 validate_decision()` — clamps leverage, ensures SL/TP positive, etc. Symbol existence checked against `scanner.get_active_universe()`.

---

# Section 7 — Layer 3 (Execution Pipeline)

## 7.1 Execution pipeline stages

### Stage 3.1 — APEX (post-Claude trade-parameter optimizer)

- **Files (in `src/apex/`):** `assembler.py` (758 LOC), `gate.py` (459 LOC), `models.py` (435 LOC), `optimizer.py` (664 LOC), `prompts.py` (226 LOC), `qwen_client.py` (248 LOC).
- **Purpose:** Takes Claude's directive and runs DeepSeek (via OpenRouter) to optimize SL/TP/size/leverage/direction.
- **LLM used:** `[apex] model = "deepseek/deepseek-v3.2"` with fallback `"deepseek/deepseek-chat"` (config.toml).
- **Endpoint:** `https://openrouter.ai/api/v1/chat/completions` (`qwen_client.py:64`).
- **Parameters:** `temperature = 0.2`, `max_tokens = 800`, `timeout_seconds = 60` (config.toml).
- **Cost tracking:** `_DS_COST_PER_M_INPUT = 0.30`, `_DS_COST_PER_M_OUTPUT = 0.88` (per million tokens, `qwen_client.py:32-33`).
- **Currently active?** Yes per config (`enabled = true`). Live trade_intelligence rows show `apex_optimized` column populated for recent trades.

### Stage 3.2 — Performance Enforcer

- **File:** `src/strategies/performance_enforcer.py:31` — `class PerformanceEnforcer`.
- **Levels (`:5-11`):**
  - Level 0 NORMAL: `pnl ≥ 0%` → trade freely
  - Level 1 CAPITAL_PRESERVATION: `pnl < -2%` → max 3 positions, max 3x leverage
  - Level 2 SURVIVAL: `pnl < -5%` → max 1 position, max 2x, BTC/ETH only
- **Sizing reductions (`get_size_multiplier`, `:126-149`):** 1.0x → 0.75x → 0.50x → 0.25x mapped to PnL bands.
- **Live state:** ENFORCER_STATE log at 21:31:59 shows `trades=16 wins=2 losses=11 wr=0.12 strk=-9 pnl=-2.79% el=1 sz_mult=0.50 trigger=pnl_caution`.

### Stage 3.3 — Gate / TradeGate (14 checks)

- **File:** `src/apex/gate.py:29` — `class TradeGate`.
- **NEVER blocks**, only adjusts (`:7-16`).
- **The 14 checks** (each named with line):
  - Check 0 (`:65-92`): hard ceiling 1.5× Claude's pre-APEX directive size.
  - Check 1 (`:94-99`): max position size USD.
  - Check 2 (`:101-106`): max leverage.
  - Check 3 (`:108-121`): max concurrent positions (5 default; reduce to 30% if at max).
  - Check 4 (`:123-160`): capital availability (conviction-weighted, scaled by signal score and weight).
  - Check 5 (`:162-172`): duplicate-symbol position → halve size.
  - Check 6 (`:174-186`): recent cooldown — reduce on fast re-entries.
  - Check 7 (`:188-193`): minimum position size floor $50.
  - Check 8 (`:221-237`): TP floor — APEX TP cannot cross Claude's TP direction.
  - Check 9 (`:239-256`): trail activation floor (50% of TP distance default).
  - Check 10 (`:257-268`): trail distance floor (40% default).
  - Check 11 (`:270-277`): mode override — `trail_only` → `trail_with_ceiling`.
  - Check 12 (`:279-291`): confidence-based size scaling (floor 0.50).
  - Check 13 (`:295-311`): R:R ratio sanity — scale-down on rr<0.5.
  - Check 14 (`:313-327`): TP/SL sanity — adjust if differ <0.1%.

### Stage 3.4 — Order Execution

- **File:** `src/trading/services/order_service.py` — `class OrderService` (`:62`).
- **Main method:** `place_order` (`:86`).
- **Idempotency:** `order_link_id` is generated by `_new_order_link_id()` (`:129`) — UUID-based, format `ti-<24-hex>`. Idempotent retry handled by `_place_order_with_idempotent_retry` (`:235`). Detects dedup via `ORDER_DEDUPED` log (`:354-355`).
- **ORDER_START emission:** `:133-135` — `f"ORDER_START | link_id={order_link_id} sym={symbol} side={side.value} type={order_type.value} qty={qty} lev={leverage} sl={stop_loss} tp={take_profit}"`.
- **ORDER_OK / ORDER_FAIL / ORDER_RETRY / ORDER_DEDUPED / ORDER_RECOVERED** — all in `order_service.py` (lines 260, 369, 382, 354, 418/441 respectively).
- **Live log:** ORDER_START emitted at 18:02-18:03 for BSBUSDT/ETHUSDT/BTCUSDT; no ORDER_RESULT line in workers.log (the success line is `ORDER_OK`, not `ORDER_RESULT`).

## 7.2 Layer 3 toggle mechanism

- **File:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/layer_state.json`
- **Current contents (verbatim):**

```json
{"layer_active":{"1":true,"2":false,"3":false},
 "user_stopped":true,"timestamp":"2026-04-26T18:10:21.315319+00:00"}
```

- **Code that reads it:**
  - `src/core/layer_manager.py:25` — `_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "layer_state.json"`.
  - `src/workers/layer_manager.py:25` — same constant (duplicate file).
  - `_load_persisted_state()` at `:82-91`.
- **Code that respects it ("Gate: only execute if Layer 3 active"):**
  - `core/layer_manager.py:317` — gate before executing new trades background task.
  - `core/layer_manager.py:351` — gate before executing position actions from CALL A.
  - `core/layer_manager.py:411` — gate before executing position actions from CALL B.
  - Logs when layer 3 inactive: `"Layer 3 inactive — skipped {t} new trades"` (`:341-343`), `"Layer 3 inactive — skipped {a} urgent position actions"` (`:354-356`), `"Layer 3 inactive — skipped {a} position actions"` (`:414-418`).

## 7.3 Order placement chain

- **Function:** `OrderService.place_order` (`:86`).
- **Where ORDER_START is logged:** `:133-135`.
- **Where ORDER_OK is logged:** `:260-262` (success).
- **Where ORDER_FAIL is logged:** `:369-370`.
- **Idempotency:** `order_link_id` UUID; retry-on-failure with idempotent semantics.
- **Retry logic:** `_place_order_with_idempotent_retry` (`:235`); detects `ORDER_DEDUPED` on Bybit "duplicate orderLinkId" responses; recovers via Bybit history lookup (`ORDER_RECOVERED` at `:418/441`).

## 7.4 Real vs paper order routing

- **Choice point:** `src/trading/client.py:70-76` — safety assertion comment "Allow mainnet data in 'shadow' mode — Transformer routes orders to Shadow (paper). Only block if mode is explicitly 'paper'".
- **Driver:** `settings.general.mode` (`shadow` | `live` | `paper`) plus `settings.bybit.testnet` (boolean).
- **Current:** `[general] mode = "shadow"` (`config.toml:2`), `[bybit] testnet = false`. Means: live mainnet *data*, but orders routed to Shadow (paper) via the `Transformer` service.
- **Real money path:** `Transformer` switches mode via `transformer_state` table; when `current_mode = "live"`, OrderService talks to Bybit directly. Schema confirmed in `trading.db` (`transformer_state(id, current_mode, last_switched_at, is_switching, switching_to, updated_at)`; `current_mode TEXT NOT NULL DEFAULT 'shadow'`).
- **Paper money path:** `src.api.shadow_client` calls `http://127.0.0.1:9090` (Shadow's `[api]` endpoint).

---

# Section 8 — Layer 4 (Position Monitoring)

## 8.1 ProfitSniper (Mode 4)

- **File:** `src/workers/profit_sniper.py:75` — `class ProfitSniper(BaseWorker)`.
- **Tick interval:** `settings.mode4.check_interval_seconds = 5` seconds (`:124`, `config.toml [mode4] check_interval_seconds = 5`).
- **Five mathematical models** (per docstring `:1-25`):
  1. Hurst Exponent (trend persistence / mean-reversion).
  2. Momentum Decay (multi-scale PnL deceleration).
  3. ATR Extension.
  4. Volume Divergence (Wyckoff/OBV).
  5. Risk/Reward Shift (forward EV).
- **Regime trail factors** (`:48`): trending=1.3, ranging=0.7, volatile=1.0, dead=0.6, balanced=0.85.
- **Action thresholds by regime** (`:57`):
  - trending: tighten 50, partial 70, full 85.
  - ranging: 35/55/70.
  - volatile: 40/60/75.
  - dead: 30/50/65.
  - balanced: 35/55/70.
- **PROFIT GATE** (`:1557-1579`): only acts when `current_pnl > 0` and `> min_profit_for_action` (default 0.10%).
- **P9_CLOSE_GATE** (`:1612-1622`): downgrade `full_close` to `tighten` when `pnl < min_profit_for_close (0.50)` to protect tiny profits without choking TP runs.
- **Anti-greed pullback backstop** (`:1624-1639`):
  - 40% pullback → tighten.
  - 60% pullback → partial_close.
  - 75% pullback → full_close.
- **Escalation pattern (`M4_ACT_PARTIAL`, `:1739-1812`):** on partial fail, fallback to `tighten_agg` (Phase 4B). Live log shows 3× `M4_ACT_PARTIAL` for INJUSDT at 17:48-17:49 (`pct=50% src=stall_escape greed=none score=31-38 pnl=-0.04% to -0.15%`).
- **`M4_ACT_CLOSE` action (`:1814-1832`):** logged with score, peak, pullback, source, greed rule.
- **Cooldown durations** (config.toml [mode4]): extreme 300s, strong 180s, medium 120s; tighten cooldown 30s; partial close cooldown 120s.

## 8.2 Position Watchdog

- **File:** `src/workers/position_watchdog.py:75` — `class PositionWatchdog(BaseWorker)`.
- **Tick interval:** `settings.watchdog.check_interval_seconds = 10` (`:123`, `config.toml [watchdog]`).
- **Three modes** (`:259`, default `passive`):
  - **PASSIVE**: observe; queue concerns to `urgent_queue`; Claude is boss.
  - **SAFETY_NET**: triggered if Claude offline >10 min, ≥3 CLI failures, OR ≥5 consecutive losses (`:323-349`). Acts on hard stop -3%, timer close, trailing exit. No new trades.
  - **EMERGENCY**: triggered if `_session_pnl_pct < -5.0` OR `_hard_stops_this_hour ≥ 3` (`:309-313`). Closes ALL positions, halts trading.
- **Startup grace period:** first 600s (10 min) only escalates for CLI crashes (`:315-321`).
- **Live mode:** all WD_TICK lines for the last 30 min read `mode=safety_net n=0 syms=[none]` — meaning watchdog escalated to SAFETY_NET (likely due to no Claude calls since Layer 2 stopped at 18:10), with 0 open positions.
- **SL/TP enforcement:** `Time-Decay SL Calculator` integration (`:163-233`).

## 8.3 Recovery Planner

- **File:** `src/fund_manager/recovery_planner.py:31` — `class RecoveryPlanner`.
- **Trigger:** `state.total_equity < state.starting_balance` (`:78-86`).
- **Deficit formula:** `self._deficit = state.starting_balance - state.total_equity`.
- **Daily target:** `target_daily = self._deficit / max(1, RECOVERY_TARGET_DAYS - self._days_in_recovery)` where `RECOVERY_TARGET_DAYS = 30` (`:97-103,28`).
- **Recovery params** (`:14-27`):
  - `RECOVERY_MAX_TRADE_PCT = 3.0` (% of trading capital).
  - `RECOVERY_MAX_SL_PCT = 1.5`.
  - `RECOVERY_TARGET_TP_PCT = 2.0`.
  - `RECOVERY_ALLOWED_STRATEGIES = ["rsi_oversold", "support_bounce", "trend_following", "mean_reversion"]`.
- **Effect on new entries:** restricts size, SL, TP, and strategy selection while active.
- **Live state:** Shadow wallet shows `starting_balance=$10,000 total_realized_pnl=-$2,265.71 total_equity≈$6,287.90` — recovery planner SHOULD be active (deficit ≈ $3,712).

## 8.4 Mode 4 sniper escalation pattern

- **The 4× partial pattern:** `_stall_escape_action` (`profit_sniper.py:2143`) — after `stall_escape_partial_after_ticks = 20` ticks (~100s at 5s cadence), first partial; after `stall_escape_full_after_ticks = 40` ticks (~200s), full close. `stall_tighten_max_applications = 3` (`config.toml [mode4]`).
- **Time windows:** see thresholds above.
- **Close conditions:** score-based action OR anti-greed pullback OR stall-escape OR claude consultation result.

## 8.5 Urgent Queue interaction

- **File:** `src/core/urgent_queue.py`.
- **Class:** `UrgentQueue` (`:35-101`); dataclass `WatchdogConcern` (`:18-32`).
- **Watchdog → UrgentQueue push:** `position_watchdog.py:1632-1661`. Concern includes `pnl_pct, sl_proximity_pct, position_age_minutes, urgency` (CRITICAL if `pnl < -2.5%` or sl_prox > 80%).
- **Strategist drain:** `strategist.py` reads via `urgent_queue.drain_concerns()` and formats into `format_for_prompt(concerns)`; `MAX_CONCERNS = 10`, `MAX_AGE_SECONDS = 600`, `COOLDOWN_SECONDS = 150`, `MAX_FORMAT_CHARS = 1500`.

---

# Section 9 — Layer 5 (TIAS Post-Trade Intelligence)

## 9.1 TIAS components

- **Files in `src/tias/`:**
  - `analyzer.py` (8206 bytes) — `TradeAnalyzer` class (`:34`); orchestrates DeepSeek call.
  - `backfill.py` (8557 bytes) — historical record analysis.
  - `collector.py` (24495 bytes) — `TradeContextCollector` (`:30`).
  - `deepseek_client.py` (9187 bytes) — `DeepSeekClient` (`:58`); raw OpenRouter HTTP.
  - `models.py` (5130 bytes) — `TradeIntelligence` dataclass.
  - `prompts.py` (7553 bytes) — `TIAS_SYSTEM_PROMPT`, `build_user_prompt`.
  - `repository.py` (19843 bytes) — `TradeIntelligenceRepo` (`:46` INSERT, `:92` UPDATE, `:131` UPDATE).
- **Trigger:** Trade close callback wrapper in `WorkerManager._tias_analyze_background` (workers/manager.py:1429). Watch the live log: `TIAS_SAVE | id=725 sym=INJUSDT dir=Buy pnl=-0.11% win=False ...` at 18:09:10.140 followed by `TIAS_ANALYZED | id=725 sym=INJUSDT cat=ENTRY_TOO_EARLY conf=0.8 cost=$0.000545 ms=2133` at 18:09:27.932.
- **Cadence:** on-demand per closed trade.

## 9.2 TIAS data flow

- **Trade closes →** TradeCoordinator close callback wrapper → captures `m4_snapshot` synchronously → schedules `TradeContextCollector.collect_and_save(record, repo, m4_snapshot)` (`collector.py:46`).
- **Data gathered (six groups):**
  - **Group A (`_extract_group_a`, :136):** outcome — symbol, direction, strategy, source, prices, PnL.
  - **Group B (`_collect_group_b`, :157):** entry context from `trade_thesis` (`leverage`, `size_usd`, `claude_thesis`, `claude_signal`) + `strategy_trades` (entry_score, ensemble_votes).
  - **Group C:** market conditions at close (regime, F&G).
  - **Group D:** technical indicators at close (RSI, MACD hist/signal, BB pct, EMA20/50, stoch k/d, ADX, ATR, ATR%, vol ratio, price vs vwap).
  - **Group E:** Mode 4 profit-tracking data (`m4_peak_pnl_pct`, `m4_ticks_in_profit`, `m4_composite_score`, `m4_hurst_value`, etc.).
  - **APEX group:** `apex_optimized`, `apex_flipped`, original/final SL/TP/size, model, response_ms, cost_usd, gate_adjustments.
  - **Group F (filled in Phase 2 by analyzer):** `ds_why`, `ds_what_worked`, `ds_what_failed`, `ds_lessons`, `ds_category`, `ds_confidence`, `ds_correct_direction`, `ds_optimal_*`.
- **DeepSeek call:** `TradeAnalyzer._call_with_fallback` (`analyzer.py:77`):
  - Primary model `[tias] primary_model = "deepseek/deepseek-chat-v3-0324"`.
  - Fallback model `"deepseek/deepseek-chat"`.
  - Temperature `0.3`, max_tokens `1500`, timeout `45s` (config.toml).
  - Endpoint: `https://openrouter.ai/api/v1/chat/completions` (`deepseek_client.py:75`).
  - JSON response shape: `{why, category, correct_direction, what_should_have_done, how_to_exploit_next_time, optimal_sl_pct, optimal_tp_pct, optimal_size_usd, optimal_leverage, confidence}`.
- **Cost pricing:** `_COST_PER_M_INPUT = 0.27`, `_COST_PER_M_OUTPUT = 1.10` (per million tokens; `analyzer.py:30-31`).
- **Output stored:** `trade_intelligence` table (Phase 1: groups A-E + APEX inserted by `repo.save()`; Phase 2: ds_* columns updated by `repo.update_analysis()` after DeepSeek returns).

## 9.3 trade_intelligence table

- **Schema:** ~70 columns (full schema in Section 10). Key columns: `id`, `symbol`, `direction`, `strategy_name`, `pnl_pct`, `pnl_usd`, `win`, `hold_seconds`, `regime`, `rsi`, `atr_pct`, `m4_*` (Mode 4 telemetry), `apex_*`, `ds_*` (DeepSeek analysis).
- **Row count:** `725` total (sqlite query `SELECT COUNT(*) FROM trade_intelligence`).
- **All 725 rows have `ds_why IS NOT NULL`** (i.e., all analyzed).
- **Wins/losses:** `win=0: 342 rows; win=1: 383 rows`.
- **Distinct symbols:** 113.
- **Top DeepSeek categories:**
  ```
  CORRECT_TRADE_BAD_LUCK  173
  CORRECT_ENTRY           145
  REGIME_MISMATCH         111
  ENTRY_TOO_EARLY         106
  CORRECT_EXIT             43
  EXIT_TOO_EARLY           31
  MOMENTUM_FADE            31
  ENTRY_TOO_LATE           27
  INDICATOR_CONFLICT       27
  STOP_TOO_TIGHT           25
  ```

## 9.4 Feedback into next prompt

- **Where TIAS data is read:** `repository.py` exposes `get_recent_lessons`, `get_top_categories`, etc. (called from `TradeAnalyzer` consumers).
- **Section in Claude prompt:** the COACHING block at the top of CALL_A (`strategist.py:1162`) — built by `enforcer.get_coaching_text(structure_cache=_sc)` (`performance_enforcer.py:428-510`). It interpolates X-RAY top picks AND recent TIAS lessons.

---

# Section 10 — Storage Layer

## 10.1 trading.db

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db`
- **Size:** 154,083,328 bytes (≈147 MB) plus WAL 104 MB and SHM 320 KB.
- **Schema version:** 24 (latest from `schema_version` table; rows: 3,4,8,9,10,11,12,13,14,15,16,20,21,22,23,24).
- **Total tables:** 51.
- **Complete table list:**

```
account_snapshots         daily_summary             pattern_log
active_strategies         discovered_patterns       portfolio_allocations
active_universe           ensemble_votes            position_snapshots
aggregated_sentiment      event_log                 positions
backtest_results          fear_greed_index          price_alerts
backtest_trades           fund_manager_log          profit_ratchet_log
brain_decisions           fund_manager_state        rebalance_history
capital_level_history     funding_rates             reddit_posts
claude_decisions          generated_strategies      regime_history
coin_regime_history       hourly_performance        risk_budget_log
conversation_log          klines                    scheduled_reports
correlation_matrix        market_snapshots          schema_version
daily_pnl                 news_articles             session_log
daily_summary             open_interest             signal_accuracy
generated_strategies      orderbook_snapshots       signals
                          orders                    sniper_log
                          pattern_occurrences       strategy_code_history
                          performance_attribution   strategy_lifecycle
                          ticker_cache              strategy_params
                          trade_history             strategy_performance
                          trade_intelligence        strategy_trades
                          trade_journal             stress_test_results
                          trade_log                 switch_history
                          trade_thesis              transformer_state
                          trial_performance         user_preferences
                          watchlists
```

- **Major table row counts (live, 2026-04-26 21:32 UTC):**

| Table | Row count |
|---|---|
| klines | 95,986 |
| signals | 158,272 |
| trade_thesis | 1,152 (open=0) |
| trade_intelligence | 725 |
| active_universe | 30 |
| funding_rates | 76,447 |
| open_interest | 75,777 |
| coin_regime_history | 15,561 |
| news_articles | 1,203 |
| aggregated_sentiment | 289,986 |
| regime_history | 1,896 |
| active_strategies | 0 |
| brain_decisions | 0 |
| discovered_patterns | 24 |
| generated_strategies | 0 |
| trade_history | 0 |
| positions | 0 |
| orders | 0 |
| ticker_cache | 200 |

- **Per-symbol kline counts (top 5):** NEARUSDT 960, GALAUSDT 927, ENAUSDT 925, ETHUSDT 924, INJUSDT 924.

## 10.2 shadow.db

- **Path:** `/home/inshadaliqbal786/shadow/data/shadow.db`
- **Size:** 877,690,880 bytes (≈837 MB) plus WAL 4.4 MB and SHM 32 KB.
- **Schema version:** 3.
- **Tables:** klines, ticker_snapshots, funding_rates, open_interest_history, tracked_coins, virtual_wallet, virtual_positions, trade_history, wallet_snapshots, daily_summary, schema_version, shadow_settings, sqlite_stat1.
- **Indexes:** idx_klines_timestamp, idx_ticker_timestamp, idx_oi_timestamp, idx_positions_status, idx_positions_symbol_status, idx_trades_symbol, idx_trades_closed_at, idx_trades_result, idx_wallet_snap_timestamp.
- **Major table row counts:**

| Table | Row count |
|---|---|
| klines | 4,082,436 |
| ticker_snapshots | 1,934,626 |
| funding_rates | 4,253 |
| open_interest_history | 394,131 |
| tracked_coins | 312 |
| virtual_wallet | 1 |
| virtual_positions | 1,091 (closed, all archived) |
| trade_history | 1,197 |
| wallet_snapshots | 19,723 |
| daily_summary | 26 |

- **Wallet state row:** `id=1 starting_balance=10000.0 total_realized_pnl=-2265.71 total_fees_paid=1446.39 total_trades=1085 total_wins=430 total_losses=655 created_at=2026-03-26 last_updated=2026-04-26 18:08:59`.

## 10.3 Other databases

- `/home/inshadaliqbal786/trading-intelligence-mcp/trading.db` — empty/legacy stub at the project root (4 KB).
- `/home/inshadaliqbal786/trading-intelligence-mcp/backups/prefetch_fix_20260421_213546/trading_pre_cleanup.db` — backup snapshot.
- `/home/inshadaliqbal786/trading-intelligence-mcp/data/trading_testnet_backup_20260326.db` — testnet snapshot from 2026-03-26.

## 10.4 config.toml

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` (31,959 bytes, last modified 2026-04-26 20:52).
- **Top-level sections (in file order):**

```
[general] [bybit] [finnhub] [reddit] [altdata] [database] [workers]
[workers.sweet_spots] [workers.sweet_spots.altdata] [brain] [risk]
[alerts] [mcp] [watchdog] [mcp_pool] [price] [sl_gateway]
[sl_gateway.min_distance_class_ceiling] [scanner] [scanner.scoring_weights]
[universe] [regime] [strategy_engine] [pnl_targets] [leverage] [optimizer]
[factory] [backtesting] [trial] [portfolio] [telegram_interactive]
[fund_manager] [enforcer] [mode4] [tias] [apex]
[apex.tp_cap_multiplier_by_class] [sentinel] [analysis.structure]
[analysis.volatility_profile] [time_decay]
[time_decay.grace_seconds_by_class] [time_decay.atr_room_multiplier_by_class]
```

- **Critical values:**
  - `[general] mode = "shadow"`, `shadow_api_url = "http://127.0.0.1:9090"`, `log_dir = "data/logs"`.
  - `[bybit] testnet = false`, `rate_limit_per_second = 10`.
  - `[universe] watch_list` = 50 coins (12 Tier A + 23 Tier B + 15 Tier C, listed in 10.4.1).
  - `[scanner] max_coins = 30`, `max_spread_pct = 0.15`.
  - `[scanner.scoring_weights] structure=0.30, strategy=0.30, signal=0.15, regime=0.15, funding=0.10`.
  - `[workers.sweet_spots]` window=5min, kline=0:30, structure=0:45, signal=1:00, regime=1:15, strategy=1:30, scanner=4:00; altdata.funding=1:45.
  - `[brain] strategic_interval=150, watchdog_interval=30, model="claude-sonnet-4-20250514", max_tokens=4096, claude_cli_timeout_seconds=300`.
  - `[risk] max_leverage=5, mandatory_stop_loss=true, default_stop_loss_pct=3.0, default_take_profit_pct=6.0, max_position_size_pct=20.0, max_open_positions=10, daily_loss_limit_pct=10.0, max_drawdown_pct=25.0`.
  - `[mode4] enabled=true, check_interval_seconds=5, base_atr_multiplier=2.5, partial_close_pct=50, score_consult_claude=50, score_auto_partial=70, score_auto_full=85`.
  - `[apex] enabled=true, model="deepseek/deepseek-v3.2", max_position_size_usd=1200, max_leverage=5`.
  - `[tias] enabled=true, primary_model="deepseek/deepseek-chat-v3-0324"`.
  - `[analysis.structure] cache_ttl_seconds=300, batch_size=25, shadow_db_path="../shadow/data/shadow.db"`.

### 10.4.1 The 50-coin watch_list

```
Tier A (12): BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT ADAUSDT DOGEUSDT
             AVAXUSDT LINKUSDT ARBUSDT NEARUSDT ATOMUSDT
Tier B (23): INJUSDT RENDERUSDT ONDOUSDT ENAUSDT PYTHUSDT SEIUSDT AEROUSDT
             RUNEUSDT GALAUSDT MANAUSDT SANDUSDT AXSUSDT LDOUSDT CRVUSDT
             DYDXUSDT AAVEUSDT ICPUSDT IMXUSDT HBARUSDT HYPEUSDT GMTUSDT
             FILUSDT MNTUSDT
Tier C (15): MONUSDT SKRUSDT PLUMEUSDT EGLDUSDT ALGOUSDT BSBUSDT KATUSDT
             HYPERUSDT ORCAUSDT BLURUSDT OPUSDT APTUSDT LTCUSDT BCHUSDT
             ALICEUSDT
```

(AEROUSDT explicitly noted as "substituted for delisted FETUSDT" in config comment.)

## 10.5 .env keys (REDACTED VALUES)

`/home/inshadaliqbal786/trading-intelligence-mcp/.env`:
```
BYBIT_API_KEY, BYBIT_API_SECRET, FINNHUB_API_KEY,
REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
ANTHROPIC_API_KEY, MCP_AUTH_TOKEN, OPENROUTER_API_KEY
```

`/home/inshadaliqbal786/shadow/.env`:
```
SHADOW_TELEGRAM_BOT_TOKEN
```

## 10.6 layer_state.json

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/data/layer_state.json`
- **Content (verbatim):**

```json
{"layer_active":{"1":true,"2":false,"3":false},
 "user_stopped":true,"timestamp":"2026-04-26T18:10:21.315319+00:00"}
```

---

# Section 11 — Wiring (Dependencies and Connections)

## 11.1 Data wiring table

| Data structure | Producers (file:line) | Consumers (file:line) | Storage |
|---|---|---|---|
| klines (table) | `database/repositories/market_repo.py:43` `save_klines` (called by `trading/services/market_service.py:210` and `workers/kline_worker.py:181`) | `analysis/structure/structure_engine.py` (via `MarketRepository.get_klines`), `strategy_worker.py` prefetch, `regime_worker.py`, `cleanup_worker.py` | trading.db.klines |
| Shadow klines | `shadow/src/collector/kline_collector.py` | `analysis/structure/shadow_kline_reader.py:108` (read-only fallback for X-RAY) | shadow.db.klines |
| signals (table) | `intelligence/signals/signal_generator.py:172` `SIG_GEN` | (analytics queries) | trading.db.signals |
| active_universe (in-memory) | `strategies/scanner.py` `_update_universe`; `workers/scanner_worker.py:297` `set_active_universe()` | 11+ readers via `await scanner.get_active_universe()` (strategy_worker, strategist, structure_worker, regime_worker, etc.) | RAM |
| active_universe (table) | `workers/scanner_worker.py:264` (DELETE) and `:279` (INSERT OR REPLACE) | telegram dashboard handlers, MCP tools | trading.db.active_universe |
| StructureCache | `workers/structure_worker.py:121,143` (`set` + `set_ranked_setups`) | scorer, strategist, performance_enforcer, apex/gate, apex/assembler, telegram analysis | RAM (TTL=300s) |
| TACache | `analysis/ta_cache.py` | strategist, volatility_profiler, apex_assembler, profit_sniper (`ta_cache.py:129`) | RAM |
| `_score_cache` (Stage 1 strategy) | `workers/strategy_worker.py:512` | `workers/scanner_worker.py:74-81` `get_strategy_score()` | RAM |
| `_signal_cache` (Stage 1 signal) | `workers/signal_worker.py:108` | `workers/scanner_worker.py:83-93` | RAM |
| `_funding_cache` | `workers/altdata_worker.py:163-170` | `workers/scanner_worker.py:123-139` | RAM |
| Per-coin regime | `workers/regime_worker.py:179` (`detector._per_coin_regimes.update`) | `workers/scanner_worker.py:103-121` `_get_regime_alignment()` | RAM + `trading.db.coin_regime_history` (line 211 INSERT) |
| Strategy hints (top 20) | `workers/strategy_worker.py:596` `layer_manager._strategy_hints` | `brain/strategist.py:1582-1594` | RAM |
| Stage 2 prompt | `brain/strategist.py:1141` `_build_trade_prompt`, `:1770` `_build_position_prompt` | passed to `claude_code_client.send_message` | transient string |
| Claude directives | `brain/strategist.py:362,446` `claude.send_message` | parsed by `decision_parser` then handed to `core/layer_manager._execute_*` | RAM (logged in `claude_decisions` table) |
| trade_thesis (table) | `core/thesis_manager.py:47` INSERT, `:109` UPDATE | TIAS collector (`tias/collector.py:78,176` SELECT for entry context); Claude prompt builders | trading.db.trade_thesis |
| trade_intelligence (table) | `tias/repository.py:46` INSERT (Phase 1), `:92,131` UPDATE (Phase 2 ds_* fill) | strategist coaching block via TIAS lessons | trading.db.trade_intelligence |
| `sniper_log` (table) | `workers/profit_sniper.py:1860` `_write_sniper_log` | analytics, `M4_EVAL` reports | trading.db.sniper_log |
| `claude_decisions` (table) | layer_manager / strategist | analytics | trading.db.claude_decisions |
| `regime_history` (table) | `workers/regime_worker.py:130` INSERT | analytics, telegram dashboard | trading.db.regime_history |
| `coin_regime_history` (table) | `workers/regime_worker.py:211` INSERT | restored at `regime_worker.py:84-95` on first tick | trading.db.coin_regime_history |
| `funding_rates` (table) | `intelligence/altdata/funding_rates.py` | scoring | trading.db.funding_rates |
| `open_interest` (table) | `intelligence/altdata/open_interest.py` | scoring | trading.db.open_interest |
| `news_articles` (table) | `workers/news_worker.py:50` | sentiment aggregator, signal_generator | trading.db.news_articles |
| `aggregated_sentiment` (table) | `intelligence/sentiment/aggregator.py` | signal_generator | trading.db.aggregated_sentiment |
| `event_log` (table) | `core/event_buffer.py` | strategist prompt EVENT BUFFER section | trading.db.event_log |
| `urgent_queue` (in-memory, src/core) | `workers/position_watchdog.py:1632-1661` `add_concern` | `brain/strategist.py` `drain_concerns` | RAM (max 10, 600s TTL) |
| `transformer_state` (table) | telegram /switch handler | OrderService routing decision | trading.db.transformer_state |

## 11.2 External API dependencies

- **Bybit WebSocket (mainnet, public):** `wss://stream.bybit.com/v5/public/linear`
  - Consumed by: `shadow/src/collector/websocket.py` (Shadow's main feed); `src/trading/websocket.py` `BybitWebSocket` (workers' price feed via `PriceWorker`).
- **Bybit REST (mainnet):** `https://api.bybit.com`
  - Called by: `src/trading/client.py` (workers, every order; klines via `MarketService.get_klines`); `src/intelligence/altdata/funding_rates.py`, `open_interest.py`.
- **Finnhub:** `[finnhub] enabled=true rate_limit_per_minute=60`
  - Called by: `src/intelligence/news/news_service.py` (workers via `NewsWorker`).
- **Anthropic (via Claude CLI subprocess):** `/usr/bin/claude`
  - Called by: `src/brain/claude_code_client.py` (workers' `ClaudeStrategist`).
  - The CLI's MCP tools forward through `mcp_stdio_proxy.py` → `http://127.0.0.1:8080/sse` (the workers' `trading-mcp-sse` server).
- **OpenRouter:** `https://openrouter.ai/api/v1/chat/completions`
  - Called by: `src/tias/deepseek_client.py` (TIAS); `src/apex/qwen_client.py` (APEX); `src/sentinel/advisor.py` (Sentinel advisor — `[sentinel] advisor_model = "deepseek/deepseek-chat-v3-0324"`).
- **CoinGecko:** rate-limited 10/min
  - Called by: `src/intelligence/altdata/onchain.py` (workers via `AltDataWorker`).
- **alternative.me Fear & Greed:** no auth required
  - Called by: `src/intelligence/altdata/fear_greed.py`.
- **Reddit:** disabled (`[reddit] enabled = false`).
- **Telegram (workers token):** for trade alerts, dashboard.
- **Telegram (Shadow token):** for trade open/close paper alerts (`shadow/src/telegram/bot.py`).

## 11.3 Inter-process boundaries

- **workers (400) ↔ Shadow (392):** `http://127.0.0.1:9090` HTTP REST. Methods: `GET /wallet`, `POST /orders`, `GET /positions`, `POST /positions/{id}/modify`, `POST /positions/{id}/close`, `GET /trade_history`, `GET /klines/<symbol>`, etc. (Shadow's `src/api/shadow_client.py` defines the route table.)
- **workers (400) ↔ MCP server (401):** No direct API call from workers. The MCP server runs independently, is reached only by the Claude CLI subprocess via the stdio proxy. Both processes hold separate DatabaseManager handles to `data/trading.db` (sharing via SQLite WAL).
- **workers (400) ↔ Telegram bot:** workers run their own Telegram client (`src/telegram/bot.py` via `TelegramBotWorker`).
- **shadow ↔ Telegram bot:** Shadow runs its own bot in `shadow/src/telegram/bot.py`.
- **shadow's read-only DB handle in workers:** `src/analysis/structure/shadow_kline_reader.py` opens `../shadow/data/shadow.db` mode=ro (`shadow_kline_reader.py:108` `XRAY_SHADOW_CONN_OPEN`) for X-RAY's H1 kline fallback.

---

# Section 12 — Live Behavior Right Now

Captured between 21:30 and 21:32 UTC on 2026-04-26.

## 12.1 Per-tag activity in last ~30 min

| Tag | Recent log line(s) (verbatim) |
|---|---|
| SCANNER_TICK | (no SCANNER_TICK line in last 30 min — the scanner_worker emits SCANNER_TICK_SUMMARY in the corrected architecture; in current logs the only matching `SCANNER_*` lines are from the legacy `MarketScanner.scan_market` path: `Scanner universe UPDATED v29: 30 coins (added: {'OPUSDT', 'MONUSDT'}, removed: {'FILUSDT', 'ADAUSDT'}, protected: 0)` at 21:32:01.262) |
| XRAY_TICK | `21:24:15 ... batch=0/2 symbols=5 analyzed=5 errors=0 cached=37 setups=12 skips=18 el=14624ms`; `21:28:14 ... batch=1/2 symbols=25 analyzed=25 ... el=178801ms`; `21:30:10 ... batch=0/2 symbols=5 analyzed=5 ... el=56050ms` |
| STRAT_CYCLE_DONE | most recent shows last completion at 18:09:16 (`coins=30 signals=12 scored=12 hints=6 urg=1 el=1246ms`); newer ticks fail to complete because of STRAT_PREFETCH_CRITICAL on 21:31 |
| SIG_BATCH | `21:06:14 SIG_BATCH | n=30 coins=30 strongest=INJUSDT type=neutral conf=0.44 el=712059ms`; `SIG_BATCH_STATS | n=30 conf_min=0.217 conf_max=0.444 conf_mean=0.240 conf_std=0.047` |
| ALTDATA | `21:30:21 ALTDATA | fg=33 funding=30 oi=30 el=150772ms` |
| REGIME_PERCOIN | `21:28:21 REGIME_PERCOIN | detected=29 total_cached=31 universe=29 divergent=12` |
| KLINE_FETCH | `21:27:37 KLINE_FETCH | klines=18991 expected=19000 symbols=30 quality=ok errors=0 el=1032990ms` (1033 s — 17 minutes!) |
| PRICE_UNIVERSE_SYNC | `21:32:01 PRICE_UNIVERSE_SYNC | added=2 removed=2 total=30` |
| ORDER_START | last at `18:03:22 ti-ed7931... sym=BTCUSDT side=Buy type=Market qty=0.007 lev=2 sl=76815 tp=78374` (no orders since 18:03) |
| ORDER_RESULT | `NO MATCHES FOUND` (the success log tag is `ORDER_OK`, not `ORDER_RESULT`) |
| M4_ACT_PARTIAL | last at `17:49:38 sym=INJUSDT pct=50% src=stall_escape greed=none score=31 pnl=-0.04%` |
| TIAS | `18:09:27 TIAS_ANALYZED | id=725 sym=INJUSDT cat=ENTRY_TOO_EARLY conf=0.8 cost=$0.000545 ms=2133` |
| BRAIN_CYCLE | last completion: `18:08:36 BRAIN_CYCLE_A_DONE | el=596437ms trades=2 view='Ranging global market with fear (F&G 33). Per-coin regimes show divergence...'`; nothing since |
| PRICE_WS_HEALTH | `21:31:20 status=connected msgs_per_min=45507 msgs_in_window=34166 window_s=45.0 subscribed=30 quotes_cached=37` |

## 12.2 Currently running processes

```
inshada+ 392  /home/inshadaliqbal786/shadow/.venv/bin/python shadow.py            (RSS 60.3 M, CPU 21m41s since 17:34)
inshada+ 400  /home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python workers.py  (RSS 370.7 M, peak 600 M cap, CPU 2h35m)
inshada+ 401  /home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python server.py --transport sse --port 8080  (RSS 82.4 M, CPU 59 s)
```

Multiple `claude` CLI processes are also running (PIDs 1179, 1193, 1908, 37908) — these are interactive `claude` shells started by the operator (not the MCP-spawned subprocesses), each consuming ~330-540 MB.

## 12.3 Live universe state

`active_universe` table contents (top 30 by opportunity_score):

```
LDOUSDT 106.0    ORCAUSDT 96.0    HYPERUSDT 93.0   KATUSDT 90.0
AEROUSDT 88.0    AXSUSDT 83.0     ETHUSDT 80.0     SKRUSDT 80.0
INJUSDT 78.0     ONDOUSDT 73.0    BSBUSDT 66.0     BTCUSDT 65.0
ENAUSDT 65.0     SEIUSDT 65.0     GALAUSDT 63.0    SOLUSDT 63.0
AAVEUSDT 60.0    CRVUSDT 58.0     ALGOUSDT 55.0    APTUSDT 55.0
ARBUSDT 55.0     HYPEUSDT 55.0    RENDERUSDT 55.0  AVAXUSDT 50.0
BNBUSDT 50.0     DOGEUSDT 50.0    LINKUSDT 50.0    ADAUSDT 48.0
FILUSDT 45.0     HBARUSDT 45.0
```

Top 5: LDOUSDT, ORCAUSDT, HYPERUSDT, KATUSDT, AEROUSDT — matching the live log line `Market scan: 39 coins scored, top 30 selected. Best: LDOUSDT, ORCAUSDT, HYPERUSDT, KATUSDT, AEROUSDT`.

## 12.4 Open positions

`SELECT COUNT(*) FROM trade_thesis WHERE status='open'` → **0**.
`SELECT * FROM virtual_positions WHERE status='open'` (shadow.db) → **(no rows)**.

## 12.5 Recent trades (last 10 from shadow.trade_history)

```
INJUSDT  Buy  3.6630986   3.6609014   -0.115%  2026-04-26T18:08:59
INJUSDT  Buy  3.6731016   3.6678993   -0.197%  2026-04-26T17:49:57
AXSUSDT  Buy  1.39791925  1.3925821   -0.437%  2026-04-26T04:51:59
HYPERUSDT Buy 0.129198748 0.127741666 -1.183%  2026-04-26T04:51:32
AXSUSDT  Buy  1.41582462  1.4025791   -0.991%  2026-04-26T04:22:42
HYPERUSDT Buy 0.132249663 0.131730469 -0.448%  2026-04-26T04:14:25
DYDXUSDT Sell 0.161331586 0.16154845  -0.189%  2026-04-26T02:48:19
ALICEUSDT Buy 0.160438117 0.159762057 -0.476%  2026-04-26T02:22:54
MAGMAUSDT Buy 0.195538644 0.194811539 -0.427%  2026-04-26T01:46:06
BASEDUSDT Sell 0.12966109 0.13063918  -0.809%  2026-04-26T01:31:32
```

(Note: `MAGMAUSDT` and `BASEDUSDT` are not in current watch_list — they are remnants of an earlier universe.)

## 12.6 Process memory state

```
free -h:
              total   used   free   shared  buff/cache  available
Mem:          3.8Gi   2.2Gi  681Mi    1.0Mi      941Mi       1.4Gi
Swap:           0B     0B     0B
```

Per-process:
- `trading-workers.service`: 370.7 MB / max 800 MB / cgroup limit 800 MB / available 229 MB.
- `shadow.service`: 60.3 MB / max 200 MB / available 89.6 MB.
- `trading-mcp-sse.service`: 82.4 MB / max 200 MB / available 67.5 MB.

Worker SYSTEM_HEALTH log at 21:31:51: `loop_lag=0.1ms tasks=28 mem=381MB cpu=71% pid=400`.

## 12.7 Recent enforcer state

`21:31:59 ENFORCER_STATE | trades=16 wins=2 losses=11 wr=0.12 strk=-9 pnl=-2.79% el=1 sz_mult=0.50 trigger=pnl_caution`.

Performance Enforcer is currently at Level 1 (CAPITAL_PRESERVATION), 50% size reduction.

## 12.8 Recent slow-tick warnings

- 21:31:14 `STRAT_PREFETCH_SLOW el=35733ms db=14757ms ta=3315ms h1_db=10424ms h1_ta=4736ms coins=30 slow_coins=[ONDOUSDT=1246ms,SKRUSDT=503ms,INJUSDT=464ms]`
- 21:31:14 `STRAT_PREFETCH_CRITICAL el=35733ms db=14757ms h1_db=10424ms coins=30`
- 21:31:14 `BASE_WORKER_TICK_SLOW name=strategy_worker el=38521ms threshold_ms=10000`
- 21:31:14 `BASE_WORKER_TICK_SLOW name=fund_manager_worker el=10788ms threshold_ms=2000 interval_s=60.0`
- 21:31:15 `BASE_WORKER_TICK_SLOW name=price_alert_worker el=2983ms threshold_ms=2000 interval_s=10.0`
- 21:31:28 `BASE_WORKER_TICK_SLOW name=price_alert_worker el=2912ms`
- 21:31:41 `BASE_WORKER_TICK_SLOW name=price_alert_worker el=3508ms`
- 21:31:59 `BASE_WORKER_TICK_SLOW name=enforcer_worker el=2031ms`

These slow-ticks are consistent with the `D-3` SQLite lock-contention bottleneck flagged in MEMORY.md (`project_shadowklinereader_fix.md`).

## 12.9 Watchdog state

`21:31:12 WD_TICK | mode=safety_net n=0 syms=[none]` repeating every 10 s. Watchdog is in SAFETY_NET because Brain (Layer 2) is stopped (`layer_active.2 = false`), so Claude's `_last_call_attempt_time` is older than 10 minutes (last BRAIN_CYCLE_A_DONE at 18:08:36 — over 3 hours ago).

---

# Section 13 — Layers Summary Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL APIs                                                                │
│   Bybit WS (mainnet, public)        wss://stream.bybit.com/v5/public/linear  │
│   Bybit REST (mainnet)              https://api.bybit.com                    │
│   Finnhub (news + econ calendar)    rate_limit 60/min                        │
│   OpenRouter (DeepSeek)             https://openrouter.ai/api/v1/chat/...    │
│   Anthropic (via /usr/bin/claude)   $0 cost, OAuth Max subscription          │
│   CoinGecko (on-chain)              rate_limit 10/min                        │
│   alternative.me F&G                                                         │
└──────────┬─────────────────────────────────────┬─────────────────────────────┘
           │                                     │
           ▼                                     ▼
┌──────────────────────────────┐   ┌────────────────────────────────────────────┐
│ Shadow PROCESS (PID 392)     │   │ workers PROCESS (PID 400, RSS 370M, 800M cap)
│ shadow.py (systemd: shadow)  │   │ workers.py (systemd: trading-workers)       │
│   subscribes to 50 coins     │   │                                             │
│   (reads workers' [universe] │   │  ┌── 7 DATA WORKERS (sweet-spot scheduling)
│    watch_list directly)      │   │  │   PriceWorker (45s, WS, watch_list 50)   │
│   writes shadow.db (837 MB)  │   │  │   KlineWorker (sweet 0:30, REST, 50)     │
│   serves HTTP API @ :9090    │   │  │   StructureWorker (sweet 0:45, X-RAY,    │
│   virtual exchange engine    │   │  │       50 batched 25/tick)                │
│   own Telegram bot           │   │  │   SignalWorker (sweet 1:00, sentiment+   │
│                              │   │  │       signals, 50)                       │
│                              │◄──┼──┤   RegimeWorker (sweet 1:15, 50)          │
│  klines fallback (mode=ro)   │   │  │   AltDataWorker (sweet 1:45 funding,     │
│  via shadow_kline_reader.py  │   │  │       OI 5min, F&G 60min)                │
│                              │   │  │   StrategyWorker (sweet 1:30, 40 strats, │
│                              │   │  │       runs internal Layers 1-3)          │
│                              │   │  └─►  populate caches:                      │
│                              │   │       TACache, StructureCache (TTL 300s),  │
│                              │   │       _signal_cache, _funding_cache,       │
│                              │   │       _per_coin_regimes, _score_cache      │
│                              │   │                                             │
│                              │   │  ┌── CYCLE TRIGGER (NOT a data worker)     │
│                              │   │  │   ScannerWorker (sweet 4:00 per 5-min   │
│                              │   │  │       window) — reads 7 caches, scores   │
│                              │   │  │       50 coins, picks top 30 (force-     │
│                              │   │  │       include open positions),           │
│                              │   │  │       writes active_universe table       │
│                              │   │  │       + MarketScanner._active_universe   │
│                              │   │  └────────────────┐                         │
│                              │   │                   │                         │
│                              │   │  ┌── Stage 2 STRATEGIST (ClaudeStrategist) │
│                              │   │  │   create_trade_plan() → CALL_A           │
│                              │   │  │     ~12-14K char prompt, 19 sections    │
│                              │   │  │   create_position_plan() → CALL_B        │
│                              │   │  │     ~5-8K char prompt                    │
│                              │   │  │   sources:                               │
│                              │   │  │     enforcer.coaching, regime, X-RAY    │
│                              │   │  │     ranked setups, F&G, market data,    │
│                              │   │  │     held positions, strategy hints,     │
│                              │   │  │     account, daily PnL, event_buffer,   │
│                              │   │  │     urgent_queue                        │
│                              │   │  │   spawns: /usr/bin/claude -p             │
│                              │   │  │     --output-format text                 │
│                              │   │  │     [--system-prompt "..."]              │
│                              │   │  │   stdin: prompt                          │
│                              │   │  │   stdout: JSON (parsed via              │
│                              │   │  │     decision_parser._extract_json)      │
│                              │   │  │   ⇣ Claude CLI may invoke MCP tools via │
│                              │   │  │     mcp_stdio_proxy.py → SSE @ :8080    │
│                              │   │  └─► StrategicPlan { new_trades,           │
│                              │   │       position_actions, risk_level,        │
│                              │   │       max_positions, focus_coins, ...}     │
│                              │   │                                             │
│                              │   │  ┌── Layer 3 EXECUTION (gated by           │
│                              │   │  │     layer_state.json layer_active[3])   │
│                              │   │  │   APEX TradeOptimizer                    │
│                              │   │  │     model: deepseek/deepseek-v3.2        │
│                              │   │  │     OpenRouter, 60 s timeout,           │
│                              │   │  │     temp 0.2, max_tokens 800            │
│                              │   │  │     IntelligencePackage assembled by    │
│                              │   │  │       IntelligenceAssembler (5 sections │
│                              │   │  │       incl. X-RAY structural data)      │
│                              │   │  │   PerformanceEnforcer                   │
│                              │   │  │     Level 0/1/2 by daily PnL            │
│                              │   │  │     size multiplier 1.0/0.75/0.50/0.25  │
│                              │   │  │   TradeGate (14 checks, NEVER blocks)   │
│                              │   │  │   OrderService.place_order              │
│                              │   │  │     ORDER_START logged                  │
│                              │   │  │     order_link_id = ti-<24-hex>         │
│                              │   │  │     idempotent retry                    │
│                              │   │  └─► Transformer routes to:                │
│                              │   │       • Shadow @ 9090 (paper, mode=shadow) │
│                              │   │       • Bybit REST (live, mode=live)       │
│                              │   │                                             │
│                              │   │  ┌── Layer 4 POSITION MONITORING           │
│                              │   │  │   ProfitSniper (Mode 4, 5 s tick)       │
│                              │   │  │     5 models: Hurst, MomDecay, ATR Ext, │
│                              │   │  │       Vol Div, Risk/Reward Shift        │
│                              │   │  │     thresholds by regime (tighten/      │
│                              │   │  │       partial/full)                     │
│                              │   │  │     anti-greed pullback 40/60/75 %      │
│                              │   │  │     PROFIT GATE: only acts pnl>0        │
│                              │   │  │     P9_CLOSE_GATE: pnl≥0.5% to close    │
│                              │   │  │     M4_ACT_PARTIAL / M4_ACT_CLOSE       │
│                              │   │  │   PositionWatchdog (10 s tick)          │
│                              │   │  │     modes: passive / safety_net /       │
│                              │   │  │       emergency                         │
│                              │   │  │     pushes WatchdogConcern → urgent_q   │
│                              │   │  │     time-decay SL (5-model Bayesian)    │
│                              │   │  │   RecoveryPlanner                       │
│                              │   │  │     trigger: equity < starting_balance  │
│                              │   │  │     deficit/30-day target               │
│                              │   │  │     allowed strategies & SL/TP caps     │
│                              │   │  └─► Trade closes → record dispatched to:  │
│                              │   │                                             │
│                              │   │  ┌── Layer 5 TIAS (per-trade)              │
│                              │   │  │   TradeContextCollector                 │
│                              │   │  │     6 groups: outcome, entry context,   │
│                              │   │  │     market, technicals, M4 telemetry,   │
│                              │   │  │     APEX optimization                   │
│                              │   │  │   row INSERT into trade_intelligence    │
│                              │   │  │   TradeAnalyzer (DeepSeek background)   │
│                              │   │  │     primary deepseek-chat-v3-0324       │
│                              │   │  │     fallback deepseek-chat              │
│                              │   │  │     row UPDATE with ds_* columns        │
│                              │   │  └─► trade_intelligence (725 rows, 113 syms)
│                              │   │       feeds back into Stage 2 COACHING     │
│                              │   │       block in next CALL_A prompt          │
│                              │   └────────────────────────────────────────────┘
└──────────────────────────────┘                                                  

┌──────────────────────────────┐
│ MCP SSE PROCESS (PID 401)    │
│ server.py --transport sse    │
│   --port 8080                │
│ trading-mcp-sse.service       │
│ 43 tools registered;          │
│ shares trading.db (separate   │
│ DB connection, WAL-safe)      │
│ Auth: Bearer MCP_AUTH_TOKEN   │
└──────────────────────────────┘
                ▲
                │
    Claude CLI subprocess
       (transient, spawned per
        prompt by ClaudeCodeClient)
              │
              ▼
   mcp_stdio_proxy.py
   (forwards stdio JSON-RPC to
    http://127.0.0.1:8080/sse)
```

---

# Section 14 — Open Questions and Contradictions

## 14.1 Multiple "Layer N" numbering schemes coexist

- `core/layer_manager.py:3-9` defines: Layer 1=DATA, Layer 2=BRAIN, Layer 3=EXECUTION (operator-facing toggle in `layer_state.json`).
- `strategies/__init__.py:3-7` defines: Layer 1=Scanner, Layer 2=Scorer, Layer 3=Ensemble, Layer 4=Brain (strategy-internal).
- `core/container.py:39-116` defines: Layer 1=Bybit, Layer 2=Analysis, Layer 3=Brain, Layer 4=Risk+Alerts, Layer 5=Strategy (DI container, historical).
- **Resolution:** all three coexist; pick by context. The operator-facing toggle is the `core/layer_manager.py` scheme (the only one that controls runtime behavior via `layer_state.json`).

## 14.2 Code vs inventory document mismatches

- **Inventory (`SYSTEM_INVENTORY.md`, 4179 lines, last modified 2026-04-24)** likely says brain.py is an active service. **Code says** (`brain.py:24-26`): `Brain v1 (this file) is DEPRECATED. Brain v2 runs inside workers.py.` Confirmed by `systemctl list-units --type=service --state=running` not listing `trading-brain`.
- **The user's MEMORY.md says** the corrected ShadowKlineReader fix shipped 2026-04-26 and `D-3 (kline_worker / trading.db lock contention)` is the new bottleneck. **Live logs confirm** at 21:31:14 `STRAT_PREFETCH_CRITICAL el=35733ms db=14757ms` and `KLINE_FETCH ... el=1032990ms` (17 minutes!) — the D-3 contention is active.
- **`signals` table count (158k rows) vs `aggregated_sentiment` count (290k rows):** sentiment rows are growing 1.83× faster; this is a retention question, not a contradiction.

## 14.3 Configuration mysteries

- **`[mcp_pool] enabled = false`** — `mcp_stdio_proxy.py` makes per-call MCP connections; `[mcp_pool]` would warm-pool them but is disabled.
- **`[reddit] enabled = false`** but `RedditWorker` is still constructed and registered (`workers/manager.py:794-795`). The worker likely no-ops on first tick.
- **`[factory] enabled = false`** ("Disabled: 0 patterns discovered, 0 backtests run — wasting CPU"); but `discovered_patterns` table has 24 rows (older) and `generated_strategies` has 0 — consistent with disabled.
- **Two `layer_manager.py` files:** `src/core/layer_manager.py` (the one cited in code) AND `src/workers/layer_manager.py` (33,668 bytes — almost identical). `_STATE_FILE` is computed from `__file__` in each, but both resolve to `data/layer_state.json`. Risk: a refactor that touches one and not the other introduces drift.
- **`src/workers/settings.py` (45,072 bytes) AND `src/config/settings.py`:** workers/settings.py exists alongside the canonical config/settings.py — both files are large; whether `workers.py` actually imports `src.workers.settings` was not verified during this collection. Worth a follow-up grep.

## 14.4 Dead-code / disabled candidates

- `src/brain/executor.py.deprecated`, `src/brain/prompt_builder.py.deprecated`, `src/brain/scheduler.py.deprecated` — explicit DEPRECATED file extensions; indicates legacy V1 brain pieces.
- `_scanner` legacy injection on PriceWorker, KlineWorker, RegimeWorker, AltDataWorker, StructureWorker, SignalWorker — multiple worker docstrings call it "legacy injection; not read by tick(); slated for removal in Phase 7" (e.g., `kline_worker.py:64-66`).
- `trading-brain.service` installed but disabled (Brain v2 in workers).
- `RedditWorker`, `BacktestWorker`, `DiscoveryWorker`, `LiveMonitorWorker`, `TrialMonitorWorker`, `ScheduledReportWorker` — registered but most produce no rows in current state.
- `OptimizationWorker` — weekly Sunday cadence; not running today.
- `X1_AlwaysTradeStrategy` — testnet-only strategy; not registered on mainnet.

## 14.5 Unclear stage boundaries

- **StrategyWorker is BOTH a "data worker" (in the seven) AND an "analytical pipeline" (Layers 1-3 internal).** Its outputs go into `_score_cache` (consumed by ScannerWorker, treating it as a data worker) AND into `layer_manager._strategy_hints` (consumed by Stage 2 strategist). It performs both roles in the same tick.
- **ScannerWorker per its own docstring (`scanner_worker.py:5-15`) is "NOT one of the 7 data workers"** — it is the cycle trigger. But `workers/manager.py:918` registers it via `self.workers.append` like any worker. From the manager's standpoint it is a worker; from the architecture standpoint it is a trigger.
- **Sentinel firewall (`src/workers/firewall.py`)** blocks `close|take_profit` actions from strategic review (`firewall.py:1-9`). This means Claude's Stage 2 CALL_B can `tighten_stop` and `set_exit` but not actually close — closes happen only via SL/TP, watchdog, or ProfitSniper. Reason cited: "26/31 wins from natural SL/TP (84% win rate, +$115), ALL 8 strategic review closes were losses (0% win rate, -$22)."
- **`urgent_queue` lives in `src/core/urgent_queue.py`** but is consumed by both watchdog (writer) and strategist (reader). It bridges Layer 4 → Layer 2 directly, bypassing the layer_manager toggle (i.e., even if Layer 2 is disabled, watchdog still queues; concerns will only be drained when Layer 2 re-starts).

---

## END OF DATA COLLECTION

**Generated by:** `/effort` max-mode Claude Code session, following `COLLECT_COMPLETE_ARCHITECTURE_TRUTH.md` literally.
**Collection window:** 2026-04-26 21:30-21:55 UTC.
**Live snapshot:** workers + mcp + shadow all running; Layer 2 + 3 paused (`user_stopped=true`); 0 open positions; 30 coins in active_universe; PriceWorker WS ~45k msgs/min; D-3 SQLite contention causing strategy_worker prefetch elapses of 30-40 s.
**Authoritative for:** "what does this system actually do as of today" — supersedes any stale assumptions in older chat sessions.
