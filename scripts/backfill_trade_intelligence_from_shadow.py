#!/usr/bin/env python3
"""Phase 5 of the price-source-divergence fix — historical backfill.

Restores historical accuracy in ``data/trading.db.trade_intelligence`` by
overwriting locally-computed ``pnl_usd`` / ``pnl_pct`` with Shadow's
authoritative ``virtual_positions.net_pnl_usd`` / ``net_pnl_pct`` for
trades that were closed via the self-initiated close paths
(``time_decay_p_win_low``, ``mode4_p9``, etc.) before Phase 1 of this
fix shipped.

Forensic context (T1 in ``dev_notes/price_source_divergence/FULL_BUNDLE.md``):
self-initiated closes recorded ``pnl_usd`` from the Transformer-overwritten
``pos.unrealized_pnl`` value, which was derived from main project's stale
``ticker_cache`` (Bug 1 silently broke WS persistence). Result: ``pnl_usd``
biased low (in absolute terms) by $0.16-$0.24 per trade vs Shadow's true
post-fee post-slippage ``net_pnl_usd``.

Behaviour:

  * Dry-run by default. ``--apply`` is required to write changes.
  * Idempotent. Repeated runs converge on zero diffs.
  * Backup taken before apply (file copy of ``data/trading.db`` to
    ``data/trading.db.pre-phase5.bak`` with timestamp suffix).
  * Adds ``pnl_source TEXT DEFAULT 'main_local'`` column to
    ``trade_intelligence`` if absent. After apply, updated rows have
    ``pnl_source = 'shadow_authoritative_backfill_2026-05-03'``.
  * Join key: ``(symbol, trade_closed_at within ±90s of Shadow.closed_at)``.
    NOT ``entry_price`` — the universal ±0.03% slippage gap (T1 Pattern A)
    means the entry-price join always misses.
  * Excluded rows: where ``|main.pnl_usd - shadow.net_pnl_usd| < $0.05``.
    These already match (closed via ``strategic_review`` or
    ``shadow_sl_tp`` paths that already used Shadow's value).
  * Unmatched rows: logged in the report as ``unmatched``; not modified.
  * Report written to
    ``dev_notes/price_source_divergence/backfill_report.md``.

Usage:

    # Dry-run (default) — produce report, no DB writes.
    python scripts/backfill_trade_intelligence_from_shadow.py

    # Apply — take backup, run UPDATEs in a transaction, write report.
    python scripts/backfill_trade_intelligence_from_shadow.py --apply

    # Limit the scope (e.g. only backfill trades from a single day).
    python scripts/backfill_trade_intelligence_from_shadow.py --since 2026-05-01

    # Tighten / loosen the close-enough threshold (default $0.05).
    python scripts/backfill_trade_intelligence_from_shadow.py --threshold 0.10

The ``--apply`` path is irreversible without restoring the backup file.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADING_DB_PATH = PROJECT_ROOT / "data" / "trading.db"
SHADOW_DB_PATH = Path("/home/inshadaliqbal786/shadow/data/shadow.db")
REPORT_PATH = (
    PROJECT_ROOT / "dev_notes" / "price_source_divergence" / "backfill_report.md"
)
BACKUP_DIR = PROJECT_ROOT / "data"
PNL_SOURCE_BACKFILL_TAG = "shadow_authoritative_backfill_2026-05-03"


@dataclass
class TradePair:
    """One main-side row joined to its Shadow counterpart."""

    symbol: str
    main_id: int
    main_closed_at: str
    main_pnl_usd: float
    main_pnl_pct: float
    main_exit_price: float
    main_position_size_usd: float
    main_closed_by: str
    shadow_position_id: str | None
    shadow_closed_at: str | None
    shadow_quantity: float | None
    shadow_net_pnl_usd: float | None
    shadow_net_pnl_pct: float | None
    shadow_exit_price: float | None
    shadow_close_trigger: str | None
    delta_pnl_usd: float


def _open_main_db(read_only: bool = True) -> sqlite3.Connection:
    """Open the trading.db with row dicts."""
    uri = f"file:{TRADING_DB_PATH}?mode={'ro' if read_only else 'rw'}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _open_shadow_db() -> sqlite3.Connection:
    """Open shadow.db read-only with row dicts."""
    if not SHADOW_DB_PATH.exists():
        sys.exit(
            f"ERROR: Shadow database not found at {SHADOW_DB_PATH}. "
            "Backfill aborted — Shadow's authoritative records are required."
        )
    uri = f"file:{SHADOW_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_pnl_source_column(conn: sqlite3.Connection) -> bool:
    """Add the ``pnl_source`` column to ``trade_intelligence`` if absent.

    Returns True iff the column was added (i.e. the schema was migrated
    by this call). Used in the report so operators can see whether the
    column was pre-existing.
    """
    cur = conn.execute("PRAGMA table_info(trade_intelligence)")
    cols = [row["name"] for row in cur.fetchall()]
    if "pnl_source" in cols:
        return False
    conn.execute(
        "ALTER TABLE trade_intelligence "
        "ADD COLUMN pnl_source TEXT DEFAULT 'main_local'"
    )
    conn.commit()
    return True


def _load_main_rows(
    conn: sqlite3.Connection, since: str | None
) -> list[sqlite3.Row]:
    """Return every closed-trade row in trade_intelligence ordered by
    closed_at. ``since`` (inclusive ISO date) bounds the scan."""
    sql = (
        "SELECT id, symbol, trade_closed_at, pnl_usd, pnl_pct, "
        "       entry_price, exit_price, position_size_usd, closed_by "
        "FROM trade_intelligence "
        "WHERE trade_closed_at IS NOT NULL"
    )
    params: tuple = ()
    if since:
        sql += " AND trade_closed_at >= ?"
        params = (since,)
    sql += " ORDER BY trade_closed_at"
    return list(conn.execute(sql, params).fetchall())


def _load_shadow_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every closed virtual_positions row."""
    return list(
        conn.execute(
            "SELECT position_id, symbol, quantity, entry_price, exit_price, "
            "       net_pnl_usd, net_pnl_pct, close_trigger, closed_at "
            "FROM virtual_positions "
            "WHERE status = 'closed' AND closed_at IS NOT NULL "
            "ORDER BY closed_at"
        ).fetchall()
    )


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware datetime.

    Tolerates the trailing-Z form some legacy rows use.
    """
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_shadow_index(rows: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    """Group Shadow rows by symbol so per-row lookup is O(N) per symbol
    rather than O(M) per main row × N Shadow rows."""
    index: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        index.setdefault(row["symbol"], []).append(row)
    return index


def _find_shadow_match(
    main_row: sqlite3.Row,
    shadow_index: dict[str, list[sqlite3.Row]],
    *,
    window_seconds: float = 90.0,
) -> sqlite3.Row | None:
    """Find the Shadow row that closed within ±window_seconds of main's
    ``trade_closed_at`` for the same symbol.

    If multiple Shadow rows match, picks the one closest in time.
    Returns None when no Shadow row falls inside the window.
    """
    candidates = shadow_index.get(main_row["symbol"], [])
    if not candidates:
        return None
    main_dt = _parse_iso(main_row["trade_closed_at"])
    best: sqlite3.Row | None = None
    best_delta = window_seconds + 1
    for cand in candidates:
        try:
            cand_dt = _parse_iso(cand["closed_at"])
        except ValueError:
            continue
        delta = abs((main_dt - cand_dt).total_seconds())
        if delta <= window_seconds and delta < best_delta:
            best = cand
            best_delta = delta
    return best


def _match_pairs(
    main_rows: list[sqlite3.Row],
    shadow_rows: list[sqlite3.Row],
) -> tuple[list[TradePair], list[sqlite3.Row]]:
    """Return (matched_pairs, unmatched_main_rows)."""
    shadow_index = _build_shadow_index(shadow_rows)
    # Shadow's position_id is a UUID string (e.g.
    # 'de6610d2-7655-4fba-85df-552207212f8f'), not an int — track used
    # ids as strings to keep matching exclusive across main rows.
    used_shadow_ids: set[str] = set()
    pairs: list[TradePair] = []
    unmatched: list[sqlite3.Row] = []
    for m in main_rows:
        s = _find_shadow_match(m, shadow_index)
        # Re-scan to skip already-used Shadow rows (one Shadow row maps to
        # at most one main row); keep the closest unused one.
        if s is not None and str(s["position_id"]) in used_shadow_ids:
            shadow_index_copy = {
                sym: [
                    r for r in rows
                    if str(r["position_id"]) not in used_shadow_ids
                ]
                for sym, rows in shadow_index.items()
            }
            s = _find_shadow_match(m, shadow_index_copy)
        if s is None:
            unmatched.append(m)
            continue
        used_shadow_ids.add(str(s["position_id"]))
        pairs.append(
            TradePair(
                symbol=m["symbol"],
                main_id=m["id"],
                main_closed_at=m["trade_closed_at"],
                main_pnl_usd=float(m["pnl_usd"] or 0.0),
                main_pnl_pct=float(m["pnl_pct"] or 0.0),
                main_exit_price=float(m["exit_price"] or 0.0),
                main_position_size_usd=float(m["position_size_usd"] or 0.0),
                main_closed_by=str(m["closed_by"] or ""),
                shadow_position_id=str(s["position_id"]),
                shadow_closed_at=str(s["closed_at"]),
                shadow_quantity=float(s["quantity"] or 0.0),
                shadow_net_pnl_usd=float(s["net_pnl_usd"] or 0.0),
                shadow_net_pnl_pct=float(s["net_pnl_pct"] or 0.0),
                shadow_exit_price=float(s["exit_price"] or 0.0),
                shadow_close_trigger=str(s["close_trigger"] or ""),
                delta_pnl_usd=(
                    float(m["pnl_usd"] or 0.0) - float(s["net_pnl_usd"] or 0.0)
                ),
            )
        )
    return pairs, unmatched


def _take_backup() -> Path:
    """Copy trading.db to a timestamped backup file. Returns the backup
    path. SQLite WAL/SHM are not backed up because the backup is only
    valid in the apply path which holds the writer lock."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"trading.db.pre-phase5.{ts}.bak"
    shutil.copy2(TRADING_DB_PATH, backup)
    return backup


def _apply_updates(
    conn: sqlite3.Connection, pairs_to_update: list[TradePair]
) -> int:
    """Update trade_intelligence rows in a single transaction. Returns
    the count of rows successfully updated."""
    if not pairs_to_update:
        return 0
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        for p in pairs_to_update:
            cur.execute(
                """
                UPDATE trade_intelligence
                SET pnl_usd = ?,
                    pnl_pct = ?,
                    exit_price = ?,
                    pnl_source = ?
                WHERE id = ?
                """,
                (
                    p.shadow_net_pnl_usd,
                    p.shadow_net_pnl_pct,
                    p.shadow_exit_price if p.shadow_exit_price else p.main_exit_price,
                    PNL_SOURCE_BACKFILL_TAG,
                    p.main_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(pairs_to_update)


def _format_report(
    *,
    pairs: list[TradePair],
    unmatched: list[sqlite3.Row],
    pairs_to_update: list[TradePair],
    pairs_already_match: list[TradePair],
    threshold: float,
    applied: bool,
    backup_path: Path | None,
    schema_migrated: bool,
    since: str | None,
) -> str:
    """Build the markdown report body."""
    lines: list[str] = []
    lines.append("# Phase 5 — trade_intelligence Backfill Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"**Mode:** {'APPLY' if applied else 'DRY-RUN'}")
    lines.append(f"**Threshold (skip-if-below):** ${threshold:.2f}")
    if since:
        lines.append(f"**Scope:** trade_closed_at >= {since}")
    if schema_migrated:
        lines.append("**Schema:** Added pnl_source TEXT DEFAULT 'main_local' column.")
    if backup_path is not None:
        lines.append(f"**Backup:** {backup_path}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Main rows scanned: {len(pairs) + len(unmatched)}")
    lines.append(f"- Matched to Shadow row: {len(pairs)}")
    lines.append(f"- Unmatched (skipped): {len(unmatched)}")
    lines.append(
        f"- Already match (|Δ| < ${threshold:.2f}): {len(pairs_already_match)}"
    )
    lines.append(f"- Would update / updated: {len(pairs_to_update)}")
    total_correction = sum(p.delta_pnl_usd for p in pairs_to_update)
    lines.append(f"- Total dollar correction: ${total_correction:+.4f}")
    lines.append("")

    lines.append("## Updated rows (top 50 by |Δ|)")
    lines.append("")
    lines.append(
        "| symbol | closed_at | closed_by | main_pnl_usd | shadow_net_pnl_usd | Δ |"
    )
    lines.append(
        "|---|---|---|---|---|---|"
    )
    for p in sorted(pairs_to_update, key=lambda x: -abs(x.delta_pnl_usd))[:50]:
        lines.append(
            f"| {p.symbol} | {p.main_closed_at} | {p.main_closed_by[:40]} | "
            f"{p.main_pnl_usd:+.4f} | {p.shadow_net_pnl_usd:+.4f} | "
            f"{p.delta_pnl_usd:+.4f} |"
        )
    if len(pairs_to_update) > 50:
        lines.append("")
        lines.append(f"_(table truncated; full update set: {len(pairs_to_update)} rows)_")
    lines.append("")

    if unmatched:
        lines.append("## Unmatched main rows (sample, first 30)")
        lines.append("")
        lines.append(
            "These rows in trade_intelligence have no Shadow virtual_positions "
            "counterpart within ±90s of trade_closed_at for the same symbol. "
            "Possible reasons: pre-Shadow rows, manual closes that bypassed "
            "Shadow, or imported test data."
        )
        lines.append("")
        lines.append(
            "| symbol | trade_closed_at | pnl_usd | closed_by |"
        )
        lines.append("|---|---|---|---|")
        for r in unmatched[:30]:
            lines.append(
                f"| {r['symbol']} | {r['trade_closed_at']} | "
                f"{r['pnl_usd']:+.4f} | {(r['closed_by'] or '')[:40]} |"
            )
        if len(unmatched) > 30:
            lines.append("")
            lines.append(f"_(table truncated; full unmatched set: {len(unmatched)} rows)_")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Shadow's ``virtual_positions.net_pnl_usd`` is post-fee post-slippage; "
        "main's pre-fix ``pnl_usd`` was pre-slippage and missing exit fee for "
        "self-initiated closes."
    )
    lines.append(
        "- The ±0.03% entry-price slippage gap is by design "
        "(``shadow/config.toml [exchange] slippage_pct = 0.03``); the join key "
        "is ``(symbol, trade_closed_at within ±90s)``, not ``entry_price``."
    )
    lines.append(
        "- After APPLY, updated rows carry "
        f"``pnl_source = '{PNL_SOURCE_BACKFILL_TAG}'``. Phase 1's helper at "
        "``trade_coordinator.py:resolve_authoritative_pnl`` ensures new closes "
        "going forward will record Shadow's value directly (the row's "
        "pnl_source remains the default ``'main_local'`` because the writer "
        "isn't aware of the column; the bypass is via the helper's own "
        "WD_LAST_CLOSE_AUTH log line)."
    )
    if not applied:
        lines.append(
            "- This was a DRY-RUN. Re-run with ``--apply`` to write changes; "
            "a backup will be taken first."
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill trade_intelligence pnl_usd / pnl_pct from "
        "Shadow virtual_positions (Phase 5 of price-source-divergence fix)."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply updates (default is dry-run with no DB writes).",
    )
    parser.add_argument(
        "--since", default=None,
        help="ISO date (YYYY-MM-DD) — only consider rows with "
        "trade_closed_at >= this date. Default: scan all rows.",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.05,
        help="Skip rows where |main.pnl_usd - shadow.net_pnl_usd| < threshold "
        "(default $0.05). These already match within rounding.",
    )
    args = parser.parse_args()

    if not TRADING_DB_PATH.exists():
        print(f"ERROR: trading.db not found at {TRADING_DB_PATH}", file=sys.stderr)
        return 2
    if not SHADOW_DB_PATH.exists():
        print(f"ERROR: shadow.db not found at {SHADOW_DB_PATH}", file=sys.stderr)
        return 2

    # Open Shadow first (always read-only) to fail fast if it's unavailable.
    shadow_conn = _open_shadow_db()
    shadow_rows = _load_shadow_rows(shadow_conn)
    shadow_conn.close()
    print(f"Loaded {len(shadow_rows)} closed Shadow virtual_positions rows.")

    # Open main DB read-only first to do the dry-run pairing without locking
    # the file. If --apply is set we'll re-open in read-write mode below.
    main_conn = _open_main_db(read_only=True)
    main_rows = _load_main_rows(main_conn, args.since)
    main_conn.close()
    print(f"Loaded {len(main_rows)} closed trade_intelligence rows.")

    pairs, unmatched = _match_pairs(main_rows, shadow_rows)
    print(f"Matched {len(pairs)} pairs, {len(unmatched)} unmatched.")

    pairs_to_update = [
        p for p in pairs if abs(p.delta_pnl_usd) >= args.threshold
    ]
    pairs_already_match = [
        p for p in pairs if abs(p.delta_pnl_usd) < args.threshold
    ]
    print(
        f"Would update {len(pairs_to_update)} rows; "
        f"{len(pairs_already_match)} already within ${args.threshold:.2f}."
    )

    backup_path: Path | None = None
    schema_migrated = False
    if args.apply:
        # Take backup BEFORE opening the writer connection so the snapshot
        # is consistent (writer connection would hold the lock).
        backup_path = _take_backup()
        print(f"Backup taken: {backup_path}")

        rw_conn = _open_main_db(read_only=False)
        try:
            schema_migrated = _ensure_pnl_source_column(rw_conn)
            if schema_migrated:
                print("Schema migration: added pnl_source column.")
            updated = _apply_updates(rw_conn, pairs_to_update)
            print(f"Applied {updated} updates.")
        finally:
            rw_conn.close()

    report = _format_report(
        pairs=pairs,
        unmatched=unmatched,
        pairs_to_update=pairs_to_update,
        pairs_already_match=pairs_already_match,
        threshold=args.threshold,
        applied=args.apply,
        backup_path=backup_path,
        schema_migrated=schema_migrated,
        since=args.since,
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(f"Report written: {REPORT_PATH}")

    if args.apply:
        # Idempotence verification — re-run the dry-run pairing on the
        # post-apply DB and report any remaining mismatches.
        verify_conn = _open_main_db(read_only=True)
        verify_rows = _load_main_rows(verify_conn, args.since)
        verify_conn.close()
        verify_pairs, _ = _match_pairs(verify_rows, shadow_rows)
        remaining = [
            p for p in verify_pairs
            if abs(p.delta_pnl_usd) >= args.threshold
        ]
        if remaining:
            print(
                f"WARNING: {len(remaining)} rows still have |Δ| >= "
                f"${args.threshold:.2f} after apply. Investigate the report."
            )
            return 1
        print("Idempotence check: zero remaining mismatches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
