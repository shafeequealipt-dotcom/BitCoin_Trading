"""Confounder checks for the replay: side split, market drift, and whether any
recorded entry feature (incl. the brain's own confidence) predicts the 30-minute
directional outcome. Label = signed return at +30m from the actual fill."""
import sqlite3, statistics as st, datetime as dt, math

sh = sqlite3.connect("shadow/data/shadow.db"); sh.row_factory = sqlite3.Row
tr = sqlite3.connect("data/trading.db"); tr.row_factory = sqlite3.Row

def ms(iso):
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)

def ret_at(symbol, t_ms, entry, sign, h):
    t0 = t_ms // 60000 * 60000
    r = sh.execute("SELECT close FROM klines WHERE symbol=? AND timestamp<=? ORDER BY timestamp DESC LIMIT 1",
                   (symbol, t0 + h * 60000)).fetchone()
    return (r[0] - entry) / entry * 100 * sign if r else None

rows = [dict(r) for r in sh.execute("SELECT symbol, side, entry_price, opened_at FROM trade_history WHERE closed_at IS NOT NULL")]
for r in rows:
    r["s"] = 1 if r["side"] == "Buy" else -1
    r["t"] = ms(r["opened_at"])
    r["r30"] = ret_at(r["symbol"], r["t"], r["entry_price"], r["s"], 30)
rows = [r for r in rows if r["r30"] is not None]

def tstat(xs):
    if len(xs) < 3: return 0.0
    return st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs)))

print("=== A. SIDE SPLIT at +30m (actual direction) ===")
for side in ("Buy", "Sell"):
    xs = [r["r30"] for r in rows if r["side"] == side]
    print(f"  {side:<4} n={len(xs):<4} right-way={100*sum(x>0 for x in xs)/len(xs):5.1f}%  avg={st.mean(xs):+.3f}%  t={tstat(xs):+.2f}")
xs = [r["r30"] for r in rows]
print(f"  ALL  n={len(xs):<4} right-way={100*sum(x>0 for x in xs)/len(xs):5.1f}%  avg={st.mean(xs):+.3f}%  t={tstat(xs):+.2f}")

print("\n=== B. MARKET DRIFT over the same window (same coins, same moments, LONG-only +30m) ===")
drift = [ret_at(r["symbol"], r["t"], r["entry_price"], 1, 30) for r in rows]
drift = [d for d in drift if d is not None]
print(f"  generic long +30m from the same entry moments: avg={st.mean(drift):+.3f}%  up-share={100*sum(d>0 for d in drift)/len(drift):.1f}%")
btc = sh.execute("SELECT close FROM klines WHERE symbol='BTCUSDT' ORDER BY timestamp ASC LIMIT 1").fetchone()[0]
btc2 = sh.execute("SELECT close FROM klines WHERE symbol='BTCUSDT' ORDER BY timestamp DESC LIMIT 1").fetchone()[0]
print(f"  BTC over whole window: {btc:.0f} -> {btc2:.0f} ({(btc2-btc)/btc*100:+.1f}%)")

print("\n=== C. DO ANY RECORDED ENTRY FEATURES PREDICT THE +30m OUTCOME? (trade_intelligence join) ===")
ti = [dict(r) for r in tr.execute(
    "SELECT symbol, direction, entry_price, captured_at, trade_closed_at, hold_seconds, claude_confidence, entry_score, "
    "supporting_count, opposing_count, adx, atr_pct, volume_ratio, rsi, fear_greed_value, leverage, position_size_usd "
    "FROM trade_intelligence")]
joined = []
for t in ti:
    try:
        close_ms = ms(t["trade_closed_at"]); open_ms = close_ms - int((t["hold_seconds"] or 0) * 1000)
    except Exception:
        continue
    s = 1 if (t["direction"] or "").lower() in ("buy", "long") else -1
    if not t["entry_price"]: continue
    r30 = ret_at(t["symbol"], open_ms, t["entry_price"], s, 30)
    if r30 is None: continue
    t["r30"] = r30; joined.append(t)
print(f"  joined rows: {len(joined)}")
for f in ("claude_confidence", "entry_score", "supporting_count", "opposing_count", "adx", "atr_pct",
          "volume_ratio", "rsi", "fear_greed_value", "leverage", "position_size_usd"):
    vals = [(t[f], t["r30"]) for t in joined if isinstance(t[f], (int, float))]
    if len(vals) < 50: print(f"  {f:<20} n={len(vals)} (too few)"); continue
    vals.sort(key=lambda x: x[0]); q = len(vals) // 5
    lo = [v[1] for v in vals[:q]]; hi = [v[1] for v in vals[-q:]]
    # rank correlation (Spearman) between feature and outcome
    n = len(vals); rx = {i: i for i in range(n)}
    ys = sorted(range(n), key=lambda i: vals[i][1]); ry = {i: 0 for i in range(n)}
    for rank, i in enumerate(ys): ry[i] = rank
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n)); rho = 1 - 6 * d2 / (n * (n * n - 1))
    print(f"  {f:<20} n={n:<4} bottom-20% avg={st.mean(lo):+.3f}% right={100*sum(x>0 for x in lo)/len(lo):4.1f}% | "
          f"top-20% avg={st.mean(hi):+.3f}% right={100*sum(x>0 for x in hi)/len(hi):4.1f}% | spearman={rho:+.3f}")
