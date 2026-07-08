"""One-off backfill: re-tag the ``news_articles.symbols`` column using the
config-driven extraction map (``[universe.coin_aliases]`` + auto-derived
tickers).

Context
-------
``news_service.fetch_latest_news`` writes ``news_articles.symbols`` once on
INSERT and never updates the column afterwards. After expanding
``[universe.coin_aliases]`` in ``config.toml`` (or extending the watch_list),
articles already in the DB stay tagged with whatever map was active at
ingestion time — for the 2026-05-04 fix that meant only 4 coins were ever
tagged historically. This script re-applies ``extract_symbols`` to every
article in a configurable lookback window and UPDATEs the column with the
union of the old set + newly-discovered symbols.

The script is **non-destructive**: it never removes a symbol that was
already tagged; it only adds new matches. Use ``--dry-run`` to preview
the impact without writing.

Usage
-----
    python scripts/backfill_news_symbols.py --hours 24 --dry-run
    python scripts/backfill_news_symbols.py --hours 168 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from project root or scripts/ directory.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import Settings  # noqa: E402
from src.database.connection import DatabaseManager  # noqa: E402
from src.intelligence.news.news_service import extract_symbols  # noqa: E402


async def run(hours: int, apply: bool, db_path: str) -> int:
    Settings.reset()
    settings = Settings._load_fresh("config.toml", ".env")
    extraction_map = settings.universe.extraction_map
    print(f"Loaded extraction_map: {len(extraction_map)} entries "
          f"(watch_list={len(settings.universe.watch_list)} coins)")

    db = DatabaseManager(db_path, wal_mode=True)
    await db.connect()
    try:
        rows = await db.fetch_all(
            """
            SELECT id, headline, summary, symbols
            FROM news_articles
            WHERE published_at > datetime('now', ?)
            ORDER BY published_at DESC
            """,
            (f"-{hours} hours",),
        )
        print(f"Articles in last {hours}h: {len(rows)}")

        will_update = 0
        added_per_symbol: dict[str, int] = {}
        for row in rows:
            text = (row["headline"] or "") + " " + (row["summary"] or "")
            try:
                old_symbols = set(json.loads(row["symbols"] or "[]"))
            except json.JSONDecodeError:
                old_symbols = set()
            new_symbols = set(extract_symbols(text, extraction_map))
            added = new_symbols - old_symbols
            if not added:
                continue
            will_update += 1
            for s in added:
                added_per_symbol[s] = added_per_symbol.get(s, 0) + 1
            merged = sorted(old_symbols | new_symbols)
            if apply:
                await db.execute(
                    "UPDATE news_articles SET symbols = ? WHERE id = ?",
                    (json.dumps(merged), row["id"]),
                )

        verb = "Updated" if apply else "Would update"
        print(f"\n{verb} {will_update}/{len(rows)} articles")
        if added_per_symbol:
            print("New tags added by symbol (count):")
            for sym, n in sorted(added_per_symbol.items(), key=lambda x: -x[1]):
                print(f"  {sym:<12} +{n}")
        else:
            print("No new tags would be added (DB is already at parity with current map).")
        if not apply:
            print("\nDry-run only. Re-run with --apply to write changes.")
        return 0
    finally:
        await db.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Lookback window for articles to re-tag (default: 24)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changes to DB. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only — do not write (default behaviour). Mutually exclusive with --apply.",
    )
    parser.add_argument(
        "--db", default="data/trading.db",
        help="Path to the SQLite DB (default: data/trading.db)",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    return asyncio.run(run(args.hours, args.apply, args.db))


if __name__ == "__main__":
    sys.exit(main())
