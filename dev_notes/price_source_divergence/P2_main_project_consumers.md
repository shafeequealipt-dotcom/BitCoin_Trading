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
