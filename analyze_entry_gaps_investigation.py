#!/usr/bin/env python3
"""Read-only analysis harness for the Entry-Quality Gaps and H1/M5 Timeframe
investigation. Reconstructs entry-time features (which are not persisted) from
the klines and coin_regime_history tables, and runs winners-vs-losers
separability statistics in pure numpy (scipy/sklearn are not installed).

Scope: bybit_demo trades only, frozen at the analysis cutoff, per operator
decision. The database is opened READ-ONLY; nothing is written or modified.

Timeframe labels are explicit everywhere: M5 = five-minute, H1 = one-hour.

Usage: python analyze_entry_gaps_investigation.py --item {1,2,3,all}
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np

DB_URI = "file:data/trading.db?mode=ro"
CUTOFF = "2026-05-26T18:00:00"  # frozen analysis cutoff (UTC)


def connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_URI, uri=True)


# --------------------------------------------------------------------------- #
# Time helpers                                                                 #
# --------------------------------------------------------------------------- #
def entry_dt_from_setup(setup_id: str) -> datetime | None:
    try:
        stamp = setup_id.split("_")[0]
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def floor_m5(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 5)


# --------------------------------------------------------------------------- #
# Pure-numpy statistics                                                        #
# --------------------------------------------------------------------------- #
def rankdata_avg(a: np.ndarray) -> np.ndarray:
    """Average ranks (1-based), ties averaged."""
    a = np.asarray(a, dtype=float)
    order = a.argsort(kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    sa = a[order]
    i = 0
    n = len(a)
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        ranks[order[i : j + 1]] = avg
        i = j + 1
    return ranks


def auc_mannwhitney(pos: np.ndarray, neg: np.ndarray) -> float:
    """AUC = P(pos > neg) with ties at 0.5. Equivalent to U / (n1 n2)."""
    pos = np.asarray(pos, dtype=float)
    neg = np.asarray(neg, dtype=float)
    pos = pos[~np.isnan(pos)]
    neg = neg[~np.isnan(neg)]
    n1, n2 = len(pos), len(neg)
    if n1 == 0 or n2 == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    r = rankdata_avg(allv)
    R1 = r[:n1].sum()
    U1 = R1 - n1 * (n1 + 1) / 2.0
    return U1 / (n1 * n2)


def cliffs_delta(pos: np.ndarray, neg: np.ndarray) -> float:
    a = auc_mannwhitney(pos, neg)
    return float("nan") if np.isnan(a) else 2.0 * a - 1.0


def summarize(label: str, win_vals, los_vals) -> str:
    w = np.asarray(win_vals, dtype=float)
    l = np.asarray(los_vals, dtype=float)
    w = w[~np.isnan(w)]
    l = l[~np.isnan(l)]
    if len(w) == 0 or len(l) == 0:
        return f"  {label}: insufficient data (winners={len(w)} losers={len(l)})"
    a = auc_mannwhitney(w, l)
    d = cliffs_delta(w, l)
    return (
        f"  {label}: winners n={len(w)} mean={w.mean():.3f} median={np.median(w):.3f} | "
        f"losers n={len(l)} mean={l.mean():.3f} median={np.median(l):.3f} | "
        f"AUC={a:.3f} Cliff_delta={d:+.3f}"
    )


# --------------------------------------------------------------------------- #
# Entry-time reconstruction                                                    #
# --------------------------------------------------------------------------- #
def entry_m5_volume_ratio(c: sqlite3.Connection, symbol: str, entry: datetime) -> float | None:
    """current_volume / SMA(volume,20) on the last 20 CLOSED M5 buckets strictly
    before the bucket containing entry (forming bucket excluded)."""
    cutoff_open = floor_m5(entry).strftime("%Y-%m-%dT%H:%M:%S")
    rows = c.execute(
        "SELECT volume FROM klines WHERE symbol=? AND timeframe='5' AND timestamp < ? "
        "ORDER BY timestamp DESC LIMIT 20",
        (symbol, cutoff_open),
    ).fetchall()
    if len(rows) < 20:
        return None
    vols = np.array([r[0] for r in rows], dtype=float)  # newest..oldest
    sma = vols.mean()
    if sma <= 0:
        return None
    return float(vols[0] / sma)


def entry_h1_regime_row(c: sqlite3.Connection, symbol: str, entry: datetime):
    """Nearest preceding per-coin H1 regime snapshot: (adx_h1, vol_ratio_h1,
    atr_pctile_h1, regime_h1)."""
    entry_sp = entry.strftime("%Y-%m-%d %H:%M:%S")
    row = c.execute(
        "SELECT adx, volume_ratio, atr_percentile, regime FROM coin_regime_history "
        "WHERE symbol=? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
        (symbol, entry_sp),
    ).fetchone()
    return row


# --------------------------------------------------------------------------- #
# Data load                                                                    #
# --------------------------------------------------------------------------- #
def load_trades(c: sqlite3.Connection):
    return c.execute(
        """
        SELECT setup_id, symbol, direction, win, pnl_usd, pnl_pct, hold_seconds,
               entry_regime, entry_rsi, entry_atr_pct, entry_macd_hist,
               entry_score, claude_confidence, supporting_count, opposing_count,
               leverage, trade_closed_at
        FROM trade_intelligence
        WHERE exchange_mode='bybit_demo' AND trade_closed_at <= ?
        ORDER BY trade_closed_at
        """,
        (CUTOFF,),
    ).fetchall()


# --------------------------------------------------------------------------- #
# Item 1: volume edge at entry, controlled for ADX and regime                  #
# --------------------------------------------------------------------------- #
def item1(c: sqlite3.Connection) -> None:
    print("=" * 78)
    print("ITEM 1: entry-time volume edge (M5 reconstructed), controlled for ADX/regime")
    print("=" * 78)
    trades = load_trades(c)
    recs = []
    for t in trades:
        (setup_id, symbol, direction, win, pnl_usd, pnl_pct, hold_s, e_reg, e_rsi,
         e_atr, e_macd, e_score, e_conf, sup, opp, lev, closed) = t
        if not setup_id:
            continue
        entry = entry_dt_from_setup(setup_id)
        if entry is None:
            continue
        vr_m5 = entry_m5_volume_ratio(c, symbol, entry)
        h1 = entry_h1_regime_row(c, symbol, entry)
        adx_h1 = h1[0] if h1 else None
        vr_h1 = h1[1] if h1 else None
        regime_h1 = h1[3] if h1 else None
        recs.append(dict(symbol=symbol, win=int(win), pnl=float(pnl_usd or 0),
                         vr_m5=vr_m5, adx_h1=adx_h1, vr_h1=vr_h1, regime_h1=regime_h1,
                         e_reg=e_reg))
    n_total = len(recs)
    n_m5 = sum(1 for r in recs if r["vr_m5"] is not None)
    n_h1 = sum(1 for r in recs if r["adx_h1"] is not None)
    print(f"\nReconstructed: {n_total} setup_id trades; entry-time M5 volume on {n_m5}; "
          f"entry-time H1 ADX on {n_h1}.")

    def split(key, pred=lambda r: True):
        w = [r[key] for r in recs if r["win"] == 1 and r[key] is not None and pred(r)]
        l = [r[key] for r in recs if r["win"] == 0 and r[key] is not None and pred(r)]
        return w, l

    print("\n-- Overall entry-time volume (the Item 1 edge test) --")
    w, l = split("vr_m5"); print(summarize("entry M5 volume_ratio", w, l))
    w, l = split("vr_h1"); print(summarize("entry H1 volume_ratio", w, l))
    print("\n-- Does ADX itself separate winners/losers at entry? (should be ~0.5 if not) --")
    w, l = split("adx_h1"); print(summarize("entry H1 ADX", w, l))

    print("\n-- Volume edge WITHIN entry-time H1 ADX bins (control for trend strength) --")
    for lo, hi, name in [(0, 20, "ADX<20"), (20, 30, "20<=ADX<30"), (30, 999, "ADX>=30")]:
        pred = lambda r, lo=lo, hi=hi: r["adx_h1"] is not None and lo <= r["adx_h1"] < hi
        w, l = split("vr_m5", pred)
        print(summarize(f"  M5 vol | {name}", w, l))

    print("\n-- Volume edge WITHIN entry_regime (persisted H1 label) --")
    regimes = sorted({r["e_reg"] for r in recs if r["e_reg"]})
    for rg in regimes:
        pred = lambda r, rg=rg: r["e_reg"] == rg
        w, l = split("vr_m5", pred)
        print(summarize(f"  M5 vol | {rg}", w, l))

    print("\n-- Profitability of the low-ADX cohort (disqualifies an ADX gate if profitable) --")
    for lo, hi, name in [(0, 20, "entry H1 ADX<20"), (20, 999, "entry H1 ADX>=20")]:
        cohort = [r for r in recs if r["adx_h1"] is not None and lo <= r["adx_h1"] < hi]
        if not cohort:
            continue
        n = len(cohort)
        wins = sum(r["win"] for r in cohort)
        net = sum(r["pnl"] for r in cohort)
        print(f"  {name}: n={n} win_rate={100*wins/n:.1f}% net_pnl=${net:+.2f} "
              f"avg_pnl=${net/n:+.2f}")


# --------------------------------------------------------------------------- #
# Logistic regression (pure numpy) for multivariate separability               #
# --------------------------------------------------------------------------- #
def _logreg_fit(X, y, iters=3000, lr=0.2, l2=1e-2):
    n, d = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(d + 1)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Xb @ w)))
        g = Xb.T @ (p - y) / n
        g[1:] += l2 * w[1:]
        w -= lr * g
    return w


def _logreg_proba(w, X):
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])
    return 1.0 / (1.0 + np.exp(-(Xb @ w)))


def logistic_cv_auc(X, y, k=5, seed=0):
    n = len(y)
    idx = np.arange(n)
    np.random.default_rng(seed).shuffle(idx)
    folds = np.array_split(idx, k)
    aucs = []
    for i in range(k):
        te = folds[i]
        tr = np.concatenate([folds[j] for j in range(k) if j != i])
        m = X[tr].mean(0); s = X[tr].std(0); s[s == 0] = 1.0
        w = _logreg_fit((X[tr] - m) / s, y[tr])
        p = _logreg_proba(w, (X[te] - m) / s)
        aucs.append(auc_mannwhitney(p[y[te] == 1], p[y[te] == 0]))
    return float(np.nanmean(aucs)), aucs


def winrate_by_quintile(vals, wins):
    v = np.asarray(vals, dtype=float)
    y = np.asarray(wins, dtype=float)
    ok = ~np.isnan(v)
    v, y = v[ok], y[ok]
    if len(v) < 25:
        return "insufficient"
    qs = np.quantile(v, [0.2, 0.4, 0.6, 0.8])
    out = []
    edges = [-np.inf, *qs, np.inf]
    for i in range(5):
        m = (v >= edges[i]) & (v < edges[i + 1])
        if m.sum() > 0:
            out.append(f"Q{i+1}={100*y[m].mean():.0f}%(n{m.sum()})")
    return " ".join(out)


# --------------------------------------------------------------------------- #
# Item 2: entry-quality separability (binary primary + magnitude secondary)    #
# --------------------------------------------------------------------------- #
def item2(c: sqlite3.Connection) -> None:
    print("=" * 78)
    print("ITEM 2: entry-quality separability (binary primary + magnitude secondary)")
    print("=" * 78)
    trades = load_trades(c)
    # Persisted entry features (timeframes: regime=H1 label; rsi/atr/macd=M5; score/conf=decision)
    feats = {"entry_rsi(M5)": 8, "entry_atr_pct(M5)": 9, "entry_macd_hist(M5)": 10,
             "entry_score": 11, "claude_confidence": 12}
    win = np.array([int(t[3]) for t in trades])
    pnl = np.array([float(t[4] or 0) for t in trades])
    print(f"\nDataset: {len(trades)} bybit_demo trades; win_rate={100*win.mean():.1f}%.")

    print("\n-- Univariate separability of each persisted entry feature (binary win/loss) --")
    print("   (indistinguishable = AUC in [0.45,0.55] and |Cliff delta|<0.15)")
    for name, col in feats.items():
        vals = np.array([t[col] if t[col] is not None else np.nan for t in trades], dtype=float)
        w = vals[(win == 1)]; l = vals[(win == 0)]
        print(summarize(name, w, l))
    # vote balance
    vb = np.array([(t[13] - t[14]) if (t[13] is not None and t[14] is not None) else np.nan
                   for t in trades], dtype=float)
    print(summarize("vote_balance(sup-opp)", vb[win == 1], vb[win == 0]))

    print("\n-- Win-rate by quintile (monotonic trend would indicate a usable signal) --")
    for name, col in [("entry_score", 11), ("claude_confidence", 12), ("entry_rsi(M5)", 8)]:
        vals = [t[col] for t in trades]
        print(f"  {name}: {winrate_by_quintile(vals, win)}")

    print("\n-- Multivariate logistic separability, 5-fold CV AUC (regime-controlled) --")
    # Model A: 3 always-present M5 entry features
    A_cols = [8, 9, 10]
    rowsA = [(t, [t[col] for col in A_cols]) for t in trades
             if all(t[col] is not None for col in A_cols)]
    if rowsA:
        XA = np.array([r[1] for r in rowsA], dtype=float)
        yA = np.array([int(r[0][3]) for r in rowsA])
        aucA, _ = logistic_cv_auc(XA, yA)
        print(f"  Model A [rsi,atr_pct,macd_hist] (M5): n={len(yA)} CV_AUC={aucA:.3f}")
        # regime-stratified
        for rg in sorted({r[0][7] for r in rowsA if r[0][7]}):
            sub = [r for r in rowsA if r[0][7] == rg]
            if len(sub) >= 60:
                Xs = np.array([r[1] for r in sub], dtype=float)
                ys = np.array([int(r[0][3]) for r in sub])
                if ys.min() != ys.max():
                    a, _ = logistic_cv_auc(Xs, ys)
                    print(f"    within {rg}: n={len(ys)} CV_AUC={a:.3f}")
    # Model B: add score + confidence
    B_cols = [8, 9, 10, 11, 12]
    rowsB = [(t, [t[col] for col in B_cols]) for t in trades
             if all(t[col] is not None for col in B_cols)]
    if rowsB:
        XB = np.array([r[1] for r in rowsB], dtype=float)
        yB = np.array([int(r[0][3]) for r in rowsB])
        aucB, _ = logistic_cv_auc(XB, yB)
        print(f"  Model B [+score,confidence]: n={len(yB)} CV_AUC={aucB:.3f}")

    print("\n-- Magnitude secondary: big-winners vs the rest (top-quartile winner PnL) --")
    win_pnls = pnl[win == 1]
    if len(win_pnls) > 0:
        thr = np.quantile(win_pnls, 0.75)
        big = (win == 1) & (pnl >= thr)
        rest = ~big
        print(f"   big-winner threshold = pnl>=${thr:.2f}; big-winners n={big.sum()} rest n={rest.sum()}")
        for name, col in feats.items():
            vals = np.array([t[col] if t[col] is not None else np.nan for t in trades], dtype=float)
            print(summarize(f"  {name} (big vs rest)", vals[big], vals[rest]))


def _parse_ts(s: str) -> datetime:
    s = s.strip().replace(" ", "T")
    if "+" in s:
        s = s.split("+")[0]
    if "." in s:
        s = s.split(".")[0]
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def atr_pct_before(c, symbol, tf, before: datetime, n=40, period=14):
    """Wilder NATR-14 (ATR as percent of last close) on the closed candles of
    timeframe tf strictly before `before`. tf='5' is M5, tf='60' is H1."""
    if tf == "5":
        bound = floor_m5(before)
    else:
        bound = before.replace(minute=0, second=0, microsecond=0)
    rows = c.execute(
        "SELECT high,low,close FROM klines WHERE symbol=? AND timeframe=? AND timestamp < ? "
        "ORDER BY timestamp DESC LIMIT ?",
        (symbol, tf, bound.strftime("%Y-%m-%dT%H:%M:%S"), n),
    ).fetchall()
    if len(rows) < period + 1:
        return None
    rows = rows[::-1]  # oldest..newest
    high = np.array([r[0] for r in rows], dtype=float)
    low = np.array([r[1] for r in rows], dtype=float)
    close = np.array([r[2] for r in rows], dtype=float)
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr = tr[:period].mean()
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
    if close[-1] <= 0:
        return None
    return float(atr / close[-1] * 100.0)


# --------------------------------------------------------------------------- #
# Item 3: H1-regime / M5-execution stop coherence                             #
# --------------------------------------------------------------------------- #
def item3(c: sqlite3.Connection) -> None:
    print("=" * 78)
    print("ITEM 3: stop-coherence — realized stop distance vs M5 ATR%% and H1 ATR%% at entry")
    print("=" * 78)
    rows = c.execute(
        """
        SELECT symbol, direction, entry_regime_at_open, entry_price, stop_loss_price,
               take_profit_price, max_hold_minutes, close_reason, actual_pnl_usd, opened_at,
               closed_at
        FROM trade_thesis
        WHERE exchange_mode='bybit_demo' AND status='closed' AND opened_at>='2026-05-25'
          AND entry_price>0 AND stop_loss_price>0
        ORDER BY opened_at
        """
    ).fetchall()
    recs = []
    for (sym, dirn, reg, ep, sl, tp, mhold, creason, pnl, opened, closed) in rows:
        try:
            entry = _parse_ts(opened)
        except Exception:
            continue
        stop_dist = abs(ep - sl) / ep * 100.0
        tp_dist = abs((tp or ep) - ep) / ep * 100.0 if tp else None
        m5 = atr_pct_before(c, sym, "5", entry)
        h1 = atr_pct_before(c, sym, "60", entry)
        recs.append(dict(sym=sym, dirn=dirn, reg=reg, ep=ep, sl=sl, tp=tp, mhold=mhold,
                         creason=creason, pnl=float(pnl or 0), stop_dist=stop_dist,
                         tp_dist=tp_dist, m5=m5, h1=h1, entry=entry, closed_raw=closed))
    n = len(recs)
    n_m5 = sum(1 for r in recs if r["m5"])
    n_h1 = sum(1 for r in recs if r["h1"])
    print(f"\n{n} bybit_demo trades opened since 2026-05-25 with SL set; M5 ATR on {n_m5}, H1 ATR on {n_h1}.")

    def agg(label, sub):
        sub = [r for r in sub if r["m5"] and r["h1"] and r["m5"] > 0 and r["h1"] > 0]
        if not sub:
            print(f"  {label}: no data")
            return
        sd = np.array([r["stop_dist"] for r in sub])
        m5 = np.array([r["m5"] for r in sub])
        h1 = np.array([r["h1"] for r in sub])
        rr = np.array([r["tp_dist"] / r["stop_dist"] for r in sub if r["tp_dist"]])
        frac_lt_1h1 = 100.0 * np.mean(sd < h1)
        print(f"  {label}: n={len(sub)} | stop_dist median={np.median(sd):.2f}% | "
              f"M5_ATR median={np.median(m5):.2f}% | H1_ATR median={np.median(h1):.2f}% | "
              f"stop/M5={np.median(sd/m5):.2f}x stop/H1={np.median(sd/h1):.2f}x | "
              f"RR median={np.median(rr):.2f} | stop<1xH1_ATR in {frac_lt_1h1:.0f}% of trades")

    print("\n-- All trades by outcome --")
    agg("winners (pnl>0)", [r for r in recs if r["pnl"] > 0])
    agg("losers  (pnl<=0)", [r for r in recs if r["pnl"] <= 0])
    print("\n-- Losers closed by stop-loss hit (bybit_sl_hit), by entry regime --")
    sl_losers = [r for r in recs if r["pnl"] < 0 and r["creason"] == "bybit_sl_hit"]
    agg("ALL sl-losers", sl_losers)
    for rg in ["trending_up", "trending_down", "volatile", "ranging"]:
        agg(f"  {rg}", [r for r in sl_losers if r["reg"] == rg])
    print("\n-- Named traces (INJ/NEAR/DYDX longs, stop-hit) --")
    for r in [r for r in recs if r["sym"] in ("INJUSDT", "NEARUSDT", "DYDXUSDT")
              and r["dirn"] == "Buy" and r["creason"] == "bybit_sl_hit" and r["m5"] and r["h1"]][:8]:
        print(f"  {r['sym']} {r['reg']} entry={r['ep']:.4f} SL={r['sl']:.4f} "
              f"stop_dist={r['stop_dist']:.2f}% M5_ATR={r['m5']:.2f}% H1_ATR={r['h1']:.2f}% "
              f"stop/M5={r['stop_dist']/r['m5']:.2f}x stop/H1={r['stop_dist']/r['h1']:.2f}x "
              f"hold={r['mhold']}m pnl=${r['pnl']:.2f}")

    # Counterfactual: after the stop fired, did price recover in the thesis direction
    # within the intended hold window? This is the decisive test — without it, a
    # wider stop would only lose more. Buy: look at subsequent M5 highs; Sell: lows.
    print("\n-- COUNTERFACTUAL recovery test for stop-loss losers (within intended hold window) --")
    def recovery(sub, label, horizon_min=None):
        considered = recov_entry = recov_tp = 0
        mfes = []
        for r in sub:
            if not r["closed_raw"] or not r["mhold"] or not r["tp"]:
                continue
            try:
                closed_dt = _parse_ts(r["closed_raw"])
            except Exception:
                continue
            win_min = max(int(r["mhold"]), horizon_min) if horizon_min else int(r["mhold"])
            hold_end = r["entry"] + timedelta(minutes=win_min)
            if hold_end <= closed_dt:
                continue
            kr = c.execute(
                "SELECT high, low FROM klines WHERE symbol=? AND timeframe='5' "
                "AND timestamp>=? AND timestamp<=? ORDER BY timestamp",
                (r["sym"], closed_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                 hold_end.strftime("%Y-%m-%dT%H:%M:%S")),
            ).fetchall()
            if not kr:
                continue
            considered += 1
            if r["dirn"] == "Buy":
                best = max(x[0] for x in kr)
                mfe = (best - r["ep"]) / r["ep"] * 100
                if best > r["ep"]:
                    recov_entry += 1
                if best >= r["tp"]:
                    recov_tp += 1
            else:
                best = min(x[1] for x in kr)
                mfe = (r["ep"] - best) / r["ep"] * 100
                if best < r["ep"]:
                    recov_entry += 1
                if best <= r["tp"]:
                    recov_tp += 1
            mfes.append(mfe)
        if considered == 0:
            print(f"  {label}: no post-stop klines available")
            return
        print(f"  {label}: n={considered} | recovered_above_entry={100*recov_entry/considered:.0f}% "
              f"| would_have_hit_TP={100*recov_tp/considered:.0f}% | "
              f"median_post_stop_favorable_excursion={np.median(mfes):.2f}%")
    recovery(sl_losers, "ALL sl-losers (within designed hold)")
    recovery([r for r in sl_losers if r["reg"] in ("trending_up", "trending_down")], "  trending sl-losers (designed hold)")
    recovery([r for r in sl_losers if r["h1"] and r["stop_dist"] < r["h1"]], "  sl-losers stop<1xH1_ATR (designed hold)")
    print("  -- extended 3-hour horizon (tests whether a longer hold would rescue the H1 thesis) --")
    recovery(sl_losers, "  ALL sl-losers (3h horizon)", horizon_min=180)
    recovery([r for r in sl_losers if r["reg"] in ("trending_up", "trending_down")], "  trending sl-losers (3h horizon)", horizon_min=180)


async def _simulate(c: sqlite3.Connection) -> None:
    """Reproduce each item's original problem situation and run it through the
    REAL project code (TAEngine, TradeGate, ClaudeStrategist helper, the real
    VolatilityProfiler classifier), showing before (fix off) vs after (fix on)
    and a per-item verdict. Uses controlled issue-reproducing data plus a real
    read-only kline sample. No writes, no DB copy."""
    from types import SimpleNamespace
    from src.config.settings import Settings
    from src.core.types import OHLCV, TimeFrame
    from src.analysis.engine import TAEngine
    from src.apex.gate import TradeGate
    from src.brain.strategist import ClaudeStrategist

    s = Settings.load("config.toml")
    print("=" * 78)
    print("SIMULATION: reproduce the issues, run the fixes through the real code")
    print("=" * 78)
    print(f"Live flags from config: closed_candle={s.ta.volume_ratio_use_closed_candle} "
          f"magnitude_advisory={s.brain.entry_magnitude_advisory_enabled}")

    # ---------------------------------------------------------------- #
    # SCENARIO A — Item 4 (FIXED): a real volume spike on the last      #
    # CLOSED bar, masked by a partially-formed newest bar.              #
    # ---------------------------------------------------------------- #
    print("\n[SCENARIO A] Item 4 — genuine volume spike on the last CLOSED M5 bar, "
          "newest bar only ~12% formed")
    base_t = datetime(2026, 5, 26, tzinfo=timezone.utc)
    vols = [1000.0] * 58 + [2800.0, 350.0]  # idx58 = last CLOSED spike; idx59 = forming partial
    candles = [OHLCV(symbol="SIMUSDT", timeframe=TimeFrame.M5,
                     timestamp=base_t + timedelta(minutes=5 * i),
                     open=100.0 + i * 0.05, high=100.6 + i * 0.05, low=99.4 + i * 0.05,
                     close=100.0 + i * 0.05, volume=float(v), turnover=0.0)
               for i, v in enumerate(vols)]
    eng = TAEngine(db=None, settings=s)
    s.ta.volume_ratio_use_closed_candle = False
    off = (await eng.analyze(candles=candles))["volume"]
    s.ta.volume_ratio_use_closed_candle = True
    on = (await eng.analyze(candles=candles))["volume"]
    print(f"  BEFORE (fix off): volume_sma_ratio={off['volume_sma_ratio']:.2f} "
          f"summary={off['volume_summary']}  <- the real spike is hidden")
    print(f"  AFTER  (fix on) : volume_sma_ratio={on['volume_sma_ratio']:.2f} "
          f"summary={on['volume_summary']}  <- the real spike is surfaced")
    a_ok = on["volume_summary"] == "SPIKE" and off["volume_summary"] in ("LOW", "BELOW_AVERAGE", "AVERAGE")
    # real-data cross-check
    krow = c.execute("SELECT symbol FROM klines WHERE timeframe='5' GROUP BY symbol "
                     "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    if krow:
        sym = krow[0]
        rows = c.execute("SELECT timestamp,open,high,low,close,volume,turnover FROM klines "
                         "WHERE symbol=? AND timeframe='5' ORDER BY timestamp DESC LIMIT 80",
                         (sym,)).fetchall()[::-1]
        rc = [OHLCV(symbol=sym, timeframe=TimeFrame.M5, timestamp=_parse_ts(r[0]),
                    open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5], turnover=r[6])
              for r in rows]
        s.ta.volume_ratio_use_closed_candle = False
        roff = (await eng.analyze(candles=rc))["volume"]["volume_sma_ratio"]
        s.ta.volume_ratio_use_closed_candle = True
        ron = (await eng.analyze(candles=rc))["volume"]["volume_sma_ratio"]
        print(f"  REAL DATA {sym} (last 80 M5 bars): fix off={roff:.3f}  fix on={ron:.3f} "
              f"(closed-bar value, no longer dragged by the partial forming bar)")
    print(f"  -> VERDICT: {'FIXED & WORKING' if a_ok else 'REVIEW'} — the fix makes a genuine "
          f"spike visible to the scorer/regime that the forming bar previously masked.")

    # ---------------------------------------------------------------- #
    # SCENARIO B — Item 2 (FIXED): magnitude advisory labels coins by   #
    # entry volatility (the feature that separated big-winners).        #
    # ---------------------------------------------------------------- #
    print("\n[SCENARIO B] Item 2 — magnitude advisory token per entry M5 volatility class")
    print("  (in production the class comes from the real VolatilityProfiler._classify(atr_pct_5m);")
    print("   high/extreme is the big-winner cohort that separated at AUC 0.633)")
    strat = ClaudeStrategist.__new__(ClaudeStrategist)
    strat.settings = s
    b_ok = True
    expect = {"extreme": "HIGH", "high": "HIGH", "medium": "MED", "low": "LOW", "dead": "LOW"}
    for cls, exp in expect.items():
        vp = SimpleNamespace(volatility_class=cls, atr_pct_5m=0.0,
                             recommended_tp_pct=0.0, recommended_sl_pct=0.0)
        s.brain.entry_magnitude_advisory_enabled = False
        off_tag = strat._magnitude_advisory_tag(vp)
        s.brain.entry_magnitude_advisory_enabled = True
        on_tag = strat._magnitude_advisory_tag(vp)
        cohort = ("big-winner potential" if exp == "HIGH"
                  else "small-winner likely" if exp == "LOW" else "mid")
        ok = off_tag == "" and f"MAG={exp}" in on_tag
        b_ok = b_ok and ok
        print(f"  class={cls:8s} OFF={off_tag!r:4s} ON={on_tag!r}  [{cohort}] {'OK' if ok else 'MISMATCH'}")
    print(f"  -> VERDICT: {'FIXED & WORKING' if b_ok else 'REVIEW'} — the brain now sees the "
          f"magnitude signal (advisory only; nothing when off, correct token per class when on).")

    # ---------------------------------------------------------------- #
    # SCENARIO C — Item 1 (DO-NOT-FIX): the profitable low-ADX volatile  #
    # trade must NOT be blocked; the real zero-conviction reject stays.  #
    # ---------------------------------------------------------------- #
    print("\n[SCENARIO C] Item 1 — gate must NOT block the profitable low-ADX volatile cohort")
    gate = TradeGate({}, s.apex)  # real system passes settings.apex (APEXSettings)
    t1 = {"symbol": "INJUSDT", "direction": "Buy", "size_usd": 500.0, "leverage": 5,
          "stop_loss_price": 5.30, "take_profit_price": 5.55,
          "_xray_confidence": 0.55, "_setup_score": 62.0, "_expected_rr": 2.1}
    r1 = await gate.validate(dict(t1))
    blocked1 = bool(r1.get("_gate_rejected"))
    t2 = {"symbol": "SOLUSDT", "direction": "Buy", "size_usd": 500.0, "leverage": 5,
          "stop_loss_price": 100.0, "take_profit_price": 105.0,
          "_xray_confidence": 0.0, "_setup_score": 0.0, "_expected_rr": 0.0}
    r2 = await gate.validate(dict(t2))
    blocked2 = bool(r2.get("_gate_rejected"))
    print(f"  low-ADX volatile trade (real conviction): blocked={blocked1} "
          f"reason={r1.get('_gate_rejected')}")
    print(f"  zero-conviction trade: blocked={blocked2} reason={r2.get('_gate_rejected')}")
    c_ok = (not blocked1) and blocked2
    print(f"  -> VERDICT: {'DO-NOT-FIX PRESERVED' if c_ok else 'REVIEW'} — profitable low-ADX "
          f"trade executes (no volume/ADX gate added), real zero-conviction reject still fires.")

    # ---------------------------------------------------------------- #
    # SCENARIO D — Item 3 (DO-NOT-FIX): stop derivation unchanged.       #
    # ---------------------------------------------------------------- #
    print("\n[SCENARIO D] Item 3 — stop derivation left unchanged (no H1-coherence widening added)")
    src_vp = open("src/analysis/volatility_profile.py").read()
    src_opt = open("src/apex/optimizer.py").read()
    # The do-not-fix marker: the H1-coherence stop-widening flag was NEVER added.
    # (atr_pct_1h is legitimately present-but-unused in _compute_params in the
    # ORIGINAL code — that is the documented finding, not a change.)
    no_widen = "stop_h1_coherence" not in src_vp and "stop_h1_coherence" not in src_opt
    # The existing APEX SL clamp is unchanged (still floors to M5-class recommended_sl_pct).
    clamp_intact = "trade.sl_pct = max(_sl_floor, min(trade.sl_pct, 5.0))" in src_opt
    print(f"  no H1-coherence stop-widening flag added: {no_widen}; existing M5 SL clamp intact: {clamp_intact}")
    no_widen = no_widen and clamp_intact
    print(f"  (counterfactual from Item 3 stands: widening stops would not have created winners "
          f"— 0% of stopped-out losers would have reached take-profit.)")
    print(f"  -> VERDICT: {'DO-NOT-FIX PRESERVED' if no_widen else 'REVIEW'} — stop sizing is "
          f"untouched; the harmful widening was correctly not applied.")

    print("\n" + "=" * 78)
    allok = a_ok and b_ok and c_ok and no_widen
    print(f"SIMULATION RESULT: {'ALL FOUR ITEMS RESPOND AS INTENDED' if allok else 'SEE REVIEW ITEMS ABOVE'}")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", default="1", choices=["1", "2", "3", "all", "sim"])
    args = ap.parse_args()
    c = connect()
    try:
        if args.item == "sim":
            asyncio.run(_simulate(c))
            return
        if args.item in ("1", "all"):
            item1(c)
        if args.item in ("2", "all"):
            item2(c)
        if args.item in ("3", "all"):
            item3(c)
    finally:
        c.close()


if __name__ == "__main__":
    main()
