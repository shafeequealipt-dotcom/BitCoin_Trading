#!/usr/bin/env python3
"""Daily trade-log exporter for the Trading Intelligence MCP bot.

Reads closed trades from data/trading.db and writes a per-day,
human-readable Markdown log plus a CSV. Read-only and safe to run anytime.

Usage:
  python scripts/daily_trade_export.py                  (today)
  python scripts/daily_trade_export.py --date 2026-07-08
  python scripts/daily_trade_export.py --since 2026-07-01
"""
import argparse
import csv
import os
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
