"""B1 (TRADING_OVERHAUL_PLAN.md Phase B): Donchian-channel trend/breakout on
4-hour bars. One of the best-documented, least data-mined effects in trend
following -- chosen for exactly that reason, not because it was tuned to
pass here. Parameters below are a single, standard choice, picked BEFORE
running this and never adjusted afterward; per the plan, a strategy that
fails is discarded, not re-tuned until it passes.

Rule: go long when the current bar's close breaks above the highest close of
the PRIOR `lookback` bars (excluding the current bar -- no look-ahead); short
on a break below the lowest close of the same window. Stop and target are
set from the symbol's own recent volatility (ATR%), not a flat percentage,
so the same rule adapts to a calm vs a wild coin.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "4h"
LOOKBACK = 20        # bars -- 20 x 4h = ~3.3 days, a standard breakout window
ATR_PERIOD = 14
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 3.0   # 2:1 reward:risk
MAX_HOLD_BARS = 60      # 60 x 4h = 10 days -- room for a trend to develop


def _atr_pct(bars: pd.DataFrame, period: int) -> pd.Series:
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return atr / bars["close"] * 100


def generate_signals(universe: list[str]) -> pd.DataFrame:
    rows = []
    for symbol in universe:
        bars = rdata.klines(symbol, INTERVAL)
        if len(bars) < LOOKBACK + ATR_PERIOD + 5:
            continue
        # PRIOR window only -- shift(1) before rolling so today's own bar
        # never contributes to its own breakout level.
        prior = bars["close"].shift(1)
        upper = prior.rolling(LOOKBACK).max()
        lower = prior.rolling(LOOKBACK).min()
        atrp = _atr_pct(bars, ATR_PERIOD).shift(1)  # yesterday's ATR, not today's (no look-ahead)

        long_break = bars["close"] > upper
        short_break = bars["close"] < lower
        valid = atrp.notna() & upper.notna() & lower.notna()

        for ts, is_long, is_short, ok, atr_here in zip(
            bars.index, long_break, short_break, valid, atrp,
        ):
            if not ok or atr_here <= 0:
                continue
            if is_long:
                rows.append({"symbol": symbol, "signal_ts": ts, "direction": 1,
                             "stop_pct": STOP_ATR_MULT * atr_here,
                             "target_pct": TARGET_ATR_MULT * atr_here,
                             "max_hold": MAX_HOLD_BARS})
            elif is_short:
                rows.append({"symbol": symbol, "signal_ts": ts, "direction": -1,
                             "stop_pct": STOP_ATR_MULT * atr_here,
                             "target_pct": TARGET_ATR_MULT * atr_here,
                             "max_hold": MAX_HOLD_BARS})
    return pd.DataFrame(rows)
