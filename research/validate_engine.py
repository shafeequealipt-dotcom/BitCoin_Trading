"""Phase A exit criterion: does research.engine.backtest.run() correctly
re-implement the SAME bracket algorithm already trusted from the one-off
forensic pass (scripts/research/replay_all_trades.py's bracket() function)?

This is NOT "does a static SL/TP bracket reproduce the live ledger's actual
P&L" -- the live bot uses an adaptive trailing-stop exit system that this
harness deliberately does not model (Phase E of the plan retires it; every
Phase B candidate strategy defines its own simple exit instead). Checking
that would be comparing two different questions' answers, not a validation.

Method: replay every one of the bot's 1,090 real trades (real symbol, side,
entry time) through BOTH the new engine and a small inline re-implementation
of the old script's bracket(), using the IDENTICAL entry-price/window-start
convention on both sides (open of the first bar at-or-after the fill minute,
window inclusive of that bar -- the new engine's actual contract for a
next-bar-open fill). If they agree to float precision on every trade, the
new module's walk-forward loop and stop-first tie-break are correct.

First run found 873/1090 exact matches and 217 near-misses up to 2% -- traced
by hand to a real, understood convention difference (the old script used the
bot's true recorded fill price and skipped the entry bar entirely; a fair
choice when replaying a trade that already happened, but not the contract a
FUTURE candidate strategy needs, which has no "true fill" to defer to). Once
both sides used the same convention, all 1,090 trades matched to 1e-8.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from research.engine import backtest

SHADOW_DB = Path(__file__).resolve().parents[1] / "shadow" / "data" / "shadow.db"
SL, TP, HMAX = 1.0, 1.0, 240  # arbitrary grid cell -- any works, this just needs to be identical on both sides


def _old_bracket_aligned(conn, symbol: str, sign: int, entry_ts_ms: int, sl: float, tp: float, hmax: int) -> float | None:
    """Verbatim tie-break logic from replay_all_trades.py's bracket(), but
    with the SAME entry convention research.engine.backtest.run() uses:
    entry price = open of the first bar at-or-after entry_ts_ms, that bar
    included in the checked window (not the true recorded fill, and not
    skipping the entry bar)."""
    path = conn.execute(
        "SELECT open, high, low, close FROM klines WHERE symbol=? AND timestamp>=? ORDER BY timestamp LIMIT ?",
        (symbol, entry_ts_ms, hmax)).fetchall()
    if len(path) < 30:
        return None
    entry = path[0][0]
    last = entry
    for _, hi, lo, cl in path:
        if sign > 0:
            if (entry - lo) / entry * 100 >= sl:
                return -sl
            if (hi - entry) / entry * 100 >= tp:
                return tp
        else:
            if (hi - entry) / entry * 100 >= sl:
                return -sl
            if (entry - lo) / entry * 100 >= tp:
                return tp
        last = cl
    return (last - entry) / entry * 100 * sign


def main() -> None:
    conn = sqlite3.connect(f"file:{SHADOW_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT symbol, side, opened_at FROM trade_history WHERE closed_at IS NOT NULL").fetchall()
    print(f"live trades: {len(rows)}")

    old_results, sig_rows = [], []
    for symbol, side, opened_at in rows:
        t0 = pd.Timestamp(opened_at, tz="UTC")
        sign = 1 if side == "Buy" else -1
        signal_ts = t0 - pd.Timedelta(minutes=1)
        entry_ts_ms = int(t0.timestamp() * 1000) // 60000 * 60000
        old = _old_bracket_aligned(conn, symbol, sign, entry_ts_ms, SL, TP, HMAX)
        if old is None:
            continue
        old_results.append(old)
        sig_rows.append({"symbol": symbol, "signal_ts": signal_ts, "direction": sign,
                          "stop_pct": SL, "target_pct": TP, "max_hold": HMAX})

    signals = pd.DataFrame(sig_rows)
    new_trades = backtest.run(signals, interval="1m")

    print(f"resolved by both: {len(old_results)}  (engine dropped: {new_trades.attrs['dropped']})")
    diffs = [abs(o - n) for o, n in zip(old_results, new_trades["gross_pct"].tolist())]
    n_exact = sum(1 for d in diffs if d < 1e-6)
    print(f"max abs diff: {max(diffs):.8f}%   mean abs diff: {sum(diffs)/len(diffs):.8f}%")
    print(f"matching to 1e-6: {n_exact}/{len(diffs)}")

    verdict = "PASS -- engine mechanics confirmed correct" if n_exact == len(diffs) else \
        "FAIL -- investigate before trusting ANY Phase B result"
    print(f"\n{verdict}")


if __name__ == "__main__":
    main()
