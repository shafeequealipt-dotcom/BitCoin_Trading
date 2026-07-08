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
