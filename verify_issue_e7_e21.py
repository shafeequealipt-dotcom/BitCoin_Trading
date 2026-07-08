"""Self-verification for E7 + E21 — open-interest wiring + strategy reactivation.

These two COMPLETE the earlier #8 fix. Re-verification found #8's companion
work already wired the brain OI field and the three OI strategies read the
corrected 24h delta, so this batch is confirm + observability, not a re-fix.

Confirms:
  A. E7 STATIC: scanner_worker assigns the brain OI field from get_oi()'s
     corrected source; the brain renders it; the OI_BRAIN_WIRED sentinel exists.
  B. E21 STATIC: the three OI strategies read oi_change_24h_pct (the corrected
     source), and STRAT_L1_DONE already exposes their fire/non-fire distribution.
  C. LIVE-DB: the real 24h OI-delta distribution crosses all three strategy
     thresholds, proving they CAN fire on real moves (no longer dead weight).

Read-only. Live DB opened read-only (WAL-safe).
"""

import sqlite3
from datetime import datetime, timedelta


def static_check():
    sw = open("src/workers/scanner_worker.py").read()
    st = open("src/brain/strategist.py").read()
    wk = open("src/workers/strategy_worker.py").read()
    d2 = open("src/strategies/categories/d2_oi_divergence.py").read()
    f3 = open("src/strategies/categories/f3_liquidation_hunt.py").read()
    g3 = open("src/strategies/categories/g3_liquidation_frontrunner.py").read()
    return {
        "E7 scanner assigns brain OI from get_oi (corrected source)":
            "alt.oi_change_24h_pct = float(_oi.get(\"change_24h_pct\"" in sw,
        "E7 brain renders the OI field": "oi_change_24h_pct" in st and "OI_24h=" in st,
        "E7 OI_BRAIN_WIRED sentinel present": "OI_BRAIN_WIRED" in sw,
        "E21 three strategies read corrected oi_change_24h_pct":
            'oi_change_24h_pct' in d2 and 'oi_change_24h_pct' in f3 and 'oi_change_24h_pct' in g3,
        "E21 STRAT_L1_DONE exposes fire/non-fire distribution":
            "STRAT_L1_DONE" in wk and "top_firing" in wk and "non_firing" in wk,
    }


def live_db_check():
    con = sqlite3.connect("file:data/trading.db?mode=ro", uri=True, timeout=5)
    cur = con.cursor()
    rows = cur.execute("SELECT DISTINCT symbol FROM open_interest").fetchall()
    d2_ok = f3_ok = g3_ok = 0
    n = 0
    for (sym,) in rows:
        latest = cur.execute("SELECT open_interest_value, timestamp FROM open_interest "
                             "WHERE symbol=? ORDER BY datetime(timestamp) DESC LIMIT 1", (sym,)).fetchone()
        if not latest or not latest[0] or latest[0] <= 0:
            continue
        cutoff = (datetime.fromisoformat(latest[1].replace('Z', '')) - timedelta(hours=23)).isoformat()
        prior = cur.execute("SELECT open_interest_value FROM open_interest WHERE symbol=? AND "
                            "datetime(timestamp)<=datetime(?) ORDER BY datetime(timestamp) DESC LIMIT 1",
                            (sym, cutoff)).fetchone()
        if not prior or not prior[0] or prior[0] <= 0:
            continue
        d = (latest[0] - prior[0]) / prior[0] * 100.0
        n += 1
        if d < -2.0:
            d2_ok += 1          # D2 fires on OI falling > 2%
        if d > 5.0:
            f3_ok += 1          # F3 gate: OI rising >= 5%
        if d > 8.0:
            g3_ok += 1          # G3 gate: OI rising >= 8%
    con.close()
    return n, d2_ok, f3_ok, g3_ok


def main():
    s = static_check()
    n, d2, f3, g3 = live_db_check()
    print("E7 + E21 VERIFICATION — open-interest wiring + strategy reactivation (completes #8)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  LIVE-DB (24h OI delta over {n} symbols):")
    print(f"    D2 (oi<-2%) can fire on {d2} symbols: {d2 > 0}")
    print(f"    F3 (oi>5%)  can fire on {f3} symbols: {f3 > 0}")
    print(f"    G3 (oi>8%)  can fire on {g3} symbols: {g3 > 0}")
    ok = all(s.values()) and d2 > 0 and f3 > 0 and g3 > 0
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
