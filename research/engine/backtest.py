"""Core backtest runner: brackets (stop/target/max-hold) over OHLCV bars.

Signal contract (what every strategy in research/strategies/ must produce):
a DataFrame with one row per candidate trade:
    symbol       str
    signal_ts    UTC timestamp -- the LAST bar of data the decision used
    direction    +1 (long) or -1 (short)
    stop_pct     stop distance, percent of entry price, > 0
    target_pct   target distance, percent of entry price, > 0
    max_hold     max bars held (in units of the `interval` passed to run())

No-look-ahead is enforced structurally: the fill price is the OPEN of the bar
AFTER signal_ts (never signal_ts's own close or any later information), and
the engine asserts entry_ts > signal_ts for every trade -- a strategy that
accidentally uses same-bar data for both signal and fill raises immediately
rather than silently producing an optimistic backtest.

Same convention as scripts/research/replay_all_trades.py (which validated
against the live ledger): if a bar touches both the stop and the target, the
stop is assumed to trigger first. This is conservative, not exact.
"""
from __future__ import annotations

import pandas as pd

from research.engine import data as rdata


class LookaheadError(ValueError):
    """Raised when a strategy's signal_ts would require unseen data to fill."""


def run(signals: pd.DataFrame, interval: str = "1h") -> pd.DataFrame:
    """Simulate every signal row as an independent bracket trade.

    Returns one row per signal with the realized outcome appended:
        entry_ts, entry_price, exit_ts, exit_price, exit_reason
        (stop / target / time), bars_held, gross_pct (signed, already
        includes direction -- positive is a win, BEFORE costs).
    Signals whose symbol/interval has no candle data, or whose window runs
    off the end of available history, are dropped (reported via the
    `dropped` attribute on the returned DataFrame for transparency).
    """
    required = {"symbol", "signal_ts", "direction", "stop_pct", "target_pct", "max_hold"}
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

        pos = bars.index.searchsorted(row.signal_ts, side="right")  # first bar AFTER signal_ts
        if pos >= len(bars):
            dropped += 1
            continue
        entry_ts = bars.index[pos]
        if entry_ts <= row.signal_ts:
            raise LookaheadError(
                f"{sym} @ {row.signal_ts}: computed entry_ts {entry_ts} is not strictly "
                "after signal_ts -- check the strategy is not filling on same-bar data."
            )
        entry_price = float(bars.iloc[pos]["open"])
        window = bars.iloc[pos: pos + int(row.max_hold)]
        if window.empty:
            dropped += 1
            continue

        sign = 1 if row.direction > 0 else -1
        stop_level = entry_price * (1 - sign * row.stop_pct / 100)
        target_level = entry_price * (1 + sign * row.target_pct / 100)

        exit_ts = window.index[-1]
        exit_price = float(window.iloc[-1]["close"])
        exit_reason = "time"
        mfe = mae = 0.0  # max favorable / adverse excursion, TO THE EVENTUAL EXIT bar
        for ts, bar in window.iterrows():
            if sign > 0:
                mfe = max(mfe, (bar["high"] - entry_price) / entry_price * 100)
                mae = max(mae, (entry_price - bar["low"]) / entry_price * 100)
            else:
                mfe = max(mfe, (entry_price - bar["low"]) / entry_price * 100)
                mae = max(mae, (bar["high"] - entry_price) / entry_price * 100)
            hit_stop = (bar["low"] <= stop_level) if sign > 0 else (bar["high"] >= stop_level)
            hit_target = (bar["high"] >= target_level) if sign > 0 else (bar["low"] <= target_level)
            if hit_stop:  # conservative: stop assumed first on a same-bar double-touch
                exit_ts, exit_price, exit_reason = ts, stop_level, "stop"
                break
            if hit_target:
                exit_ts, exit_price, exit_reason = ts, target_level, "target"
                break

        gross_pct = (exit_price - entry_price) / entry_price * 100 * sign
        bars_held = int(window.index.get_loc(exit_ts)) + 1
        out_rows.append({
            "symbol": sym, "signal_ts": row.signal_ts, "direction": sign,
            "entry_ts": entry_ts, "entry_price": entry_price,
            "exit_ts": exit_ts, "exit_price": exit_price, "exit_reason": exit_reason,
            "bars_held": bars_held, "gross_pct": gross_pct,
            "mfe_pct": mfe, "mae_pct": mae,
        })

    result = pd.DataFrame(out_rows)
    result.attrs["dropped"] = dropped
    result.attrs["total_signals"] = len(signals)
    return result
