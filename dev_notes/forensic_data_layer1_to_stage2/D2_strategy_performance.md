# D2 — Strategy Performance Table

**Capture timestamp:** 2026-04-27 23:03:34 UTC
**Source DB snapshot:** `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
**Snapshot data extent:** Latest `updated_at` in `strategy_performance`: 2026-04-27T22:29:49.571518+00:00. Latest `created_at` in `strategy_trades`: 2026-04-27T22:25:35.117223+00:00.

---

## D.2.1 — `strategy_performance` table

### Schema (verbatim from `sqlite3 .schema strategy_performance`)

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

Total row count: **124** (one row per strategy-symbol pair, all `timeframe='all'`).

### 20 most recent rows (ORDER BY updated_at DESC)

```
id   strategy        symbol         tf   total  wins  losses  win_rate  avg_pnl  avg_pnl_pct  max_dd  sharpe  pf  updated_at
10   claude_trader   ETHUSDT        all  72     31    41      0.4306    0.0      -0.0354      0.0     -      -   2026-04-27T22:29:49
9    claude_trader   BTCUSDT        all  65     28    37      0.4308    0.0      -0.0104      0.0     -      -   2026-04-27T22:29:00
27   claude_trader   AAVEUSDT       all  11     5     6       0.4545    0.0      -0.2475      0.0     -      -   2026-04-26T22:36:36
79   claude_trader   CRVUSDT        all  2      0     2       0.0       0.0      -0.1826      0.0     -      -   2026-04-26T22:25:34
132  claude_trader   INJUSDT        all  2      0     2       0.0       0.0      -0.0925      0.0     -      -   2026-04-26T18:09:09
131  claude_trader   AXSUSDT        all  1      0     1       0.0       0.0      -0.4368      0.0     -      -   2026-04-26T04:52:02
130  claude_trader   HYPERUSDT      all  2      0     2       0.0       0.0      -0.735       0.0     -      -   2026-04-26T04:51:32
124  claude_trader   DYDXUSDT       all  2      0     2       0.0       0.0      -0.0677      0.0     -      -   2026-04-26T02:48:20
66   claude_trader   ALICEUSDT      all  4      1     3       0.25      0.0      1.099        0.0     -      -   2026-04-26T02:22:55
50   claude_trader   MAGMAUSDT      all  8      3     5       0.375     0.0      -0.0161      0.0     -      -   2026-04-26T01:46:07
84   claude_trader   BASEDUSDT      all  5      4     1       0.8       0.0      0.3847       0.0     -      -   2026-04-26T01:31:32
32   claude_trader   ALGOUSDT       all  3      2     1       0.6667    0.0      0.5071       0.0     -      -   2026-04-26T01:14:27
127  claude_trader   ORCAUSDT       all  2      2     0       1.0       0.0      1.1763       0.0     -      -   2026-04-26T00:41:04
60   claude_trader   MOVRUSDT       all  3      0     3       0.0       0.0      -0.5747      0.0     -      -   2026-04-26T00:38:08
126  claude_trader   TRUMPUSDT      all  2      0     2       0.0       0.0      -0.09        0.0     -      -   2026-04-26T00:17:16
128  claude_trader   ZBTUSDT        all  2      1     1       0.5       0.0      -0.085       0.0     -      -   2026-04-25T23:52:28
129  claude_trader   WCTUSDT        all  1      0     1       0.0       0.0      -0.46        0.0     -      -   2026-04-25T23:51:45
122  claude_trader   ZKPUSDT        all  4      3     1       0.75      0.0      0.1913       0.0     -      -   2026-04-24T22:08:54
121  claude_trader   TREEUSDT       all  5      2     3       0.4       0.0      -0.3943      0.0     -      -   2026-04-24T21:47:43
125  claude_trader   ZAMAUSDT       all  1      0     1       0.0       0.0      -1.3879      0.0     -      -   2026-04-24T17:58:56
```

(`avg_pnl` is always 0.0; `sharpe_ratio` and `profit_factor` are NULL on all rows.)

### Distribution: which strategies have entries

```sql
SELECT strategy, COUNT(DISTINCT symbol) AS symbols, SUM(total_trades) AS total_trades,
       SUM(winning_trades) AS wins, SUM(losing_trades) AS losses,
       ROUND(SUM(winning_trades)*1.0/NULLIF(SUM(total_trades),0),3) AS overall_wr,
       ROUND(AVG(avg_pnl_pct),4) AS avg_pnl_pct
FROM strategy_performance GROUP BY strategy ORDER BY total_trades DESC;
```

Result:

| strategy | symbols | total_trades | wins | losses | overall_wr | avg_pnl_pct |
|---|---|---|---|---|---|---|
| `claude_trader` | 124 | 958 | 451 | 507 | 0.471 | -0.0841 |

**Only one strategy is represented in `strategy_performance`: `claude_trader`.** The 39 registered strategies (A1–K4) have **zero rows** in this table. Cumulative across all 124 symbols: 958 trades, 451 wins, 507 losses, overall WR 47.1%, avg PnL pct −0.084%.

Top-20 symbols by trade count for `claude_trader` (sorted DESC by total_trades):

| symbol | trades | wins | losses | wr | avg_pnl_pct | last updated |
|---|---|---|---|---|---|---|
| ETHUSDT | 72 | 31 | 41 | 0.431 | -0.0354 | 2026-04-27 22:29 |
| BTCUSDT | 65 | 28 | 37 | 0.431 | -0.0104 | 2026-04-27 22:29 |
| HYPEUSDT | 46 | 21 | 25 | 0.457 | 0.0222 | 2026-04-21 23:01 |
| SOLUSDT | 38 | 14 | 24 | 0.368 | -0.0385 | 2026-04-22 16:10 |
| SIRENUSDT | 37 | 9 | 28 | 0.243 | -0.5817 | 2026-04-20 09:08 |
| ZECUSDT | 37 | 19 | 18 | 0.514 | -0.1349 | 2026-04-24 17:08 |
| RIVERUSDT | 35 | 13 | 22 | 0.371 | -0.1624 | 2026-04-24 17:11 |
| RAVEUSDT | 35 | 18 | 17 | 0.514 | -0.3755 | 2026-04-21 14:30 |
| ARIAUSDT | 32 | 14 | 18 | 0.438 | 0.3412 | 2026-04-22 16:38 |
| CLUSDT | 28 | 18 | 10 | 0.643 | 0.1304 | 2026-04-21 23:03 |
| FARTCOINUSDT | 23 | 14 | 9 | 0.609 | 0.1223 | 2026-04-18 10:54 |
| TAOUSDT | 22 | 15 | 7 | 0.682 | 0.005 | 2026-04-17 19:35 |
| DOGEUSDT | 21 | 6 | 15 | 0.286 | 0.0367 | 2026-04-13 15:12 |
| ADAUSDT | 19 | 10 | 9 | 0.526 | 0.0265 | 2026-04-17 19:25 |
| SUIUSDT | 17 | 9 | 8 | 0.529 | -0.0339 | 2026-04-17 19:25 |
| XRPUSDT | 15 | 10 | 5 | 0.667 | 0.0788 | 2026-04-17 19:22 |
| BSBUSDT | 14 | 5 | 9 | 0.357 | -0.2674 | 2026-04-23 18:23 |
| ENAUSDT | 14 | 6 | 8 | 0.429 | -0.0222 | 2026-04-18 11:30 |
| DOTUSDT | 14 | 6 | 8 | 0.429 | -0.1282 | 2026-04-23 19:45 |
| ENJUSDT | 14 | 8 | 6 | 0.571 | 0.0658 | 2026-04-23 19:44 |

---

## D.2.2 — `claude_trader` performance: 0 wins / 6 trades observation

### Daily PnL ground truth (`daily_pnl` table, last 3 days)

```
date         start  end       realized_pnl  trades  wins  losses  max_dd  target_hit  halted
2026-04-27   0.0    6274.42   -0.2229       6       1     5       0.0     0           0
2026-04-26   0.0    6285.23   -0.3601       2       0     2       0.0     0           0
2026-04-25   0.0    6308.33   -1.4          2       0     2       0.0     0           0
```

Sum past 3 days: 10 trades, 1 win, 9 losses; cumulative realized PnL ≈ −1.99 USDT.

(The 22:27 observation reads "0 wins / 6 trades over prior 2 days" — `daily_pnl` shows 2026-04-26+27 = 8 trades, 1 win, 7 losses; the "0 wins" likely covers the strict prior 48 h before the observation moment, when 2026-04-27 had not yet booked the single win recorded today at 1W 5L.)

### Recent `strategy_trades` rows for `claude_trader` (created_at DESC, last 25)

All 25 rows below have `pnl=NULL`, `pnl_pct=NULL`, `was_win=NULL`, `exit_time=NULL`.

```
trade_id                                     symbol     dir    score  ens     lev  pnl  pnl_pct  was_win  entry_time           exit_time
BTCUSDT_Buy_20260427222535                   BTCUSDT    Buy    100.0  CLAUDE  2    -    -        -        2026-04-27T22:25:35  -
ETHUSDT_Sell_20260427222534                  ETHUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T22:25:34  -
ETHUSDT_Sell_20260427221036                  ETHUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T22:10:36  -
BTCUSDT_Buy_20260427220343                   BTCUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-27T22:03:43  -
ETHUSDT_Sell_20260427220342                  ETHUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T22:03:42  -
BTCUSDT_Sell_20260427215533                  BTCUSDT    Sell   100.0  CLAUDE  3    -    -        -        2026-04-27T21:55:33  -
ETHUSDT_Buy_20260427215532                   ETHUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-27T21:55:32  -
AAVEUSDT_Sell_20260426223402                 AAVEUSDT   Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T22:34:02  -
CRVUSDT_Buy_20260426222300                   CRVUSDT    Buy    100.0  CLAUDE  2    -    -        -        2026-04-26T22:23:00  -
INJUSDT_Buy_20260426180851                   INJUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T18:08:51  -
INJUSDT_Buy_20260426174323                   INJUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T17:43:23  -
AXSUSDT_Buy_20260426044349                   AXSUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:43:49  -
HYPERUSDT_Buy_20260426044348                 HYPERUSDT  Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:43:48  -
HYPERUSDT_Buy_20260426041017                 HYPERUSDT  Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:10:17  -
AXSUSDT_Buy_20260426041017                   AXSUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T04:10:17  -
DYDXUSDT_Sell_20260426023930                 DYDXUSDT   Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T02:39:30  -
ALICEUSDT_Buy_20260426021914                 ALICEUSDT  Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T02:19:14  -
MAGMAUSDT_Buy_20260426014021                 MAGMAUSDT  Buy    100.0  CLAUDE  2    -    -        -        2026-04-26T01:40:21  -
BASEDUSDT_Sell_20260426012658                BASEDUSDT  Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T01:26:58  -
ALGOUSDT_Buy_20260426011136                  ALGOUSDT   Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T01:11:36  -
TRUMPUSDT_Sell_20260426004117                TRUMPUSDT  Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T00:41:17  -
WCTUSDT_Buy_20260426004113                   WCTUSDT    Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T00:41:13  -
MOVRUSDT_Buy_20260426003028                  MOVRUSDT   Buy    100.0  CLAUDE  3    -    -        -        2026-04-26T00:30:28  -
ORCAUSDT_Buy_20260426003026                  ORCAUSDT   Buy    100.0  CLAUDE  2    -    -        -        2026-04-26T00:30:26  -
TRUMPUSDT_Sell_20260426000419                TRUMPUSDT  Sell   100.0  CLAUDE  3    -    -        -        2026-04-26T00:00:00  -
```

### What's contributing to 0 wins (data documentation only)

1. **`strategy_trades` does not get its closure fields written.** All 1219 `claude_trader` rows have `pnl IS NULL`, `was_win IS NULL`, `exit_time IS NULL` (verified: `SELECT COUNT(*) AS total, SUM(CASE WHEN pnl IS NULL THEN 1 ELSE 0 END) AS null_pnl, SUM(CASE WHEN was_win IS NULL THEN 1 ELSE 0 END) AS null_was_win FROM strategy_trades` → `1219|1219|1219`). The insert path is `src/core/trade_recorder.py:88-110` (only 12 columns, no pnl/exit/was_win fields). A `grep -rnE "UPDATE strategy_trades|strategy_trades.*SET" src/` returns 0 matches — there is **no UPDATE statement against `strategy_trades` anywhere in `src/`**, so the docstring in `trade_recorder.py:72-73` ("PnL fields … are updated later when the trade closes via TradeCoordinator callbacks") is unfulfilled in the current codebase.
2. **`strategy_performance` IS being updated**, via `WorkerManager._update_strategy_performance` (`src/workers/manager.py:1967-2018`). It computes new totals/wr/avg_pnl_pct from a closure record and `INSERT OR REPLACE`s the row. Most recent updates: ETHUSDT @ 22:29:49, BTCUSDT @ 22:29:00 (today). So per-symbol aggregates are accurate; only the per-row `strategy_trades` audit log is missing closure data.
3. **The 6 trades on 2026-04-27 (per `daily_pnl`) match the recent rows.** Looking at created_at on `strategy_trades` for 2026-04-27: 7 trades on that date — BTCUSDT×2 (Buy 22:25, Sell 21:55), ETHUSDT×3 (Sell 22:25, Sell 22:10, Sell 22:03, Buy 21:55), one BTCUSDT Buy 22:03. The `daily_pnl` row says trades=6 wins=1 losses=5 — meaning by the snapshot moment, 6 had closed and 1 was a win.
4. **Per-symbol BTC/ETH cumulative WR is 43.1%/43.1%** with avg_pnl_pct of −0.0104%/−0.0354% (entries 9 & 10 above). Both updated 22:29 today. Cumulatively, claude_trader on BTCUSDT is 28W / 37L, ETHUSDT 31W / 41L — i.e., persistently sub-50% on the highest-volume coins, with average PnL pct around −0.01% to −0.04%.
5. **Recent universe of trades is dominated by BTC / ETH and a long tail of low-volume alts:** of the 25 most-recent rows above, 7 are BTC/ETH and the rest are coins each with 1–2 trades total in `strategy_performance` and most with 0 wins (CRVUSDT 0/2, INJUSDT 0/2, AXSUSDT 0/1, HYPERUSDT 0/2, DYDXUSDT 0/2, MOVRUSDT 0/3, TRUMPUSDT 0/2, WCTUSDT 0/1, ZAMAUSDT 0/1).
6. **Mode is NORMAL during this window** (live `STRAT_PNL_GATE`: `halted=N rsn=ok pnl_pct=+0.00 wins=0 losses=2`), so no PnL-mode-tightening is in effect — the threshold is 50, and 7 of 10 setups survive per cycle (D1.8). The trade-source field on every recent `strategy_trades` row is `claude_trader` / category `CLAUDE` (per `_execute_claude_trade` → `record_strategy_trade`), so all observed 0-win trades came through the Claude direct execution path, not through any A1–K4 strategy executor.

The data shows the 0-wins-from-6-trades observation is consistent with the underlying claude_trader cumulative win rate (~43.1% on BTC/ETH; lower on first-time symbols where insufficient samples skew toward 0%). No closed-trade audit on a per-row basis is available in the snapshot DB because `strategy_trades` closure fields are never written.
