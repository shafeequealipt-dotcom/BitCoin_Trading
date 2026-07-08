"""Five-Fix Follow-Up — Fix 3 offline validation (2026-06-10).

Replays the REAL losing-window stop-hit trades (2026-06-10 09:30-14:00 UTC,
the captured losing window: 74 stop-hit closes, net -451 USD) through the
LIVE volatility-scaled-stop code path (compute_volatility_scaled_stop, the
exact function the flag enables) and walks the recorded 5-minute klines
forward from each entry.

Per trade, two arms:
  baseline arm  — the constant 1.5 percent entry stop (today's behaviour);
  scaled arm    — the Fix-7 stop: the coin's volatility-recommended distance
                  (NATR-14 on the 100 5-minute candles BEFORE entry, the same
                  classifier ladder the live profiler uses), floored at 1.5,
                  capped at 5.0, with the proportional size haircut so the
                  dollar risk AT the stop stays within the same budget.

Pass conditions (Part D of the program):
  1. Among the trades whose baseline stop is hit inside the first 30 minutes
     (the noise-deaths), the MAJORITY survive those minutes under the scaled
     stop.
  2. On every trade, the scaled arm's dollar risk at the stop is at or under
     the baseline reference budget (the helper's haircut guarantee, asserted
     trade by trade).

Honest limitations, stated plainly: the per-trade regime multiplier the live
profiler applies (0.7 to 1.2 on the stop) is NOT reproducible offline because
the regime at entry time was not recorded per trade; this replay holds it at
1.0 (the neutral middle). Survival here means the initial adverse excursion
no longer ends the trade — whether a survivor then wins depends on the exit
engines and is measured live, not claimed here. Read-only: never rewrites data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.workers.strategy_worker import compute_volatility_scaled_stop

DB = "data/trading.db"
WINDOW_START = "2026-06-10 09:30:00"
WINDOW_END = "2026-06-10 14:00:00"
REF_PCT = 1.5
CAP_PCT = 5.0
SURVIVAL_MINUTES = 30
NATR_PERIOD = 14
PRE_BARS = 100

# The live profiler's classifier ladder and base stop per class
# (volatility_profile.py _BASE_PARAMS / _classify, thresholds from
# VolatilityProfileSettings defaults — re-read from Settings at runtime below
# so a config change is honoured).
_BASE_SL = {"dead": 0.20, "low": 0.35, "medium": 1.00, "high": 2.00, "extreme": 3.00}


def _classify(atr_pct: float, s) -> str:
    if atr_pct < s.dead_threshold:
        return "dead"
    if atr_pct < s.low_threshold:
        return "low"
    if atr_pct < s.medium_threshold:
        return "medium"
    if atr_pct < s.high_threshold:
        return "high"
    return "extreme"


def _natr14(bars: list[tuple[float, float, float]]) -> float:
    """Wilder NATR-14 in percent from (high, low, close) bars, oldest first."""
    if len(bars) < NATR_PERIOD + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, lo, c = bars[i]
        pc = bars[i - 1][2]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr = sum(trs[:NATR_PERIOD]) / NATR_PERIOD
    for tr in trs[NATR_PERIOD:]:
        atr = (atr * (NATR_PERIOD - 1) + tr) / NATR_PERIOD
    last_close = bars[-1][2]
    return (atr / last_close) * 100.0 if last_close > 0 else 0.0


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main() -> None:
    settings = Settings.load()
    vp = settings.volatility_profile
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    trades = con.execute(
        "SELECT symbol, direction, entry_price, size_usd, leverage, opened_at, "
        "pnl_usd, hold_minutes FROM trade_log "
        "WHERE datetime(created_at) BETWEEN datetime(?) AND datetime(?) "
        "AND close_reason IN ('bybit_sl_hit','bybit_demo_sl_tp') "
        "AND pnl_usd < 0 AND entry_price > 0 ORDER BY opened_at",
        (WINDOW_START, WINDOW_END),
    ).fetchall()
    print(f"Losing-window stop-hit trades replayed: {len(trades)}")

    replayed = 0
    skipped = 0
    baseline_hit_30 = 0
    scaled_survives = 0
    both_hit = 0
    risk_violations = 0
    widened = 0
    details: list[str] = []

    for t in trades:
        sym = t["symbol"]
        side = (t["direction"] or "").strip().lower()
        entry = float(t["entry_price"])
        size = float(t["size_usd"] or 100.0)
        opened = _parse_ts(t["opened_at"])

        pre_rows = con.execute(
            "SELECT high, low, close FROM klines WHERE symbol=? AND timeframe='5' "
            "AND datetime(timestamp) < datetime(?) "
            "ORDER BY datetime(timestamp) DESC LIMIT ?",
            (sym, opened.isoformat(sep=" ").split("+")[0], PRE_BARS),
        ).fetchall()
        post_rows = con.execute(
            "SELECT high, low, close FROM klines WHERE symbol=? AND timeframe='5' "
            "AND datetime(timestamp) >= datetime(?) "
            "ORDER BY datetime(timestamp) ASC LIMIT ?",
            (
                sym,
                opened.isoformat(sep=" ").split("+")[0],
                SURVIVAL_MINUTES // 5,
            ),
        ).fetchall()
        if len(pre_rows) < NATR_PERIOD + 1 or not post_rows:
            skipped += 1
            continue

        pre = [(float(r["high"]), float(r["low"]), float(r["close"])) for r in reversed(pre_rows)]
        natr = _natr14(pre)
        vol_class = _classify(natr, vp)
        rec_sl = max(min(_BASE_SL[vol_class], vp.max_sl_pct), vp.min_sl_pct)

        is_buy = side in ("buy", "long")
        baseline_sl = entry * (1 - REF_PCT / 100.0) if is_buy else entry * (1 + REF_PCT / 100.0)

        # The LIVE code path: same helper the enabled flag invokes at entry.
        new_sl, new_size, target_pct, final_pct = compute_volatility_scaled_stop(
            sl=baseline_sl,
            current_price=entry,
            direction="Buy" if is_buy else "Sell",
            size_usd=size,
            recommended_sl_pct=rec_sl,
            reference_stop_pct=REF_PCT,
            max_cap_pct=CAP_PCT,
        )

        # Dollar-risk guarantee: scaled risk at stop <= reference budget.
        ref_risk = size * REF_PCT / 100.0
        scaled_risk = new_size * final_pct / 100.0
        if scaled_risk > ref_risk * 1.0001:  # float tolerance
            risk_violations += 1
        if final_pct > REF_PCT + 1e-9:
            widened += 1

        def _hit(stop_price: float) -> bool:
            for r in post_rows:
                if is_buy and float(r["low"]) <= stop_price:
                    return True
                if not is_buy and float(r["high"]) >= stop_price:
                    return True
            return False

        replayed += 1
        b_hit = _hit(baseline_sl)
        s_hit = _hit(new_sl)
        if b_hit:
            baseline_hit_30 += 1
            if not s_hit:
                scaled_survives += 1
                details.append(
                    f"  SURVIVES: {sym} {side} entry={entry} class={vol_class} "
                    f"natr={natr:.2f}% stop {REF_PCT:.1f}%->{final_pct:.2f}% "
                    f"size {size:.0f}->{new_size:.1f} (risk {scaled_risk:.2f}<={ref_risk:.2f})"
                )
            else:
                both_hit += 1

    con.close()

    print(f"Replayed with full kline coverage: {replayed} (skipped for data: {skipped})")
    print(f"Baseline 1.5% stop hit within first {SURVIVAL_MINUTES} minutes: {baseline_hit_30}")
    print(f"  of those, the volatility-scaled stop SURVIVES: {scaled_survives}")
    print(f"  of those, even the scaled stop is hit (genuine adverse): {both_hit}")
    print(f"Trades where the scaled stop widened beyond 1.5%: {widened}")
    print(f"Dollar-risk budget violations (must be 0): {risk_violations}")
    for d in details[:12]:
        print(d)
    if len(details) > 12:
        print(f"  ... and {len(details) - 12} more survivors")

    assert risk_violations == 0, "scaled arm exceeded the reference dollar budget"
    assert baseline_hit_30 > 0, "no baseline noise-deaths found — window query wrong?"
    survival_rate = scaled_survives / baseline_hit_30
    print(f"\nNoise-death survival rate under the scaled stop: {survival_rate:.0%}")
    assert survival_rate >= 0.5, (
        f"PASS CONDITION FAILED: only {survival_rate:.0%} of noise-deaths survive "
        f"(need a majority)"
    )
    print("PASS: the majority of noise-deaths survive their first minutes at the "
          "same dollar risk; genuine adverse moves still stop within budget.")


if __name__ == "__main__":
    main()
