"""Time-ordered fold splitting for walk-forward validation.

Folds are contiguous, chronological, non-overlapping thirds of the signal
set BY TIME (not by row count) -- a strategy tuned on fold 1 and judged on
fold 2, or tuned on 1+2 and judged on 3, never sees its own test period.
"""
from __future__ import annotations

import pandas as pd


def time_folds(signals: pd.DataFrame, n_folds: int = 3) -> list[pd.DataFrame]:
    if signals.empty:
        return [signals] * n_folds
    s = signals.sort_values("signal_ts")
    t0, t1 = s["signal_ts"].min(), s["signal_ts"].max()
    edges = pd.date_range(t0, t1, periods=n_folds + 1)
    folds = []
    for i in range(n_folds):
        lo, hi = edges[i], edges[i + 1]
        mask = (s["signal_ts"] >= lo) & (s["signal_ts"] <= hi if i == n_folds - 1 else s["signal_ts"] < hi)
        folds.append(s[mask])
    return folds
