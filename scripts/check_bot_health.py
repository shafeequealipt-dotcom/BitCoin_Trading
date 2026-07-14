#!/usr/bin/env python3
"""Stall / health watchdog for the Trading Intelligence MCP bot.

Reads data/trading.db and flags the danger signs of a SILENT halt during an
unattended run: no new trade in N hours, no PNL_DAILY write recently, or the
system halted. Read-only and safe to run on a timer (every few hours).

Usage:
  python3 scripts/check_bot_health.py
  python3 scripts/check_bot_health.py --max-trade-gap-hours 6
  python3 scripts/check_bot_health.py --db data/trading.db

Exit code 0 = healthy, 1 = a warning fired (handy for launchd/cron alerts).
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


def resolve_db(arg):
    if arg:
        return arg
    for cand in ("data/trading.db", "trading.db"):
        if os.path.exists(cand):
            return cand
    return "data/trading.db"


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def last_trade(conn):
    try:
        row = conn.execute(
            "SELECT MAX(closed_at), MAX(opened_at) FROM trade_log"
        ).fetchone()
        return row
    except sqlite3.Error:
        return (None, None)


def last_pnl_daily(conn):
    try:
        row = conn.execute("SELECT MAX(date) FROM daily_pnl").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def mode_state(conn):
    try:
        row = conn.execute(
            "SELECT current_mode, is_switching FROM transformer_state LIMIT 1"
        ).fetchone()
        return row
    except sqlite3.Error:
        return (None, None)


def parse_utc(s):
    if not s:
        return None
    s = s.replace("T", " ").replace("Z", "").replace("+00:00", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser(description="Stall/health watchdog for trading.db")
    ap.add_argument("--db", default=None, help="path to trading.db")
    ap.add_argument("--max-trade-gap-hours", type=float, default=6.0)
    ap.add_argument("--allow-live", action="store_true",
                    help="do not warn if mode is live (off by default)")
    args = ap.parse_args()

    db = resolve_db(args.db)
    if not os.path.exists(db):
        print(f"HEALTH: DB NOT FOUND at {db}")
        sys.exit(1)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    now = now_utc()
    warnings = []

    lt = last_trade(conn)
    last_closed, last_opened = lt[0], lt[1]
    parsed = [parse_utc(last_closed), parse_utc(last_opened)]
    last_any = max([p for p in parsed if p is not None], default=None)
    if last_any is None:
        warnings.append("NO TRADES EVER recorded — bot may not be trading")
    else:
        gap = now - last_any
        if gap > timedelta(hours=args.max_trade_gap_hours):
            warnings.append(
                f"NO trade in {gap.total_seconds()/3600:.1f}h "
                f"(last={last_any.isoformat()}) — possible silent stall"
            )

    last_pnl = last_pnl_daily(conn)
    if last_pnl:
        lp = parse_utc(last_pnl)
        if lp and lp.date() < (now.date() - timedelta(days=1)):
            warnings.append(
                f"daily_pnl stale: last date {last_pnl} "
                f"(no row for today {now.date().isoformat()}) — PNL_DAILY not writing"
            )
    else:
        warnings.append("no daily_pnl row — PnL manager not persisting")

    mode, switching = mode_state(conn)
    if mode is None:
        warnings.append("transformer_state missing — mode unknown")
    elif mode == "bybit" and not args.allow_live:
        warnings.append("MODE IS LIVE — expected paper (shadow/bybit_demo) for experiment")
    if switching:
        warnings.append("exchange switch in progress (is_switching=1)")

    conn.close()

    print(f"HEALTH CHECK @ {now.isoformat()}Z | mode={mode} | "
          f"last_trade={last_any.isoformat() if last_any else 'none'}")
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  ! {w}")
        sys.exit(1)
    print("OK: bot appears healthy (trades flowing, paper mode, PnL writing).")
    sys.exit(0)


if __name__ == "__main__":
    main()
