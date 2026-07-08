"""Self-verification for Issue #8 — OI 1h delta mislabeled as 24h.

Offline check against CURRENT code + live DB (read-only). Two parts:

  A. STATIC: the OI fetch now sources its delta from the DB repo (the SAME
     ~24h delta the signal generator uses) instead of computing a 1h delta; the
     brain field is renamed honestly (oi_change_24h_pct) and populated by the
     scanner; the strategist renders OI_24h; and the signal-generator path is
     untouched (still reads get_latest_open_interest).
  B. REAL DB DELTA: on live data the new value is a genuine ~24h DB delta
     (computed against a >=23h-old snapshot) that DIFFERS from the old 1h
     delta, so the OI-gated strategies' multi-percent thresholds can now fire.
     The 24h-vs-1h magnitude is printed as evidence but is NOT a pass gate:
     24h can be smaller than a 1h blip when OI round-trips, so gating on it
     gave flaky false FAILs while the wiring was correct.

Read-only; no writes.
"""
import sqlite3

DB = "file:/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db?mode=ro"


def static_check():
    oi = open("src/intelligence/altdata/open_interest.py").read()
    cp = open("src/core/coin_package.py").read()
    sc = open("src/workers/scanner_worker.py").read()
    st = open("src/brain/strategist.py").read()
    sg = open("src/intelligence/signals/signal_generator.py").read()
    return {
        "fetch sources delta from repo": "_latest = await self._repo.get_latest_open_interest(symbol)" in oi
        and '"change_24h_pct": change_24h,' in oi,
        "fetch no longer computes 1h delta": "change_pct = safe_divide" not in oi,
        "brain field renamed honestly": "oi_change_24h_pct: float = 0.0" in cp
        and "oi_change_4h_pct: float" not in cp,
        "scanner populates brain OI field": "alt.oi_change_24h_pct = float(_oi.get(" in sc,
        "strategist renders OI_24h": "OI_24h={pkg.alt_data.oi_change_24h_pct" in st,
        "signal-generator path untouched": "get_latest_open_interest" in sg
        and 'oi.get("change_24h_pct"' in sg,
    }


def magnitude_check():
    c = sqlite3.connect(DB, uri=True)

    def oi_le(sym, expr):
        r = c.execute(
            "SELECT open_interest_value FROM open_interest WHERE symbol=? "
            "AND timestamp<=datetime('now',?) ORDER BY timestamp DESC LIMIT 1",
            (sym, expr),
        ).fetchone()
        return r[0] if r else None

    def latest(sym):
        r = c.execute(
            "SELECT open_interest_value FROM open_interest WHERE symbol=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (sym,),
        ).fetchone()
        return r[0] if r else None

    rows = []
    bigger = 0
    total = 0
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "BNBUSDT"]:
        cur, p1, p23 = latest(sym), oi_le(sym, "-1 hours"), oi_le(sym, "-23 hours")
        if cur and p1 and p23 and p1 > 0 and p23 > 0:
            old = (cur - p1) / p1 * 100.0
            new = (cur - p23) / p23 * 100.0
            rows.append((sym, old, new))
            total += 1
            if abs(new) > abs(old):
                bigger += 1
    return rows, bigger, total


def main():
    s = static_check()
    rows, bigger, total = magnitude_check()
    print("ISSUE #8 VERIFICATION — OI mislabel")
    print("  STATIC (DB-sourced delta + honest field + signal path intact):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  REAL MAGNITUDE (live DB): OLD 1h vs NEW ~24h OI delta")
    for sym, old, new in rows:
        print(f"    {sym}: OLD 1h={old:+.3f}%  NEW ~24h={new:+.3f}%")
    print(f"    NEW magnitude larger on {bigger}/{total} sampled coins "
          f"(informational — 24h-vs-1h magnitude is data-dependent)")
    # Gate on the ACTUAL #8 invariant, not a data-dependent magnitude race:
    #   (1) the static wiring is correct, AND
    #   (2) for >=3 coins the 24h delta is a REAL DB-derived value (every row
    #       above required a >=23h-old snapshot, so `new` is a genuine 24h
    #       delta, not the 0.0 cold-start fallback) that DIFFERS from the old
    #       1h delta — proving the source window genuinely changed, not just
    #       the label.
    # |24h| > |1h| is deliberately NOT required: when 24h OI round-trips it can
    # be smaller than a recent 1h blip (e.g. SOL/XRP on a flat day), which made
    # the old `bigger >= 5/6` gate emit flaky false FAILs while the wiring was
    # perfectly correct.
    distinct = sum(1 for _, old, new in rows if abs(new - old) > 1e-6)
    ok = all(s.values()) and total >= 3 and distinct >= total - 1
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
