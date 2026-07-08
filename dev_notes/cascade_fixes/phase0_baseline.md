# Phase 0 — Pre-Flight Baseline (Five Priority Cascade Fixes)

Captured 2026-05-10, prior to any code changes. Establishes the comparison baseline against which each issue's Phase 4 verification will measure improvement.

## Process and Branch State

- Workers PID 401: `python workers.py` started 16:51 UTC, ~38 min CPU on capture (long-running)
- MCP server PID 402: `python server.py --transport sse --port 8080`
- Current git branch: `feature/bybit-demo-adapter` (HEAD `d15e368`)
- Local main branch exists; remote `origin/main` exists
- Per-issue cascade-fix branches will be created off `main` (operator confirmation)

## Runtime Mode

- `config.toml` sets `[general] mode = "shadow"` at line ~ (third match in `mode = `)
- Logs evidence runtime is `bybit_demo`: 484 `BYBIT_DEMO_*` events in current `workers.log`, 15 `BYBIT_DEMO_ORDER_RECEIVED`
- Runtime override mechanism documented in PHASE5 report; confirmed identical here. No mode change required for fix series.

## Database State

Captured via `python3 sqlite3` on `data/trading.db`:

### Schema version
- `SELECT * FROM schema_version` shows 22 versions applied; **highest = 30**
- New SCHEMA_VERSION values for this fix series: **31** (Issue 4: positions.exchange_mode), **32** (Issue 1: ASC index for fear_greed_index)

### Row counts
| Table | Rows | Notes |
|-------|-----:|-------|
| `fear_greed_index` | **21,516** | Substantial — unbounded ORDER BY ASC scan would be slow |
| `ticker_cache` | **208** | Small (PK on symbol = #symbols subscribed) |
| `positions` | **0** | Confirms Issue 4 — bybit_demo never persists open positions |

### `positions` schema (12 columns, no exchange_mode)
```
0  symbol             TEXT  PK
1  side               TEXT  NOT NULL
2  size               REAL  NOT NULL
3  entry_price        REAL  NOT NULL
4  mark_price         REAL  DEFAULT 0
5  unrealized_pnl     REAL  DEFAULT 0
6  realized_pnl       REAL  DEFAULT 0
7  leverage           INT   DEFAULT 1
8  liquidation_price  REAL  DEFAULT 0
9  stop_loss          REAL  NULLABLE
10 take_profit        REAL  NULLABLE
11 updated_at         TEXT  DEFAULT datetime('now')
```
**No `exchange_mode` column** — confirms Issue 4 schema gap.

### `orders` schema (for comparison — already has exchange_mode at column 13)
```
13 exchange_mode      TEXT  NOT NULL DEFAULT 'shadow'
```
This is the model for Issue 4's `positions.exchange_mode` migration.

### Indexes
- `fear_greed_index`: `idx_fear_greed_ts ON fear_greed_index(timestamp DESC)` — DESC, but unbounded query at `altdata_repo.py:65` uses ORDER BY ASC (mismatch)
- `ticker_cache`: only `sqlite_autoindex_ticker_cache_1` on PK `symbol`
- `positions`: `sqlite_autoindex_positions_1` on PK + `idx_positions_symbol`

## DB_LOCK_WAIT Distribution (Cascade Evidence)

Sampled from three rotated `general.*.log` files:

| Log file | Time span | ticker_cache | fear_greed | position_snapshots | account_snapshots | trade_history | other | TOTAL |
|----------|-----------|-------------:|-----------:|-------------------:|------------------:|--------------:|------:|------:|
| `general.log` (current) | n/a | **719** (99.7%) | **0** | 0 | 0 | 0 | 2 | 721 |
| `general.2026-05-10_17-43-57_716987.log` | 17:43:57 → 17:53:15 (9 min) | **35,290** (99.8%) | **0** | 7 | 10 | 0 | 46 | 35,353 |
| `general.2026-05-10_17-36-43_019765.log` | 17:36:43 → 17:43:57 (7 min) | **35,270** (99.7%) | **0** | 10 | 10 | 0 | 64 | 35,354 |

**Bottom line:** ticker_cache is 99.7%+ of all DB_LOCK_WAIT holders. **fear_greed_index is 0%** — directly contradicting the report's Issue 1 claim.

Rate: ~5,050 DB_LOCK_WAIT events/min during the 7-9 min sampled windows = **~84/sec** of waits while the system is under load.

### Wait time distribution

| Log file | Max wait_ms | Count > 1s | Count > 10s |
|----------|------------:|-----------:|------------:|
| `general.log` (current) | 2,593 | 721 | 0 |
| `general.2026-05-10_17-43-57_716987.log` | **63,648** | 35,353 | **27,208** |

**Worst observed wait: 63.6 seconds** (worse than the report's 26.79s). 27,208/35,353 = **77% of waits exceed 10 seconds** in the worst-case window. This makes Issue 2 the dominant root cause, not Issue 1.

## Worker-Level Symptoms (from current `workers.log`, ~2 hours: 17:00:45 → 19:08:25)

| Event | Count | Interpretation |
|-------|------:|----------------|
| `BASE_WORKER_TICK_SLOW` | 230 | Workers regularly running over their tick budget |
| `WD_TICK_SLOW` | 14 | Watchdog freezes |
| `WORKER_TICK_FAIL` | **1** | Single profit_sniper crash 17:25:39 with `RuntimeError: dictionary changed size during iteration` (XRPUSDT) — **Issue 3 confirmed in current logs, not just the historic 2026-05-09 crash** |
| `services_unwired` | **130** | Issue 5 firing constantly |
| `TIME_DECAY_STRUCT_GUARD` | **130** | Exact 1:1 match with services_unwired — confirms every time-decay structural check is being silently blocked by Issue 5 |

Sample TIME_DECAY_STRUCT_GUARD line:
```
TIME_DECAY_STRUCT_GUARD | sym=LINKUSDT p_win=0.098 pnl=-0.60% mae=-0.61%
  entry_xray=0.70 entry_setup=bullish_fvg_ob entry_regime=ranging
  reason='no_data:services_unwired' blocked=true
```
LINKUSDT with `p_win=0.098` (under the 0.25 force-close threshold) is being held open because the structural-invalidation gate cannot determine validity. This is **exactly** the silently-disabled-safety scenario Issue 5 predicts.

Sample WORKER_TICK_FAIL line:
```
WORKER_TICK_FAIL | name=profit_sniper tier=None err_type=RuntimeError
  err='dictionary changed size during iteration' restart_count=1
  | tid=t-XRPUSDT-sniper
```

## Report Reference Drift (verified during Phase 1 investigation)

The realtime monitoring reports cited stale file:line references. Drift documented per issue in the plan; key updates:

| Report ref | Current ref | Drift |
|-----------|-------------|-------|
| `manager.py:1323` (L4 construction) | manager.py:1323 | accurate |
| `manager.py:1380+` (regime build) | manager.py:1470 | shifted |
| `manager.py:1480-1483` (watchdog late-wire) | manager.py:1480-1483 | accurate |
| `bybit_demo_adapter.py:131-166` (BD get_positions) | adapter.py:131-166 | accurate |
| `bybit_demo_adapter.py:455-459` (BD close_position) | adapter.py:287-470 (full method); save at 459 | partial |
| `position_service.py:54-80` (live get_positions) | position_service.py:54-80 | accurate |
| `time_decay_sl.py:397-412` (struct guard) | time_decay_sl.py:397-412 | accurate |
| `position_watchdog.py:985` (_compute_structural_invalidation) | position_watchdog.py:935-985 | line shifted ~50 |
| `layer4_protection.py:243` (gate) | layer4_protection.py:213-243 | line shifted, body unchanged |
| `profit_sniper.py` crash site | profit_sniper.py:327 (the unprotected iteration) | confirmed via crash log |

## Comparison Baseline (will be re-measured per issue)

| Metric | Baseline | Issue 1 target | Issue 3 target | Issue 5 target | Issue 2 target | Issue 4 target | Final |
|--------|---------|---------------:|---------------:|---------------:|---------------:|---------------:|------:|
| ticker_cache % of DB_LOCK_WAIT | 99.7% | unchanged | unchanged | unchanged | <50% | unchanged | <50% |
| Max wait_ms | 63,648 | unchanged | unchanged | unchanged | <1,000 | unchanged | <1,000 |
| DB_LOCK_WAIT > 10s count (per ~9min) | 27,208 | unchanged | unchanged | unchanged | ~0 | unchanged | ~0 |
| services_unwired count (per 2h) | 130 | unchanged | unchanged | **0** | unchanged | unchanged | 0 |
| TIME_DECAY_STRUCT_GUARD blocked count | 130 | unchanged | unchanged | structural reason only | unchanged | unchanged | structural reason only |
| profit_sniper WORKER_TICK_FAIL (per 2h) | 1 | unchanged | **0** | unchanged | unchanged | unchanged | 0 |
| positions row count when bybit_demo open | 0 | unchanged | unchanged | unchanged | unchanged | **>0** | >0 |
| Operational ceiling (positions before degradation) | ~3-4 | unchanged | unchanged | unchanged | 8+ | unchanged | 10+ |

## Verification Gate Passed

- Baseline metrics captured (above)
- Drift documented (above)
- Current mode confirmed (bybit_demo runtime, shadow config — known divergence)
- Report claims verified against current code (Issue 1 contradicted, Issues 2-5 confirmed)
- Schema version recorded (30); next free versions 31, 32

Phase 0 complete. Issue 1 may begin.
