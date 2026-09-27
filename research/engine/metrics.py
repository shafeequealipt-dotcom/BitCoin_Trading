"""The one report every strategy is judged by, per TRADING_OVERHAUL_PLAN.md.

`report()` computes the numbers; `promotion.py` applies the pre-registered
pass/fail bar to them. Keeping these separate means the bar can be audited
independently of the arithmetic that feeds it.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research.engine import backtest
from research.engine.costs import COST_MODELS


def _t_stat(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def report(trades: pd.DataFrame, cost_model: str = "market") -> dict:
    """`trades` is the output of backtest.run() (must have gross_pct, mfe_pct, mae_pct)."""
    cost_pct = COST_MODELS[cost_model]
    n = len(trades)
    if n == 0:
        return {"n": 0, "cost_model": cost_model}

    net = trades["gross_pct"] - cost_pct
    wins = net[net > 0]
    losses = net[net <= 0]
    gross_profit = wins.sum()
    gross_loss = -losses.sum()

    edge_ratio = trades["mfe_pct"].mean() / trades["mae_pct"].mean() if trades["mae_pct"].mean() > 0 else float("nan")

    equity = net.cumsum()
    running_max = equity.cummax()
    max_dd = (equity - running_max).min()

    return {
        "n": n,
        "cost_model": cost_model,
        "win_rate_pct": 100 * len(wins) / n,
        "expectancy_pct": float(net.mean()),
        "expectancy_gross_pct": float(trades["gross_pct"].mean()),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
        "avg_win_pct": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_pct": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": float(abs(wins.mean() / losses.mean())) if len(wins) and len(losses) and losses.mean() != 0 else 0.0,
        "edge_ratio": float(edge_ratio),
        "t_stat": _t_stat(net),
        "max_drawdown_pct": float(max_dd),
        "total_net_pct": float(net.sum()),
        "avg_bars_held": float(trades["bars_held"].mean()),
        "dropped_signals": int(trades.attrs.get("dropped", 0)),
        "total_signals": int(trades.attrs.get("total_signals", n)),
    }


def baseline_reports(signals: pd.DataFrame, interval: str, cost_model: str, n_random: int = 200,
                      seed: int = 1337) -> dict:
    """Random-entry and mirror-direction baselines for the SAME universe/period.

    Mirror: identical signals, direction flipped -- answers "did we pick the
    wrong side of a real move, or is there no move to pick a side of at all".
    Random: same symbols/stop/target/max_hold shape, but entry TIMESTAMPS
    resampled uniformly from the same symbols' available history -- answers
    "is this strategy's timing better than an arbitrary moment".
    """
    mirror_signals = signals.copy()
    mirror_signals["direction"] = -mirror_signals["direction"]
    mirror_trades = backtest.run(mirror_signals, interval)
    mirror = report(mirror_trades, cost_model)

    rng = np.random.default_rng(seed)
    rand_rows = []
    symbols = signals["symbol"].unique().tolist()
    from research.engine import data as rdata
    for _ in range(n_random):
        base = signals.sample(1, random_state=rng.integers(0, 2**31 - 1)).iloc[0]
        sym = rng.choice(symbols)
        bars = rdata.klines(sym, interval)
        if len(bars) < base["max_hold"] + 2:
            continue
        idx = rng.integers(0, len(bars) - int(base["max_hold"]) - 1)
        rand_rows.append({
            "symbol": sym, "signal_ts": bars.index[idx], "direction": int(rng.choice([1, -1])),
            "stop_pct": base["stop_pct"], "target_pct": base["target_pct"], "max_hold": base["max_hold"],
        })
    random_trades = backtest.run(pd.DataFrame(rand_rows), interval) if rand_rows else pd.DataFrame()
    random_rep = report(random_trades, cost_model) if len(random_trades) else {"n": 0}

    return {"mirror": mirror, "random": random_rep}
