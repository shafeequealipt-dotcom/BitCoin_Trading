"""Retention engine — compresses old data and maintains database health.

Runs daily (triggered by DailyRollup) to:
  - Compress ticker_snapshots: 30-90 days → hourly, 90+ days → daily
  - Compress wallet_snapshots: same schedule
  - Delete expired open_interest_history (90+ days)
  - Run ANALYZE, WAL checkpoint
  - Run VACUUM weekly
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("retention")

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


class RetentionEngine:
    """Database retention and cleanup engine.

    Args:
        db: Connected DatabaseManager instance.
        config: Shadow configuration.
    """

    def __init__(self, db: DatabaseManager, config: ShadowConfig) -> None:
        self._db = db
        self._config = config
        self._hourly_after = getattr(config.database, "ticker_retention_days", 30)
        self._daily_after = getattr(config.database, "oi_retention_days", 90)
        self._oi_retention = getattr(config.database, "oi_retention_days", 90)
        self._vacuum_interval = 7  # days

    async def run_cleanup(self) -> dict[str, Any]:
        """Run all retention tasks. Returns summary dict."""
        start = time.time()
        db_path = self._db.db_path
        size_before = _file_size_mb(db_path)

        results: dict[str, Any] = {}

        # 1. Compress tickers (Unix ms timestamps)
        results["ticker_hourly"] = await self._compress_ticker_to_hourly()
        results["ticker_daily"] = await self._compress_ticker_to_daily()

        # 2. Compress wallet snapshots (ISO timestamps)
        results["wallet_hourly"] = await self._compress_wallet_to_hourly()
        results["wallet_daily"] = await self._compress_wallet_to_daily()

        # 3. Delete expired OI
        results["oi_deleted"] = await self._delete_expired_oi()

        # 4. Maintenance
        await self._run_analyze()
        await self._run_wal_checkpoint()
        results["vacuum_run"] = await self._run_vacuum()

        size_after = _file_size_mb(db_path)
        duration = time.time() - start

        results["db_size_before_mb"] = round(size_before, 1)
        results["db_size_after_mb"] = round(size_after, 1)
        results["space_reclaimed_mb"] = round(size_before - size_after, 1)
        results["duration_seconds"] = round(duration, 1)

        total_deleted = sum(
            v for k, v in results.items()
            if isinstance(v, int) and k != "vacuum_run"
        )
        log.info(
            "Retention cleanup: {n} rows removed, {mb:.1f}MB reclaimed, took {sec:.1f}s",
            n=total_deleted, mb=results["space_reclaimed_mb"], sec=duration,
        )

        return results

    # ─── Compression: ticker_snapshots (Unix ms timestamps) ──────────

    async def _compress_ticker_to_hourly(self) -> int:
        """Compress ticker_snapshots 30-90 days old to one per (symbol, hour)."""
        now_ms = _now_ms()
        start = now_ms - (self._daily_after * DAY_MS)
        end = now_ms - (self._hourly_after * DAY_MS)
        return await self._compress_unix_table("ticker_snapshots", start, end, HOUR_MS, has_symbol=True)

    async def _compress_ticker_to_daily(self) -> int:
        """Compress ticker_snapshots 90+ days old to one per (symbol, day)."""
        now_ms = _now_ms()
        end = now_ms - (self._daily_after * DAY_MS)
        start = 0  # everything older than 90 days
        return await self._compress_unix_table("ticker_snapshots", start, end, DAY_MS, has_symbol=True)

    # ─── Compression: wallet_snapshots (ISO string timestamps) ───────

    async def _compress_wallet_to_hourly(self) -> int:
        """Compress wallet_snapshots 30-90 days old to one per hour."""
        now = datetime.now(timezone.utc)
        start_iso = (now - timedelta(days=self._daily_after)).isoformat()
        end_iso = (now - timedelta(days=self._hourly_after)).isoformat()
        return await self._compress_iso_table("wallet_snapshots", start_iso, end_iso, hour_group=True)

    async def _compress_wallet_to_daily(self) -> int:
        """Compress wallet_snapshots 90+ days old to one per day."""
        now = datetime.now(timezone.utc)
        end_iso = (now - timedelta(days=self._daily_after)).isoformat()
        return await self._compress_iso_table("wallet_snapshots", "2000-01-01", end_iso, hour_group=False)

    # ─── Generic compression helpers ─────────────────────────────────

    async def _compress_unix_table(
        self, table: str, start_ms: int, end_ms: int, bucket_ms: int, has_symbol: bool
    ) -> int:
        """Compress a table with Unix ms timestamps by keeping one row per bucket."""
        if start_ms >= end_ms:
            return 0

        count_before = await self._count(table, f"timestamp >= {start_ms} AND timestamp < {end_ms}")
        if count_before <= 1:
            return 0

        partition = f"symbol, (timestamp / {bucket_ms})" if has_symbol else f"(timestamp / {bucket_ms})"

        await self._db.execute(f"""
            DELETE FROM {table}
            WHERE timestamp >= ? AND timestamp < ?
            AND rowid NOT IN (
                SELECT rowid FROM (
                    SELECT rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY {partition}
                               ORDER BY (timestamp % {bucket_ms}) ASC
                           ) as rn
                    FROM {table}
                    WHERE timestamp >= ? AND timestamp < ?
                ) WHERE rn = 1
            )
        """, (start_ms, end_ms, start_ms, end_ms))

        count_after = await self._count(table, f"timestamp >= {start_ms} AND timestamp < {end_ms}")
        deleted = count_before - count_after
        if deleted > 0:
            log.info("{table} compressed: {before} → {after} ({deleted} removed)",
                     table=table, before=count_before, after=count_after, deleted=deleted)
        return deleted

    async def _compress_iso_table(
        self, table: str, start_iso: str, end_iso: str, hour_group: bool
    ) -> int:
        """Compress a table with ISO string timestamps."""
        count_before = await self._count(
            table, f"timestamp >= '{start_iso}' AND timestamp < '{end_iso}'"
        )
        if count_before <= 1:
            return 0

        # Group by date+hour or just date
        if hour_group:
            group_expr = "substr(timestamp, 1, 13)"  # "2026-03-26T14"
        else:
            group_expr = "substr(timestamp, 1, 10)"  # "2026-03-26"

        await self._db.execute(f"""
            DELETE FROM {table}
            WHERE timestamp >= ? AND timestamp < ?
            AND rowid NOT IN (
                SELECT rowid FROM (
                    SELECT rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY {group_expr}
                               ORDER BY timestamp ASC
                           ) as rn
                    FROM {table}
                    WHERE timestamp >= ? AND timestamp < ?
                ) WHERE rn = 1
            )
        """, (start_iso, end_iso, start_iso, end_iso))

        count_after = await self._count(
            table, f"timestamp >= '{start_iso}' AND timestamp < '{end_iso}'"
        )
        deleted = count_before - count_after
        if deleted > 0:
            log.info("{table} compressed: {before} → {after} ({deleted} removed)",
                     table=table, before=count_before, after=count_after, deleted=deleted)
        return deleted

    # ─── Deletion ────────────────────────────────────────────────────

    async def _delete_expired_oi(self) -> int:
        """Delete open_interest_history older than retention period."""
        cutoff_ms = _now_ms() - (self._oi_retention * DAY_MS)
        count = await self._count("open_interest_history", f"timestamp < {cutoff_ms}")
        if count == 0:
            return 0
        await self._db.execute(
            "DELETE FROM open_interest_history WHERE timestamp < ?", (cutoff_ms,)
        )
        log.info("OI cleanup: {n} rows deleted ({days}+ days)", n=count, days=self._oi_retention)
        return count

    # ─── Maintenance ─────────────────────────────────────────────────

    async def _run_analyze(self) -> None:
        """Update query planner statistics."""
        try:
            await self._db.execute("ANALYZE")
            log.debug("ANALYZE complete")
        except Exception as e:
            log.warning("ANALYZE failed: {err}", err=str(e))

    async def _run_wal_checkpoint(self) -> None:
        """Flush WAL to main database file."""
        try:
            await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            log.debug("WAL checkpoint complete")
        except Exception as e:
            log.warning("WAL checkpoint failed: {err}", err=str(e))

    async def _run_vacuum(self) -> bool:
        """Run VACUUM if not done in the last 7 days."""
        try:
            row = await self._db.fetch_one(
                "SELECT value FROM shadow_settings WHERE key = 'last_vacuum_date'"
            )
            last_vacuum = row["value"] if row else None

            if last_vacuum:
                last_date = datetime.fromisoformat(last_vacuum).date()
                days_since = (datetime.now(timezone.utc).date() - last_date).days
                if days_since < self._vacuum_interval:
                    log.debug("VACUUM skipped (last: {d} days ago)", d=days_since)
                    return False

            size_before = _file_size_mb(self._db.db_path)
            await self._db.execute("VACUUM")
            size_after = _file_size_mb(self._db.db_path)

            today = datetime.now(timezone.utc).date().isoformat()
            await self._db.execute(
                "INSERT OR REPLACE INTO shadow_settings (key, value, updated_at) "
                "VALUES ('last_vacuum_date', ?, datetime('now'))",
                (today,),
            )

            log.info(
                "VACUUM: {before:.1f}MB → {after:.1f}MB (reclaimed {saved:.1f}MB)",
                before=size_before, after=size_after, saved=size_before - size_after,
            )
            return True
        except Exception as e:
            log.warning("VACUUM failed: {err}", err=str(e))
            return False

    # ─── Health metrics ──────────────────────────────────────────────

    async def get_health_metrics(self) -> dict[str, Any]:
        """Compute database health metrics."""
        db_path = self._db.db_path
        db_size = _file_size_mb(db_path)
        wal_path = db_path + "-wal"
        wal_size = _file_size_mb(wal_path) if os.path.exists(wal_path) else 0

        tables = {}
        for table in ["klines", "ticker_snapshots", "open_interest_history",
                       "trade_history", "wallet_snapshots", "daily_summary", "funding_rates"]:
            try:
                row = await self._db.fetch_one(f"SELECT COUNT(*) as cnt FROM {table}")
                tables[table] = row["cnt"] if row else 0
            except Exception:
                tables[table] = 0

        last_vacuum_row = await self._db.fetch_one(
            "SELECT value FROM shadow_settings WHERE key = 'last_vacuum_date'"
        )
        last_vacuum = last_vacuum_row["value"] if last_vacuum_row else None

        return {
            "db_size_mb": round(db_size, 1),
            "wal_size_mb": round(wal_size, 1),
            "table_rows": tables,
            "last_vacuum": last_vacuum,
        }

    # ─── Helpers ─────────────────────────────────────────────────────

    async def _count(self, table: str, where: str) -> int:
        """Count rows matching a WHERE clause."""
        try:
            row = await self._db.fetch_one(f"SELECT COUNT(*) as cnt FROM {table} WHERE {where}")
            return row["cnt"] if row else 0
        except Exception:
            return 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0
