#!/usr/bin/env python3
"""Experiment comparator for the Trading Intelligence MCP bot.

Supports the "refine one by one" loop (Phase 3 of the 30-day plan): compare a
BASELINE window (before a change) against a TREATMENT window (after a change)
and report whether expectancy / profit factor / win rate improved per
strategy. Read-only and safe to run any time.

Usage:
  python3 scripts/compare_experiment.py --split-date 2026-08-05
  python3 scripts/compare_experiment.py \
      --baseline-start 2026-07-09 --baseline-end 2026-07-22 \
      --treatment-start 2026-07-23 --treatment-end 2026-08-05
  python3 scripts/compare_experiment.py --split-date 2026-08-05 --top 10

Window filter uses trade_intelligence.trade_closed_at (falls back to
trade_log.closed_at if trade_intelligence is empty).
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


def fetch_window(conn, start, end):
    sql = (
        "SELECT strategy_name, pnl_pct, pnl_usd, win "
        "FROM trade_intelligence "
        "WHERE DATE(trade_closed_at) >= ? AND DATE(trade_closed_at) <= ?"
    )
    try:
        rows = conn.execute(sql, (start, end)).fetchall()
        if rows:
            return rows
    except sqlite3.Error:
        pass
    sql2 = (
        "SELECT strategy, pnl_pct, pnl_usd, "
        "CASE WHEN pnl_usd >= 0 THEN 1 ELSE 0 END "
        "FROM trade_log "
        "WHERE DATE(closed_at) >= ? AND DATE(closed_at) <= ?"
    )
    try:
        return conn.execute(sql2, (start, end)).fetchall()
    except sqlite3.Error:
        return []


def summarize(values):
    n = len(values)
    if n == 0:
        return None
    wins = [v for v in values if v[2]]
    losses = [v for v in values if not v[2]]
    gross_win = sum(v[1] for v in wins)
    gross_loss = sum(abs(v[1]) for v in losses)
    wr = len(wins) / n
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    exp = wr * avg_win - (1 - wr) * avg_loss
    return {"n": n, "wr": wr, "pf": pf, "exp": exp,
            "net": sum(v[1] for v in values), "avg_pct": sum(v[0] for v in values) / n}


def pf_str(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def group_by(rows, idx):
    g = defaultdict(list)
    for r in rows:
        key = r[idx] if r[idx] not in (None, "") else "(unknown)"
        g[key].append((r[1], r[2], bool(r[3])))
    return g


def main():
    ap = argparse.ArgumentParser(description="Compare baseline vs treatment trade windows")
    ap.add_argument("--db", default=None)
    ap.add_argument("--split-date", default=None,
                    help="trades before = baseline, on/after = treatment")
    ap.add_argument("--baseline-start", default=None)
    ap.add_argument("--baseline-end", default=None)
    ap.add_argument("--treatment-start", default=None)
    ap.add_argument("--treatment-end", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out-dir", default="data/trade_logs")
    args = ap.parse_args()

    if args.split_date:
        bs, be = "2000-01-01", (datetime_from(args.split_date) - timedelta(days=1)).isoformat()
        ts, te = args.split_date, date.today().isoformat()
    else:
        bs, be = args.baseline_start, args.baseline_end
        ts, te = args.treatment_start, args.treatment_end
    if not (bs and be and ts and te):
        print("ERROR: provide --split-date OR all four --baseline/treatment start/end.",
              file=sys.stderr)
        sys.exit(1)

    db = resolve_db(args.db)
    if not os.path.exists(db):
        print(f"ERROR: database not found at {db}.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db)
    base_rows = fetch_window(conn, bs, be)
    treat_rows = fetch_window(conn, ts, te)
    conn.close()

    out = [f"# EXPERIMENT COMPARISON", ""]
    out.append(f"Baseline : {bs} .. {be}  ({len(base_rows)} trades)")
    out.append(f"Treatment: {ts} .. {te}  ({len(treat_rows)} trades)")
    out.append("")

    bs_sum = summarize([(r[1], r[2], bool(r[3])) for r in base_rows])
    ts_sum = summarize([(r[1], r[2], bool(r[3])) for r in treat_rows])
    if bs_sum and ts_sum:
        out.append("## OVERALL")
        out.append(f"{'metric':<12}{'baseline':>12}{'treatment':>12}{'delta':>12}")
        out.append(f"{'n':<12}{bs_sum['n']:>12}{ts_sum['n']:>12}{ts_sum['n']-bs_sum['n']:>+12}")
        out.append(f"{'win%':<12}{bs_sum['wr']*100:>11.1f}{ts_sum['wr']*100:>11.1f}"
                   f"{ (ts_sum['wr']-bs_sum['wr'])*100:>+11.1f}")
        out.append(f"{'PF':<12}{pf_str(bs_sum['pf']):>12}{pf_str(ts_sum['pf']):>12}")
        out.append(f"{'exp$':<12}{bs_sum['exp']:>12.2f}{ts_sum['exp']:>12.2f}"
                   f"{ts_sum['exp']-bs_sum['exp']:>+12.2f}")
        out.append(f"{'net$':<12}{bs_sum['net']:>+12.2f}{ts_sum['net']:>+12.2f}"
                   f"{ts_sum['net']-bs_sum['net']:>+12.2f}")

    bg = group_by(base_rows, 0)
    tg = group_by(treat_rows, 0)
    strategies = sorted(set(bg) | set(tg))
    out.append("")
    out.append("## PER-STRATEGY (expectancy delta = treatment - baseline)")
    out.append(f"{'strategy':<24}{'base_exp':>10}{'treat_exp':>10}{'delta_exp':>10}{'verdict':>10}")
    verdicts = []
    for s in strategies:
        b = summarize(bg[s]) if s in bg else None
        t = summarize(tg[s]) if s in tg else None
        be_v = b["exp"] if b else 0.0
        te_v = t["exp"] if t else 0.0
        delta = te_v - be_v
        verdict = "n/a" if (not b or not t) else ("IMPROVED" if delta > 0 else "REGRESSED")
        if b and t:
            verdicts.append((s, delta, verdict))
        out.append(f"{str(s):<24}{be_v:>10.2f}{te_v:>10.2f}{delta:>+10.2f}{verdict:>10}")

    if verdicts:
        verdicts.sort(key=lambda x: x[1], reverse=True)
        out.append("")
        out.append(f"TOP IMPROVED (top {min(args.top, len(verdicts))}):")
        for s, d, v in verdicts[:args.top]:
            out.append(f"  + {s}: exp delta ${d:+.2f} ({v})")
        out.append(f"BOTTOM REGRESSED (top {min(args.top, len(verdicts))}):")
        for s, d, v in verdicts[-args.top:]:
            out.append(f"  - {s}: exp delta ${d:+.2f} ({v})")

    text = "\n".join(out) + "\n"
    print(text)
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, "EXPERIMENT_COMPARISON.md")
    with open(path, "w") as f:
        f.write(text)
    print(f"Wrote: {path}")


def datetime_from(s):
    try:
        return date.fromisoformat(s)
    except ValueError:
        return date.today()


if __name__ == "__main__":
    main()
