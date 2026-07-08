# Issue 1 — Phase 2 Operator Discussion Report

## Summary

The realtime monitoring report's Issue 1 diagnosis (fear_greed_index unbounded query is the cascade trigger) does not match current evidence. Direct log sampling across 70,000+ DB_LOCK_WAIT events shows **0 events** held by fear_greed_index queries; **99.7%+ are held by ticker_cache** writes. The cascade trigger and amplifier are the same defect (Issue 2).

The unbounded query identified by the report (`altdata_repo.py:65`) does exist and is a latent footgun, but it is currently dormant — its only caller is the on-demand MCP `get_fear_greed_index` tool with `include_history=True`, which is invoked sporadically by the operator.

Defensive cleanup of the latent unbounded query was the operator's chosen path (Option C from plan).

## Evidence

### Code state

`src/database/repositories/altdata_repo.py:54-75` (BEFORE FIX):
```python
async def get_fear_greed_history(self, days: int = 30) -> list[FearGreedData]:
    cutoff = (now_utc() - timedelta(days=days)).isoformat()
    rows = await self._db.fetch_all(
        "SELECT * FROM fear_greed_index WHERE timestamp > ? ORDER BY timestamp ASC",
        (cutoff,),
    )
```

No LIMIT clause. Index `idx_fear_greed_ts` is `DESC` while query orders `ASC` — plan falls back to scan-and-sort.

### DB_LOCK_WAIT holder distribution (Phase 0 baseline)

| Log file | Window | ticker_cache | fear_greed | Total |
|----------|--------|-------------:|-----------:|------:|
| `general.log` (current) | a few minutes | 719 (99.7%) | 0 | 721 |
| `general.2026-05-10_17-43-57_716987.log` | 9 min | 35,290 (99.8%) | 0 | 35,353 |
| `general.2026-05-10_17-36-43_019765.log` | 7 min | 35,270 (99.7%) | 0 | 35,354 |

### Caller chain

```
MCP tool get_fear_greed_index(include_history=True)
  └── FearGreedClient.get_history(days=7)
      └── AltDataRepository.get_fear_greed_history(days=30)  [DEFAULT]
          └── SELECT * FROM fear_greed_index WHERE timestamp > ? ORDER BY timestamp ASC
```

### Table state

- 21,516 rows (Phase 0 baseline)
- Insert rate: 1/hour
- TEXT-typed `timestamp` column

## Solution chosen

**Option C — defensive cleanup**:

1. `AltDataRepository.get_fear_greed_history(days=30, *, limit=10_000)` — add `limit` kwarg, hard-coded cap of 10,000.
2. `FearGreedClient.get_history(days=30, *, limit=10_000)` — clamp both args to [1, 365] and [1, 10000].
3. `migrations.py` — add `CREATE INDEX IF NOT EXISTS idx_fear_greed_ts_asc ON fear_greed_index(timestamp ASC)` and bump `SCHEMA_VERSION` to 31.
4. Add `FEAR_GREED_HISTORY_QUERY | days=D limit=L returned=N` debug log for instrumentation.

## Trade-offs

### Pros
- Eliminates the only unbounded query in the codebase
- Bounds worst-case mutex hold time for this query
- Index creation is fast (<1s for 21k rows)
- Migration is online, reversible
- Shadow mode unaffected
- No breaking change for consumers (kwarg defaults preserve behavior)

### Cons
- Adds one more index (modest extra disk + write overhead per fear_greed insert, which is 1/hour)
- The fix does NOT address the 99.7% holder (ticker_cache) — that's Issue 2's job
- 10,000-row default LIMIT could theoretically clip a future use case that needs more, but at 1 row/hour it's >1 year of data

### Risks
- None significant. Index creation on a 21k-row table is sub-second; ALTER schema with IF NOT EXISTS makes the migration idempotent.

## Verification plan

After deploy:
1. `EXPLAIN QUERY PLAN` on the production DB confirms ASC index usage
2. New `FEAR_GREED_HISTORY_QUERY` log lines appear in DEBUG
3. DB_LOCK_WAIT distribution remains ticker_cache-dominated (no regression)
4. Shadow mode `/sentiment` Telegram command still returns Fear & Greed history
5. MCP `get_fear_greed_index` tool with `include_history=True` returns same data shape
