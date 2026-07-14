#!/usr/bin/env python3
"""Daily trade-log exporter for the Trading Intelligence MCP bot.

Reads closed trades from data/trading.db and writes a per-day,
human-readable Markdown log plus a CSV. Read-only and safe to run anytime.

Also writes a full-table dated archive of trade_log + trade_intelligence
to <out-dir>/archive/ on every run (see IMPLEMENT_ENTRY_VOLUME_GATE.md
Phase 2 item 2). The prior entries-quality diagnosis was built from a
captured worker.log bundle that later rotated off the VM, permanently
blocking any re-validation across windows — trade_intelligence carries
the entry-time features (volume_ratio, X-RAY confidence, regime, etc.)
that log bundle held, so a DB-driven dated snapshot is a durable
substitute that survives log rotation.

Usage:
  python scripts/daily_trade_export.py                  (today)
  python scripts/daily_trade_export.py --date 2026-07-08
  python scripts/daily_trade_export.py --since 2026-07-01
"""
import argparse
import csv
import glob
import os
import re
import sqlite3
import sys
from datetime import date, timedelta


def resolve_db(arg):
    if arg:
        return arg
    for cand in ("data/trading.db", "trading.db"):
        if os.path.exists(cand):
            return cand
    return "data/trading.db"


def fetch_day(conn, day):
    sql = """
        SELECT symbol, direction, strategy, pnl_pct, pnl_usd,
               leverage, size_usd, hold_minutes, close_reason, closed_at
        FROM trade_log
        WHERE DATE(closed_at) = ?
        ORDER BY closed_at ASC
    """
    try:
        return conn.execute(sql, (day,)).fetchall()
    except sqlite3.Error as e:
        print(f"  [warn] trade_log query failed: {e}", file=sys.stderr)
        return []


def fetch_range(conn, since):
    sql = """
        SELECT symbol, direction, strategy, pnl_pct, pnl_usd,
               leverage, size_usd, hold_minutes, close_reason, closed_at
        FROM trade_log
        WHERE DATE(closed_at) >= ?
        ORDER BY closed_at ASC
    """
    try:
        return conn.execute(sql, (since,)).fetchall()
    except sqlite3.Error as e:
        print(f"  [warn] trade_log range query failed: {e}", file=sys.stderr)
        return []


def fetch_daily_pnl(conn, day):
    try:
        return conn.execute(
            "SELECT starting_equity, ending_equity, realized_pnl, total_trades, "
            "wins, losses, max_drawdown_pct, target_hit, halted "
            "FROM daily_pnl WHERE date = ?",
            (day,),
        ).fetchone()
    except sqlite3.Error:
        return None


_ARCHIVE_TABLES = ("trade_log", "trade_intelligence")
_ARCHIVE_FNAME_RE = re.compile(r"^(?P<table>[a-z_]+)_(?P<day>\d{4}-\d{2}-\d{2})\.csv$")


def archive_full_table(conn, table, archive_dir, day_label):
    """Dump every column/row of ``table`` to a dated, immutable CSV snapshot.

    Unlike the rolling since-window export above, this is a full-table
    point-in-time capture — the goal is a durable substitute for the
    captured log bundles that previously backed entry-quality analyses
    and later rotated off the VM. Returns (path, row_count) or None if
    the table doesn't exist / query fails (best-effort, never raises).
    """
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
    except sqlite3.Error as e:
        print(f"  [warn] archive query failed for {table}: {e}", file=sys.stderr)
        return None
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    os.makedirs(archive_dir, exist_ok=True)
    path = os.path.join(archive_dir, f"{table}_{day_label}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return path, len(rows)


def prune_old_archives(archive_dir, retention_days):
    """Delete dated archive files older than ``retention_days``.

    Filename-date driven (not mtime) so re-running the export for a past
    day never prunes based on today's clock. Best-effort — a stray file
    that doesn't match the naming pattern is left alone, not deleted.
    """
    if retention_days <= 0 or not os.path.isdir(archive_dir):
        return
    cutoff = date.today() - timedelta(days=retention_days)
    for path in glob.glob(os.path.join(archive_dir, "*.csv")):
        m = _ARCHIVE_FNAME_RE.match(os.path.basename(path))
        if not m:
            continue
        try:
            file_day = date.fromisoformat(m.group("day"))
        except ValueError:
            continue
        if file_day < cutoff:
            try:
                os.remove(path)
            except OSError as e:
                print(f"  [warn] could not prune {path}: {e}", file=sys.stderr)


def render(trades, label, pnl):
    lines = [f"# TRADE LOG — {label}", ""]
    if pnl:
        se, ee, rp, tt, w, l, mdd, th, ha = pnl
        lines.append(
            f"Day PnL: {rp:+.2f} USD | trades={tt} wins={w} losses={l} "
            f"| max_dd={mdd:.2f}% | target_hit={th} halted={ha}"
        )
        lines.append("")
    if not trades:
        lines.append("(no closed trades for this period)")
        return "\n".join(lines)
    tot_usd = 0.0
    wins = 0
    for t in trades:
        sym, d, strat, pct, usd, lev, sz, hold, reason, closed = t
        tot_usd += (usd or 0.0)
        if (usd or 0.0) >= 0:
            wins += 1
        lines.append(
            f"- {sym} {d} | strat={strat} | pnl={pct:+.2f}% (${usd:+.2f}) "
            f"| {lev}x ${sz:.0f} | hold={hold:.0f}m | reason={reason} | {closed}"
        )
    lines.append("")
    lines.append(f"TOTAL: {len(trades)} trades, {wins} wins, net ${tot_usd:+.2f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Export a daily trade log from trading.db")
    ap.add_argument("--db", default=None, help="path to trading.db")
    ap.add_argument("--date", default=None, help="single day YYYY-MM-DD (default today)")
    ap.add_argument("--since", default=None, help="inclusive start day YYYY-MM-DD")
    ap.add_argument("--out-dir", default="data/trade_logs", help="output directory")
    ap.add_argument(
        "--archive-retention-days", type=int, default=90,
        help="prune dated trade_log/trade_intelligence archives older than "
             "this many days (0 = keep forever)",
    )
    ap.add_argument(
        "--skip-archive", action="store_true",
        help="skip the full-table dated archive step (rolling export only)",
    )
    args = ap.parse_args()

    db = resolve_db(args.db)
    if not os.path.exists(db):
        print(f"ERROR: database not found at {db}. Run the bot first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    if args.date:
        day = args.date
        trades = fetch_day(conn, day)
        pnl = fetch_daily_pnl(conn, day)
        label = day
    else:
        since = args.since or (date.today() - timedelta(days=29)).isoformat()
        trades = fetch_range(conn, since)
        pnl = None
        label = f"since {since}"

    if not args.skip_archive:
        archive_dir = os.path.join(args.out_dir, "archive")
        today_label = date.today().isoformat()
        for table in _ARCHIVE_TABLES:
            result = archive_full_table(conn, table, archive_dir, today_label)
            if result:
                path, n = result
                print(f"Archived {table}: {n} rows -> {path}")
        prune_old_archives(archive_dir, args.archive_retention_days)

    conn.close()

    text = render(trades, label, pnl)
    print(text)

    os.makedirs(args.out_dir, exist_ok=True)
    safe = label.replace(" ", "_").replace(":", "")
    md_path = os.path.join(args.out_dir, f"TRADELOG_{safe}.md")
    with open(md_path, "w") as f:
        f.write(text + "\n")
    csv_path = os.path.join(args.out_dir, f"TRADELOG_{safe}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "direction", "strategy", "pnl_pct", "pnl_usd",
                    "leverage", "size_usd", "hold_minutes", "close_reason", "closed_at"])
        for t in trades:
            w.writerow(t)
    print(f"\nWrote: {md_path}\nWrote: {csv_path}")


if __name__ == "__main__":
    main()
