# 03 — Worker Database Access Map

Target: `src/workers/` (28 worker files).
Synthesis source: Phase-1 Explore agent over every worker file, cross-referenced with audit Chapter 3.

## 1. Per-worker access summary

| Worker | Tick | Reads | Writes | Ops/tick | Critical | DM/Repo |
|---|---|---|---|---|---|---|
| allocation_worker | 300 s | — | — | 0 | bg | — |
| altdata_worker | 300 s | — | — | 0 (via repo) | bg | altdata_repo |
| backtest_worker | 3600 s | factory tables | — | 1-2 (on demand) | bg | backtest_repo, factory_repo |
| base_worker | n/a | — | — | 0 | n/a | — (framework) |
| bybit_demo_ws_worker | event | — | — | 0 | bg (WS feeder) | — (in-memory) |
| cleanup_worker | 3600 s | 11 retention scans | DELETE × tables + INSERT OR REPLACE daily_summary | 11+ retention + 1 rollup + PRAGMA ops + checkpoint | bg | direct DM |
| discovery_worker | 7200 s | — | strategies, patterns | 1-5 (on schedule) | bg | factory_repo, learning_repo |
| enforcer_worker | 300 s | — | — | 0 | bg | — (calls services) |
| fund_manager_worker | 60 s | — | — | 0 | bg | — (in-memory) |
| fund_reconciler | 60 s | — | — | 0 | bg | — |
| kline_worker | 300 s (sweet 0:30) | staleness scan | klines chunked exm | 1 fetch + chunked writes (50 symbols × M5/H1/H4/D1) | **CP** | market_repo |
| live_monitor_worker | varies | — | — | 0 | bg | — |
| manager (worker manager) | event | orders/positions/trade_log | INSERT OR REPLACE / UPDATE / DELETE per fill/close | event-driven | **CP** | trading_repo |
| news_worker | varies | — | news_articles | per-article (~30/hr) | bg | news_repo |
| optimization_worker | 3600 s | — | — | 0 (on schedule) | bg | — |
| position_watchdog | 10 s | klines, ticker_cache, trade_thesis, account_snapshots | trade_thesis, sniper_log | 5-10 reads + 1-2 writes per position per tick | **CP** | market_repo + direct DM |
| price_alert_worker | 10 s | — (in-memory) | (UPDATE if alerts exist; 0 rows today) | 0 | bg | telegram_repo |
| price_worker | 45 s | ticker_cache fallback | ticker_cache (via buffer) | 0-1 (buffered) | **CP** | market_repo |
| profit_sniper | 5 s | klines, position cache | sniper_log INSERT/UPDATE | 1 write per 5-20 ticks | **CP** | direct DM |
| reddit_worker | varies | — | reddit_posts | per-post | bg | sentiment_repo |
| regime_worker | 300 s (sweet 1:15) | coin_regime_history restore | regime_history (global) + 50 per-coin INSERT + retention DELETE | 1 restore + 2 writes per tick | bg | direct DM |
| scanner_worker | 300 s (sweet 4:00) | — | active_universe (DELETE + executemany INSERT OR REPLACE 30 rows) | 1 DELETE + 1 exm | cycle trigger | direct DM |
| scheduled_report_worker | 300 s | scheduled_reports (0 rows today) | — | 0 | bg | telegram_repo |
| signal_worker | 300 s (sweet 1:00) | — | signals (via altdata_repo) | per-signal | bg | altdata_repo |
| strategy_worker | 30 s | — | — | 0 (via services) | bg | — |
| structure_worker | 300 s (sweet 0:45) | klines batch | — | 1-2 batch reads | bg | market_repo |
| sweet_spot_scheduler | n/a | — | — | 0 | scheduler | — |
| telegram_bot_worker | event | price_alerts, scheduled_reports (polled), other reads on /commands | conversation_log on every /chat | event-driven; poll every 30 s | bg | telegram_repo + direct DM |
| ticker_cache_buffer | 500 ms | ticker_cache fallback | ticker_cache batched executemany | 1 exm flush per 500 ms (≤50 rows) | support | market_repo |
| trial_monitor_worker | 3600 s | — | — | 0 (on schedule) | bg | — |
| worker_liveness_watchdog | 30 s | — | — | 0 | utility | — |

Direct DB-touching workers count: 13 of 28 (kline, ticker_cache_buffer, profit_sniper, position_watchdog, regime, scanner, cleanup, telegram_bot, discovery, news, reddit, structure, manager).

Critical-path workers (must not block live decisions): kline_worker, profit_sniper, position_watchdog, price_worker, manager.

## 2. Aggregate operation rates (steady state estimate)

Reads per minute (steady state):

- profit_sniper: 12/min (5 s tick, most reads from in-memory ring buffer; klines fetch every N ticks)
- position_watchdog: ~6/min (10 s tick, 1-2 prefetch batches + per-position lookups)
- ticker_cache_buffer (read fallback only): ~1/min
- structure_worker: 0.2/min (kline batch every 5 min)
- cleanup_worker: 0.17/min (hourly retention scans)
- on-demand telegram/MCP dashboards: variable, peak ~10 concurrent on /dashboard

Subtotal: ~18-25 reads/min steady-state, with bursts of 10+ concurrent reads when a dashboard refreshes.

Writes per minute (steady state):

- ticker_cache_buffer: 2/min (one flush per 500 ms, but executemany of ~50 rows)
- kline_worker: 0.2/min (one tick every 5 min, but with chunked executemany of ~9000 rows in chunks of 500)
- profit_sniper: 2/min (sniper_log INSERT every ~30 s; spike to 12/min during active trades)
- position_watchdog: 6/min (trade_thesis UPDATE per open position per 10 s; with 9 open positions that's 54 writes/min)
- regime_worker: 0.4/min (51 rows per 5-min tick = ~10/min)
- scanner_worker: 0.2/min (DELETE + executemany 30 rows per 5-min tick)
- cleanup_worker: 0.17/min (hourly retention DELETEs across many tables)
- news_worker / reddit_worker / sentiment / altdata: ~30/hr ≈ 0.5/min combined
- manager (event-driven on fills): variable, 0-10/min during active trading

Subtotal: ~11-65 writes/min depending on trade activity.

## 3. Read-heavy workers (eligible for read pool)

These workers only read or are dominated by reads on the hot path:

- profit_sniper (reads klines every tick; writes sniper_log periodically — but writes are small and infrequent)
- position_watchdog (reads klines/thesis/orders every 10 s; writes thesis updates)
- structure_worker (klines reads only)
- backtest_worker (factory reads only)
- telegram_bot_worker (handler reads on demand; only conversation_log writes)

## 4. Write-heavy workers (must hit writer)

- kline_worker (chunked executemany large)
- ticker_cache_buffer (executemany every 500 ms)
- profit_sniper (sniper_log writes)
- position_watchdog (thesis updates)
- regime_worker (51-row burst per cycle)
- scanner_worker (DELETE + executemany)
- cleanup_worker (retention DELETEs + checkpoint)
- manager (per-fill writes)

## 5. Mixed (must use both)

- position_watchdog (klines/thesis reads + thesis writes)
- regime_worker (history restore reads + history writes)
- discovery_worker (factory reads + factory writes)

## 6. Lock-hold patterns that drive cascades today

From audit Chapter 5 and verified against the slow workers in our Phase 0 baseline:

- kline_worker chunked executemany on 50 symbols × 4 timeframes × up to 200 candles: each chunk is fast (~50ms in the lock), but cumulative through the 9000-row batch can hold the lock long enough to back up sub-5-s workers.
- ticker_cache_buffer flush: bounded to ~50 rows; small per-flush but cumulative since flushes are 2/s.
- WAL checkpoint contention: hourly PASSIVE checkpoint normally fast, but `WAL_CHECKPOINT_BUSY` warns when reader pinned a snapshot.
- price_alerts poll on a 0-row table: trivially fast (index lookup returns nothing), but every 30 s acquires the lock and contributes to the queue depth.

## 7. Implications for the refactor

- No worker file changes in Phase 3.1-3.6. Every worker calls `db.execute`/`db.fetch_all`/etc. which transparently routes to writer or reader on the pooled path.
- Cleanup_worker (`src/workers/cleanup_worker.py`) DOES change in Phase 3.3 — it extends its existing `log_lock_histogram()` call to also emit `CONN_POOL_STATS` when running pooled.
- The 13 direct DB-touching workers all benefit; reads no longer block reads, writes don't block reads, and reads don't block the writer (other than the in-flight write itself).
- Critical-path workers (profit_sniper, position_watchdog, kline_worker) move from "competing with every other worker for one lock" to "competing only with other writers for the write lock, never with reads."

End of `03_worker_access_map.md`.
