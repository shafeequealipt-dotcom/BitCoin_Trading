"""Data retention cleanup: delete old data with configurable per-table retention.

PROTECTED tables (TIAS, trade_log, thesis_store, virtual_positions, etc.)
are NEVER eligible for retention cleanup. The list is enforced at module
import time — adding a protected table to RETENTION_POLICIES will fail
loudly at startup, before any cleanup runs.
"""

import os
from datetime import timedelta

from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.protected_tables import PROTECTED_TABLES

log = get_logger("database")

# (table_name, timestamp_column, retention_days)
# Tables not listed here are PERMANENT and never cleaned
RETENTION_POLICIES: list[tuple[str, str, int]] = [
    ("klines", "created_at", 90),
    ("orderbook_snapshots", "timestamp", 7),
    ("news_articles", "published_at", 30),
    ("reddit_posts", "created_at", 14),
    ("aggregated_sentiment", "created_at", 30),
    ("fear_greed_index", "timestamp", 90),
    ("funding_rates", "fetched_at", 30),
    ("open_interest", "timestamp", 30),
    ("signals", "created_at", 30),
    ("session_log", "created_at", 30),
    ("account_snapshots", "updated_at", 90),
]


# Phase 0a defense-in-depth: refuse to load if any PROTECTED table is
# accidentally added to the retention list. The previous cleanup fix
# wiped TIAS data and caused $19 in losses — this assertion makes that
# class of regression impossible to ship.
def _validate_retention_policies() -> None:
    bad = [t for (t, _col, _days) in RETENTION_POLICIES if t.lower() in PROTECTED_TABLES]
    if bad:
        raise RuntimeError(
            f"RETENTION_POLICIES contains PROTECTED tables: {bad}. "
            f"These hold cumulative learning / audit data and must never "
            f"be wiped. Remove them from RETENTION_POLICIES."
        )


_validate_retention_policies()


async def cleanup_old_data(db: DatabaseManager, max_age_days: int | None = None) -> dict[str, int]:
    """Delete old data from tables with configurable retention.

    Permanent tables (trade_history, orders, strategy_performance,
    brain_decisions, etc.) are never cleaned.

    Args:
        db: Active DatabaseManager.
        max_age_days: Override default retention for all tables.

    Returns:
        Dict mapping table_name -> rows_deleted.
    """
    results: dict[str, int] = {}

    for table, ts_col, default_days in RETENTION_POLICIES:
        days = max_age_days if max_age_days is not None else default_days
        cutoff = (now_utc() - timedelta(days=days)).isoformat()
        try:
            cursor = await db.execute(
                f"DELETE FROM {table} WHERE {ts_col} < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount if hasattr(cursor, "rowcount") else 0
            if deleted > 0:
                results[table] = deleted
        except Exception as e:
            log.warning("Cleanup failed for {t}: {err}", t=table, err=str(e))

    total = sum(results.values())
    db_size = 0.0
    try:
        if os.path.exists(db.db_path):
            db_size = os.path.getsize(db.db_path) / (1024 * 1024)
    except Exception:
        pass

    log.info(
        "Cleanup: deleted {n} rows across {t} tables | DB size: {sz:.1f} MB",
        n=total, t=len(results), sz=db_size,
    )
    return results
