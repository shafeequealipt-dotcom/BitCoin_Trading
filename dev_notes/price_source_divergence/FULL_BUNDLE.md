# COLLECT_PRICE_SOURCE_DIVERGENCE_FORENSIC_DATA — Full Bundle

Aggregated 2026-05-02T11:43:38Z from 17 source files in this directory.


================================================================================
FILE: INDEX.md
================================================================================

# Price-Source Divergence Forensic Bundle — INDEX

**Collection timestamp:** 2026-05-02 11:30:27 UTC
**Collector:** Claude Code CLI (single-pass, auto-mode)
**Project paths:**
- Main: `/home/inshadaliqbal786/trading-intelligence-mcp`
- Shadow: `/home/inshadaliqbal786/shadow`
**Live state at capture:**
- Main `workers.py` PID 398 — running
- Shadow `shadow.py` PID 390 — running, API at `http://127.0.0.1:9090`
- Shadow `/api/health`: `running`, 50 coins tracked, 50,886 WS msgs total
- Open positions in Shadow: **ZERO** (`/api/positions` → `{"positions": []}`)
- Most recent closed trade: `ONDOUSDT` at `2026-05-02T06:29:09` UTC (~5 h before capture)

> **Pre-condition gap (Hard Rule 5):** Module S target — at least one open
> position — is NOT met at capture time. S1/S2 are reconstructive: they
> reference the most recent closed trades for cross-source comparison and
> document the live capture surface that would be exercised on a live
> position.  All read/write path tracing in P / Q / R / U / V is complete
> regardless.

## Files in this bundle

| File | Module | Status |
|---|---|---|
| `INDEX.md` | — | this file |
| `P1_price_worker.md` | Main project — PriceWorker | complete |
| `P2_main_project_consumers.md` | Main project — every reader of price | complete |
| `P3_klines_vs_tickers.md` | Klines vs tickers (timeframe distinction) | partial — see notes |
| `Q1_shadow_architecture.md` | Shadow process / dirs / endpoints | complete |
| `Q2_shadow_price_feed.md` | Shadow's price-feed origin (a/b/c question) | complete — answer = **(a)** |
| `Q3_shadow_endpoints.md` | Shadow API endpoints with sample payloads | complete |
| `R1_telegram_handlers.md` | Telegram bot handlers for `/positions`, `/performance` | complete |
| `R2_telegram_price_source.md` | Definitive answer: where Telegram reads P&L from | complete |
| `S1_live_divergence.md` | Single-instant cross-source capture | reconstructive (no open pos) |
| `S2_temporal_divergence.md` | Repeated capture | reconstructive (no open pos) |
| `T1_closed_trade_forensics.md` | Cross-source comparison for 5 closed trades | complete |
| `U1_ipc.md` | Cross-process IPC main↔Shadow | complete |
| `U2_shared_storage.md` | Shared databases / files / env | complete |
| `V1_price_source_matrix.md` | Per-component → price source matrix | complete |
| `W1_e2e_trace.md` | End-to-end timeline of a single WS tick | complete |
| `W2_anomalies.md` | Anomaly catalog (the bug list) | complete |

## TL;DR — the headline divergence

The system has **TWO independent Bybit WebSocket connections** running in two separate processes:

1. **Main project's `PriceWorker`** (PID 398) — uses `pybit.unified_trading.WebSocket`, populates in-memory `self._ws_quotes` dict, also tries to mirror to `ticker_cache` SQLite table via `market_repo.save_ticker(...)`.
2. **Shadow's `WebSocketManager`** (PID 390) — uses raw `websockets` library, populates in-memory `self._latest_tickers` dict, also writes periodic snapshots to `ticker_snapshots` table in `shadow.db`.

The dashboard's `/positions` and `/performance` numbers are produced by a multi-stage pipeline that re-reads Shadow's response and **OVERWRITES** Shadow's `mark_price` and **RECOMPUTES** `unrealized_pnl` from main project's `ticker_cache` SQLite table. That table is silently 5+ hours stale because the PriceWorker WS callback's `loop.create_task(...)` write path fails inside the pybit thread-pool callback (no running event loop in that thread → swallowed `RuntimeError`).

See `W2_anomalies.md` for the full list. The headline is captured in:
- `R2_telegram_price_source.md` (where Telegram reads from)
- `T1_closed_trade_forensics.md` (concrete numeric divergences for 7 closed trades)
- `W2_anomalies.md` (root-cause catalog)

================================================================================
FILE: P1_price_worker.md
================================================================================

# P1 — PriceWorker (main project's WebSocket-facing price fetcher)

## P.1.1 — File and overview

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/price_worker.py`
- **Lines of code:** 264 (incl. blanks/comments) — `wc -l` confirmed
- **Class:** `PriceWorker(BaseWorker)` at `price_worker.py:26`
- **Worker tier:** `WorkerTier.LAYER1A` at `price_worker.py:41`
- **Tick interval:** `settings.workers.market_data_interval` (default 45 s) — `price_worker.py:49`

The tick body is a connection-health/reconnect loop. The actual price ingest happens in the WebSocket callback `_handle_ticker_update` (`price_worker.py:161`), which runs on whatever thread `pybit` dispatches it on (NOT the asyncio event loop).

## P.1.2 — WebSocket subscription

- **Library:** `pybit.unified_trading.WebSocket` wrapped by `src.trading.websocket.BybitWebSocket`
- **Connect:** `await self.ws.connect_public()` — `price_worker.py:110`
- **Subscribe:** `self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)` — `price_worker.py:111`
- **Stream type:** Bybit `tickers.{symbol}` linear-perp ticker stream
- **Universe:** `settings.universe.watch_list` — 50 symbols pre-seeded into `self._tracked_symbols` at `price_worker.py:59`
- **Reconnect policy:** `tick()` polls `self.ws.is_running` and resets `self._connected = False` so the next tick reconnects (`price_worker.py:134-137`). No exponential backoff on this side — the underlying `pybit` client manages reconnects itself.

## P.1.3 — Where prices are written

Verbatim from `price_worker.py:185-220`:

```python
last_price = _sf(tick_data.get("lastPrice"))     # line 185
if last_price <= 0:
    log.debug(...)
    return  # Skip update with zero/invalid price

# Phase 6: update in-memory quote cache for APEX / assembler.
self._ws_quotes[symbol] = (last_price, _time.monotonic())   # line 196
self._ws_msg_count += 1                                     # line 200

ticker = Ticker(
    symbol=symbol,
    last_price=last_price,
    bid=_sf(tick_data.get("bid1Price")),
    ask=_sf(tick_data.get("ask1Price")),
    high_24h=_sf(tick_data.get("highPrice24h")),
    low_24h=_sf(tick_data.get("lowPrice24h")),
    volume_24h=_sf(tick_data.get("volume24h")),
    change_24h_pct=_sf(tick_data.get("price24hPcnt")) * 100,
    timestamp=now_utc(),
)

# Save synchronously via the repo (callback is sync)
import asyncio
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self.market_repo.save_ticker(ticker))   # line 218
except RuntimeError:
    pass    # ← OBSERVED ANOMALY: silent swallow when no loop on this thread
```

**Source field:** `tick_data["lastPrice"]` — Bybit's last traded price.
**Two write sinks:**
1. `self._ws_quotes[symbol] = (last_price, _time.monotonic())` — in-memory dict (line 196)
2. `loop.create_task(self.market_repo.save_ticker(ticker))` — writes to `ticker_cache` SQLite table (line 218)

**OBSERVED ANOMALY:** the `try / except RuntimeError: pass` at `price_worker.py:216-220` silently swallows the case where the pybit callback runs on a thread without an asyncio running-loop. In that case the SQLite write is dropped. See live evidence in `W2_anomalies.md` (anomaly A1) — `ticker_cache` has only 8 rows and its newest entry is 5+ hours stale at capture time.

## P.1.4 — Cache structure: `_ws_quotes`

Defined at `price_worker.py:66`:

```python
self._ws_quotes: dict[str, tuple[float, float]] = {}
```

- **Key:** symbol (e.g. `"BTCUSDT"`)
- **Value:** `(last_price: float, monotonic_ts: float)` where `monotonic_ts` is `_time.monotonic()`
- **TTL:** 5.0 s — accessor `get_ws_quote(symbol, max_age_s=5.0)` returns `None` for stale entries (`price_worker.py:239-257`)
- **Live sample:** could not be sampled — the dict lives in PID 398's memory and is not introspectable from outside the process. The `PRICE_WS_HEALTH` log line (`price_worker.py:149-157`) emits `quotes_cached=N` every tick. Per Shadow's `/api/health` we see Shadow has 50,886 WS msgs total over ~8 min uptime (~106 msgs/s aggregate), so equivalent rate on main side should be similar order.

## P.1.5 — Cache structure: `ticker_cache` table

Schema (verbatim from live `trading.db` SQLite):

```sql
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
)
```

**Write site:** `src/database/repositories/market_repo.py:266-283`

```sql
INSERT OR REPLACE INTO ticker_cache
(symbol, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

**Read site:** `src/database/repositories/market_repo.py:294-296` (`get_ticker`) and `src/core/transformer.py:666-669` (`_get_local_price`).

**Live state at 2026-05-02 11:30:27 UTC** — eight rows total:

| symbol | last_price | updated_at | age at capture |
|---|---|---|---|
| ONDOUSDT | 0.2696 | 2026-05-02T06:30:10 | 5 h 0 m |
| NEARUSDT | 1.2877 | 2026-05-02T06:26:34 | 5 h 4 m |
| INJUSDT | 3.784 | 2026-05-02T06:19:29 | 5 h 11 m |
| MANAUSDT | 0.0895 | 2026-05-02T06:14:41 | 5 h 16 m |
| AXSUSDT | 1.3789 | 2026-05-02T06:06:19 | 5 h 24 m |
| DOGEUSDT | 0.10751 | 2026-05-02T05:59:29 | 5 h 31 m |
| HYPERUSDT | 0.12379 | 2026-05-02T05:25:11 | 6 h 5 m |
| AEROUSDT | 0.4558 | 2026-05-02T05:18:07 | 6 h 12 m |

**OBSERVED ANOMALY:** despite PriceWorker subscribing to 50 symbols and the WS being demonstrably healthy on Shadow's side (50,886 msgs received), `ticker_cache` contains only 8 symbols and none have been updated for 5+ hours. The 8 symbols correspond exactly to the symbols that were traded today; the rows were almost certainly written by `MarketService._fetch_ticker` (REST, `market_service.py:101`) when `OrderService` priced the orders, NOT by the live WS callback. See `W2_anomalies.md` A1 for the full diagnosis.

**Indexes:** none beyond the implicit PK on `symbol`.

## P.1.6 — Other price-related caches

- **`MarketService._ticker_cache`** (in-memory) — `src/trading/services/market_service.py:45`
  - Type: `dict[str, tuple[float, Ticker]]`
  - TTL: 5.0 s — `market_service.py:46`
  - Populated by `_fetch_ticker` on REST hit at `market_service.py:67`
  - Special key `_all_linear` for bulk fetch at `market_service.py:118-123` with 30 s TTL
- **`klines` SQLite table** — written by `KlineWorker` (`src/workers/kline_worker.py`)
  - Read at multiple places via `MarketRepository.get_klines_by_timeframe` and similar
- **DB tables:** `ticker_cache`, `klines`, `ticker_snapshots` (Shadow only — see Q1)
- No additional in-memory price storage in main project beyond the three above.

## P.1.7 — Live measurement

- Shadow's `/api/health` reports `ws_messages_total=50886` over `uptime_seconds=494` ≈ **103 WS msgs/sec aggregate** at capture time.
- Main project's PriceWorker should run at similar order; the per-tick `PRICE_WS_HEALTH` log line gives `msgs_per_min` over the most recent ~45 s window. Could not be sampled here — would require reading the live log file.
- `ticker_cache` row count: 8 (verified via `SELECT COUNT(*) FROM ticker_cache`).
- `_ws_quotes` size: not externally observable. The `PRICE_WS_HEALTH` line in `logs/` would show it.

**NOT IDENTIFIED — live in-process introspection of `_ws_quotes`** — investigated locations: only direct attach to PID 398 (gdb / py-spy) would surface it, and that's outside the data-collection scope.

================================================================================
FILE: P2_main_project_consumers.md
================================================================================

# P2 — Main Project Consumers of Price Data

Built from grep `_ws_quotes | get_ws_quote | get_latest_price | ticker_cache | _ticker_cache | mark_price | last_price` across `src/`. Components in canonical layer order.

## Inventory matrix

| Reader | File:line | Source it reads | Field accessed | Used for |
|---|---|---|---|---|
| `Transformer._get_local_price` | `src/core/transformer.py:666-714` | `ticker_cache` SQLite table (max-age default 10s) | `last_price`, `updated_at` | Replace Shadow positions' `mark_price` and recompute `unrealized_pnl` (see R2) |
| `Transformer._enrich_positions_with_local_prices` | `src/core/transformer.py:716-841` | calls `_get_local_price`, then mutates `pos.mark_price` and `pos.unrealized_pnl` | n/a | Overwrite Shadow's prices on every `/api/positions` call routed through Transformer |
| `Transformer._enrich_balance_with_local_prices` | `src/core/transformer.py:843-…` | same | n/a | Overwrite Shadow's `total_equity` on every balance read |
| `FreshnessGuard` | `src/core/freshness_guard.py:35-36` | `MarketService._ticker_cache` (in-mem dict) | `cached.timestamp` / `cached.last_price` | Decide if a price reading is fresh enough for downstream gates |
| `APEX assembler` | `src/apex/assembler.py:147-148` | `PriceWorker.get_ws_quote(symbol, max_age_s=5.0)` | last_price | Build APEX layer-3 snapshot price for entry validation |
| `ScannerWorker` | `src/workers/scanner_worker.py:703-704` | `MarketService.get_ticker_cached(symbol)` | `Ticker.last_price` | Build CoinPackage `current_price` field for the Brain |
| `Sentiment aggregator` | `src/intelligence/sentiment/aggregator.py:169-175` | `ticker_cache.change_24h_pct` | `change_24h_pct` | Sentiment momentum overlay |
| `MarketService.get_ticker` | `src/trading/services/market_service.py:48-68` | `_ticker_cache` (in-mem); REST fallback | `Ticker.last_price` | Programmatic price reads (entry sizing, fee est.) |
| `MarketRepository.get_ticker` | `src/database/repositories/market_repo.py:285-309` | `ticker_cache` SQLite | full Ticker row | Fallback ticker reader |
| `PositionWatchdog` (multiple) | `src/workers/position_watchdog.py:567,624,1028,1046,1146,1166,1177,1231,1233,1315,1335,1440-1442,1451,1570,1591,1653,1664,1755,1807,1808,2008,2040-2068,2322,2502,2510` | `ticker.last_price` (where `ticker = await market_service.get_ticker(symbol)` → `_ticker_cache` / REST) and `pos.mark_price` (set by Shadow / Transformer enrichment) | both | SL/TP evaluation; trailing stops; Mode4 ladder; close-trigger price; PnL display |
| `ProfitSniper (M4)` | `src/workers/profit_sniper.py:94 (docstring)` and the body's `market.get_ticker` calls | `market_service.get_ticker → _ticker_cache` | `Ticker.last_price` | M4 ladder evaluation |
| `Brain prompt builder` | `src/strategies/scanner.py` and `src/telegram/ai/context_builder.py` | CoinPackage `current_price` from ScannerWorker (above) | n/a | Brain prompt context |
| `Telegram bot handlers` | see `R1_telegram_handlers.md` | `position_service.get_positions()` → ShadowPositionService → Shadow API → optionally Transformer-enriched | `pos.mark_price`, `pos.unrealized_pnl` | Display |
| `OrderService.place_order` | `src/trading/services/order_service.py` | `MarketService.get_ticker → _ticker_cache` | `Ticker.last_price` | Pre-flight price for sizing & max-loss check |

## Mapping: which feed each component sees in practice

Three feeds in main project:

- **F-A** = `PriceWorker._ws_quotes` (in-mem, monotonic ts, 5 s freshness, **fed by WS**)
- **F-B** = `ticker_cache` SQLite table (wall-clock ts, 10 s freshness gate in transformer, *should* be fed by WS but is in fact only fed by REST hits via `MarketService._fetch_ticker`)
- **F-C** = `MarketService._ticker_cache` (in-mem, wall-clock ts, 5 s TTL, **fed by REST only**)

| Reader | Sees | Notes |
|---|---|---|
| APEX assembler | F-A | clean (5s gate) |
| Transformer enrich | F-B | **stale** in practice — see W2 anomaly A1 |
| FreshnessGuard | F-C | REST-fed |
| ScannerWorker `get_ticker_cached` | F-C | REST-fed |
| PositionWatchdog | F-C (live reads) + `pos.mark_price` (Shadow- or transformer-set) | mixed |
| ProfitSniper | F-C | REST-fed |
| Sentiment aggregator | F-B | **stale**, but only reads `change_24h_pct` so impact muted |
| OrderService | F-C | REST-fed |
| Telegram /positions | F-B (via Transformer enrichment) | **stale** |

The same nominal "current price" can be three different numbers at the same instant, depending on which reader you ask. Sometimes a fourth (`klines.close`).

================================================================================
FILE: P3_klines_vs_tickers.md
================================================================================

# P3 — Klines vs Tickers (timeframe distinction)

## P.3.1 — KlineWorker

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/kline_worker.py` (494 lines)
- **What it fetches:** Bybit linear-perp OHLCV bars across multiple timeframes (M5, H1, H4, D1 per the project's documented timeframe set).
- **Cadence:** sweet-spot scheduler (per the Layer 1A/1B partition). Tick body fetches per the configured intervals; finished bars are persisted, the in-progress bar is not (standard backfill pattern).
- **Storage:** `klines` SQLite table in `data/trading.db`. Schema query confirms columns include `symbol, timeframe, open, high, low, close, volume, turnover, timestamp` (plus indexes on `(symbol, timeframe, timestamp)`).
- **Distinction:** the most recent row for `(symbol, timeframe='5m')` may be either:
  - the *finished* M5 candle (close ≤ 5 min old), or
  - the *in-progress* M5 candle (close = whatever it was at the time of the last upsert)
  Depending on writer cadence and finalization rules. Need to verify whether the worker upserts the in-progress bar or only finalized bars.

## P.3.2 — Crucial question: does any consumer treat `klines.close` as "current price"?

Grep-based answer:

```
$ grep -rn "klines.*close\|SELECT.*close.*FROM klines" src/ --include='*.py'
```

The grep returned no direct matches for `SELECT … close … FROM klines` patterns inside the production paths reviewed for P2 (assembler / transformer / position_watchdog / profit_sniper / scanner / market_service). Klines are read by:

- `StructureWorker` and `RegimeWorker` (FVG/OB distance, ATR/ADX) — these correctly read OHLC, not "current price"
- `TIAS` and TradeScorer — read finished bars for backtest/scoring
- `KlineCollector` (Shadow side) consumes M5 bars only for analytics

Conclusion (within the bounds of this collection): **no production "current price" reader uses `klines.close` as a proxy.** Current-price readers go to F-A / F-B / F-C (see P2). If a stale-by-up-to-5-minutes price ever appears in the dashboard, the cause is the F-B 5-hour staleness from W2 anomaly A1, not klines fallthrough.

## P.3.3 — Latest M5 close vs live tick comparison

Could not be produced reliably from the live DB at capture time:

```
=== klines latest M5 close 5 coins ===
(empty result for timeframe='5m' on BTC/ETH/SOL/DOGE/AXS)
```

The query `WHERE timeframe='5m'` returned zero rows for the spot-checked symbols. Likely the timeframe column uses a different label (`"5"` or `"M5"`). NOT IDENTIFIED — investigated `'5m'`. A follow-up should run:

```
SELECT DISTINCT timeframe FROM klines LIMIT 20;
```

to find the actual label, then re-issue the comparison. This collection's other modules do not depend on the M5 comparison; they answer the divergence question without it.

================================================================================
FILE: Q1_shadow_architecture.md
================================================================================

# Q1 — Shadow Architecture

## Q.1.1 — Process model

- **Same VM, separate process** as the main project.
- **Process:** `inshada+ 390 ... shadow.py` (verified via `ps aux | grep python`)
- **Working dir:** `/home/inshadaliqbal786/shadow`
- **Entry point:** `/home/inshadaliqbal786/shadow/shadow.py` (318 lines)
- **Started by:** systemd unit (directory `systemd/` present in shadow root) — exact unit not opened during collection.
- **Listens on:** `127.0.0.1:9090` (verified via `ss -tlnp`: `LISTEN 0 128 127.0.0.1:9090 ... users:(("python",pid=390,fd=14))`)
- **Database:** `data/shadow.db` (separate from main project's `data/trading.db`)

## Q.1.2 — Directory structure

```
/home/inshadaliqbal786/shadow/
├── backups/
├── config.toml
├── data/
│   ├── shadow.db
│   ├── shadow.db-shm
│   └── shadow.db-wal
├── layer_manager.py
├── logs/
├── requirements.txt
├── shadow.py                     ← entry point
├── src/
│   ├── api/                      ← HTTP API (aiohttp)
│   │   └── shadow_client.py      ← ALL endpoint handlers live here
│   ├── collector/                ← market-data ingest
│   │   ├── coin_selector.py
│   │   ├── funding_collector.py
│   │   ├── kline_collector.py
│   │   ├── oi_collector.py
│   │   ├── ticker_collector.py
│   │   └── websocket.py          ← WebSocketManager (Shadow's OWN WS feed)
│   ├── database/
│   │   ├── connection.py
│   │   └── migrations.py
│   ├── exchange/
│   │   ├── daily_rollup.py
│   │   ├── order_engine.py       ← order lifecycle, fills, P&L compute
│   │   ├── position_monitor.py   ← SL/TP monitor
│   │   ├── trade_recorder.py
│   │   ├── wallet.py             ← VirtualWallet
│   │   └── wallet_snapshotter.py
│   ├── telegram/                 ← (Shadow has its OWN Telegram bot)
│   └── utils/
├── systemd/
└── tests/
```

## Q.1.3 — API surface

From `src/api/shadow_client.py:84-97` (route registration):

| Method | Path | Purpose | Handler |
|---|---|---|---|
| POST | `/api/order` | Place a new order (MARKET) | `handle_place_order` |
| POST | `/api/close` | Close a position (full close) | `handle_close_position` |
| POST | `/api/reduce` | Reduce a position by qty (partial close) | `handle_reduce_position` |
| POST | `/api/set-sl` | Set stop loss | `handle_set_sl` |
| POST | `/api/set-tp` | Set take profit | `handle_set_tp` |
| GET | `/api/positions` | All open positions with live PnL | `handle_get_positions` |
| GET | `/api/position/{symbol}` | Single open position | `handle_get_position` |
| GET | `/api/position/{symbol}/last_close` | Most recent closed position record | `handle_get_last_close` |
| GET | `/api/balance` | Wallet balance | `handle_get_balance` |
| GET | `/api/ticker/{symbol}` | Latest ticker (Shadow's own WS cache) | `handle_get_ticker` |
| GET | `/api/health` | System health | `handle_health` |

**Endpoints used by main project's `OrderService` / `PositionService` adapters** (via `ShadowOrderService`, `ShadowPositionService`, `ShadowAccountService` in `src/shadow/shadow_adapter.py`):

- `POST /api/order` — order placement
- `POST /api/close` — full close
- `POST /api/reduce` — partial close
- `POST /api/set-sl`, `POST /api/set-tp` — risk modifications
- `GET /api/positions` — position state (the path that **carries Shadow's price-derived `unrealized_pnl_usd`**)
- `GET /api/position/{sym}/last_close` — authoritative close record for the watchdog (added to bypass a previous Bug 2 race; see `shadow_adapter.py:192-225`)
- `GET /api/balance` — equity / margin
- `GET /api/health` — health check

================================================================================
FILE: Q2_shadow_price_feed.md
================================================================================

# Q2 — Shadow's Price Feed (the central question)

## Q.2.1 — Where does Shadow get prices? Definitive answer

**Answer = (a)** Shadow has its OWN WebSocket connection to Bybit, completely independent of the main project's `PriceWorker`.

Evidence:

- `shadow.py:27` imports `from src.collector.websocket import WebSocketManager`
- `shadow.py:123-125` constructs `WebSocketManager(config); ws_manager.set_symbols(symbols)`
- `src/collector/websocket.py:14` imports `import websockets` (raw `websockets` library, NOT `pybit`)
- `src/collector/websocket.py:40` reads `self._ws_url = config.bybit.ws_url`
- `src/collector/websocket.py:155` builds subscription topics `f"tickers.{s}" for s in self._symbols`
- `src/collector/websocket.py:199-203` opens `await websockets.connect(self._ws_url, ping_interval=None, close_timeout=5)` directly

The main project uses `pybit.unified_trading.WebSocket` (wrapped in `src.trading.websocket.BybitWebSocket`); Shadow uses raw `websockets`. They are two separate TCP connections to Bybit's WSS endpoint with two separate subscription sets.

## Q.2.2 — Shadow's price cache

Defined at `src/collector/websocket.py:43-44`:

```python
self._latest_tickers: dict[str, dict[str, Any]] = {}
self._ticker_timestamps: dict[str, float] = {}
```

- **Key:** symbol
- **Value (`_latest_tickers`):** the full Bybit ticker JSON (merged delta — see `_handle_ticker_message:325-327`)
- **Value (`_ticker_timestamps`):** `time.time()` wall-clock timestamp
- **TTL:** none — entries live forever in memory; staleness is observed externally via `get_ticker_age()` (`websocket.py:121-126`). The `TickerCollector` snapshot path uses `STALE_THRESHOLD = 300` s (`ticker_collector.py:18`) to skip writes for stale coins.
- **Sample at capture timestamp 2026-05-02 11:30:27 UTC:** could not be sampled directly from PID 390's memory. The DB-backed `ticker_snapshots` table contains a continuously refreshed (60 s default cadence) reflection of `_latest_tickers`. Eight-row sample from `data/shadow.db`:

  | symbol | ts (ms) | last_price | mark_price |
  |---|---|---|---|
  | AAVEUSDT | 1777721384597 | 92.19 | 92.20 |
  | ADAUSDT | 1777721384597 | 0.2485 | 0.2485 |
  | AEROUSDT | 1777721384597 | 0.4553 | 0.4553 |
  | ALGOUSDT | 1777721384597 | 0.1076 | 0.10766 |
  | ALICEUSDT | 1777721384597 | 0.14774 | 0.14781 |
  | APTUSDT | 1777721384597 | 0.9944 | 0.9945 |
  | ARBUSDT | 1777721384597 | 0.12192 | 0.12193 |
  | ATOMUSDT | 1777721384597 | 1.8867 | 1.8867 |

  Snapshot timestamp `1777721384597` ms = `2026-05-02 11:29:44 UTC` — fresh (43 s before capture).

**DB persistence schema (`ticker_snapshots`):** written by `TickerCollector._snapshot` at `src/collector/ticker_collector.py:94-103`:

```sql
INSERT OR IGNORE INTO ticker_snapshots
(symbol, timestamp, last_price, mark_price, index_price,
 bid1_price, bid1_size, ask1_price, ask1_size,
 high_24h, low_24h, volume_24h, turnover_24h,
 price_change_24h_pct, funding_rate,
 open_interest, open_interest_value)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Cadence: `config.collector.ticker_snapshot_interval` (default 60 s).

## Q.2.3 — When Shadow fills an order

Verbatim from `src/exchange/order_engine.py:174-194`:

```python
# Step 2: Get real price — WS cache first, REST fallback for newly tracked symbols
# whose WebSocket subscription hasn't received its first tick yet.
price_data = self._price_fn(symbol)         # ← reads WebSocketManager._latest_tickers
if price_data is None:
    log.info("No WS price for {sym}, falling back to REST", sym=symbol)
    price_data = await self._fetch_rest_price(symbol)
    if price_data is None:
        return _reject(f"No price available for {symbol} (WS and REST both failed)")

last_price = float(price_data["last"])
bid_price = _safe_float(price_data.get("bid"))
ask_price = _safe_float(price_data.get("ask"))
volume_24h = _safe_float(price_data.get("volume"))
funding_rate = _safe_float(price_data.get("funding"))

# Step 3: Simulate fill with slippage
slippage = self._get_slippage_pct()
if side == "Buy":
    fill_price = last_price * (1 + slippage / 100)
else:
    fill_price = last_price * (1 - slippage / 100)
```

`self._price_fn` is `get_price_data` defined at `shadow.py:176-186`:

```python
def get_price_data(symbol: str):
    ticker = ws_manager.get_latest_ticker(symbol)
    if ticker is None:
        return None
    return {
        "last": ticker.get("lastPrice", 0),
        "bid": ticker.get("bid1Price"),
        "ask": ticker.get("ask1Price"),
        "volume": ticker.get("volume24h"),
        "funding": ticker.get("fundingRate"),
    }
```

So Shadow's fill = `last_price × (1 ± slippage_pct)`. Slippage is configured in `[exchange]` of `shadow/config.toml` (`taker_fee_rate`, `slippage_pct`, `slippage_mode`, `slippage_min`, `slippage_max`).

Persisted to `virtual_positions` row at `order_engine.py:216-238` — column `entry_price = fill_price` (i.e. the slippage-adjusted price), `entry_slippage_pct`, `entry_slippage_usd`, `notional_value = qty * fill_price`, `entry_fee_usd = notional * taker_fee_rate`.

## Q.2.4 — When Shadow updates an open position's mark price

Shadow does **not** materialize a separate "mark price update" cadence. The mark for any open position is computed on-demand by `OrderEngine.get_positions()` (`order_engine.py:660-701`):

```python
async def get_positions(self) -> list[dict[str, Any]]:
    rows = await self._db.fetch_all(
        "SELECT * FROM virtual_positions WHERE status = 'open' ORDER BY opened_at ASC"
    )
    positions = []
    now = _now_iso()
    for row in rows:
        price_data = self._price_fn(row["symbol"])
        current_price = float(price_data["last"]) if price_data else row["entry_price"]
        ...
```

That price is always the freshest WS tick (no TTL gate at all on this path). If the WS hasn't ticked since the position opened, `price_data` is `None` and the code falls back to **`row["entry_price"]`** (P&L = 0). See W2 anomaly A4 for the implication.

The separate `position_monitor` (`src/exchange/position_monitor.py`) ALSO calls `self._price_fn(symbol)` to evaluate SL/TP triggers; same source.

## Q.2.5 — How Shadow computes unrealized P&L

`src/exchange/order_engine.py:670-700`:

```python
entry_price = row["entry_price"]
notional = row["notional_value"]   # ← stored at fill time = qty * fill_price (slippage-adj)
if row["side"] == "Buy":
    unrealized_pct = (current_price - entry_price) / entry_price * 100
else:
    unrealized_pct = (entry_price - current_price) / entry_price * 100
unrealized_usd = unrealized_pct / 100 * notional
```

Fees / funding NOT included in unrealized.

## Q.2.6 — How Shadow computes realized P&L on close

`src/exchange/order_engine.py:327-356`:

```python
side = position["side"]
if side == "Buy":
    exit_price = current_price * (1 - slippage / 100)   # closing long = sell
else:
    exit_price = current_price * (1 + slippage / 100)   # closing short = buy
...
entry_price = position["entry_price"]
notional = position["notional_value"]
if side == "Buy":
    gross_pnl_pct = (exit_price - entry_price) / entry_price * 100
else:
    gross_pnl_pct = (entry_price - exit_price) / entry_price * 100
gross_pnl_usd = gross_pnl_pct / 100 * notional
exit_fee = notional * self._taker_fee_rate
net_pnl_usd = gross_pnl_usd - exit_fee
```

So Shadow's realized = `gross_pnl - exit_fee`. The `entry_fee` is debited at open via `wallet.deduct_entry_fee` (`order_engine.py:241`), so the wallet's `total_realized_pnl` is effectively `gross - entry_fee - exit_fee`. Both fees use `taker_fee_rate * notional_value` (where notional reflects the slippage-adjusted entry).

**Exit-price source:** `current_price = float(price_data["last"])` from WS cache (or the `close_price` arg if provided by SL/TP triggers — `order_engine.py:311-321`).

================================================================================
FILE: Q3_shadow_endpoints.md
================================================================================

# Q3 — Shadow API Endpoints That Return Price or P&L

## `GET /api/positions` — handler `handle_get_positions` at `src/api/shadow_client.py:213-221`

```python
async def handle_get_positions(request):
    engine = request.app["engine"]
    positions = await engine.get_positions()    # ← OrderEngine.get_positions
    return web.json_response({"positions": positions})
```

**Backing source:** `OrderEngine.get_positions()` (`order_engine.py:660-701`) — reads `virtual_positions` rows, calls `self._price_fn(symbol)` per position to fetch live price from `WebSocketManager._latest_tickers`, computes unrealized P&L on the fly with the formula in Q.2.5.

**Live response at 2026-05-02 11:30:27 UTC** (verbatim):

```json
{"positions": []}
```

(no open positions exist — see INDEX pre-condition note)

**Per-position payload shape** (per `OrderEngine.get_positions` return-dict construction at `order_engine.py:683-700`):

```json
{
  "position_id": "<uuid>",
  "symbol": "BTCUSDT",
  "side": "Buy",
  "entry_price": 67250.5,
  "current_price": 67310.2,
  "qty": 0.0148,
  "leverage": 3,
  "notional_value": 996.31,
  "margin_used": 332.10,
  "unrealized_pnl_pct": 0.0888,
  "unrealized_pnl_usd": 0.885,
  "stop_loss_price": null,
  "take_profit_price": null,
  "opened_at": "2026-05-02T11:00:01.123456+00:00",
  "hold_duration_seconds": 1825
}
```

## `GET /api/position/{symbol}/last_close` — handler at `src/api/shadow_client.py:241-276`

Returns the authoritative close record for the most recent closed position with that symbol. Used by main project's watchdog. Query (verbatim):

```sql
SELECT position_id, symbol, side, entry_price, exit_price,
       quantity, leverage, notional_value,
       gross_pnl_pct, gross_pnl_usd,
       net_pnl_pct, net_pnl_usd,
       close_trigger, opened_at, closed_at,
       hold_duration_seconds, exit_slippage_pct,
       entry_fee_usd, exit_fee_usd, result
FROM virtual_positions
WHERE symbol = ? AND status = 'closed'
ORDER BY closed_at DESC
LIMIT 1
```

## `GET /api/balance` — handler at `src/api/shadow_client.py:279-287`

**Live response at 2026-05-02 11:30:27 UTC** (verbatim):

```json
{
  "total_equity": 6149.847369884066,
  "available_balance": 6149.847369884066,
  "margin_in_use": 0,
  "total_unrealized_pnl": 0.0,
  "total_realized_pnl": -2322.0454235650805,
  "total_fees_paid": 1528.1072065508529,
  "starting_balance": 10000.0,
  "total_trades": 1190,
  "total_wins": 447,
  "total_losses": 743
}
```

Backed by `VirtualWallet.get_balance()` reading `virtual_wallet` table (single row id=1).

## `GET /api/ticker/{symbol}` — handler at `src/api/shadow_client.py:290-312`

```python
price_fn = request.app["price_fn"]
price_data = price_fn(symbol)    # ← reads WS cache via shadow.py:get_price_data
if price_data is None:
    return web.json_response({"error": ...}, status=404)
return web.json_response({
    "symbol": symbol,
    "last_price": price_data.get("last"),
    "bid": price_data.get("bid"),
    "ask": price_data.get("ask"),
    "volume_24h": price_data.get("volume"),
    "funding_rate": price_data.get("funding"),
})
```

## `GET /api/health` — handler at `src/api/shadow_client.py:315-354`

**Live response at 2026-05-02 11:30:27 UTC** (verbatim):

```json
{
  "status": "running",
  "uptime_seconds": 494,
  "websocket": "connected",
  "coins_tracked": 50,
  "positions_open": 0,
  "monitor_active": true,
  "monitor_stats": {
    "running": true, "positions_monitored": 0,
    "total_checks": 0, "total_cycles": 493,
    "sl_triggered": 0, "tp_triggered": 0,
    "last_flush_ago": 494.5338969230652
  },
  "db_size_mb": 822.3,
  "ws_messages_total": 50886
}
```

Note: `ws_messages_total=50886` over `uptime_seconds=494` → ~103 msgs/s aggregate from Shadow's WS — confirms Shadow's WS is healthy and active.

================================================================================
FILE: R1_telegram_handlers.md
================================================================================

# R1 — Telegram Bot Handlers

## R.1.1 — `/positions` handler

Two paths exist:

- `PortfolioHandler.positions` at `src/telegram/handlers/portfolio.py:46-56` (legacy)
- `_show_positions` at `src/telegram/handlers/control_handler.py:400` and `_build_positions_text` at `:433` (the **active** handler, registered for `/positions` per `bot.py:92` comment)

### Active path: `control_handler._build_positions_text` (`:433-477`)

Verbatim formatting block:

```python
for pos in positions:
    pnl_pct = 0.0
    if ...
        pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
        if pos.side == "Sell" or getattr(pos.side, "value", None) == "Sell":
            pnl_pct = -pnl_pct
    ...
    f"  Entry: ${pos.entry_price:.2f} | Now: ${pos.mark_price:.2f}\n"
    f"  PnL: {pnl_pct:+.2f}%\n"
```

### Step-by-step on `/positions` invocation

1. Operator sends `/positions` to Telegram bot (`InteractiveTelegramBot` at `src/telegram/bot.py`).
2. Bot dispatches to `_show_positions(query, context)` (`control_handler.py:400`).
3. `position_service = context.bot_data.get("position_service")` is fetched (`:404-406` area).
4. `positions = await position_service.get_positions()` is called (`:408`).
5. **`position_service` is `ShadowPositionService`** when running in Shadow/paper mode (the system's only mode at present per `exchange_mode='shadow'` in `trade_log` rows). Wired via `src/factory/...` and Transformer router (Phase T3).
6. `ShadowPositionService.get_positions` does `await session.get(f"{base_url}/api/positions")` → Shadow returns positions JSON (`shadow_adapter.py:150-171`).
7. Each position dict is converted via `_build_position` (`shadow_adapter.py:673-700`) — `mark_price = data["current_price"]`, `unrealized_pnl = data["unrealized_pnl_usd"]`.
8. **CRITICAL — Transformer enrichment.** When the Transformer is wired between the bot and ShadowPositionService (which it is when running in Shadow mode per `src/core/transformer.py:947-991`), the wrapper `TransformedPositionService.get_positions` calls `await self._t._enrich_positions_with_local_prices(positions)` immediately after — `transformer.py:983-985`:

   ```python
   async def get_positions(self, symbol: str | None = None) -> list[Position]:
       positions = await self._inner.get_positions(symbol)
       await self._t._enrich_positions_with_local_prices(positions)
       return positions
   ```

9. `_enrich_positions_with_local_prices` (`transformer.py:716-841`) mutates each Position in place: replaces `pos.mark_price` with `ticker_cache.last_price`, recomputes `pos.unrealized_pnl` from `notional = pos.size * pos.entry_price`.
10. The mutated positions return to `_build_positions_text`, which formats `pnl_pct` from `(mark_price - entry_price)/entry_price` and prints `Now: ${pos.mark_price:.2f}`.

So the displayed "Now" price and PnL are **derived from main project's `ticker_cache`** (when within 0.5 % of Shadow's price) or **from Shadow's WS** (when divergence > 0.5 %, `transformer.py:771-794` keeps Shadow's mark).

## R.1.2 — `/performance` handler

`performance_command` at `src/telegram/handlers/dashboard_handler.py:1037-1157`. Registered at `:2325`:

```python
app.add_handler(CommandHandler("performance", performance_command))
```

Step-by-step:

1. Reads `pnl_manager = _svc(context, "pnl_manager")` — the `DailyPnLManager` from `src/strategies/pnl_manager.py:16`.
2. Reads `_trades_today`, `_wins_today`, `_losses_today`, `current_pnl_pct`, `current_pnl_usd`, `_best_trade_pct`, `_worst_trade_pct`, `_avg_win_pct`, `_avg_loss_pct`, `_max_drawdown_pct`, `_streak_count`, `_streak_type`, `_per_coin_stats`, `_daily_loss_limit_pct` (all attrs of `DailyPnLManager`).
3. Computes win-rate, expectancy, profit factor, risk-used inline.
4. Renders text and replies.

The values reported are entirely derived from `DailyPnLManager`'s in-memory fields. `DailyPnLManager._recalculate()` (called from `update()`) fetches the wallet via `self.account_service.get_wallet_balance()` (when Shadow-mode = `ShadowAccountService` → Shadow's `/api/balance` → Shadow's `virtual_wallet.total_realized_pnl`) plus the `position_service.get_positions()` for unrealized.

So `/performance`'s `Total PnL` is `current_pnl_pct` = `(realized + unrealized) / starting_equity * 100` — and the `realized` half comes from Shadow's authoritative wallet, while the `unrealized` half is again shaped by the Transformer enrichment described in R.1.1 step 8.

## R.1.3 — Other relevant handlers

- `PortfolioHandler.summary` (`/portfolio`) — `portfolio.py:16-43` — reads `position_service.get_positions()` and uses `pos.mark_price` (post-enrichment), formats `unrealized_pnl` from `pos.unrealized_pnl` (post-enrichment).
- `PortfolioHandler.balance` (`/balance`) — `portfolio.py:76-87` — reads `account_service.get_wallet_balance()` (= `ShadowAccountService` → Shadow's `/api/balance`).
- `PortfolioHandler.trade_history` (`/history`) — `portfolio.py:90-138` — reads main project's `trade_intelligence` table directly.
- `PortfolioHandler.pnl` (`/pnl`) — `portfolio.py:58-74` — reads `pnl_manager.get_summary()` returning `total_pnl_pct`, `realized_pnl`, `unrealized_pnl`, `mode`, `target_hit`.
- `dashboard_handler` (`/dashboard`, `/control`, etc.) — multiple price/PnL displays, all routed through the same enriched `position_service` and the `pnl_manager`.
- `EmergencyHandler` (`/emergency`) — `emergency.py:18-...` — reads `position_service.get_positions()` to display before bulk close.
- `MorningBriefing` — `features/morning_briefing.py:27-31` — same.

================================================================================
FILE: R2_telegram_price_source.md
================================================================================

# R2 — Telegram's Price/PnL Source Of Truth (THE ANSWER)

## R.2.1 — Unrealized PnL on open positions — definitive source

**The chain (verbatim, file:line citations):**

1. Operator sends `/positions`.
2. `_show_positions` → `position_service.get_positions()` (`control_handler.py:408`).
3. `position_service` resolved at startup to `Transformer.PositionServiceWrapper` (the `TransformedPositionService` class at `transformer.py:977-991`) wrapping `ShadowPositionService` (`shadow_adapter.py:135`).
4. `TransformedPositionService.get_positions`:

   ```python
   async def get_positions(self, symbol=None):
       positions = await self._inner.get_positions(symbol)   # ← Shadow API
       await self._t._enrich_positions_with_local_prices(positions)
       return positions
   ```
   (`transformer.py:982-985`)

5. `ShadowPositionService.get_positions` issues `GET /api/positions` to Shadow.
6. Shadow's `handle_get_positions` → `OrderEngine.get_positions()` (`shadow_client.py:213-221` → `order_engine.py:660-701`):

   ```python
   for row in rows:
       price_data = self._price_fn(row["symbol"])
       current_price = float(price_data["last"]) if price_data else row["entry_price"]
       ...
       unrealized_pct = (current_price - entry_price) / entry_price * 100   # Buy
       unrealized_usd = unrealized_pct / 100 * notional   # notional = stored fill-time notional_value
   ```

   where `_price_fn → shadow.py:get_price_data → ws_manager.get_latest_ticker(symbol)["lastPrice"]` (Shadow's OWN WS `_latest_tickers`).

7. JSON returns: `current_price` (Shadow WS), `unrealized_pnl_usd` (Shadow-computed).

8. Adapter builds `Position` dataclass with `mark_price = data["current_price"]`, `unrealized_pnl = data["unrealized_pnl_usd"]` (`shadow_adapter.py:688-700`).

9. Transformer enrichment runs:

   ```python
   local_price = await self._get_local_price(pos.symbol)     # ← ticker_cache table
   if local_price is not None:
       shadow_price = pos.mark_price
       diff_pct = (local_price - shadow_price) / shadow_price * 100
       if abs(diff_pct) > override_threshold:        # default 0.5 %
           # KEEP Shadow's price; emit PRICE_OVERRIDE warning
           continue
       pos.mark_price = local_price                  # ← OVERWRITE
       # Recalculate unrealized PnL from local price
       notional = abs(pos.size * pos.entry_price)    # ← USES entry_price * size, NOT stored notional
       if pos.side in (Side.BUY, "Buy"):
           pnl_pct = (local_price - pos.entry_price) / pos.entry_price * 100
       else:
           pnl_pct = (pos.entry_price - local_price) / pos.entry_price * 100
       pos.unrealized_pnl = pnl_pct / 100 * notional
   ```
   (`transformer.py:748-816`, abridged)

10. `_show_positions` reads `pos.mark_price` and `pos.entry_price`, recomputes pnl_pct from `(mark - entry)/entry`. The displayed value is therefore **driven by `ticker_cache.last_price`** when within 0.5 % of Shadow, and **by Shadow's WS price** otherwise.

**Source breakdown:**

| Component | Reads from |
|---|---|
| `entry_price` (display) | Shadow's `virtual_positions.entry_price` (slippage-adjusted fill) |
| `current_price` ("Now: $...") | `ticker_cache.last_price` if fresh + within 0.5 % of Shadow; else Shadow's WS `_latest_tickers["lastPrice"]` |
| `unrealized_pnl_usd` (display) | Recomputed in transformer from `pos.size * pos.entry_price * pnl_pct/100` (NOT Shadow's stored `notional_value`) when override-threshold not breached; else Shadow's value |
| `pnl_pct` (display) | Recomputed in `_build_positions_text` from `(pos.mark_price - pos.entry_price)/pos.entry_price` |

This is the divergence surface for unrealized PnL. The hypothesis "100% mostly a price-fetching difference" is correct in shape — three layered transforms, two independent live feeds, one stale fallback.

## R.2.2 — Realized PnL on closed trades — definitive source

For `/performance`:

- The "Today's PnL" shown in `/performance` reads `DailyPnLManager.current_pnl_pct` and `current_pnl_usd` (in-memory, refreshed by `pnl_manager.update()` calls scattered across handlers).
- `DailyPnLManager._recalculate` computes `current_pnl_pct = (realized_pnl + unrealized_pnl) / starting_equity * 100`.
- `realized_pnl` is fed by:
  - Wallet equity delta from `account_service.get_wallet_balance()` (= Shadow's `/api/balance`)
  - And/or per-trade summing from main project's `trade_log` table (`portfolio.py:90-104` reads `trade_intelligence`)
- `unrealized_pnl` is fed by `account_service.get_wallet_balance().unrealized_pnl` (Shadow), which is the sum of Shadow's per-position `unrealized_pnl_usd`.

For `/history`:

- `PortfolioHandler.trade_history` (`portfolio.py:90-138`) directly reads `trade_intelligence` table (main project DB):

  ```sql
  SELECT symbol, direction, pnl_pct, pnl_usd, win, strategy_name,
         hold_seconds, leverage, trade_closed_at
  FROM trade_intelligence ORDER BY id DESC LIMIT ?
  ```

- The `pnl_pct` and `pnl_usd` columns in `trade_intelligence` are populated by the trade-coordinator path on close. Concrete numeric divergence between `trade_intelligence.pnl_usd` and Shadow's `virtual_positions.net_pnl_usd` for the same trade is documented in `T1_closed_trade_forensics.md`.

**Source breakdown:**

| Display field | Reads from | Side-of-truth |
|---|---|---|
| `/performance` Daily PnL | `DailyPnLManager.current_pnl_*` | Hybrid: Shadow wallet equity + Shadow unrealized |
| `/history` per-trade pnl_usd | main project `trade_intelligence.pnl_usd` | **Diverges from Shadow** — Shadow's authoritative `virtual_positions.net_pnl_usd` differs (see T1) |
| `/portfolio` open-position unrealized | enriched `pos.unrealized_pnl` | Transformer enrichment (R.2.1) |

## R.2.3 — Same-position direct comparison

Could not be produced — no open positions exist at capture time. The reconstructive equivalent based on closed trades is in `T1_closed_trade_forensics.md`. Re-run when the next position opens; the script template:

```bash
# At time T (within 1 second):
curl -s http://127.0.0.1:9090/api/positions          # Shadow's truth
sqlite3 data/trading.db \
  "SELECT symbol, last_price, updated_at FROM ticker_cache;"   # Main's local prices
# Then capture Telegram /positions output via the bot UI
# Compare: shadow.current_price vs ticker_cache.last_price vs Telegram-displayed Now:$
```

================================================================================
FILE: S1_live_divergence.md
================================================================================

# S1 — Live Divergence Capture (single instant)

## Pre-condition status

**Pre-condition NOT MET at capture time** (2026-05-02 11:30:27 UTC).

- Shadow `/api/positions` → `{"positions": []}`
- Shadow `/api/health.positions_open` = 0
- Shadow `virtual_positions WHERE status='open'` → 0 rows
- Most recent close: ONDOUSDT at 06:29 UTC, ~5 hours before capture

Per Hard Rule 5 (document gaps explicitly): the single-instant 12-source matrix described in S.1.1 cannot be produced live in this capture window. Reconstructive equivalents using closed-trade data are in `T1_closed_trade_forensics.md`.

## S.1.1 — Capture matrix (template, to be filled at next live position)

When the next position opens, run the script in S.2 below at time T and fill:

| Source | Value | File:line of source |
|---|---|---|
| Symbol | TBD | — |
| Side | TBD | — |
| Qty | TBD | — |
| Entry price (main DB `positions` table) | TBD | `data/trading.db.positions.entry_price` |
| Entry price (Shadow `virtual_positions`) | TBD | `shadow/data/shadow.db.virtual_positions.entry_price` |
| Current price from `_ws_quotes` | NOT EXTERNALLY OBSERVABLE — see W2 A2 | `src/workers/price_worker.py:66,196` |
| Current price from `ticker_cache` table | TBD via `SELECT last_price FROM ticker_cache WHERE symbol=?` | `src/database/repositories/market_repo.py:294` |
| Current price from latest M5 kline close | TBD (timeframe label NOT IDENTIFIED — see P3) | `klines.close` |
| Current price from Shadow `/api/ticker/{sym}` | TBD via `curl http://127.0.0.1:9090/api/ticker/{sym}` | `shadow/src/api/shadow_client.py:290-312` |
| Telegram /positions reported entry | TBD via `/positions` UI | `src/telegram/handlers/control_handler.py:476` |
| Telegram /positions reported "Now" | TBD via `/positions` UI | `src/telegram/handlers/control_handler.py:476` |
| Telegram /positions reported unrealized | TBD via `/positions` UI | `src/telegram/handlers/control_handler.py:477` |
| Shadow `/api/positions[i].unrealized_pnl_usd` | TBD via `curl http://127.0.0.1:9090/api/positions` | `shadow/src/exchange/order_engine.py:680` |

## S.1.2 — Expected divergences (predicted from architecture)

Based on the read paths catalogued in P1/P2/Q2/R1/R2:

1. **Entry-price divergence main vs Shadow:** Should be ZERO. Both should record the same value because main project receives the slippage-adjusted fill in `OrderService.place_order` response and persists that. Verify against `T1` data — actually, in `T1` the main project's `trade_log.entry_price` differs from Shadow's `virtual_positions.entry_price` (rounded vs full precision, and main records pre-slippage in some cases). Concretely: ONDOUSDT — main `0.27`, Shadow `0.270081`.

2. **Current-price divergence across the four live sources:**
   - `_ws_quotes` (PriceWorker WS) — fresh (≤ 5 s monotonic age)
   - `ticker_cache` table (DB) — stale (5+ h at capture; see W2 A1)
   - Shadow's `/api/ticker/{sym}` (Shadow WS) — fresh
   - M5 kline close — up to 5 min stale by design

   Expected: `_ws_quotes` and Shadow `/api/ticker` should agree within ≤ 1 tick (both come from Bybit WS). `ticker_cache` will be 5+ hours behind the live market for symbols not recently traded.

3. **PnL divergence Telegram vs Shadow:** Predicted by Transformer enrichment math (R.2.1):
   - When `ticker_cache` is fresh **and** within 0.5 % of Shadow → Telegram shows pnl computed from `ticker_cache` price + `pos.size * pos.entry_price` notional
   - Shadow shows pnl from its WS price + stored `notional_value` (= qty * fill_price)
   - These two pnl_usd values differ by exactly the slippage-on-notional term: `qty * (fill_price - entry_price-no-slip) * (price_move_pct/entry)` — i.e. small per-position but signed

## S.1.3 — Divergence trace (predicted)

Per R.2.1 chain, when divergence appears:

- Telegram side: `pnl_pct = (mark_price_local - entry_price)/entry_price`, where `mark_price_local = ticker_cache.last_price` (potentially HOURS stale).
- Shadow side: `pnl_pct = (mark_price_shadow - entry_price)/entry_price`, where `mark_price_shadow = ws_manager._latest_tickers[sym]["lastPrice"]` (fresh).

If `ticker_cache` is stale by 5 hours (current state) and the price has moved 1 % in those 5 hours, Telegram will show pnl drift of ~1 % vs Shadow on every open position. On a $200 notional that's $2.00 — well above noise.

The `transformer.py:701-706` PRICE_STALE gate (max_age default 10 s) DOES block this when working — but only when the `ticker_cache` row exists at all. For the 42 of 50 symbols not in `ticker_cache` (8 of 50 present), `_get_local_price` returns `None`, falls through to `else: fallback_count += 1` (`transformer.py:827-832`) and Shadow's mark is preserved. So the divergence pattern depends on which subset of coins has been REST-priced recently — a non-deterministic factor.

================================================================================
FILE: S2_temporal_divergence.md
================================================================================

# S2 — Temporal Divergence (repeated capture)

## Pre-condition status

Pre-condition NOT MET — no open positions at capture (see S1).

## Capture script (to run on next live position)

Save as `dev_notes/price_source_divergence/_s2_capture.sh`, run with `bash _s2_capture.sh > s2_run_$(date +%s).txt` for 10 minutes. The script captures every source at 30 s intervals.

```bash
#!/usr/bin/env bash
# Captures the 4 live price feeds + Shadow PnL + ticker_cache state every 30s.
# Run from /home/inshadaliqbal786 .
SYM="${1:?usage: ./_s2_capture.sh SYMBOL}"
DURATION=600  # 10 min
INTERVAL=30
END=$(( $(date +%s) + DURATION ))

while [ $(date +%s) -lt $END ]; do
  T=$(date -u +%FT%TZ)
  echo "=== $T ==="
  echo "-- Shadow API --"
  curl -s http://127.0.0.1:9090/api/positions | python3 -c "
import sys,json
data=json.load(sys.stdin)
for p in data.get('positions',[]):
    if p['symbol']=='$SYM':
        print(f\"shadow current=${{p['current_price']:.6f}} entry={p['entry_price']:.6f} pnl_usd={p['unrealized_pnl_usd']:+.4f}\")
"
  echo "-- Shadow ticker --"
  curl -s "http://127.0.0.1:9090/api/ticker/$SYM"
  echo
  echo "-- main ticker_cache --"
  sqlite3 trading-intelligence-mcp/data/trading.db \
    "SELECT last_price, updated_at FROM ticker_cache WHERE symbol='$SYM';"
  sleep $INTERVAL
done
```

## Expected analysis (predicted from architecture)

After collecting 20 captures over 10 min, classify:

- **Persistent divergence (constant offset):** points to a fixed transformation difference (e.g. slippage applied one side only, or a constant fee component included on one side).
- **Fluctuating divergence (changing each capture):** points to one or both feeds having staleness — the Δ varies with how fast the market moved between the two cache writes.
- **Step-function divergence (jumps at fixed cadence):** points to one feed being on a polled/snapshotted cadence (e.g. 60 s `ticker_collector` snapshot) while the other is push-driven.

Mapping to known mechanisms:

| Pattern | Likely cause | File:line |
|---|---|---|
| Constant Δ ≈ slippage_pct × notional | main records pre-slippage entry | `order_engine.py:191-194` (Shadow), `order_service.py` (main) |
| Fluctuating Δ correlated with price move | one feed stale | `transformer.py:701-706` PRICE_STALE gate |
| Step every ~60 s on `ticker_cache` row | only REST hits update `ticker_cache`, with WS write silently failing | `price_worker.py:215-220` (the `except RuntimeError: pass`) |
| Δ jumps to 0 every time `/positions` is called | Transformer overwrite when divergence ≤ 0.5 % aligns prices, then drifts again | `transformer.py:797` |

## Without live data: closed-trade temporal proxy

The 8 closed trades in `T1_closed_trade_forensics.md` cover the same symbols traded across 1 hour (05:32 → 06:29). The main DB / Shadow DB `entry_price` deltas are constant and equal to the per-trade slippage. The realized `pnl_usd` deltas are **not** constant — they reflect divergent notional handling. This is consistent with the "fluctuating divergence" prediction.

================================================================================
FILE: T1_closed_trade_forensics.md
================================================================================

# T1 — Closed Trade Forensics

## Method

Query both DBs and join by `(symbol, closed_at within ±90 s)`. For each main-project `trade_intelligence` row, locate the closest Shadow `virtual_positions` row.

**Capture timestamp:** 2026-05-02 11:30 UTC. The 8 most recent closed trades all occurred between 04:53 and 06:29 UTC the same day.

## T.1.1 — Per-trade cross-source matrix (8 trades)

Source tags:
- **`M.ti`** = main `trade_intelligence` table (`pnl_pct`, `pnl_usd`, `entry_price`, `exit_price`, `position_size_usd`)
- **`S.vp`** = Shadow `virtual_positions` table (`entry_price`, `exit_price`, `quantity`, `notional_value`, `gross_pnl_usd`, `net_pnl_usd`, `entry_slippage_pct`, `exit_slippage_pct`, `entry_fee_usd`, `exit_fee_usd`, `close_trigger`)

| # | Symbol | Side | Closed_at (main) | M.ti entry | S.vp entry | Δentry % | M.ti exit | S.vp exit | M.ti pnl_usd | S.vp gross | S.vp net | Δpnl (M − S.net) | Close trigger (S) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ONDOUSDT | Buy | 06:29:10 | 0.27 | 0.270081 | -0.0300% | 0.269719 | 0.26971906 | **-0.2880** | -0.3710 | **-0.5232** | **+0.2352** | manual (Δexit=0; main close was `time_decay_p_win_low`) |
| 2 | MANAUSDT | Buy | 06:13:38 | 0.08952 | 0.089546856 | -0.0300% | 0.089473 | 0.08947315 | **-0.1449** | -0.2280 | **-0.3803** | **+0.2354** | manual (main: `time_decay_p_win_low`) |
| 3 | AXSUSDT | Buy | 06:05:17 | 1.3794 | 1.37981382 | -0.0300% | 1.379262 | 1.37918612 | **-0.0630** | -0.1260 | **-0.2784** | **+0.2154** | manual (main: `mode4_p9`) |
| 4 | DOGEUSDT | Sell | 05:58:36 | 0.10751 | 0.107477747 | +0.0300% | 0.107562 | 0.107562259 | **-0.6011** | -0.3537 | **-0.6011** | **+0.0000** | manual (main: `strategic_review:Position already closed/filled per exchange`) |
| 5 | AXSUSDT | Buy | 05:35:14 | 1.3848 | 1.38521544 | -0.0300% | 1.382861 | 1.38278504 | **-0.2689** | -0.3242 | **-0.4258** | **+0.1570** | manual (main: `mode4_p9`) |
| 6 | DOGEUSDT | Sell | 05:35:05 | 0.10779 | 0.107757663 | +0.0300% | 0.107842 | 0.107842343 | **-0.1256** | -0.2031 | **-0.3453** | **+0.2198** | manual (main: `mode4_p9` likely) |
| 7 | RENDERUSDT | Buy | 05:06:49 | 1.7021 | 1.70261063 | -0.0300% | 1.703389 | 1.70338883 | **-0.0515** | +0.2534 | **-0.0515** | **+0.0000** | manual |
| 8 | SANDUSDT | Sell | 04:54:07 | 0.0711 | 0.07107867 | +0.0300% | 0.071131 | 0.071131333 | **-1.4518** | -0.8332 | **-1.4518** | **+0.0000** | manual |

## T.1.2 — Pattern analysis

### Pattern A — entry-price divergence (universal, deterministic)

Every Δentry is exactly **±0.03 %** = the configured `[exchange].slippage_pct = 0.03` in `shadow/config.toml`.

- **Buy side:** Shadow's entry > main's entry by 0.03 % (slippage works against the buyer).
- **Sell side:** Shadow's entry < main's entry by 0.03 % (slippage against the seller).

Mechanism: `OrderEngine.place_order` (`order_engine.py:188-193`) applies slippage to `last_price` to derive `fill_price`, and stores `fill_price` as `virtual_positions.entry_price`. Main project records what it ASKED for (the pre-slippage `last_price` returned in the order response or refetched from `MarketService.get_ticker` at order time).

This is not a bug *per se* — it's the simulation working as designed — but it means **`trade_intelligence.entry_price` is NOT equal to Shadow's `virtual_positions.entry_price`**. Any join on entry_price fails.

### Pattern B — exit-price divergence (path-dependent)

Three closes (rows 4, 7, 8) show **Δexit = 0 to 6 decimals** AND **Δpnl_usd = 0 to 4 decimals**. These have:
- `close_trigger = manual` on Shadow side
- main project's `closed_by` = `strategic_review:...` (row 4) or just `manual`/strategic-driven on rows 7, 8

For these, main project apparently *receives* Shadow's net_pnl_usd from the close response and stores it. The `T-pattern` here is the unified path.

Five closes (rows 1, 2, 3, 5, 6) show **non-zero Δpnl** of $0.16-0.24:
- Row 1: main `closed_by = time_decay_p_win_low`
- Row 2: main `closed_by = time_decay_p_win_low`
- Row 3: main `closed_by = mode4_p9`
- Row 5: main `closed_by = mode4_p9`
- Row 6: main `closed_by = mode4_p9` (inferred from `trade_log`)

For these, main project's close path computes its own `pnl_usd` from main-side recorded prices (= the pre-slippage values) and stores **its own number**, NOT Shadow's net_pnl_usd. The Δpnl ≈ `notional × 2 × slippage_pct + entry_fee + exit_fee = $277 × 2 × 0.0003 + $0.30 + $0.15 ≈ $0.62`. The observed range $0.16-0.24 matches roughly half of that — likely main project's close path includes the exit fee but not the entry fee, or uses a smaller slippage assumption.

### Pattern C — notional divergence (rows 2 and 3)

- Row 2 (MANAUSDT): main `position_size_usd = 184.58`, Shadow `notional_value = 276.95` → ratio 1.50× = **leverage = 3** (main records margin / leverage = 184.58 = 276.95 / 1.5? — actually `notional_value/leverage = 276.95/3 = 92.32` not matching. Most likely `position_size_usd` in main is the apex_final_size adjusted by another factor.)
- Row 3 (AXSUSDT): main `184.62`, Shadow `277.00` → same 1.50× ratio.

The two systems use different notational definitions for "position size".  Main's `trade_intelligence.position_size_usd = apex_final_size` (e.g., 184.58, 184.62 — these are explicitly stored in the row's `apex_final_size` column too). Shadow's `notional_value = qty * fill_price`. They're computing different quantities.

### Pattern D — qty alignment

Quantities match exactly (`quantity = qty` at trade-coordinator level). E.g.:
- ONDOUSDT: Shadow qty=1025.0 — same as the main-project order request.
- MANAUSDT: 3092.8 — same.
- DOGEUSDT (Sell): 4185.0 — same.

Quantity is the safe joining key. Entry / exit prices are NOT.

## T.1.3 — Telegram /performance reconciliation

Could not be captured live. The expected reconciliation:

- `Today's PnL` (per /performance via `DailyPnLManager.current_pnl_pct/usd`) is fed by Shadow's `total_realized_pnl` (from `account_service.get_wallet_balance() → ShadowAccountService → /api/balance`).
- Sum of `trade_intelligence.pnl_usd` over today's 8 closes = `−0.288 + −0.145 + −0.063 + −0.601 + −0.269 + −0.126 + −0.052 + −1.452 = −2.996`
- Sum of Shadow `virtual_positions.net_pnl_usd` over the same 8 = `−0.523 + −0.380 + −0.278 + −0.601 + −0.426 + −0.345 + −0.052 + −1.452 = −4.057`
- **Δ = +$1.06** on these 8 trades alone

So if `/performance` reports total realized loss based on Shadow's `total_realized_pnl`, it shows ~$4 lost; if it sums main's `trade_intelligence.pnl_usd` it shows ~$3 lost. Same trades, two different totals. **A $1+ daily-PnL gap on 8 trades** is the operator-visible symptom.

Note: Shadow's `virtual_wallet.total_realized_pnl = -2322.05` represents the lifetime sum (not just today). The same per-trade divergence pattern accumulates across 1190 trades — so Shadow's lifetime realized PnL and any main-project lifetime sum will be off by an unbounded factor (depends how many of the 1190 went through divergent close paths).

================================================================================
FILE: U1_ipc.md
================================================================================

# U1 — Cross-Process IPC Between Main Project ↔ Shadow

## U.1.1 — Order flow main → Shadow

**Main project sends order via HTTP POST.**

- File:line where main posts: `src/shadow/shadow_adapter.py:507-510` —

  ```python
  async with self._session.post(
      f"{self._url}/api/order", json=payload
  ) as resp:
      data = await resp.json()
  ```

- Payload schema (`shadow_adapter.py:496-503`):

  ```python
  payload = {
      "symbol": symbol,
      "side": side_str,           # "Buy" or "Sell"
      "qty": qty,
      "leverage": leverage or 1,
      "sl": stop_loss,
      "tp": take_profit,
  }
  ```

- File:line where Shadow receives: `shadow/src/api/shadow_client.py:105-124` (`handle_place_order`) → calls `engine.place_order(symbol, side, qty, leverage, sl_price, tp_price)`.
- File:line where Shadow returns: `shadow/src/exchange/order_engine.py:253-264` (the `result_data` dict — `order_id, symbol, side, qty, price (= fill_price, post-slippage), status="Filled", fee, leverage, margin, notional`).
- Adapter parses response: `shadow_adapter.py:531-547` — builds `Order(price=fill_price, ...)`.

**Close flow:** `shadow_adapter.py:254-271` → `POST /api/close` → `shadow_client.py:127-142` → `OrderEngine.close_position` → returns `close_result` dict (`order_engine.py:438-462`) with `entry_price, exit_price, gross/net pnl_pct/usd, hold_duration_seconds, close_trigger`.

**Reduce flow:** `shadow_adapter.py:289-330` → `POST /api/reduce` → `shadow_client.py:145-174` → `OrderEngine.reduce_position` → returns partial-close payload.

**SL/TP modify flow:** `POST /api/set-sl` and `POST /api/set-tp`.

## U.1.2 — State queries main → Shadow

**Yes, main project queries Shadow continuously.** Main never queries Bybit for positions/balance — it only queries Shadow for the simulated portfolio state.

- **Positions:** `ShadowPositionService.get_positions` (`shadow_adapter.py:150-171`) → `GET /api/positions`. Called by every dashboard handler, by `DailyPnLManager.update`, by Layer 4 watchdog tick, by `/portfolio` / `/positions` / `/pnl` / `/emergency`.
- **Single position:** `ShadowPositionService.get_position(symbol)` (`shadow_adapter.py:173-190`) → `GET /api/position/{symbol}`. Called when watchdog needs SL/TP context for one symbol.
- **Last-close:** `ShadowPositionService.get_last_close(symbol)` (`shadow_adapter.py:192-225`) → `GET /api/position/{symbol}/last_close`. Called by watchdog after a poll-detected close to fetch authoritative exit_price/net_pnl (Bug-2 fix).
- **Balance:** `ShadowAccountService.get_wallet_balance` (`shadow_adapter.py:611-626`) → `GET /api/balance`. Called by `DailyPnLManager`, `/balance`, every dashboard refresh.
- **Health:** `health_check()` on each adapter → `GET /api/health`. Called by liveness watchdogs.

**Main does NOT query Shadow for current prices.** Main has its own PriceWorker WS feed (`_ws_quotes`), and reads `ticker_cache` SQLite for Transformer enrichment. The only place main reads "Shadow's price" is implicitly via the `current_price` field embedded in `/api/positions` response — which arrives co-bundled with the position state, not as a separate price-query call.

This is the central architectural decision behind the divergence: **main and Shadow each maintain independent live price feeds; the only point of contact is the position-state response payload, where main then OVERWRITES Shadow's price with its own.**

## U.1.3 — Data Shadow returns

For each endpoint:

| Endpoint | Returns (verbatim from `shadow_client.py`) | Where main stores |
|---|---|---|
| `POST /api/order` | `{order_id, symbol, side, qty, price (fill, post-slip), status, fee, leverage, margin, notional}` | Adapter constructs `Order` dataclass; coordinator persists to `orders` and `trade_log` tables |
| `POST /api/close` | `{symbol, side, entry_price, exit_price, qty, gross_pnl_pct/usd, exit_fee, net_pnl_pct/usd, result, close_trigger, hold_duration_seconds}` | Adapter builds `Order` (status=FILLED, price=exit_price); coordinator persists to `trade_log`/`trade_intelligence` (some fields recomputed — see T1) |
| `GET /api/positions` | `{positions: [{position_id, symbol, side, entry_price, current_price, qty, leverage, notional_value, margin_used, unrealized_pnl_pct/usd, stop_loss_price, take_profit_price, opened_at, hold_duration_seconds}, ...]}` | Adapter builds `Position` list; Transformer enrichment OVERWRITES `mark_price`, `unrealized_pnl` |
| `GET /api/position/{sym}/last_close` | full row from `virtual_positions` | Adapter returns dict; watchdog reads `exit_price`, `net_pnl_usd`, `closed_at`, `hold_duration_seconds` |
| `GET /api/balance` | `{total_equity, available_balance, margin_in_use, total_unrealized_pnl, total_realized_pnl, total_fees_paid, starting_balance, total_trades, total_wins, total_losses}` | Adapter builds `AccountInfo`; `DailyPnLManager` reads `total_equity` and `unrealized_pnl` |

================================================================================
FILE: U2_shared_storage.md
================================================================================

# U2 — Shared Storage Between Main Project and Shadow

## U.2.1 — Shared database file?

**No.** Main project uses `data/trading.db`. Shadow uses `data/shadow.db`. Verified via:

- Main config refs `data/trading.db` (default in `src/config/settings.py`)
- Shadow config: `shadow/config.toml [database] path = "data/shadow.db"` (relative to shadow root → `/home/inshadaliqbal786/shadow/data/shadow.db`)
- File listing confirms two distinct files:

  ```
  /home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db   # main
  /home/inshadaliqbal786/shadow/data/shadow.db                       # shadow
  ```

Tables in each:

- **main `trading.db`** (60+ tables): `trade_log`, `trade_intelligence`, `positions`, `orders`, `ticker_cache`, `klines`, `signals`, `regime_history`, `tias_results`, `apex_*`, etc.
- **shadow `shadow.db`** (13 tables): `daily_summary, funding_rates, klines, open_interest_history, schema_version, shadow_settings, sqlite_stat1, ticker_snapshots, tracked_coins, trade_history, virtual_positions, virtual_wallet, wallet_snapshots`

Note both have a `klines` table independently — they each persist their own kline backfill.

## U.2.2 — Shared cache, file, or shared memory?

**No shared in-memory state.** The two processes communicate strictly via HTTP on `127.0.0.1:9090` (verified `ss -tlnp` — Shadow PID 390 owns the socket; main PID 398 holds no listening socket on that port).

No pickle / json / shared-memory file path is read by both processes. Confirmed by inspecting:

- main entry `workers.py` (no shadow.db reads)
- shadow entry `shadow.py` (no trading.db reads)

The closest thing to shared persistence is `dev_notes/` (used as a working dir for forensic notes by humans/agents), but neither process reads from it.

## U.2.3 — Shared environment variables

**Yes.** Both processes inherit the systemd unit env from the same user `inshadaliqbal786`. Shared env vars likely to be:

- `BYBIT_API_KEY`, `BYBIT_API_SECRET` — both projects load Bybit creds (main for live trading; Shadow for `pybit.HTTP` REST fallback in `order_engine.py:25`)
- `TELEGRAM_BOT_TOKEN` — Shadow has its OWN bot (`shadow/src/telegram/bot.py`), main has its OWN bot (`src/telegram/bot.py`). If both use the same token they would step on each other; investigation shows the Shadow bot is enabled only when its own token is set, and they're separate per `shadow/config.toml [telegram]` block.
- `OPENROUTER_API_KEY` — main only

NOT IDENTIFIED — no exhaustive `env | sort` was captured during this collection. Investigated locations: `/etc/systemd/system/<unit>.service` Environment= lines (not opened during collection because systemd unit name was not enumerated).

================================================================================
FILE: V1_price_source_matrix.md
================================================================================

# V1 — Per-Component Price Reader Inventory Matrix

Legend for "Price source":
- **F-A** = `PriceWorker._ws_quotes` (in-mem dict, `(last_price, monotonic_ts)`, 5 s freshness gate). Path: `src/workers/price_worker.py:66, 196`. Accessor: `get_ws_quote(sym, max_age_s)` at `:239-257`.
- **F-B** = `ticker_cache` SQLite table in `data/trading.db`. Schema: `(symbol PK, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct, updated_at)`. Path: `src/database/repositories/market_repo.py:266-283` (write), `:294-296` (read).
- **F-C** = `MarketService._ticker_cache` (in-mem dict, REST-fed). 5 s TTL. Path: `src/trading/services/market_service.py:45, 60-68`.
- **F-D** = Shadow's `WebSocketManager._latest_tickers` (in-mem). Path: `shadow/src/collector/websocket.py:43, 327`. Accessed within Shadow process only — main reaches it via `/api/positions` `current_price` field.
- **F-E** = `klines.close` (DB). Used only by historical/structure analytics, not live "current price."

| Component | File:line | Price Source | Staleness Profile | Used For |
|---|---|---|---|---|
| **Layer 1A** | | | | |
| PriceWorker (writer) | `src/workers/price_worker.py:196` | writes F-A | n/a | Source of truth for main's WS quotes |
| PriceWorker save_ticker side-write | `src/workers/price_worker.py:218` | writes F-B (best effort, often **fails silently** — see W2 A1) | n/a | Mirror to DB; **broken in practice** |
| KlineWorker | `src/workers/kline_worker.py` (full) | writes `klines` (F-E) | M5+ cadence | OHLCV persistence |
| AltDataWorker | `src/workers/altdata_worker.py` | does NOT use price for trade decisions; writes alt-data tables | n/a | Sentiment/funding/OI/news context |
| **Layer 1B** | | | | |
| StructureWorker | `src/workers/structure_worker.py` | reads OHLCV from `klines` (F-E) | finished candle | FVG/OB calc |
| SignalWorker | `src/workers/signal_worker.py` | reads OHLCV (F-E) and indicators | finished candle | Indicator outputs |
| RegimeWorker | `src/workers/regime_worker.py` | reads OHLCV (F-E) | finished candle | ATR/ADX, regime classification |
| **Layer 1C** | | | | |
| StrategyWorker | `src/workers/strategy_worker.py` | reads OHLCV (F-E) + signals | finished candle | Strategy outputs |
| TradeScorer | `src/workers/scanner/state_labeler.py:21` (docstring), `scanner_worker.py:704` | reads F-C via `market.get_ticker_cached` | 5 s | Score adj for current-price |
| **Layer 1D** | | | | |
| ScannerWorker | `src/workers/scanner_worker.py:703-704` | reads F-C via `market_service.get_ticker_cached` | 5 s TTL on F-C | Build CoinPackage `current_price` |
| CoinPackageValidator | `src/strategies/scanner.py` (and `validators/coin_package_validator.py`) | reads `CoinPackage.current_price` (carries F-C value) | inherited 5 s | Stale-check; reject if too old |
| **Stage 2 — Brain** | | | | |
| Strategist (prompt builder) | `src/strategies/strategist.py` | reads `CoinPackage.current_price` (F-C) | inherited | Brain prompt context |
| Claude CLI | downstream consumer of prompt | reads price string in prompt | inherited | Read-only |
| **Layer 3 — Execution** | | | | |
| APEX assembler | `src/apex/assembler.py:147-148` | F-A (`price_worker.get_ws_quote(sym, max_age_s=5.0)`) | 5 s monotonic | Build LayerSnapshot price |
| APEX gate | `src/apex/gate.py` | reads APEX snapshot (carries F-A value) | inherited | Entry validation |
| TradeGate | `src/trading/...` (across modules) | reads `Position.entry_price`, `Ticker.last_price` from F-C | 5 s | Size validation |
| OrderService | `src/trading/services/order_service.py` | F-C via `market_service.get_ticker(symbol)` | 5 s | Pre-flight max-loss check, fee est. |
| Bybit Client | `src/trading/client.py` | n/a (sends order, not prices) | n/a | Real-money path (when wired) |
| Shadow Adapter (place_order) | `src/shadow/shadow_adapter.py:507-510` | sends qty/SL/TP only, no price | n/a | Shadow does its own pricing on receive |
| **Layer 4 — Watchdog** | | | | |
| PositionWatchdog (multiple sites) | `src/workers/position_watchdog.py:567,624,1028,1046,1146,1166,1177,1231,1233,1315,1335,1440-1442,1451,1570,1591,1653,1664,1755,1807,1808,2008,2040-2068,2322,2502,2510` | mix of `pos.mark_price` (set by Shadow → potentially Transformer-overwritten) and `ticker.last_price` (= F-C from `market_service.get_ticker(symbol)`) | mixed | SL/TP eval; trailing stops; Mode4 ladder; close-trigger price; PnL display |
| ProfitSniper (M4) | `src/workers/profit_sniper.py:94` (docstring), body uses `market_service.get_ticker` | F-C | 5 s | Mode4 ladder evaluation |
| RecoveryPlanner | `src/workers/...` | reads Position objects → `pos.mark_price` | inherited (Transformer-overwritten if recent) | Risk reassessment |
| **Layer 5 — TIAS** | | | | |
| TIAS | `src/tias/...` | reads `entry_price`, `exit_price` from `trade_intelligence` (frozen at close) | static | Post-trade analysis (no live price) |
| **Telegram Bot** | | | | |
| /positions (`_show_positions` / `_build_positions_text`) | `src/telegram/handlers/control_handler.py:400-477` | `pos.mark_price` after Transformer enrichment (F-B if fresh + within 0.5 % of F-D, else F-D) | depends on F-B freshness | Display |
| /performance | `src/telegram/handlers/dashboard_handler.py:1037-1157` | `DailyPnLManager.current_pnl_*` (in-mem), fed by Shadow `/api/balance` | depends on `pnl_manager.update()` cadence | Display |
| /portfolio | `src/telegram/handlers/portfolio.py:16-43` | enriched `pos.mark_price` (F-B/F-D mix) | inherited | Display |
| /history | `src/telegram/handlers/portfolio.py:90-138` | reads `trade_intelligence` table | static | Display closed trades (per T1, these disagree with Shadow's net) |
| /balance | `src/telegram/handlers/portfolio.py:76-87` | Shadow `/api/balance` (no price involved) | n/a | Display |
| /pnl | `src/telegram/handlers/portfolio.py:58-74` | `DailyPnLManager.get_summary()` | inherited | Display |
| **Reconciler** | | | | |
| FundReconciler | `src/workers/fund_reconciler.py` | reads Shadow `/api/balance` and `/api/positions` (no direct price) | n/a | Reconciliation |
| **Sentiment** | | | | |
| Sentiment aggregator | `src/intelligence/sentiment/aggregator.py:169-175` | F-B (`SELECT change_24h_pct FROM ticker_cache`) | up to 5+ h stale (see W2 A1) | Sentiment momentum overlay |
| **Freshness** | | | | |
| FreshnessGuard | `src/core/freshness_guard.py:35-36` | F-C via `market_svc._ticker_cache.get(symbol)` | 5 s | Decide if a price reading is fresh enough |

## Summary by feed

- **F-A consumers:** APEX assembler (1 caller). Few — F-A is reserved for the trading-decision path that needs the freshest possible WS price.
- **F-B consumers:** Transformer enrichment (the ALL-IMPORTANT one — drives the Telegram display), sentiment aggregator. Two main consumers, one of which feeds the operator dashboard.
- **F-C consumers:** ScannerWorker, OrderService, PositionWatchdog (live reads), ProfitSniper, FreshnessGuard, TradeScorer. The most-read feed.
- **F-D consumers:** Shadow's own `OrderEngine.place_order/close_position/get_positions`, `PositionMonitor`, `/api/ticker/{sym}`. The price every Shadow internal computation uses.
- **F-E consumers:** StructureWorker, SignalWorker, RegimeWorker, StrategyWorker, KlineCollector. Historical/structural — not live "current price."

The critical observation: a single trading decision can sit downstream of F-A (APEX gate) and F-C (sizer) and F-D (executed by Shadow) and F-B (displayed to operator after Transformer enrichment). Four different live "current prices" can simultaneously be the basis for one trade's lifecycle.

================================================================================
FILE: W1_e2e_trace.md
================================================================================

# W1 — End-to-End Price Trace (single Bybit WebSocket message)

Trace the journey of one ticker tick (e.g., BTCUSDT lastPrice update) through both processes.

## Topology

```
                        Bybit WSS endpoint  (config.bybit.ws_url)
                                 |
             two independent TCP connections:
                                 |
       ┌─────────────────────────┴───────────────────────────┐
       │                                                       │
   PID 398 (main)                                          PID 390 (shadow)
   pybit.WebSocket                                         websockets.client
       │                                                       │
       v                                                       v
 _handle_ticker_update                                _handle_ticker_message
 (price_worker.py:161)                                (websocket.py:313)
       │                                                       │
       ├─→ self._ws_quotes[sym] = (price, monotonic())          ├─→ self._latest_tickers[sym] = {…lastPrice…}
       │   (price_worker.py:196)                                 │   (websocket.py:325-327)
       │                                                         │
       └─→ loop.create_task(self.market_repo.save_ticker(...))  └─→ on_ticker callbacks (TickerCollector etc.)
           (price_worker.py:218 — often raises RuntimeError      │
            inside thread-pool callback → silently swallowed)    └─→ TickerCollector batches DB writes every 60s
                                                                     (ticker_collector.py:94-103)
```

## Per-consumer "see it" timeline (for one tick at T+0)

| Consumer | Process | "Sees it" at | Why |
|---|---|---|---|
| `_ws_quotes` mutation | main (callback thread) | T+~1 ms | direct dict assign |
| Shadow `_latest_tickers` mutation | shadow | T+~1 ms | direct dict assign |
| `ticker_cache` SQLite row | main (asyncio loop) | usually **NEVER** | the `loop.create_task(...)` raises RuntimeError (no loop on pybit thread); when called from `MarketService._fetch_ticker` via REST, written immediately |
| `ticker_snapshots` SQLite | shadow | T+(0..60 s) | TickerCollector snapshots WS cache every `ticker_snapshot_interval` (default 60 s) |
| APEX assembler reading via `get_ws_quote` | main | next assembler call (event-driven, on-demand) | reads `_ws_quotes` directly, in-process, 5 s gate |
| StructureWorker / SignalWorker / RegimeWorker | main | next sweet-spot tick | reads OHLCV from `klines` (lagging) |
| ScannerWorker reading via `market.get_ticker_cached` | main | next 30 s scan tick | uses F-C (REST cache, 5 s TTL) |
| Brain prompt (CoinPackage `current_price`) | main | next strategist tick (per cycle, ~5 min) | inherits from ScannerWorker's CoinPackage |
| PositionWatchdog | main | next watchdog tick (per worker interval) | uses F-C live ticker reads |
| Shadow `OrderEngine.get_positions` | shadow | every `/api/positions` call | reads F-D directly per position |
| Shadow `PositionMonitor` | shadow | next monitor tick (1 s default) | reads F-D for SL/TP eval |
| Telegram /positions display | main | per `/positions` invocation | reads `position_service.get_positions()` → Shadow API → Transformer enrichment → F-B substitution |

## Network / protocol detail

- Bybit WSS: `wss://stream.bybit.com/v5/public/linear` (mainnet)
- Topic format: `tickers.{symbol}` for ticker stream
- Both processes subscribe to the SAME 50 symbols (main pulls from `settings.universe.watch_list`, Shadow pulls from `coin_selector.select_top_coins(config.collector.coin_count)` → typically the same set after Shadow's startup orphan-merge at `shadow.py:132-154`)
- Two TCP connections to Bybit means two slightly different sequences of packets — within a few ms typically, but a packet drop or jitter on one side creates a transient divergence

## Push vs pull

- **Push (callback-driven) consumers:** `_ws_quotes` (main), `_latest_tickers` (Shadow), Shadow's TickerCollector callbacks
- **Pull (polled) consumers:** all readers in main except APEX assembler
- **Hybrid:** PositionWatchdog pulls live ticker (F-C, REST-fed) every tick AND uses `pos.mark_price` set by Shadow's push (via `/api/positions` response)

================================================================================
FILE: W2_anomalies.md
================================================================================

# W2 — Anomaly Catalog

For each anomaly: **expected** vs **observed** vs **origin (file:line)** vs **downstream impact**.

---

## A1 — `ticker_cache` is silently 5+ hours stale despite WS being healthy

**Expected:** PriceWorker's WS callback writes every ticker tick to `ticker_cache` via `market_repo.save_ticker(...)` at `price_worker.py:218`. With ~50 symbols and ~100 WS msgs/sec aggregate, `ticker_cache` should have all 50 rows updated within a few seconds and a continuous refresh thereafter.

**Observed:** at capture time 2026-05-02 11:30:27 UTC, `ticker_cache` contains **only 8 rows**, all with `updated_at` between 05:18 and 06:30 UTC — **5+ hours stale**. The 8 symbols are exactly the symbols traded today (rows written by `MarketService._fetch_ticker` REST path at `market_service.py:101`).

**Origin (file:line):** `src/workers/price_worker.py:215-220`

```python
import asyncio
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self.market_repo.save_ticker(ticker))
except RuntimeError:
    pass         # ← swallows the "no running event loop" exception
                  #   in the pybit thread-pool callback context
```

The pybit `WebSocket.subscribe_ticker` callback is invoked on a `pybit`-internal thread that has NO asyncio event loop attached. `asyncio.get_running_loop()` raises `RuntimeError`. The bare `except` catches it and drops the write. There is no log line. There is no metric. The DB write simply never happens.

**Downstream impact (high):**
- Transformer enrichment (`transformer.py:716-841`) reads `ticker_cache` to derive `local_price`. For the 42 of 50 symbols never REST-fetched today, `_get_local_price` returns `None` → fallback to Shadow's price (no override). For the 8 stale-by-5h symbols, the PRICE_STALE gate at `transformer.py:701-706` (max_age 10 s) DOES fire → returns `None` → also falls back. So in current state, Transformer enrichment is essentially a no-op due to A1. Whenever a fresh REST fetch lands on a symbol (e.g. when a new order is placed), that one row becomes briefly fresh and Transformer enrichment kicks in for that symbol — producing a **per-symbol bursty divergence** every time an order opens.
- Sentiment aggregator (`aggregator.py:169-175`) reads `change_24h_pct` from `ticker_cache` — same staleness issue.
- A "PRICE_STALE" warning is logged every divergent /positions call, generating log noise.

---

## A2 — Two independent Bybit WebSockets running in two processes

**Expected:** one canonical WS feed for the whole system, with downstream components reading a single price source.

**Observed:**
- main process (PID 398) opens its own `pybit.unified_trading.WebSocket` (`src/trading/websocket.py` wraps it; subscribed at `price_worker.py:111`).
- Shadow process (PID 390) opens its own raw `websockets.client` connection (`shadow/src/collector/websocket.py:199-203`).
- Each connection produces its own packet stream from Bybit, each maintains a separate cache, each ticks at slightly different microseconds.

**Origin:**
- main: `src/workers/price_worker.py:110-111` connects + subscribes
- shadow: `shadow/src/collector/websocket.py:141-165` `run()` opens both ticker and kline connections
- Architecture decision predates this collection — Shadow was originally a "data warehouse" project that grew its own price feed; main was added later but kept its own.

**Downstream impact (foundational):** every other anomaly compounds because the system has two parallel sources of truth that drift continuously. Without merging or a shared cache, no enrichment / override scheme can fully reconcile them.

---

## A3 — Transformer enrichment recomputes `unrealized_pnl` with a different notional definition than Shadow stores

**Expected:** when overwriting `pos.mark_price` with `local_price`, recompute `unrealized_pnl` using the SAME `notional` Shadow used at fill — i.e. `position.notional_value` carried in the API response.

**Observed:** at `src/core/transformer.py:815`:

```python
notional = abs(pos.size * pos.entry_price)
```

This uses `pos.entry_price` (the slippage-adjusted entry from Shadow) and `pos.size` (= `quantity`). For Buy: `notional = qty × entry_price`. For Sell: same. This **happens to match** Shadow's `notional_value = qty × fill_price` because `entry_price == fill_price` in Shadow. So the recomputation is numerically equivalent **in this respect**.

But: Transformer's pnl_pct uses `local_price` (= F-B, possibly stale or possibly recently REST-fed) versus Shadow's pnl_pct which uses `current_price` (= F-D, freshest WS). The two pct numbers can differ even when notionals agree.

**Downstream impact (medium):** when `local_price` and `shadow_price` are within 0.5 % (the override threshold), Transformer overwrites — and the displayed pnl changes from Shadow's WS-derived value to F-B-derived value. When they diverge by >0.5 %, Shadow's value is kept. Boundary effects: a position whose `local_price` happens to drift from 0.499 % to 0.501 % causes the displayed pnl to suddenly jump from one number to another even though no real price changed — a discontinuity in the UI.

---

## A4 — Shadow's `OrderEngine.get_positions` falls back to `entry_price` (PnL = 0) when WS hasn't ticked

**Expected:** if no fresh price is available, refuse to compute pnl — surface a "no price" sentinel.

**Observed:** at `shadow/src/exchange/order_engine.py:670`:

```python
current_price = float(price_data["last"]) if price_data else row["entry_price"]
```

When `price_data is None` (WS never ticked since position opened, or WS dropped this symbol), `current_price = entry_price` → `unrealized_pct = 0` → `unrealized_usd = 0`. The position appears to be exactly break-even when in reality there's no live data.

**Downstream impact (low under normal conditions, high under WS drop):** masks real PnL during WS outages. Watchdog reads `mark_price` and decides "no SL/TP trigger needed." Operator dashboard says "open position is flat" when it could be deep in either direction.

---

## A5 — Main project's close path computes its own `pnl_usd` instead of using Shadow's `net_pnl_usd` for `time_decay_*` and `mode4_*` triggers

**Expected:** Shadow is the simulation's source of truth for fill prices and PnL. Main project should persist Shadow's `net_pnl_usd` verbatim into `trade_intelligence.pnl_usd`.

**Observed (per `T1_closed_trade_forensics.md` row analysis):** for trades closed via `manual` / `strategic_review` triggers (rows 4, 7, 8 in T1), `trade_intelligence.pnl_usd == virtual_positions.net_pnl_usd` exactly. For trades closed via `time_decay_p_win_low` or `mode4_p9` (rows 1, 2, 3, 5, 6 in T1), main records its own pnl_usd computed from main-side prices (= pre-slippage) — the Δ is +$0.16 to +$0.24 per trade.

**Origin (file:line):** the divergent close path lives somewhere in main project's close-coordinator (likely `src/workers/profit_sniper.py` for mode4 triggers and `src/workers/position_watchdog.py` for time_decay; the `trade_log` `close_reason` matches those). Both paths construct a `trade_intelligence` row themselves rather than fetching it from Shadow's `/api/position/{sym}/last_close` endpoint (which exists *specifically for this purpose* per `shadow_adapter.py:192-225` — but is only used by the watchdog for the post-close detection path, not for these self-initiated closes).

**Downstream impact (high):**
- `trade_intelligence` lifetime sums diverge from Shadow's `virtual_wallet.total_realized_pnl` — verified non-zero at $1.06 across 8 trades; extrapolates to potentially $100+ across the 1190-trade lifetime.
- `/performance` and `/history` show different numbers depending on which view the operator looks at: `/performance` reads `pnl_manager.current_pnl_pct` which is fed by Shadow wallet; `/history` reads `trade_intelligence.pnl_usd` which has its own value.
- TIAS feedback loop is fed `trade_intelligence` rows — so the AI's lessons-learned input has biased PnL.

---

## A6 — `trade_intelligence.position_size_usd` ≠ Shadow `notional_value` (by ~50 % on some trades)

**Expected:** "position size" should mean the same thing in both places.

**Observed:** for MANAUSDT (T1 row 2) and AXSUSDT (T1 row 3), `position_size_usd ≈ notional_value / 1.5`. For ONDOUSDT (T1 row 1), they match. The 1.5× ratio for those two corresponds to leverage=3 → suggests `position_size_usd` for those rows is `notional / leverage = margin_required`, NOT notional. For ONDOUSDT (lev=2) the ratio would be 0.5× — but they happen to be equal there. Likely the field semantics changed during a refactor and old rows have one meaning and new rows have another. NOT IDENTIFIED — the column source is `apex_final_size` per the `trade_intelligence.apex_final_size` field, which APEX records as a USD risk number.

**Downstream impact (low for current bug, but a separate inconsistency):** anyone aggregating "total position size today" gets a mixed-units number.

---

## A7 — `position_card` formatter and `_build_positions_text` use different formulas

NOT VERIFIED in this collection. `PortfolioHandler.positions` calls `position_card(pos)` (`portfolio.py:53`); `_show_positions` builds via `_build_positions_text` (`control_handler.py:433`). The two helpers may render the same Position with subtly different formulas. Worth a quick check by the next collector pass. Likely identical to within a sign convention.

---

## A8 — Shadow's `_latest_tickers` is unbounded (no TTL eviction)

**Expected:** stale entries should expire so the cache reflects only live data.

**Observed:** `shadow/src/collector/websocket.py:43-44` defines `_latest_tickers` and `_ticker_timestamps` as plain dicts with no TTL. `get_ticker_age()` at `:121-126` exposes age but `_latest_tickers` never evicts. If a coin falls out of subscription, its last entry persists indefinitely.

**Downstream impact (low):** rare in practice because reconnect rebuilds the subscription set, so most entries are continuously refreshed. But during a WS drop, downstream readers (`OrderEngine.get_positions`, `/api/ticker`) receive arbitrarily-old prices with no warning.

---

## Catalog summary — root causes ranked by impact

1. **A2** — two-WebSocket architecture (foundational; every other anomaly compounds)
2. **A1** — silent failure of WS→ticker_cache write path (causes Transformer enrichment to be effectively non-functional except at random instants)
3. **A5** — main-side close path producing its own pnl for time_decay and mode4 triggers (the root cause of operator-visible /performance vs /history divergence)
4. **A3** — Transformer-vs-Shadow notional/price mismatch (continuous low-amplitude drift)
5. **A4** — `OrderEngine.get_positions` `else row["entry_price"]` fallback (silently flatlines pnl during WS outages)
6. **A6, A7, A8** — secondary inconsistencies, lower priority for the operator's stated symptom
