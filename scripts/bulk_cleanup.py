#!/usr/bin/env python3
"""One-time bulk cleanup + VACUUM for trading.db and shadow.db.

Intended to be run ONCE by an operator after the retention-policy changes
land, to collapse the initial large-delete + VACUUM into a single controlled
window. The periodic `cleanup_worker` (trading) and `RetentionEngine` (shadow)
are idempotent, so subsequent ticks keep things clean without this script.

Usage
-----
    python scripts/bulk_cleanup.py --db both                  # default: clean both
    python scripts/bulk_cleanup.py --db trading               # trading only
    python scripts/bulk_cleanup.py --db shadow                # shadow only
    python scripts/bulk_cleanup.py --db both --dry-run        # count rows only
    python scripts/bulk_cleanup.py --db both --verbose        # detailed output

Exit codes
----------
    0 = success (or dry-run completed)
    1 = error during cleanup
    2 = configuration or environment failure

Safety
------
    * Does NOT delete data outside the RETENTION_POLICIES already vetted by
      the periodic workers. Re-uses their exact policies.
    * Respects --dry-run: only SELECTs, never DELETEs.
    * VACUUM is blocking; run during a quiet window. Script will print a
      "start VACUUM" line so operators know the DB is temporarily locked.
    * Never touches open positions (shadow): explicit `status = 'closed'`
      guard in RetentionEngine._delete_closed_positions().
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

# Resolve project roots
TRADING_ROOT = Path("/home/inshadaliqbal786/trading-intelligence-mcp")
SHADOW_ROOT = Path("/home/inshadaliqbal786/shadow")

# Ensure trading project's src is importable regardless of CWD
if str(TRADING_ROOT) not in sys.path:
    sys.path.insert(0, str(TRADING_ROOT))


def _mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


# ─── Trading cleanup ────────────────────────────────────────────────────────

async def _cleanup_trading(dry_run: bool, verbose: bool) -> int:
    """Run retention DELETEs + VACUUM on trading.db.

    Returns number of rows deleted (0 on dry-run).
    """
    # Re-use the SAME policies the periodic worker consumes, so this script
    # is strictly a "speed-up" over waiting for the next hourly tick.
    from src.config.settings import Settings
    from src.core.utils import now_utc
    from src.database.connection import DatabaseManager
    from src.workers.cleanup_worker import RETENTION_POLICIES

    settings = Settings.load()
    db_path = TRADING_ROOT / "data" / "trading.db"
    size_before = _mb(db_path)
    print(f"[trading] DB path  : {db_path}")
    print(f"[trading] size bfr : {size_before:.1f} MB")
    print(f"[trading] policies : {len(RETENTION_POLICIES)} tables")

    # Trading's DatabaseManager exposes connect() / disconnect().
    db = DatabaseManager(str(db_path), wal_mode=True)
    await db.connect()

    total_deleted = 0
    tables_cleaned = 0
    try:
        for table, max_days, ts_col in RETENTION_POLICIES:
            cutoff = (now_utc() - timedelta(days=max_days)).isoformat()

            count_row = await db.fetch_one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {ts_col} < ?",
                (cutoff,),
            )
            count = int(count_row["n"]) if count_row else 0

            if count == 0:
                if verbose:
                    print(f"  {table:30s} 0 rows > {max_days}d ({ts_col})  — skip")
                continue

            if dry_run:
                print(f"  {table:30s} {count:>8d} rows would delete (> {max_days}d, {ts_col})")
                continue

            try:
                cursor = await db.execute(
                    f"DELETE FROM {table} WHERE {ts_col} < ?",
                    (cutoff,),
                )
                deleted = cursor.rowcount if hasattr(cursor, "rowcount") else count
                total_deleted += deleted
                tables_cleaned += 1
                print(f"  {table:30s} {deleted:>8d} rows deleted   (> {max_days}d, {ts_col})")
            except Exception as e:
                print(f"  {table:30s} ERROR: {e}", file=sys.stderr)

        if dry_run:
            print(f"[trading] dry-run — no changes. size stays {size_before:.1f} MB")
            return 0

        if total_deleted > 0:
            print(f"[trading] start VACUUM (DB briefly locked) ...")
            t0 = time.time()
            await db.execute("VACUUM")
            size_after = _mb(db_path)
            dt = time.time() - t0
            reclaimed = size_before - size_after
            print(
                f"[trading] VACUUM  : {size_before:.1f} MB → {size_after:.1f} MB "
                f"(reclaimed {reclaimed:.1f} MB, took {dt:.1f}s)"
            )
        else:
            print(f"[trading] nothing deleted — skipping VACUUM")

        print(f"[trading] deleted : {total_deleted} rows across {tables_cleaned} tables")
        return total_deleted
    finally:
        await db.disconnect()


# ─── Shadow cleanup ─────────────────────────────────────────────────────────

async def _cleanup_shadow(dry_run: bool, verbose: bool) -> int:
    """Run shadow RetentionEngine (compress + delete + vacuum).

    Returns total rows touched (best-effort; compression isn't a pure DELETE).
    """
    if not SHADOW_ROOT.exists():
        print(f"[shadow] project path not found: {SHADOW_ROOT}", file=sys.stderr)
        return 0

    # Isolate sys.path additions so shadow imports don't collide with trading.
    if str(SHADOW_ROOT) not in sys.path:
        sys.path.insert(0, str(SHADOW_ROOT))

    from src.database.connection import DatabaseManager as ShadowDB  # noqa: E402
    from src.utils.config import load_config as load_shadow_config  # noqa: E402
    from src.utils.retention import RetentionEngine  # noqa: E402

    config = load_shadow_config()
    db_path = Path(config.database.path)
    size_before = _mb(db_path)
    print(f"[shadow]  DB path  : {db_path}")
    print(f"[shadow]  size bfr : {size_before:.1f} MB")
    print(f"[shadow]  closed_positions_retention_days = {config.database.closed_positions_retention_days}")

    # Shadow's DatabaseManager exposes connect() / close().
    db = ShadowDB(str(db_path), wal_mode=True)
    await db.connect()

    engine = RetentionEngine(db, config)

    if dry_run:
        # Count-only mode. We replicate RetentionEngine's cutoffs without
        # executing the DELETE/compression statements.
        from datetime import datetime, timezone
        cutoff_iso = (
            datetime.now(timezone.utc)
            - timedelta(days=engine._closed_positions_retention)
        ).isoformat()
        row = await db.fetch_one(
            "SELECT COUNT(*) AS n FROM virtual_positions "
            "WHERE status = 'closed' AND closed_at < ?",
            (cutoff_iso,),
        )
        vp_would = int(row["n"]) if row else 0
        print(f"[shadow]  virtual_positions closed > {engine._closed_positions_retention}d: {vp_would}")

        oi_cutoff_ms = int(time.time() * 1000) - (engine._oi_retention * 86_400_000)
        row = await db.fetch_one(
            "SELECT COUNT(*) AS n FROM open_interest_history WHERE timestamp < ?",
            (oi_cutoff_ms,),
        )
        oi_would = int(row["n"]) if row else 0
        print(f"[shadow]  open_interest_history      > {engine._oi_retention}d: {oi_would}")
        print(f"[shadow]  dry-run — no changes. size stays {size_before:.1f} MB")
        await db.close()
        return 0

    try:
        # Force-run VACUUM by clearing the last_vacuum_date marker so the
        # engine's weekly gate fires even if already run this week.
        try:
            await db.execute(
                "INSERT OR REPLACE INTO shadow_settings (key, value, updated_at) "
                "VALUES ('last_vacuum_date', '2000-01-01', datetime('now'))"
            )
        except Exception:
            pass  # If shadow_settings doesn't exist, VACUUM gate will still fire

        print(f"[shadow]  running RetentionEngine.run_cleanup() ...")
        results = await engine.run_cleanup()

        if verbose:
            for k, v in results.items():
                print(f"  {k:30s} {v}")

        size_after = _mb(db_path)
        reclaimed = size_before - size_after
        total = sum(
            v for k, v in results.items()
            if isinstance(v, int) and k != "vacuum_run"
        )
        print(
            f"[shadow]  {size_before:.1f} MB → {size_after:.1f} MB "
            f"(reclaimed {reclaimed:.1f} MB). rows touched: {total}"
        )
        return total
    finally:
        await db.close()


# ─── CLI ────────────────────────────────────────────────────────────────────

async def _main_async(args: argparse.Namespace) -> int:
    total = 0
    try:
        if args.db in ("trading", "both"):
            total += await _cleanup_trading(args.dry_run, args.verbose)
        if args.db in ("shadow", "both"):
            total += await _cleanup_shadow(args.dry_run, args.verbose)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    mode = "dry-run" if args.dry_run else "live"
    print(f"\n[done] mode={mode}  rows touched={total}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="bulk_cleanup",
        description="One-time bulk cleanup + VACUUM for trading.db and shadow.db",
    )
    p.add_argument(
        "--db", choices=("trading", "shadow", "both"), default="both",
        help="Which database(s) to clean (default: both).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Count rows that would be deleted; make no changes.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Print per-table/per-step detail.",
    )
    args = p.parse_args()

    # sanity check PYTHONPATH
    if not TRADING_ROOT.exists():
        print(f"ERROR: trading project not found: {TRADING_ROOT}", file=sys.stderr)
        return 2
    if args.db != "trading" and not SHADOW_ROOT.exists():
        print(f"ERROR: shadow project not found: {SHADOW_ROOT}", file=sys.stderr)
        return 2

    os.chdir(TRADING_ROOT)  # Settings.load() reads config.toml from cwd
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
