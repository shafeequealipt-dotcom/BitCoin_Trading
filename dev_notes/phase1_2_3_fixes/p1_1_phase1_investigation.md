# P1-1 Phase 1 — auto_vacuum Migration Investigation

## 1. The Defect

Live SQLite database `/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db` runs in `auto_vacuum=0` (NONE), but the codebase expects `auto_vacuum=2` (INCREMENTAL). The mismatch causes:

- `cleanup_worker.tick()` (`src/workers/cleanup_worker.py:229–238`) detects mode != 2 every hour and emits `DB_VACUUM_MIGRATION_REQUIRED` (dedup'd to once per day).
- `DatabaseManager.connect()` (`src/database/connection.py:161–168`) emits `DB_AUTO_VACUUM_NOT_INCREMENTAL` on every connection open (which is why the 5-hour log has 2 occurrences — one per worker process).
- `PRAGMA incremental_vacuum(1000)` is NEVER invoked (the `else` branch at `cleanup_worker.py:240` only fires when mode == 2). Consequence: freelist pages accumulate indefinitely.

Current freelist on live DB: **858 pages × 4096 B = 3.35 MB reclaimable** (a small leak today, but unbounded growth across weeks).

## 2. Code-Path Verification (read end-to-end)

### `scripts/t1_4_migrate_to_incremental_vacuum.sh` (98 lines, full read)

Behavior:

1. Argparse: optional first arg is DB path; defaults `data/trading.db`.
2. Pre-flight: requires `sqlite3` binary on PATH; requires DB file exists.
3. Reads current `PRAGMA auto_vacuum`. Exits 0 immediately if already == 2.
4. Locking probe: `fuser "$DB_PATH"` — if any process holds the file open, exits 1 with instructions to stop services.
5. Runs (timed):

   ```sql
   PRAGMA auto_vacuum = INCREMENTAL;
   VACUUM;
   ```

6. Re-reads `PRAGMA auto_vacuum`; exits 1 if != 2; exits 0 with restart instructions if == 2.

Notable: the script does NOT take a backup. The header comment explicitly states a backup is a pre-condition (`data/trading.db.bak-pre-six-tier-fixes-<timestamp>`). I will take one as the first action.

Notable: `fuser` may not detect aiosqlite ephemeral file handles in every state. The systemd stop is the authoritative gate.

### `src/database/connection.py:127–192` `DatabaseManager.connect()`

Sets PRAGMAs in this order on each connect: `journal_mode=WAL`, `busy_timeout=10000`, `foreign_keys=ON`, `cache_size=-65536`, `synchronous=NORMAL`, `wal_autocheckpoint=2000`, `journal_size_limit=104857600`, `temp_store=MEMORY`, `mmap_size=268435456`. None of these flip `auto_vacuum` — auto_vacuum is a persistent file-level setting that can only be changed by `PRAGMA auto_vacuum = N; VACUUM;` (the rewrite is mandatory).

The block at lines 156–176 reads `PRAGMA auto_vacuum`, emits `DB_AUTO_VACUUM_NOT_INCREMENTAL` warning if != 2, or `DB_AUTO_VACUUM_OK` info if == 2. Wrapped in try/except so a PRAGMA probe failure doesn't crash boot.

### `src/workers/cleanup_worker.py:208–251`

Hourly tick: reads `PRAGMA auto_vacuum`. If != 2, emits `DB_VACUUM_MIGRATION_REQUIRED` once per UTC date and skips. If == 2, runs `PRAGMA incremental_vacuum(1000)` and emits `VACUUM | mode=incremental pages=1000 success=Y` on success or `VACUUM_FAIL` on exception.

Key constants:

- `_INCREMENTAL_VACUUM_PAGES = 1000` (line 48) → 4 MB reclaimed per hour cap.
- `_CLEANUP_LARGE_BATCH_THRESHOLD = 1000` (line 37) → warning when pending rows for any retention table ≥ 1000.

Retention policies (lines 51–88): klines 7d, news 30d, sentiment 30d, fear_greed 90d, signals 30d, account_snapshots 90d, brain_decisions 60d, trade_thesis 60d (only if closed), regime_history 60d, plus market_snapshots 30d, position_snapshots 7d, event_log 30d, claude_decisions 90d.

Per-tick flow:

1. WAL `checkpoint(PASSIVE)`.
2. `log_lock_histogram()`.
3. `_sweep_klines_retention()` — per (symbol, timeframe) keep newest 300 rows.
4. Loop over RETENTION_POLICIES — pre-count pending, warn if ≥ 1000, DELETE.
5. auto_vacuum check → incremental_vacuum or migration-required warning.
6. Log aggregate `CLEANUP` line + DB size.
7. Daily rollup into `daily_summary` if yesterday not yet summarized.

The CLEANUP_LARGE_BATCH events in the 5-hour log (5 occurrences) are from retention tables where pending > 1000 — NOT from freelist accumulation directly. But freelist accumulation amplifies the cost of those eventual large deletes because the file can't shrink and SQLite has to walk the freelist to reuse pages. So the operator's correlation (auto_vacuum=0 → CLEANUP_LARGE_BATCH cascade) is indirect but real.

## 3. Live DB Profile

| Metric | Value |
|--------|-------|
| Size | 197.5 MB |
| Page size | 4096 B |
| Page count | 48 213 |
| Freelist count | 858 pages (3.35 MB) |
| `auto_vacuum` | 0 (NONE) — needs migration |
| `journal_mode` | WAL ✓ |
| -wal file | 8.8 MB |
| -shm file | 32 KB |

## 4. Migration Risk Assessment

### Operational risk

- **EXCLUSIVE lock during VACUUM** — by definition, no other connection can read or write while VACUUM rewrites the file. Workers and MCP server must be stopped.
- **Temp space** — VACUUM writes a new copy of the database into a sibling file in the same directory, then renames. Worst case: 2× DB size = ~400 MB free disk needed. Disk is GCP VM root; check `df -h` immediately before the run.
- **Time to run** — empirically for a 200 MB SQLite DB on local SSD: 5–20 s. Will be timed by the script and reported.
- **Service downtime** — total ~30–60 s: stop (~5 s) + backup copy (~5 s) + VACUUM (~15 s) + start (~10 s) + warmup (~10 s).

### Data-loss risk

- VACUUM is non-destructive: it rewrites pages but never deletes user data. Schema is preserved. Indexes are rebuilt during VACUUM.
- Fresh backup taken before migration absorbs any unrecoverable disk-level failure.
- WAL checkpoint runs implicitly as part of the workers being stopped (clean shutdown).

### Recovery plan if migration fails

1. Script exits non-zero. DB may have `auto_vacuum=2` set but VACUUM mid-rewrite was interrupted (rare — SQLite is crash-safe; partial VACUUM rolls back on next open).
2. If the file is corrupted (extreme): `cp data/trading.db.bak-p1-1-<timestamp> data/trading.db` + `rm data/trading.db-wal data/trading.db-shm`. Restart services.
3. The original codepath (`DB_VACUUM_MIGRATION_REQUIRED` warning) continues to fire — no regression in behavior on rollback.

### Risks the migration does NOT mitigate

- The CLEANUP_LARGE_BATCH events come from retention tables with > 1000 pending rows at cleanup time. The migration reduces the cost of the subsequent DELETE (freelist reused incrementally instead of accumulating), but does NOT change the pending count. Watch the post-migration `CLEANUP_RUN | table=X pending_pre=N` line — if a specific table consistently shows pending > 1000, the retention policy itself needs tuning (out of scope for P1-1).

## 5. Observability Already In Place

- `DB_AUTO_VACUUM_OK | mode=INCREMENTAL` on every connect (`connection.py:170–172`)
- `DB_AUTO_VACUUM_NOT_INCREMENTAL` warning on every connect when not migrated (`connection.py:162–168`)
- `VACUUM | mode=incremental pages=1000 success=Y` hourly when migrated (`cleanup_worker.py:244–247`)
- `VACUUM_FAIL | mode=incremental err='...'` on exception (`cleanup_worker.py:249–251`)
- `DB_VACUUM_MIGRATION_REQUIRED` daily-dedup'd warning when not migrated (`cleanup_worker.py:232–237`)

The prompt's Rule 6 requires a new `DB_INCREMENTAL_VACUUM_OK pages_freed=N elapsed_ms=N` tag per hourly run. The existing `VACUUM | mode=incremental pages=1000 success=Y` line is close but missing:

- `pages_freed` — actual reclaimed pages (not just the cap). SQLite returns this from `PRAGMA freelist_count` before vs after, or from `PRAGMA incremental_vacuum`'s side effects.
- `elapsed_ms` — wall-clock duration of the PRAGMA call.

I'll add a minimal observability commit on top of the migration to align with Rule 6 (one-line tag rename + delta calculation) — but only if the operator wants it. The existing tag is sufficient for verification.

## 6. Contracts — Before vs After

### `cleanup_worker.tick()`

- **Before migration:** Every hour, fires `DB_VACUUM_MIGRATION_REQUIRED` warning (dedup'd to daily) + skips reclamation. Freelist grows monotonically until next manual VACUUM.
- **After migration:** Every hour, fires `VACUUM | mode=incremental pages=1000 success=Y` (or `VACUUM_FAIL` on error). Up to 1000 freelist pages (4 MB) reclaimed per hour, holding the EXCLUSIVE lock for < 1 s typically (constant-time at N pages).

No public signature change. No caller adjustments.

### `DatabaseManager.connect()`

- **Before migration:** Emits `DB_AUTO_VACUUM_NOT_INCREMENTAL | current_mode=0 expected=2 | …`
- **After migration:** Emits `DB_AUTO_VACUUM_OK | mode=INCREMENTAL | …`

No public signature change.

## 7. The Plan's Operator Decision Points (preview for Phase 2 report)

Three orthogonal choices the operator picks:

**Backup strategy:**
A. Run as-is (rely on existing testnet backup from March).
B. Take fresh `data/trading.db.bak-p1-1-<timestamp>` copy first (~5 s, ~200 MB on root disk). **RECOMMENDED.**
C. Snapshot at GCP-disk level (heavier, longer). Overkill for this scope.

**Maintenance window:**
A. Stop both services (`trading-workers`, `trading-mcp-sse`), run, restart. **RECOMMENDED.** Total downtime ~30–60 s.
B. Some online VACUUM trick — NOT POSSIBLE in SQLite, requires EXCLUSIVE lock.
C. Wait for a low-activity window — not necessary, downtime is short.

**Observability:**
A. Ship migration only — existing `VACUUM | mode=incremental` log suffices.
B. Ship migration + small commit adding `pages_freed`/`elapsed_ms` to the log line to exactly match prompt Rule 6.

Recommendation will be presented in Phase 2 report.

## 8. Verification Approach (after migration)

Immediate (script's own output):

- Script prints `Post-migration auto_vacuum mode: 2`.
- Script exit code 0.

First hour (one cleanup tick):

- `tail -F data/logs/workers.log | grep -E 'VACUUM|DB_AUTO_VACUUM'` shows `VACUUM | mode=incremental pages=1000 success=Y`.
- No `DB_VACUUM_MIGRATION_REQUIRED` lines in the next 24 h.

Two-hour soak (Phase 4 gate):

- 2 consecutive `VACUUM | mode=incremental` log lines.
- `CLEANUP_LARGE_BATCH` rate drops materially vs the 5-event-per-5h baseline (target: ≤ 1 per 5 h).
- `DB_LOCK_WAIT` events count drops or stays flat vs baseline of 23 per 5 h (target: ≤ 15 per 5 h).

Manual confirmation (one-shot):

```
sqlite3 data/trading.db "PRAGMA auto_vacuum; PRAGMA freelist_count;"
# Expect: 2  (and freelist_count trending DOWN every hour)
```

## 9. Out of Scope for P1-1

- Retention-policy tuning if `CLEANUP_RUN | pending_pre=N` reveals a specific table is hot.
- Investigating the `fetch_all:` DB_LOCK_WAIT chain (scheduled_reports → journal lookback). Deferred to P1-2 re-evaluation.
- Changing the `_INCREMENTAL_VACUUM_PAGES = 1000` constant. The current 4 MB/hour reclamation rate is appropriate for the current freelist growth rate.

## 10. NOT FOUND

- No `BACKUP_OK`-style structured log around backup ops in the existing codebase — the trading-backup.service handles that separately. No need to fabricate one for this migration.
- No automated backup-then-migrate combined script. The migration script itself defers backup to the operator. I will take the backup manually as part of the implementation step.
