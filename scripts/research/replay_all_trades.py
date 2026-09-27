"""Minute-by-minute replay of every trade against stored 1m candles.

Answers, with the bot's ACTUAL entries (symbol, side, fill, time):
  1. Edge ratio at fixed horizons (entry quality, independent of exits)
  2. Coin-flip test: same trades, mirrored direction
  3. Exit grid: is there ANY simple SL/TP bracket that makes these entries pay?
  4. Counterfactual for the bot's own 'manual' (intervention) exits
Conservative conventions: excursions measured from the candle AFTER the entry
minute (no look-ahead); if SL and TP are both touched inside one candle, SL is
assumed first.
"""
import sqlite3, statistics as st, datetime as dt, random
from collections import defaultdict

c = sqlite3.connect("shadow/data/shadow.db")
c.row_factory = sqlite3.Row
trades = [dict(r) for r in c.execute(
    "SELECT symbol, side, entry_price, exit_price, opened_at, closed_at, initial_stop_loss, "
    "initial_take_profit, close_trigger, net_pnl_usd, gross_pnl_pct, notional_value "
    "FROM trade_history WHERE closed_at IS NOT NULL ORDER BY opened_at")]

def ms(iso):
    iso = iso.replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)

HMAX = 240
HORIZONS = [5, 15, 30, 60, 120, 240]
COST_NOW = 0.17     # measured: 0.11% fee + 0.06% slippage, % of notional round trip
COST_MAKER = 0.04   # 0.02% maker each side, no slippage (optimistic: assumes fills)

paths = []
for t in trades:
    t0 = ms(t["opened_at"]) // 60000 * 60000
    rows = c.execute(
        "SELECT timestamp, high, low, close FROM klines WHERE symbol=? AND timestamp>? AND timestamp<=? ORDER BY timestamp",
        (t["symbol"], t0, t0 + HMAX * 60000)).fetchall()
    if len(rows) < 30:
        continue
    t["path"] = [(r["high"], r["low"], r["close"]) for r in rows]
    t["t0"] = t0
    t["close_ms"] = ms(t["closed_at"])
    paths.append(t)
print(f"trades with >=30 min of candle path: {len(paths)} / {len(trades)}")

def exc(t, sign, h):
    e = t["entry_price"]
    fav = adv = 0.0
    last = None
    for hi, lo, cl in t["path"][:h]:
        if sign > 0:
            fav = max(fav, (hi - e) / e * 100); adv = max(adv, (e - lo) / e * 100)
        else:
            fav = max(fav, (e - lo) / e * 100); adv = max(adv, (hi - e) / e * 100)
        last = cl
    ret = (last - e) / e * 100 * sign if last else 0.0
    return fav, adv, ret

def sgn(t): return 1 if t["side"] == "Buy" else -1

# ---------- 1 + 2: edge ratio, actual vs mirrored ----------
print("\n=== 1+2. EDGE RATIO (avg MFE / avg MAE) — ACTUAL direction vs MIRRORED (coin-flip test) ===")
print("  horizon | actual: MFE%  MAE%  e-ratio  %close-fav  avg_ret% | mirrored: e-ratio  %close-fav  avg_ret%")
for h in HORIZONS:
    A = [exc(t, sgn(t), h) for t in paths if len(t["path"]) >= min(h, 30)]
    M = [exc(t, -sgn(t), h) for t in paths if len(t["path"]) >= min(h, 30)]
    mfe = st.mean(a[0] for a in A); mae = st.mean(a[1] for a in A)
    mfe_m = st.mean(a[0] for a in M); mae_m = st.mean(a[1] for a in M)
    fav = 100 * sum(a[2] > 0 for a in A) / len(A); fav_m = 100 * sum(a[2] > 0 for a in M) / len(M)
    print(f"  {h:>4}m   |        {mfe:5.2f} {mae:5.2f}  {mfe/mae:6.3f}   {fav:5.1f}%    {st.mean(a[2] for a in A):+.3f} |"
          f"          {mfe_m/mae_m:6.3f}   {fav_m:5.1f}%    {st.mean(a[2] for a in M):+.3f}")

# by phase at 30m
print("\n  e-ratio @30m by period (actual / mirrored):")
for lab, a, b in [("P0 Jul", "0", "2026-08-02"), ("P1 Aug02-15", "2026-08-02", "2026-08-15"),
                  ("P2+ Aug15-now", "2026-08-15", "9999")]:
    sub = [t for t in paths if a <= t["opened_at"][:10] < b]
    if len(sub) < 10: continue
    A = [exc(t, sgn(t), 30) for t in sub]; M = [exc(t, -sgn(t), 30) for t in sub]
    print(f"    {lab:<14} n={len(sub):<4} actual={st.mean(x[0] for x in A)/st.mean(x[1] for x in A):.3f} "
          f"mirrored={st.mean(x[0] for x in M)/st.mean(x[1] for x in M):.3f}")

# ---------- 3: exit grid ----------
def bracket(t, sign, sl, tp, hmax=HMAX):
    e = t["entry_price"]
    last = e
    for hi, lo, cl in t["path"][:hmax]:
        if sign > 0:
            if (e - lo) / e * 100 >= sl: return -sl
            if (hi - e) / e * 100 >= tp: return tp
        else:
            if (hi - e) / e * 100 >= sl: return -sl
            if (e - lo) / e * 100 >= tp: return tp
        last = cl
    return (last - e) / e * 100 * sign

print("\n=== 3. EXIT GRID on the SAME entries (avg net % of notional per trade) ===")
print("  split: first 70% of trades = IN-sample, last 30% = OUT-of-sample (robustness check)")
cut = int(len(paths) * 0.7)
ins, oos = paths[:cut], paths[cut:]
best = []
for sl in (0.5, 1.0, 1.5, 2.0):
    for tp in (0.5, 1.0, 1.5, 2.0, 3.0):
        for hmax in (60, 240):
            r_in = [bracket(t, sgn(t), sl, tp, hmax) for t in ins]
            r_oos = [bracket(t, sgn(t), sl, tp, hmax) for t in oos]
            g_in, g_oos = st.mean(r_in), st.mean(r_oos)
            best.append((g_in - COST_NOW, sl, tp, hmax, g_in, g_oos))
best.sort(reverse=True)
print("  top 8 by IN-sample (gross = before costs):")
print("   SL%  TP%  hold | gross_in  net_in@0.17  net_in@0.04 | gross_oos  net_oos@0.17  net_oos@0.04")
for _, sl, tp, hmax, gi, go in best[:8]:
    print(f"   {sl:3.1f} {tp:4.1f} {hmax:4d}m | {gi:+.3f}   {gi-COST_NOW:+.3f}      {gi-COST_MAKER:+.3f}     | {go:+.3f}    "
          f"{go-COST_NOW:+.3f}       {go-COST_MAKER:+.3f}")
n_pos_now = sum(1 for b in best if b[4] - COST_NOW > 0)
n_pos_mk = sum(1 for b in best if b[4] - COST_MAKER > 0)
print(f"  grid cells (of {len(best)}) with positive IN-sample net: at 0.17% cost={n_pos_now}  at 0.04% cost={n_pos_mk}")
n_both = sum(1 for b in best if b[4] - COST_MAKER > 0 and b[5] - COST_MAKER > 0)
print(f"  cells positive in BOTH halves at maker cost: {n_both}")

# same grid on mirrored direction, for reference
mir = []
for sl in (0.5, 1.0, 1.5, 2.0):
    for tp in (0.5, 1.0, 1.5, 2.0, 3.0):
        mir.append(st.mean(bracket(t, -sgn(t), sl, tp, 240) for t in paths))
print(f"  mirrored-direction grid: best gross={max(mir):+.3f}  mean gross={st.mean(mir):+.3f}"
      f"  | actual-direction grid (240m): best gross="
      f"{max(b[4] for b in best if b[3]==240):+.3f}  mean gross={st.mean(b[4] for b in best if b[3]==240):+.3f}")

# ---------- 4: counterfactual for 'manual' closes ----------
print("\n=== 4. COUNTERFACTUAL for the bot's 'manual' (intervention) exits ===")
man = [t for t in paths if t["close_trigger"] == "manual" and t["initial_stop_loss"] and t["initial_take_profit"]]
act, hyp = [], []
for t in man:
    e, s = t["entry_price"], sgn(t)
    sl = abs(t["initial_stop_loss"] - e) / e * 100
    tp = abs(t["initial_take_profit"] - e) / e * 100
    act.append(t["gross_pnl_pct"] or 0)
    hyp.append(bracket(t, s, sl, tp, HMAX))
if man:
    print(f"  n={len(man)}  actual avg gross={st.mean(act):+.3f}%   if left to original SL/TP (<=240m) avg gross={st.mean(hyp):+.3f}%")
    better = sum(1 for a, h in zip(act, hyp) if h > a)
    print(f"  holding would have been better in {better}/{len(man)} ({100*better/len(man):.0f}%) cases")
    print(f"  of those held: hit TP={sum(1 for t,h in zip(man,hyp) if h>0 and abs(h-abs(t['initial_take_profit']-t['entry_price'])/t['entry_price']*100)<1e-6)}  "
          f"hit SL={sum(1 for t,h in zip(man,hyp) if h<0 and abs(abs(h)-abs(t['initial_stop_loss']-t['entry_price'])/t['entry_price']*100)<1e-6)}")

# ---------- 5: realised capture ----------
print("\n=== 5. HOW MUCH OF THE AVAILABLE MOVE THE BOT ACTUALLY KEPT ===")
w = [t for t in paths if (t["gross_pnl_pct"] or 0) > 0]
if w:
    caps = []
    for t in w:
        held = max(1, int((t["close_ms"] - t["t0"]) / 60000))
        fav, adv, _ = exc(t, sgn(t), min(held, HMAX))
        if fav > 0: caps.append((t["gross_pnl_pct"] or 0) / fav)
    print(f"  winners: median capture of in-trade peak = {100*st.median(caps):.0f}%  (n={len(caps)})")
l = [t for t in paths if (t["gross_pnl_pct"] or 0) <= 0]
green = 0
for t in l:
    held = max(1, int((t["close_ms"] - t["t0"]) / 60000))
    fav, _, _ = exc(t, sgn(t), min(held, HMAX))
    if fav >= COST_NOW * 2: green += 1
print(f"  losers that were at least +{COST_NOW*2:.2f}% in profit before closing red: {green}/{len(l)} ({100*green/len(l):.0f}%)")
