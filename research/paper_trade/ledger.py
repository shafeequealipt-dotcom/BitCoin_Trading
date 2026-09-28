"""SQLite ledger for the B1v2 paper trial. Own database
(research/paper_trade/data/ledger.db), own starting balance, own equity
curve -- deliberately never touches shadow.db (the live bot's paper wallet)
so this strategy's track record can never be mixed with, or mistaken for,
the current bot's. See research/reports/paper_trial_plan.md for why.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "ledger.db"
STARTING_EQUITY_USD = 10_000.0  # matches the live bot's own original starting balance
COST_MARKET_ROUND_TRIP_PCT = 0.17  # same cost model the strategy was backtested and PASSED under


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            equity_usd REAL NOT NULL,
            peak_equity_usd REAL NOT NULL,
            realized_pnl_today_usd REAL NOT NULL DEFAULT 0,
            today_date TEXT NOT NULL,
            halted_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS positions (
            position_id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts TEXT NOT NULL,
            notional_usd REAL NOT NULL,
            risk_usd REAL NOT NULL,
            initial_stop_price REAL NOT NULL,
            current_stop_price REAL NOT NULL,
            trail_activate_pct REAL NOT NULL,
            trail_pct REAL NOT NULL,
            armed INTEGER NOT NULL DEFAULT 0,
            extreme_price REAL NOT NULL,
            max_hold_bars INTEGER NOT NULL,
            bars_held INTEGER NOT NULL DEFAULT 0,
            last_checked_ts TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS trade_history (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            entry_ts TEXT NOT NULL,
            exit_ts TEXT NOT NULL,
            notional_usd REAL NOT NULL,
            gross_pnl_usd REAL NOT NULL,
            fee_usd REAL NOT NULL,
            net_pnl_usd REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            bars_held INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT
        );
        """)
        row = c.execute("SELECT 1 FROM wallet WHERE id=1").fetchone()
        if row is None:
            import datetime
            c.execute(
                "INSERT INTO wallet(id, equity_usd, peak_equity_usd, today_date) VALUES (1, ?, ?, ?)",
                (STARTING_EQUITY_USD, STARTING_EQUITY_USD, datetime.date.today().isoformat()),
            )
        c.commit()


@dataclass
class WalletState:
    equity_usd: float
    peak_equity_usd: float
    realized_pnl_today_usd: float
    today_date: str
    halted_reason: str | None


def get_wallet() -> WalletState:
    with _conn() as c:
        row = c.execute(
            "SELECT equity_usd, peak_equity_usd, realized_pnl_today_usd, today_date, halted_reason FROM wallet WHERE id=1"
        ).fetchone()
    return WalletState(*row)


def roll_day_if_needed() -> None:
    """Reset the daily-loss counter at UTC midnight -- checked every poll."""
    import datetime
    today = datetime.date.today().isoformat()
    with _conn() as c:
        c.execute("UPDATE wallet SET realized_pnl_today_usd=0, today_date=? WHERE id=1 AND today_date != ?",
                  (today, today))
        c.commit()


def get_open_positions() -> list[sqlite3.Row]:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        return c.execute("SELECT * FROM positions WHERE status='open'").fetchall()


def open_position(symbol: str, direction: int, entry_price: float, entry_ts: str,
                   notional_usd: float, risk_usd: float, stop_price: float, max_hold_bars: int,
                   trail_activate_pct: float, trail_pct: float,
                   bars_held: int = 0, extreme_price: float | None = None,
                   armed: bool = False, last_checked_ts: str | None = None) -> int:
    """`trail_activate_pct`/`trail_pct` are the ABSOLUTE percent values
    computed from ATR at SIGNAL time (per b1v2_trend_breakout_trailing.py)
    -- fixed for this trade's whole life, exactly like the backtest, not
    recomputed later from a moving ATR. `bars_held`/`extreme_price`/`armed`/
    `last_checked_ts` let the caller pass the result of already processing
    the entry bar itself (matching backtest_trailing.py, where the entry
    bar is bar #1 of the checked window) in the same step as opening."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO positions(symbol, direction, entry_price, entry_ts, notional_usd, risk_usd, "
            "initial_stop_price, current_stop_price, trail_activate_pct, trail_pct, armed, extreme_price, "
            "max_hold_bars, bars_held, last_checked_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, direction, entry_price, entry_ts, notional_usd, risk_usd, stop_price, stop_price,
             trail_activate_pct, trail_pct, int(armed),
             extreme_price if extreme_price is not None else entry_price, max_hold_bars, bars_held,
             last_checked_ts if last_checked_ts is not None else entry_ts),
        )
        c.commit()
        log_event("position_opened", f"{symbol} dir={direction} entry={entry_price} notional=${notional_usd:.2f} risk=${risk_usd:.2f}")
        return cur.lastrowid


def update_trailing(position_id: int, new_stop_price: float, armed: bool, extreme_price: float,
                     bars_held: int, last_checked_ts: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE positions SET current_stop_price=?, armed=?, extreme_price=?, bars_held=?, "
            "last_checked_ts=? WHERE position_id=?",
            (new_stop_price, int(armed), extreme_price, bars_held, last_checked_ts, position_id),
        )
        c.commit()


def close_position(position_id: int, exit_price: float, exit_ts: str, exit_reason: str) -> float:
    """Closes the position, books the trade, updates wallet equity. Returns net_pnl_usd."""
    with _conn() as c:
        c.row_factory = sqlite3.Row
        pos = c.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()
        if pos is None or pos["status"] != "open":
            return 0.0
        gross_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100 * pos["direction"]
        gross_pnl_usd = pos["notional_usd"] * gross_pct / 100
        fee_usd = pos["notional_usd"] * COST_MARKET_ROUND_TRIP_PCT / 100
        net_pnl_usd = gross_pnl_usd - fee_usd

        c.execute("UPDATE positions SET status='closed' WHERE position_id=?", (position_id,))
        c.execute(
            "INSERT INTO trade_history(symbol, direction, entry_price, exit_price, entry_ts, exit_ts, "
            "notional_usd, gross_pnl_usd, fee_usd, net_pnl_usd, exit_reason, bars_held) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pos["symbol"], pos["direction"], pos["entry_price"], exit_price, pos["entry_ts"], exit_ts,
             pos["notional_usd"], gross_pnl_usd, fee_usd, net_pnl_usd, exit_reason, pos["bars_held"]),
        )
        wallet = c.execute("SELECT equity_usd, peak_equity_usd, realized_pnl_today_usd FROM wallet WHERE id=1").fetchone()
        new_equity = wallet["equity_usd"] + net_pnl_usd
        new_peak = max(wallet["peak_equity_usd"], new_equity)
        c.execute(
            "UPDATE wallet SET equity_usd=?, peak_equity_usd=?, realized_pnl_today_usd=realized_pnl_today_usd+? WHERE id=1",
            (new_equity, new_peak, net_pnl_usd),
        )
        c.commit()
        log_event("position_closed", f"{pos['symbol']} exit={exit_price} reason={exit_reason} net_pnl=${net_pnl_usd:+.2f} equity=${new_equity:.2f}")
        return net_pnl_usd


def set_halted(reason: str | None) -> None:
    with _conn() as c:
        c.execute("UPDATE wallet SET halted_reason=? WHERE id=1", (reason,))
        c.commit()


def log_event(kind: str, detail: str = "") -> None:
    with _conn() as c:
        c.execute("INSERT INTO events(ts, kind, detail) VALUES (?, ?, ?)", (time.time(), kind, detail))
        c.commit()
