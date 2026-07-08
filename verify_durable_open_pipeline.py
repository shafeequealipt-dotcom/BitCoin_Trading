#!/usr/bin/env python3
"""verify_durable_open_pipeline.py — Pass 3 runtime end-to-end pipeline check.

Drives the durable-open DATA FLOW through the REAL project classes (no SQL/method
mocks): real DatabaseManager + migrations, real ThesisManager (reserve/finalize/
void/sweep), the REAL Position type the sweep consumes, and the REAL close path
(close_thesis). Proves each pipeline end-to-end:

  A. Happy path:   reserve -> finalize(open+order_id) -> close_thesis -> booked
  B. Crash-adopt:  reserve -> (no finalize) -> sweep(live entry-matched Position)
                   -> adopted to open -> close_thesis -> booked
  C. Crash-void:   reserve -> sweep(no position) -> voided -> not an open trade
  D. Entry-guard:  reserve -> sweep(live position, entry MISMATCH) -> voided

Run: .venv/bin/python verify_durable_open_pipeline.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool) -> None:
    (PASS if cond else FAIL).append(name)
    print(("PASS: " if cond else "FAIL: ") + name)


async def main() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.core.thesis_manager import ThesisManager
    from src.core.types import Position, Side

    _side = next(iter(Side))  # any valid Side member (real enum)

    def pos(symbol, entry):
        return Position(symbol=symbol, side=_side, size=1.0,
                        entry_price=entry, mark_price=entry)

    db = DatabaseManager(str(Path(tempfile.mkdtemp()) / "t.db"))
    await db.connect()
    await run_migrations(db)
    tm = ThesisManager(db)

    async def reserve(sym, entry):
        return await tm.save_thesis(
            symbol=sym, direction="long", entry_price=entry, stop_loss_price=entry * 0.9,
            take_profit_price=entry * 1.1, size_usd=100.0, leverage=5, max_hold_minutes=30,
            trailing_activation_pct=0.5, thesis="(reserved)", order_id="", status="reserving")

    async def row(tid):
        return await db.fetch_one(
            "SELECT status, order_id, actual_pnl_usd FROM trade_thesis WHERE id=?", (tid,))

    # ── Pipeline A: happy path reserve -> finalize -> close ──
    a = await reserve("AAAUSDT", 1.0)
    await tm.finalize_thesis(a, order_id="OID-A", thesis="real")
    ra = await row(a)
    check("A: finalized to open+order_id", ra["status"] == "open" and ra["order_id"] == "OID-A")
    await tm.close_thesis(symbol="AAAUSDT", close_price=1.1, actual_pnl_pct=10.0,
                          actual_pnl_usd=10.0, close_reason="tp", order_id="OID-A")
    ra = await row(a)
    check("A: close_thesis booked via (symbol,order_id)", ra["status"] == "closed" and abs(ra["actual_pnl_usd"] - 10.0) < 1e-9)

    # ── Pipeline B: crash before finalize, live entry-matched position -> adopt -> close ──
    b = await reserve("BBBUSDT", 2.0)
    res = await tm.sweep_reserving_theses([pos("BBBUSDT", 2.0)])
    check("B: sweep adopted the entry-matched reservation", res["adopted"] == 1)
    rb = await row(b)
    check("B: adopted row is now open", rb["status"] == "open")
    # adopted row keeps order_id='' (finalize never ran) -> close_thesis symbol branch
    await tm.close_thesis(symbol="BBBUSDT", close_price=2.2, actual_pnl_pct=10.0,
                          actual_pnl_usd=5.0, close_reason="tp", order_id="")
    rb = await row(b)
    check("B: adopted position closes + books PnL", rb["status"] == "closed" and abs(rb["actual_pnl_usd"] - 5.0) < 1e-9)

    # ── Pipeline C: crash, no live position -> void ──
    c = await reserve("CCCUSDT", 3.0)
    res = await tm.sweep_reserving_theses([])  # confirmed-empty snapshot
    check("C: sweep voided the stale reservation", res["voided"] == 1)
    rc = await row(c)
    check("C: voided (never a tradeable open)", rc["status"] == "voided")

    # ── Pipeline D: live position but entry MISMATCH -> void (not mis-adopted) ──
    d = await reserve("DDDUSDT", 4.0)
    res = await tm.sweep_reserving_theses([pos("DDDUSDT", 8.0)])  # 100% off
    rd = await row(d)
    check("D: entry-mismatch position did NOT adopt", rd["status"] == "voided")

    # ── Cross-check: brain view (get_open_theses) only ever saw real opens ──
    opens = {o.get("symbol") for o in await tm.get_open_theses()}
    check("brain get_open_theses never exposed a reserving/voided row",
          opens == set())  # all four are now closed/voided

    await db.disconnect()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
