#!/usr/bin/env python3
"""30-day post-mortem analyzer for the Trading Intelligence MCP bot.

Reads closed-trade autopsies from data/trading.db and ranks performance by
every useful dimension (strategy, category, regime, TIAS category, direction,
symbol, APEX effect, exit reason) so you can see which trades went well.
Read-only and safe to run anytime. Run it after the 30-day collection window.

Usage:
  python scripts/analyze_30d.py                       (last 30 days)
  python scripts/analyze_30d.py --days 30 --top 10
  python scripts/analyze_30d.py --since 2026-07-01 --all
  python scripts/analyze_30d.py --db data/trading.db --out-dir data/trade_logs

Base table: trade_intelligence (richest — carries outcome + regime + TIAS
category + APEX flags per close, linked to trade_log by trade_id). Falls back
to trade_log if trade_intelligence is empty.
"""
import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta


def resolve_db(arg):
    if arg:
        return arg
    for cand in ("data/trading.db", "trading.db"):
        if os.path.exists(cand):
            return cand
    return "data/trading.db"


def load_rows(conn, since, use_all):
    sql = (
        "SELECT strategy_name, strategy_category, regime, ds_category, direction, "
        "symbol, apex_optimized, apex_flipped, closed_by, "
        "pnl_pct, pnl_usd, win "
        "FROM trade_intelligence"
    )
    if not use_all:
        sql += " WHERE DATE(trade_closed_at) >= ?"
        params = (since,)
    else:
        params = ()
    try:
        rows = conn.execute(sql, params).fetchall()
        if rows:
            return rows, "trade_intelligence"
    except sqlite3.Error as e:
        print(f"  [warn] trade_intelligence query failed: {e}", file=sys.stderr)

    sql2 = (
        "SELECT strategy, '(unknown)', '(unknown)', '(unknown)', direction, "
        "symbol, 0, 0, close_reason, pnl_pct, pnl_usd, "
        "CASE WHEN pnl_usd >= 0 THEN 1 ELSE 0 END "
        "FROM trade_log"
    )
    if not use_all:
        sql2 += " WHERE DATE(closed_at) >= ?"
        params2 = (since,)
    else:
        params2 = ()
    try:
        rows2 = conn.execute(sql2, params2).fetchall()
        if rows2:
            return rows2, "trade_log (fallback)"
    except sqlite3.Error as e:
        print(f"  [warn] trade_log query failed: {e}", file=sys.stderr)
    return [], "none"


def summarize(values):
    n = len(values)
    if n == 0:
        return None
    wins = [v for v in values if v[2]]
    losses = [v for v in values if not v[2]]
    gross_win = sum(v[1] for v in wins)
    gross_loss = sum(abs(v[1]) for v in losses)
    win_rate = len(wins) / n
    if gross_loss > 0:
        pf = gross_win / gross_loss
    elif gross_win > 0:
        pf = float("inf")
    else:
        pf = 0.0
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    avg_pct = sum(v[0] for v in values) / n
    total_usd = sum(v[1] for v in values)
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "total_usd": total_usd, "avg_pct": avg_pct,
        "pf": pf, "expectancy": expectancy, "avg_win": avg_win, "avg_loss": avg_loss,
    }


def pf_str(pf):
    if pf == float("inf"):
        return "inf (no losses)"
    return f"{pf:.2f}"


def group_by(rows, idx):
    g = defaultdict(list)
    for r in rows:
        key = r[idx]
        if key in (None, ""):
            key = "(unknown)"
        g[key].append((r[9], r[10], bool(r[11])))
    return g


def rank_section(title, rows, idx, top, out):
    groups = group_by(rows, idx)
    stats = []
    for key, vals in groups.items():
        s = summarize(vals)
        if s:
            stats.append((key, s))
    stats.sort(key=lambda x: x[1]["expectancy"], reverse=True)
    out.append(f"\n## {title}\n")
    out.append(f"{'group':<26} {'n':>4} {'win%':>6} {'PF':>9} {'exp$':>9} {'net$':>10} {'avg%':>7}")
    for key, s in stats:
        out.append(
            f"{str(key):<26} {s['n']:>4} {s['win_rate']*100:>5.1f} "
            f"{pf_str(s['pf']):>9} {s['expectancy']:>9.2f} {s['total_usd']:>+10.2f} "
            f"{s['avg_pct']:>+7.2f}"
        )
    if top and len(stats) > top:
        out.append(f"\n  TOP {top} by expectancy:")
        for key, s in stats[:top]:
            out.append(f"    + {key}: n={s['n']} win%={s['win_rate']*100:.1f} "
                       f"PF={pf_str(s['pf'])} net=${s['total_usd']:+.2f}")
        out.append(f"  BOTTOM {top} by expectancy:")
        for key, s in stats[-top:]:
            out.append(f"    - {key}: n={s['n']} win%={s['win_rate']*100:.1f} "
                       f"PF={pf_str(s['pf'])} net=${s['total_usd']:+.2f}")


def best_configs(rows, min_n, top, out):
    g = defaultdict(list)
    for r in rows:
        key = (r[0] or "(unknown)", r[2] or "(unknown)")
        g[key].append((r[9], r[10], bool(r[11])))
    stats = [(k, summarize(v)) for k, v in g.items() if summarize(v) and summarize(v)["n"] >= min_n]
    stats.sort(key=lambda x: x[1]["expectancy"], reverse=True)
    out.append(f"\n## BEST STRATEGY x REGIME (min {min_n} trades)\n")
    if not stats:
        out.append("  (not enough samples with the minimum trade count)")
        return
    for (strat, regime), s in stats[:top]:
        out.append(
            f"  + {strat} @ {regime}: n={s['n']} win%={s['win_rate']*100:.1f} "
            f"PF={pf_str(s['pf'])} exp=${s['expectancy']:.2f} net=${s['total_usd']:+.2f}"
        )


def best_single_trades(rows, top, out):
    vals = [(r[0], r[2], r[4], r[3], r[5], r[6], r[10]) for r in rows]
    vals.sort(key=lambda x: x[6], reverse=True)
    out.append(f"\n## TOP {top} INDIVIDUAL TRADES (by net USD)\n")
    for strat, regime, direction, ds_cat, symbol, apex, usd in vals[:top]:
        apex_tag = "APEX" if apex else "raw"
        out.append(
            f"  + {symbol} {direction} {strat} | {regime} | {ds_cat} | "
            f"{apex_tag} | ${usd:+.2f}"
        )


def main():
    ap = argparse.ArgumentParser(description="30-day trade performance analyzer")
    ap.add_argument("--db", default=None, help="path to trading.db")
    ap.add_argument("--days", type=int, default=30, help="lookback window in days")
    ap.add_argument("--since", default=None, help="inclusive start day YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="ignore window, use all rows")
    ap.add_argument("--top", type=int, default=10, help="top/bottom N to print")
    ap.add_argument("--min-n", type=int, default=5, help="min trades to rank a group")
    ap.add_argument("--out-dir", default="data/trade_logs", help="output directory")
    args = ap.parse_args()

    db = resolve_db(args.db)
    if not os.path.exists(db):
        print(f"ERROR: database not found at {db}. Run the bot first.", file=sys.stderr)
        sys.exit(1)

    since = args.since or (date.today() - timedelta(days=args.days - 1)).isoformat()
    conn = sqlite3.connect(db)
    rows, source = load_rows(conn, since, args.all)
    conn.close()

    if not rows:
        print("No closed trades found in the window. Run the bot, then re-run this.")
        sys.exit(0)

    out = [f"# 30-DAY TRADE ANALYSIS", ""]
    out.append(f"Source table: {source}")
    out.append(f"Window: {('all rows' if args.all else 'since ' + since)}")
    out.append(f"Trades analyzed: {len(rows)}")

    overall = summarize([(r[9], r[10], bool(r[11])) for r in rows])
    if overall:
        out.append("")
        out.append(f"OVERALL: n={overall['n']} win%={overall['win_rate']*100:.1f} "
                   f"PF={pf_str(overall['pf'])} exp=${overall['expectancy']:.2f} "
                   f"net=${overall['total_usd']:+.2f} avg%={overall['avg_pct']:+.2f}")

    rank_section("BY STRATEGY", rows, 0, args.top, out)
    rank_section("BY STRATEGY CATEGORY", rows, 1, args.top, out)
    rank_section("BY REGIME", rows, 2, args.top, out)
    rank_section("BY TIAS CATEGORY (ds_category)", rows, 3, args.top, out)
    rank_section("BY DIRECTION", rows, 4, args.top, out)
    rank_section("BY SYMBOL", rows, 5, args.top, out)
    rank_section("BY EXIT REASON (closed_by)", rows, 8, args.top, out)
    rank_section("BY APEX OPTIMIZED (0=raw, 1=APEX)", rows, 6, 0, out)
    rank_section("BY APEX FLIPPED (0=no, 1=flipped)", rows, 7, 0, out)

    best_configs(rows, args.min_n, args.top, out)
    best_single_trades(rows, args.top, out)

    text = "\n".join(out) + "\n"
    print(text)

    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, "ANALYSIS_30d.md")
    with open(path, "w") as f:
        f.write(text)
    print(f"Wrote: {path}")


if __name__ == "__main__":
    main()
