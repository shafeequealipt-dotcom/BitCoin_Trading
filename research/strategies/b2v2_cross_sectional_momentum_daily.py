"""B2 follow-up (2026-09-28, approved focused round): IDENTICAL mechanism to
b2_cross_sectional_momentum.py (rank the universe by trailing return, long
the top fraction, short the bottom fraction) -- the ONLY change is the
formation/holding horizon: 1 day instead of 7. Crypto cross-sectional
momentum is documented in the literature to concentrate at 1-3 day
horizons, distinct from the 7-day/weekly convention borrowed from equities
research -- this is a standard, well-known alternative horizon, not a
parameter search.

This also directly addresses WHY the original failed the bar: weekly
rebalancing over a 2-year OOS fold gives few independent rebalance events
(~35), and every symbol within one rebalance shares the same market
moment, so the reported significance was starved of independent
observations even though the average return was genuinely positive.
Daily rebalancing gives ~7x more independent events for the same window --
a more honest test of whether the RANKING itself carries information, not
just a way to manufacture significance.

Top/bottom fraction (20%) and market-neutral construction are unchanged
from B2. Fixed before running, not adjusted afterward.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "1d"
LOOKBACK_DAYS = 1        # was 7 in b2_cross_sectional_momentum.py
REBALANCE_DAYS = 1       # was 7
TOP_FRACTION = 0.2
BOTTOM_FRACTION = 0.2
MIN_UNIVERSE_PER_DATE = 10
WIDE_STOP_TARGET_PCT = 20.0


def generate_signals(universe: list[str]) -> pd.DataFrame:
    returns_by_symbol = {}
    for symbol in universe:
        bars = rdata.klines(symbol, INTERVAL)
        if len(bars) < LOOKBACK_DAYS + REBALANCE_DAYS + 5:
            continue
        ret = bars["close"].pct_change(LOOKBACK_DAYS) * 100
        ret.index = ret.index.normalize()
        returns_by_symbol[symbol] = ret[~ret.index.duplicated(keep="last")]

    if len(returns_by_symbol) < MIN_UNIVERSE_PER_DATE:
        return pd.DataFrame()

    panel = pd.DataFrame(returns_by_symbol).sort_index()
    rebalance_dates = panel.index[::REBALANCE_DAYS]

    rows = []
    for date in rebalance_dates:
        cross = panel.loc[date].dropna()
        if len(cross) < MIN_UNIVERSE_PER_DATE:
            continue
        ranked = cross.sort_values()
        n = len(ranked)
        n_bottom = max(1, int(n * BOTTOM_FRACTION))
        n_top = max(1, int(n * TOP_FRACTION))
        shorts = ranked.index[:n_bottom]
        longs = ranked.index[-n_top:]
        for symbol in longs:
            rows.append({"symbol": symbol, "signal_ts": date, "direction": 1,
                         "stop_pct": WIDE_STOP_TARGET_PCT, "target_pct": WIDE_STOP_TARGET_PCT,
                         "max_hold": REBALANCE_DAYS})
        for symbol in shorts:
            rows.append({"symbol": symbol, "signal_ts": date, "direction": -1,
                         "stop_pct": WIDE_STOP_TARGET_PCT, "target_pct": WIDE_STOP_TARGET_PCT,
                         "max_hold": REBALANCE_DAYS})
    return pd.DataFrame(rows)
