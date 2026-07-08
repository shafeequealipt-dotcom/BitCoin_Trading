#!/usr/bin/env python3
"""verify_durable_open_simulation.py — Pass 4 live-situation simulation.

Reproduces the ORIGINAL failure conditions (orphan-occurring data) against the
REAL code and checks each fix responds as intended:

PART 1 — durable-open failure injection (real ThesisManager):
  S1 place_order RAISES  -> reservation voided (the v1-review BLOCKER class): no
     phantom open row, invisible to the brain.
  S2 crash BEFORE finalize, position still live -> the reservation SURVIVES as a
     'reserving' row (the original bug left NOTHING) and the confirmed-position
     sweep adopts it -> recoverable.
  S3 crash, order never filled (no position) -> reservation voided.

PART 2 — heal --apply path on synthetic orphan data (real _already_booked +
  _backfill_one): an exchange close with no local record is booked into
  trade_history + daily_pnl with the correct win convention and carried-forward
  equity, and a re-run is idempotent (the close now matches by exit_time).

Run: .venv/bin/python verify_durable_open_simulation.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile

import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool) -> None:
    (PASS if cond else FAIL).append(name)
    print(("PASS: " if cond else "FAIL: ") + name)


def _load_heal():
    spec = importlib.util.spec_from_file_location(
        "heal_mod", str(ROOT / "scripts" / "backfill_orphan_closes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def main() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.core.thesis_manager import ThesisManager
    from src.core.types import Position, Side
    heal = _load_heal()

    _side = next(iter(Side))

    db = DatabaseManager(str(Path(tempfile.mkdtemp()) / "t.db"))
    await db.connect()
    await run_migrations(db)
    tm = ThesisManager(db)

    async def reserve(sym, entry):
        return await tm.save_thesis(
            symbol=sym, direction="long", entry_price=entry, stop_loss_price=entry * 0.9,
            take_profit_price=entry * 1.1, size_usd=100.0, leverage=5, max_hold_minutes=30,
            trailing_activation_pct=0.5, thesis="(reserved)", order_id="", status="reserving")

    async def status_of(tid):
        r = await db.fetch_one("SELECT status FROM trade_thesis WHERE id=?", (tid,))
        return r["status"] if r else None

    print("===== PART 1: durable-open failure injection =====")
    # S1 — place_order RAISES -> void (mimics strategy_worker try/except)
    t1 = await reserve("RAISEUSDT", 1.0)
    await tm.void_thesis(t1, "place_order_raised")
    check("S1 raise: reservation voided", await status_of(t1) == "voided")
    check("S1 raise: not visible to brain", "RAISEUSDT" not in {o.get("symbol") for o in await tm.get_open_theses()})

    # S2 — crash before finalize, position live -> reservation survives + adopted
    t2 = await reserve("CRASHUSDT", 5.0)
    check("S2 crash: reservation row SURVIVES (original bug left nothing)",
          await status_of(t2) == "reserving")
    res = await tm.sweep_reserving_theses(
        [Position(symbol="CRASHUSDT", side=_side, size=1.0, entry_price=5.0, mark_price=5.0)])
    check("S2 crash: confirmed-position sweep adopted it", res["adopted"] == 1 and await status_of(t2) == "open")

    # S3 — crash, order never filled (no position) -> void
    t3 = await reserve("NOFILLUSDT", 9.0)
    res = await tm.sweep_reserving_theses([])  # confirmed-empty
    check("S3 no-fill: reservation voided", res["voided"] == 1 and await status_of(t3) == "voided")

    await db.disconnect()

    print("\n===== PART 2: heal --apply on synthetic orphan data =====")
    # The heal functions run against an aiosqlite connection (as the real script
    # does), NOT the DatabaseManager — so use a migrated temp DB opened the same
    # way production opens it. Create schema via DatabaseManager+migrations, then
    # reopen with aiosqlite.
    p2_path = str(Path(tempfile.mkdtemp()) / "heal.db")
    _seed_db = DatabaseManager(p2_path)
    await _seed_db.connect()
    await run_migrations(_seed_db)
    await _seed_db.disconnect()

    today = datetime.now(timezone.utc)
    d_today = today.strftime("%Y-%m-%d")
    d_prev = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    hdb = await aiosqlite.connect(p2_path)
    hdb.row_factory = aiosqlite.Row
    # seed daily_pnl (prev day with ending_equity to carry forward; today row)
    await hdb.execute("INSERT INTO daily_pnl (date, starting_equity, ending_equity, "
                      "realized_pnl, total_trades, wins, losses) VALUES (?,?,?,?,?,?,?)",
                      (d_prev, 690.0, 700.0, 5.0, 10, 6, 4))
    await hdb.execute("INSERT INTO daily_pnl (date, starting_equity, ending_equity, "
                      "realized_pnl, total_trades, wins, losses) VALUES (?,?,?,?,?,?,?)",
                      (d_today, 700.0, 701.0, 1.0, 3, 2, 1))
    # a recorded close (sets the floor + a dedup target)
    rec_iso = today.replace(microsecond=0).isoformat()
    await hdb.execute("INSERT INTO trade_history (trade_id, symbol, side, entry_price, "
                      "exit_price, qty, pnl, pnl_pct, strategy, signal_confidence, notes, "
                      "entry_time, exit_time, exchange_mode) VALUES "
                      "('bd-rec1','ZZZUSDT','Buy',1.0,1.1,10,0.5,5.0,'s',0,'',?,?,'bybit_demo')",
                      (rec_iso, rec_iso))
    await hdb.commit()
    db = hdb  # Part 2 uses the aiosqlite connection from here on

    async def fetch1(sql, params=()):
        async with hdb.execute(sql, params) as c:
            return await c.fetchone()

    rec_ms = today.timestamp() * 1000.0
    # dedup: the recorded close IS booked; a far-off time for same symbol is NOT
    booked_rec = await heal._already_booked(db, "ZZZUSDT", rec_ms)
    booked_far = await heal._already_booked(db, "ZZZUSDT", rec_ms - 3600 * 1000.0)
    check("dedup: recorded close detected as booked", booked_rec is True)
    check("dedup: far-off same-symbol time NOT booked (would be orphan)", booked_far is False)

    # synthetic orphan today
    orphan_dt = today.replace(microsecond=0) - timedelta(minutes=30)
    orphan = dict(trade_id="bd-orphan-SIM1", order_id="SIM1clOID", symbol="ORPHUSDT",
                  open_side="Buy", entry=2.0, exit=2.04, qty=50.0, pnl=2.0, pnl_pct=2.0,
                  entry_iso=orphan_dt.isoformat(), exit_iso=orphan_dt.isoformat(),
                  exit_date=d_today)
    before = await fetch1("SELECT realized_pnl, total_trades, wins FROM daily_pnl WHERE date=?", (d_today,))
    await heal._backfill_one(db, orphan)
    await db.commit()
    after = await fetch1("SELECT realized_pnl, total_trades, wins FROM daily_pnl WHERE date=?", (d_today,))
    th = await fetch1("SELECT pnl, side FROM trade_history WHERE trade_id='bd-orphan-SIM1'")
    check("apply: trade_history row written", th is not None and abs(th["pnl"] - 2.0) < 1e-9)
    check("apply: daily_pnl realized += orphan pnl", abs(after["realized_pnl"] - (before["realized_pnl"] + 2.0)) < 1e-9)
    check("apply: daily_pnl total_trades += 1", after["total_trades"] == before["total_trades"] + 1)
    check("apply: daily_pnl wins += 1 (pnl>0 win)", after["wins"] == before["wins"] + 1)

    # idempotency: the orphan's close time now matches its trade_history row
    orphan_ms = orphan_dt.timestamp() * 1000.0
    check("idempotency: re-run sees the orphan as already booked",
          (await heal._already_booked(db, "ORPHUSDT", orphan_ms)) is True)

    # new-date orphan -> carries starting_equity forward from prev day
    future = (today + timedelta(days=2))
    d_future = future.strftime("%Y-%m-%d")
    orphan2 = dict(trade_id="bd-orphan-SIM2", order_id="SIM2", symbol="NEWDUSDT",
                   open_side="Buy", entry=1.0, exit=1.0, qty=1.0, pnl=-0.5, pnl_pct=-0.5,
                   entry_iso=future.isoformat(), exit_iso=future.isoformat(), exit_date=d_future)
    await heal._backfill_one(db, orphan2)
    await db.commit()
    nd = await fetch1("SELECT starting_equity, realized_pnl, losses FROM daily_pnl WHERE date=?", (d_future,))
    check("apply: new-date row carries starting_equity forward (not 0)",
          nd is not None and nd["starting_equity"] == 701.0)
    check("apply: new-date loss counted (pnl<0)", nd["losses"] == 1)

    await hdb.close()  # Part 2 conn is aiosqlite (Part 1's DatabaseManager was disconnected earlier)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
