# M2 — Stage 2 / Layer 3 DB Tables

Forensic snapshot 2026-05-02 — DB snapshot: `/tmp/trading_snapshot_1777722335.db`.

Tables in scope: `orders`, `positions`, `trade_thesis`, `trade_intelligence`, `claude_decisions`, `brain_decisions`, `apex_decisions`, `enforcer_stats`, `account_snapshots`, plus `fund_manager_state`/`fund_manager_log` discovered in scope.

---

## Table: `orders`

### Schema (DDL via .schema)

```sql
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    qty REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'New',
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- `sqlite_autoindex_orders_1` (PK on `order_id`)
- `idx_orders_symbol_status` ON `orders(symbol, status)`

### Writers
- `src/database/repositories/trading_repo.py:42` — `INSERT OR REPLACE INTO orders ...`.

### Readers
- `src/database/repositories/trading_repo.py:74` — `SELECT * FROM orders WHERE order_id = ?`
- `src/database/repositories/trading_repo.py:91` — `SELECT * FROM orders WHERE symbol = ? AND status IN ('New', 'PartiallyFilled') ORDER BY created_at DESC`
- `src/database/repositories/trading_repo.py:96` — `SELECT * FROM orders WHERE status IN ('New', 'PartiallyFilled') ORDER BY created_at DESC`
- `src/database/repositories/trading_repo.py:112` — `SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?`
- `src/database/repositories/trading_repo.py:117` — `SELECT * FROM orders ORDER BY created_at DESC LIMIT ?`

### Counts
- `SELECT COUNT(*) FROM orders` → **0**
- Growth rate by day: NOT FOUND — table is empty in the snapshot.

---

## Table: `positions`

### Schema

```sql
CREATE TABLE positions (
    symbol TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    entry_price REAL NOT NULL,
    mark_price REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    leverage INTEGER NOT NULL DEFAULT 1,
    liquidation_price REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- `sqlite_autoindex_positions_1` (PK on `symbol`)
- `idx_positions_symbol` ON `positions(symbol)`

### Writers
- `src/database/repositories/trading_repo.py:138` — `INSERT OR REPLACE INTO positions ...`
- `src/database/repositories/trading_repo.py:132` — `DELETE FROM positions WHERE symbol = ?`

### Readers
- `src/database/repositories/trading_repo.py:169` — `SELECT * FROM positions WHERE symbol = ?`
- `src/database/repositories/trading_repo.py:182` — `SELECT * FROM positions WHERE size > 0 ORDER BY symbol`

### Counts
- `SELECT COUNT(*) FROM positions` → **0**
- Growth: empty in snapshot.

---

## Table: `trade_thesis`

### Schema

```sql
CREATE TABLE trade_thesis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss_price REAL NOT NULL,
    take_profit_price REAL NOT NULL,
    size_usd REAL NOT NULL,
    leverage INTEGER NOT NULL DEFAULT 2,
    max_hold_minutes INTEGER NOT NULL DEFAULT 30,
    trailing_activation_pct REAL NOT NULL DEFAULT 1.0,
    thesis TEXT NOT NULL,
    market_context TEXT DEFAULT '',
    strategy_hints TEXT DEFAULT '',
    consensus TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    close_price REAL,
    actual_pnl_pct REAL,
    actual_pnl_usd REAL,
    close_reason TEXT,
    lesson TEXT,
    order_id TEXT,
    bybit_position_idx TEXT
, exchange_mode TEXT NOT NULL DEFAULT 'shadow', apex_flipped INTEGER NOT NULL DEFAULT 0, apex_original_direction TEXT NOT NULL DEFAULT '', apex_reason TEXT NOT NULL DEFAULT '');
```

### Indexes
- PK on `id`
- `idx_trade_thesis_symbol_status` ON `trade_thesis(symbol, status)`
- `idx_trade_thesis_status` ON `trade_thesis(status)`
- `idx_trade_thesis_opened` ON `trade_thesis(opened_at)`

### Writers
- `src/core/thesis_manager.py:47` — `INSERT INTO trade_thesis ...` (open).
- `src/core/thesis_manager.py:126` — `UPDATE trade_thesis ...` (close path 1).
- `src/core/thesis_manager.py:140` — `UPDATE trade_thesis ...` (close path 2).

### Readers
- `src/core/thesis_manager.py:82` — `... FROM trade_thesis ...`.
- `src/core/thesis_manager.py:173` — `... FROM trade_thesis ...`.
- `src/core/thesis_manager.py:188` — `SELECT DISTINCT symbol FROM trade_thesis WHERE status = 'open'`.
- `src/tias/collector.py:79` — `SELECT stop_loss_price, take_profit_price FROM trade_thesis ...`.
- `src/tias/collector.py:179` — `... FROM trade_thesis ...`.
- `src/strategies/performance_enforcer.py:323` — `... FROM trade_thesis ...`.
- `src/workers/cleanup_worker.py:243` — `... FROM trade_thesis ...`.

### Counts
- `SELECT COUNT(*) FROM trade_thesis` → **1257**
- Daily growth (column `opened_at`):
  - 2026-05-02: 30
  - 2026-05-01: 5
  - 2026-04-30: 9
  - 2026-04-29: 20
  - 2026-04-28: 32
  - 2026-04-27: 7
  - 2026-04-26: 18
  - 2026-04-25: 5
  - 2026-04-24: 19
  - 2026-04-23: 25

---

## Table: `trade_intelligence`

### Schema (TIAS — multi-group columns + DeepSeek + APEX)

```sql
CREATE TABLE trade_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Group A: Trade Outcome
    symbol TEXT NOT NULL, direction TEXT NOT NULL,
    strategy_name TEXT NOT NULL, strategy_category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '', closed_by TEXT NOT NULL,
    entry_price REAL NOT NULL, exit_price REAL NOT NULL,
    pnl_pct REAL NOT NULL, pnl_usd REAL NOT NULL,
    win INTEGER NOT NULL, hold_seconds REAL NOT NULL,
    -- Group B: Entry Decision Context
    leverage REAL, position_size_usd REAL,
    claude_thesis TEXT, claude_signal TEXT, claude_confidence REAL,
    entry_score REAL, ensemble_votes TEXT,
    -- Group C: Market Conditions at Close
    regime TEXT, fear_greed_value INTEGER, fear_greed_label TEXT,
    -- Group D: Technical Indicators at Close
    rsi REAL, macd_hist REAL, macd_signal REAL, bollinger_pct REAL,
    ema_20 REAL, ema_50 REAL, stochastic_k REAL, stochastic_d REAL,
    adx REAL, atr_value REAL, atr_pct REAL,
    volume_ratio REAL, price_vs_vwap REAL,
    -- Group E: Mode4 Profit Tracking
    m4_peak_pnl_pct REAL, m4_ticks_in_profit INTEGER, m4_ticks_total INTEGER,
    m4_composite_score REAL, m4_hurst_value REAL, m4_momentum_decay REAL,
    m4_extension_score REAL, m4_ev_ratio REAL, m4_volume_div_score REAL,
    -- Group F: DeepSeek Analysis
    ds_why TEXT, ds_what_worked TEXT, ds_what_failed TEXT,
    ds_lessons TEXT, ds_category TEXT, ds_confidence REAL, ds_analyzed_at TEXT,
    -- Group G: Metadata
    trade_id TEXT, trade_closed_at TEXT NOT NULL, captured_at TEXT NOT NULL
,
    -- Later additions (ALTER TABLE):
    ds_correct_direction TEXT, ds_what_should_done TEXT, ds_how_to_exploit TEXT,
    ds_optimal_direction TEXT, ds_optimal_sl_pct REAL, ds_optimal_tp_pct REAL,
    ds_optimal_size_usd REAL, ds_optimal_leverage INTEGER,
    ds_raw_response TEXT, ds_response_time_ms INTEGER,
    ds_input_tokens INTEGER, ds_output_tokens INTEGER,
    ds_cost_usd REAL, ds_model TEXT,
    analysis_version INTEGER, analysis_attempts INTEGER DEFAULT 0,
    entry_regime TEXT, entry_rsi REAL, entry_macd_hist REAL, entry_atr_pct REAL,
    apex_optimized INTEGER DEFAULT 0, apex_flipped INTEGER DEFAULT 0,
    apex_original_direction TEXT, apex_final_direction TEXT,
    apex_original_sl REAL, apex_final_sl REAL,
    apex_original_tp REAL, apex_final_tp REAL,
    apex_original_size REAL, apex_final_size REAL,
    apex_confidence REAL, apex_tp_mode TEXT, apex_reasoning TEXT,
    apex_model TEXT, apex_response_ms INTEGER, apex_cost_usd REAL,
    gate_adjustments TEXT, apex_tp_fill_rate REAL, regime_verified INTEGER DEFAULT 0
);
```

### Indexes
- PK on `id`
- `idx_ti_symbol` ON `trade_intelligence (symbol)`
- `idx_ti_win` ON `trade_intelligence (win)`
- `idx_ti_ds_why` ON `trade_intelligence (ds_why)`
- `idx_ti_trade_closed_at` ON `trade_intelligence (trade_closed_at)`
- `idx_ti_ds_category` ON `trade_intelligence (ds_category)`
- `idx_ti_apex_optimized` ON `trade_intelligence (apex_optimized)`

### Writers
- `src/tias/repository.py:46` — `INSERT INTO trade_intelligence ({col_names}) VALUES ({placeholders})`.
- `src/tias/repository.py:92` — `UPDATE trade_intelligence SET {set_clause} WHERE id = ?`.
- `src/tias/repository.py:131` — `UPDATE trade_intelligence ...` (DeepSeek analysis update).

### Readers (selected — many)
- `src/tias/repository.py:114, 148, 164, 186, 221, 253, 280, 307, 373, 396, 474, 497`
- `src/core/trade_recorder.py:45` — `SELECT DISTINCT symbol FROM trade_intelligence ...`.
- `src/workers/manager.py:896, 901, 905` — APEX startup stats query.
- `src/telegram/handlers/system.py:119`, `analysis.py:64`, `portfolio.py:103`, `apex_handler.py:57, 129, 183`, `dashboard_handler.py:2048, 2092, 2166, 2183, 2246`.

### Counts
- `SELECT COUNT(*) FROM trade_intelligence` → **821**
- Daily growth (column `trade_closed_at`):
  - 2026-05-02: 29
  - 2026-05-01: 5
  - 2026-04-30: 9
  - 2026-04-29: 18
  - 2026-04-28: 27
  - 2026-04-27: 6
  - 2026-04-26: 15
  - 2026-04-25: 5
  - 2026-04-24: 18
  - 2026-04-23: 24

---

## Table: `claude_decisions`

### Schema

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
```

### Indexes
- PK on `id`
- `idx_claude_decisions_ts` ON `claude_decisions(ts_epoch)`

### Writers
- `src/core/data_lake.py:111` — `INSERT INTO claude_decisions ...`.

### Readers
- NOT FOUND via grep on `FROM claude_decisions` — no readers in `src/`. Gap: write-only audit table.

### Counts
- `SELECT COUNT(*) FROM claude_decisions` → **1232**
- Daily growth:
  - 2026-05-02: 51
  - 2026-05-01: 17
  - 2026-04-30: 33
  - 2026-04-29: 59
  - 2026-04-28: 56
  - 2026-04-27: 119
  - 2026-04-26: 42
  - 2026-04-25: 5
  - 2026-04-24: 29
  - 2026-04-23: 31

---

## Table: `brain_decisions`

### Schema

```sql
CREATE TABLE brain_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_hash TEXT NOT NULL,
    market_state_json TEXT NOT NULL DEFAULT '{}',
    claude_response TEXT NOT NULL DEFAULT '',
    decision_json TEXT NOT NULL DEFAULT '{}',
    action_taken TEXT NOT NULL DEFAULT '',
    outcome_json TEXT NOT NULL DEFAULT '{}',
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_brain_created` ON `brain_decisions(created_at DESC)`

### Writers
- `src/database/repositories/learning_repo.py:162` — `INSERT INTO brain_decisions ...`.
- `src/database/repositories/learning_repo.py:173` — `UPDATE brain_decisions SET action_taken=?, outcome_json=? WHERE id=?`.
- `src/brain/brain_v2.py:392` — `INSERT INTO brain_decisions ...`.

### Readers
- `src/database/repositories/learning_repo.py:180` — `SELECT * FROM brain_decisions ORDER BY created_at DESC LIMIT ?`.
- `src/database/repositories/learning_repo.py:188` — `SELECT COALESCE(SUM(cost_usd), 0) as total FROM brain_decisions WHERE DATE(created_at) = DATE('now')`.
- `src/telegram/handlers/brain.py:34`, `system.py:101` — `SELECT action_taken, trigger, cost_usd, created_at FROM brain_decisions ...`.

### Counts
- `SELECT COUNT(*) FROM brain_decisions` → **0**
- Growth: empty. Gap: writers exist (learning_repo, brain_v2) but no rows in this snapshot — `claude_decisions` (1232 rows) is the live table; `brain_decisions` may be unused / superseded.

---

## Table: `apex_decisions`

- **Status:** NOT FOUND in DB snapshot. `sqlite3 .schema apex_decisions` returned empty. APEX results are stored as columns on `trade_intelligence` (`apex_*` columns added via ALTER TABLE — see schema above) per `src/workers/manager.py:1842-1854` ("APEX has no in-memory cache to hydrate — the assembler queries `trade_intelligence`").
- **Writers/readers:** NOT FOUND via grep on `apex_decisions`.

---

## Table: `enforcer_stats`

- **Status:** NOT FOUND in DB snapshot. `sqlite3 .schema enforcer_stats` returned empty. Searched `src/` for `enforcer_stats` — no hits.
- Performance Enforcer (`src/strategies/performance_enforcer.py`) reads from `trade_thesis` directly (`performance_enforcer.py:323`).

---

## Table: `account_snapshots`

### Schema

```sql
CREATE TABLE account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    total_equity REAL NOT NULL,
    available_balance REAL NOT NULL,
    used_margin REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    margin_level_pct REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_account_snapshots_time` ON `account_snapshots(updated_at DESC)`

### Writers
- `src/database/repositories/trading_repo.py:250` — `INSERT INTO account_snapshots ...`.
- `src/core/transformer.py:919` — `INSERT INTO account_snapshots ...` (T1 mode-switch path).

### Readers
- NOT FOUND via grep on `FROM account_snapshots` — no readers in `src/`. Gap: write-only metric table.

### Counts
- `SELECT COUNT(*) FROM account_snapshots` → **47514**
- Daily growth (column `updated_at`):
  - 2026-05-02: 1006
  - 2026-05-01: 473
  - 2026-04-30: 626
  - 2026-04-29: 1055
  - 2026-04-28: 843
  - 2026-04-27: 3780
  - 2026-04-26: 2120
  - 2026-04-25: 393
  - 2026-04-24: 1209
  - 2026-04-23: 1395

---

## Table: `fund_manager_state` (in-scope; persistent state for fund manager)

### Schema

```sql
CREATE TABLE fund_manager_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### Indexes
- `sqlite_autoindex_fund_manager_state_1` (PK on `key`).

### Writers
- `src/core/trading_mode.py:145` — `INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('trading_mode', ?)`.
- `src/risk/drawdown.py:94` — `INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) ...` (peak_equity).
- `src/fund_manager/tiered_capital.py:91` — `INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('starting_equity', ?)`.
- `src/fund_manager/tiered_capital.py:157` — `INSERT OR REPLACE INTO fund_manager_state (key, value) VALUES ('capital_override_pct', ?)`.
- `src/fund_manager/tiered_capital.py:162` — `DELETE FROM fund_manager_state WHERE key = 'capital_override_pct'`.
- `src/fund_manager/capital_allocator.py:304` — `INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) ...`.
- `src/fund_manager/profit_ratchet.py:166` — `INSERT OR REPLACE INTO fund_manager_state (key, value, updated_at) ...`.

### Readers
- `src/core/trading_mode.py:125` — `SELECT value FROM fund_manager_state WHERE key = 'trading_mode'`.
- `src/risk/drawdown.py:43` — `SELECT value FROM fund_manager_state WHERE key = 'peak_equity'`.
- `src/fund_manager/tiered_capital.py:84` — `SELECT value FROM fund_manager_state WHERE key = 'starting_equity'`.
- `src/fund_manager/tiered_capital.py:98` — `SELECT value FROM fund_manager_state WHERE key = 'capital_override_pct'`.
- `src/fund_manager/capital_allocator.py:93` — `SELECT * FROM fund_manager_state WHERE key = 'capital_level'`.
- `src/fund_manager/profit_ratchet.py:49` — `SELECT * FROM fund_manager_state WHERE key = 'profit_ratchet'`.

### Counts
- `SELECT COUNT(*) FROM fund_manager_state` → **4**
- Live contents (from snapshot):
  - `starting_equity` = `168000.0` (updated 2026-04-10 21:00:21).
  - `capital_override_pct` = `0.5` (updated 2026-04-14 09:46:52).
  - `profit_ratchet` = `{"total_locked": 539.9751076206283, "equity_high": 164958.0, "trade_locked": 539.9751076206283, "updated_at": "2026-05-02T04:09:50.969303+00:00"}` (updated 2026-05-02 04:09:50).
  - `peak_equity` = `50000.0` (updated 2026-05-02 11:22:43).

---

## Table: `fund_manager_log` (audit log)

### Schema

```sql
CREATE TABLE fund_manager_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    symbol TEXT DEFAULT '',
    details_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Indexes
- PK on `id`
- `idx_fm_log` ON `fund_manager_log(event_type, created_at DESC)`

### Writers / readers
- NOT FOUND via grep — no `INSERT INTO fund_manager_log` / `FROM fund_manager_log` in `src/`. Gap: schema present (created at `src/database/migrations.py:808-816`) but no callers wired in current codebase.

### Counts
- `SELECT COUNT(*) FROM fund_manager_log` → **0**

---

## Other tables in DB snapshot (out of scope but adjacent)

`.tables` returned 71 tables. Stage 1 (data) tables not detailed here: `klines`, `funding_rates`, `orderbook_snapshots`, `news_articles`, `aggregated_sentiment`, `regime_history`, `coin_regime_history`, `signals`, `signal_accuracy`, `pattern_log`, `pattern_occurrences`, `discovered_patterns`, `ticker_cache`, `correlation_matrix`, `market_snapshots`, `open_interest`, `economic_calendar`, `reddit_posts`, `fear_greed_index`, `transformer_state`, etc. Stage 2/3 lifecycle/learning tables also out of scope: `trade_history`, `trade_journal`, `trade_log`, `strategy_*`, `backtest_*`, `pnl_*`.
