"""B1 follow-up (2026-09-28, approved focused round): IDENTICAL entry to
b1_trend_breakout.py (same Donchian 20-bar breakout, same 4h bars, same ATR
period, same universe) -- the ONLY change is the exit. The original capped
every winner at a fixed 3x-ATR target; classic trend-following instead lets
the position run until the trend itself reverses (a trailing stop), which
is the entire point of trading a breakout. This isolates whether a better
exit rescues an entry that already showed real (if not-yet-significant)
separation from noise in the first round.

Exit: initial stop = 1.5x ATR (unchanged from B1). Once price moves 1x ATR
in favor, the stop arms and trails 2x ATR behind the best price seen since
entry -- standard "chandelier exit" shape, never loosens. No fixed target;
runs to the trailing stop or the same 10-day max hold as B1.

Every number here (1.5x initial, 1.0x arm, 2.0x trail) was fixed before this
was run, matching B1's own choices where the concept carries over (initial
stop) and otherwise using round, standard multiples -- not searched.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "4h"
EXIT_STYLE = "trailing"
LOOKBACK = 20
ATR_PERIOD = 14
STOP_ATR_MULT = 1.5        # unchanged from b1_trend_breakout.py
TRAIL_ACTIVATE_ATR_MULT = 1.0
TRAIL_ATR_MULT = 2.0
MAX_HOLD_BARS = 60          # unchanged from b1_trend_breakout.py


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
        bars = rdata.klines(symbol, INTERVAL)
        if len(bars) < LOOKBACK + ATR_PERIOD + 5:
            continue
        prior = bars["close"].shift(1)
        upper = prior.rolling(LOOKBACK).max()
        lower = prior.rolling(LOOKBACK).min()
        atrp = _atr_pct(bars, ATR_PERIOD).shift(1)

        long_break = bars["close"] > upper
        short_break = bars["close"] < lower
        valid = atrp.notna() & upper.notna() & lower.notna()

        for ts, is_long, is_short, ok, atr_here in zip(
            bars.index, long_break, short_break, valid, atrp,
        ):
            if not ok or atr_here <= 0:
                continue
            if is_long or is_short:
                rows.append({
                    "symbol": symbol, "signal_ts": ts, "direction": 1 if is_long else -1,
                    "stop_pct": STOP_ATR_MULT * atr_here,
                    "trail_activate_pct": TRAIL_ACTIVATE_ATR_MULT * atr_here,
                    "trail_pct": TRAIL_ATR_MULT * atr_here,
                    "max_hold": MAX_HOLD_BARS,
                })
    return pd.DataFrame(rows)
