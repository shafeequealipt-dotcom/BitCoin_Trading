"""Phase A exit criterion: fed the live bot's ACTUAL entries (symbol, side,
entry price/time, original stop/target), does the new engine reproduce the
live ledger's results within a small tolerance?

Uses shadow.db's 1-minute candles (interval="1m") -- the live bot's trades
hold 5-30 minutes, so hourly bars can't resolve them; see the note in
research/engine/data.py. If this script's numbers don't line up with
scripts/research/replay_all_trades.py, the new engine has a bug and nothing
it says about a candidate strategy can be trusted -- fix this before moving
on to Phase B, per the plan.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from research.engine import backtest, metrics

SHADOW_DB = Path(__file__).resolve().parents[1] / "shadow" / "data" / "shadow.db"


def load_live_signals() -> pd.DataFrame:
    with sqlite3.connect(f"file:{SHADOW_DB}?mode=ro", uri=True) as c:
        df = pd.read_sql_query(
            "SELECT symbol, side, entry_price, initial_stop_loss, initial_take_profit, "
            "opened_at, closed_at FROM trade_history "
            "WHERE closed_at IS NOT NULL AND initial_stop_loss IS NOT NULL "
            "AND initial_take_profit IS NOT NULL", c)
    df["signal_ts"] = pd.to_datetime(df["opened_at"], utc=True) - pd.Timedelta(minutes=1)
    df["closed_ts"] = pd.to_datetime(df["closed_at"], utc=True)
    df["direction"] = df["side"].map({"Buy": 1, "Sell": -1})
    df["stop_pct"] = (df["initial_stop_loss"] - df["entry_price"]).abs() / df["entry_price"] * 100
    df["target_pct"] = (df["initial_take_profit"] - df["entry_price"]).abs() / df["entry_price"] * 100
    # Cap the replay window generously past the longest observed hold so no
    # legitimate trade gets truncated (240 x 1m bars = 4h; forensic script
    # used the same cap and found 1090/1090 trades resolved within it).
    df["max_hold"] = 240
    return df[["symbol", "signal_ts", "direction", "stop_pct", "target_pct", "max_hold", "closed_ts"]]


def main() -> None:
    signals = load_live_signals()
    print(f"live trades with usable SL/TP: {len(signals)}")

    trades = backtest.run(signals, interval="1m")
    print(f"engine resolved: {len(trades)}  dropped: {trades.attrs['dropped']}")

    rep_market = metrics.report(trades, "market")
    print("\n=== ENGINE, replayed on the bot's own entries (market cost) ===")
    for k, v in rep_market.items():
        print(f"  {k:<24} {v}")

    print("\n=== compare against scripts/research/replay_all_trades.py's numbers ===")
    print("  (that script found, same 1,090-ish trades: win~38%, avg_move -0.062%,")
    print("   e-ratio 0.93-1.05 across horizons, 0/40 exit brackets profitable)")
    tol_note = (
        "PASS" if abs(rep_market["win_rate_pct"] - 38.2) < 5
        and abs(rep_market["expectancy_gross_pct"] - (-0.062)) < 0.15
        else "CHECK -- numbers diverge, investigate before trusting Phase B results"
    )
    print(f"\n  quick-tolerance check: {tol_note}")


if __name__ == "__main__":
    main()
