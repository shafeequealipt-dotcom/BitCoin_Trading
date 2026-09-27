"""Run one candidate strategy through the full Phase B pipeline: generate
signals -> walk-forward split -> backtest each fold -> promotion.evaluate()
against the pre-registered bar. Usage:

    research/.venv/bin/python -m research.run_strategy b1_trend_breakout
"""
from __future__ import annotations

import importlib
import sys

from research.engine import backtest, backtest_trailing, data as rdata, metrics, promotion, walkforward

_EXIT_STYLES = {
    "bracket": (backtest.run, ("stop_pct", "target_pct")),
    "trailing": (backtest_trailing.run, ("stop_pct", "trail_activate_pct", "trail_pct")),
}


def main(strategy_name: str) -> None:
    mod = importlib.import_module(f"research.strategies.{strategy_name}")
    interval = getattr(mod, "INTERVAL", "1h")
    exit_style = getattr(mod, "EXIT_STYLE", "bracket")
    run, extra_cols = _EXIT_STYLES[exit_style]
    universe = rdata.universe()

    print(f"generating signals for {strategy_name} over {len(universe)} symbols ({interval}, exit={exit_style})...")
    signals = mod.generate_signals(universe)
    print(f"signals: {len(signals)}")
    if signals.empty:
        print("no signals generated -- nothing to test")
        return

    folds = walkforward.time_folds(signals, n_folds=3)
    fold_reports = []
    for i, fold in enumerate(folds, 1):
        trades = run(fold, interval)
        rep = metrics.report(trades, "market")
        fold_reports.append(rep)
        print(f"  fold {i}: n={rep.get('n')} signal_ts {fold['signal_ts'].min() if len(fold) else '-'} "
              f".. {fold['signal_ts'].max() if len(fold) else '-'}")

    # OOS = the LAST fold only, tested with parameters fixed before any fold was seen
    # (this strategy has no in-run tuning step -- see the module docstring).
    oos_trades = run(folds[-1], interval)
    oos_report = metrics.report(oos_trades, "market")
    baselines = metrics.baseline_reports(folds[-1], interval, "market", runner=run, extra_cols=extra_cols)

    result = promotion.evaluate(strategy_name, oos_report, fold_reports, baselines)
    promotion.print_result(result)

    # Also report at maker cost for reference (see costs.py caveat: this is
    # conditional on the limit-order execution layer in Phase D).
    oos_maker = metrics.report(oos_trades, "maker")
    print(f"\n  (reference only) OOS at maker cost: expectancy={oos_maker['expectancy_pct']:+.4f}% "
          f"t={oos_maker['t_stat']:.2f}")
    if exit_style == "trailing" and len(oos_trades):
        armed_rate = 100 * oos_trades["armed"].mean()
        print(f"  trailing-stop armed on {armed_rate:.0f}% of OOS trades "
              f"(the rest stopped out on the INITIAL stop before ever arming)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m research.run_strategy <strategy_module_name>")
        sys.exit(1)
    main(sys.argv[1])
