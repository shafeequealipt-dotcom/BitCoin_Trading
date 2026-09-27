"""B2 (TRADING_OVERHAUL_PLAN.md Phase B): cross-sectional momentum.

Different in kind from B1/B3/B4/B5: those pick direction from ONE coin's
own recent behaviour; this picks direction from how each coin's recent
return RANKS against every other coin in the universe at the same moment --
long the strongest performers, short the weakest, rebalanced periodically.
Market-neutral by construction (equal long/short exposure each rebalance),
which is the point: it removes "is BTC going up" as a confound entirely
(see finding F9 in the plan -- the live bot's long bias tracked overall
market direction, not genuine skill).

Exit is primarily TIME, not price -- standard for this style (the live
score is what matters, not an individual position's path). stop_pct and
target_pct are set deliberately WIDE (20%) so the engine's required bracket
fields are structurally present but essentially never bind at a 7-day hold
on daily bars; the rebalance period is the real exit.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata

INTERVAL = "1d"
LOOKBACK_DAYS = 7        # momentum formation window
REBALANCE_DAYS = 7       # hold period = rebalance cadence
TOP_FRACTION = 0.2       # long the top 20% by trailing return
BOTTOM_FRACTION = 0.2    # short the bottom 20%
MIN_UNIVERSE_PER_DATE = 10  # need enough breadth for the ranking to mean anything
WIDE_STOP_TARGET_PCT = 20.0


def generate_signals(universe: list[str]) -> pd.DataFrame:
    # Build one aligned DataFrame of trailing returns, indexed by calendar
    # date, columns = symbol -- the cross-sectional rank needs every
    # symbol's return AS OF THE SAME DATE, not each symbol's own private
    # timeline the way B1/B3/B4/B5 operate.
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
