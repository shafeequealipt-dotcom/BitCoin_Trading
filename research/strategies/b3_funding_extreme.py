"""B3 (TRADING_OVERHAUL_PLAN.md Phase B): fade extreme funding rates.

Documented crypto-perp effect: when funding is very positive, longs are
paying shorts heavily -- positioning is crowded long, and crowded
positioning tends to mean-revert. Symmetric on the short side. This tests
the FADE direction (short when funding is extreme positive, long when
extreme negative) on 1h bars, using each symbol's OWN trailing distribution
so the threshold adapts per-coin rather than using one flat rate for every
coin (a 0.03%/8h funding rate is "extreme" for a large-cap, routine for a
small one).

Parameters fixed before running: 90-day trailing window per symbol,
signal at the 95th/5th percentile of that symbol's own funding history,
volatility-scaled stop/target identical in form to B1 (so any pass/fail
difference between strategies isn't just "which one got the nicer exit").
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "1h"
FUNDING_LOOKBACK_DAYS = 90
PCTL_HI = 0.95
PCTL_LO = 0.05
ATR_PERIOD = 14
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 3.0
MAX_HOLD_BARS = 48  # 48h -- funding resets every 8h, this spans several resets


def _atr_pct(bars: pd.DataFrame, period: int) -> pd.Series:
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return (tr.rolling(period).mean() / bars["close"] * 100)


def generate_signals(universe: list[str]) -> pd.DataFrame:
    rows = []
    for symbol in universe:
        fr = rdata.funding(symbol)
        if len(fr) < 100:
            continue
        bars = rdata.klines(symbol, INTERVAL)
        if len(bars) < ATR_PERIOD + 10:
            continue
        atrp = _atr_pct(bars, ATR_PERIOD)

        # Per-funding-event trailing percentile of THIS symbol's own rate
        # history (funding posts every 8h -> ~270 events in 90 days).
        window = FUNDING_LOOKBACK_DAYS * 3
        roll_hi = fr["rate"].rolling(window, min_periods=window // 3).quantile(PCTL_HI).shift(1)
        roll_lo = fr["rate"].rolling(window, min_periods=window // 3).quantile(PCTL_LO).shift(1)
        extreme_hi = fr["rate"] >= roll_hi
        extreme_lo = fr["rate"] <= roll_lo

        for ts, rate, hi, lo, ok_hi, ok_lo in zip(
            fr.index, fr["rate"], roll_hi, roll_lo, extreme_hi, extreme_lo,
        ):
            if not (ok_hi or ok_lo) or pd.isna(hi):
                continue
            # ATR at (or just before) the funding timestamp -- 1h bars, so
            # searchsorted lands on the bar at-or-before ts; using a value
            # STRICTLY before ts avoids leaking the funding-triggering bar's
            # own volatility into its own signal.
            pos = bars.index.searchsorted(ts, side="left") - 1
            if pos < 0 or pos >= len(atrp) or pd.isna(atrp.iloc[pos]) or atrp.iloc[pos] <= 0:
                continue
            atr_here = atrp.iloc[pos]
            direction = -1 if ok_hi else 1  # fade: short crowded-long, long crowded-short
            rows.append({"symbol": symbol, "signal_ts": ts, "direction": direction,
                         "stop_pct": STOP_ATR_MULT * atr_here, "target_pct": TARGET_ATR_MULT * atr_here,
                         "max_hold": MAX_HOLD_BARS})
    return pd.DataFrame(rows)
