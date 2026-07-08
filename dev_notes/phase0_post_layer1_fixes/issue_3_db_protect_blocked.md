# Issue 3 — DB_PROTECT_BLOCKED hourly DELETE FROM trade_thesis

**Status:** PRESENT — fires every hour.
**Tier:** 1 (silent data growth).
**Source observation:** `data/logs/general.log` (and workers.log) — 21 events between 2026-04-26 00:52 and 2026-04-27 07:18 UTC.

## A. Mechanism

CleanupWorker (`src/workers/cleanup_worker.py`, interval 3600s at line 71) iterates `RETENTION_POLICIES` (line 52 lists `("trade_thesis", "opened_at", 60)`), executes `DELETE FROM trade_thesis WHERE opened_at < ?` per table, catches the exception at line 134, logs `Cleanup failed for trade_thesis: ...` at WARNING.

The `assert_not_protected_destructive(sql, force=force_protected)` guard at `src/database/connection.py:295` runs before lock acquire. `trade_thesis` is in `PROTECTED_TABLES` at `src/database/protected_tables.py:36-46` (line 43). The guard logs `DB_PROTECT_BLOCKED | sql_kind=DELETE table=trade_thesis ...` at ERROR (line 135-138), then raises `ProtectedTableViolation` (line 139-144). The exception lands in CleanupWorker's catch.

The retry interval is 3600s (one full cleanup cycle). Every hour at HH:18 the cleanup tick fires; `trade_thesis` is rejected; the worker continues with other tables. There is no exponential backoff and no operator alert — the WARNING line is buried among other hourly events.

Observed cadence (from logs):
- 2026-04-26: 11 events (00:52, 00:58, 01:06, 01:36, 02:06, 03:50, 04:00, 04:07, 04:18, 05:23, 11:43, 16:35, 17:35, 18:36, 20:07, 22:18, 23:18, then 00:18 next day)
- 2026-04-27: 4 events through 07:18

The variable cadence in early hours (multiple within minutes) is because the worker was restarted multiple times during the Layer 1 restructure deployment; once stable, it's exactly hourly.

trade_thesis state on disk:
- 1154 rows from 2026-03-26 to 2026-04-26 (32 days; ~36 rows/day)
- Schema includes `opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `closed_at TIMESTAMP NULL`, `actual_pnl_pct REAL`, `lesson TEXT`, plus apex/exchange-mode columns.
- Indexes: `idx_trade_thesis_opened` on `opened_at`, `idx_trade_thesis_status`, `idx_trade_thesis_symbol_status`. Cleanup query uses the `opened_at` index — efficient.
- No soft-delete column (`deleted_at`) exists.

## B. Dependencies

- `ThesisManager` (writer) at `src/.../thesis_manager.py` (writes new theses on every Stage 2 decision).
- Strategist reads (Phase 3 audit fix `0afd4e2` references): `src/brain/strategist.py:1017, 1587` reads `_strategy_consensus_summary` (different table, mentioned for context — no relation to trade_thesis).
- `grep -rn 'trade_thesis' src/` to enumerate every consumer (will run during Phase 3 implementation; needed to confirm no consumer expects "all rows ever exist").
- `_extract_external_caller_frame()` at `src/database/connection.py:42-68` is unused by `assert_not_protected_destructive` today; it's a candidate for caller attribution enrichment.

## C. Constraints

- `trade_thesis` carries trade journals + lessons that strategist reads back on subsequent decisions. Aggressive deletion would erode the system's learning capability.
- Schema has no `deleted_at` — soft-delete migration would touch every reader. Heavyweight.
- Other tables in `PROTECTED_TABLES` (`tias_results`, `tias_analyses`, `trade_intelligence`, `trade_log`, `trade_history`, `thesis_store`, `virtual_positions`, `sniper_log`) must remain protected. Only remove `trade_thesis`.
- The 60-day retention on `opened_at` already filters; current dataset's oldest row is 32 days old → first run will delete nothing. So removing protection is safe in practice. But future growth past 60d will trigger non-zero deletes.

## D. Fix candidates

1. **Remove from PROTECTED_TABLES + TTL fence + caller attribution (chosen, per user direction).**
   - Drop `"trade_thesis"` from the frozenset at `protected_tables.py:43`.
   - Confirm `RETENTION_POLICIES` line 52 retention is sane (60 days) and configurable.
   - Add `_extract_external_caller_frame()` call in `assert_not_protected_destructive` so any FUTURE blocked DELETE shows `caller_file/caller_line/caller_method`.
   - Add `CLEANUP_RUN | table=trade_thesis deleted={n} oldest_kept_ts={ts}` per-table outcome at INFO.
   - Add `CLEANUP_LARGE_BATCH | table=trade_thesis pending={n}` warn if pending > 1000 (defensive; operator notification).
2. Stop the cleanup entirely (retention=forever). Rejected — table grows unbounded; storage will eventually fill.
3. Soft-delete migration. Rejected — heaviest change; consumers all need filtering; not warranted given the simpler TTL fence is safe.

## E. Observability gap

- `DB_PROTECT_BLOCKED` lacks caller attribution. Operators see "DELETE blocked" but not which scheduler emitted it. Easy to fix via the existing `_extract_external_caller_frame()`.
- No `CLEANUP_RUN` per-table outcome. Cleanup currently emits one summary line `CLEANUP | deleted=19851 tables=5 db_size=147.7MB` (line 163-164) — useful but doesn't tell which tables had non-zero deletes.
- No `CLEANUP_LARGE_BATCH` warn for unexpected backlog (e.g., if retention shortens or a backfill produces many old rows).

## F. Verification approach

- Wait until next HH:18 boundary after Phase 3 deploys → exactly one `CLEANUP_RUN | table=trade_thesis deleted=0 ...` at INFO (no rows past 60 days yet); zero `DB_PROTECT_BLOCKED` for trade_thesis.
- Synthesize a row with `opened_at = '2026-01-01'` (>60 days back), run the cleanup tick → `CLEANUP_RUN deleted=1`.
- Synthesize a future blocked DELETE on a still-protected table (e.g., tias_results) from a test caller → `DB_PROTECT_BLOCKED ... caller_file=tests/... caller_line=... caller_method=test_xxx`.
- 24h trace post-deploy → zero `Cleanup failed for trade_thesis` lines.

Pre-fix vs post-fix `SELECT COUNT(*) FROM trade_thesis`: pre = 1154, post-first-run = 1154 (no rows past 60d yet), post-30-days = trending toward steady-state ~36*60 = ~2160 rows.

## G. Rollback path

- Revert protected_tables.py change → trade_thesis re-protected; cleanup begins failing again. No DB state changes (since first run will delete 0 rows, there's nothing to restore).
- Revert cleanup_worker.py change → CLEANUP_RUN log goes away; existing CLEANUP summary preserved.
- Revert connection.py change → caller attribution goes away; old DB_PROTECT_BLOCKED format restored.

DB backup at `data/trading.db.pre-post-layer1-fixes.20260427.bak` is available if a row gets unexpectedly deleted (highly unlikely given oldest row is 32d < 60d retention).
