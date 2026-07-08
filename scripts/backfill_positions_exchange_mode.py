"""I4 of cascade-fix series — backfill ``positions.exchange_mode`` for
existing rows.

Phase 0 baseline confirmed ``SELECT COUNT(*) FROM positions`` was 0
when this fix series began, so this backfill is largely vacuous in the
production database. It is provided for symmetry with the other
``CRITICAL/HIGH`` series backfills (orders, trade_history,
account_snapshots, trade_intelligence) and for any deployment whose
``positions`` table accumulated rows between Phase 0 and the schema
migration.

Logic:
  - All rows with ``exchange_mode='shadow'`` (the column DEFAULT) and
    ``updated_at >= '2026-05-08T11:19:26'`` are flipped to
    ``'bybit_demo'``. The cut-over timestamp matches the
    ``transformer_state.last_switched_at`` value used by the v30
    backfills for orders / account_snapshots / trade_history.
  - Rows older than the cut-over are unchanged (they really were
    shadow). Rows already tagged ``'bybit_demo'`` are unchanged.

Idempotent: re-running is a no-op once the cut-over backfill has been
applied (the WHERE filter excludes rows already tagged).

Usage::

    python -m scripts.backfill_positions_exchange_mode

Or with a non-default DB path::

    python -m scripts.backfill_positions_exchange_mode \\
        --db data/trading.db
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make ``src.*`` importable when run as a script.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.database.connection import DatabaseManager  # noqa: E402

# Mirrors the constant used by the v30 backfills in migrations.py for
# orders / account_snapshots / trade_history. Sourced from
# transformer_state.last_switched_at — the cut-over timestamp at which
# bybit_demo became the active mode.
_CUTOVER_ISO = "2026-05-08T11:19:26"

_BACKFILL_SQL = (
    "UPDATE positions SET exchange_mode = 'bybit_demo' "
    "WHERE exchange_mode = 'shadow' AND updated_at >= ?"
)


async def _run(db_path: str) -> int:
    mgr = DatabaseManager(db_path)
    await mgr.connect()
    try:
        # Pre-count for observability.
        before = await mgr.fetch_one(
            "SELECT COUNT(*) AS n FROM positions"
        )
        print(f"positions row count BEFORE: {before['n'] if before else 0}")
        # Distribution.
        dist = await mgr.fetch_all(
            "SELECT exchange_mode, COUNT(*) AS n FROM positions "
            "GROUP BY exchange_mode"
        )
        print("BEFORE distribution:")
        for row in dist:
            print(f"  exchange_mode={row['exchange_mode']!r}: {row['n']}")

        # Run the backfill.
        cur = await mgr.execute(_BACKFILL_SQL, (_CUTOVER_ISO,))
        # SQLite reports rows affected via cursor.rowcount.
        affected = cur.rowcount if hasattr(cur, "rowcount") else 0
        print(f"backfill affected rows: {affected}")

        # Post-distribution.
        dist = await mgr.fetch_all(
            "SELECT exchange_mode, COUNT(*) AS n FROM positions "
            "GROUP BY exchange_mode"
        )
        print("AFTER distribution:")
        for row in dist:
            print(f"  exchange_mode={row['exchange_mode']!r}: {row['n']}")
        return int(affected)
    finally:
        await mgr.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="data/trading.db",
        help="Path to the trading database (default: data/trading.db)",
    )
    args = parser.parse_args()
    affected = asyncio.run(_run(args.db))
    print(f"DONE — {affected} row(s) backfilled to exchange_mode='bybit_demo'")


if __name__ == "__main__":
    main()
