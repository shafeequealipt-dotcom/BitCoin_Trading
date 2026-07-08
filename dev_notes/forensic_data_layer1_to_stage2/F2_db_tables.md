# F2 — DB Tables Used by Layer 1 → Stage 2 Pipeline

Snapshot DB:
`/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
File timestamp: `Apr 27 22:56` (153,624,576 bytes).

DDL is taken verbatim via `sqlite3 .schema <table>`. Row counts via
`SELECT COUNT(*)`. Indexes via
`SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=...`.

Writer/reader file:line citations come from grep over `src/`.

---

## T1 — `klines`

- **DDL:**
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
  CREATE INDEX idx_klines_symbol_tf_ts
    ON klines(symbol, timeframe, timestamp DESC);
  ```
- **Writer:** `MarketRepository.save_klines` at
  `src/database/repositories/market_repo.py:65-128` (uses
  `executemany` of `INSERT OR IGNORE INTO klines ...` at line 103;
  chunked, default chunk size at line 31).
  Called from `MarketService.save_klines` at `src/trading/services/market_service.py:222`.
  KlineWorker triggers via `self.market_service.get_klines(...)` at
  `src/workers/kline_worker.py:200-202` (the service path internally
  calls `save_klines`).
- **Readers:**
  - `MarketRepository.get_klines` (called by structure_worker at
    `src/workers/structure_worker.py:351`).
  - `KlineWorker` itself for freshness scan at
    `src/workers/kline_worker.py:330-338` (`SELECT symbol, MAX(timestamp) AS newest_ts FROM klines WHERE timeframe = ? AND symbol IN (...)`).
  - `RegimeDetector.detect_per_coin` (downstream — not pasted; reads via market_repo).
  - Strategist via `_prefetch_*` and `ta_cache` (indirect, through `TAEngine.analyze` → market_repo.get_klines).
- **Row count (snapshot):** **95,331**.
- **Indexes (snapshot):** UNIQUE(symbol, timeframe, timestamp) [implicit] + `idx_klines_symbol_tf_ts(symbol, timeframe, timestamp DESC)`.
- **Growth rate:** Each `KLINE_TICK_SUMMARY` reports `saved=N`. Tail:
  ```
  22:25:51  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  22:30:41  fetched=20000  saved=20000  tf_split={5:10000,60:10000,240:0,D:0}
  22:35:44  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  22:40:46  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  22:45:40  fetched=20000  saved=20000  tf_split={5:10000,60:10000,240:0,D:0}
  22:55:51  fetched=39539  saved=39539  tf_split={5:10000,60:10000,240:9997,D:9542}
  23:00:45  fetched=29997  saved=29997  tf_split={5:10000,60:10000,240:9997,D:0}
  ```
  Note: `INSERT OR IGNORE` means `saved=N` reports the fetched count, not unique inserts. Net new rows per cycle is much lower (only the latest 5-minute kline is actually new). True growth ≈ 200 unique rows per 5-min cycle (50 symbols × 4 timeframes × at most a few new bars).

OBSERVED ANOMALY (data freshness): snapshot tail —
`SELECT symbol, timeframe, MAX(timestamp) FROM klines GROUP BY symbol, timeframe LIMIT 10`
returns daily-TF newest timestamps from `2026-04-23` to `2026-04-22` for several
coins (the snapshot was taken at 22:56 on 2026-04-27). The static
snapshot does not contain the latest 5-min cycle ticks. Live DB would
need to be queried for current freshness (live KLINE_FRESHNESS_WARN
events would surface stragglers).

---

## T2 — `ticker_cache`

- **DDL:** see F1 entry C2.
- **Writer:** `MarketRepository.save_ticker` at
  `src/database/repositories/market_repo.py:268`:
  `INSERT OR REPLACE INTO ticker_cache ...`. Called from
  `PriceWorker._handle_ticker_update` at `src/workers/price_worker.py:218`.
- **Readers:**
  - `MarketRepository.get_ticker` at `src/database/repositories/market_repo.py:285-296`.
  - `src/core/transformer.py:667`.
  - `src/intelligence/sentiment/aggregator.py:169` (for momentum).
- **Row count (snapshot):** **200**.
- **Indexes:** PK on `symbol`.
- **Growth rate:** N/A — `INSERT OR REPLACE` keeps row count = subscribed-symbols ever seen. Stable at 200.

---

## T3 — `active_universe`

- **DDL:**
  ```sql
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
  ```
- **Writer:** `ScannerWorker.tick` at
  `src/workers/scanner_worker.py:993-1013`:
  ```
  await self.db.execute("DELETE FROM active_universe")
  ...
  await self.db.executemany(
      "INSERT OR REPLACE INTO active_universe (symbol, ...) VALUES (?, ?, ?, ?, ?, ?, ?)",
      insert_rows,
  )
  ```
- **Readers:** `MarketScanner.get_active_universe()` (pulled in-memory via `ScannerWorker.scanner.set_active_universe(new_symbols)` at `scanner_worker.py:1024`). Direct DB readers: NOT FOUND in `src/` grep beyond the ScannerWorker DELETE/INSERT itself.
- **Row count (snapshot):** **2** — only `BTCUSDT` and `ETHUSDT`, both with `opportunity_score=0.0` and `coin_tier=1`, `updated_at=2026-04-27 22:24:00`.
- **Indexes:** PK on `symbol` only.
- **Growth rate:** N/A — DELETE + INSERT every scanner cycle (every 5 min). Steady-state row count = `len(final)` from the cycle (currently 2).

---

## T4 — `regime_history`

- **DDL:**
  ```sql
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
  CREATE INDEX idx_regime_time ON regime_history(detected_at DESC);
  ```
- **Writer:** `RegimeWorker.tick` at `src/workers/regime_worker.py:145-157`:
  `INSERT INTO regime_history (symbol, regime, confidence, adx, atr_percentile, choppiness, detected_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))` (one INSERT per tick, global regime only).
- **Readers:** NOT FOUND any direct SELECT FROM regime_history in `src/`. Only `coin_regime_history` is restored at `regime_worker.py:90-102`.
- **Row count (snapshot):** **2,006**.
- **Indexes:** PK + `idx_regime_time`.
- **Growth rate (snapshot, last 24 h via `substr(detected_at,1,13), COUNT(*)`):**
  ```
  2026-04-26 22  → 8
  2026-04-26 23  → 12
  2026-04-27 00  → 12
  2026-04-27 01  → 12
  2026-04-27 02  → 12
  2026-04-27 03  → 12
  2026-04-27 04  → 12
  2026-04-27 05  → 12
  2026-04-27 06  → 11
  [ no rows for 2026-04-27 07 through 20 ]
  2026-04-27 21  → 1
  2026-04-27 22  → 6
  ```
  OBSERVED ANOMALY: 15-hour gap from 06:00 to 21:00 on 2026-04-27 in the snapshot. Matches the 22:27 monitor observation cited in the prompt.

---

## T5 — `coin_regime_history`

- **DDL:**
  ```sql
  CREATE TABLE coin_regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        regime TEXT NOT NULL,
        confidence REAL NOT NULL,
        adx REAL,
        choppiness REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );
  CREATE INDEX idx_coin_regime_symbol ON coin_regime_history(symbol, timestamp DESC);
  ```
- **Writer:** `RegimeWorker.tick` at `src/workers/regime_worker.py:251-258`:
  `INSERT INTO coin_regime_history (symbol, regime, confidence, adx, choppiness) VALUES (?, ?, ?, ?, ?)`. One INSERT per coin per tick.
- **Readers:** `RegimeWorker.tick` first-tick restore at `src/workers/regime_worker.py:90-102` (filter by `WHERE timestamp > datetime('now', '-30 minutes') AND symbol IN (...)`).
- **Row count (snapshot):** **20,951**.
- **Indexes:** PK + `idx_coin_regime_symbol`.
- **Cleanup:** `regime_worker.py:285-287` — `DELETE FROM coin_regime_history WHERE timestamp < datetime('now', '-24 hours')` once per 100 ticks.
- **Growth rate:** ~49 rows per 5-min tick (per_coin_size=49 from REGIME_TICK_SUMMARY) → ~588/h sustained.

---

## T6 — `aggregated_sentiment`

- **DDL:** see F1 entry C13.
- **Writer:** `SentimentRepository.save_aggregated_sentiment` at `src/database/repositories/sentiment_repo.py:128`:
  `INSERT INTO aggregated_sentiment (symbol, overall_score, level, news_score, news_count, reddit_score, reddit_count, fear_greed_value, momentum, created_at) VALUES (...)`. Called from `SentimentAggregator.aggregate_for_symbol` at `src/intelligence/sentiment/aggregator.py:270`.
- **Readers:** `SentimentRepository.get_*` at `src/database/repositories/sentiment_repo.py:157` (`SELECT * FROM aggregated_sentiment WHERE symbol = ? ORDER BY created_at DESC LIMIT ?`) and `:174` (`WHERE symbol = ? AND created_at > ?`). MCP tool `get_aggregated_sentiment` at `src/mcp/tools/sentiment_tools.py:73`.
- **Row count (snapshot):** **276,330**.
- **Indexes:** PK + `idx_agg_sentiment_symbol`.
- **Cleanup:** `src/database/cleanup.py:26` — 30-day retention; `src/workers/cleanup_worker.py:48` — `("aggregated_sentiment", 30, "created_at")`.
- **Growth rate (snapshot, last hours):**
  ```
  2026-04-27 21  → 56
  2026-04-27 22  → 116
  ```
  Steady ~50/min × N where N = signal_worker fires/min → matches sentiment writes per signal_worker cycle (50 coins × 1 fire/5-min = ~600/h).

---

## T7 — `funding_rates`

- **DDL:**
  ```sql
  CREATE TABLE funding_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        funding_rate REAL NOT NULL,
        next_funding_time TEXT NOT NULL,
        predicted_rate REAL NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_funding_symbol ON funding_rates(symbol, fetched_at DESC);
  ```
- **Writer:** `AltDataRepo.save_funding_rate` at `src/database/repositories/altdata_repo.py:87` —
  `INSERT INTO funding_rates (symbol, funding_rate, next_funding_time, predicted_rate, fetched_at) ...`. Called from `FundingRateTracker.fetch_current_rates` (the tracker writes to DB; in-memory `_funding_cache` is in AltDataWorker — see F1).
- **Readers:** `AltDataRepo.get_funding_rates` at `src/database/repositories/altdata_repo.py:99` (paginated query). Used in dashboards and TIAS/APEX.
- **Row count (snapshot):** **87,145**.
- **Indexes:** PK + `idx_funding_symbol`.
- **Growth rate:** Each AltDataWorker fire writes 50 rows; fires every 5 min → ~600/h sustained.

---

## T8 — `open_interest`

- **DDL:**
  ```sql
  CREATE TABLE open_interest (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        open_interest_value REAL NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_oi_symbol ON open_interest(symbol, timestamp DESC);
  ```
- **Writer:** `AltDataRepo.save_open_interest` at `src/database/repositories/altdata_repo.py:158`:
  `INSERT INTO open_interest (symbol, open_interest_value) VALUES (?, ?)`. Called from `OpenInterestTracker.fetch_current(symbols)` via AltDataWorker.
- **Readers:** dashboards / strategist (grep showed `fetch_one:SELECT * FROM open_interest WHERE symbol = ? ORD` in DB_LOCK_WAIT lines).
- **Row count (snapshot):** **79,565**.
- **Indexes:** PK + `idx_oi_symbol`.
- **Cadence:** OI fires every `open_interest_minutes=5` from `config.toml:146` → 50 rows/cycle → ~600/h.

---

## T9 — `fear_greed_index`

- **DDL:**
  ```sql
  CREATE TABLE fear_greed_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value INTEGER NOT NULL,
        classification TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_fear_greed_ts ON fear_greed_index(timestamp DESC);
  ```
- **Writer:** `AltDataRepo.save_fear_greed` at `src/database/repositories/altdata_repo.py:33`:
  `INSERT INTO fear_greed_index (value, classification, timestamp) VALUES (?, ?, ?)`. Called from `FearGreedClient.fetch_current` via AltDataWorker.
- **Readers:** Single-row latest fetch — appears in DB_LOCK_WAIT logs as `fetch_one:SELECT * FROM fear_greed_index ORDER BY timestam`.
- **Row count (snapshot):** **21,373**.
- **Indexes:** PK + `idx_fear_greed_ts`.
- **Cadence:** F&G fires every `fear_greed_minutes=60` from `config.toml:148`.

---

## T10 — `signals`

- **DDL:**
  ```sql
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
  CREATE INDEX idx_signals_symbol ON signals(symbol, created_at DESC);
  ```
- **Writer:** `AltDataRepo` (or signal repo) at `src/database/repositories/altdata_repo.py:204`:
  `INSERT INTO signals (symbol, signal_type, confidence, source, components, reasoning, created_at) ...`. Called by `SignalGenerator` / `intelligence_aggregator`.
- **Readers:** dashboards, strategist context. NOT FOUND any direct read in the Layer 1→Stage 2 critical path beyond the in-memory `_signal_cache` (F1 C4).
- **Row count (snapshot):** **155,693**.
- **Indexes:** PK + `idx_signals_symbol`.
- **Sample latest (snapshot):** every coin `signal_type=neutral confidence≈0.20–0.24 source=intelligence_aggregator created_at=2026-04-27T22:26:03`.
- **Growth rate:** 50 signals per signal_worker tick × 12 ticks/h = ~600/h.

---

## T11 — `news_articles`

- **DDL:**
  ```sql
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
  CREATE INDEX idx_news_published ON news_articles(published_at DESC);
  CREATE INDEX idx_news_symbols ON news_articles(symbols);
  ```
- **Writer:** NewsWorker via news repository (NOT FOUND a single `INSERT INTO news_articles` line in our grep — `src/workers/news_worker.py` is only 75 lines and delegates writes; check news_repo for actual SQL).
- **Readers:** Sentiment aggregator + DB_LOCK_WAIT log shows `fetch_all:SELECT * FROM news_articles WHERE symbols LIKE ?`.
- **Row count (snapshot):** **1,226**.
- **Indexes:** PK + `idx_news_published` + `idx_news_symbols`.
- **Sample latest:**
  ```
  7860475 "Bitcoin whale holdings hit five-month high: Is BTC headed to $80K next?"  Cointelegraph  +0.30  ["BTCUSDT"]   2026-04-27T22:53:42.163245+00:00
  7860474 "Canada advances bill to ban crypto political donations"                    Cointelegraph  -0.15  []           2026-04-27T22:53:42.160136+00:00
  7860372 "Industry leaders are pouring hundreds of millions into a rescue plan for Aave users after massive crypto hack"  CoinDesk 0.0 [] 2026-04-27T22:28:12.110766+00:00
  ```

---

## T12 — `strategy_performance`

- **DDL:**
  ```sql
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
  CREATE INDEX idx_strategy_perf_name ON strategy_performance(strategy);
  ```
- **Writer:** Strategy / TIAS feedback path (NOT GREP'D for full file:line in this module — see D2 collection). The `apply_restrictions` filter at `src/strategies/pnl_manager.py` would be the relevant reader.
- **Readers:** `apply_restrictions` (D1), TIAS feedback. Detail belongs in D2.
- **Row count (snapshot):** **124**.
- **Sample (top 5 by total_trades):**
  ```
  claude_trader  ETHUSDT   total=72  win=31  win_rate=0.4306
  claude_trader  BTCUSDT   total=65  win=28  win_rate=0.4308
  claude_trader  HYPEUSDT  total=46  win=21  win_rate=0.4565
  claude_trader  SOLUSDT   total=38  win=14  win_rate=0.3684
  claude_trader  SIRENUSDT total=37  win=9   win_rate=0.2432
  ```

---

## T13 — `cycle_metrics` (CycleTracker hourly aggregate)

- **DDL:** see top of file. ALTER-added columns include `signal_buy_pct`, `signal_sell_pct`, `signal_neutral_pct`, `xray_setup_type_count`, `regime_distribution_json`, `l1_strategies_fired_avg`, `l2_score_p50`, `l3_consensus_dist_json`, `package_completeness_avg`, `freshness_klines_to_xray_p50`.
- **Writer:** `CycleTracker` — wired via `services["cycle_tracker"]`. Writers: NOT GREP'D for `INSERT INTO cycle_metrics` in this module pass.
- **Readers:** Operator dashboards. Out of pipeline-critical scope.
- **Row count (snapshot):** **0**.

---

## T14 — `ensemble_votes`

- **DDL:** see top of file. Indexes: NONE on this table beyond PK.
- **Writers / Readers:** NOT GREP'D for this module. Out of pipeline-critical scope (per E1/D1 modules).
- **Row count (snapshot):** **0**.

---

## T15 — `brain_decisions` and `claude_decisions`

- **claude_decisions DDL:**
  ```sql
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
  CREATE INDEX idx_claude_decisions_ts ON claude_decisions(ts_epoch);
  ```
- **brain_decisions:** present (see DDL) but row count = 0 — appears unused.
- **Writer (claude_decisions):** `LayerManager._record_decision_to_data_lake` at `src/core/layer_manager.py:862-876` → `data_lake.write_claude_decision(...)` (asyncio.create_task). Actual SQL: NOT GREP'D in this module.
- **Row count (snapshot):** `claude_decisions` = **1,016**; `brain_decisions` = **0**.
- **Sample latest 3 (claude_decisions):**
  ```
  call_a  "Late NY dead zone, both BTC and ETH sold off hard today (-2.3% and -3.5%). Low volatility..."  resp_ms=113851 prompt_length=0 created_at=2026-04-27 22:25:16
  call_a  "Only 2 coins available (BTCUSDT, ETHUSDT). ETHUSDT already has a position being managed by watchdog..."  resp_ms=79318 prompt_length=0 created_at=2026-04-27 22:18:22
  call_b  ""  resp_ms=97495 prompt_length=0 created_at=2026-04-27 22:14:33
  ```

---

## SPECIAL — D-3 LOCK CONTENTION FORENSICS

DB lock instrumentation lives in `src/database/connection.py`:
- `_locked()` async context manager at line 168-231: acquires `self._lock` (asyncio.Lock at line 93), records wait time into `_wait_samples` (deque maxlen 1000), and emits `DB_LOCK_WAIT` warning when `wait_ms >= self._lock_wait_warn_ms` (default `DB_LOCK_WAIT_WARN_MS = 1000.0` per line 38).
- `log_lock_histogram()` at line 233-269 emits `DB_LOCK_HIST` periodic summary.

Search:
- `data/logs/workers.log` (current): **0** `DB_LOCK_WAIT` events in the captured window.
- `data/logs/general.log`: many events captured. The events use a deeper format that includes both holder (previous lock holder, may say `holder=none` if the prior holder cleared `_last_holder` or had no op tag) and `caller=<op>`.

### 5 instances of `DB_LOCK_WAIT > 1000ms` (verbatim from general.log)

1. `2026-04-26 16:35:28.823 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1108 holder=none caller=fetch_all: ...`
2. `2026-04-26 16:35:28.828 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1112 holder=none caller=fetch_all: ...`
3. `2026-04-26 16:35:28.847 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1129 holder=none caller=fetch_all:SELECT * FROM price_alerts WHERE triggered = 0 | no_ctx`
4. `2026-04-26 16:35:28.849 | WARNING  | src.database.connection:_locked:136 | DB_LOCK_WAIT | wait_ms=1132 holder=none caller=fetch_all:SELECT * FROM scheduled_reports WHERE enabled =  | no_ctx`
5. `2026-04-26 17:35:32.200 | WARNING  | src.database.connection:_locked:149 | DB_LOCK_WAIT | wait_ms=1017 holder=fetch_one:SELECT * FROM fear_greed_index ORDER BY timestam caller=fetch_all: ...`

For each: holder named is the OP TAG of the previous lock holder
(per `_locked` line 198: `prev_holder = self._last_holder`). Where
`holder=none`, the previous holder cleared `_last_holder` (or was the
very first acquire). Where `holder=fetch_one:SELECT * FROM fear_greed_index`,
that operation had been the immediately-previous holder.

The **upstream caller (worker)** is captured in
`_extract_external_caller_frame()` at line 42-68; it is recorded only
on the warn path and embedded in the warning as
`frame=<filename>:<lineno>`. The samples shown above use the older
format which does NOT include `frame=...` — that field came in with
the Phase 1 D-3 fix (line 217-222 in current code) but the historic
log lines pre-date that emission. Newer samples in the same
`general.log` use the post-fix form (`_locked:149` instead of `:136`).

### Longest single transaction (likely kline_worker bulk insert)

The longest waits captured were >130 seconds:

1. `2026-04-26 21:14:54.791 ... DB_LOCK_WAIT | wait_ms=137403 holder=execute:` (137.4 s).
2. `2026-04-26 21:14:53.842 ... DB_LOCK_WAIT | wait_ms=136584 holder=executemany:` (136.6 s).
3. `2026-04-26 21:14:52.555 ... DB_LOCK_WAIT | wait_ms=135569 holder=execute:INSERT INTO account_snapshots` (135.6 s).
4. `2026-04-26 21:14:51.281 ... DB_LOCK_WAIT | wait_ms=134424 holder=execute:INSERT INTO coin_regime_history` (134.4 s).
5. `2026-04-26 21:14:45.105 ... DB_LOCK_WAIT | wait_ms=135909 holder=fetch_all:` (135.9 s).

Holder identity for the longest wait (137,403 ms): `holder=execute:`
(truncated — full SQL not in line; only the first 48 chars logged per
`_locked` line 300 / 337 op tag). The bulk INSERT pattern matches the
kline_worker `executemany` of `INSERT OR IGNORE INTO klines ...` at
`src/database/repositories/market_repo.py:127`. The transaction size
in rows: per `KlineWorker.tick` evidence (workers.log tail) =
**29,997 rows per cycle** when M5+H1+H4 fire together (`tf_split={5:10000,60:10000,240:9997,D:0}`), or **39,539 rows** when D1 also fires (`{5:10000,60:10000,240:9997,D:9542}` at 22:55:51). The `executemany` is chunked at `market_repo.py:127`:
`await self._db.executemany(sql, params[i : i + chunk_size])` with
`chunk_size` per line 31 docstring (default mirrors the historical single-call). Concrete chunk size value: NOT FOUND verbatim in the read excerpt.

Lock hold time: ≈ wall-clock time between the `executemany`'s acquire
and release. Not directly logged per-transaction. Indirect estimate:
the longest wait observed by a waiter (137.4 s) implies the holder
held the lock for >137 s. Live `KLINE_TICK_SUMMARY` shows `el=10433ms`
to `el=21363ms` for the entire tick (which includes both fetch and
DB write); so the 137-second holds are **outliers** likely associated
with WAL-checkpoint stalls or the kline_worker's combined fetch +
multi-table-lookup tick body, not the single executemany alone. The
post-fix instrumentation (`frame=` field) is needed to attribute
specific holds; the historic samples cannot pin the exact line.

OBSERVED ANOMALY: `DB_LOCK_HIST | n=536 p50=0ms p95=0ms max=1135ms`
(2026-04-26 16:35:28.856) shows distribution heavily skewed —
99% of acquires are <1 ms but the tail extends past 1 second. The
post-Layer-1 fix added a `top_callers=[...]` list to this emit
(`src/database/connection.py:262-266`) but the historic line shown
predates that addition.
