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
