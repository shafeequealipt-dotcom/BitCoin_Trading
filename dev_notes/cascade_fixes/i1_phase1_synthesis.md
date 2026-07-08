# Issue 1 — Phase 1 Investigation Synthesis

## What the report said

`LIVE_PIPELINE_MONITOR_2026-05-10.md` (Bug #17) and `PHASE5_LIVE_MONITORING_REPORT.md` (Finding A14) claim:

> A SQL query reads from the `fear_greed_index` table using
> `SELECT * FROM fear_greed_index ORDER BY timestamp` without a LIMIT
> clause. The table has accumulated thousands of rows. The query has
> no index on `timestamp`. SQLite must scan and sort the entire table,
> holding the connection mutex for up to 11 seconds (max observed
> 26.79s). 99.8 % of DB_LOCK_WAIT events show INSERT INTO ticker_cache
> as the holder, but the ROOT cause is the fear_greed_index query
> that started holding the mutex first.

## What current code shows (verified 2026-05-10)

### All `fear_greed_index` queries

| File:Line | Query | LIMIT? | Caller | Hot path? |
|-----------|-------|:------:|--------|-----------|
| `src/database/repositories/altdata_repo.py:44` | `SELECT * FROM fear_greed_index ORDER BY timestamp DESC LIMIT 1` | YES | `get_latest_fear_greed()` → `FearGreedClient.get_latest()` | Hot (per-coin per-cycle) |
| `src/database/repositories/altdata_repo.py:65` (BEFORE FIX) | `SELECT * FROM fear_greed_index WHERE timestamp > ? ORDER BY timestamp ASC` | **NO** | `get_fear_greed_history(days=30)` → `FearGreedClient.get_history()` | **Not hot** — only on-demand |
| `src/apex/assembler.py:654-658` | `... ORDER BY timestamp DESC LIMIT 1` (with 24h staleness filter) | YES | `IntelligenceAssembler.assemble_section_4_structural()` | Hot (per-coin × per-cycle) |
| `src/tias/collector.py:314-316` | `... ORDER BY timestamp DESC LIMIT 1` | YES | `TradeContextCollector._collect_group_c()` | Per-trade close |
| `src/telegram/handlers/analysis.py:141-143` | `... ORDER BY fetched_at DESC LIMIT 1` | YES | Telegram `/sentiment` | User-triggered |

### Caller chain for the only unbounded query (`altdata_repo.py:65`)

```
AltDataRepository.get_fear_greed_history(days=30)
    └── FearGreedClient.get_history(days=30)               (src/intelligence/altdata/fear_greed.py:143)
        └── altdata_tools._fg(args)                        (src/mcp/tools/altdata_tools.py:29)
            └── MCP tool `get_fear_greed_index` with `include_history=True`
```

The only production caller is the MCP tool, which is **invoked on demand by the operator** (Telegram, Claude desktop). It is NOT in any worker tick loop. Frequency: sporadic, human-driven.

### Schema

`src/database/migrations.py:205-215`:
```sql
CREATE TABLE IF NOT EXISTS fear_greed_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    value INTEGER NOT NULL,
    classification TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fear_greed_ts ON fear_greed_index(timestamp DESC);
```

Existing index is `DESC`. The unbounded query orders `ASC`. SQLite cannot serve an `ASC` ORDER BY from a `DESC` index efficiently in all cases (the optimizer may use a backward scan, but for `WHERE timestamp > ?` + `ORDER BY ASC` the plan typically falls back to scan-and-sort).

### Table state (Phase 0 baseline)

- Row count: **21,516**
- Insert rate: 1 row / 60 minutes (`fear_greed_minutes` setting)
- TEXT-typed `timestamp` column → string comparison, not native datetime

### DB_LOCK_WAIT distribution (Phase 0 sample, three rotated logs)

| Log file | ticker_cache holders | fear_greed holders | Total events |
|----------|---------------------:|-------------------:|-------------:|
| `general.log` (current) | 719 (99.7%) | **0** | 721 |
| `general.2026-05-10_17-43-57_716987.log` | 35,290 (99.8%) | **0** | 35,353 |
| `general.2026-05-10_17-36-43_019765.log` | 35,270 (99.7%) | **0** | 35,354 |

Max wait observed: **63,648ms** (worse than report's 26.79s).

## Confirm or refine the report's diagnosis

The report's diagnosis is **partially stale**:

1. The unbounded query exists exactly where described (`altdata_repo.py:65`). **Confirmed.**
2. The query has no LIMIT. **Confirmed.**
3. It reads from a 21,516-row table. **Confirmed.**
4. The `ASC` ordering against a `DESC` index forces an inefficient plan. **Confirmed.**
5. The query is the cascade trigger holding the mutex 11s. **NOT confirmed.** Direct log evidence shows zero fear_greed_index holders across 70k+ events; the single dominant holder is `ticker_cache` (Issue 2).

The most likely explanation for the discrepancy: the cited monitoring runs were performed on an earlier code state where the unbounded query may have had a different (perhaps more frequent) caller, OR the query's caller was a worker rather than an MCP tool. In current code, even if the query did fire, it would not appear in the holder distribution because it's so rare.

## Recommended fix point

The unbounded query is a **latent footgun**, not the active cascade trigger. Defensive cleanup is still warranted:

1. Add `limit: int = 10000` parameter to `get_fear_greed_history` so the row cap is explicit and configurable.
2. Add ASC-ordered index `idx_fear_greed_ts_asc ON fear_greed_index(timestamp ASC)` so the existing ASC ordering is index-served.
3. Clamp `days` in `FearGreedClient.get_history` to [1, 365] and `limit` to [1, 10000] as a defensive layer above the repo.
4. Add `FEAR_GREED_HISTORY_QUERY` debug log so future regressions are visible in instrumentation.

## Estimated impact

- DB_LOCK_WAIT distribution: **unchanged** (fear_greed was already 0%)
- Latent risk eliminated if MCP usage increases or if a new worker starts calling `get_history`
- One additional index (~21k rows, sub-second to build)
- Migration is online (CREATE INDEX IF NOT EXISTS), reversible (DROP INDEX)
- Shadow mode unaffected (query is mode-agnostic)

The bigger leverage point for the cascade is Issue 2 (ticker_cache batching), which addresses 99.7% of the holders.
