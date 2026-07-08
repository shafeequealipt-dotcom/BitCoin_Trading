"""Element 2 live verification (Four-Element Prompt Recalibration,
2026-06-11) — READ-ONLY.

Part D trial condition: each briefed coin with prior attempts today
shows the genuine count and net result, hand-verified against the trade
records for at least two coins; a fresh coin shows none.

Three independent reads are compared per symbol:
1. Direct SQL against data/trading.db (opened read-only via SQLite URI)
   — the same window/mode semantics as the shipped helper, written out
   longhand so an error in the helper cannot hide itself.
2. The shipped helper ``session_attempts_today`` through the project's
   own DatabaseManager.
3. The newest Call-A prompt dump in data/stage2_dumps (when present) —
   the rendered "Session today:" lines the brain actually read. Run
   after the restart with the dump sentinel enabled; before that, the
   script reports the dump comparison as not-yet-available.

Usage: python3 verify_session_attempts_live.py [--mode bybit_demo]
The mode defaults to the value in config.toml's active adapter
([bybit_demo] enabled=true -> bybit_demo, else shadow).

Output is plain prose for a screen reader: no tables, no emoji. The
script never writes anything.
"""

import asyncio
import glob
import json
import os
import re
import sqlite3
import sys

DB_PATH = "data/trading.db"
DUMP_GLOB = "data/stage2_dumps/*.json"


def resolve_mode() -> str:
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            return arg.split("=", 1)[1]
    if "--mode" in sys.argv:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    try:
        cfg = open("config.toml", encoding="utf-8").read()
        # The active adapter is [general] mode = "..." — the same key
        # the transformer's current_mode ultimately reflects.
        m = re.search(r'^\[general\]', cfg, re.M)
        if m:
            tail = cfg[m.end():]
            m2 = re.search(r'^mode\s*=\s*"([^"]+)"', tail, re.M)
            if m2:
                return m2.group(1)
    except Exception:
        pass
    return "shadow"


def direct_sql(mode: str) -> dict:
    uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute(
            "SELECT symbol, COUNT(DISTINCT opened_at), SUM(pnl_usd) "
            "FROM trade_log WHERE exchange_mode = ? "
            "AND opened_at >= date('now') "
            "AND opened_at < date('now', '+1 day') "
            "GROUP BY symbol ORDER BY 2 DESC",
            (mode,),
        )
        return {r[0]: (int(r[1]), float(r[2] or 0.0)) for r in cur.fetchall()}
    finally:
        con.close()


async def helper_read(symbols: list, mode: str) -> dict:
    from src.core.trade_recorder import session_attempts_today
    from src.database.connection import DatabaseManager

    db = DatabaseManager(DB_PATH)
    await db.connect()
    try:
        return await session_attempts_today(
            db, symbols=symbols, exchange_mode=mode,
        )
    finally:
        await db.disconnect()


def newest_dump_lines() -> tuple[str, dict]:
    files = sorted(glob.glob(DUMP_GLOB), key=os.path.getmtime)
    for path in reversed(files):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue
        prompt = str(data.get("prompt", ""))
        if "Session today:" not in prompt:
            continue
        out = {}
        sym = None
        for line in prompt.splitlines():
            if line.startswith("### "):
                sym = line[4:].split(" ", 1)[0].strip()
            m = re.match(
                r"\s+Session today: (\d+) attempts, net ([+-][0-9.]+) USD",
                line,
            )
            if m and sym:
                out[sym] = (int(m.group(1)), float(m.group(2)))
        return path, out
    return "", {}


def main() -> int:
    mode = resolve_mode()
    print(f"Verifying session-attempt counts for exchange mode {mode}.")
    sql = direct_sql(mode)
    if not sql:
        print(
            "No closed trades recorded today in this mode yet — nothing "
            "to verify against. Re-run after trades close today."
        )
        return 0
    print(f"Direct SQL finds {len(sql)} coins with attempts today.")
    top = list(sql.items())[:4]
    for sym, (att, net) in top:
        print(f"  {sym}: {att} attempts, net {net:+.2f} USD by direct SQL.")

    helper = asyncio.get_event_loop().run_until_complete(
        helper_read([s for s, _ in top] + ["ZZZFRESHUSDT"], mode)
    )
    failures = 0
    for sym, (att, net) in top:
        h = helper.get(sym)
        if not h:
            print(f"FAIL: helper returned nothing for {sym}.")
            failures += 1
            continue
        if int(h["attempts"]) == att and abs(float(h["net_usd"]) - net) < 0.005:
            print(f"PASS: helper matches direct SQL for {sym}.")
        else:
            print(
                f"FAIL: helper mismatch for {sym} — helper says "
                f"{h['attempts']} attempts net {h['net_usd']:+.2f}, direct "
                f"SQL says {att} attempts net {net:+.2f}."
            )
            failures += 1
    if "ZZZFRESHUSDT" in helper:
        print("FAIL: a never-traded symbol returned a row.")
        failures += 1
    else:
        print("PASS: a fresh symbol returns no row (renders nothing).")

    dump_path, dump = newest_dump_lines()
    if not dump_path:
        print(
            "Dump comparison not yet available: no stage2 dump with a "
            "Session today line found. Enable the sentinel (touch "
            "data/stage2_dumps/.enabled), wait one Call-A cycle after the "
            "restart, and re-run."
        )
    else:
        print(f"Newest dump with the line: {dump_path}.")
        for sym, (att, net) in dump.items():
            ref = sql.get(sym)
            if ref is None:
                print(
                    f"NOTE: dump shows {sym} at {att} attempts but direct "
                    f"SQL has no rows — check the dump's age (counts move "
                    f"during the day)."
                )
                continue
            if ref[0] == att and abs(ref[1] - net) < 0.005:
                print(f"PASS: rendered line matches the ledger for {sym}.")
            else:
                print(
                    f"NOTE: rendered line for {sym} says {att} attempts net "
                    f"{net:+.2f}; the ledger NOW says {ref[0]} attempts net "
                    f"{ref[1]:+.2f}. A small drift is expected when trades "
                    f"closed after the dump; investigate only if the dump "
                    f"is fresh."
                )

    if failures == 0:
        print("RESULT: PASS — helper and ledger agree on every checked coin.")
        return 0
    print(f"RESULT: FAIL — {failures} mismatches; do not trust the line.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
