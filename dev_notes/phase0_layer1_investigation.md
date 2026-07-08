# Phase 0 — Layer 1 Universe Alignment Investigation

**Date:** 2026-04-26
**Brief:** `/home/inshadaliqbal786/IMPLEMENT_LAYER1_UNIVERSE_ALIGNMENT_PROFESSIONAL.md`
**Blueprint:** `/home/inshadaliqbal786/LAYER1_UNIVERSE_ALIGNMENT_BLUEPRINT.md`
**Factual baseline:** `dev_notes/layer1_to_xray_complete_state.md`
**Status:** Investigation complete. No code changed.

---

## 0. Three Hard Rules Recap

- **HR-1 — One source of truth:** every component asking "which coins?" traces back to `[universe] watch_list` in `config.toml`.
- **HR-2 — Open positions always included:** `ScannerWorker_input_set = watch_list ∪ open_position_symbols`; `Shadow_subscriptions = watch_list ∪ open_position_symbols`.
- **HR-3 — Per-piece rollback:** each modification piece is one git commit, independently reversible.

---

## 1. Component Inventory & Findings

### 1.1 `src/strategies/scanner.py` — `MarketScanner` (~402 LOC)

**Class signature** (lines 14-35):
```python
class MarketScanner:
    """Scans all Bybit USDT perps and selects the top coins by opportunity score."""

    def __init__(self, settings: Settings, market_service: MarketService,
                 instrument_service=None) -> None:
        self.settings = settings
        self.market_service = market_service
        self.instrument_service = instrument_service
        self.regime_detector = None  # Late-wired from WorkerManager
        self._position_service = None  # Late-wired from WorkerManager
        self._removed_cooldown: dict[str, float] = {}
        self._cache: list[dict] = []
        self._cache_time: float = 0.0
        self._active_universe: list[str] = []
        self._universe_version: int = 0
        self._subscribers: list = []
```

Late-wired services:
- `_position_service` ← injected at `manager.py:898-900` (`scanner._position_service = pos_svc` where `pos_svc = self._services.get("position")`).
- `regime_detector` ← injected later in startup chain.

**Public API:**
- `async scan_market() -> list[dict]` (lines 151-335) — returns top `cfg.max_coins` coin dicts (default 30 from `[scanner] max_coins=30`)
- `async get_active_universe() -> list[str]` (lines 364-374) — returns `list(self._active_universe)`; falls back to `[c["symbol"] for c in self._cache]` if active list empty AND cache fresh; triggers `scan_market()` if cache stale (>300s)
- `subscribe(callback)` (lines 37-39) — registers async/sync callback for universe changes
- `_update_universe(results)` (lines 41-149) — private, BTC/ETH force-prepend + position protection + 5-min cooldown
- `_scan_testnet(cfg)` (lines 337-359) — testnet-only path using hardcoded `SUPPORTED_SYMBOLS`
- `get_coin_tier(symbol, vol, range)` (lines 376-401) — leverage-tier classifier (orthogonal to fix)

**`scan_market()` flow** (lines 151-335):
1. line 165-166: testnet branch → `_scan_testnet(cfg)`
2. line 169: `tickers = await self.market_service.get_all_linear_tickers()` — fetches **ALL ~300 USDT perpetuals** in one Bybit call
3. line 170-178: fallback chain (if bulk fetch fails: `get_tickers(symbols=settings.bybit.default_symbols)`; if that fails: return `self._cache`)
4. lines 180-321: per-ticker scoring loop — **per-ticker independent** (no cross-ticker percentiles → filtering before this loop is safe)
5. line 322: `scored.sort(key=lambda x: x["score"], reverse=True)`
6. line 323: `result = scored[:cfg.max_coins]` (top 30)
7. line 325-326: cache result
8. line 328-332: log `Market scan: N coins scored, top T selected. Best: ...`
9. line 334: `await self._update_universe(result)`

**Scoring formula** (per-ticker independent, lines 209-296):

| Component | Range | Thresholds |
|---|---:|---|
| Momentum (`abs(change_24h_pct)`) | 0-30 | ≥10%→30, ≥5%→25, ≥3%→20, ≥1.5%→15, ≥0.8%→10 |
| Volatility (`(high-low)/price * 100`) | 0-25 | ≥8%→25, ≥5%→20, ≥3%→15, ≥1.5%→10 |
| Trend strength (`change_abs / range`) | 0-15 | ≥0.6→15, ≥0.4→10, ≥0.25→5 |
| Volume (`volume_24h`) | 0-20 | ≥$500M→20, ≥$100M→18, ≥$50M→15, ≥$20M→10, ≥$5M→5 |
| Spread (`(ask-bid)/bid * 100`) | 0-10 | ≤0.02%→10, ≤0.05%→8, ≤0.10%→5, ≤0.20%→2 |
| Regime bonus (from `regime_detector`) | -10 to +10 | trending_up/down→+10, volatile→+5, dead→-10 |
| Chop penalty | -15 | If `range > 5%` AND `trend_ratio < 0.25` |

**Hard disqualifiers** (lines 186-197): volume < $5M, price < $0.0001, spread > 0.5%.

**Final:** `score = max(0, score)`. Logged as `SCAN_SCORE | sym=... final=... ...` at DEBUG.

**`_update_universe(results)` flow** (lines 41-149):
- Line 52: `new_symbols = [c["symbol"] for c in results[:cfg.max_coins]]`
- Lines 54-57: force-prepend BTC/ETH if not present
- Lines 59-82: **position protection** — `positions = await self._position_service.get_positions()`, build `protected_symbols = {p.symbol for p in positions}`, force-include each protected symbol not already in `new_symbols`. **Failure mode (lines 66-72):** if position fetch fails, fall back to `protected_symbols = set(self._active_universe)` (refuses to remove ANY coins this tick — fail-safe).
- Lines 84-97: **cooldown filter** — symbols removed in last 5 minutes blocked from re-entry (unless protected).
- Lines 99-102: compute `added`, `removed` deltas vs old set.
- Lines 104-112: record removals in `self._removed_cooldown`; prune entries >1h old.
- Lines 114-116: update global `SUPPORTED_SYMBOLS` registry.
- Lines 118-141: if changed → update `self._active_universe`, increment `_universe_version`, log `Scanner universe UPDATED v{ver}: ...`, notify subscribers.
- Lines 143-149: pre-cache instrument info for new symbols (best-effort).

**Internal state:**
| Field | Type | Updated by | Used by |
|---|---|---|---|
| `_active_universe` | `list[str]` | `_update_universe()` | `get_active_universe()` |
| `_universe_version` | `int` | `_update_universe()` | logs |
| `_removed_cooldown` | `dict[str, float]` | `_update_universe()` | re-entry gate |
| `_cache` | `list[dict]` | `scan_market()` | `get_active_universe()` fallback |
| `_cache_time` | `float` | `scan_market()` | TTL check (300s) |
| `_position_service` | service ref | manager.py:900 (late) | `_update_universe()` |
| `_subscribers` | `list` | `subscribe()` | `_update_universe()` notifies |

**All log emissions:**
- `SCAN_SCORE | sym=... final=...` (DEBUG, line 298-308)
- `Market scan: N scored, top T selected. Best: ...` (INFO, line 329-332)
- `Scanner universe UPDATED v{ver}: N coins (added: A, removed: R, protected: P)` (INFO, line 121-127)
- `Scanner universe INITIALIZED: N coins` (INFO, line 138-140)
- `Scanner: PROTECTING {sym} (has open position, kept in universe)` (INFO, line 79-82)
- `Scanner: N coins blocked by re-entry cooldown: ...` (INFO, line 94-97)
- Error logs for fallback triggers (line 171, 177)

### 1.2 `src/workers/scanner_worker.py` (59 LOC)

**Constructor** (lines 13-30):
```python
class ScannerWorker(BaseWorker):
    def __init__(self, settings: Settings, db: DatabaseManager, scanner: MarketScanner) -> None:
        super().__init__(
            name="scanner_worker",
            interval_seconds=float(settings.scanner.scan_interval_seconds),  # 300s
            settings=settings, db=db,
        )
        self.scanner = scanner
```

**`tick()` method** (lines 33-58):
- Line 35: `results = await self.scanner.scan_market()`
- Line 39: `await self.db.execute("DELETE FROM active_universe")`
- Lines 42-50: per-result `INSERT OR REPLACE INTO active_universe (...)` — 7 columns
- Line 52: log `SCANNER | coins={n} ... | {ctx()}`

Receives a fully-constructed `MarketScanner` (manager.py:890). No code change needed in Phase 2.

### 1.3 `src/workers/structure_worker.py` (210 LOC)

**Constructor** (lines 39-72):
```python
def __init__(self, settings, db, engine, cache, scanner=None,
             coin_discovery=None, shadow_kline_reader=None) -> None:
    ...
    self._coin_discovery = coin_discovery   # ← Phase 3 removes
    self._shadow_reader = shadow_kline_reader
    ...
    self._batch_size = settings.structure.batch_size       # 25
    self._scan_full = settings.structure.scan_full_market  # ← Phase 3 removes (Phase 6 cleanup)
    self._coin_refresh_interval = settings.structure.coin_refresh_interval  # 600
```

**`_get_universe()` method** (lines 147-187, ~40 lines, dual-mode):

```python
async def _get_universe(self) -> list[str]:
    if self._scan_full and self._coin_discovery:
        # Full market mode: 134 coins via CoinDiscovery + scanner-merge
        now = time.monotonic()
        if not self._full_universe or (now - self._universe_refreshed_at) > self._coin_refresh_interval:
            self._full_universe = self._coin_discovery.get_analyzable_coins()  # ← LINE 153
            if self._scanner:
                try:
                    active = await self._scanner.get_active_universe()  # ← LINE 157
                    active_set = set(self._full_universe)
                    for sym in active:
                        if sym not in active_set:
                            self._full_universe.append(sym)
                except Exception:
                    pass
            self._universe_refreshed_at = now
            self._batch_start = 0
        if not self._full_universe:
            return self.settings.bybit.default_symbols[:10]
        batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]
        self._batch_start += self._batch_size
        if self._batch_start >= len(self._full_universe):
            self._batch_start = 0
        return batch if batch else self._full_universe[:self._batch_size]
    else:
        # Legacy scanner mode
        universe = []
        if self._scanner:
            try:
                universe = await self._scanner.get_active_universe()  # ← LINE 182
            except Exception:
                pass
        if not universe:
            universe = self.settings.bybit.default_symbols[:10]
        return universe
```

Phase 3 replaces this entire method with a 5-line scanner-only version (per blueprint Section 10.1).

### 1.4 `src/analysis/structure/coin_discovery.py` (106 LOC)

**Class signature & SQL** (per prior investigation):
```python
class CoinDiscovery:
    def __init__(self, shadow_db_path, min_candles=50, refresh_interval=600): ...
    def get_analyzable_coins(self) -> list[str]:
        # Opens per-call sync sqlite3 connection (?mode=ro)
        # SELECT symbol, COUNT(*) FROM klines WHERE timestamp > ? GROUP BY symbol HAVING cnt >= ?
        # Returns sorted list. In-memory cache for refresh_interval seconds.
```

**Single caller across project:** `structure_worker.py:153` (verified by `grep -rn "coin_discovery\|CoinDiscovery"`).

Phase 3 stops calling it; Phase 6 deletes the file.

### 1.5 `src/workers/manager.py` — wiring (relevant slices)

**CoinDiscovery construction** (lines 178-194):
```python
if settings.structure.scan_full_market:
    try:
        from src.analysis.structure.coin_discovery import CoinDiscovery   # ← Phase 6 removes
        from src.analysis.structure.shadow_kline_reader import ShadowKlineReader
        shadow_path = settings.structure.shadow_db_path
        coin_discovery = CoinDiscovery(
            shadow_db_path=shadow_path,
            refresh_interval=settings.structure.coin_refresh_interval,
        )                                                                  # ← Phase 6 removes
        shadow_reader = ShadowKlineReader(shadow_db_path=shadow_path)
        await shadow_reader.connect()
        self._services["coin_discovery"] = coin_discovery                  # ← Phase 6 removes
        self._services["shadow_kline_reader"] = shadow_reader
        ...
```

**MarketScanner construction** (lines 886-890):
```python
if s.scanner.enabled and market_svc:
    inst_svc = self._services.get("instrument_service")
    scanner = MarketScanner(s, market_svc, instrument_service=inst_svc)   # ← Phase 2 adds watch_list
    self._services["scanner"] = scanner
    self.workers.append(ScannerWorker(s, db, scanner))
```

**Position service late-wire** (lines 898-900):
```python
pos_svc = self._services.get("position")
if pos_svc:
    scanner._position_service = pos_svc
```

**StructureWorker construction** (lines 919-926):
```python
sw = StructureWorker(
    settings=s, db=db, engine=se, cache=sc,
    scanner=self._services.get("scanner"),
    coin_discovery=self._services.get("coin_discovery"),    # ← Phase 3 removes this kwarg
    shadow_kline_reader=self._services.get("shadow_kline_reader"),
)
```

**Service registry** (lines 580-603) — `_EXPECTED_SERVICE_KEYS` includes `"coin_discovery"` (line 586), `"scanner"` (line 601). Phase 6 removes `"coin_discovery"`.

### 1.6 `shadow/src/collector/coin_selector.py` (132 LOC)

**Class:** `CoinSelector(db: DatabaseManager, config: ShadowConfig)`. Uses `pybit.unified_trading.HTTP(testnet=False)`.

**`select_top_coins(count=100)`** (lines 32-53):
1. Calls `await asyncio.to_thread(self._fetch_and_rank, count)` — wraps sync Bybit call in thread pool
2. Calls `await self._save_to_db(symbols)` — writes to `tracked_coins` table
3. Logs top 5
4. On exception: falls back to `_get_cached_coins()` (reads `SELECT symbol FROM tracked_coins WHERE is_active=1`)

**`_fetch_and_rank(count)`** (lines 55-94):
1. `self._client.get_instruments_info(category="linear")` — sync, all instruments
2. Filter to `status=="Trading" AND quoteCoin=="USDT" AND contractType=="LinearPerpetual"` → `active_symbols` set
3. `self._client.get_tickers(category="linear")` — sync, all tickers
4. For each ticker in `active_symbols`: append `(symbol, float(ticker["turnover24h"]))`
5. Sort by turnover desc, return top N symbols

**`_save_to_db(symbols)`** (lines 96-119):
- `UPDATE tracked_coins SET is_active = 0` (deactivate all)
- `INSERT … ON CONFLICT(symbol) DO UPDATE SET rank_by_volume=excluded.rank_by_volume, is_active=1`

**`tracked_coins` schema** (`shadow/src/database/migrations.py:106-114`):
```sql
CREATE TABLE IF NOT EXISTS tracked_coins (
    symbol TEXT PRIMARY KEY,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    volume_24h_usd REAL DEFAULT 0,
    rank_by_volume INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    last_tick_at TEXT
)
```

### 1.7 `shadow/shadow.py` (309 LOC) — startup sequence

**Lines 111-150 — CoinSelector + orphan re-add:**
```python
coin_selector = CoinSelector(db=db, config=config)
symbols = await coin_selector.select_top_coins(config.collector.coin_count)

if not symbols:
    log.error("No coins selected — cannot start collectors")
    return

ws_manager = WebSocketManager(config)
ws_manager.set_symbols(symbols)

# Orphan re-add: open positions outside top-100
open_pos_rows = await db.fetch_all(
    "SELECT DISTINCT symbol FROM virtual_positions WHERE status = 'open'"
)
orphan_symbols = [r["symbol"] for r in open_pos_rows if r["symbol"] not in symbols]
if orphan_symbols:
    log.warning("Open positions with untracked symbols — re-adding: {syms}",
                syms=", ".join(orphan_symbols))
    for sym in orphan_symbols:
        symbols.append(sym)
        await db.execute(
            """INSERT INTO tracked_coins (symbol, added_at, rank_by_volume, is_active)
               VALUES (?, datetime('now'), 0, 1)
               ON CONFLICT(symbol) DO UPDATE SET is_active = 1""",
            (sym,),
        )
    ws_manager.set_symbols(symbols)
```

The orphan re-add queries Shadow's **own database** (`virtual_positions WHERE status='open'`) — NOT trading.db, NOT an HTTP API. This works because Shadow IS the virtual exchange.

**Lines 254-263 — asyncio.gather() task list:**
```python
tasks = [
    asyncio.create_task(ws_manager.run(), name="websocket"),
    asyncio.create_task(kline_collector.run(), name="kline_collector"),
    asyncio.create_task(ticker_collector.run(), name="ticker_collector"),
    asyncio.create_task(funding_collector.run(), name="funding_collector"),
    asyncio.create_task(oi_collector.run(), name="oi_collector"),
    asyncio.create_task(position_monitor.run(), name="position_monitor"),
    asyncio.create_task(wallet_snapshotter.run(), name="wallet_snapshotter"),
    asyncio.create_task(daily_rollup.run(), name="daily_rollup"),
]
```

**No daily coin refresh task is scheduled** despite `coin_refresh_interval=86400` in config (D-1 — see §6 below).

### 1.8 Configuration files

**Workers' `config.toml` `[scanner]`** (lines 295-301):
```toml
[scanner]
enabled = true
scan_interval_seconds = 300
min_volume_24h = 5000000
max_coins = 30
max_spread_pct = 0.15
```

**Workers' `[bybit] default_symbols`** (lines 20-30): 20-coin fallback list (BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, SUI, 1000PEPE, WIF, HYPE, AAVE, NEAR, APT, ARB, OP, LTC, BCH, TON).

**Workers' `[analysis.structure]`** (config.toml:670-702): contains `scan_full_market = true` (line ~699). Phase 6 removes this key.

**No `[universe]` section exists yet.** Phase 1 adds it.

**Shadow's `config.toml` `[collector]`:**
```toml
[collector]
coin_count = 100
coin_refresh_interval = 86400
kline_interval = "1"
ticker_snapshot_interval = 60
funding_rate_interval = 28800
open_interest_interval = 300
```

**Shadow's config dataclass** (`shadow/src/utils/config.py:48-56`): `CollectorConfig` with same fields. `_build_collector` builder at line 145-153.

### 1.9 `active_universe` table — write-only

**Schema** (`src/database/migrations.py:377-386`):
```sql
CREATE TABLE IF NOT EXISTS active_universe (
    symbol TEXT PRIMARY KEY,
    opportunity_score REAL NOT NULL,
    volume_24h REAL,
    change_24h_pct REAL,
    funding_rate REAL,
    spread_pct REAL,
    coin_tier INTEGER DEFAULT 3,
    updated_at TEXT DEFAULT (datetime('now'))
)
```

**Writes:** `scanner_worker.py:39` (DELETE), `scanner_worker.py:42` (INSERT OR REPLACE).
**Reads:** ZERO `SELECT FROM active_universe` in `src/` (verified by grep). The actual source of truth is the in-memory `MarketScanner._active_universe` accessed via `get_active_universe()`.

**Conclusion:** Table is write-only / informational. The 11 downstream consumers all use `get_active_universe()` (in-memory), not the table.

### 1.10 Open-position service (HR-2 source)

**File:** `src/trading/services/position_service.py`

**Method** (lines 49-77):
```python
@retry(max_attempts=3, delay=1.0)
@timed
async def get_positions(self, symbol: str | None = None) -> list[Position]:
    """Returns list of Position objects (only positions with size > 0)."""
```

`Position` is a dataclass in `src/core/types.py` with `.symbol` attribute.

**MarketScanner usage** (scanner.py:64): `positions = await self._position_service.get_positions()` → builds `protected_symbols = {p.symbol for p in positions}`. Wired late at `manager.py:898-900`.

**Shadow's open-position source** (shadow.py:129-131): Queries Shadow's OWN `shadow.db.virtual_positions WHERE status='open'`. Different mechanism, same effect.

---

## 2. Complete Universe Data-Flow Map (current state)

```
                                ┌─────────────────────────────────────┐
                                │ Bybit REST API (/v5/market/tickers) │
                                │   ~300 USDT linear perpetuals       │
                                └─────────────────────────────────────┘
                                                  │
                              ┌───────────────────┴────────────────────┐
                              │                                          │
                              ▼                                          ▼
              ┌──────────────────────────────┐         ┌────────────────────────────┐
              │ Shadow.CoinSelector          │         │ MarketScanner.scan_market  │
              │ (startup only, never refreshed)│         │ (every 300s)                │
              │                                │         │                              │
              │ → top 100 by turnover24h       │         │ → score all 300, take top 30│
              │ + orphan re-add               │         │ + force BTC/ETH               │
              │ = ~102 coins                  │         │ + protect open positions      │
              └──────────────────────────────┘         │ + 5-min cooldown              │
                              │                         └────────────────────────────┘
                              ▼                                          │
              ┌──────────────────────────────┐                          ▼
              │ Shadow's WebSocket subs       │         ┌────────────────────────────┐
              │ (~102 coins ticker + kline)  │         │ _active_universe (in-mem 30)│
              └──────────────────────────────┘         │ + active_universe table     │
                              │                         │   (write-only, never read)  │
                              ▼                         └────────────────────────────┘
              ┌──────────────────────────────┐                          │
              │ shadow.db klines              │                          ▼
              │ (~134 distinct coins build  │         ┌────────────────────────────┐
              │  up over time)                │         │ scanner.get_active_universe│
              └──────────────────────────────┘         │   () called by 11 consumers│
                              │                         └────────────────────────────┘
                              ▼                                          │
              ┌──────────────────────────────┐         ┌────────────────┴───────────┐
              │ CoinDiscovery (every 600s)   │◄───X──┤ Strategist x2, PriceWorker, │
              │  → 126 coins (those w/50+    │ READS │ KlineWorker, SignalWorker,  │
              │   1-min candles in last 24h) │       │ AltDataWorker, RegimeWorker,│
              └──────────────────────────────┘       │ StrategyWorker,             │
                              │                       │ structure_worker x2,         │
                              ▼                       │ manager init                 │
              ┌──────────────────────────────┐       └────────────────────────────┘
              │ structure_worker (every 60s) │
              │ → CoinDiscovery merge scanner│
              │ → 134 coins, batched 25/tick │
              └──────────────────────────────┘
```

**The mismatch:** Shadow's 102 ≠ Scanner's 30 ≠ CoinDiscovery's 126. ~70 Shadow streams nobody analyses, ~104 structure analyses nobody trades.

**After Phase 1-6:** all three converge to `[universe] watch_list` (50) with the 30-coin active focus selected from those 50.

---

## 3. All Files To Be Modified Across Both Projects

(Already itemized in the plan; summary here for the report.)

**Trading Intelligence MCP (`/home/inshadaliqbal786/trading-intelligence-mcp`):**
- `config.toml` (Phase 1, 6)
- `src/config/settings.py` (Phase 1, 6)
- `src/strategies/scanner.py` (Phase 2)
- `src/workers/manager.py` (Phase 2, 3, 6)
- `src/workers/structure_worker.py` (Phase 3)
- `src/analysis/structure/coin_discovery.py` (Phase 6 — delete)
- `scripts/verify_watch_list.py` (Phase 1 — new)
- `tests/test_universe_settings.py` (Phase 1 — new)
- `tests/test_scanner_filter.py` (Phase 2 — new)

**Shadow (`/home/inshadaliqbal786/shadow`):**
- `src/collector/coin_selector.py` (Phase 4)
- `src/utils/config.py` (Phase 4)
- `config.toml` (Phase 4, 6)
- `shadow.py` (Phase 4 — log lines)

---

## 4. All Log Tags Involved (grep targets for verification)

**Existing (preserved):**
- `SCANNER`, `Scanner universe UPDATED`, `Scanner universe INITIALIZED`, `Scanner: PROTECTING`, `Scanner: N coins blocked by re-entry cooldown`, `SCAN_SCORE`, `Market scan: ...` (scanner.py)
- `XRAY_TICK`, `XRAY_TICK_ERR`, `XRAY_SCANNER_ERR`, `XRAY_SESSION_ERR` (structure_worker.py)
- `XRAY_COINS`, `XRAY_COINS_ERR` (coin_discovery.py — drops at Phase 6)
- `Selected {n} coins`, `Open positions with untracked symbols — re-adding`, `Expanded symbol list by {n} orphan(s)` (shadow.py)

**New (Phase 2-4):**
- `SCANNER_WATCH_LIST | size=N source=config.universe.watch_list` (Phase 2, INFO, once per process)
- `SCANNER_INPUT | watch_list=N protected=M total=K → scored=K → top=30` (Phase 2, INFO, per scan)
- `XRAY_UNIVERSE_EMPTY | {ctx()}` (Phase 3, WARNING, on empty active_universe)
- `SHADOW_WATCH_LIST | size=N source=workers_config` (Phase 4, INFO, once at startup)
- `SHADOW_SUBS_FINAL | watch=N orphans=M total=K` (Phase 4, INFO, after orphan re-add)

---

## 5. Cross-Process Open-Position Visibility

**MarketScanner (workers process):** `await self._position_service.get_positions()` → fetches from Bybit via `BybitClient.get_positions(category="linear", settleCoin="USDT")`.

**Shadow (separate process):** `SELECT DISTINCT symbol FROM virtual_positions WHERE status='open'` against its OWN `shadow.db`.

These are **different sources** but produce the same set in steady-state (Shadow IS the virtual exchange — every open position in Bybit's "shadow" mode corresponds to a row in Shadow's `virtual_positions`).

**Implication for HR-2:** Both Phase 2 (scanner filter) and Phase 4 (Shadow subs) preserve their own open-position fetches — no cross-process coupling needed for this.

**Cross-process config sharing (for `[universe] watch_list`):**
- Per `/etc/systemd/system/shadow.service`: `ProtectHome=read-only` allows Shadow to READ any file under `/home/inshadaliqbal786/*`.
- Per `/etc/systemd/system/trading-workers.service`: same `ProtectHome=read-only`.
- Both can read each other's config files. **D4 decision:** workers' `config.toml` is the single source of truth; Shadow reads it via absolute path `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml`.

---

## 6. Discovered Issues (DEFERRED)

- **D-1 (new):** Shadow's daily coin refresh task is missing from `asyncio.gather()` at `shadow.py:254-263`. The `coin_refresh_interval=86400` config is set but no scheduler fires it. **Becomes moot post-Phase-4** because watch_list is static. No action needed in this fix; Phase 6 will comment out the dead config keys.
- **D-2 (new):** `active_universe` SQL table is write-only (zero `SELECT FROM active_universe` in `src/`). Blueprint Section 19.1 defers cleanup. Mention in Phase 6 report.
- **D-3 (carryover from prior fix):** `kline_worker` heavy `executemany` writes hold `DatabaseManager.asyncio.Lock` for 5-30s, causing structure_worker's `market_repo.get_klines` to wait. Pre-existing. Out of scope.

---

## 7. Phase 0 Verification Gate (per brief)

The brief requires the investigation to answer 5 questions before proceeding:

| Brief Question | Answer |
|---|---|
| 1. Public API of MarketScanner today? | `scan_market() -> list[dict]`, `get_active_universe() -> list[str]`, `subscribe(callback)`, `_update_universe(results)` (private). Constructor `(settings, market_service, instrument_service=None)`. Late-wired `_position_service` (manager.py:898-900) and `regime_detector`. |
| 2. Where is `scan_full_market` config flag read in code? | `src/workers/structure_worker.py:71` — `self._scan_full = settings.structure.scan_full_market`. Branch test at line 149 (`if self._scan_full and self._coin_discovery`). Field defined at `src/config/settings.py:721` (`StructureSettings.scan_full_market: bool = True`). |
| 3. How does Shadow's orphan re-add logic find open-position symbols today? | Direct SQL query against Shadow's OWN `shadow.db`: `SELECT DISTINCT symbol FROM virtual_positions WHERE status='open'` (`shadow.py:129-131`). NOT via HTTP API, NOT via workers' `trading.db`. |
| 4. What happens if `scanner.get_active_universe()` returns an empty list? | None of the 11 callers explicitly handle empty — they iterate it (so empty → no-op tick). Safe. After Phase 3, `structure_worker._get_universe()` will explicitly log `XRAY_UNIVERSE_EMPTY` and return `[]` if scanner empty. |
| 5. Is there a startup-order dependency between Shadow service and workers service? | None hard-coded. Workers read Shadow only via HTTP API (`http://127.0.0.1:9090`); if Shadow is down, calls fail and workers degrade gracefully (existing). The persistent `ShadowKlineReader` connection (sister fix) opens read-only against `shadow.db` and degrades cleanly if missing. |

**Verification gate PASSED. Proceeding to Phase 1.**
