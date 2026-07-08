# Trading Intelligence MCP — Project State Snapshot (2026-04-26)

This is a curated, code-anchored map of the system as it stands at HEAD of the corrected Layer 1 migration. Future sessions should read this before re-exploring; it captures the architecture, file paths, line ranges, current state of each layer, hard rules, and pointers to active engagement docs. Treat it as a starting point, not a frozen contract — re-grep before relying on any specific line number that's older than a week.

---

## 1. The system at a glance

Two projects:
- **Trading Intelligence MCP** (`/home/inshadaliqbal786/trading-intelligence-mcp/`) — main trading engine (this project).
- **Shadow** (`/home/inshadaliqbal786/shadow/`) — virtual exchange simulator (out of scope for most engagements).

Trading Intelligence MCP is an enterprise-grade autonomous crypto futures paper-trading system. Stack: Python 3.10+, asyncio, aiosqlite (SQLite WAL mode), numpy (no pandas). Loguru structured logging routed to component-specific log files (workers.log / brain.log / mcp.log / general.log). MCP protocol exposed for Claude integration.

Goal: 10-30 minute holds, 1-2% profit targets per trade, 3-6 trades/hour, $100 starting capital, $0.50/hour minimum profit. Eventual transition to real money.

---

## 2. Architecture (corrected Layer 1, post-2026-04-26 migration)

```
Layer 1A — Data Layer (7 workers, sweet-spot scheduling, all 50 watch_list coins)
    ┌─────────────────────────────────────────────────────────────┐
    │ KlineWorker        (sweet spot 0:30, parent SweetSpotWorker)│
    │ StructureWorker    (0:45)                                   │
    │ SignalWorker       (1:00)                                   │
    │ RegimeWorker       (1:15)                                   │
    │ StrategyWorker     (1:30)                                   │
    │ AltDataWorker      (funding 1:45, OI 5min, F&G 60min)       │
    │ PriceWorker        (continuous WS — BaseWorker, not sweet)  │
    └─────────────────────────────────────────────────────────────┘
                              │
                              │ workers populate caches
                              ↓
    Warm caches (TACache, StructureCache, _signal_cache,
    _per_coin_regimes, _score_cache, _funding_cache, _ws_quotes)

Layer 1B — Cycle Trigger (separate, sweet spot 4:00)
    ┌─────────────────────────────────────────────────────────────┐
    │ ScannerWorker — reads warm caches, computes composite       │
    │ opportunity_score per coin from 5 weighted components:      │
    │   structure (30%) + strategy (30%) + signal (15%) +         │
    │   regime (15%) + funding (10%)                              │
    │ Force-includes open-position coins (HR-3).                  │
    │ Writes top 30 to active_universe table + MarketScanner._active_universe.
    └─────────────────────────────────────────────────────────────┘

Layer 2 — Cycle Execution (Stage 2 → Claude → APEX → Gate → Execute)
    ┌─────────────────────────────────────────────────────────────┐
    │ src/brain/strategist.py:592, :1250 — reads active_universe  │
    │ Builds prompt → Claude (CLI subprocess) → directives        │
    │ APEX: src/apex/optimizer.py + intelligence_assembler.py     │
    │ Gate: src/apex/gate.py (12 checks)                          │
    │ Execute: src/core/rule_engine.py + shadow executor          │
    └─────────────────────────────────────────────────────────────┘
```

---

## 3. Module map (path → role)

```
src/
├── core/                — types, exceptions, logging, log_context (cycle_id),
│                          trade_coordinator, rule_engine, urgent_queue, transformer
├── trading/             — Bybit REST client, WebSocket, services (market, order,
│                          position, account)
├── analysis/            — TAEngine, ta_cache.py (TACache, TTL 90-120s), indicators
│   └── structure/       — X-RAY: structure_engine, structure_cache (TTL 300s),
│                          shadow_kline_reader (async-aiosqlite, 2026-04-25 fix),
│                          session_timing, setup_scanner
├── intelligence/        — news (Finnhub), sentiment (Reddit + aggregator),
│   ├── altdata/         — fear_greed, funding_rates, open_interest, onchain
│   └── signals/         — confidence, signal_generator
├── brain/               — claude_client (CLI subprocess), strategist, decision_parser,
│                          prompts (Call A/B), brain_manager (legacy, deprecated)
├── strategies/          — registry (43 strategies), scorer (TradeScorer 4-comp),
│                          ensemble (voter), regime (RegimeDetector), scanner
│                          (MarketScanner — get_active_universe()), pnl_manager,
│                          smart_leverage
├── workers/             — manager (ServiceContainer), base_worker (BaseWorker +
│                          SweetSpotWorker), sweet_spot_scheduler, 7 data workers,
│                          ScannerWorker, position_watchdog, profit_sniper, news/reddit/
│                          cleanup/discovery/enforcer/etc.
├── database/            — connection (DatabaseManager + aiosqlite + lock-wait
│                          instrumentation), migrations (24 versions),
│                          repositories (12), cleanup, protected_tables
├── factory/             — AI strategy discovery, generation, backtesting
├── fund_manager/        — capital allocation, position sizing, risk weather
├── telegram/            — interactive bot, 14 handlers
├── apex/                — optimizer, prompts, gate, intelligence_assembler
├── mcp/                 — MCP protocol tools (8 modules)
├── config/              — settings.py (38 dataclasses, _build_xxx builders)
└── core/layer_manager.py — Layer 1/2/3 enable/disable + brain review loop
```

---

## 4. Settings & config (post-Phase-1)

**`src/config/settings.py`** — singleton `Settings._instance`, loaded via `Settings.load()` or `Settings._load_fresh()`. All sections are `@dataclass`. Validation in `__post_init__`.

Key dataclasses:
- `UniverseSettings.watch_list: list[str]` — the curated 50 coins. Validated `≥10`, regex `^[A-Z0-9]+USDT$`, no duplicates. Source of truth for "which coins?"
- `WorkerSettings.sweet_spots: SweetSpotsSettings` — chain offsets MM:SS. Validation enforces strict chain ordering at startup.
- `AltDataSweetSpotsSettings` — funding_rates (MM:SS), open_interest_minutes, fear_greed_minutes.
- `ScannerSettings.scoring_weights: ScannerScoringWeights` — composite-score weights (default 0.30/0.30/0.15/0.15/0.10).
- `StructureSettings` — X-RAY config (cache_ttl, batch_size=25, min_candles=50).

**`config.toml`** — TOML mirror of all sections. Recently added: `[workers.sweet_spots]`, `[workers.sweet_spots.altdata]`, `[scanner.scoring_weights]`. Watch list at `[universe] watch_list` (50 coins).

---

## 5. The 7 data workers — current state (post-migration)

| Worker | File | Parent | Sweet spot | Universe | Public accessor (Phase 6) |
|---|---|---|---|---|---|
| KlineWorker | `src/workers/kline_worker.py` | SweetSpotWorker | 0:30 | watch_list (50) | (`is_circuit_open()` legacy) |
| StructureWorker | `src/workers/structure_worker.py` | SweetSpotWorker | 0:45 | watch_list (50) | `get_setup_score(coin)` |
| SignalWorker | `src/workers/signal_worker.py` | SweetSpotWorker | 1:00 | watch_list (50) | `get_signal(coin)` |
| RegimeWorker | `src/workers/regime_worker.py` | SweetSpotWorker | 1:15 | watch_list (50) | `get_regime(coin)` |
| StrategyWorker | `src/workers/strategy_worker.py` | SweetSpotWorker | 1:30 | watch_list (50) | `get_score(coin)` |
| AltDataWorker | `src/workers/altdata_worker.py` | SweetSpotWorker | funding 1:45, OI 5min, F&G 60min | watch_list (50) | `get_funding(coin)` |
| PriceWorker | `src/workers/price_worker.py` | BaseWorker (continuous) | n/a (45s tick) | watch_list (50) | `get_ws_quote(coin, max_age_s)` |
| ScannerWorker | `src/workers/scanner_worker.py` | SweetSpotWorker | 4:00 | reads worker accessors | (cycle trigger; not consumed by other workers) |

All 7 + ScannerWorker emit standardized log lines:
- `SWEET_SPOT_REGISTERED | worker=X offset=MM:SS window_min=N | {ctx()}` at construction.
- `SWEET_SPOT_FIRED | worker=X offset=MM:SS drift_ms=Y fires=N | {ctx()}` per fire.
- `<WORKER>_TICK_SUMMARY | universe=50 ... el=Xms drift_ms=Y | {ctx()}` per tick.
- (Existing per-domain log tags preserved — KLINE_FETCH, XRAY_TICK_SUMMARY, SIG_BATCH, REGIME_GLOBAL, STRAT_CYCLE_DONE, ALTDATA_FUNDING_TICK, PRICE_WS_HEALTH, SCANNER_TICK_SUMMARY.)

`_on_universe_change` rotation handlers were removed from all 5 workers that had them in Phase 7. The master callback dispatcher in manager.py was also removed.

---

## 6. Cycle (Stage 2 → Claude → APEX → Gate → Execute)

- **Strategist:** `src/brain/strategist.py` (2393 LOC). Reads `active_universe` (30 coins) at lines 592 and 1250. Builds Claude prompts (Call A: trades; Call B: positions).
- **Claude client:** `src/brain/claude_client.py` — subprocess wrapper. Timeouts/retries configured in `[brain]` section.
- **APEX:** `src/apex/optimizer.py` + `intelligence_assembler.py`. Reads per-coin TA / structure / WS quote / TIAS history.
- **Gate:** `src/apex/gate.py`. 12 checks (size cap, leverage limit, SL/TP bounds, conviction-weighted allocation, etc.).
- **Rule Engine:** `src/core/rule_engine.py`. Per-trade risk evaluation, applies APEX optimization.
- **Execute:** `src/trading/services/shadow_executor.py`. Submits to Bybit. ORDER_START with `link_id` (idempotency, Phase 5 fix).

`cycle_id` propagation: `src/core/log_context.py` defines ContextVar-based `did=` (decision id), `tid=` (trade id), `wid=` (watchdog id), `sid=` (strategy id). `ctx()` returns the compact suffix. New decision_id created at strategist line 258.

---

## 7. Database

`src/database/connection.py` — `DatabaseManager` async wrapper around aiosqlite. WAL mode + perf pragmas. asyncio.Lock with wait-time instrumentation (`DB_LOCK_WAIT | wait_ms=X holder=Y caller=Z`). Threshold: 1000ms.

Key tables (`src/database/migrations.py`, schema version 24):
- `klines` — OHLCV per (symbol, timeframe, timestamp). Written by KlineWorker.
- `signals` — SignalWorker output.
- `aggregated_sentiment`, `fear_greed_index`, `funding_rates`, `open_interest` — sentiment/altdata.
- `regime_history`, `coin_regime_history` — RegimeWorker.
- `active_universe` — 30 coins, written by ScannerWorker each tick.
- `positions`, `orders`, `trade_history`, `strategy_trades` — PROTECTED tables (cleanup-guarded per `src/database/protected_tables.py`).

Known issue: D-3 lock contention. KlineWorker holds `DatabaseManager._lock` 5-30s during executemany of klines saves. Sweet-spot scheduling (1 fire per 5 min vs. previous 7-fires) reduces frequency by ~6× but doesn't eliminate the per-fire hold time. Fix is a separate engagement — see prior memory `project_shadowklinereader_fix.md` §D-3.

---

## 8. Hard rules (corrected Layer 1)

1. **Workers operate on watch_list, never on active_universe.** Active_universe is the cycle's 30-coin focus, not the worker scope.
2. **Workers don't synchronize with each other or the cycle.** Each has its own SweetSpotScheduler. No event bus, no inter-worker callbacks.
3. **Open-position coins always force-included in active_universe.** ScannerWorker fetches positions and adds them regardless of opportunity score.
4. **Sweet spots respect the chain.** Validated at startup by `SweetSpotsSettings.__post_init__`; bad order → ConfigError, workers refuse to start.
5. **Watch_list is the only source of truth for "which coins?"** Anywhere that needs to know which coins exist, the answer is `settings.universe.watch_list`.
6. **Per-phase atomic git commits.** Each migration phase is one commit. Bad phase → revert that commit only.

---

## 9. Engagement docs (active)

- **Architecture authority:** `/home/inshadaliqbal786/LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md`
- **Implementation prompt:** `/home/inshadaliqbal786/IMPLEMENT_LAYER1_CORRECTED_MIGRATION_PROFESSIONAL.md`
- **Per-phase reports:** `dev_notes/phase{0..9}_*.md` — full detail of every change.
- **Plan file (this engagement):** `/home/inshadaliqbal786/.claude/plans/plan-mode-today-zippy-music.md`

Recent prior engagement docs:
- `dev_notes/phase0_layer1_investigation.md`, `phase1_baseline_measurements.md`, etc. — pre-corrected-Layer-1 work (Phase 2 watch_list filter, etc.). Largely superseded by the corrected migration.
- `dev_notes/phase0_shadowklinereader_investigation.md` through `phase7_decision_and_summary.md` — the 2026-04-25/26 ShadowKlineReader fix.
- `dev_notes/phase{1..13}_*report.md` — post-Layer-1 fixes (boot ordering, WAL checkpoint, ORDER_START idempotency, brain credentials, etc.).

---

## 10. Quick-start grep recipes

```bash
# Find every place a setting is used
rg -n 'settings\.universe\.watch_list' src/

# Verify no worker reads active_universe directly
rg -n 'get_active_universe' src/workers/

# Confirm sweet-spot inheritance
.venv/bin/python -c "
from src.workers.kline_worker import KlineWorker
from src.workers.structure_worker import StructureWorker
print(KlineWorker.__bases__, StructureWorker.__bases__)"

# Check sweet-spot drift in live logs
grep 'SWEET_SPOT_FIRED' logs/workers.log | tail -50

# Audit active_universe writes (only ScannerWorker should write)
rg -n 'INTO active_universe|FROM active_universe' src/

# Find all log tags emitted today
grep -oE '[A-Z][A-Z0-9_]+_[A-Z][A-Z0-9_]*' logs/workers.log | sort -u
```

---

## 11. Things to remember

- The `MarketScanner.scan_market()` raw-ticker path still exists in `src/strategies/scanner.py:200-410`. It runs at boot via `manager.py:532` to seed `_active_universe` so the system isn't empty before ScannerWorker's first sweet-spot fire (~4 min worst case). After the first fire, the new composite-score path owns it.
- `MarketScanner._subscribers` and `subscribe(callback)` API are kept (zero cost when empty). Future non-worker subscribers can register without re-introducing the rotation-handler architecture.
- `cleanup_worker` (`src/workers/cleanup_worker.py`) is responsible for kline retention (`_KLINES_RETENTION_PER_SYMTF = 300`) and stale-row pruning. Untouched by this migration.
- `position_watchdog` (`src/workers/position_watchdog.py`, 127KB) and `profit_sniper` (138KB) are large but unrelated to Layer 1; this migration didn't touch them.
- Brain v1 (`src/brain/manager.py` + `brain.py`) is **deprecated**. Use `workers.py` + `LayerManager` (in `src/core/layer_manager.py`). Layer 2 in LayerManager runs the alternating Call A / Call B brain review loop.

This document supersedes `~/.claude/.../memory/project_architecture.md` and `project_xray_status.md` for all Layer 1 questions. Those memory files predate the corrected migration and should be re-read with caution.
