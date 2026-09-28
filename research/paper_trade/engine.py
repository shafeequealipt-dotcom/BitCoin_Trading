"""Live paper-trading loop for b1v2_trend_breakout_trailing -- the ONLY
strategy that has passed the research promotion bar (see
research/reports/round2_results_2026_09_28.md). Own ledger
(research/paper_trade/ledger.py), own risk limits (research/engine/sizing.py),
fully isolated from the live bot's shadow.db paper account -- this
strategy's track record can never be mixed with the current bot's.

Signal generation and exit math are IMPORTED, not reimplemented: this file
calls b1v2_trend_breakout_trailing.generate_signals() and
research.engine.trailing_logic exactly as the backtest that passed used
them. There is no separate "live version" of the strategy logic to
accidentally diverge from what was tested.

Fill convention (matches the backtest exactly, see the module docstring in
backtest_trailing.py): a signal on bar T fills at the OPEN of bar T+1, and
bar T+1's own high/low is checked for a stop touch as the FIRST bar of the
holding period. Live, this means waiting for bar T+1 to CLOSE before we
know its open (to fill at) and its full range (to check) -- one extra bar
of latency versus a theoretical instant fill, which is conservative, not
optimistic, and keeps live execution exactly equivalent to the backtest.

Pre-registered trial plan and pass/fail criteria, written BEFORE this was
first run: research/reports/paper_trial_plan.md.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from research.engine import data as rdata
from research.engine import sizing
from research.engine import trailing_logic as tl
from research.paper_trade import ledger, live_data
from research.strategies import b1v2_trend_breakout_trailing as strat

LOG_PATH = Path(__file__).resolve().parent / "logs" / "engine.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("paper_trade")

POLL_INTERVAL_SECONDS = 900  # 15 min -- well under the 4h bar interval, so a new close is never missed by much
LIMITS = sizing.RiskLimits()


def _process_open_position(pos, bars: pd.DataFrame) -> None:
    if bars.empty:
        return
    last_checked = pd.Timestamp(pos["last_checked_ts"])
    new_bars = bars[bars.index > last_checked]
    if new_bars.empty:
        return

    sign = int(pos["direction"])
    stop_level = pos["current_stop_price"]
    extreme = pos["extreme_price"]
    armed = bool(pos["armed"])
    bars_held = pos["bars_held"]
    last_ts = last_checked

    for ts, bar in new_bars.iterrows():
        bars_held += 1
        last_ts = ts
        if tl.check_stop_hit(stop_level, sign, bar["high"], bar["low"]):
            reason = "trail_stop" if armed else "initial_stop"
            pnl = ledger.close_position(pos["position_id"], stop_level, str(ts), reason)
            log.info(f"CLOSED {pos['symbol']} @ {stop_level:.6g} reason={reason} net_pnl=${pnl:+.2f}")
            return
        if bars_held >= pos["max_hold_bars"]:
            exit_price = float(bar["close"])
            pnl = ledger.close_position(pos["position_id"], exit_price, str(ts), "time")
            log.info(f"CLOSED {pos['symbol']} @ {exit_price:.6g} reason=time net_pnl=${pnl:+.2f}")
            return
        extreme = tl.update_extreme(extreme, sign, bar["high"], bar["low"])
        stop_level, armed = tl.ratchet_stop(
            stop_level, extreme, pos["entry_price"], sign, pos["trail_activate_pct"], pos["trail_pct"],
        )

    ledger.update_trailing(pos["position_id"], stop_level, armed, extreme, bars_held, str(last_ts))


def _maybe_open_new_positions(universe: list[str], open_symbols: set[str]) -> None:
    signals = strat.generate_signals(universe)
    if signals.empty:
        return

    for symbol in universe:
        if symbol in open_symbols:
            continue  # never pyramid -- one position per symbol, matching the backtest's implicit assumption
        bars = rdata.klines(symbol, strat.INTERVAL)
        if len(bars) < 2:
            continue
        # A tradeable signal is one whose signal_ts is the SECOND-TO-LAST bar
        # -- meaning its entry bar (the very next one) is bars.index[-1], the
        # latest CLOSED bar, whose open/high/low we can now use.
        sig_ts = bars.index[-2]
        entry_ts = bars.index[-1]
        row = signals[(signals["symbol"] == symbol) & (signals["signal_ts"] == sig_ts)]
        if row.empty:
            continue
        row = row.iloc[0]
        entry_bar = bars.iloc[-1]
        entry_price = float(entry_bar["open"])
        sign = int(row["direction"])
        stop_pct = float(row["stop_pct"])
        stop_price = tl.initial_stop(entry_price, sign, stop_pct)

        wallet = ledger.get_wallet()
        if wallet.halted_reason:
            log.warning(f"SKIP {symbol}: trading halted ({wallet.halted_reason})")
            continue
        notional_usd, risk_usd = sizing.position_size(wallet.equity_usd, stop_pct, LIMITS.risk_pct_per_trade)
        open_positions = [
            sizing.Position(p["symbol"], p["direction"], p["entry_price"], p["notional_usd"],
                             p["risk_usd"], p["current_stop_price"])
            for p in ledger.get_open_positions()
        ]
        ok, reason = sizing.can_open(wallet.equity_usd, open_positions, risk_usd, LIMITS,
                                      wallet.realized_pnl_today_usd, wallet.peak_equity_usd)
        if not ok:
            log.info(f"BLOCKED {symbol} entry: {reason}")
            ledger.log_event("entry_blocked", f"{symbol} reason={reason}")
            continue

        # Process the entry bar itself as bar #1 of the hold (matches
        # backtest_trailing.py: the entry bar is included in the window).
        extreme = tl.update_extreme(entry_price, sign, entry_bar["high"], entry_bar["low"])
        new_stop, armed = tl.ratchet_stop(stop_price, extreme, entry_price, sign,
                                           float(row["trail_activate_pct"]), float(row["trail_pct"]))
        hit_on_entry_bar = tl.check_stop_hit(stop_price, sign, entry_bar["high"], entry_bar["low"])

        pid = ledger.open_position(
            symbol, sign, entry_price, str(entry_ts), notional_usd, risk_usd, stop_price,
            int(row["max_hold"]), float(row["trail_activate_pct"]), float(row["trail_pct"]),
            bars_held=1, extreme_price=extreme, armed=armed, last_checked_ts=str(entry_ts),
        )
        if hit_on_entry_bar:
            # Extremely tight initial stop touched within the very entry
            # bar's own range -- rare but must be handled identically to
            # the backtest, which checks the entry bar for exactly this.
            pnl = ledger.close_position(pid, stop_price, str(entry_ts), "initial_stop")
            log.info(f"OPENED+IMMEDIATELY CLOSED {symbol} (stop hit within entry bar) net_pnl=${pnl:+.2f}")
        else:
            ledger.update_trailing(pid, new_stop, armed, extreme, 1, str(entry_ts))
            log.info(f"OPENED {symbol} dir={sign} entry={entry_price:.6g} stop={new_stop:.6g} "
                      f"notional=${notional_usd:.2f} risk=${risk_usd:.2f}")


def tick() -> None:
    ledger.roll_day_if_needed()
    universe = rdata.universe()
    live_data.refresh_universe(universe)

    open_positions = ledger.get_open_positions()
    for pos in open_positions:
        bars = rdata.klines(pos["symbol"], strat.INTERVAL)
        _process_open_position(pos, bars)

    open_symbols = {p["symbol"] for p in ledger.get_open_positions()}
    _maybe_open_new_positions(universe, open_symbols)

    wallet = ledger.get_wallet()
    drawdown_pct = (wallet.peak_equity_usd - wallet.equity_usd) / wallet.peak_equity_usd * 100
    if drawdown_pct >= LIMITS.total_drawdown_limit_pct and not wallet.halted_reason:
        ledger.set_halted("total_drawdown_limit_pct")
        log.critical(f"HALTED: drawdown {drawdown_pct:.1f}% >= limit {LIMITS.total_drawdown_limit_pct}%")

    n_open = len(ledger.get_open_positions())
    log.info(f"TICK_DONE equity=${wallet.equity_usd:.2f} peak=${wallet.peak_equity_usd:.2f} "
              f"drawdown={drawdown_pct:.2f}% open_positions={n_open} halted={wallet.halted_reason or 'no'}")


def main() -> None:
    ledger.init_db()
    log.info(f"paper_trade engine starting -- strategy={strat.__name__} "
              f"poll_interval={POLL_INTERVAL_SECONDS}s limits={LIMITS}")
    while True:
        try:
            tick()
        except Exception as e:
            log.exception(f"TICK_FAILED err={e}")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
