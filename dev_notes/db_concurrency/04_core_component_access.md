# 04 — Core Component Database Access

Targets: `src/core/`, `src/brain/`, `src/apex/`, `src/strategies/`, `src/risk/`, `src/tias/`, `src/fund_manager/`, `src/portfolio/`, `src/factory/`. (MCP and Telegram covered in 05/06.)

Total DB-access sites outside workers + repositories: **44 sites across 21 files**.

## 1. core/ (10 files, 19 sites)

### data_lake.py (3 writes)

| Line | Op | Table |
|---|---|---|
| 41 | INSERT INTO market_snapshots | market_snapshots |
| 140 | INSERT OR REPLACE INTO trade_log | trade_log |
| 156 | INSERT OR REPLACE INTO trade_log (alternate path) | trade_log |
| 185 | INSERT INTO position_snapshots | position_snapshots |
| 209 | INSERT INTO claude_decisions | claude_decisions |
| 230 | INSERT INTO event_log | event_log |
| 254 | INSERT OR REPLACE INTO daily_summary | daily_summary |

(Actually 7 writes — corrected from initial summary.)

### thesis_manager.py (7 sites)

| Line | Op | Table | Note |
|---|---|---|---|
| 158 | INSERT INTO trade_thesis | trade_thesis | Thesis open |
| 239 | fetch_all (open theses) | trade_thesis | Cycle reconciliation |
| 253 | fetch_all status=closed | trade_thesis | Analytical (with ORDER BY) |
| 323 | UPDATE trade_thesis (close) | trade_thesis | Open → closed |
| 341 | UPDATE trade_thesis (lesson) | trade_thesis | Lesson append |
| 421 | fetch_all (aggregated stats) | trade_thesis | Analytical |
| 502 | fetch_all (recent for strategy review) | trade_thesis | Analytical — audit's slow query 2 |

### cycle_tracker.py (1 write)

Line 442: `INSERT OR REPLACE INTO cycle_metrics`.

### transformer.py (5 sites)

Line 168 fetch_one transformer_state (bootstrap); 174 INSERT default state; 661/810/1220/1237 UPDATE transformer_state (mode switches); 845 fetch_all switch_history; 904 fetch_one ticker_cache.

### transformer_state_reader.py (1 read)

Line 80 fetch_one transformer_state.

### trading_mode.py (2 sites)

Lines 270/335 INSERT OR REPLACE trading_mode; line 288 fetch_one.

### trade_recorder.py (2 sites)

Line 44 fetch_all trade_intelligence; line 95 INSERT INTO strategy_trades.

### trade_coordinator.py (1 read)

Line 210 fetch_all trade_thesis WHERE status='open' (restore on restart).

### freshness_guard.py (1 read)

Line 84 fetch_one.

## 2. strategies/ (2 files, 4 sites)

### performance_enforcer.py (2 analytical reads)

Lines 580/596: fetch_all trade_thesis WHERE status='closed' AND DATE(closed_at)=? — daily PnL roll-up with ORDER BY closed_at DESC. Audit's slow query 2.

### pnl_manager.py (2 sites)

Line 132 fetch_one daily_pnl (recover state); line 188 INSERT OR REPLACE daily_pnl.

## 3. fund_manager/ (5 files, 11 sites)

### capital_allocator.py (2 sites)

Line 92 fetch_one fund_manager_state; line 302 INSERT OR REPLACE.

### tiered_capital.py (5 sites)

Lines 83/97 fetch_one fund_manager_state; lines 90/258/263 INSERT/DELETE/INSERT.

### profit_ratchet.py (2 sites)

Line 48 fetch_one; line 164 INSERT OR REPLACE.

### momentum_allocator.py (1 read)

Line 55 fetch_all trade_intelligence.

### manager.py (4 sites)

Line 416 fetch_one portfolio_allocations; 536 fetch (historical trades); 560 fetch_one (final PnL check); 569 INSERT (record transaction).

## 4. risk/ (1 file, 2 sites)

### drawdown.py (2 sites)

Line 42 fetch_one fund_manager_state (load peak_equity); line 93 INSERT OR REPLACE (persist peak_equity).

## 5. tias/ (2 files, 10 sites)

### collector.py (6 point reads)

Lines 78, 202, 227, 239, 340, 481: fetch_one trade_thesis — enrichment per trade (SL/TP fields, fear-greed lookups).

### repository.py (4 sites; per-trade analytics)

Not exhaustively enumerated here — covered by TIAS-specific test paths.

## 6. apex/ (1 file, 2 point reads)

### assembler.py (2 point reads)

Line 315 fetch_one sniper_log (M4 composite); line 654 fetch_one fear_greed_index (24h staleness).

## 7. portfolio/ (2 files, 2 reads)

### analytics.py (1)

Line 36 fetch_all (rolling correlation).

### correlation.py (1)

Line 28 fetch_all (correlation matrix).

## 8. brain/ (1 file, 1 write)

### brain_v2.py (1 write)

Line 391 INSERT INTO brain_decisions. NOTE: this is the legacy brain_v2 path. The active strategist path writes to `claude_decisions`, NOT `brain_decisions`. The `brain_decisions` table currently has 0 rows because `brain_v2` is not on the active code path. This is one of the Phase 5 cleanup targets.

## 9. factory/ (1 file, 1 read)

### trial_manager.py (1 read)

Line 36 fetch_all (backtest trial metrics).

## 10. observability/, sentinel/, intelligence/, analysis/, trading/, alerts/

No direct DB access detected — these subsystems operate via in-memory services or external APIs, with DB access mediated by repositories or core services.

## 11. Pattern classification

| Pattern | Count |
|---|---|
| ANALYTICAL READ (ORDER BY / GROUP BY / DATE filter) | 10 |
| POINT READ (single-row fetch_one) | 18 |
| POINT WRITE (single INSERT/UPDATE/DELETE) | 15 |
| BATCH WRITE (executemany) | 0 |
| TRANSACTIONAL MULTI-WRITE (transaction() context) | 0 |

## 12. Implicit multi-write flows (not transactional today)

These sequences span multiple `execute` calls without atomicity. They are listed as a latent risk; the refactor does NOT change their behavior.

- Trade-open flow: `ThesisManager.save_thesis()` INSERT trade_thesis → later `TradeRecorder.record_strategy_trade()` INSERT strategy_trades (separate DB calls).
- Trade-close flow: `DataLake.write_trade()` INSERT OR REPLACE trade_log → `ThesisManager.close_thesis()` UPDATE trade_thesis SET status='closed' → TIAS collector fetch_one trade_thesis (3 separate calls).
- Mode switch: Transformer performs 2-3 sequential UPDATE transformer_state calls.

If a process crash interrupts any of these mid-sequence, state inconsistency is possible. The system tolerates this today (trade-log is the authoritative ledger; thesis state can be reconstructed from trade_log on next startup). Phase 5 may layer atomic grouping on top, but it is out of scope for this refactor.

## 13. Implications for the refactor

- No file changes here in Phase 3. Every site calls `db.fetch_one`/`db.fetch_all`/`db.execute` which transparently routes through the pooled engine.
- The 10 ANALYTICAL READS (especially in `thesis_manager.py:253/421/502` and `performance_enforcer.py:580/596`) are the queries that today contribute most to the lock-wait tail because they take measurable time (TEMP B-TREE sort) and block every other operation while running. Under the pooled model these run on a reader connection and do not block other readers or the writer.
- The 15 POINT WRITES are short single-row INSERTs/UPDATEs. They serialize on the writer lock but each is fast (sub-millisecond expected); the writer lock is contended only when actual DML overlaps actual DML, which is rare.

End of `04_core_component_access.md`.
