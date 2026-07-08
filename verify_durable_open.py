#!/usr/bin/env python3
"""verify_durable_open.py — offline proof of the thesis-before-order durable-open lifecycle.

Proves, against a real migrated temp DB (no mocks of the SQL layer):

  * reserve  -> status='reserving', INVISIBLE to get_open_theses (brain) and to
                the recover_state_from_db query pattern (status='open')
  * finalize -> flips reserving->'open' AND stamps the real order_id atomically;
                now VISIBLE to the brain
  * void     -> flips reserving->'voided'; stays invisible
  * sweep    -> adopts reserving rows whose symbol has a live position (->open),
                voids the rest (the reserve-then-die / finalize-failure window)
  * legacy   -> save_thesis() with no status= still defaults to 'open'

Run: .venv/bin/python verify_durable_open.py
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


async def _save(tm, sym, status="reserving", order_id=""):
    return await tm.save_thesis(
        symbol=sym, direction="long", entry_price=1.0, stop_loss_price=0.9,
        take_profit_price=1.2, size_usd=100.0, leverage=5, max_hold_minutes=30,
        trailing_activation_pct=0.5, thesis="(reserved)", order_id=order_id,
        status=status,
    )


async def main() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.core.thesis_manager import ThesisManager

    tmpdir = tempfile.mkdtemp()
    db = DatabaseManager(str(Path(tmpdir) / "t.db"))
    await db.connect()
    await run_migrations(db)
    tm = ThesisManager(db)

    async def status_of(tid):
        r = await db.fetch_one(
            "SELECT status, order_id FROM trade_thesis WHERE id=?", (tid,)
        )
        return (r["status"], r["order_id"]) if r else (None, None)

    async def open_syms():
        return {o.get("symbol") for o in (await tm.get_open_theses())}

    # 1) reserve -> reserving, invisible to brain
    tid = await _save(tm, "AAAUSDT")
    check("reserve returns positive id", tid > 0)
    st, _ = await status_of(tid)
    check("reserved row status=reserving", st == "reserving")
    check("reserving INVISIBLE to get_open_theses", "AAAUSDT" not in await open_syms())

    # 2) finalize -> open + order_id, visible
    ok = await tm.finalize_thesis(tid, order_id="OID-AAA", thesis="real")
    check("finalize returns True", ok is True)
    st, oid = await status_of(tid)
    check("finalized status=open", st == "open")
    check("finalized order_id stamped", oid == "OID-AAA")
    check("finalized VISIBLE to get_open_theses", "AAAUSDT" in await open_syms())

    # 3) reserve -> void
    tid2 = await _save(tm, "BBBUSDT")
    check("void returns True", (await tm.void_thesis(tid2, "order_rejected")) is True)
    st2, _ = await status_of(tid2)
    check("voided status=voided", st2 == "voided")
    check("voided INVISIBLE to get_open_theses", "BBBUSDT" not in await open_syms())

    # 4) sweep: adopt ONLY on entry-price match; void otherwise.
    #    _save reserves entry_price=1.0.
    tid3 = await _save(tm, "CCCUSDT")  # live position, entry matches -> ADOPT
    tid4 = await _save(tm, "DDDUSDT")  # no live position           -> VOID
    tid_mm = await _save(tm, "GGGUSDT")  # live position but entry MISMATCH -> VOID
    live = [
        {"symbol": "CCCUSDT", "entry_price": 1.0},     # matches reserved 1.0
        {"symbol": "GGGUSDT", "entry_price": 2.0},     # 100% off -> must NOT adopt
    ]
    res = await tm.sweep_reserving_theses(live)
    check("sweep adopted=1 (entry-matched only)", res.get("adopted") == 1)
    check("sweep voided=2 (no-position + entry-mismatch)", res.get("voided") == 2)
    st3, _ = await status_of(tid3)
    st4, _ = await status_of(tid4)
    st_mm, _ = await status_of(tid_mm)
    check("sweep: entry-matched live position -> open", st3 == "open")
    check("sweep: no live position -> voided", st4 == "voided")
    check("sweep: entry-MISMATCH same-symbol position -> voided (not mis-adopted)", st_mm == "voided")
    check("sweep-adopted CCCUSDT now visible", "CCCUSDT" in await open_syms())
    check("entry-mismatch GGGUSDT NOT visible", "GGGUSDT" not in await open_syms())

    # 5) legacy back-compat: no status= defaults to 'open'
    tid5 = await tm.save_thesis(
        symbol="EEEUSDT", direction="long", entry_price=5.0, stop_loss_price=4.5,
        take_profit_price=5.5, size_usd=100.0, leverage=5, max_hold_minutes=30,
        trailing_activation_pct=0.5, thesis="legacy", order_id="OID-EEE",
    )
    st5, oid5 = await status_of(tid5)
    check("legacy save_thesis defaults status=open", st5 == "open")
    check("legacy save_thesis keeps order_id", oid5 == "OID-EEE")

    # 6) recover_state_from_db query pattern (status='open') excludes reserving
    await _save(tm, "FFFUSDT")
    r = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM trade_thesis WHERE status='open' AND symbol='FFFUSDT'"
    )
    check("recover-pattern (status=open) excludes reserving", (r["n"] if r else -1) == 0)

    await db.disconnect()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
