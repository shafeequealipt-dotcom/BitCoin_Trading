"""Forensic pass over the shadow exchange ledger (authoritative economics)."""
import sqlite3, statistics as st
from collections import defaultdict

c = sqlite3.connect("shadow/data/shadow.db")
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("SELECT * FROM trade_history WHERE closed_at IS NOT NULL ORDER BY closed_at")]
print(f"ledger trades: {len(rows)}  first={rows[0]['closed_at'][:10]}  last={rows[-1]['closed_at'][:10]}")

# --- unit sanity: print 3 rows ---
print("\n=== unit sanity (3 rows) ===")
for r in rows[-3:]:
    side = 1 if r["side"] == "Buy" else -1
    move = (r["exit_price"] - r["entry_price"]) / r["entry_price"] * 100 * side
    print(f"  {r['symbol']:<12} {r['side']:<4} lev={r['leverage']} notional={r['notional_value']:.0f} margin={r['margin_used']:.0f} "
          f"move%={move:+.3f} gross%={r['gross_pnl_pct']} net%={r['net_pnl_pct']} gross$={r['gross_pnl_usd']:.2f} "
          f"fees$={r['total_fees_usd']:.2f} slip$={r['total_slippage_usd']:.2f} peak%={r['peak_pnl_pct']} mdd%={r['max_drawdown_pct']}")

def move_pct(r):
    side = 1 if r["side"] == "Buy" else -1
    return (r["exit_price"] - r["entry_price"]) / r["entry_price"] * 100 * side

for r in rows:
    r["move"] = move_pct(r)
    n = r["notional_value"] or 0
    r["fee_pct"] = (r["total_fees_usd"] or 0) / n * 100 if n else 0
    r["slip_pct"] = (r["total_slippage_usd"] or 0) / n * 100 if n else 0
    r["hold_min"] = (r["hold_duration_seconds"] or 0) / 60

def summary(label, rs):
    if not rs:
        print(f"  {label:<34} n=0"); return
    n = len(rs)
    w = [r for r in rs if (r["net_pnl_usd"] or 0) > 0]
    l = [r for r in rs if (r["net_pnl_usd"] or 0) <= 0]
    net = sum(r["net_pnl_usd"] or 0 for r in rs)
    gross = sum(r["gross_pnl_usd"] or 0 for r in rs)
    fees = sum(r["total_fees_usd"] or 0 for r in rs)
    aw = st.mean(r["net_pnl_usd"] for r in w) if w else 0
    al = st.mean(r["net_pnl_usd"] for r in l) if l else 0
    payoff = abs(aw / al) if al else 0
    mv = st.mean(r["move"] for r in rs)
    print(f"  {label:<34} n={n:<5} win={100*len(w)/n:5.1f}% gross=${gross:+9.2f} fees=${fees:8.2f} net=${net:+9.2f} "
          f"$/tr={net/n:+6.2f} avgW=${aw:+6.2f} avgL=${al:+6.2f} payoff={payoff:4.2f} avg_move={mv:+.3f}%")

print("\n=== OVERALL ===")
summary("all", rows)

print("\n=== BY PHASE (closed_at) ===")
phases = [("P0 pre-Aug02", "0000", "2026-08-02"), ("P1 Aug02-15 six fixes", "2026-08-02", "2026-08-15"),
          ("P2 Aug15-Sep05 phases1-4", "2026-08-15", "2026-09-05"), ("P3 Sep05-16 trail revert", "2026-09-05", "2026-09-16"),
          ("P4 Sep16+ RR/tradeable", "2026-09-16", "9999")]
for lab, a, b in phases:
    summary(lab, [r for r in rows if a <= r["closed_at"][:10] < b])

print("\n=== TRADES PER DAY (activity collapse) ===")
per_day = defaultdict(int)
for r in rows: per_day[r["closed_at"][:7]] += 1
for k in sorted(per_day): print(f"  {k}: {per_day[k]} trades")

print("\n=== COST STRUCTURE (per trade, % of notional) ===")
print(f"  fee%   median={st.median(r['fee_pct'] for r in rows):.4f}  mean={st.mean(r['fee_pct'] for r in rows):.4f}")
print(f"  slip%  median={st.median(r['slip_pct'] for r in rows):.4f}  mean={st.mean(r['slip_pct'] for r in rows):.4f}")
print(f"  avg |move| per trade = {st.mean(abs(r['move']) for r in rows):.3f}%   avg signed move = {st.mean(r['move'] for r in rows):+.4f}%")
tot_g = sum(r["gross_pnl_usd"] or 0 for r in rows); tot_f = sum(r["total_fees_usd"] or 0 for r in rows)
print(f"  total gross ${tot_g:+.2f}  total fees ${tot_f:.2f}  -> fees are {100*tot_f/max(abs(tot_g),1e-9):.0f}% of |gross|")

print("\n=== BY CLOSE TRIGGER ===")
bt = defaultdict(list)
for r in rows: bt[r["close_trigger"] or "?"].append(r)
for k, v in sorted(bt.items(), key=lambda kv: -len(kv[1])): summary(k, v)

print("\n=== BY SIDE ===")
for s in ("Buy", "Sell"): summary(s, [r for r in rows if r["side"] == s])

print("\n=== BY HOLD TIME ===")
for lab, a, b in [("<5m", 0, 5), ("5-15m", 5, 15), ("15-30m", 15, 30), ("30-60m", 30, 60), ("60-120m", 60, 120), (">120m", 120, 1e9)]:
    summary(lab, [r for r in rows if a <= r["hold_min"] < b])

print("\n=== BY LEVERAGE ===")
bl = defaultdict(list)
for r in rows: bl[int(r["leverage"] or 0)].append(r)
for k in sorted(bl): summary(f"{k}x", bl[k])

print("\n=== BY HOUR (UTC, opened) ===")
for lab, hs in [("00-04 Asia late", range(0, 4)), ("04-08 Asia/pre-EU", range(4, 8)), ("08-12 London", range(8, 12)),
                ("12-16 London/NY", range(12, 16)), ("16-20 NY", range(16, 20)), ("20-24 NY late", range(20, 24))]:
    summary(lab, [r for r in rows if r["opened_at"] and int(r["opened_at"][11:13]) in hs])

# ---- MFE / MAE: the core question: are ENTRIES bad or EXITS bad? ----
print("\n=== MFE / MAE (peak_pnl_pct / max_drawdown_pct) ===")
pk = [r for r in rows if r["peak_pnl_pct"] is not None]
print(f"  rows with peak data: {len(pk)}")
if pk:
    print("  peak_pnl_pct sample:", [round(r["peak_pnl_pct"], 3) for r in pk[-8:]])
    print("  max_drawdown_pct sample:", [round(r["max_drawdown_pct"] or 0, 3) for r in pk[-8:]])
    print("  gross_pnl_pct sample:", [round(r["gross_pnl_pct"] or 0, 3) for r in pk[-8:]])
    print("  move% sample:", [round(r["move"], 3) for r in pk[-8:]])
