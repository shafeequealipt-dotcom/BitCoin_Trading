# 02 — Repository Layer Inventory

Target: `src/database/repositories/` (11 files, 2,202 lines, 85 methods).
Synthesis source: Phase-1 Explore agent over each file.

## 1. Per-repository inventory

### altdata_repo.py (341 lines, 12 methods)

| Method | Op | API | Tables |
|---|---|---|---|
| `save_fear_greed(data)` | W | execute | fear_greed_index |
| `get_latest_fear_greed()` | R | fetch_one | fear_greed_index |
| `get_fear_greed_history(days, limit)` | R | fetch_all | fear_greed_index |
| `save_funding_rate(rate)` | W | execute | funding_rates |
| `get_funding_rates(symbol, hours)` | R | fetch_all | funding_rates |
| `get_latest_funding_rate(symbol)` | R | fetch_one | funding_rates |
| `save_open_interest(symbol, oi_value)` | W | execute | open_interest |
| `get_open_interest(symbol, hours)` | R | fetch_all | open_interest |
| `get_latest_open_interest(symbol)` | R | fetch_one | open_interest |
| `_compute_oi_delta_pct(symbol, current_oi, lookback_hours)` | R | fetch_one | open_interest |
| `save_signal(signal)` | W | execute | signals |
| `get_latest_signal(symbol)` | R | fetch_one | signals |

Reads 6, writes 4, mixed 0. No transactions. No SQLite-specific syntax.

### backtest_repo.py (99 lines, 7 methods)

| Method | Op | API | Tables | SQLite |
|---|---|---|---|---|
| `save_result(result)` | W | execute | backtest_results | INSERT OR REPLACE |
| `save_trades(backtest_id, trades)` | W | execute (loop) | backtest_trades | — |
| `get_result(result_id)` | R | fetch_one | backtest_results | — |
| `get_results_for_strategy(strategy_id)` | R | fetch_all | backtest_results | — |
| `save_lifecycle_transition(...)` | W | execute | strategy_lifecycle | — |
| `get_lifecycle_history(strategy_id)` | R | fetch_all | strategy_lifecycle | — |
| `save_trial_performance(...)` | W | execute | trial_performance | INSERT OR REPLACE |

Reads 3, writes 4. `save_trades` loops per-row (potential batch-opportunity).

### context_repo.py (140 lines, 14 methods)

Preferences, watchlists, active_strategies, session_log. 7 reads / 7 writes. 3x INSERT OR REPLACE.

### factory_repo.py (193 lines, 11 methods)

discovered_patterns, generated_strategies, pattern_occurrences. 6 reads / 5 writes. 2x INSERT OR REPLACE, 3x datetime('now').

### learning_repo.py (190 lines, 13 methods) ⚠ race-condition risk

13 methods across strategy_performance, signal_accuracy, pattern_log, brain_decisions. 2 reads, 8 writes, 3 read-modify-write WITHOUT explicit transaction:

- `update_strategy_stats(strategy, symbol, pnl, was_win)` — fetch_one then execute UPDATE.
- `update_signal_outcome(signal_id, actual_direction, prices)` — fetch_one then execute UPDATE.
- (also `update_pattern_outcome` is single-statement, no race risk.)

These three are latent lost-update risks under any concurrency model. In current production they run from `discovery_worker` on a multi-hour cadence and are single-coroutine, so the race is theoretical. Phase 5 candidate for writer-locked transactional wrapping if Phase 4 reveals contention.

### market_repo.py (420 lines, 8 methods)

| Method | Op | API | Tables | Notes |
|---|---|---|---|---|
| `save_klines(klines)` | W | executemany (chunked 500) | klines | INSERT OR IGNORE, sleep(0) between chunks |
| `get_klines(symbol, timeframe, limit)` | R | fetch_all | klines | — |
| `get_klines_batch(symbols, timeframe, limit)` | R | fetch_all | klines | ROW_NUMBER() window |
| `save_ticker(ticker)` | W | execute | ticker_cache | INSERT OR REPLACE |
| `save_tickers_batch(tickers)` | W | executemany | ticker_cache | INSERT OR REPLACE |
| `attach_ticker_buffer(buffer)` | — | — | — | Configures TickerCacheBuffer |
| `get_ticker(symbol)` | R | fetch_one | ticker_cache | Consults buffer first |
| `save_orderbook(symbol, bids, asks)` | W | execute | orderbook_snapshots | — |

Reads 4 (one cache-backed), writes 4. The two batched writes (`save_klines`, `save_tickers_batch`) implement the chunking with `asyncio.sleep(0)` between chunks — the post-D-3 fix designed to bound per-chunk lock-hold time on the legacy single-lock model. Under the pooled model these will still yield (politeness), but they no longer share a lock with reader operations.

### news_repo.py (259 lines, 7 methods)

news_articles, economic_calendar. 5 reads / 2 writes. INSERT OR IGNORE for dedup. `get_by_symbol` and `search` use `LIKE '%symbol%'` — the audit's slow-query case 3.

### portfolio_repo.py (67 lines, 5 methods)

portfolio_allocations, correlation_matrix, rebalance_history, stress_test_results. 1 read / 4 writes. 2x INSERT OR REPLACE.

### sentiment_repo.py (201 lines, 8 methods)

reddit_posts, aggregated_sentiment. 6 reads / 2 writes. INSERT OR IGNORE.

### telegram_repo.py (89 lines, 9 methods)

price_alerts, trade_journal, scheduled_reports, conversation_log. 3 reads / 6 writes. Top cascade-holder query lives here at line 32 (`SELECT * FROM price_alerts WHERE triggered = 0` — 0-row table).

### trading_repo.py (453 lines, 11 methods)

orders, positions, trade_history, account_snapshots. 6 reads / 5 writes. 3x INSERT OR REPLACE. `exchange_mode` parameter on every write since the HIGH-2 / I4 fix series.

## 2. Aggregate counts

| Dimension | Count |
|---|---|
| Total methods | 85 |
| Read-only | 46 (54%) |
| Write-only | 34 (40%) |
| Mixed read-then-write (race risk) | 5 (6%, all in `learning_repo.py`) |

| API call | Count |
|---|---|
| `execute` | 42 |
| `executemany` | 2 |
| `fetch_one` | 29 |
| `fetch_all` | 26 |
| `transaction()` context | 0 |

| SQLite-specific syntax | Count |
|---|---|
| `INSERT OR REPLACE` methods | 19 |
| `INSERT OR IGNORE` methods | 4 |
| `datetime('now')` defaults / inline | 4 |
| `DATE('now')` aggregate | 1 |
| `ROW_NUMBER() OVER` window function | 1 (market_repo `get_klines_batch`) |

## 3. Cross-repository / cross-table atomic patterns

None found.

- Every repository method targets exactly one table.
- No `transaction()` context manager usage in any repository.
- No cross-repository call sequences wrapped in shared atomicity.
- Closest to multi-step: `backtest_repo.save_trades()` loops per-trade with per-execute calls. `result` and `trades` are written separately with no atomicity.
- Implicit multi-write flows live OUTSIDE the repositories (trade open/close in `core/`); they too are not transactional today (see `04_core_component_access.md`).

This is good news for the refactor: the writer lock semantics in Option B match the existing implicit-transaction-per-execute behavior bit-for-bit. We are not changing any caller's transactional contract.

## 4. Lock-hold patterns and chunking

Only `market_repo.save_klines` and `market_repo.save_tickers_batch` are batch writers. Both already implement the D-3-era chunking (`_DEFAULT_KLINE_SAVE_CHUNK_SIZE` = 500, with `await asyncio.sleep(0)` between chunks) to bound per-chunk lock-hold time on the legacy model. The same chunking remains useful on the pooled model — it bounds writer-lock-hold time so readers (now on independent connections) are still freed at chunk boundaries for any code that might transition off the writer to do follow-up reads.

## 5. Implications for the refactor

- No repository file changes in Phase 3.
- The writer connection in Option B handles every write across all 11 repositories without any per-method change. The reader pool serves every read.
- The 5 read-modify-write methods in `learning_repo.py` are NOT wrapped today and will NOT be wrapped during Phase 3. They are flagged for Phase 5 review.
- The 0 transaction() calls means the existing `transaction()` context manager (defined at `connection.py:521`) is a no-op feature; we preserve it for API parity but it will simply hold the writer lock under the pooled model.

End of `02_repository_inventory.md`.
