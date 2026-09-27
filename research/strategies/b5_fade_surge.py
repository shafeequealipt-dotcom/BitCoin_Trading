"""B5 (TRADING_OVERHAUL_PLAN.md Phase B): fade the exact surge the LIVE bot's
own universe selector chases.

This is the direct test of finding F3 in TRADING_OVERHAUL_PLAN.md: the live
bot's universe_selector.py ranks coins by recent volatility + volume surge
(src/strategies/universe_selector.py `compute_multiday_score`), so the bot
systematically arrives at a coin just AFTER it has already spiked. F3 found
this is statistically weak evidence (a research lead, not a proven edge) --
this strategy is how that lead gets a real, numbered answer instead of
staying a hunch.

Signal: reproduce the SAME shape of condition the live selector rewards
(volume now vs its own trailing baseline, plus a real price range expansion)
on 1h bars, then take the OPPOSITE side of the immediately-preceding move
(mean-reversion), on the theory that a spike this selector just rewarded is
often already exhausted, not just starting.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "1h"
VOL_SURGE_WINDOW = 6          # bars -- matches the "recent" window universe_selector uses (recent_n<=2 of a longer baseline; widened here for hourly granularity)
VOL_BASELINE_WINDOW = 48      # 2 days baseline
VOLUME_SURGE_MIN = 2.0        # recent turnover >= 2x its own 2-day baseline
RANGE_EXPANSION_MIN_PCT = 3.0  # the surge window's own high-low range, percent of price
ATR_PERIOD = 14
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 2.0   # deliberately tighter than B1/B3/B4 -- this is a fast mean-reversion play, not a trend ride
MAX_HOLD_BARS = 12      # 12h -- reversion, if it happens, happens fast


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
        if len(bars) < VOL_BASELINE_WINDOW + ATR_PERIOD + 10:
            continue

        recent_turnover = bars["turnover"].rolling(VOL_SURGE_WINDOW).mean()
        baseline_turnover = bars["turnover"].rolling(VOL_BASELINE_WINDOW).mean().shift(VOL_SURGE_WINDOW)
        volume_ratio = recent_turnover / baseline_turnover

        window_high = bars["high"].rolling(VOL_SURGE_WINDOW).max()
        window_low = bars["low"].rolling(VOL_SURGE_WINDOW).min()
        range_pct = (window_high - window_low) / bars["close"] * 100
        move_over_window = bars["close"].pct_change(VOL_SURGE_WINDOW) * 100  # sign of the surge

        atrp = _atr_pct(bars, ATR_PERIOD)

        is_surge = (volume_ratio >= VOLUME_SURGE_MIN) & (range_pct >= RANGE_EXPANSION_MIN_PCT)

        for ts, surge, mv, atr_here in zip(bars.index, is_surge, move_over_window, atrp):
            if not surge or pd.isna(mv) or pd.isna(atr_here) or atr_here <= 0 or mv == 0:
                continue
            direction = -1 if mv > 0 else 1  # fade: short after a surge UP, long after a surge DOWN
            rows.append({"symbol": symbol, "signal_ts": ts, "direction": direction,
                         "stop_pct": STOP_ATR_MULT * atr_here, "target_pct": TARGET_ATR_MULT * atr_here,
                         "max_hold": MAX_HOLD_BARS})
    return pd.DataFrame(rows)
