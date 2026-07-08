#!/usr/bin/env python3
"""P8 of P1-P10 — backfill trade_log.exchange_mode for mistagged demo rows.

Background:
    Audit AUDIT_BYBIT_DEMO_WIRING_GAPS_FINDINGS.md (L9-G5) found that
    data_lake.write_trade did not accept an exchange_mode kwarg. The
    column defaults to 'shadow', so every bybit_demo trade since the
    operator enabled bybit_demo on 2026-05-08 11:27:17 has been tagged
    'shadow' — 116 rows as of 2026-05-09 (audit said 73; it grew).

What this script does:
    1. Capture current state to a *.bak.sql for rollback.
    2. UPDATE trade_log SET exchange_mode='bybit_demo' WHERE
       exchange_mode='shadow' AND closed_at >= '2026-05-08 11:27:00'.
    3. Print the count of rows updated.

Idempotent: running twice is a no-op (the second run finds zero rows
matching the WHERE clause because the first run already retagged them).

Safe to run while trading-workers is active: SQLite WAL mode + the
UPDATE is small + atomic. No table lock beyond the duration of the
UPDATE itself.

Usage:
    python3 scripts/backfill_p8_trade_log_exchange_mode.py [--dry-run]

The --dry-run flag prints what would be updated without writing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db")
BACKUP_PATH = Path(
    "/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/p1_p10_fixes/"
    "p8_trade_log_pre_backfill.sql"
)
# Cut-over: any trade_log row with closed_at >= this timestamp that is
# still tagged exchange_mode='shadow' is genuinely a bybit_demo trade
# (the operator enabled bybit_demo at exactly this moment per
# transformer_state.last_switched_at).
CUT_OVER_TS = "2026-05-08 11:27:00"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print only; no writes")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        # Pre-flight: count rows matching the criteria.
        cur.execute(
            "SELECT COUNT(*) FROM trade_log "
            "WHERE exchange_mode='shadow' AND closed_at >= ?",
            (CUT_OVER_TS,),
        )
        match_count = cur.fetchone()[0]
        print(f"Found {match_count} mistagged 'shadow' rows since {CUT_OVER_TS}.")

        if match_count == 0:
            print("Nothing to backfill. Already in correct state.")
            return 0

        if args.dry_run:
            print("--dry-run: not writing. Re-run without --dry-run to apply.")
            return 0

        # Save the pre-backfill (trade_id, exchange_mode, closed_at) so
        # rollback is trivially possible if needed.
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with BACKUP_PATH.open("w") as fh:
            fh.write(
                f"-- P8 backfill rollback record. Generated for {match_count} rows.\n"
                f"-- Cut-over timestamp: {CUT_OVER_TS}\n"
                f"-- Run these UPDATEs to revert if needed:\n\n"
            )
            cur.execute(
                "SELECT trade_id FROM trade_log "
                "WHERE exchange_mode='shadow' AND closed_at >= ?",
                (CUT_OVER_TS,),
            )
            for (tid,) in cur.fetchall():
                fh.write(
                    f"UPDATE trade_log SET exchange_mode='shadow' "
                    f"WHERE trade_id={tid!r};\n"
                )
        print(f"Pre-backfill state saved to {BACKUP_PATH}")

        cur.execute(
            "UPDATE trade_log SET exchange_mode='bybit_demo' "
            "WHERE exchange_mode='shadow' AND closed_at >= ?",
            (CUT_OVER_TS,),
        )
        conn.commit()
        print(f"Backfilled {cur.rowcount} rows: 'shadow' → 'bybit_demo'.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
