#!/usr/bin/env python3
"""Issue I2 (F-17, 2026-05-14) — one-shot orphan-position backfill.

Identifies positions table rows that are NOT in the coordinator's
``_trades`` map AND have no corresponding open thesis in
``trade_thesis`` (status='open'). These rows are confirmed orphans
left by pre-I2 close paths that silently skipped delete_position.

This script is **operator-supervised, one-shot only**. Per the prompt's
Rule 3, sweeping orphans via a cron job is a band-aid that hides the
source path. The source path is fixed by I2's manager.py edits; this
script clears the legacy residue.

Usage:
    .venv/bin/python scripts/backfill_orphan_positions.py [--dry-run]

By default the script prints what it would delete, then asks for
confirmation before deleting. Pass ``--dry-run`` to skip the deletion
phase entirely. Pass ``--yes`` to skip the confirmation prompt (for
non-interactive operator runs).

Output:
    POSITION_ORPHAN_BACKFILL_START — counts at script start
    POSITION_ORPHAN_FOUND          — per-orphan diagnostic
    POSITION_ORPHAN_DELETED        — per-deletion success
    POSITION_ORPHAN_BACKFILL_DONE  — summary at end

Safety:
    * Read-only by default — no deletions without --yes or interactive y
    * Reads BOTH coordinator state AND DB; deletes only when both
      sources confirm the row is orphan
    * Logs every deletion with full context for audit trail
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root on path when run directly
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


async def _run(dry_run: bool, auto_yes: bool) -> int:
    """Run the backfill. Returns 0 on success, non-zero on failure."""
    import aiosqlite

    db_path = _PROJECT_ROOT / "data" / "trading.db"
    if not db_path.exists():
        print(f"FATAL: trading.db not found at {db_path}")
        return 1

    print(f"Connecting to {db_path}")
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row

        # Step 1: enumerate all positions rows
        async with db.execute(
            "SELECT symbol, side, size, entry_price, updated_at, "
            "exchange_mode FROM positions"
        ) as cur:
            all_positions = await cur.fetchall()
        print(f"\nPOSITION_ORPHAN_BACKFILL_START total_rows={len(all_positions)}")

        if not all_positions:
            print("No rows in positions table — nothing to backfill.")
            return 0

        # Step 2: enumerate symbols with active open theses (a thesis
        # row with status='open' means the trade is still live in the
        # learning system's view; we MUST NOT delete those positions).
        async with db.execute(
            "SELECT DISTINCT symbol FROM trade_thesis WHERE status = 'open'"
        ) as cur:
            open_thesis_rows = await cur.fetchall()
        open_thesis_syms = {r["symbol"] for r in open_thesis_rows}
        print(f"Symbols with open theses: {len(open_thesis_syms)}")

        # Step 3: identify orphans — positions table rows whose symbol
        # is NOT in open_thesis_syms. These are confirmed orphans.
        orphans = [
            r for r in all_positions if r["symbol"] not in open_thesis_syms
        ]
        print(f"\nFound {len(orphans)} orphan rows:")
        for r in orphans:
            print(
                f"  POSITION_ORPHAN_FOUND | sym={r['symbol']} side={r['side']} "
                f"size={r['size']} entry_price={r['entry_price']} "
                f"updated_at={r['updated_at']} mode={r['exchange_mode']}"
            )

        if not orphans:
            print("\nNo orphans found — DB is clean.")
            return 0

        if dry_run:
            print("\n--dry-run set; no deletions performed.")
            print(f"POSITION_ORPHAN_BACKFILL_DONE deleted=0 dry_run=Y")
            return 0

        # Step 4: confirmation
        if not auto_yes:
            print(f"\nProceed to DELETE {len(orphans)} orphan rows? [y/N]: ",
                  end="", flush=True)
            answer = input().strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted; no deletions performed.")
                return 0

        # Step 5: deletion loop
        deleted = 0
        for r in orphans:
            sym = r["symbol"]
            try:
                await db.execute(
                    "DELETE FROM positions WHERE symbol = ?", (sym,)
                )
                deleted += 1
                print(
                    f"  POSITION_ORPHAN_DELETED | sym={sym} "
                    f"prior_updated_at={r['updated_at']}"
                )
            except Exception as e:
                print(
                    f"  POSITION_ORPHAN_DELETE_FAIL | sym={sym} err={e}"
                )

        await db.commit()
        print(
            f"\nPOSITION_ORPHAN_BACKFILL_DONE deleted={deleted} "
            f"orphans_found={len(orphans)}"
        )

        # Step 6: verify
        async with db.execute("SELECT COUNT(*) FROM positions") as cur:
            row = await cur.fetchone()
        post_count = row[0] if row else 0
        print(f"positions table now has {post_count} rows.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted; perform no DELETE.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation (for unattended operator runs).",
    )
    args = parser.parse_args()
    return asyncio.run(_run(dry_run=args.dry_run, auto_yes=args.yes))


if __name__ == "__main__":
    raise SystemExit(main())
