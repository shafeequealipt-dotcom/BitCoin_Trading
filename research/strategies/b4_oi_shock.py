"""B4 (TRADING_OVERHAUL_PLAN.md Phase B): fade open-interest shocks.

Documented crypto-perp effect: a sharp OI increase alongside a sharp price
move often means new leveraged positioning piling INTO the move (not
smart-money accumulation) -- a setup for a liquidation-cascade reversal once
the move runs out of new buyers/sellers to squeeze. This tests fading the
move (short after an OI+price spike up, long after an OI+price spike down)
on 1h bars, using each symbol's own trailing OI-change distribution so a
"shock" is relative to that coin's normal OI churn.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "1h"
OI_CHANGE_WINDOW = 24        # bars -- 24h OI build-up window
OI_SHOCK_PCTL = 0.90         # top/bottom 10% of this symbol's own 24h-OI-change history
PRICE_MOVE_MIN_PCT = 2.0     # the OI build-up must coincide with a real price move
ATR_PERIOD = 14
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 3.0
MAX_HOLD_BARS = 24  # 24h -- a squeeze/reversal plays out fast or not at all


def _atr_pct(bars: pd.DataFrame, period: int) -> pd.Series:
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean() / bars["close"] * 100


def generate_signals(universe: list[str]) -> pd.DataFrame:
    rows = []
    for symbol in universe:
        oi = rdata.open_interest(symbol, "1h")
        bars = rdata.klines(symbol, INTERVAL)
        if len(oi) < 200 or len(bars) < ATR_PERIOD + OI_CHANGE_WINDOW + 10:
            continue

        oi_change = oi["oi"].pct_change(OI_CHANGE_WINDOW) * 100
        hi_thr = oi_change.rolling(500, min_periods=150).quantile(OI_SHOCK_PCTL).shift(1)
        lo_thr = oi_change.rolling(500, min_periods=150).quantile(1 - OI_SHOCK_PCTL).shift(1)

        atrp = _atr_pct(bars, ATR_PERIOD)
        price_move = bars["close"].pct_change(OI_CHANGE_WINDOW) * 100

        # Align OI (its own timestamps) to the nearest bar at-or-before it,
        # matching B3's alignment convention -- no look-ahead.
        for ts, oic, hi, lo in zip(oi.index, oi_change, hi_thr, lo_thr):
            if pd.isna(hi) or pd.isna(lo):
                continue
            pos = bars.index.searchsorted(ts, side="right") - 1
            if pos < OI_CHANGE_WINDOW or pos >= len(bars):
                continue
            move = price_move.iloc[pos]
            atr_here = atrp.iloc[pos]
            if pd.isna(move) or pd.isna(atr_here) or atr_here <= 0:
                continue
            shock_up = oic >= hi and move >= PRICE_MOVE_MIN_PCT
            shock_down = oic <= lo and move <= -PRICE_MOVE_MIN_PCT
            if not (shock_up or shock_down):
                continue
            direction = -1 if shock_up else 1  # fade the spike
            rows.append({"symbol": symbol, "signal_ts": bars.index[pos], "direction": direction,
                         "stop_pct": STOP_ATR_MULT * atr_here, "target_pct": TARGET_ATR_MULT * atr_here,
                         "max_hold": MAX_HOLD_BARS})
    return pd.DataFrame(rows)
