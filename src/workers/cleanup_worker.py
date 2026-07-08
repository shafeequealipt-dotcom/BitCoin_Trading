"""Cleanup worker: deletes old data to prevent database growth. Runs VACUUM daily."""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")

# Phase 3 (post-Layer-1 fix). Per-table extra WHERE clauses that the
# cleanup loop appends to the standard ``WHERE {ts_col} < ?`` template.
# Used to add safety filters that go beyond simple time-based retention
# — most importantly to never delete a still-OPEN trade_thesis even if
# its opened_at falls past the retention window. Without this filter,
# a long-held position could have its journal pruned out from under
# us. Keys are table names; values are SQL fragments without leading
# AND. Empty / missing → no extra filter.
_RETENTION_EXTRA_FILTERS: dict[str, str] = {
    # trade_thesis: only delete CLOSED journals and VOIDED reservations.
    # Open/reserving theses stay regardless of age (rare-but-real
    # long-running positions; reserving rows are resolved by the sweep).
    # 'voided' rows (durable-open: rejected/raised open attempts) carry no
    # learning value, so age-pruning them keeps the table from accreting
    # one row per failed open. Safety > storage.
    "trade_thesis": "status IN ('closed', 'voided')",
}

# Phase 3 (post-Layer-1 fix). Threshold for emitting a CLEANUP_LARGE_BATCH
# warning. Most cleanup ticks delete < 1000 rows; a single tick deleting
# more is unexpected (e.g. retention shortened, backfill produced many
# old rows, or a soft-delete migration regression). Emit at WARNING so
# operators notice without blocking.
_CLEANUP_LARGE_BATCH_THRESHOLD = 1000

# T1-4 / F4 fix (six-tier-fixes 2026-05-11). Pages reclaimed per
# PRAGMA incremental_vacuum invocation on each hourly cleanup tick.
# 1000 pages * 4 KB page_size = 4 MB reclaimed per hour. SQLite
# completes this in < 1 s on the 172 MB production DB; no exclusive
# lock is held long enough to cascade through worker ticks (compare to
# the legacy daily full VACUUM that froze writers up to 21 s today).
# Requires the DB to be in auto_vacuum=INCREMENTAL mode — see
# scripts/t1_4_migrate_to_incremental_vacuum.sh for the one-time
# migration step.
_INCREMENTAL_VACUUM_PAGES = 1000

# Retention policies: (table_name, max_age_days, timestamp_column)
RETENTION_POLICIES: list[tuple[str, int, str]] = [
    # Safety net below the per-(symbol, timeframe) insert-time cap in
    # MarketRepository.save_klines (keep 300). 7 days covers the worst case
    # where a symbol stops updating (insert-time cap no longer prunes it).
    ("klines", 7, "timestamp"),
    ("orderbook_snapshots", 7, "timestamp"),
    ("news_articles", 30, "published_at"),
    ("reddit_posts", 14, "created_at"),
    ("aggregated_sentiment", 30, "created_at"),
    # economic_calendar: table exists but is not populated (no worker integration)
    ("fear_greed_index", 90, "timestamp"),
    ("funding_rates", 30, "fetched_at"),
    ("open_interest", 30, "timestamp"),
    ("signals", 30, "created_at"),
    ("account_snapshots", 90, "updated_at"),
    # Data lake tables (#10)
    ("market_snapshots", 30, "created_at"),
    ("position_snapshots", 7, "created_at"),
    ("event_log", 30, "created_at"),
    ("claude_decisions", 90, "created_at"),
    # Prefetch/Performance fix — three tables that were accumulating forever.
    # Operator-set retention: keep 60 days of audit/learning history on all
    # three tables. Rationale: matches `claude_decisions` retention (90d) and
    # gives TIAS/ML lookback a full 2-month window; aligns conservatively with
    # the long-running backtest needs expressed after the initial 7/14/3-day
    # policy deleted ~2200 rows in one CleanupWorker tick.
    # brain_decisions:  one row per Call A (~12/hr). Readers (telegram, learning_repo,
    #                   brain_v2) all LIMIT/DATE('now').
    # trade_thesis:     opened_at (NOT closed_at) — daily_summary rollup at line 124
    #                   queries closed trades from yesterday; retention window never
    #                   affects that read path.
    # regime_history:   write-only audit (no src/ readers). coin_regime_history has
    #                   its own 24h pruning in regime_worker.py:175-183.
    ("brain_decisions", 60, "created_at"),
    ("trade_thesis", 60, "opened_at"),
    ("regime_history", 60, "detected_at"),
    # trade_log + daily_summary: forever (no retention)
]


class CleanupWorker(BaseWorker):
    """Deletes old data from tables with configurable retention policies.

    Runs VACUUM at most once per day to reclaim space.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        super().__init__(
            name="cleanup_worker",
            interval_seconds=3600.0,  # Every hour
            settings=settings,
            db=db,
        )
        self._last_vacuum_date: str = ""

    async def tick(self) -> None:
        """Delete old data and optionally run VACUUM."""
        total_deleted = 0
        tables_cleaned = 0

        # Phase 2 (post-Layer-1 fix): explicit WAL checkpoint at the top of
        # the hourly cleanup tick. SQLite's auto-checkpoint (every 2000
        # frames by config) only opportunistically truncates when no
        # readers hold a snapshot — under steady load that condition is
        # rarely satisfied, and the live -wal file was observed pinned at
        # the 100 MiB cap. PASSIVE mode never blocks. We tolerate failures
        # silently — VACUUM later in the same tick provides the harder
        # reclamation guarantee.
        try:
            await self.db.checkpoint(mode="PASSIVE")
        except Exception as e:
            log.debug(
                "WAL_CHECKPOINT_SKIP | err='{err}'", err=str(e)[:120]
            )

        # Phase 9 (post-Layer-1 fix): emit a DB lock-wait histogram so
        # operators have a hourly snapshot of contention. Cheap — reads
        # a bounded ring buffer.
        try:
            self.db.log_lock_histogram()
        except Exception as e:
            log.debug(
                "DB_LOCK_HIST_SKIP | err='{err}'", err=str(e)[:120]
            )

        # Phase 2 (Stage-1/2 fix): per (symbol, timeframe) row-count retention
        # sweep. This is the belt-and-suspenders backstop for the now-deferred
        # cleanup inside MarketRepository.save_klines. Runs once per hour with
        # no lock contention against the kline_worker hot path. See
        # ``_sweep_klines_retention`` for the full rationale.
        try:
            sweep_stats = await self._sweep_klines_retention()
            if sweep_stats["deleted"] > 0:
                total_deleted += sweep_stats["deleted"]
                tables_cleaned += 1
        except Exception as e:
            log.warning(
                "KLINES_RETENTION_SWEEP_FAIL | err='{err}'",
                err=str(e)[:150],
            )

        # System 1 (observability): bound the brain-capture dump directory.
        # This is a file-only sweep (no SQL, never a trading or protected
        # table). Fire-and-forget — a failure logs at WARNING and never aborts
        # the rest of the cleanup tick.
        try:
            self._sweep_stage2_dumps()
        except Exception as e:
            log.warning(
                "STAGE2_PRUNE_FAIL | err='{err}'", err=str(e)[:150],
            )

        for table, max_days, ts_col in RETENTION_POLICIES:
            try:
                cutoff = (now_utc() - timedelta(days=max_days)).isoformat()

                # Phase 3 (post-Layer-1 fix). Build the WHERE clause
                # with optional per-table safety filter. trade_thesis
                # gets ``status='closed'`` so a long-running open
                # position can never have its journal pruned out from
                # under it.
                extra_filter = _RETENTION_EXTRA_FILTERS.get(table, "").strip()
                if extra_filter:
                    where_clause = f"{ts_col} < ? AND ({extra_filter})"
                else:
                    where_clause = f"{ts_col} < ?"

                # Phase 3 (post-Layer-1 fix). Pre-flight count so the
                # CLEANUP_RUN log includes ``pending`` AND we can warn
                # on unexpectedly large batches BEFORE the delete.
                pending_row = await self.db.fetch_one(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE {where_clause}",
                    (cutoff,),
                )
                pending = int(pending_row["n"]) if pending_row else 0

                if pending >= _CLEANUP_LARGE_BATCH_THRESHOLD:
                    log.warning(
                        f"CLEANUP_LARGE_BATCH | table={table} pending={pending} "
                        f"threshold={_CLEANUP_LARGE_BATCH_THRESHOLD} "
                        f"retention_days={max_days} | {ctx()}"
                    )

                cursor = await self.db.execute(
                    f"DELETE FROM {table} WHERE {where_clause}",
                    (cutoff,),
                )
                deleted = cursor.rowcount if hasattr(cursor, 'rowcount') else 0

                # Phase 3 (post-Layer-1 fix). Per-table outcome at INFO.
                # Operators see "what got deleted from where" without
                # grepping the aggregate CLEANUP line below.
                log.info(
                    f"CLEANUP_RUN | table={table} deleted={deleted} "
                    f"pending_pre={pending} retention_days={max_days} "
                    f"ts_col={ts_col} extra_filter='{extra_filter}' | {ctx()}"
                )

                if deleted > 0:
                    total_deleted += deleted
                    tables_cleaned += 1
            except Exception as e:
                log.warning("Cleanup failed for {t}: {err}", t=table, err=str(e))

        # T1-4 / F4 fix (six-tier-fixes 2026-05-11) — replace daily full
        # VACUUM with hourly PRAGMA incremental_vacuum(N). The full VACUUM
        # held the EXCLUSIVE lock for up to 21 s (live evidence at 11:32
        # today), cascading into F2 / F3 / F5 / F8. incremental_vacuum is
        # constant-time at N pages and completes in <1 s.
        #
        # Requires the DB to be in auto_vacuum=INCREMENTAL mode (PRAGMA
        # auto_vacuum returns 2). New databases get this from
        # connection.py boot wiring. Existing databases require a one-time
        # migration: see scripts/t1_4_migrate_to_incremental_vacuum.sh.
        # Until the migration runs, this block emits a single warning per
        # day and skips — full VACUUM is intentionally NOT run from this
        # path any more so the cascade can never re-fire.
        # P1-1 (2026-05-13): the prior code used ``row[0]`` to read the
        # PRAGMA result, but ``DatabaseManager.fetch_one`` returns a dict
        # whose keys are column names — ``row[0]`` raises KeyError on it
        # and was silently swallowed by the outer except. Effect: even
        # after the operator ran ``scripts/t1_4_migrate_to_incremental_vacuum.sh``
        # and the file was in mode=2, this branch reported mode=0 and the
        # incremental_vacuum call below was never reached. Key by column
        # name to read the actual mode.
        try:
            row = await self.db.fetch_one("PRAGMA auto_vacuum")
            current_auto_vacuum = int(row["auto_vacuum"]) if row else 0
        except Exception as _ave:
            current_auto_vacuum = 0
            log.debug(
                f"AUTO_VACUUM_PROBE_FAIL | err='{str(_ave)[:120]}'"
            )
        if current_auto_vacuum != 2:
            today = now_utc().strftime("%Y-%m-%d")
            if today != self._last_vacuum_date:
                log.warning(
                    f"DB_VACUUM_MIGRATION_REQUIRED | "
                    f"current_auto_vacuum={current_auto_vacuum} expected=2 | "
                    f"run scripts/t1_4_migrate_to_incremental_vacuum.sh | "
                    f"freelist_pages may grow until migration runs | {ctx()}"
                )
                self._last_vacuum_date = today
        else:
            # P1-1 (2026-05-13): track freelist before/after and wall-clock
            # so the hourly emit reports actual reclamation (pages_freed)
            # and the time the EXCLUSIVE lock was held (elapsed_ms). The
            # operator can now see when the freelist is growing faster
            # than the cap can reclaim (pages_freed == _INCREMENTAL_VACUUM_PAGES
            # for several ticks in a row implies the cap should be raised).
            try:
                free_before_row = await self.db.fetch_one(
                    "PRAGMA freelist_count"
                )
                freelist_before = (
                    int(free_before_row["freelist_count"])
                    if free_before_row else 0
                )
                t0 = time.monotonic()
                await self.db.execute(
                    f"PRAGMA incremental_vacuum({_INCREMENTAL_VACUUM_PAGES})"
                )
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                free_after_row = await self.db.fetch_one(
                    "PRAGMA freelist_count"
                )
                freelist_after = (
                    int(free_after_row["freelist_count"])
                    if free_after_row else 0
                )
                pages_freed = max(0, freelist_before - freelist_after)
                log.info(
                    f"DB_INCREMENTAL_VACUUM_OK | pages_freed={pages_freed} "
                    f"elapsed_ms={elapsed_ms:.0f} "
                    f"freelist_before={freelist_before} "
                    f"freelist_after={freelist_after} "
                    f"pages_cap={_INCREMENTAL_VACUUM_PAGES} | {ctx()}"
                )
            except Exception as e:
                log.warning(
                    f"VACUUM_FAIL | mode=incremental err='{str(e)[:120]}' | {ctx()}"
                )

        # Log database size
        db_size = 0.0
        try:
            db_path = self.db.db_path
            if os.path.exists(db_path):
                db_size = os.path.getsize(db_path) / (1024 * 1024)
        except Exception:
            pass

        log.info(f"CLEANUP | deleted={total_deleted} tables={tables_cleaned} db_size={db_size:.1f}MB | {ctx()}")
        log.info(
            "Cleanup worker: deleted {n} old records across {t} tables | DB size: {sz:.1f} MB",
            n=total_deleted, t=tables_cleaned, sz=db_size,
        )

        # Daily summary rollup (#10: write_daily_summary)
        try:
            yesterday = (now_utc() - timedelta(days=1)).strftime("%Y-%m-%d")
            existing = await self.db.fetch_one(
                "SELECT id FROM daily_summary WHERE date = ?", (yesterday,),
            )
            if not existing:
                stats = await self.db.fetch_one(
                    """SELECT COUNT(*) as total,
                              SUM(CASE WHEN actual_pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                              SUM(CASE WHEN actual_pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
                              COALESCE(SUM(actual_pnl_pct), 0) as total_pnl,
                              COALESCE(MAX(actual_pnl_pct), 0) as best,
                              COALESCE(MIN(actual_pnl_pct), 0) as worst
                       FROM trade_thesis
                       WHERE DATE(closed_at) = ? AND status = 'closed'""",
                    (yesterday,),
                )
                if stats and (stats["total"] or 0) > 0:
                    await self.db.execute(
                        """INSERT OR REPLACE INTO daily_summary
                           (date, total_trades, wins, losses, total_pnl_pct,
                            best_trade_pct, worst_trade_pct)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (yesterday, stats["total"], stats["wins"] or 0,
                         stats["losses"] or 0, stats["total_pnl"] or 0,
                         stats["best"] or 0, stats["worst"] or 0),
                    )
                    log.info("Daily summary written for {date}: {t} trades",
                             date=yesterday, t=stats["total"])
        except Exception as e:
            log.debug("Daily summary rollup skipped: {err}", err=str(e))

    def _sweep_stage2_dumps(self) -> None:
        """Age- and count-bounded retention for the brain-capture dump dir.

        System 1 (observability): ``data/stage2_dumps`` holds one JSON file per
        brain call (the full prompt, system prompt, and response). The capture
        has no rotation of its own, so this hourly sweep prunes ``*.json`` older
        than ``capture_retention_days`` and, if the directory is still over
        ``capture_max_files``, deletes the oldest by mtime down to that cap.

        Fenced three ways so it can only ever touch dump files: (1) the
        directory leaf name must be exactly ``stage2_dumps``; (2) only the
        ``*.json`` glob is considered, so the ``.enabled`` sentinel (no
        ``.json`` suffix) is never matched; (3) each ``unlink`` is its own
        ``try/except``. It issues no SQL, so the protected-tables guard is not
        in play — it never touches the trading database or any protected table.
        """
        obs = getattr(self.settings, "observability", None)
        if obs is None:
            return
        dump_dir = Path(getattr(obs, "capture_dir", "data/stage2_dumps"))
        if not dump_dir.is_absolute():
            dump_dir = Path.cwd() / dump_dir
        # Fence 1: only ever the brain-capture directory.
        if dump_dir.name != "stage2_dumps" or not dump_dir.is_dir():
            return
        retention_days = int(getattr(obs, "capture_retention_days", 7))
        max_files = int(getattr(obs, "capture_max_files", 5000))
        cutoff = time.time() - retention_days * 86400.0

        # Fence 2: only *.json dump files (never .enabled, never the dir).
        files = list(dump_dir.glob("*.json"))
        scanned = len(files)
        deleted_age = 0
        survivors: list[tuple[float, Path]] = []
        for f in files:
            try:
                mtime = f.stat().st_mtime
            except Exception:
                continue
            if mtime < cutoff:
                try:
                    f.unlink()  # Fence 3: per-file isolation.
                    deleted_age += 1
                except Exception:
                    pass
            else:
                survivors.append((mtime, f))

        deleted_count = 0
        if len(survivors) > max_files:
            survivors.sort(key=lambda t: t[0])  # oldest first
            for _mt, f in survivors[: len(survivors) - max_files]:
                try:
                    f.unlink()
                    deleted_count += 1
                except Exception:
                    pass

        retained = scanned - deleted_age - deleted_count
        log.info(
            f"STAGE2_PRUNE | dir={dump_dir.name} scanned={scanned} "
            f"deleted_age={deleted_age} deleted_count={deleted_count} "
            f"retained={retained} cutoff_days={retention_days} "
            f"max_files={max_files} | {ctx()}"
        )

    async def _sweep_klines_retention(self) -> dict:
        """Per (symbol, timeframe) row-count retention sweep.

        Backstop for the deferred per-insert retention in
        ``MarketRepository.save_klines``. The hot-path cleanup runs only
        every N invocations to avoid lock contention with kline_worker;
        this sweep guarantees the klines table never exceeds retention
        even if a symbol goes quiet, the counter resets on a repository
        re-creation, or the deferred path skips for any other reason.

        Keeps the newest 300 rows per (symbol, timeframe), matching
        ``MarketRepository._KLINES_RETENTION_PER_SYMTF``. Each pair is
        processed in its own short transaction with an ``await
        asyncio.sleep(0)`` yield between pairs so cleanup never monopolises
        the event loop.

        Returns:
            ``{"pairs": int, "deleted": int, "el_ms": float}``.
        """
        # 300 matches MarketRepository._KLINES_RETENTION_PER_SYMTF. Kept
        # local (not imported) so the worker has no compile-time dep on
        # the repository class — the invariant is documented here and
        # in the repository.
        keep = 300
        t0 = time.time()
        try:
            pairs = await self.db.fetch_all(
                "SELECT DISTINCT symbol, timeframe FROM klines"
            )
        except Exception as e:
            log.warning(
                "KLINES_RETENTION_SWEEP_SKIP | err='{err}'",
                err=str(e)[:150],
            )
            return {"pairs": 0, "deleted": 0, "el_ms": 0.0}

        total_deleted = 0
        failed = 0
        for row in pairs:
            sym = row["symbol"]
            tf = row["timeframe"]
            try:
                cursor = await self.db.execute(
                    """
                    DELETE FROM klines
                    WHERE symbol = ? AND timeframe = ?
                      AND timestamp < (
                        SELECT timestamp FROM klines
                        WHERE symbol = ? AND timeframe = ?
                        ORDER BY timestamp DESC
                        LIMIT 1 OFFSET ?
                      )
                    """,
                    (sym, tf, sym, tf, keep - 1),
                )
                deleted = getattr(cursor, "rowcount", 0) or 0
                total_deleted += deleted
            except Exception as e:
                failed += 1
                log.debug(
                    "KLINES_SWEEP_PAIR_FAIL | sym={s} tf={t} err='{err}'",
                    s=sym, t=tf, err=str(e)[:80],
                )
            # Yield the event loop between pairs so a long sweep cannot
            # block watchdog / kline_worker ticks. Zero-delay sleep is
            # sufficient — it releases the scheduler without actual wait.
            await asyncio.sleep(0)

        el_ms = (time.time() - t0) * 1000
        log.info(
            f"KLINES_RETENTION_SWEEP | pairs={len(pairs)} deleted={total_deleted} "
            f"failed={failed} keep={keep} el={el_ms:.0f}ms | {ctx()}"
        )
        return {"pairs": len(pairs), "deleted": total_deleted, "el_ms": el_ms}
