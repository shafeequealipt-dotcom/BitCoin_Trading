"""Trailing-stop bracket simulator -- the "let winners run" exit style,
added specifically for the B1 follow-up (2026-09-28): the original B1 used a
fixed target (3x ATR), capping every winner at the same R-multiple. Classic
trend-following systems (Turtle/Donchian-style) instead let the position run
until the trend itself reverses, which is the entire point of trading a
breakout in the first place -- this module is that standard alternative,
not a re-tuned version of the fixed-bracket backtest.py.

Signal contract adds two fields to backtest.py's:
    trail_activate_pct   favorable move (percent of entry) required before
                          the trailing stop arms. Before that point only the
                          INITIAL stop_pct protects the position -- this
                          matches every production trailing-stop convention
                          in this codebase (see src/analysis/vol_scale.py)
                          and prevents ordinary noise from stopping out a
                          position before it has even confirmed the entry.
    trail_pct            distance the stop trails behind the best price
                          seen since entry, once armed. The stop can only
                          MOVE IN THE FAVORABLE DIRECTION once armed -- it
                          never loosens back toward entry.
No target_pct / max target -- the position runs to the trailing stop or
max_hold, whichever comes first. Same no-look-ahead contract as
backtest.py: fill is the OPEN of the bar after signal_ts; entry_ts <=
signal_ts raises.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata
from research.engine.backtest import LookaheadError


def run(signals: pd.DataFrame, interval: str = "1h") -> pd.DataFrame:
    required = {"symbol", "signal_ts", "direction", "stop_pct", "trail_activate_pct", "trail_pct", "max_hold"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"signals missing required columns: {missing}")

    out_rows = []
    dropped = 0
    by_symbol: dict[str, pd.DataFrame] = {}

    for row in signals.itertuples(index=False):
        sym = row.symbol
        if sym not in by_symbol:
            by_symbol[sym] = rdata.klines(sym, interval)
        bars = by_symbol[sym]
        if bars.empty:
            dropped += 1
            continue

        pos = bars.index.searchsorted(row.signal_ts, side="right")
        if pos >= len(bars):
            dropped += 1
            continue
        entry_ts = bars.index[pos]
        if entry_ts <= row.signal_ts:
            raise LookaheadError(f"{sym} @ {row.signal_ts}: entry_ts {entry_ts} not strictly after signal_ts")
        entry_price = float(bars.iloc[pos]["open"])
        window = bars.iloc[pos: pos + int(row.max_hold)]
        if window.empty:
            dropped += 1
            continue

        sign = 1 if row.direction > 0 else -1
        stop_level = entry_price * (1 - sign * row.stop_pct / 100)
        armed = False
        extreme = entry_price  # best price seen so far, in the favorable direction
        mfe = mae = 0.0

        exit_ts = window.index[-1]
        exit_price = float(window.iloc[-1]["close"])
        exit_reason = "time"
        for ts, bar in window.iterrows():
            fav_excursion = ((bar["high"] - entry_price) if sign > 0 else (entry_price - bar["low"])) / entry_price * 100
            adv_excursion = ((entry_price - bar["low"]) if sign > 0 else (bar["high"] - entry_price)) / entry_price * 100
            mfe = max(mfe, fav_excursion)
            mae = max(mae, adv_excursion)

            hit_stop = (bar["low"] <= stop_level) if sign > 0 else (bar["high"] >= stop_level)
            if hit_stop:
                exit_ts, exit_price, exit_reason = ts, stop_level, "trail_stop" if armed else "initial_stop"
                break

            # Update the running extreme, then re-check/ratchet the trail.
            bar_extreme = bar["high"] if sign > 0 else bar["low"]
            if sign > 0:
                extreme = max(extreme, bar_extreme)
            else:
                extreme = min(extreme, bar_extreme)
            favorable_pct = (extreme - entry_price) / entry_price * 100 * sign
            if favorable_pct >= row.trail_activate_pct:
                armed = True
                candidate_stop = extreme * (1 - sign * row.trail_pct / 100)
                # Only ever tighten toward the favorable side, never loosen.
                if sign > 0:
                    stop_level = max(stop_level, candidate_stop)
                else:
                    stop_level = min(stop_level, candidate_stop)

        gross_pct = (exit_price - entry_price) / entry_price * 100 * sign
        bars_held = int(window.index.get_loc(exit_ts)) + 1
        out_rows.append({
            "symbol": sym, "signal_ts": row.signal_ts, "direction": sign,
            "entry_ts": entry_ts, "entry_price": entry_price,
            "exit_ts": exit_ts, "exit_price": exit_price, "exit_reason": exit_reason,
            "bars_held": bars_held, "gross_pct": gross_pct,
            "mfe_pct": mfe, "mae_pct": mae, "armed": armed,
        })

    result = pd.DataFrame(out_rows)
    result.attrs["dropped"] = dropped
    result.attrs["total_signals"] = len(signals)
    return result
